# ARGUS Tool Auth Checklist

> Read me when: running `probe.py doctor` or troubleshooting a cold-start channel auth failure.

Source: TOOL-SOP.md §0.1 Ironclad Rules + §1.1, §2.1, §3.1, §4.1, §5.1, §6.1 auth sections + `tool_auth_check.py` doctor flow

---

## Channel 1 — Comet 9223 (Perplexity dedicated)

| Field | Detail |
|-------|--------|
| **Start command** | `comet-debug` (alias in `~/.zshrc`) |
| **Equivalent command** | `nohup /Applications/Comet.app/Contents/MacOS/Comet --remote-debugging-port=9223 '--remote-allow-origins=*' >/dev/null 2>&1 &` + `sleep 5` |
| **Verification command** | `curl -s http://localhost:9223/json/version \| head -c 100` |
| **Success signal** | HTTP 200 + `Browser: Chrome/147...` |
| **Common pitfall** | `--remote-allow-origins=*` must be single-quoted; zsh glob expands `*`, leaving Chrome running but with the origin restriction still in place |
| **Fix command** | `pkill -9 -f Comet && sleep 2 && comet-debug` |

**Cookie extraction (one-time setup)**:
```bash
python3 ~/.claude/skills/shelf/perplexity-reader/scripts/perplexity-login.py login
# Cookie written to $PERPLEXITY_COOKIES_PATH (default: ~/.config/argus/cookies/perplexity.json)
python3 ~/.claude/skills/shelf/perplexity-reader/scripts/perplexity-reader.py check-auth
# Expected: valid — logged in as: <name>
```

---

## Channel 2 — Chrome 9222 (web-access + chrome-reader)

| Field | Detail |
|-------|--------|
| **Start command** | `chrome-debug` (alias in `~/.zshrc`) |
| **Equivalent command** | `nohup /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir=$HOME/.chrome-debug-profile '--remote-allow-origins=*' >/dev/null 2>&1 &` + `sleep 5` |
| **Verification command** | `curl -s http://localhost:9222/json/version \| head -c 100` |
| **Success signal** | HTTP 200 + `Browser: Chrome/...` |
| **Common pitfall** | **DevToolsActivePort path mismatch** (see patch below); Chrome upgrades may change origin-validation behavior |
| **Fix command** | Restart Chrome + apply the DevToolsActivePort patch (see below) |

### DevToolsActivePort Patch (required — without it, cdp-proxy connections will fail)

**Root cause**: `--user-data-dir=~/.chrome-debug-profile` causes Chrome to write `DevToolsActivePort` into the isolated profile directory. However, `cdp-proxy.mjs` only scans the default path `~/Library/Application Support/Google/Chrome/DevToolsActivePort`. The UUID-based `wsPath` is never found, so the WebSocket URL constructed by the proxy is invalid.

```bash
WS_URL=$(curl -s http://localhost:9222/json/version | python3 -c "import sys,json; print(json.load(sys.stdin)['webSocketDebuggerUrl'])")
WS_PATH=${WS_URL#ws://localhost:9222}
WS_PATH=${WS_PATH#ws://127.0.0.1:9222}
TARGET=~/Library/Application\ Support/Google/Chrome
mkdir -p "$TARGET"
printf "9222\n${WS_PATH}\n" > "$TARGET/DevToolsActivePort"
echo "DevToolsActivePort patched: $WS_PATH"
```

**One-time toggle (Chrome UI)**:
```bash
open -a "Google Chrome" "chrome://inspect/#remote-debugging"
# → Check ☑ Allow remote debugging for this browser instance
```

---

## Channel 3 — CDP Proxy 3456 (web-access)

| Field | Detail |
|-------|--------|
| **Start command** | `bash ~/.claude/skills/shelf/web-access/scripts/check-deps.sh` |
| **Prerequisites** | Chrome 9222 running + DevToolsActivePort patch applied |
| **Pre-start cleanup** | `pkill -f cdp-proxy.mjs 2>/dev/null` |
| **Verification command** | `curl -s http://localhost:3456/targets \| head -c 300` |
| **Success signal** | Non-empty JSON array with at least 1 target |
| **Common pitfall** | "Connection failed" in the proxy is caused by a DevToolsActivePort path mismatch in ~90% of cases — not a Chrome remote-debugging toggle issue |
| **Fix command** | Re-apply patch → `pkill -f cdp-proxy.mjs` → restart proxy |

---

## Channel 4 — NotebookLM (NLM)

| Field | Detail |
|-------|--------|
| **Start command** | `notebooklm login` (triggers Google OAuth browser popup) |
| **Verification command** | `notebooklm status` |
| **Success signal** | `Authenticated as: xxx` |
| **Additional verification** | `notebooklm list --json` → returns notebook list (empty list is valid) |
| **Common pitfall** | `auth check` failure requires re-running `notebooklm login`; session stored at `~/.notebooklm/storage_state.json` |
| **Fix command** | `rm ~/.notebooklm/storage_state.json && notebooklm login` |

---

## Channel 5 — Bird (X/Twitter)

| Field | Detail |
|-------|--------|
| **Start command** | None required (CLI tool) |
| **Verification command** | `bird whoami` |
| **Success signal** | `@handle (display name)` |
| **Common pitfall** | Safari cookie `EPERM` is intermittent — ignore it; Chrome default profile cookies work fine |
| **Fix command** | Confirm Chrome is logged in to x.com; Safari permission errors can be disregarded |

---

## Doctor Flow Summary (probe.py Stage 0)

`tool_auth_check.py` checks channels in the following priority order:

| Channel ID | Essential (exit 1 on fail) | Optional (manifest skipped) |
|------------|---------------------------|----------------------------|
| `comet-9223` | Yes (Perplexity dependency) | — |
| `perplexity-auth` | Yes | — |
| `chrome-9222` | Yes (web-access dependency) | — |
| `notebooklm-auth` | Yes | — |
| `bird` | — | Yes |
| `cdp-proxy-3456` | — | Yes (can be rebuilt if Chrome is healthy) |

**Commands**:
```bash
# Full check
python3 scripts/tool_auth_check.py

# Essential channels only
python3 scripts/tool_auth_check.py --essential comet-9223,perplexity-auth,chrome-9222,notebooklm-auth

# Strict mode (exit 1 immediately on any essential failure)
python3 scripts/tool_auth_check.py --strict --json
```

**Exit codes**: 0 all-green / 1 essential-fail / 2 partial
