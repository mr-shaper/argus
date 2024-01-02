# Contributing to Argus

Thank you for your interest in contributing to Argus. This document covers how to set up a dev environment, submit pull requests, and report issues.

## Table of Contents

- [Dev Setup](#dev-setup)
- [Code Style](#code-style)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Reporting Issues](#reporting-issues)
- [Channel Contributions](#channel-contributions)
- [Out of Scope](#out-of-scope)

---

## Dev Setup

```bash
git clone <this-repo>.git argus
cd argus

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install core deps
pip install -r requirements.txt

# Install optional sister skills per README Prerequisites
pip install notebooklm-py
# git clone https://github.com/eze-is/web-access (for WebAccess channel)
# brew install gh && gh auth login     (for GitHub channel)

# Verify your channel setup
python3 scripts/probe.py doctor --no-popup
```

## Code Style

- **Python version**: 3.9+
- **Formatter**: PEP 8 (use `flake8` or `ruff`)
- **Line length**: 100 characters max
- **Imports**: standard library first, then third-party, then local
- **Docstrings**: required for public functions; Google style preferred
- **Type hints**: encouraged for new code

Run a quick lint check before submitting:

```bash
pip install ruff
ruff check scripts/
```

## Submitting a Pull Request

1. **Fork** the repo and create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Make your changes** — keep commits focused and atomic.

3. **Test locally** — at minimum run:
   ```bash
   python3 scripts/probe.py doctor --no-popup
   python3 -m py_compile scripts/probe.py scripts/github_fetch.py
   ```

4. **Check for PII** — before pushing, verify no private tokens, cookies, or personal paths are included:
   ```bash
   grep -rniE 'cookie|token|api.key|password|secret' scripts/ references/ \
     --include="*.py" --include="*.md" --include="*.json"
   ```
   Expected: 0 hits (or only variable names referencing env vars, not literal values).

5. **Open a PR** against `main` with a clear title and description:
   - What problem does this solve?
   - Which channel(s) are affected?
   - How was it tested?

6. **PR title format**: `[channel] short description` — e.g., `[nlm] fix 300-source overflow guard`

## Reporting Issues

Use GitHub Issues. Please include:

- **Environment**: OS, Python version, `probe.py doctor` output
- **Channel affected**: Perplexity / NotebookLM / Bird / WebAccess / GitHub / Other
- **Steps to reproduce**
- **Expected vs actual behavior**
- **Logs** (redact any tokens or personal data)

### Issue Labels

| Label | Meaning |
|-------|---------|
| `bug` | Something isn't working |
| `channel:perplexity` | Perplexity Quick/Deep |
| `channel:nlm` | NotebookLM |
| `channel:bird` | X/Twitter (Bird CLI) |
| `channel:webaccess` | WebAccess/Chrome crawl |
| `channel:github` | GitHub channel |
| `sister-skill` | Issue requires a bring-your-own sister skill |
| `enhancement` | Feature request |
| `good first issue` | Good entry point for new contributors |

## Channel Contributions

Argus uses a **bring-your-own sister skill** model for some channels (see README Prerequisites). Community contributions that provide open, installable equivalents for the "bring your own" channels are especially welcome:

- **Perplexity channel**: A public `perplexity-reader` replacement (any approach that respects Perplexity TOS) would unlock the Quick/Deep channels for more users.
- **Chrome reader**: A public, zero-dependency alternative to `chrome-reader.py` for WebAccess fallback.
- **Bird CLI**: Contributions that wrap a public X/Twitter API client into the `bird` interface expected by `probe.py`.

If you build one of these, open a PR or link it in an issue — we'll add it to the Prerequisites table.

## Out of Scope

The following are **intentionally out of scope** for OSS contributions:

- Proprietary browser integrations (Comet.app)
- Any feature that requires scraping in violation of a service's ToS
- Changes to the OODC-Observe state machine contract (`probe.py` Stage 0-4 flow)

## License

By contributing to Argus, you agree that your contributions will be licensed under the [Apache License 2.0](./LICENSE).

## perplexity-reader Reference Stub (BYO)

If `scripts/perplexity_quick.py` or `perplexity_deep.py` is called but `perplexity-reader` skill is not installed, `doctor` will report it missing. Below is a 90-line minimal stub that passes `check-auth` so doctor goes green; `quick`/`deep` return `NOT IMPLEMENTED` (you fill in the actual logic).

Save as `~/.claude/skills/shelf/perplexity-reader/scripts/perplexity-reader.py`:

```python
#!/usr/bin/env python3
"""Minimal perplexity-reader stub. Implements check-auth only; quick/deep are placeholders.

Implement Perplexity browser/HTTP integration based on your environment (CDP, Playwright, raw API, etc).
"""
import argparse, json, os, sys
from pathlib import Path

COOKIES_PATH = Path(os.environ.get("PERPLEXITY_COOKIES_PATH", str(Path.home() / ".config/argus/cookies/perplexity.json")))

def cmd_check_auth(args):
    if not COOKIES_PATH.exists():
        print(f"FAIL: cookies not found at {COOKIES_PATH}", file=sys.stderr); sys.exit(1)
    try:
        cookies = json.loads(COOKIES_PATH.read_text())
    except Exception as e:
        print(f"FAIL: cannot parse cookies: {e}", file=sys.stderr); sys.exit(1)
    # Accept presence of ANY perplexity.ai cookie (lenient — matches Comet behavior where
    # NextAuth session-token isn't exposed as web cookie)
    names = [c.get("name", "") for c in cookies] if isinstance(cookies, list) else list(cookies.keys())
    if any("pplx" in n or "comet" in n or "perplexity" in n for n in names):
        print(f"OK: {len(names)} cookie name(s) present"); sys.exit(0)
    print(f"FAIL: no perplexity-domain cookie among {names}", file=sys.stderr); sys.exit(1)

def cmd_quick(args):
    print("NOT IMPLEMENTED: replace this stub with your Perplexity quick-search integration", file=sys.stderr); sys.exit(2)

def cmd_deep(args):
    print("NOT IMPLEMENTED: replace this stub with your Perplexity deep-research integration", file=sys.stderr); sys.exit(2)

def cmd_login(args):
    print("NOT IMPLEMENTED: implement login flow that writes cookies to PERPLEXITY_COOKIES_PATH", file=sys.stderr); sys.exit(2)

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-auth")
    qp = sub.add_parser("quick-search"); qp.add_argument("query"); qp.add_argument("--output-dir")
    dp = sub.add_parser("deep-search"); dp.add_argument("query"); dp.add_argument("--output", required=True)
    sub.add_parser("login")
    args = p.parse_args()
    {"check-auth": cmd_check_auth, "quick-search": cmd_quick, "deep-search": cmd_deep, "login": cmd_login}[args.cmd](args)

if __name__ == "__main__": main()
```

Make executable: `chmod +x ~/.claude/skills/shelf/perplexity-reader/scripts/perplexity-reader.py`

## Bird Channel — How to Implement (BYO)

The `bird` CLI is a proprietary Node.js binary with no public upstream. `scripts/bird_batch.py` calls it via `subprocess.run(["bird", ...])`. To implement a public alternative, your binary must accept:

- `bird whoami` → exit 0 if logged in, exit non-zero otherwise; stdout should print the user handle
- `bird search "<query>" -n <N>` → newline-separated tweet records, JSON or text
- `bird read <url>` → tweet body (text)
- `bird thread <url>` → full thread (multiple tweets)
- `bird news -n <N>` → trending topics

The official `bird_batch.py` uses `-n` flag (not `--limit`). Authentication is via Chrome default profile cookies (no API key).

Recommended public alternatives (community welcome to PR a reference impl):
- Playwright-based X.com scraper
- A wrapper around the official X API v2 (requires paid tier for search)
