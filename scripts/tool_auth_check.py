#!/usr/bin/env python3
"""
ARGUS Pre-Flight Doctor — 7-channel cold-start guide + auto-fix + osascript popup

Usage:
  tool_auth_check.py                        # interactive mode (popup-guided)
  tool_auth_check.py --strict               # exit 1 if any required channel fails
  tool_auth_check.py --skip webaccess       # skip specified channel
  tool_auth_check.py --essential perplexity,nlm   # check only required channels
  tool_auth_check.py --json                 # machine-readable output
  tool_auth_check.py --no-popup             # no popup (cron/CI mode)

Channels:
  1. comet-9223       Comet (Perplexity) — port via ARGUS_COMET_PORT (default 9223)
  2. perplexity-auth  Cookie valid
  3. chrome-9222      Chrome debug — port via ARGUS_CHROME_PORT (default 9222)
  4. webaccess-proxy  CDP Proxy 3456 + DevToolsActivePort patch
  5. notebooklm-auth  Google OAuth
  6. bird-auth        Chrome default profile cookie
  7. xhs-scout        XHS MCP local service health (non-essential)

Each channel has 3 stages: detect → auto-fix / manual-guide → validate
Popup uses osascript (macOS); press Enter / click "Done" → re-validate. Times out with fallback skip.

Environment variables:
  ARGUS_COMET_PORT      Comet remote-debugging port (default: 9223)
  ARGUS_CHROME_PORT     Chrome remote-debugging port (default: 9222)
  ARGUS_XHS_MCP_URL     XHS MCP endpoint (default: http://localhost:18060/mcp)
  ARGUS_XHS_HEALTH_URL  XHS health endpoint (default: http://localhost:18060/health)
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ──────────────────────────── Helpers ────────────────────────────

HOME = Path.home()
PERPLEXITY_READER = HOME / ".claude/skills/shelf/perplexity-reader/scripts/perplexity-reader.py"
PERPLEXITY_LOGIN = HOME / ".claude/skills/shelf/perplexity-reader/scripts/perplexity-login.py"
WEBACCESS_CHECK = HOME / ".claude/skills/shelf/web-access/scripts/check-deps.sh"
CHROME_READER = HOME / ".claude/scripts/chrome-reader.py"
CHROME_DEBUG_PROFILE = HOME / ".chrome-debug-profile"
DEV_TOOLS_ACTIVE_PORT = HOME / "Library/Application Support/Google/Chrome/DevToolsActivePort"

POPUP_ENABLED = True
POPUP_TIMEOUT_S = 300   # 5min


def http_code(url: str, timeout: float = 2.0) -> int:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except Exception:
        return 0


def http_body(url: str, timeout: float = 3.0) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def run(cmd: list, timeout: int = 15, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)


def run_shell(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout)


def _is_headless() -> bool:
    """Detect whether running in a headless environment (no GUI / CI)."""
    # No TTY (cron / pipe)
    if not sys.stdout.isatty():
        return True
    return False


def popup(title: str, body: str, buttons=("Skip", "Done"), default: str = "Done",
          icon: str = "note", timeout: int = POPUP_TIMEOUT_S) -> str:
    """macOS osascript dialog. Returns clicked button text; returns 'TIMEOUT' / 'DISABLED' on timeout/disabled.
    Headless environments (no GUI) automatically fall back to stderr print + return 'HEADLESS'.
    """
    if not POPUP_ENABLED:
        print(f"  [popup disabled] {title}: {body[:80]}...")
        return "DISABLED"

    # Headless fallback: no TTY → print full instructions to stderr, skip osascript
    if _is_headless():
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[ARGUS DOCTOR — {title}]", file=sys.stderr)
        print(body, file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        return "HEADLESS"

    body_escaped = body.replace('"', '\\"').replace("\n", "\\n")
    buttons_str = ", ".join(f'"{b}"' for b in buttons)
    script = (
        f'display dialog "{body_escaped}" '
        f'buttons {{{buttons_str}}} '
        f'default button "{default}" '
        f'with title "{title}" '
        f'with icon {icon} '
        f'giving up after {timeout}'
    )
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout + 10)
        # osascript failure (no GUI session / headless desktop) → stderr fallback
        if r.returncode != 0 or "No display" in (r.stderr or "") or "not able to run" in (r.stderr or "").lower():
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"[ARGUS DOCTOR — {title}] (osascript fallback)", file=sys.stderr)
            print(body, file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            return "HEADLESS"
        out = r.stdout.strip()
        # Returns something like "button returned:Done, gave up:false"
        if "gave up:true" in out:
            return "TIMEOUT"
        for part in out.split(","):
            if "button returned" in part:
                return part.split(":", 1)[-1].strip()
        return "UNKNOWN"
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        print(f"  popup error: {e}")
        return "ERR"


def log_check(name: str, ok: bool, msg: str):
    icon = "✅" if ok else "❌"
    print(f"  {icon} {name}: {msg}")


# ──────────────────────────── Channel Checks ────────────────────────────

def check_comet_9223(interactive: bool = True) -> dict:
    """Perplexity dependency: Comet live (port from ARGUS_COMET_PORT, default 9223)."""
    comet_port = os.environ.get("ARGUS_COMET_PORT", "9223")
    comet_url = f"http://localhost:{comet_port}/json/version"
    if http_code(comet_url) == 200:
        return {"status": "ok", "msg": f"Comet {comet_port} live"}

    # Auto-start
    print(f"  Comet {comet_port} not ready, attempting auto-start...")
    comet_bin = "/Applications/Comet.app/Contents/MacOS/Comet"
    if not Path(comet_bin).exists():
        return {"status": "fail", "msg": f"Comet.app not installed ({comet_bin})"}

    subprocess.Popen(
        ["nohup", comet_bin, f"--remote-debugging-port={comet_port}", "--remote-allow-origins=*"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    for i in range(6):
        time.sleep(2)
        if http_code(comet_url) == 200:
            return {"status": "ok", "msg": f"Comet {comet_port} auto-started"}

    # Popup fallback
    if interactive:
        ret = popup(
            f"ARGUS Doctor — Comet {comet_port}",
            f"Auto-start of Comet failed.\nPlease run manually in terminal:\n\n    comet-debug\n\n(alias defined in ~/.zshrc)\nClick 'Started' when done; click 'Skip Perplexity' to skip the channel.",
            buttons=("Skip Perplexity", "Started"), default="Started",
        )
        if ret == "Started":
            for _ in range(5):
                if http_code(comet_url) == 200:
                    return {"status": "ok", "msg": f"Comet {comet_port} (manual)"}
                time.sleep(2)
            return {"status": "fail", "msg": f"Comet {comet_port} still unreachable"}
        return {"status": "skipped", "msg": "user skipped"}
    return {"status": "fail", "msg": f"Comet {comet_port} auto-start failed (non-interactive)"}


def check_perplexity_auth(interactive: bool = True) -> dict:
    """Perplexity cookie valid."""
    if not PERPLEXITY_READER.exists():
        return {"status": "fail", "msg": f"{PERPLEXITY_READER} not found"}

    r = run(["python3", str(PERPLEXITY_READER), "check-auth"], timeout=30)
    if "valid" in r.stdout.lower():
        return {"status": "ok", "msg": r.stdout.strip().split("\n")[-1][:80]}

    if interactive:
        ret = popup(
            "ARGUS Doctor — Perplexity Cookie",
            f"Perplexity cookie is invalid or missing.\nPlease run in terminal:\n\n    python3 {PERPLEXITY_LOGIN} login\n\nThis will extract the cookie from the running Comet via CDP.\nClick 'Logged In' when done.",
            buttons=("Skip Perplexity", "Logged In"), default="Logged In",
        )
        if ret == "Logged In":
            r2 = run(["python3", str(PERPLEXITY_READER), "check-auth"], timeout=30)
            if "valid" in r2.stdout.lower():
                return {"status": "ok", "msg": "valid (after login)"}
            return {"status": "fail", "msg": f"cookie still invalid: {r2.stdout[:100]}"}
        return {"status": "skipped", "msg": "user skipped"}
    return {"status": "fail", "msg": "cookie invalid (non-interactive)"}


def check_chrome_9222(interactive: bool = True) -> dict:
    """Chrome debug + DevToolsActivePort patch (port from ARGUS_CHROME_PORT, default 9222)."""
    chrome_port = os.environ.get("ARGUS_CHROME_PORT", "9222")
    chrome_url = f"http://localhost:{chrome_port}/json/version"
    if http_code(chrome_url) != 200:
        # Auto-start
        print(f"  Chrome {chrome_port} not ready, attempting auto-start...")
        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if not Path(chrome_bin).exists():
            return {"status": "fail", "msg": "Google Chrome.app not found"}
        subprocess.Popen(
            ["nohup", chrome_bin,
             f"--remote-debugging-port={chrome_port}",
             f"--user-data-dir={CHROME_DEBUG_PROFILE}",
             "--remote-allow-origins=*"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        for _ in range(6):
            time.sleep(2)
            if http_code(chrome_url) == 200:
                break
        else:
            if interactive:
                ret = popup(
                    f"ARGUS Doctor — Chrome {chrome_port}",
                    f"Auto-start of Chrome failed.\nPlease run manually in terminal:\n\n    chrome-debug\n\n(alias defined in ~/.zshrc)\nClick 'Started' when done.",
                    buttons=("Skip", "Started"), default="Started",
                )
                if ret != "Started":
                    return {"status": "skipped", "msg": "user skipped"}
                if http_code(chrome_url) != 200:
                    return {"status": "fail", "msg": f"Chrome {chrome_port} unreachable"}
            else:
                return {"status": "fail", "msg": f"Chrome {chrome_port} auto-start failed"}

    # DevToolsActivePort patch
    body = http_body(chrome_url)
    try:
        data = json.loads(body)
        ws_url = data.get("webSocketDebuggerUrl", "")
        # Extract ws path portion
        ws_path = ""
        for prefix in (f"ws://localhost:{chrome_port}", f"ws://127.0.0.1:{chrome_port}"):
            if ws_url.startswith(prefix):
                ws_path = ws_url[len(prefix):]
                break
        if ws_path:
            DEV_TOOLS_ACTIVE_PORT.parent.mkdir(parents=True, exist_ok=True)
            DEV_TOOLS_ACTIVE_PORT.write_text(f"{chrome_port}\n{ws_path}\n")
            return {"status": "ok", "msg": f"{chrome_port} + DevToolsActivePort patched ({ws_path[:40]})"}
    except Exception as e:
        return {"status": "fail", "msg": f"patch failed: {e}"}
    return {"status": "fail", "msg": "could not parse webSocketDebuggerUrl"}


def check_webaccess_proxy(interactive: bool = True) -> dict:
    """Web-access CDP Proxy 3456 + Chrome inspect authorization."""
    def targets_ok() -> tuple[bool, str]:
        body = http_body("http://localhost:3456/targets", timeout=5)
        if not body:
            return False, "no response"
        try:
            d = json.loads(body)
            if isinstance(d, list):
                return True, f"{len(d)} targets"
            if isinstance(d, dict) and "error" in d:
                return False, d["error"]
        except json.JSONDecodeError:
            return False, body[:60]
        return False, "unknown"

    ok, msg = targets_ok()
    if ok:
        return {"status": "ok", "msg": msg}

    # Ensure Chrome is available first
    chrome_port = os.environ.get("ARGUS_CHROME_PORT", "9222")
    if http_code(f"http://localhost:{chrome_port}/json/version") != 200:
        return {"status": "fail", "msg": f"Chrome {chrome_port} not ready (run chrome-9222 first)"}

    # Restart proxy
    print("  Restarting CDP Proxy...")
    run_shell("pkill -f cdp-proxy.mjs", timeout=5)
    time.sleep(1)
    if WEBACCESS_CHECK.exists():
        run([str(WEBACCESS_CHECK)], timeout=30)
        time.sleep(3)

    ok, msg = targets_ok()
    if ok:
        return {"status": "ok", "msg": msg}

    # Popup-guided Chrome inspect authorization
    if interactive:
        # Auto-open chrome://inspect
        subprocess.run(
            ["open", "-a", "Google Chrome", "chrome://inspect/#remote-debugging"],
            capture_output=True,
        )
        ret = popup(
            "ARGUS Doctor — Web-access CDP Authorization",
            "Chrome has opened chrome://inspect/#remote-debugging.\n\nPlease:\n1. Check ☑ 'Allow remote debugging for this browser instance'\n2. If prompted to restart Chrome, click Relaunch\n3. Click 'Authorized' when done\n\n(DevToolsActivePort patch already written; proxy should connect after authorization)",
            buttons=("Skip", "Authorized"), default="Authorized",
        )
        if ret == "Authorized":
            # Restart proxy and retry
            run_shell("pkill -f cdp-proxy.mjs", timeout=5)
            time.sleep(1)
            if WEBACCESS_CHECK.exists():
                run([str(WEBACCESS_CHECK)], timeout=30)
                time.sleep(3)
            ok, msg = targets_ok()
            if ok:
                return {"status": "ok", "msg": msg}
            return {"status": "fail", "msg": f"still failed: {msg}"}
        return {"status": "skipped", "msg": "user skipped"}
    return {"status": "fail", "msg": msg}


def check_notebooklm_auth(interactive: bool = True) -> dict:
    """NotebookLM Google OAuth."""
    r = run(["notebooklm", "status"], timeout=15)
    combined = (r.stdout + r.stderr).lower()
    # Loose match: covers "Authenticated as: xxx" (TOOL-SOP §2.1 expected) / "Notebook ID" (currently observed)
    if "authenticated" in combined or "notebook id" in combined:
        return {"status": "ok", "msg": "NLM authenticated"}
    # Fallback: "No notebook selected" does not mean not logged in; use list as auth signal
    # (logged-in but no notebook selected is a valid state; list success = auth token valid)
    if "no notebook selected" in combined or "use 'notebooklm use" in combined:
        r_list = run(["notebooklm", "list"], timeout=15)
        if r_list.returncode == 0 and ("notebook" in r_list.stdout.lower() or "│" in r_list.stdout):
            return {"status": "ok", "msg": "NLM authenticated (no active notebook)"}

    if interactive:
        ret = popup(
            "ARGUS Doctor — NotebookLM",
            "NotebookLM is not logged in.\nPlease run in terminal:\n\n    notebooklm login\n\nA browser will open to complete Google OAuth.\nClick 'Logged In' when done.",
            buttons=("Skip NLM", "Logged In"), default="Logged In",
        )
        if ret == "Logged In":
            r2 = run(["notebooklm", "status"], timeout=15)
            if "Notebook" in (r2.stdout + r2.stderr) or "Authenticated" in (r2.stdout + r2.stderr):
                return {"status": "ok", "msg": "NLM authenticated (after login)"}
            return {"status": "fail", "msg": "still not authenticated"}
        return {"status": "skipped", "msg": "user skipped"}
    return {"status": "fail", "msg": "not authenticated"}


def check_xhs_scout(interactive: bool = True) -> dict:
    """XHS MCP local service health check (port via ARGUS_XHS_HEALTH_URL / ARGUS_XHS_MCP_URL)."""
    health_url = os.environ.get("ARGUS_XHS_HEALTH_URL", "http://localhost:18060/health")
    mcp_url = os.environ.get("ARGUS_XHS_MCP_URL", "http://localhost:18060/mcp")
    if http_code(health_url) == 200:
        return {"status": "ok", "msg": f"XHS MCP healthy ({health_url})"}
    # Fallback: try MCP endpoint directly
    if http_code(mcp_url, timeout=3.0) not in (0,):
        return {"status": "ok", "msg": f"XHS MCP reachable ({mcp_url})"}

    if interactive:
        ret = popup(
            "ARGUS Doctor — XHS MCP",
            f"XHS MCP service not detected at {health_url}.\n\nPlease start the local XHS MCP service on this machine,\nthen click 'Started', or click 'Skip' to continue without XHS.",
            buttons=("Skip", "Started"), default="Started",
        )
        if ret == "Started":
            if http_code(health_url) == 200:
                return {"status": "ok", "msg": f"XHS MCP healthy (after start)"}
            return {"status": "fail", "msg": f"XHS MCP still unreachable at {health_url}"}
        return {"status": "skipped", "msg": "user skipped"}
    return {"status": "fail", "msg": f"XHS MCP unreachable at {health_url} (non-interactive)"}


def check_bird_auth(interactive: bool = True) -> dict:
    """Bird X/Twitter cookie."""
    r = run(["bird", "whoami"], timeout=15)
    combined = r.stdout + r.stderr
    if "@" in r.stdout:
        # Extract @handle
        import re
        m = re.search(r"@\w+", r.stdout)
        handle = m.group(0) if m else "unknown"
        return {"status": "ok", "msg": f"logged in as {handle}"}

    if interactive:
        ret = popup(
            "ARGUS Doctor — Bird (X/Twitter)",
            "Bird does not recognize a logged-in session.\n\nPlease verify:\n1. Chrome default profile is logged in at x.com\n2. If already logged in but bird still fails, run:\n\n    bird whoami --cookie-source chrome\n\nClick 'Logged In' when done.",
            buttons=("Skip Bird", "Logged In"), default="Logged In",
        )
        if ret == "Logged In":
            # Re-validate with same command as popup instructions: bird whoami --cookie-source chrome
            r2 = run(["bird", "whoami", "--cookie-source", "chrome"], timeout=15)
            if "@" in r2.stdout:
                return {"status": "ok", "msg": "bird logged in (after prompt)"}
            return {"status": "fail", "msg": "still not logged in"}
        return {"status": "skipped", "msg": "user skipped"}
    return {"status": "fail", "msg": "not logged in"}


# ──────────────────────────── Main ────────────────────────────

CHANNELS = [
    # (name, checker, essential)
    ("comet-9223",       check_comet_9223,       True),   # Perplexity dependency; port via ARGUS_COMET_PORT
    ("perplexity-auth",  check_perplexity_auth,  True),
    ("chrome-9222",      check_chrome_9222,      True),   # web-access/chrome-reader; port via ARGUS_CHROME_PORT
    ("webaccess-proxy",  check_webaccess_proxy,  False),  # optional
    ("notebooklm-auth",  check_notebooklm_auth,  True),
    ("bird-auth",        check_bird_auth,        False),
    ("xhs-scout",        check_xhs_scout,        False),  # local XHS MCP; URL via ARGUS_XHS_HEALTH_URL
]


def main():
    global POPUP_ENABLED

    parser = argparse.ArgumentParser(description="ARGUS Pre-Flight Doctor")
    parser.add_argument("--skip", default="", help="Comma-separated channel names to skip")
    parser.add_argument("--essential", default="", help="Override essential list (comma-sep)")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any essential channel fails")
    parser.add_argument("--no-popup", action="store_true", help="Disable osascript popup (CI/cron)")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    if args.no_popup:
        POPUP_ENABLED = False

    skip_set = set(s.strip() for s in args.skip.split(",") if s.strip())
    essential_override = set(s.strip() for s in args.essential.split(",") if s.strip())

    results = {}
    start = time.time()

    if not args.json:
        print("=" * 60)
        print("ARGUS Pre-Flight Doctor")
        print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

    interactive = POPUP_ENABLED

    for name, checker, essential in CHANNELS:
        if name in skip_set:
            results[name] = {"status": "skipped", "msg": "CLI --skip", "essential": essential}
            if not args.json:
                log_check(name, True, "skipped (CLI)")
            continue

        if not args.json:
            print(f"\n[CHECK] {name}")
        try:
            res = checker(interactive=interactive)
            res["essential"] = essential if not essential_override else (name in essential_override)
            results[name] = res
            if not args.json:
                log_check(name, res["status"] == "ok", res["msg"])
        except Exception as e:
            results[name] = {"status": "error", "msg": str(e), "essential": essential}
            if not args.json:
                log_check(name, False, f"exception: {e}")

    elapsed = time.time() - start

    # Summary
    ok_cnt = sum(1 for r in results.values() if r["status"] == "ok")
    fail_cnt = sum(1 for r in results.values() if r["status"] in ("fail", "error"))
    skip_cnt = sum(1 for r in results.values() if r["status"] == "skipped")
    essential_fail = [n for n, r in results.items()
                      if r.get("essential") and r["status"] in ("fail", "error")]

    if args.json:
        print(json.dumps({
            "elapsed_s": round(elapsed, 1),
            "channels": results,
            "summary": {
                "ok": ok_cnt,
                "fail": fail_cnt,
                "skipped": skip_cnt,
                "essential_failed": essential_fail,
            },
        }, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 60)
        print(f"Done: {ok_cnt} ok / {fail_cnt} fail / {skip_cnt} skipped / {elapsed:.1f}s")
        if essential_fail:
            print(f"⚠️ Essential channels failed: {', '.join(essential_fail)}")
        print("=" * 60)

    if args.strict and essential_fail:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
