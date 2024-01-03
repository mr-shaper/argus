# Perplexity Reader

> Sister skill for Argus: extract Perplexity Quick and Deep Research results via Chrome/Comet CDP.

Perplexity Reader provides a CDP-based interface for reading Perplexity AI pages, running Quick Search queries, and triggering Deep Research jobs. It serves as the Perplexity backend channel for the Argus multi-source research orchestrator.

## Usage

### Check browser connectivity

```bash
python3 perplexity-reader.py check
```

Verifies that a CDP-enabled browser (Comet :9223 or Chrome :9222) is reachable.

### Verify Perplexity authentication

```bash
python3 perplexity-reader.py check-auth
```

Confirms that a valid Perplexity session cookie is loaded and the account is signed in.

### Read the currently active Perplexity tab

```bash
python3 perplexity-reader.py read-current [--format markdown|json]
```

Extracts the content of whichever Perplexity thread is open in the active tab. Defaults to `markdown`.

### Fetch a specific Perplexity URL

```bash
python3 perplexity-reader.py fetch <url> [--format markdown|json]
```

Navigates to `<url>` (must be a `perplexity.ai` URL), waits for the answer to finish rendering, then extracts the result.

### Run a Deep Research query

```bash
python3 perplexity-reader.py deep-search "<query>" --output FILE
```

Submits a Deep Research job, polls until completion, and writes the full report to `FILE` (Markdown).

### Run a Quick Search query

```bash
python3 perplexity-reader.py quick-search "<query>" --output-dir DIR
```

Runs a standard Perplexity search, saves the answer and sources as Markdown files under `DIR`.

## Prerequisites

- **Comet** running with remote debugging on port **9223** (preferred), **or**
- **Chrome / Chromium** launched with `--remote-debugging-port=9222` (fallback)
- A valid Perplexity session cookie at `$PERPLEXITY_COOKIES_PATH`

Start Comet with debugging:

```bash
open -a Comet --args --remote-debugging-port=9223
```

Or Chrome:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 --no-first-run
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PERPLEXITY_COOKIES_PATH` | `~/.claude/skills/shelf/perplexity-reader/cookies.json` | Path to Perplexity session cookies (JSON) |
| `ARGUS_COMET_PORT` | `9223` | CDP port for Comet browser (preferred) |
| `ARGUS_CHROME_PORT` | `9222` | CDP port for Chrome/Chromium (fallback) |

Cookie format follows the standard Netscape/JSON export supported by most browser extensions.

## Integration with Argus

This skill is used by the Argus orchestrator as the **Perplexity channel** (both Quick and Deep modes):

- `argus/scripts/perplexity_quick.py` — invokes `quick-search` via subprocess
- `argus/scripts/perplexity_deep.py` — invokes `deep-search` via subprocess

Argus enforces a concurrency limit of **3 simultaneous Perplexity requests** (semaphore) to avoid rate-limiting. Do not call this skill in parallel beyond that limit.

When running standalone (outside Argus), the skill can be used directly as a lightweight Perplexity scraper without the full orchestrator stack.

See the [Argus README](../../README.md) for the full multi-source orchestration context.

## License

Apache-2.0 — see [LICENSE](../../LICENSE) in the argus repository root.
