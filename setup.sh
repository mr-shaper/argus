#!/usr/bin/env bash
# Argus one-shot setup helper (Phase 2 stabilization)
# Adapted from charles@macOS deployment postmortem; generalized for OSS.
set -e

ARGUS_DIR="${ARGUS_DIR:-$HOME/argus}"
WEBACCESS_URL="${WEBACCESS_FORK_URL:-YOUR_WEBACCESS_FORK_URL}"   # P10: BYO placeholder, do NOT hardcode an upstream

echo "[argus-setup] argus dir: $ARGUS_DIR"

# 1. Clone / pull repo
if [ -d "$ARGUS_DIR/.git" ]; then
    git -C "$ARGUS_DIR" pull --ff-only
else
    git clone https://github.com/YOUR_GITHUB_HANDLE/argus "$ARGUS_DIR"
fi
cd "$ARGUS_DIR"

# 2. Check Python 3.10+
PYTHON_BIN="$(command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)"
PYTHON_VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "[argus-setup] python: $PYTHON_BIN ($PYTHON_VER)"
if ! "$PYTHON_BIN" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"; then
    echo "[argus-setup] ERROR: Python >=3.10 required. Install via 'brew install python@3.12' or apt."
    exit 1
fi

# 3. Brew core deps (macOS) - skip silently on Linux
if command -v brew >/dev/null; then
    command -v gh >/dev/null || brew install gh
fi

# 4. Venv + pip
[ -d .venv ] || "$PYTHON_BIN" -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

# 5. Playwright chromium (NotebookLM dep, ~150MB)
.venv/bin/playwright install chromium 2>&1 | tail -3 || echo "[argus-setup] playwright install warning, NotebookLM login may fail"

# 6. Sister skills
if [ "$WEBACCESS_URL" != "YOUR_WEBACCESS_FORK_URL" ]; then
    [ -d "$HOME/.local/share/web-access" ] || git clone "$WEBACCESS_URL" "$HOME/.local/share/web-access"
else
    echo "[argus-setup] WEBACCESS_FORK_URL not set, skip web-access clone (set env var to enable)"
fi

# 7. Config .env (idempotent)
if [ ! -f .env ]; then
    cat > .env <<EOF
ARGUS_COMET_PORT=9223
ARGUS_CHROME_PORT=9222
ARGUS_OUTPUT_DIR=\$HOME/argus-output
PERPLEXITY_COOKIES_PATH=\$HOME/.config/argus/cookies/perplexity.json
ARGUS_XHS_MCP_URL=http://localhost:18060/mcp
EOF
fi

# 8. Wrapper script
cat > run-argus.sh <<'WRAPPER'
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -f .env ] && { set -a; . ./.env; set +a; }
export PATH="$PWD/.venv/bin:$PATH"
exec .venv/bin/python scripts/probe.py "$@"
WRAPPER
chmod +x run-argus.sh

echo ""
echo "[argus-setup] DONE."
echo "  Next: source .venv/bin/activate"
echo "        ./run-argus.sh doctor"
echo "  See README.md Onboarding section for sister-skill auth steps."
