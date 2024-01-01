---
name: argus
description: Use when deep multi-source research is needed (OODC-Observe phase), orchestrating Perplexity + NotebookLM + Bird + WebAccess + Chrome + GitHub into one command, or diagnosing research toolchain authorization failures. Triggers on: deep research / multi-source research / OODC-O / full research / observe orchestration / 100+ source report / tool authorization doctor / probe.py / argus skill.
---

# Argus — Multi-Source Research Orchestrator

## Three Pillars

- **SKILLS**: Orchestrates sister skills — `perplexity-reader`, `notebooklm`, `bird`, `web-access` — into a single coherent pipeline with shared quota management and anti-ban policies.
- **DOCTOR**: `probe.py doctor` auto-detects all 6 channels, self-heals auth failures via `osascript`-guided remediation popups, and produces actionable exit codes.
- **Onboarding**: Per-channel first-time auth walkthrough so every channel reaches green before firing a research run.

---

## DOCTOR — Auto-Detect & Self-Heal

`probe.py doctor` is the heart of Argus onboarding. It checks all 6 channels with two modes:

**Quick check (essential channels only)**:
```bash
python3 scripts/probe.py doctor --essential comet-9223,perplexity-auth,chrome-9222
```

**Full check (all 6 channels with osascript-guided remediation)**:
```bash
python3 scripts/probe.py doctor
```

When a channel fails, Argus pops a macOS dialog (via `osascript`) with the exact remediation command — for example, "Click OK to launch perplexity-login.py". You can disable popups with `--no-popup` for CI/cron use.

**Exit codes**:
- `0` — all channels healthy
- `2` — some non-essential channel failures (safe to proceed with degraded set)
- `3` — one or more essential channels failed (do not fire a run)
- `4` — quota exceeded (Perplexity Deep or NLM 300-source cap)

Run doctor as the first step in any new environment. Re-run whenever a channel starts returning auth errors.

---

## Onboarding — First-Time Auth Per Channel

Full step-by-step setup lives in `README.md`. Summary per channel:

| Channel | One-time setup |
|---------|---------------|
| Perplexity Quick + Deep | Launch Comet browser on `:9223`, then run `python3 scripts/perplexity-login.py` once to capture session cookies |
| NotebookLM | Install `notebooklm-py` package, run `notebooklm login` to complete Google OAuth flow |
| Bird (X/Twitter) | Install bird CLI, run `bird auth` to authenticate via Chrome default profile |
| WebAccess | Start Chrome with `--remote-debugging-port=9222`, run `web-access/check-deps.sh` to verify CDP proxy on `localhost:3456` |
| GitHub | `brew install gh && gh auth login`; optionally set `GITHUB_TOKEN` env var for higher rate limits |
| Chrome-reader | Shares the Chrome `:9222` instance used by WebAccess; no separate auth needed |

After completing each step, run `probe.py doctor` to confirm the channel turns green.

---

## Quick Start

```bash
# Clone and verify channels
git clone <repo> argus && cd argus
python3 scripts/probe.py doctor          # verify all 6 channels

# One-command deep research (Stages 0-4, produces manifest.json + 10+ dimension report)
python3 scripts/probe.py run \
  --topic "AI dev tooling" --dimensions 15 --budget 90m \
  --output ~/research-raw/ai-dev-tooling/

# Resume from checkpoint (after network or quota interruption)
python3 scripts/probe.py resume ~/research-raw/ai-dev-tooling/_manifest.json
```

> **Path note:** If installed as a Claude Code skill, replace `scripts/` with `<skill-root>/scripts/`.

---

## Pre-Fire Gate (Read Before Running Any Research)

> **Policy (locked):** In a new session, the AI has zero autonomous fire authority. Argus can only profile the request, recommend a package, and open a Gate. The user decides. Violation = unacceptable quality regression.

**4-Step Gate:**

```
Request → [Step 1 Profile] → [Step 2 Recommend] → [Step 3 GATE: wait for user] → [Step 4 FIRE]
```

**Step 1 — Profile:** Check 4 dimensions:
- Information type: AI synthesis / social sentiment / academic depth / code ecosystem
- Freshness: quick answer <5 min / medium 30 min / full 60-90 min
- Language scope: English / multilingual
- Quota risk: Perplexity Deep remaining N/100, NLM 300-source cap remaining M

**Step 2 — Recommend:** Present the 4-package menu:

| # | Package | Channels | Budget | Best for |
|---|---------|----------|--------|----------|
| S1 | Quick Answer | PPLX-Quick + WebAccess + GitHub | <5 min | Definitions, quick facts |
| S2 | Industry Overview | PPLX-Quick×3 + PPLX-Deep + WebAccess + Bird | 20-30 min | Competitive / trends / tech stacks |
| S3 | Deep Research | S2 + NLM 15-dimension report | 45-60 min | Academic / long reports / 10+ dimensions |
| S4 | Full Research | All 6 channels + NLM 15-dim + multimedia | 60-90 min | Major strategic decisions |

Include: default recommendation + 2 alternatives + quota cost per option + expected output + 2-sentence rationale.

**Step 3 — GATE:** Wait for user approval. Accepted responses:
- `"Use S{X}"` — fire using the matching T-* template
- `"S{X} plus WebAccess / drop Deep"` — fire with modification
- `"Re-recommend"` — re-profile
- `"Full S4"` — full 90-minute run

**Step 4 — FIRE:** Only after Gate approval. Use templates from `references/agent-dispatch.md`. Zero hand-crafted prompts.

**Anti-patterns (strictly forbidden):**
- Auto-fire `probe.py run` in a new session without Gate approval
- Skip Step 1 profile and jump directly to package recommendation
- Recommend without stating quota cost and expected output
- Pre-warm any channel before Gate approval

---

## 7 Ironclad Rules

1. **Perplexity must use Comet port 9223** — Chrome 9222 triggers hcaptcha on Perplexity
2. **Chrome 9222 "connection failed" = `DevToolsActivePort` path mismatch**, not a permissions issue
3. **NLM Pro hard limit: 300 sources/notebook** — fail-fast above this; Phase 2 DEDUPE must gate `len <= 300`
4. **AI must not read source body text** — handle URL + title + snippet only; full body goes to NLM for indexing
5. **NLM input must be fully ready before firing report** — `all status==ready` gate required (Phase 4 WAIT)
6. **Perplexity and Bird are independent channels** — do not feed their output into NLM; they land in `research-raw/<ch>/`
7. **Perplexity all-channel `Semaphore(3)`** — Quick and Deep share this cap; concurrent > 3 triggers account ban

---

## 6-Channel Matrix

| Channel | Script | Concurrency | Latency | Responsibility |
|---------|--------|-------------|---------|----------------|
| Perplexity Quick | `perplexity_quick.py` | Shared Sem(3) | 30-60s | AI synthesis summaries → `research-raw/perplexity_quick/` |
| Perplexity Deep | `perplexity_deep.py` | Shared Sem(3), 100/day quota | 5-10 min | Deep AI reports → `research-raw/perplexity_deep/` |
| NotebookLM | `nlm_pipeline.py {discover,inject,wait,generate}` | Source-add serial 1.5s / report parallel ×3 | 15-45 min | Web/PDF/YouTube URL indexing → structured report |
| Bird | `bird_batch.py` | 5 parallel | 5-10s | X/Twitter posts only; Bird does not collect YouTube |
| WebAccess | `webaccess_crawl.py` | multi-targetId parallel | 2-15s/page | Web crawl; YouTube URL discovery → hand off to NLM |
| GitHub | `github_fetch.py {repo,trending,issues,release,search-repos}` | Sem(10) repo / Sem(3) search | 0.5-3s | AI repos README + issues + release + trending; `--to-nlm` cap 30 |

> **YouTube path:** `webaccess_crawl.py` discovers YouTube URLs → passes them to `nlm_pipeline.py inject` for NLM source-add. Bird does **not** handle YouTube.

---

## Implementation Status (v1.1)

| Feature | Status | Notes |
|---------|--------|-------|
| `probe.py doctor` (6 channels) | Real | `tool_auth_check.py` all 6 channels |
| `probe.py run` Stage 1 | Real | Thread-fire Quick/Bird; Popen Deep |
| `probe.py run` Stage 2+ | Gate | NLM needs SOP §2.0 Phase 1.5; user Gate review of prompts required |
| Perplexity Quick (3-round) | Real | Semaphore(3) anti-ban policy locked |
| Perplexity Deep (CDP 10-step) | Real | `perplexity_deep.py` tested 120s/13KB |
| NLM Phase 1 Discover | Real | |
| NLM Phase 2 Dedupe (300 cap) | Real | Rule #3 code assertion |
| NLM Phase 3 INJECT | Real | subprocess serial sleep 1.5s (v1.1) |
| NLM Phase 4 WAIT | Real | Single-thread poll + Rule #5 gate |
| NLM Phase 5 GENERATE | Real | ThreadPool max=3 (v1.1) |
| NLM Phase 6 DOWNLOAD | Real | Artifact wait + download (v1.1) |
| Bird batch | Real | v1.1 `bird_batch.py` call bug fixed |
| WebAccess CDP | Real | |
| Chrome-reader | Real | |
| GitHub channel | Real | `github_fetch.py` 5 subcommands + SOP §6.5 7-segment + rate-limit |
| Agent dispatch template library | Real | `references/agent-dispatch.md` 12 templates + SOP 12-chapter coverage |

---

## Red Flags — STOP

| Symptom | What it means |
|---------|---------------|
| `WebFetch https://www.perplexity.ai/...` | Returns JS bundle only; must use `perplexity_{quick,deep}.py` |
| Perplexity concurrent > 3 | Triggers account ban; `threading.Semaphore(3)` is shared and locked |
| NLM `source add` ignoring 300-source cap | Silent failure above limit; DEDUPE phase must gate this |
| `chrome-reader.py` reading body text | Violates Rule #4; full body must go to NLM for indexing |
| Firing `generate report` before sources are ready | Shallow output; must run Phase 4 WAIT_READY poll first |
| `notebooklm ask` instead of `generate report` | Prohibited by default; use `--enable-ask` to explicitly unlock |
| Perplexity returns 500-600 bytes | AI still generating; apply 3-round: fire → sleep 120s → re-fetch → <3KB retry |

---

## Mandatory Reference Read Protocol

**Load this skill, then immediately read the files below — not on demand, but first.**
SKILL.md is a navigation stub; the real protocols live in `references/`.

| When | Read | Why |
|------|------|-----|
| Any Argus stage (0-4) | `references/oodc-o-integration.md` | OODC × Argus boundary; prevents AI from reading source body text (Rule #4) |
| Spawning any parallel sub-agent | `references/agent-dispatch.md` | 12 Task Prompt templates; without this, prompts drift |
| Designing NLM prompts (Phase 1.5) | `references/prompt-library.md` | 5-rubric gate; without this, NLM reports are shallow |
| Any channel `status=error` | `references/troubleshooting.md` | Symptom → root cause → fix lookup |
| Any channel doctor fail | `references/tool-auth-checklist.md` | `DevToolsActivePort` / MCP handshake fixes |

**Skipping = Argus not properly loaded. Quality drops below acceptable baseline.**

---

## Agent Dispatch

Argus is a multi-source research orchestrator. Before spawning any parallel sub-agent, read `references/agent-dispatch.md`. Select the matching Template ID → fill variables → spawn. Templates embed all 6 essential parameters + channel SOP + anti-patterns. Zero hand-crafted Task Prompts.

**11 templates:** T-PPLX-QUICK / T-PPLX-DEEP / T-NLM-INJECT / T-NLM-WAIT / T-NLM-GEN / T-NLM-DL / T-NLM-MEDIA / T-USER-GATE / T-BIRD / T-WEB / T-GH

**Decision trees to consult before calling Argus:**
- §8 Source stratification (what goes to NLM vs independent channel)
- §9 Global budget (time / quota)
- §10 Failure fallback matrix (doctor must cover)
- §11 Perplexity two-tier decision (Quick vs Deep)

---

## References

| File | When to Read |
|------|-------------|
| `references/agent-dispatch.md` | Before spawning any parallel sub-agent — 12 Task Prompt templates + dispatch rules + anti-patterns |
| `references/tool-auth-checklist.md` | Any channel auth failure, or first `probe.py doctor` not all green |
| `references/prompt-library.md` | Before designing 10+ dimension NLM prompts (5-rubric gate) |
| `references/troubleshooting.md` | Any channel error — look up symptom → root cause → fix |
| `references/oodc-o-integration.md` | Integrating into OODC-loop state machine / harness PROJECT_STATE / manifest.json protocol |

---

## Related Sister Skills

- `perplexity-reader` — Perplexity base CLI (Argus wraps the 3-round strategy around it)
- `notebooklm` — NLM official CLI (Argus implements the 7-phase workflow on top)
- `bird` — X/Twitter CLI (upstream; Argus calls `bird_batch.py` which wraps it)
- `web-access` — Chrome CDP Proxy on port 3456; required for WebAccess channel

See `README.md` for the full first-time setup walkthrough and `CONTRIBUTING.md` for development guidelines.
