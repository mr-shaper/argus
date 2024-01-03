#!/usr/bin/env python3
"""Chrome Reader — extract page content via Chrome DevTools Protocol (CDP).
Used as WebAccess fallback in Argus orchestrator.

Connects to Chrome debug port (9222) and provides:
  check                      Check CDP port status
  list-tabs [--filter PAT]   List all open tabs
  read-tab <url-pattern>     Read content of an already-open tab
  read-url <url>             Open new tab -> navigate -> extract -> close
  screenshot <url-pattern>   Screenshot a tab and save to /tmp/

Port assignment: 9222 (Chrome) — never touch 9223 (Comet/Perplexity)
"""

import argparse
import base64
import fcntl
import json
import os
import re
import sys
import time
from http.client import HTTPConnection

# --- Constants ---
CDP_HOST = "localhost"
CDP_PORT = 9222
LOCK_FILE = os.path.expanduser("~/.cdp-9222.lock")

# Generic content extraction JS
EXTRACT_JS = r"""
(() => {
  const result = {};

  // Title
  result.title = document.title || '';

  // URL
  result.url = window.location.href;

  // Timestamp
  result.timestamp = new Date().toISOString();

  // Meta
  const getMeta = (name) => {
    const el = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
    return el ? el.getAttribute('content') : '';
  };
  result.meta = {
    description: getMeta('description') || getMeta('og:description'),
    ogTitle: getMeta('og:title'),
    ogImage: getMeta('og:image'),
    author: getMeta('author'),
  };

  // Main content detection (heuristic)
  const candidates = [
    document.querySelector('article'),
    document.querySelector('[role="main"]'),
    document.querySelector('main'),
    document.querySelector('.content'),
    document.querySelector('#content'),
    document.querySelector('.post-content'),
    document.querySelector('.article-content'),
  ].filter(Boolean);

  let mainEl = null;
  if (candidates.length > 0) {
    // Pick the longest candidate
    mainEl = candidates.reduce((a, b) =>
      (a.innerText || '').length >= (b.innerText || '').length ? a : b
    );
  }
  if (!mainEl || (mainEl.innerText || '').length < 100) {
    mainEl = document.body;
  }

  result.content = (mainEl.innerText || '').substring(0, 50000);

  // Links
  const links = [];
  const seenUrls = new Set();
  mainEl.querySelectorAll('a[href]').forEach(a => {
    const href = a.href;
    const text = (a.innerText || '').trim().substring(0, 100);
    if (href && !seenUrls.has(href) && text && !href.startsWith('javascript:')) {
      seenUrls.add(href);
      links.push({href, text});
    }
  });
  result.links = links.slice(0, 50);

  // Images
  const images = [];
  mainEl.querySelectorAll('img[src]').forEach(img => {
    const src = img.src;
    const alt = (img.alt || '').trim();
    if (src && !src.startsWith('data:')) {
      images.push({src, alt});
    }
  });
  result.images = images.slice(0, 20);

  // Tables
  const tables = [];
  mainEl.querySelectorAll('table').forEach((table, idx) => {
    if (idx >= 5) return;
    const rows = [];
    table.querySelectorAll('tr').forEach((tr, ri) => {
      if (ri >= 50) return;
      const cells = [];
      tr.querySelectorAll('th, td').forEach(cell => {
        cells.push((cell.innerText || '').trim().substring(0, 200));
      });
      rows.push(cells);
    });
    tables.push(rows);
  });
  result.tables = tables;

  return JSON.stringify(result);
})()
"""

# --- File lock ---
_lock_fd = None


def acquire_lock():
    """Acquire file lock to prevent concurrent CDP access."""
    global _lock_fd
    try:
        _lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (IOError, OSError):
        print("⚠️  Another CDP process is using port 9222, waiting...", file=sys.stderr)
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_EX)  # blocking wait
            return True
        except Exception:
            return False


def release_lock():
    """Release the file lock."""
    global _lock_fd
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
        except Exception:
            pass
        _lock_fd = None


# --- CDP core functions ---

def check_cdp():
    """Check whether the Chrome debug port is reachable; return tab list or None."""
    try:
        conn = HTTPConnection(CDP_HOST, CDP_PORT, timeout=3)
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


def list_page_tabs(tabs):
    """Filter the tab list to only page-type entries."""
    return [t for t in tabs if t.get("type") == "page"]


def find_tab_by_pattern(tabs, pattern):
    """Find a tab by matching URL or title against the given pattern."""
    pattern_lower = pattern.lower()
    for tab in tabs:
        url = tab.get("url", "").lower()
        title = tab.get("title", "").lower()
        if pattern_lower in url or pattern_lower in title:
            return tab
    return None


def cdp_create_tab(url="about:blank"):
    """Create a new tab; return (tab_id, ws_url) or (None, None) on failure."""
    try:
        import urllib.parse
        encoded_url = urllib.parse.quote(url, safe=':/')
        conn = HTTPConnection(CDP_HOST, CDP_PORT, timeout=5)
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
        print(f"CDP create tab failed: {e}", file=sys.stderr)
    return None, None


def cdp_close_tab(tab_id):
    """Close the specified tab."""
    try:
        conn = HTTPConnection(CDP_HOST, CDP_PORT, timeout=5)
        conn.request("PUT", f"/json/close/{tab_id}")
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except Exception:
        return False


_cdp_msg_id = 0


def cdp_connect(ws_url, timeout=30):
    """Open a persistent WebSocket connection to the CDP endpoint."""
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
    """Execute JavaScript and return the parsed result value."""
    result = cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    }, timeout=timeout)

    inner = result.get("result", {})
    value = inner.get("value")

    if "exceptionDetails" in result:
        err = result["exceptionDetails"]
        print(f"JS execution error: {err.get('text', 'unknown')}", file=sys.stderr)
        return None

    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def cdp_navigate(ws, url, timeout=30):
    """Navigate to a URL and wait until readyState === 'complete'."""
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


def cdp_screenshot(ws, path=None):
    """Capture a screenshot of the current page; return the saved file path."""
    result = cdp_send(ws, "Page.captureScreenshot", {
        "format": "png",
        "quality": 90,
    }, timeout=15)

    data = result.get("data", "")
    if not data:
        print("Screenshot failed: no data returned", file=sys.stderr)
        return None

    if not path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = f"/tmp/chrome_screenshot_{ts}.png"

    with open(path, "wb") as f:
        f.write(base64.b64decode(data))

    return path


# --- Command implementations ---

def cmd_check():
    """Check Chrome debug port status."""
    tabs = check_cdp()
    if tabs is None:
        print("❌ Chrome debug port (9222) is not reachable", file=sys.stderr)
        print("\nRun: chrome-debug", file=sys.stderr)
        print("(alias defined in ~/.zshrc)", file=sys.stderr)
        sys.exit(2)

    pages = list_page_tabs(tabs)
    print(f"✅ Chrome CDP available — {len(pages)} page tab(s)")
    for p in pages[:10]:
        title = p.get("title", "")[:60]
        url = p.get("url", "")[:80]
        print(f"  [{p.get('id', '')[:8]}] {title}")
        print(f"           {url}")
    if len(pages) > 10:
        print(f"  ... and {len(pages) - 10} more tab(s)")


def cmd_list_tabs(filter_pattern=None):
    """List open tabs, optionally filtered by URL/title pattern."""
    tabs = check_cdp()
    if tabs is None:
        print("❌ Chrome debug port (9222) is not reachable", file=sys.stderr)
        sys.exit(2)

    pages = list_page_tabs(tabs)
    if filter_pattern:
        pat = filter_pattern.lower()
        pages = [p for p in pages if pat in p.get("url", "").lower() or pat in p.get("title", "").lower()]

    output = []
    for p in pages:
        output.append({
            "id": p.get("id", ""),
            "title": p.get("title", ""),
            "url": p.get("url", ""),
        })

    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_read_tab(pattern):
    """Read content from an already-open tab matching the given pattern."""
    acquire_lock()
    try:
        tabs = check_cdp()
        if tabs is None:
            print("❌ Chrome debug port (9222) is not reachable", file=sys.stderr)
            sys.exit(2)

        pages = list_page_tabs(tabs)
        tab = find_tab_by_pattern(pages, pattern)
        if not tab:
            print(f"❌ No tab found matching '{pattern}'", file=sys.stderr)
            print("Available tabs:", file=sys.stderr)
            for p in pages[:5]:
                print(f"  {p.get('title', '')[:50]} — {p.get('url', '')[:60]}", file=sys.stderr)
            sys.exit(1)

        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            print("❌ Tab has no WebSocket URL", file=sys.stderr)
            sys.exit(1)

        print(f"📖 Reading: {tab.get('title', '')}", file=sys.stderr)
        ws = cdp_connect(ws_url)
        try:
            # Wait for content to settle
            time.sleep(1)
            data = cdp_evaluate(ws, EXTRACT_JS)
            if data:
                print(format_markdown(data))
            else:
                print("❌ Content extraction failed", file=sys.stderr)
                sys.exit(1)
        finally:
            ws.close()
    finally:
        release_lock()


def cmd_read_url(url):
    """Open a new tab, navigate to URL, extract content, then close the tab."""
    acquire_lock()
    tab_id = None
    try:
        tab_id, ws_url = cdp_create_tab(url)
        if not tab_id:
            print(f"❌ Could not create tab for: {url}", file=sys.stderr)
            sys.exit(1)

        print(f"📖 Navigating to: {url}", file=sys.stderr)
        ws = cdp_connect(ws_url)
        try:
            if not cdp_navigate(ws, url, timeout=30):
                print("⚠️  Page load may be incomplete", file=sys.stderr)

            # Extra wait for dynamic content
            time.sleep(2)
            data = cdp_evaluate(ws, EXTRACT_JS)
            if data:
                print(format_markdown(data))
            else:
                print("❌ Content extraction failed", file=sys.stderr)
                sys.exit(1)
        finally:
            ws.close()
    finally:
        if tab_id:
            cdp_close_tab(tab_id)
        release_lock()


def cmd_screenshot(pattern, output_path=None):
    """Take a screenshot of the tab matching the given pattern."""
    acquire_lock()
    try:
        tabs = check_cdp()
        if tabs is None:
            print("❌ Chrome debug port (9222) is not reachable", file=sys.stderr)
            sys.exit(2)

        pages = list_page_tabs(tabs)
        tab = find_tab_by_pattern(pages, pattern)
        if not tab:
            print(f"❌ No tab found matching '{pattern}'", file=sys.stderr)
            sys.exit(1)

        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            print("❌ Tab has no WebSocket URL", file=sys.stderr)
            sys.exit(1)

        ws = cdp_connect(ws_url)
        try:
            path = cdp_screenshot(ws, output_path)
            if path:
                print(f"✅ Screenshot saved: {path}")
            else:
                sys.exit(1)
        finally:
            ws.close()
    finally:
        release_lock()


# --- Output formatting ---

def format_markdown(data):
    """Format extracted page data as Markdown."""
    lines = []
    lines.append(f"# {data.get('title', 'Untitled')}")
    lines.append(f"\n> URL: {data.get('url', '')}")
    lines.append(f"> Extracted: {data.get('timestamp', '')}")

    meta = data.get('meta', {})
    if meta.get('description'):
        lines.append(f"\n**Description**: {meta['description']}")
    if meta.get('author'):
        lines.append(f"**Author**: {meta['author']}")

    content = data.get('content', '')
    if content:
        lines.append(f"\n---\n\n{content[:30000]}")

    links = data.get('links', [])
    if links:
        lines.append("\n## Links\n")
        for link in links[:30]:
            lines.append(f"- [{link['text']}]({link['href']})")

    images = data.get('images', [])
    if images:
        lines.append("\n## Images\n")
        for img in images[:10]:
            alt = img.get('alt', 'image')
            lines.append(f"- ![{alt}]({img['src']})")

    tables = data.get('tables', [])
    for i, table in enumerate(tables[:3]):
        if not table:
            continue
        lines.append(f"\n## Table {i+1}\n")
        for ri, row in enumerate(table[:30]):
            lines.append("| " + " | ".join(row) + " |")
            if ri == 0:
                lines.append("| " + " | ".join(["---"] * len(row)) + " |")

    return "\n".join(lines)


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(
        description="Chrome Reader — extract page content via Chrome DevTools Protocol (CDP)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s check                          Check Chrome debug port
  %(prog)s list-tabs                      List all open tabs
  %(prog)s list-tabs --filter github      Filter tabs by keyword
  %(prog)s read-tab github                Read content of a matching tab
  %(prog)s read-url https://github.com    Open new tab and read URL
  %(prog)s screenshot github              Screenshot the matching tab

Port: 9222 (Chrome) — never touch 9223 (Comet/Perplexity)
        """)

    subparsers = parser.add_subparsers(dest="command", help="subcommand")

    # check
    subparsers.add_parser("check", help="Check Chrome debug port status")

    # list-tabs
    p_list = subparsers.add_parser("list-tabs", help="List open tabs")
    p_list.add_argument("--filter", help="Filter by URL/title keyword")

    # read-tab
    p_read_tab = subparsers.add_parser("read-tab", help="Read content of an open tab")
    p_read_tab.add_argument("pattern", help="URL or title match pattern")
    p_read_tab.add_argument("--json", action="store_true", help="Output in JSON format")

    # read-url
    p_read_url = subparsers.add_parser("read-url", help="Open new tab and read URL")
    p_read_url.add_argument("url", help="Target URL")
    p_read_url.add_argument("--json", action="store_true", help="Output in JSON format")

    # screenshot
    p_screenshot = subparsers.add_parser("screenshot", help="Screenshot a tab")
    p_screenshot.add_argument("pattern", help="URL or title match pattern")
    p_screenshot.add_argument("-o", "--output", help="Output file path")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    try:
        import websocket  # noqa: F401
    except ImportError:
        print("Error: websocket-client is required. Run: pip3 install websocket-client", file=sys.stderr)
        sys.exit(1)

    if args.command == "check":
        cmd_check()
    elif args.command == "list-tabs":
        cmd_list_tabs(args.filter)
    elif args.command == "read-tab":
        cmd_read_tab(args.pattern)
    elif args.command == "read-url":
        cmd_read_url(args.url)
    elif args.command == "screenshot":
        cmd_screenshot(args.pattern, args.output)


if __name__ == "__main__":
    main()
