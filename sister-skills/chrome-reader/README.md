# Chrome Reader

> Sister skill for Argus: extract single page content via Chrome DevTools Protocol (CDP).

Chrome Reader navigates to a known URL using a remote-debugging Chrome or Comet session and returns the rendered page content as clean Markdown. It is used by the Argus orchestrator as the **WebAccess fallback channel** when the proxy-based web-access skill is unavailable or rate-limited.

## Usage

### Read a URL

```bash
python3 chrome-reader.py read <url> [--format markdown|json] [--wait-ms 2000]
```

Navigates to `<url>`, waits for the page to finish loading (plus optional extra wait), and returns the content.

### Check browser connectivity

```bash
python3 chrome-reader.py check
```

Verifies that a CDP-enabled browser is reachable on the configured port.

### List open tabs

```bash
python3 chrome-reader.py list-tabs
```

Prints a list of currently open tabs (title + URL) in the connected browser.

### Read the active tab

```bash
python3 chrome-reader.py read-active [--format markdown|json]
```

Extracts content from whichever tab is currently in focus, without navigating away.

## Prerequisites

- **Chrome / Chromium** launched with `--remote-debugging-port=9222`, **or**
- **Comet** running with remote debugging on port **9223**

Start Chrome with debugging:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 --no-first-run
```

Or Comet:

```bash
open -a Comet --args --remote-debugging-port=9223
```

Chrome Reader does **not** route traffic through a proxy and does not support
JavaScript-heavy SPAs that require extended interaction — use the web-access skill
for those cases.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ARGUS_CHROME_PORT` | `9222` | CDP port for Chrome/Chromium |
| `ARGUS_COMET_PORT` | `9223` | CDP port for Comet (takes priority if reachable) |
| `CHROME_READER_WAIT_MS` | `1500` | Extra wait after page load, in milliseconds |

## Integration with Argus

Chrome Reader is registered as the **WebAccess fallback** channel in Argus. When the
primary web-access skill (CDP Proxy :3456) is unreachable, Argus falls back to calling:

```python
# argus/scripts/web_access.py  (simplified)
result = subprocess.run(
    ["python3", "sister-skills/chrome-reader/scripts/chrome-reader.py", "read", url],
    capture_output=True, text=True
)
```

It is also used directly by Argus for single-URL reads in the Chrome-Reader channel
(`argus/scripts/chrome_reader.py`).

See the [Argus README](../../README.md) for the full multi-source orchestration context.

## License

Apache-2.0 — see [LICENSE](../../LICENSE) in the argus repository root.
