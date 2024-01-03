---
name: chrome-reader
description: >
  Use to read a known URL via Chrome CDP (no WebFetch). Used as WebAccess fallback
  in Argus. Triggers: chrome-reader / read-tab / CDP fetch / single URL read without proxy.
---

# Chrome Reader (Argus sister skill)

CDP-based single-page content extractor. Navigates to a URL using a remote-debugging
Chrome or Comet session and returns rendered Markdown — no proxy, no WebFetch.

For full usage details see [README.md](README.md).

## CLI Reference

```
chrome-reader.py check
chrome-reader.py list-tabs
chrome-reader.py read-active [--format markdown|json]
chrome-reader.py read <url> [--format markdown|json] [--wait-ms 2000]
```

## Subcommand Summary

| Subcommand | Purpose |
|---|---|
| `check` | Verify CDP browser is reachable |
| `list-tabs` | List open tabs in the connected browser |
| `read-active` | Extract content from the currently focused tab |
| `read <url>` | Navigate to URL and extract rendered content |

## Key Environment Variables

```
ARGUS_CHROME_PORT      CDP port for Chrome (default: 9222)
ARGUS_COMET_PORT       CDP port for Comet (default: 9223, takes priority)
CHROME_READER_WAIT_MS  Extra wait after page load in ms (default: 1500)
```

## Integration

Used by `argus/scripts/chrome_reader.py` as the Chrome-Reader channel, and as the
WebAccess fallback when the CDP Proxy (:3456) is unavailable.

See the main [argus README](../../README.md) for orchestration context.
