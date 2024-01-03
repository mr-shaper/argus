---
name: perplexity-reader
description: >
  Use when reading Perplexity pages, fetching Quick/Deep Research results, or providing
  the Perplexity backend to Argus orchestrator. Triggers: perplexity URL / quick-search /
  deep-search / read-current tab / check-auth.
---

# Perplexity Reader (Argus sister skill)

CDP-based Perplexity extractor. Reads live Perplexity threads and runs Quick/Deep Research
queries via a remote-debugging browser session (Comet :9223 preferred, Chrome :9222 fallback).

For full usage details see [README.md](README.md).

## CLI Reference

```
perplexity-reader.py check
perplexity-reader.py check-auth
perplexity-reader.py read-current [--format markdown|json]
perplexity-reader.py fetch <url> [--format markdown|json]
perplexity-reader.py deep-search "<query>" --output FILE
perplexity-reader.py quick-search "<query>" --output-dir DIR
```

## Subcommand Summary

| Subcommand | Purpose |
|---|---|
| `check` | Verify CDP browser is reachable |
| `check-auth` | Confirm Perplexity session cookie is valid |
| `read-current` | Extract content of the active Perplexity tab |
| `fetch <url>` | Navigate to a Perplexity URL and extract result |
| `deep-search` | Submit a Deep Research job, poll, save report |
| `quick-search` | Run a Quick Search, save answer + sources |

## Key Environment Variables

```
PERPLEXITY_COOKIES_PATH   Path to session cookies JSON
ARGUS_COMET_PORT          CDP port for Comet (default: 9223)
ARGUS_CHROME_PORT         CDP port for Chrome (default: 9222)
```

## Integration

Used by `argus/scripts/perplexity_quick.py` and `argus/scripts/perplexity_deep.py`
via subprocess. Argus caps Perplexity concurrency at 3 (semaphore) — do not bypass.

See the main [argus README](../../README.md) for orchestration context.
