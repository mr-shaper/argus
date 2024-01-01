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
