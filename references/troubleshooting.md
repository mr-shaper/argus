# ARGUS Troubleshooting

> Read me when: a channel is producing errors, truncated output, auth failures, or unexpected behavior during probe.py execution.

Source: TOOL-SOP.md §1.6, §2.6, §3.6, §4.6, §5.6, §6.6 — consolidated failure table

---

## Channel 1 — Perplexity (Comet 9223)

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| Answer is 500–600 bytes (Quick mode) | Page fetched before AI finishes generating | 3-round strategy: Round 1 fire + sleep 120s + Round 2 re-fetch + Round 3 size-check retry |
| Chrome 9222 used for Perplexity → hcaptcha | Wrong browser; Perplexity detects non-Comet origin | **Must use Comet 9223** — Chrome 9222 is not a substitute |
| `osascript` quit leaves Comet process alive | Renderer not killed | `pkill -9 -f Comet` then restart `comet-debug` |
| `--remote-allow-origins=*` ineffective, origin blocked | zsh glob expands `*` | Wrap the flag in single quotes |
| `/sidecar/` tab has no Deep Research menu item | Sidecar is Comet's built-in AI panel — a subset of features | **Must use the perplexity.ai main-site tab**, not /sidecar/ |
| `.click()` on React button has no effect | React synthetic events ignore native `.click()` | **dispatchEvent 5-pack**: pointerdown / mousedown / pointerup / mouseup / click |
| `execCommand('insertText')` returns empty | Deprecated API; does not work on React contenteditable | **Use CDP native `Input.insertText`** |
| Deep Research menu item with `role=menuitem` returns `[]` | Menu items are rendered as DIVs, not `menuitem` elements | Find the DIV inside `[role=menu]` by `textContent === "Deep research"`, then walk up to the ancestor DIV/BUTTON |
| Deep Research done-detection false-positive (answer half-flagged as done) | Single-condition check | done = `/Prepared by Deep Research/` **AND** `answerLen > 500` — both must match |
| CDP tab `/json/new?url=X` ignores the `url` parameter | Comet implementation difference | Create tab with `/json/new` first, then explicitly call `Page.navigate` |
| Answer extraction drops heading/table structure | `innerText` strips markdown formatting | Convert HTML → markdown (Turndown or custom), or persist `outerHTML` |

---

## Channel 2 — NotebookLM (NLM)

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| `bulk-import --per-notebook 100` auto-creates a new notebook | That command is designed for auto-bucketing, not merging into an existing notebook | Use a `source add` loop instead of `bulk-import` |
| `@handle` YouTube URL returns 404 | Channel privacy change or `@handle` deprecated | Switch to the full channel URL or channel ID |
| `source list` returns empty but sources were added | Processing in progress or API bug | Wait 30 s then retry, or add `--json` flag |
| Audio / Video / Quiz generation fails intermittently | Google rate limit (unreliable content types) | Wait 5–10 min and retry; `report` / `mind-map` / `study-guide` are reliable types and unaffected |
| More than 5 concurrent report requests all hang | Rate limit | Cap `max_workers=3`; use exponential-backoff retry on failure |
| `source add` fails after 300 entries | **Pro plan hard limit** | Phase 2 DEDUPE + GATE gate: only inject if `len(urls) ≤ 300` |
| NLM crawling social platforms hits a login wall | Platform anti-scraping; NLM crawler cannot authenticate | Phase 2 blacklist filtering for these domains; use the dedicated channel `research-raw/` instead |
| Report quality is generic ("overview + 5 bullet points") | Prompt is too vague — no framework, no format spec | Rewrite the prompt following the 5-rubric standard in `references/prompt-library.md` Phase 1b |
| Main AI uses `notebooklm ask` instead of `report` | Shortcut taken / misunderstanding of report's structural advantage | `ask` is disabled by default; add `--enable-ask` only when explicitly needed; `report` is the structured output path |
| Source status stuck at `processing` for > 10 min (single PDF) | Large file indexing is slow | Keep waiting; `timeout=600s`; error sources do not block the pipeline (skip and continue) |
| Phase 5 blocked with "sources not ready" | Ironclad Rule #7: sources_ready gate | Wait until Phase 4 `all(s.status == "ready")` passes before firing `generate` |

---

## Channel 3 — Bird (X/Twitter)

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| Zero results for an exact-match keyword | X search engine favors fuzzy matching; exact terms may return nothing | Use a broader keyword (drop model numbers, version strings, quotes) |
| `EPERM` on Safari cookie read | macOS permission restriction | Ignore — Chrome default profile cookies still work |
| Specific user returns 404 | Account is private or suspended | Exception-handle and `continue` |
| Zero results but query looks valid | X anti-scraping rate throttle | Reduce concurrency (< 5) and increase request intervals |

---

## Channel 4 — Web-access CDP 3456

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| Proxy "connection failed" / WebSocket error | **DevToolsActivePort path mismatch** (root cause in ~90% of cases) | Re-apply the DevToolsActivePort patch from `tool-auth-checklist.md` |
| Proxy hangs after Chrome upgrade | Chrome changed its origin-validation mechanism | Confirm `--remote-allow-origins=*` is single-quoted, then re-apply the patch |
| `/new?url=` request times out | Target page is heavy or has anti-scraping measures | Raise `timeout` to 30 s; or use `/navigate?target=$T&url=X` as a two-step approach |
| Lazy-loaded content missing (0 JS-rendered items) | Scroll was not triggered | `/scroll?direction=bottom` + `sleep 2s` + re-evaluate |
| User's existing Chrome tab closed unexpectedly | Code closed a tab it did not own | Only close tabs created by this session; pair `/close?target=$T` with the correct `targetId` |
| `eval` returns `null` / `undefined` | Selector did not match | Validate the selector in Chrome DevTools Console first |

---

## Channel 5 — chrome-reader.py

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| Chrome 9222 unreachable | Chrome not running or port not open | Start Chrome with `chrome-debug`, then verify with `curl localhost:9222/json/version` |
| Reading a login-walled page (returns login form content) | Target page requires an authenticated session | Manually log in via the Chrome default profile first, then use `read-tab` on the already-open tab |
| Output text is very short (< 100 chars) | Readability extraction failed (unusual page structure) | Switch to web-access CDP `/eval` with a custom selector |

**Note**: chrome-reader.py **does not depend on cdp-proxy.mjs** — it connects directly to Chrome 9222 via WebSocket. A DevToolsActivePort path mismatch does not affect chrome-reader; Chrome only needs to be reachable on port 9222.

---

## Cross-Channel Fallback Matrix

| Channel | When unreachable | When partially reachable |
|---------|-----------------|--------------------------|
| Perplexity | Mark `skipped` in manifest; other channels continue | 3-round fallback for truncated answers |
| NLM | `skipped` + warn "no 15-dimension report" | Skip error sources, do not block the overall pipeline |
| Bird | `skipped` | Log zero-result queries and `continue` |
| web-access | `skipped` | Single-page failure: `continue` |
| chrome-reader | `skipped` | — |

**Conditions for `exit 1`** (fatal failure):
- `tool_auth_check` detects an essential channel as unreachable **and** `--strict` flag is set
- `budget_remaining_s < 600` with zero core channels ready
