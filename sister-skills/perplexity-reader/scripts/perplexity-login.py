#!/usr/bin/env python3
"""Perplexity Login -- extract auth cookies from running browser CDP

Usage:
  perplexity-login.py login    Extract Perplexity cookies from Comet/Chrome via CDP
  perplexity-login.py check    Validate stored cookies

Prerequisites:
  Comet (comet-debug) or Chrome (chrome-debug) must be running with remote debugging enabled.

Cookie storage path (configurable via env var):
  PERPLEXITY_COOKIES_PATH  -- defaults to ~/.config/argus/cookies/perplexity.json
"""

import sys
import os
import json
import time
from pathlib import Path
from http.client import HTTPConnection

# ---------------------------------------------------------------------------
# Configuration -- all paths driven by environment variables, no hardcoding
# ---------------------------------------------------------------------------
COOKIES_PATH = Path(os.environ.get(
    "PERPLEXITY_COOKIES_PATH",
    str(Path.home() / ".config" / "argus" / "cookies" / "perplexity.json")
))
COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)

# Browser CDP endpoints: Comet preferred, Chrome as fallback
BROWSER_PORTS = [
    {"name": "Comet", "host": "localhost", "port": 9223},
    {"name": "Chrome", "host": "localhost", "port": 9222},
]

PERPLEXITY_URL = "https://www.perplexity.ai"
AUTH_COOKIE_NAME = "__Secure-next-auth.session-token"


# ---------------------------------------------------------------------------
# Cookie I/O helpers
# ---------------------------------------------------------------------------

def save_cookies(cookies):
    """Save cookies to disk in Playwright-compatible JSON format."""
    # Filter to perplexity.ai domain only
    perp_cookies = [c for c in cookies if "perplexity.ai" in c.get("domain", "")]

    # Convert CDP cookie format -> Playwright format
    pw_cookies = []
    for c in perp_cookies:
        pw_cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "expires": c.get("expires", -1),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": c.get("sameSite", "Lax"),
        }
        pw_cookies.append(pw_cookie)

    with open(COOKIES_PATH, "w") as f:
        json.dump(pw_cookies, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(pw_cookies)} cookie(s) to {COOKIES_PATH}")


def has_auth_cookie(cookies):
    """Return True if the auth session token cookie is present and non-empty."""
    for c in cookies:
        if c.get("name") == AUTH_COOKIE_NAME and c.get("value"):
            return True
    return False


def load_cookies():
    """Load cookies from disk. Returns None if file does not exist."""
    if not COOKIES_PATH.exists():
        return None
    with open(COOKIES_PATH, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CDP browser connection
# ---------------------------------------------------------------------------

def get_browser_ws_url():
    """Try browser CDP endpoints in priority order; return the first working WebSocket URL."""
    for b in BROWSER_PORTS:
        try:
            conn = HTTPConnection(b["host"], b["port"], timeout=3)
            conn.request("GET", "/json")
            resp = conn.getresponse()
            if resp.status == 200:
                tabs = json.loads(resp.read().decode())
                conn.close()
                # Prefer tabs of type "page"
                for tab in tabs:
                    if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                        print(f"Using {b['name']} (port {b['port']})", file=sys.stderr)
                        return tab["webSocketDebuggerUrl"]
                # Fall back to any tab with a debugger URL
                for tab in tabs:
                    if tab.get("webSocketDebuggerUrl"):
                        print(f"Using {b['name']} (port {b['port']})", file=sys.stderr)
                        return tab["webSocketDebuggerUrl"]
            conn.close()
        except Exception:
            pass
    ports = ", ".join(f"{b['name']}:{b['port']}" for b in BROWSER_PORTS)
    print(f"ERROR: No browser CDP endpoint reachable ({ports})", file=sys.stderr)
    return None


def extract_cookies_from_browser():
    """Extract all cookies from the running browser via CDP (Network.getAllCookies)."""
    try:
        import websocket
    except ImportError:
        print(
            "Error: websocket-client is required. Run: pip3 install websocket-client",
            file=sys.stderr,
        )
        sys.exit(1)

    ws_url = get_browser_ws_url()
    if not ws_url:
        print(
            "Please start a browser with remote debugging: comet-debug or chrome-debug",
            file=sys.stderr,
        )
        sys.exit(2)

    ws = websocket.create_connection(ws_url, timeout=10)
    msg = json.dumps({"id": 1, "method": "Network.getAllCookies"})
    ws.send(msg)
    result = json.loads(ws.recv())
    ws.close()

    if "result" in result and "cookies" in result["result"]:
        return result["result"]["cookies"]

    print(f"ERROR: CDP cookie extraction failed: {result}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_login():
    """Extract Perplexity cookies from the running Comet/Chrome browser."""
    print("Extracting Perplexity cookies from browser...")

    all_cookies = extract_cookies_from_browser()
    if all_cookies is None:
        sys.exit(1)

    perp_cookies = [c for c in all_cookies if "perplexity.ai" in c.get("domain", "")]

    if not perp_cookies:
        print("ERROR: No Perplexity cookies found in browser.", file=sys.stderr)
        print("Please log in to Perplexity in Comet or Chrome first.", file=sys.stderr)
        sys.exit(1)

    if not has_auth_cookie(perp_cookies):
        print("ERROR: Perplexity cookies found but auth token is missing.", file=sys.stderr)
        print("Please log in to Perplexity in Comet or Chrome first.", file=sys.stderr)
        sys.exit(1)

    save_cookies(perp_cookies)
    print("OK: Cookie extraction successful.")


def cmd_check():
    """Validate stored Perplexity cookies (offline + online check)."""
    cookies = load_cookies()
    if cookies is None:
        print(f"missing -- Cookie file not found ({COOKIES_PATH}). Run: perplexity-login.py login")
        sys.exit(2)

    if not has_auth_cookie(cookies):
        print("expired -- Auth token absent in stored cookies. Please re-login.")
        sys.exit(2)

    # Local expiry check
    for c in cookies:
        if c.get("name") == AUTH_COOKIE_NAME:
            expires = c.get("expires", 0)
            if expires > 0 and expires < time.time():
                print("expired -- Cookie has expired. Please re-login.")
                sys.exit(2)
            break

    # Online session validation
    try:
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError

        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        req = Request(
            f"{PERPLEXITY_URL}/api/auth/session",
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
            print(f"valid -- Logged in as: {data['user'].get('name', 'unknown')}")
            return
        else:
            print("expired -- Session invalid. Please re-login.")
            sys.exit(2)
    except HTTPError as e:
        if e.code in (401, 403):
            print("expired -- Cookie rejected by server. Please re-login.")
            sys.exit(2)
        print(f"error -- Validation request failed: HTTP {e.code}")
        sys.exit(1)
    except Exception as e:
        # Treat as valid if online check fails (network issue, not auth issue)
        print(f"valid -- Cookie present (online validation failed: {e})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "login":
        cmd_login()
    elif cmd == "check":
        cmd_check()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
