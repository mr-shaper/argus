#!/usr/bin/env python3
"""Perplexity Reader — OSS sister skill for Argus v1.0

Provides six subcommands to interact with Perplexity AI via Chrome DevTools Protocol (CDP).
Supports Comet (Perplexity's native browser) and Chrome with automatic fallback.

Subcommands:
  check                                     Check CDP port reachability for all browsers
  check-auth                                Validate Perplexity session cookie
  read-current [--format markdown|json]     Extract content from the active Perplexity tab
  fetch <url> [--format markdown|json]      Navigate to URL and extract content via CDP
  deep-search "<query>" --output FILE       Run Perplexity Deep Research (5-10 min)
  quick-search "<query>" --output-dir DIR   Run Perplexity Quick Search (30-60 s)

Environment variables:
  PERPLEXITY_COOKIES_PATH   Path to cookies JSON file
                            (default: ~/.config/argus/cookies/perplexity.json)
  ARGUS_COMET_PORT          Comet CDP port (default: 9223)
  ARGUS_CHROME_PORT         Chrome CDP port (default: 9222)

Exit codes:
  0  success
  1  general error
  2  browser CDP unreachable / cookie invalid
  4  dry-run stopped
"""

import sys
import os
import json
import argparse
import subprocess
import tempfile
import time
import re
from datetime import datetime, date
from http.client import HTTPConnection

# ---------------------------------------------------------------------------
# Configuration — env-var driven, no hard-coded user paths
# ---------------------------------------------------------------------------

_COMET_PORT = int(os.environ.get("ARGUS_COMET_PORT", "9223"))
_CHROME_PORT = int(os.environ.get("ARGUS_CHROME_PORT", "9222"))

_DEFAULT_COOKIES = os.path.expanduser(
    os.environ.get("PERPLEXITY_COOKIES_PATH",
                   "~/.config/argus/cookies/perplexity.json")
)

BROWSERS = [
    {
        "name": "Comet",
        "host": "localhost",
        "port": _COMET_PORT,
        "bin": "/Applications/Comet.app/Contents/MacOS/Comet",
        "args": [
            f"--remote-debugging-port={_COMET_PORT}",
            "--remote-allow-origins=*",
        ],
    },
    {
        "name": "Chrome",
        "host": "localhost",
        "port": _CHROME_PORT,
        "bin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "args": [
            f"--remote-debugging-port={_CHROME_PORT}",
            "--user-data-dir=" + os.path.expanduser("~/.chrome-debug-profile"),
            "--remote-allow-origins=*",
        ],
    },
]

# Legacy compat
CHROME_DEBUG_HOST = "localhost"
CHROME_DEBUG_PORT = _CHROME_PORT

COOKIES_FILE = _DEFAULT_COOKIES
AUTH_COOKIE_NAME = "__Secure-next-auth.session-token"

# ---------------------------------------------------------------------------
# JavaScript snippets
# ---------------------------------------------------------------------------

EXTRACT_JS = r"""
(() => {
  // Extract main answer content
  const answerEls = document.querySelectorAll('[class*="prose"], [class*="answer"], [class*="markdown"]');
  let content = '';
  if (answerEls.length > 0) {
    const texts = [];
    answerEls.forEach(el => {
      const text = el.innerText.trim();
      if (text && text.length > 50) texts.push(text);
    });
    content = texts.join('\n\n---\n\n');
  }
  if (!content) {
    const main = document.querySelector('main') || document.body;
    content = main.innerText;
  }

  // Extract citation / source links
  const sources = [];
  const seen = new Set();
  document.querySelectorAll('a[href]').forEach(a => {
    const href = a.href;
    if (!href || href.startsWith('javascript:') || href.includes('perplexity.ai')) return;
    const container = a.closest('[class*="source"]')
                   || a.closest('[class*="citation"]')
                   || a.closest('[class*="reference"]');
    if (container || a.querySelector('[class*="source"]') || a.closest('sup')) {
      const title = a.textContent.trim() || a.title || href;
      const key = href;
      if (!seen.has(key) && title.length < 200) {
        seen.add(key);
        sources.push({ title, url: href });
      }
    }
  });

  return JSON.stringify({
    title: document.title,
    url: location.href,
    content: content,
    sources: sources,
    timestamp: new Date().toISOString()
  });
})()
"""

# React synthetic event helper (used by deep/quick search)
_DISPATCH_JS = """
function click5(target) {
  const r = target.getBoundingClientRect();
  const o = {bubbles:true,cancelable:true,view:window,
              clientX:r.x+r.width/2,clientY:r.y+r.height/2,button:0};
  ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(
    t => target.dispatchEvent(new MouseEvent(t, o))
  );
}
"""

# ---------------------------------------------------------------------------
# CDP helpers — v1 (single request, evaluate_js)
# ---------------------------------------------------------------------------

def check_chrome_debug():
    """Check Chrome debug port; return tab list or None."""
    try:
        conn = HTTPConnection(CHROME_DEBUG_HOST, CHROME_DEBUG_PORT, timeout=3)
        conn.request("GET", "/json")
        resp = conn.getresponse()
        if resp.status == 200:
            tabs = json.loads(resp.read().decode())
            conn.close()
            return tabs
        conn.close()
        return None
    except Exception:
        return None


def find_perplexity_tab(tabs):
    """Find the first Perplexity page tab in a tab list."""
    for tab in tabs:
        url = tab.get("url", "")
        if "perplexity.ai" in url and tab.get("type") == "page":
            return tab
    return None


def evaluate_js(ws_url, js_code):
    """Execute JS via CDP WebSocket and return the parsed result."""
    try:
        import websocket
    except ImportError:
        print(
            "Error: websocket-client library required. Run: pip3 install websocket-client",
            file=sys.stderr,
        )
        sys.exit(1)

    ws = websocket.create_connection(ws_url, timeout=10)
    msg = json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": js_code,
            "returnByValue": True,
            "awaitPromise": True,
        },
    })
    ws.send(msg)
    result = json.loads(ws.recv())
    ws.close()

    if "result" in result and "result" in result["result"]:
        value = result["result"]["result"].get("value")
        if value:
            return json.loads(value) if isinstance(value, str) else value
    if "result" in result and "exceptionDetails" in result["result"]:
        err = result["result"]["exceptionDetails"]
        print(f"JS error: {err.get('text', 'unknown')}", file=sys.stderr)
        return None
    return None


# ---------------------------------------------------------------------------
# CDP helpers — v4 (multi-browser fallback, persistent WebSocket)
# ---------------------------------------------------------------------------

def find_available_browser():
    """Probe browser CDP ports in priority order; return browser dict or None."""
    for b in BROWSERS:
        try:
            conn = HTTPConnection(b["host"], b["port"], timeout=3)
            conn.request("GET", "/json")
            resp = conn.getresponse()
            if resp.status == 200:
                resp.read()
                conn.close()
                return b
            conn.close()
        except Exception:
            pass
    return None


def detect_cloudflare(ws):
    """Return True if the current page is a Cloudflare challenge."""
    check_js = r"""
    (() => {
        const text = document.body ? document.body.innerText : '';
        const hasCF = text.includes('Verify you are human')
                   || text.includes('security check');
        const hasCFFrame = document.querySelector(
          'iframe[src*="challenges.cloudflare.com"]') !== null;
        return hasCF || hasCFFrame;
    })()
    """
    try:
        result = cdp_evaluate(ws, check_js, timeout=5)
        return result is True
    except Exception:
        return False


def restart_browser(browser):
    """Kill and relaunch a browser to bypass Cloudflare; return success bool."""
    name = browser["name"]
    port = browser["port"]
    print(f"Restarting {name} to bypass Cloudflare...", file=sys.stderr)

    try:
        if name == "Comet":
            subprocess.run(["pkill", "-f", "Comet.app"], timeout=5, capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "Google Chrome"], timeout=5, capture_output=True)
    except Exception:
        pass
    time.sleep(3)

    try:
        subprocess.Popen(
            [browser["bin"]] + browser["args"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"  Failed to restart {name}: {e}", file=sys.stderr)
        return False

    for _ in range(10):
        time.sleep(1)
        try:
            conn = HTTPConnection(browser["host"], port, timeout=3)
            conn.request("GET", "/json")
            resp = conn.getresponse()
            if resp.status == 200:
                resp.read()
                conn.close()
                print(f"  {name} restarted successfully (port {port})", file=sys.stderr)
                return True
            conn.close()
        except Exception:
            pass

    print(f"  {name} CDP port still unreachable after restart", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# CDP navigation primitives
# ---------------------------------------------------------------------------

_cdp_msg_id = 0


def cdp_create_tab(url="about:blank", browser=None):
    """Create a new browser tab via CDP HTTP API; return (tab_id, ws_url)."""
    b = browser or find_available_browser() or BROWSERS[0]
    host, port = b["host"], b["port"]
    try:
        import urllib.parse
        encoded_url = urllib.parse.quote(url, safe=":/?=&")
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("PUT", f"/json/new?{encoded_url}")
        resp = conn.getresponse()
        if resp.status == 200:
            tab = json.loads(resp.read().decode())
            conn.close()
            tab_id = tab.get("id")
            ws_url = tab.get("webSocketDebuggerUrl")
            if tab_id and ws_url:
                return tab_id, ws_url
        conn.close()
    except Exception as e:
        print(f"CDP create tab failed ({b['name']}): {e}", file=sys.stderr)
    return None, None


def cdp_close_tab(tab_id, browser=None):
    """Close a browser tab via CDP HTTP API."""
    b = browser or find_available_browser() or BROWSERS[0]
    host, port = b["host"], b["port"]
    try:
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("PUT", f"/json/close/{tab_id}")
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except Exception:
        return False


def cdp_connect(ws_url, timeout=30):
    """Open a persistent WebSocket connection to a browser tab."""
    import websocket
    return websocket.create_connection(ws_url, timeout=timeout)


def cdp_send(ws, method, params=None, timeout=30):
    """Send a CDP command and wait for the matching response."""
    global _cdp_msg_id
    _cdp_msg_id += 1
    msg_id = _cdp_msg_id

    payload = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params

    ws.send(json.dumps(payload))

    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        ws.settimeout(remaining)
        try:
            raw = ws.recv()
            resp = json.loads(raw)
            if resp.get("id") == msg_id:
                if "error" in resp:
                    raise RuntimeError(f"CDP error: {resp['error']}")
                return resp.get("result", {})
        except Exception as e:
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                break
            raise

    raise TimeoutError(f"CDP command {method} timed out after {timeout}s")


def cdp_evaluate(ws, expression, timeout=30):
    """Execute JS on a persistent WebSocket; return parsed value."""
    result = cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    }, timeout=timeout)

    inner = result.get("result", {})
    value = inner.get("value")

    if "exceptionDetails" in result:
        err = result["exceptionDetails"]
        print(f"JS error: {err.get('text', 'unknown')}", file=sys.stderr)
        return None

    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def cdp_navigate(ws, url, timeout=30):
    """Navigate to URL and wait for document.readyState === 'complete'."""
    try:
        cdp_send(ws, "Page.enable", timeout=5)
    except Exception:
        pass

    cdp_send(ws, "Page.navigate", {"url": url}, timeout=10)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            state = cdp_evaluate(ws, "document.readyState", timeout=5)
            if state == "complete":
                return True
        except (TimeoutError, RuntimeError):
            pass

    print(f"Navigation timed out: {url}", file=sys.stderr)
    return False


def cdp_wait_for_selector(ws, check_js, timeout=30, interval=0.5):
    """Poll a JS condition until truthy or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = cdp_evaluate(ws, check_js, timeout=5)
            if result:
                return True
        except (TimeoutError, RuntimeError):
            pass
        time.sleep(interval)
    return False


def cdp_fetch_page_content(ws, url, timeout=30):
    """Navigate to URL and extract page content; return dict or None."""
    if not cdp_navigate(ws, url, timeout=timeout):
        return None

    content_ready = cdp_wait_for_selector(
        ws,
        'document.querySelectorAll(\'[class*="prose"], [class*="answer"], [class*="markdown"]\').length > 0',
        timeout=20,
    )

    if not content_ready:
        has_content = cdp_wait_for_selector(
            ws,
            '(document.querySelector("main") || document.body).innerText.length > 200',
            timeout=10,
        )
        if not has_content:
            print(f"Content render timed out: {url}", file=sys.stderr)
            return None

    time.sleep(2)
    return cdp_evaluate(ws, EXTRACT_JS, timeout=10)


# ---------------------------------------------------------------------------
# Browser lifecycle helpers
# ---------------------------------------------------------------------------

def ensure_browser_running():
    """Ensure at least one browser CDP port is reachable; exit(2) otherwise."""
    browser = find_available_browser()
    if browser:
        return browser
    ports = ", ".join(f"{b['name']}:{b['port']}" for b in BROWSERS)
    print(f"All browser CDP ports unreachable ({ports})", file=sys.stderr)
    print("Start Comet or Chrome with remote debugging enabled.", file=sys.stderr)
    sys.exit(2)


def ensure_chrome_running():
    """Legacy compat: ensure a browser is running; return tab list."""
    browser = ensure_browser_running()
    try:
        conn = HTTPConnection(browser["host"], browser["port"], timeout=3)
        conn.request("GET", "/json")
        resp = conn.getresponse()
        tabs = json.loads(resp.read().decode())
        conn.close()
        return tabs
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Content formatting
# ---------------------------------------------------------------------------

def format_markdown(data):
    """Format extracted page data as Markdown."""
    lines = []
    title = data.get("title", "Untitled")
    if " - Perplexity" in title:
        title = title.split(" - Perplexity")[0].strip()

    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"> Source: {data.get('url', 'N/A')}")
    lines.append(f"> Fetched: {data.get('timestamp', datetime.now().isoformat())}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(data.get("content", ""))

    sources = data.get("sources", [])
    if sources:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Sources")
        lines.append("")
        for i, src in enumerate(sources, 1):
            lines.append(f"{i}. [{src.get('title', 'Link')}]({src.get('url', '')})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def load_cookies():
    """Load cookies from the configured JSON file."""
    if not os.path.exists(COOKIES_FILE):
        return None
    with open(COOKIES_FILE, "r") as f:
        return json.load(f)


def check_auth():
    """Validate Perplexity session cookie; return (status, message)."""
    cookies = load_cookies()
    if cookies is None:
        return "missing", f"Cookie file not found: {COOKIES_FILE}"

    has_auth = False
    for c in cookies:
        if c.get("name") == AUTH_COOKIE_NAME and c.get("value"):
            has_auth = True
            expires = c.get("expires", 0)
            if expires > 0 and expires < time.time():
                return "expired", "Cookie has expired"
            break

    if not has_auth:
        return "expired", "No auth token found in cookies"

    try:
        from urllib.request import Request, urlopen
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        req = Request(
            "https://www.perplexity.ai/api/auth/session",
            headers={
                "Cookie": cookie_str,
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36"
                ),
            },
        )
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        if data and data.get("user"):
            return "valid", f"Logged in as: {data['user'].get('name', 'unknown')}"
        return "expired", "Session invalid"
    except Exception as e:
        return "valid", f"Cookie present (online check failed: {e})"


def try_refresh_from_browser():
    """Attempt to refresh cookies using perplexity-login.py if available."""
    login_script = os.path.join(os.path.dirname(__file__), "perplexity-login.py")
    if not os.path.exists(login_script):
        return False
    try:
        result = subprocess.run(
            ["python3", login_script, "login"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Multi-browser session runner (Cloudflare detection + restart recovery)
# ---------------------------------------------------------------------------

def _try_cdp_session(callback, purpose="operation"):
    """Run callback(ws, browser) with multi-browser fallback and Cloudflare recovery.

    Tries each browser in priority order. If all are blocked by Cloudflare,
    attempts a browser restart before giving up.
    """
    # Phase 1: try each browser
    for browser in BROWSERS:
        tab_id, ws_url = cdp_create_tab(browser=browser)
        if not tab_id:
            print(f"  {browser['name']}: CDP unreachable, skipping", file=sys.stderr)
            continue

        ws = None
        try:
            ws = cdp_connect(ws_url)
            cdp_navigate(ws, "https://www.perplexity.ai/library", timeout=30)
            time.sleep(1)
            if detect_cloudflare(ws):
                print(
                    f"  {browser['name']}: Cloudflare challenge detected, trying next",
                    file=sys.stderr,
                )
                ws.close()
                ws = None
                cdp_close_tab(tab_id, browser=browser)
                continue

            print(f"  {browser['name']}: connected, starting {purpose}", file=sys.stderr)
            result = callback(ws, browser)
            return result
        except Exception as e:
            print(f"  {browser['name']}: {purpose} failed: {e}", file=sys.stderr)
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
            cdp_close_tab(tab_id, browser=browser)

    # Phase 2: restart and retry
    print(f"\nAll browsers blocked or unreachable, attempting restart...", file=sys.stderr)
    for browser in BROWSERS:
        if restart_browser(browser):
            tab_id, ws_url = cdp_create_tab(browser=browser)
            if not tab_id:
                continue
            ws = None
            try:
                ws = cdp_connect(ws_url)
                cdp_navigate(ws, "https://www.perplexity.ai/library", timeout=30)
                time.sleep(1)
                if detect_cloudflare(ws):
                    print(
                        f"  {browser['name']}: still blocked after restart",
                        file=sys.stderr,
                    )
                    ws.close()
                    ws = None
                    cdp_close_tab(tab_id, browser=browser)
                    continue
                print(f"  {browser['name']}: recovered after restart", file=sys.stderr)
                result = callback(ws, browser)
                return result
            except Exception as e:
                print(
                    f"  {browser['name']}: still failing after restart: {e}",
                    file=sys.stderr,
                )
            finally:
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass
                cdp_close_tab(tab_id, browser=browser)

    print(
        "\nAll browsers unavailable (Cloudflare + restart failed). Please check manually.",
        file=sys.stderr,
    )
    return None


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_check(args):
    """Check CDP port reachability for all configured browsers."""
    found_any = False
    for b in BROWSERS:
        try:
            conn = HTTPConnection(b["host"], b["port"], timeout=3)
            conn.request("GET", "/json")
            resp = conn.getresponse()
            if resp.status == 200:
                tabs = json.loads(resp.read().decode())
                conn.close()
                print(f"OK  {b['name']} (port {b['port']}): {len(tabs)} tab(s)")
                perp_tab = find_perplexity_tab(tabs)
                if perp_tab:
                    print(f"    Perplexity tab: {perp_tab.get('title', 'N/A')}")
                found_any = True
            else:
                conn.close()
                print(f"ERR {b['name']} (port {b['port']}): unreachable")
        except Exception:
            print(f"ERR {b['name']} (port {b['port']}): unreachable")

    if not found_any:
        print("\nNo browser with remote debugging is running.")
        sys.exit(2)


def cmd_read_current(args):
    """Read content from the currently active Perplexity tab."""
    tabs = check_chrome_debug()
    if tabs is None:
        print("Chrome debug port unreachable", file=sys.stderr)
        sys.exit(2)

    tab = find_perplexity_tab(tabs)
    if not tab:
        page_tabs = [t for t in tabs if t.get("type") == "page"]
        if page_tabs:
            tab = page_tabs[-1]
            print(
                f"No Perplexity tab found; using current tab: {tab.get('title', 'N/A')}",
                file=sys.stderr,
            )
        else:
            print("No available browser tabs", file=sys.stderr)
            sys.exit(1)

    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        print("Tab has no WebSocket URL", file=sys.stderr)
        sys.exit(1)

    print(f"Reading: {tab.get('title', 'N/A')}", file=sys.stderr)

    data = evaluate_js(ws_url, EXTRACT_JS)
    if not data:
        print("Content extraction failed", file=sys.stderr)
        sys.exit(1)

    fmt = getattr(args, "format", "markdown")
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_markdown(data))


def cmd_check_auth(args):
    """Validate Perplexity session cookie; attempt refresh if invalid."""
    status, msg = check_auth()
    if status != "valid":
        print(f"{status} — {msg}. Attempting refresh...", file=sys.stderr)
        if try_refresh_from_browser():
            status, msg = check_auth()

    print(f"{status} — {msg}")
    if status != "valid":
        print(
            "Ensure a browser with Perplexity logged in is running with remote debugging."
        )
        sys.exit(2)


def cmd_fetch(args):
    """Navigate to a Perplexity URL via CDP and extract its content."""
    url = args.url
    print(f"Fetching: {url}", file=sys.stderr)

    def _do_fetch(ws, browser):
        return cdp_fetch_page_content(ws, url)

    data = _try_cdp_session(_do_fetch, purpose="content extraction")
    if not data:
        print("Content extraction failed", file=sys.stderr)
        sys.exit(1)

    fmt = getattr(args, "format", "markdown")
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_markdown(data))


# ---------------------------------------------------------------------------
# Deep Research CDP Session (v7.0+)
# ---------------------------------------------------------------------------

import urllib.request as _urllib_request
import websocket as _websocket


_COMET = f"http://localhost:{_COMET_PORT}"
_COMET_BIN = "/Applications/Comet.app/Contents/MacOS/Comet"


class CDPSession:
    """CDP session wrapper for a single browser tab."""

    def __init__(self, ws_url):
        self.ws = _websocket.create_connection(ws_url, timeout=15)
        self._id = 0
        self._call("Page.enable")
        self._call("Runtime.enable")

    def _call(self, method, params=None, timeout=20):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                r = json.loads(self.ws.recv())
                if r.get("id") == mid:
                    if "error" in r:
                        raise RuntimeError(f"CDP {method} error: {r['error']}")
                    return r.get("result", {})
            except _websocket.WebSocketTimeoutException:
                break
        raise TimeoutError(f"CDP {method} timeout")

    def eval(self, expression, await_promise=True):
        r = self._call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        return r.get("result", {}).get("value")

    def insert_text(self, text):
        """CDP Input.insertText — works reliably with React contenteditable."""
        self._call("Input.insertText", {"text": text})

    def navigate(self, url):
        self._call("Page.navigate", {"url": url})

    def bring_to_front(self):
        try:
            self._call("Page.bringToFront")
        except Exception:
            pass

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Comet lifecycle helpers
# ---------------------------------------------------------------------------

def comet_alive():
    """Return True if Comet CDP endpoint is responding."""
    try:
        _urllib_request.urlopen(f"{_COMET}/json/version", timeout=2).read()
        return True
    except Exception:
        return False


def ensure_comet():
    """Start Comet if not running; return True on success."""
    if comet_alive():
        return True
    if not os.path.exists(_COMET_BIN):
        print(f"Comet.app not installed: {_COMET_BIN}", file=sys.stderr)
        return False
    print("Starting Comet on port 9223...", flush=True)
    subprocess.Popen(
        [
            "nohup",
            _COMET_BIN,
            f"--remote-debugging-port={_COMET_PORT}",
            "--remote-allow-origins=*",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(6):
        time.sleep(2)
        if comet_alive():
            return True
    return False


def new_tab():
    """Create a new Comet tab; return (tab_id, ws_url)."""
    r = _urllib_request.urlopen(
        _urllib_request.Request(f"{_COMET}/json/new", method="PUT"), timeout=10
    )
    d = json.loads(r.read())
    return d["id"], d["webSocketDebuggerUrl"]


def close_tab(tab_id):
    """Close a Comet tab by ID."""
    try:
        _urllib_request.urlopen(f"{_COMET}/json/close/{tab_id}", timeout=5).read()
    except Exception:
        pass


def activate_tab(tab_id):
    """Bring a Comet tab to the foreground."""
    try:
        _urllib_request.urlopen(f"{_COMET}/json/activate/{tab_id}", timeout=5).read()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Deep Research steps
# ---------------------------------------------------------------------------

def open_perplexity_tab(log):
    """Open a new Comet tab, navigate to perplexity.ai, and verify login."""
    log("[1] Creating new Comet tab...")
    tab_id, ws_url = new_tab()
    log(f"    tab_id={tab_id[:10]}...")
    ses = CDPSession(ws_url)

    log("[2] Navigating to https://www.perplexity.ai/")
    ses.navigate("https://www.perplexity.ai/")

    log("[3] Waiting 12 s for React hydration...")
    time.sleep(12)
    activate_tab(tab_id)
    ses.bring_to_front()

    log("[4] Verifying page is ready...")
    state = ses.eval("""({
      url: location.href,
      hasPlus: !!document.querySelector('button[aria-label="Add files or tools"]'),
      hasInput: !!document.querySelector('div[contenteditable="true"]'),
      hasModeToggle: !!([...document.querySelectorAll('button')].find(
        el => ['Search','Deep research','Model council','Max'].includes(
          (el.textContent||'').trim()
        )
      )),
    })""")
    log(f"    {state}")
    if not state or not state.get("hasInput"):
        raise RuntimeError(f"Perplexity tab not ready (no input): {state}")
    if not state.get("hasModeToggle") and not state.get("hasPlus"):
        raise RuntimeError(f"Perplexity tab not ready (no mode toggle): {state}")
    return tab_id, ses


def _find_mode_toggle_btn(ses):
    """Find the current mode toggle button text."""
    return ses.eval("""
(() => {
  const modeNames = ['Search', 'Deep research', 'Model council', 'Max',
                     'Learn step by step', 'Control browser'];
  const btn = [...document.querySelectorAll('button')].find(el =>
    modeNames.includes((el.textContent||'').trim())
  );
  return btn ? {found: true, text: (btn.textContent||'').trim()} : {found: false};
})()
""")


def activate_deep_mode(ses, log, max_retry=3):
    """Activate Deep Research mode via v6 dropdown or v5 plus-menu fallback."""
    for attempt in range(1, max_retry + 1):
        label = f"retry #{attempt}" if attempt > 1 else "attempt"
        log(f"[5] {label} — activating Deep research mode...")

        ses.eval("document.querySelector('[role=menu]') && document.body.click()")
        time.sleep(0.5)

        # Strategy A: v6 UI — mode toggle button
        toggle_info = _find_mode_toggle_btn(ses)
        log(f"    mode toggle button: {toggle_info}")

        if toggle_info and toggle_info.get("found"):
            if toggle_info.get("text") == "Deep research":
                log("    Already in Deep research mode")
                return True

            log(f"    [v6] clicking mode toggle '{toggle_info.get('text')}'...")
            ses.eval(_DISPATCH_JS + """
(() => {
  const modeNames = ['Search', 'Deep research', 'Model council', 'Max',
                     'Learn step by step', 'Control browser'];
  const btn = [...document.querySelectorAll('button')].find(el =>
    modeNames.includes((el.textContent||'').trim())
  );
  if (!btn) throw new Error('mode toggle button not found');
  click5(btn);
})()
""")
            time.sleep(2)

            menu = ses.eval("""
(() => {
  const m = document.querySelector('[role=menu]');
  return {visible: !!m, text: m ? m.innerText.slice(0, 300) : null};
})()
""")
            log(f"    dropdown: {menu}")

            if menu and menu.get("visible") and "Deep research" in (menu.get("text") or ""):
                log("[6] [v6] clicking Deep research menu item...")
                res = ses.eval(_DISPATCH_JS + """
(() => {
  const menu = document.querySelector('[role=menu]');
  if (!menu) return {err: 'no menu'};
  let target = [...menu.querySelectorAll('[role=menuitemradio]')]
    .find(el => /Deep research/i.test((el.textContent||'').trim()));
  if (!target) {
    target = [...menu.querySelectorAll('[role=menuitem]')]
      .find(el => /Deep research/i.test((el.textContent||'').trim()));
  }
  if (!target) {
    const leaf = [...menu.querySelectorAll('*')]
      .filter(el => {
        const ownText = Array.from(el.childNodes)
          .filter(n => n.nodeType === 3)
          .map(n => n.textContent.trim())
          .join('');
        return ownText === 'Deep research';
      }).pop();
    if (leaf) {
      target = leaf;
      let hops = 0;
      while (target && hops < 5 && target !== menu) {
        if (target.getAttribute('role') || ['BUTTON','A','LI'].includes(target.tagName)) break;
        target = target.parentElement;
        hops++;
      }
    }
  }
  if (!target) return {err: 'no Deep research item', menuText: menu.innerText.slice(0,100)};
  click5(target);
  return {
    clicked: target.tagName,
    role: target.getAttribute('role'),
    text: (target.textContent||'').trim().slice(0, 40)
  };
})()
""")
                log(f"    {res}")
                if res and not res.get("err"):
                    time.sleep(3)
                    log("[7] Verifying Deep research mode (v6)...")
                    mode = ses.eval("""
(() => {
  const menuGone = !document.querySelector('[role=menu]');
  const modeNames = ['Search', 'Deep research', 'Model council', 'Max',
                     'Learn step by step', 'Control browser'];
  const btn = [...document.querySelectorAll('button')].find(el =>
    modeNames.includes((el.textContent||'').trim())
  );
  const btnText = btn ? (btn.textContent||'').trim() : null;
  const input = document.querySelector('div[contenteditable="true"]');
  const card = input?.closest('div[class*="rounded"],form');
  const cardHasDeep = card ? card.innerText.includes('Deep research') : false;
  return {menuGone, inDeepMode: btnText === 'Deep research' || cardHasDeep,
          modeBtnText: btnText, cardHasDeep};
})()
""")
                    log(f"    {mode}")
                    if mode and mode.get("inDeepMode"):
                        return True
                    if attempt < max_retry:
                        log(f"    Mode not active, retrying ({attempt}/{max_retry})")
                        time.sleep(1.5)
                        continue
                    raise RuntimeError(
                        f"Failed to enter Deep research mode (v6, {max_retry} retries): {mode}"
                    )
                log(f"    v6 click failed: {res}")

        # Strategy B: v5 UI — plus-button menu
        log("    [v5 fallback] trying plus-button menu...")
        ses.eval("document.querySelector('[role=menu]') && document.body.click()")
        time.sleep(0.3)

        ses.eval(_DISPATCH_JS + """
(() => {
  const btn = document.querySelector('button[aria-label="Add files or tools"]');
  if (!btn) throw new Error('no + button (v5 fallback)');
  click5(btn);
})()
""")
        time.sleep(2)

        menu_v5 = ses.eval("""
(() => {
  const m = document.querySelector('[role=menu]');
  return {visible: !!m, text: m ? m.innerText.slice(0, 300) : null};
})()
""")
        log(f"    [v5] menu: {menu_v5}")
        if not menu_v5 or not menu_v5.get("visible") or "Deep research" not in (menu_v5.get("text") or ""):
            if attempt < max_retry:
                log("    v5 menu missing Deep research, retrying in 2s")
                time.sleep(2)
                continue
            raise RuntimeError(
                f"Both v6 and v5 failed — no Deep research in menu: "
                f"v6={toggle_info} v5={menu_v5}"
            )

        log("[6] [v5] clicking Deep research menu item...")
        res_v5 = ses.eval(_DISPATCH_JS + """
(() => {
  const menu = document.querySelector('[role=menu]');
  let target = [...menu.querySelectorAll('[role=menuitem],[role=menuitemradio]')]
    .find(el => /Deep research/i.test((el.textContent||'').trim()));
  if (!target) {
    const leaf = [...menu.querySelectorAll('*')]
      .filter(el => (el.textContent||'').trim() === 'Deep research').pop();
    if (!leaf) return {err: 'no Deep research item', menuItems: menu.innerText.slice(0,100)};
    target = leaf;
    let hops = 0;
    while (target && hops < 3 && !target.getAttribute('role') && target.tagName !== 'BUTTON') {
      if (['DIV','LI','A'].includes(target.tagName) &&
          target.parentElement?.getAttribute('role') === 'menu') break;
      target = target.parentElement;
      hops++;
    }
  }
  if (!target) return {err: 'no clickable target'};
  click5(target);
  return {clicked: target.tagName, role: target.getAttribute('role'),
          text: (target.textContent||'').trim().slice(0,40)};
})()
""")
        log(f"    {res_v5}")
        if not res_v5 or res_v5.get("err"):
            if attempt < max_retry:
                log("    Click failed, retrying")
                time.sleep(1.5)
                continue
            raise RuntimeError(f"Deep research click failed (v5): {res_v5}")

        time.sleep(3)

        log("[7] Verifying Deep research mode (v5)...")
        mode_v5 = ses.eval("""
(() => {
  const menuGone = !document.querySelector('[role=menu]');
  const modeNames = ['Search', 'Deep research', 'Model council', 'Max'];
  const btn = [...document.querySelectorAll('button')].find(el =>
    modeNames.includes((el.textContent||'').trim())
  );
  const input = document.querySelector('div[contenteditable="true"]');
  const card = input?.closest('div[class*="rounded"],form');
  return {
    menuGone,
    inDeepMode: (btn && (btn.textContent||'').trim() === 'Deep research')
              || (card ? card.innerText.includes('Deep research') : false),
    modeBtnText: btn ? (btn.textContent||'').trim() : null
  };
})()
""")
        log(f"    {mode_v5}")
        if mode_v5 and mode_v5.get("inDeepMode"):
            return True

        if attempt < max_retry:
            log(f"    Mode not active, retrying ({attempt}/{max_retry})")
            time.sleep(1.5)
            continue

        raise RuntimeError(
            f"Failed to enter Deep research mode ({max_retry} retries): {mode_v5}"
        )


def fill_query(ses, query, log):
    """Type the query into the Perplexity input via CDP Input.insertText."""
    log(f"[fill] Inserting query ({len(query)} chars via CDP Input.insertText)...")
    ses.eval("document.querySelector('div[contenteditable=\"true\"]').focus()")
    time.sleep(0.3)
    ses.insert_text(query)
    time.sleep(1)
    filled = ses.eval("""
(() => {
  const i = document.querySelector('div[contenteditable="true"]');
  return {text: (i.textContent||'').slice(0,200), len: (i.textContent||'').length};
})()
""")
    log(f"    Inserted: len={filled.get('len')} head='{(filled.get('text') or '')[:80]}...'")
    if not filled or filled.get("len", 0) < min(20, len(query) - 5):
        raise RuntimeError(f"Query not inserted or incomplete: {filled}")
    return True


def submit_deep(ses, log):
    """Submit the Deep Research query (consumes 1 quota)."""
    log("[submit_deep] Submitting Deep Research query...")
    submit_state = ses.eval("""
(() => {
  const s = document.querySelector('button[aria-label="Submit"]');
  return {exists: !!s, disabled: s?.disabled};
})()
""")
    if not submit_state or not submit_state.get("exists") or submit_state.get("disabled"):
        raise RuntimeError(f"Submit button not available: {submit_state}")

    ses.eval(_DISPATCH_JS + """
(() => {
  click5(document.querySelector('button[aria-label="Submit"]'));
})()
""")
    time.sleep(3)

    u = None
    for _ in range(15):
        u = ses.eval("location.href")
        if u and "/search/" in u:
            log(f"    URL -> {u[:80]}")
            return u
        time.sleep(1)
    raise RuntimeError(f"URL did not change to /search/ after submit: {u}")


def submit_quick(ses, log):
    """Submit a Quick Search query (no Deep quota consumed)."""
    log("[submit_quick] Submitting Quick Search query...")
    submit_state = ses.eval("""
(() => {
  const s = document.querySelector('button[aria-label="Submit"]');
  return {exists: !!s, disabled: s?.disabled};
})()
""")
    if not submit_state or not submit_state.get("exists") or submit_state.get("disabled"):
        raise RuntimeError(f"Submit button not available: {submit_state}")

    ses.eval(_DISPATCH_JS + """
(() => {
  click5(document.querySelector('button[aria-label="Submit"]'));
})()
""")
    time.sleep(3)

    u = None
    for _ in range(15):
        u = ses.eval("location.href")
        if u and "/search/" in u:
            log(f"    URL -> {u[:80]}")
            return u
        time.sleep(1)
    raise RuntimeError(f"URL did not change to /search/ after submit: {u}")


def poll_until_done(ses, timeout_s, wait_stable_s, log, poll_interval_s=30):
    """Poll for 'Prepared by Deep Research' signature and content stability."""
    log(
        f"[poll_until_done] Polling for completion "
        f"(timeout={timeout_s}s, stable={wait_stable_s}s, interval={poll_interval_s}s)..."
    )
    t0 = time.time()
    last_len = 0
    stable_since = None
    while time.time() - t0 < timeout_s:
        elapsed = int(time.time() - t0)
        state = ses.eval(r"""
(() => {
  const body = document.body.innerText;
  const els = [...document.querySelectorAll('[class*="prose"], [class*="markdown"]')];
  const answerLen = els.reduce((s,el)=>s+el.innerText.length, 0);
  const done = /Prepared by Deep Research/i.test(body);
  const srcMatch = body.match(/(\d+)\s*sources?/i);
  return {answerLen, done, sourcesClaim: srcMatch ? srcMatch[0] : null, bodyLen: body.length};
})()
""")
        if not state:
            time.sleep(5)
            continue
        al = state.get("answerLen", 0)
        log(
            f"    T+{elapsed:3d}s | answer={al:6d} | body={state.get('bodyLen'):5d} | "
            f"sources={state.get('sourcesClaim')} | done={state.get('done')}"
        )

        if state.get("done") and al > 500:
            if al == last_len:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= wait_stable_s:
                    log(f"    Complete (done marker + {wait_stable_s}s stable)")
                    return state
            else:
                stable_since = None
        last_len = al
        time.sleep(poll_interval_s)
    raise TimeoutError(f"Deep Research poll timed out after {timeout_s}s (last answerLen={last_len})")


def poll_quick_answer(ses, timeout_s, log):
    """Poll for Quick Search answer >= 500 chars (5s interval)."""
    log(f"[poll_quick_answer] Polling for answer (timeout={timeout_s}s, interval=5s)...")
    t0 = time.time()
    state = None
    while time.time() - t0 < timeout_s:
        elapsed = int(time.time() - t0)
        state = ses.eval(r"""
(() => {
  const els = [...document.querySelectorAll('[class*="prose"], [class*="markdown"]')];
  const answerLen = els.reduce((s,el)=>s+el.innerText.length, 0);
  const bodyLen = document.body.innerText.length;
  return {answerLen, bodyLen};
})()
""")
        if not state:
            time.sleep(5)
            continue
        al = state.get("answerLen", 0)
        log(f"    T+{elapsed:3d}s | answer={al} chars | body={state.get('bodyLen')} chars")
        if al >= 500:
            log(f"    Answer ready ({al} chars >= 500 threshold)")
            return state
        time.sleep(5)
    log(f"    Poll timed out after {timeout_s}s, attempting extraction anyway...")
    return state


def extract_markdown(ses, log):
    """Extract answer text with multi-level DOM fallback."""
    log("[extract_markdown] Extracting answer (prose -> markdown -> main -> body fallback)...")
    result = ses.eval("""
(() => {
  const el = document.querySelector('[class*="prose"]')
          || document.querySelector('[class*="markdown"]')
          || document.querySelector('[class*="answer"]')
          || document.querySelector('main')
          || document.body;
  if (!el) return null;
  const selectorUsed = el.matches('[class*="prose"]') ? 'prose'
                     : el.matches('[class*="markdown"]') ? 'markdown'
                     : el.matches('[class*="answer"]') ? 'answer'
                     : el.tagName === 'MAIN' ? 'main'
                     : 'body(fallback)';
  const body = document.body.innerText;
  const srcMatch = body.match(/(\\d+)\\s*sources?/i);
  return {
    text: el.innerText,
    length: el.innerText.length,
    selector: selectorUsed,
    sourcesClaim: srcMatch ? srcMatch[0] : null
  };
})()
""")
    if not result or not result.get("text"):
        raise RuntimeError("No answer container found (prose/markdown/answer/main/body all failed)")
    log(f"    Selector: {result.get('selector', 'unknown')}")
    log(f"    Extracted {result['length']} chars | sources={result.get('sourcesClaim')}")
    meta = ses.eval("({url: location.href, title: document.title})")
    return {
        "url": meta.get("url") if meta else None,
        "title": meta.get("title") if meta else None,
        "length": result["length"],
        "text": result["text"],
        "sources_claim": result.get("sourcesClaim"),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "selector": result.get("selector"),
    }


# ---------------------------------------------------------------------------
# deep-search command
# ---------------------------------------------------------------------------

def cmd_deep_search(args):
    """Run Perplexity Deep Research via CDP and write result to a Markdown file.

    Steps:
      1. Ensure Comet is running (auto-start if needed)
      2. Open a new Perplexity tab and verify login
      3. Activate Deep research mode (v6 dropdown + v5 fallback)
      4. Insert query via CDP Input.insertText
      5. Submit (consumes 1 Deep Research quota)
      6. Poll until 'Prepared by Deep Research' + content is stable
      7. Extract Markdown and write to output file
    """
    from pathlib import Path

    def log(m):
        print(m, flush=True)

    log("=== perplexity-reader deep-search ===")
    log(f"Query: {args.query}")
    log(f"Output: {args.output}")
    log(f"Timeout: {args.timeout}s  wait_stable: {args.wait_stable_s}s")

    if not comet_alive():
        log("Comet not running, attempting auto-start...")
        if not ensure_comet():
            print("Comet startup failed", file=sys.stderr)
            sys.exit(1)
        log("Comet started")

    tab_id = None
    ses = None
    try:
        tab_id, ses = open_perplexity_tab(log)
        activate_deep_mode(ses, log)
        fill_query(ses, args.query, log)

        if args.dry_run:
            log("\n[DRY-RUN] Query filled but not submitted. Remove --dry-run to submit.")
            sys.exit(4)

        submit_deep(ses, log)

        state = poll_until_done(
            ses,
            timeout_s=args.timeout,
            wait_stable_s=args.wait_stable_s,
            log=log,
            poll_interval_s=args.poll_interval,
        )

        result = extract_markdown(ses, log)
        result["query"] = args.query
        result["sources_claim"] = result.get("sources_claim") or state.get("sourcesClaim")

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        md = (
            f"# Perplexity Deep Research Result\n\n"
            f"**Query**: {args.query}\n\n"
            f"**URL**: {result['url']}\n\n"
            f"**Fetched**: {result['fetched_at']}\n\n"
            f"**Sources**: {result.get('sources_claim')}\n\n"
            f"**Length**: {result['length']} chars\n\n"
            f"---\n\n{result['text']}\n"
        )
        out.write_text(md, encoding="utf-8")
        log(f"\nSaved: {out} ({len(md)} bytes)")

    except TimeoutError as e:
        print(f"\nTimeout: {e}", file=sys.stderr)
        if ses:
            ses.close()
        if tab_id and not getattr(args, "keep_tab", False):
            close_tab(tab_id)
        sys.exit(2)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        if ses:
            ses.close()
        if tab_id and not getattr(args, "keep_tab", False):
            close_tab(tab_id)
        sys.exit(1)
    finally:
        if ses:
            ses.close()
        if tab_id and not getattr(args, "keep_tab", False):
            close_tab(tab_id)


# ---------------------------------------------------------------------------
# quick-search command
# ---------------------------------------------------------------------------

def cmd_quick_search(args):
    """Run Perplexity Quick Search via CDP (30-60 s) and write results to a file.

    Steps:
      1. Ensure Comet is running (auto-start if needed)
      2. Open a new Perplexity tab and verify login
      3. Insert query (no mode switch — stays in default Search mode)
      4. Submit (no Deep Research quota consumed)
      5. Poll until answer >= 500 chars
      6. Extract Markdown and write to <output_dir>/perplexity_quick/<slug>-<ts>.md
    """
    from pathlib import Path

    def log(m):
        print(m, flush=True)

    def slugify(text, maxlen=50):
        slug = re.sub(r"[^\w\s-]", "", text.lower())
        slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
        return slug[:maxlen] or "query"

    rounds = getattr(args, "rounds", 1)
    query = args.query
    output_dir = Path(args.output_dir)

    log("=== perplexity-reader quick-search ===")
    log(f"Query: {query}")
    log(f"Output dir: {output_dir}")
    log(f"Rounds: {rounds}")

    if not comet_alive():
        log("Comet not running, attempting auto-start...")
        if not ensure_comet():
            print("Comet startup failed", file=sys.stderr)
            sys.exit(1)
        log("Comet started")

    slug = slugify(query)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pq_dir = output_dir / "perplexity_quick"
    pq_dir.mkdir(parents=True, exist_ok=True)
    out_file = pq_dir / f"{slug}-{ts}.md"

    results_md = []
    success_rounds = 0

    for r_idx in range(1, rounds + 1):
        tab_id = None
        ses = None
        try:
            log(f"--- Round {r_idx}/{rounds} | query='{query[:60]}' ---")
            tab_id, ses = open_perplexity_tab(log)
            fill_query(ses, query, log)
            submit_quick(ses, log)
            poll_quick_answer(ses, timeout_s=60, log=log)
            result = extract_markdown(ses, log)
            result["query"] = query

            round_md = (
                f"## Round {r_idx}\n\n"
                f"**URL**: {result['url']}\n\n"
                f"**Sources**: {result.get('sources_claim', 'N/A')}\n\n"
                f"**Answer** ({result['length']} chars):\n\n"
                f"{result['text']}\n"
            )
            results_md.append(round_md)
            success_rounds += 1

        except Exception as e:
            log(f"Round {r_idx} error: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            results_md.append(f"## Round {r_idx}\n\n*ERROR: {e}*\n")
        finally:
            if ses:
                ses.close()
            if tab_id:
                close_tab(tab_id)

        if r_idx < rounds:
            time.sleep(2)

    header = (
        f"# Perplexity Quick Search — {query}\n\n"
        f"> Generated: {datetime.now().isoformat()} | "
        f"rounds={rounds} | channel=perplexity_quick (CDP Comet {_COMET_PORT})\n\n"
    )
    out_file.write_text(header + "\n".join(results_md), encoding="utf-8")
    log(f"\nSaved: {out_file} ({out_file.stat().st_size} bytes)")

    if success_rounds == 0:
        print(f"All {rounds} round(s) failed", file=sys.stderr)
        sys.exit(1)
    elif success_rounds < rounds:
        print(f"Partial success: {success_rounds}/{rounds}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Perplexity Reader v1.0 OSS — CDP-based Perplexity client "
            "(Comet + Chrome dual-browser fallback)"
        )
    )
    subparsers = parser.add_subparsers(dest="command", help="subcommand")

    # check
    subparsers.add_parser("check", help="Check browser CDP port reachability")

    # read-current
    p_read = subparsers.add_parser(
        "read-current", help="Extract content from the active Perplexity tab"
    )
    p_read.add_argument("--format", choices=["markdown", "json"], default="markdown")

    # check-auth
    subparsers.add_parser("check-auth", help="Validate Perplexity session cookie")

    # fetch
    p_fetch = subparsers.add_parser("fetch", help="Navigate to URL and extract content via CDP")
    p_fetch.add_argument("url", help="Perplexity page URL")
    p_fetch.add_argument("--format", choices=["markdown", "json"], default="markdown")

    # deep-search
    p_deep = subparsers.add_parser(
        "deep-search",
        help="Run Perplexity Deep Research (5-10 min, consumes 1 quota)",
    )
    p_deep.add_argument("query", help="Research query (50-300 chars recommended)")
    p_deep.add_argument(
        "--output", "-o",
        default="./deep_research_result.md",
        help="Output Markdown file path (default: ./deep_research_result.md)",
    )
    p_deep.add_argument(
        "--timeout", type=int, default=600,
        help="Poll timeout in seconds (default: 600)",
    )
    p_deep.add_argument(
        "--wait-stable-s", type=int, default=30,
        help="Seconds of content stability before declaring done (default: 30)",
    )
    p_deep.add_argument(
        "--poll-interval", type=int, default=30,
        help="Poll interval in seconds (default: 30)",
    )
    p_deep.add_argument(
        "--dry-run", action="store_true",
        help="Fill query but do not submit (zero quota consumed, useful for DOM testing)",
    )
    p_deep.add_argument(
        "--keep-tab", action="store_true",
        help="Keep the browser tab open after completion for manual review",
    )

    # quick-search
    p_quick = subparsers.add_parser(
        "quick-search",
        help="Run Perplexity Quick Search (30-60 s, no Deep quota consumed)",
    )
    p_quick.add_argument("query", help="Search query")
    p_quick.add_argument(
        "--output-dir", required=True,
        help="Output root directory (results written to <DIR>/perplexity_quick/<slug>-<ts>.md)",
    )
    p_quick.add_argument(
        "--rounds", type=int, default=1,
        help="Number of search rounds (default: 1)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "check": cmd_check,
        "read-current": cmd_read_current,
        "check-auth": cmd_check_auth,
        "fetch": cmd_fetch,
        "deep-search": cmd_deep_search,
        "quick-search": cmd_quick_search,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
