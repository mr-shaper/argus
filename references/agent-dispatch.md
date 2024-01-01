# ARGUS Agent Dispatch — Parallel Sub-Agent Scheduling Rules & Task Prompt Template Library

> **This file is ARGUS's "dispatch operating system."** Before spawning any sub-agent, the main AI **must** read this file, select the appropriate template, fill in the variables, and then spawn. **Never hand-compose a Task Prompt** — doing so risks omitting SOP clauses, Ironclad Rules, or concurrency constraints.
>
> **Version**: v1.1, 2026-04-22 (anti-ban Ironclad Rule policy locked)
> **Upstream**: TOOL-SOP.md §0.1 (9 Ironclad Rules) + §1–§6 (channel SOPs) + §7 (cross-channel orchestration)

---

## §0. When to Dispatch an Agent (decision matrix — avoid over-spawning)

```
┌──────────────────────────────────────┬──────────┬────────────────────────┐
│ Scenario                              │ Dispatch?│ Granularity            │
├──────────────────────────────────────┼──────────┼────────────────────────┤
│ Single channel, simple query (<30s)  │ No       │ Spawn overhead > gain  │
│ Single channel, long task (5–10 min) │ Yes (×1) │ fire-and-forget        │
│ NLM wait for sources to be ready     │ Yes (×1) │ free main AI context   │
│ Multi-channel parallel dogfood (5–6) │ Yes (×N) │ one agent per channel  │
│ Long log output — protect main ctx   │ Yes (Exp)│ return summary only    │
│ Phase 1 exploration (read SOP/design)│ Yes (Exp)│ multiple parallel      │
│ Phase 3 code generation (clear spec) │ Yes (gen)│ one agent per file     │
└──────────────────────────────────────┴──────────┴────────────────────────┘
```

**Anti-pattern**: simple grep / Read / subprocess calls are faster when the main AI does them directly — do not spawn an agent for every Bash command.

---

## §0.5 Pre-flight Gate — New Session Intake Protocol (policy locked)

> **Top-level Ironclad Rule**: on receiving any research request in a new session, the main AI has **zero autonomous fire authority**. It must complete all 4 steps before spawning agents or running `probe.py run` — user retains 100% of the decision. This is the **Phase 0.5 package-approval Gate** that precedes §3.11 T-USER-GATE (Phase 1.5 prompts approval). Violation = 3.25.

**Intake 4 steps**:

```
Request arrives → [Step 1 Profile] → [Step 2 Recommend] → [Step 3 GATE] → [Step 4 FIRE]
                   ↑ main AI self-   ↑ 2–3 candidates   ↑ wait for      ↑ use §3 T-*
                     assessment                            user approval    template
```

**Step 1 Profile (4 dimensions)**: information type / recency / language sphere / quota sensitivity  
**Step 2 Recommend**: package (S1 Quick Answer / S2 Industry Survey / S3 Deep Research / S4 Full Dogfood), details in `SKILL.md` §"New Session Intake Protocol". Output must include: the default recommendation + 2 alternatives + quota cost + expected output + 2-sentence rationale.  
**Step 3 GATE**: wait for user response — one of ✅ / ✏️ / ❌ / 🔥. Do not proceed to Step 4 without approval.  
**Step 4 FIRE**: after Gate passes, spawn using the T-* template from §2.

**Anti-patterns (strictly forbidden — violation = 3.25)**:
```
❌ Auto-fire `probe.py run` on receiving a topic in a new session   (Gate bypass)
❌ Skip Step 1 profiling and jump straight to package recommendation (no owner awareness)
❌ Recommend without listing quota cost + expected output            (incomplete decision info)
❌ Pre-copy prompts.json or spawn agent before Gate approval        (soft-fire violation)
❌ Offer only one package tier without alternatives                  (removes user decision authority)
```

**Relationship between §0.5 and §3.11 T-USER-GATE**:
- **§0.5 Phase 0.5 Package Gate**: fires **before** research begins — decides which channels to run (strategic layer)
- **§3.11 Phase 1.5 Prompts Gate**: fires **before** NLM 15-dimension prompts.json is sent — reviews individual prompt quality (tactical layer)
- Both gates require user approval; neither can be skipped

---

## §1. Task Prompt Six Elements (required in every template)

```
1. Goal (Goal)             — one sentence describing the deliverable
2. Context (Context)       — ARGUS Ironclad Rule anchors + upstream dependencies
3. Input (Input)           — required file paths + commands to run
4. Output (Output)         — Done Criteria (verifiable completion conditions)
5. Quality Gate            — PUA three red lines (L1 Loop / L2 Fact / L3 Exhaust) + rule assertions
6. Feedback Format         — [P8-X-COMPLETION] or [P8-X-DELIVERY] + word-count cap
```

**Anti-pattern**: any missing element → sub-agent improvises → output drifts from spec.

---

## §2. Channel × Template Quick-Reference

| Channel | Template ID | SOP Anchor | Concurrency Rule | Primary Use Case |
|---------|-------------|-----------|-----------------|-----------------|
| Perplexity Quick | `T-PPLX-QUICK` | SOP §1.3 | Sem(3) shared (#9) | 3-round AI synthesis |
| Perplexity Deep | `T-PPLX-DEEP` | SOP §1.0.1 | Sem(3) occupies 1 slot (#9) + Comet 9223 (#1) | CDP 10-step deep research |
| NLM Inject | `T-NLM-INJECT` | SOP §2.2 | **serial** + sleep 1.5s + 300 cap (#5) | Bulk source import |
| NLM Wait | `T-NLM-WAIT` | SOP §2.3 | **single-threaded poll** + #7 all-ready gate | Source readiness gate |
| NLM Generate | `T-NLM-GEN` | SOP §2.4 | concurrency=3 reliable | Multi-dimension report generation |
| NLM Download | `T-NLM-DL` | SOP §2.4.1 | Ironclad Rule #6 (no source body reads) | Pull report markdown |
| Bird | `T-BIRD` | SOP §3 | max 5 concurrent | X/Twitter batch query |
| WebAccess | `T-WEB` | SOP §5 | targetId multi-concurrent | Multi-page crawl |
| Chrome-Reader | `T-CR` | SOP §6 | synchronous | Known-URL single-page read |
| GitHub | `T-GH` | SOP §6.5 | Sem(10) repo / Sem(3) search | repo / trending / issues |
| NLM Media | `T-NLM-MEDIA` | SOP §2.4.2 | reliable sync / unreliable ≤1 | audio / video / mindmap / slide-deck / quiz |
| User Gate | `T-USER-GATE` | SOP §2.0 Phase 1.5 | main AI self-check (not spawned) | prompts.json approval gate |

---

## §3. 10 Standard Task Prompt Templates (self-contained, fill-in-the-blank)

### T-PPLX-QUICK — Perplexity Quick 3-Round

```
You are an ARGUS P8 sub-agent (template=T-PPLX-QUICK).

[GOAL] Run Perplexity Quick 3-round fetch for {query_list} (one query per line)
       and persist results to {output_dir}/perplexity_quick/.

[CONTEXT · ARGUS Ironclad Rules]
- Rule #1: Comet 9223 only — Chrome 9222 is forbidden (triggers hcaptcha)
- Rule #9: All Perplexity channels share a Semaphore(3) cap (policy locked).
           If Perplexity Deep is running concurrently, this task occupies (Sem total − Deep slots) slots.
- SOP §1.3: 3-round strategy (Round 1 fire → sleep 120s → Round 2 re-fetch → Round 3 retry <3 KB)

[INPUT]
  - Tool: python3 scripts/perplexity_quick.py
  - Args: {query_list} --output-dir {output_dir}/perplexity_quick --rounds 3 --concurrency 3

[OUTPUT · Done Criteria]
  - One {slug(query)}.md per query, ≥3 KB each
  - Returns JSON: {"fired": N, "total_bytes": B, "errors": [...]}
  - Zero queries skipped (failures must produce a .err file)

[QUALITY GATE · PUA Three Red Lines]
  L1 CLOSE LOOP: paste full perplexity_quick.py stdout (≥10 lines)
  L2 FACT-DRIVEN: if "not logged in / hcaptcha" appears, exit 1 + paste Comet 9223 /json/version output
  L3 EXHAUST: <3 KB → auto Round 3 retry; still <3 KB after retry → mark degraded, do not abort

[ANTI-PATTERNS — FORBIDDEN]
  ❌ --concurrency > 3 (violates Rule #9)
  ❌ subprocess.run(..., timeout=30) — Deep answers need 60–90 s
  ❌ Hand-rolling curl to perplexity.ai — must use the perplexity-reader skill

[FEEDBACK] [P8-{X}-COMPLETION] + JSON summary + first 2 lines of stdout as evidence
[WORD LIMIT] 500
```

### T-PPLX-DEEP — Perplexity Deep Research (CDP 10-step)

```
You are an ARGUS P8 sub-agent (template=T-PPLX-DEEP).

[GOAL] Run Perplexity Deep Research for "{query}" and persist to
       {output_dir}/perplexity_deep/{slug}.md.
       **Each invocation consumes 1/100 Deep quota and 5–10 min — fire with care.**

[CONTEXT · ARGUS Ironclad Rules]
- Rule #1: Comet 9223 required (CDP connection)
- Rule #9: This task occupies 1 Sem(3) slot; Quick tasks are capped at ≤2 while this runs
- SOP §1.0.1: CDP UI 10-step pattern (navigate → hydrate 12s → verify → Add files
  menu → Deep research chip → Input.insertText → Submit → Poll)

[INPUT]
  - Tool: python3 scripts/perplexity_deep.py fetch "{query}" --submit
          --output {output_dir}/perplexity_deep/{slug}.md --budget-file {budget_path}
  - SOP: TOOL-SOP §1.0.1 (10-step canonical text)

[OUTPUT · Done Criteria]
  - {slug}.md ≥5 KB, containing both ## Answer and ## Sources sections
  - Sources count ≥100 (Deep typically returns ~300)
  - Returns: {"bytes": B, "sources": N, "duration_s": D}

[QUALITY GATE]
  L1: paste full perplexity_deep.py stdout (10-step execution log)
  L2: answerLen < 500 → continue polling (AI still generating)
  L3: "Prepared by Deep Research" marker must be present before marking ready
  L4: 5-minute timeout with no ready marker → mark error + record tab_id for manual review

[ANTI-PATTERNS]
  ❌ Chrome 9222 (triggers hcaptcha and account flag risk)
  ❌ WebFetch perplexity.ai (returns only JS bundle)
  ❌ More than 1 concurrent Deep task (Deep occupies a full Sem slot)
  ❌ Skipping Add files menu → Deep research chip (without selection, runs as Quick mode)

[FEEDBACK] [P8-{X}-COMPLETION] + JSON + last 30 lines of stdout
[WORD LIMIT] 600
```

### T-NLM-INJECT — NLM Bulk Source Injection (anti-ban policy locked)

```
You are an ARGUS P8 sub-agent (template=T-NLM-INJECT).

[GOAL] Import all {N} URLs from {yt_urls_file} + {doc_urls_file} into
       NotebookLM notebook={nb_id}.
       Anti-ban policy locked: **serial execution + sleep 1.5s between each call — mandatory.**

[CONTEXT · ARGUS Ironclad Rules]
- Rule #5: NLM Pro hard limit is 300 sources/notebook (pre-check: total ≤ 300 before running)
- Rule #8: NLM is an isolated channel — Perplexity/Bird output must not be mixed in
- Policy locked: injection **forbids ThreadPool / any parallelism** — for-loop + sleep 1.5s only
- SOP §2.2: notebooklm source add CLI command pattern

[INPUT]
  - Tool: python3 scripts/nlm_pipeline.py inject --notebook-id {nb_id}
          --yt-urls {yt_urls_file} --doc-urls {doc_urls_file}
  - Upstream dependency: notebooklm CLI installed (`notebooklm status` shows Authenticated as: ...)

[OUTPUT · Done Criteria]
  - Returns: {"total": N, "success": S, "errors": E, "source_ids": [...]}
  - success + errors == total (zero URLs silently skipped)
  - Failures written to {output_dir}/nlm-inject-errors.jsonl (Phase 4 wait filters these — does not block)

[QUALITY GATE]
  L1 CLOSE LOOP: print progress every 10 entries ([10/300] ✅ ...)
  L2 FACT-DRIVEN: "notebooklm: command not found" → exit 1 + prompt to install CLI
  L3 EXHAUST: 429 rate-limit → double sleep to 3s and continue

[ANTI-PATTERNS — FORBIDDEN (account-ban consequences are severe)]
  ❌ ThreadPoolExecutor parallel source add
  ❌ Removing the sleep 1.5s to "go faster"
  ❌ Injecting when total > 300 (Rule #5 exit 4)
  ❌ Mixing in URLs from Perplexity/Bird (violates Rule #8)

[FEEDBACK] [P8-{X}-COMPLETION] + JSON + last 20 lines of progress log
[WORD LIMIT] 400
```

### T-NLM-WAIT — NLM Source Readiness Poll (single-threaded gate)

```
You are an ARGUS P8 sub-agent (template=T-NLM-WAIT).

[GOAL] Poll NotebookLM notebook={nb_id} until all sources have status=="ready"
       (Ironclad Rule #7 gate). Estimated time: 5–30 min (depends on source count + NLM throughput).

[CONTEXT · ARGUS Ironclad Rules]
- Rule #7: Firing a report before all inputs are ready produces shallow output
- Policy locked: wait task uses **single-threaded polling** — no parallel wait agents

[INPUT]
  - Tool: python3 scripts/nlm_pipeline.py wait --notebook-id {nb_id}
          --poll-interval 30 --timeout 1800
  - Internally: notebooklm source list --notebook {nb_id} --json

[OUTPUT · Done Criteria]
  - All sources status==ready → exit 0
  - Timeout reached before all ready → exit 1 + return list of not-ready sources
  - Returns: {"total": N, "ready": R, "not_ready": [...], "duration_s": D}

[QUALITY GATE]
  L1: print "[HH:MM:SS] {ready}/{total} ready" on every poll
  L2: poll_interval must be ≥ 30s (shorter intervals trigger NLM rate-limit)
  L3: on timeout → graceful exit; do not extend with "just one more 30-min wait"

[ANTI-PATTERNS]
  ❌ Running multiple wait processes in parallel (violates single-thread lock)
  ❌ poll_interval < 30s (triggers rate-limit)
  ❌ Allowing Phase 5 Generate to proceed before all sources are ready (violates Rule #7)

[FEEDBACK] [P8-{X}-COMPLETION] + JSON + last 10 lines of poll log
[WORD LIMIT] 300
```

### T-NLM-GEN — NLM Multi-Dimension Report Batch Generation

```
You are an ARGUS P8 sub-agent (template=T-NLM-GEN).

[GOAL] Use the N prompts in {prompts_file} to batch-generate reports for notebook={nb_id}.
       concurrency=3 for reliable types (prevents NLM rate-limiting).

[CONTEXT · ARGUS Ironclad Rules]
- Rule #7: Re-assert sources are ready before calling (guaranteed by caller or passed from wait phase)
- SOP §2.4: notebooklm generate report --format custom --append <prompt>
- concurrency=3 is the NLM reliable-type ceiling (exceeding it triggers rate-limiting)

[INPUT]
  - Tool: python3 scripts/nlm_pipeline.py generate --notebook-id {nb_id}
          --prompts-file {prompts_file} --concurrency 3
  - ask mode is disabled by default (Ironclad Rule enforcement)

[OUTPUT · Done Criteria]
  - Each prompt returns one task_id (artifact)
  - Returns JSON: {"prompts": N, "fired": F, "task_ids": {dim: task_id}}

[QUALITY GATE]
  L1: paste subprocess.run stdout for each prompt
  L2: --concurrency > 3 → argparse clamps to 3 + emits WARN (already implemented in code)
  L3: 429 rate-limit → built-in --retry 2 with exponential backoff; if still failing → rerun at concurrency=1

[ANTI-PATTERNS]
  ❌ Firing without wait phase (violates Rule #7)
  ❌ Enabling --enable-ask as the default path (report-only unless user explicitly requests ask)
  ❌ concurrency > 3 (mandated ceiling)

[FEEDBACK] [P8-{X}-COMPLETION] + JSON task_id map + stdout
[WORD LIMIT] 400
```

### T-NLM-DL — NLM Report Download

```
You are an ARGUS P8 sub-agent (template=T-NLM-DL).

[GOAL] Download the report markdown for {task_id_list} to {output_dir}/nlm-reports/{dim}.md.

[CONTEXT · ARGUS Ironclad Rules]
- Rule #6: Main AI must not read source body text. This task downloads only the note/report text
  (already an AI-synthesized artifact) — never call notebooklm source fulltext.
- SOP §2.4 line 319/588: artifact wait + download report

[INPUT]
  - Tool: python3 scripts/nlm_pipeline.py download --notebook-id {nb_id}
          --output-dir {output_dir}/nlm-reports
  - Internally: for dim, task_id: notebooklm artifact wait + download report

[OUTPUT · Done Criteria]
  - N files at {dim}.md, each ≥3 KB
  - Files < 3 KB → mark quality_low but do not delete; Orient phase decides their value
  - Returns: {"downloaded": N, "quality_low": L, "files": [...]}

[QUALITY GATE]
  L1: print "[{dim}] {bytes}B → {path}" for each file
  L2: must not call notebooklm source fulltext (Rule #6)
  L3: artifact wait timeout → record task_id for later retry; do not delete partial file

[ANTI-PATTERNS]
  ❌ Reading source body text (Rule #6)
  ❌ Deleting files < 3 KB (may still have value as quality_low)

[FEEDBACK] [P8-{X}-COMPLETION] + file manifest + JSON
[WORD LIMIT] 300
```

### T-BIRD — X/Twitter Batch Query

```
You are an ARGUS P8 sub-agent (template=T-BIRD).

[GOAL] Run bird CLI batch search for {query_list} and persist results to {output_dir}/bird/{slug}.txt.

[CONTEXT · ARGUS Ironclad Rules]
- SOP §3: bird CLI usage (Chrome default profile cookie)
- Concurrency ≤ 5 (X/Twitter anti-bot tolerance threshold)

[INPUT]
  - Tool: python3 scripts/bird_batch.py {query_list} --output-dir {output_dir}/bird
          --per-query 20 --concurrency 5
  - Bird CLI: bird search "..." -n 20

[OUTPUT · Done Criteria]
  - One {slug}.txt per query
  - Returns: {"queries": N, "tweets_total": T, "errors": [...]}

[QUALITY GATE]
  L1: paste the first tweet returned for each query
  L2: "0 results" → retry with a broader keyword
  L3: cookie expired → exit 1 + prompt user to run bird login

[ANTI-PATTERNS]
  ❌ concurrency > 5 (anti-bot)
  ❌ Using Bird to fetch YouTube URLs (YouTube belongs to the WebAccess channel — Ironclad Rule division of responsibility)

[FEEDBACK] [P8-{X}-COMPLETION] + JSON + sample tweets
[WORD LIMIT] 400
```

### T-WEB — WebAccess Multi-Page Crawl

```
You are an ARGUS P8 sub-agent (template=T-WEB).

[GOAL] Crawl {url_list} via WebAccess CDP Proxy 3456 and persist results to
       {output_dir}/webaccess/{slug}.md.

[CONTEXT · ARGUS Ironclad Rules]
- SOP §5: web-access skill + CDP Proxy 3456
- Multiple targetId concurrent requests are fine (no anti-ban risk)
- Rule #6: Do not read source body text (extract URL + title + snippet only)

[INPUT]
  - Tool: python3 scripts/webaccess_crawl.py {url_list} --output-dir {output_dir}/webaccess
          --concurrency 5

[OUTPUT · Done Criteria]
  - One {slug}.md per URL (containing title + snippet, not full body)
  - YouTube URLs discovered: separately persisted to {output_dir}/webaccess/youtube-urls.txt for NLM inject
  - Returns: {"urls": N, "youtube_discovered": Y, "errors": E}

[QUALITY GATE]
  L1: paste CDP targetId allocation log
  L2: Chrome 9222 "connection failed" → Rule #4 (DevToolsActivePort path mismatch)
  L3: Rule #6 (extract URL + title + snippet only — no body text)

[ANTI-PATTERNS]
  ❌ Reading URL body text and feeding it to the main AI (Rule #6)
  ❌ Single-threaded serial crawl (wastes targetId concurrency capacity)

[FEEDBACK] [P8-{X}-COMPLETION] + JSON + sample youtube-urls
[WORD LIMIT] 400
```

### T-GH — GitHub gh CLI Data Fetch

```
You are an ARGUS P8 sub-agent (template=T-GH).

[GOAL] Fetch data for {repo_or_query} using gh CLI with
       {subcmd: repo / trending / issues / release / search-repos},
       and persist to {output_dir}/github/ or github-trending/.

[CONTEXT · ARGUS Ironclad Rules]
- SOP §6.5: gh CLI usage (authenticated: 5000/hr; unauthenticated: 60/hr)
- Concurrency: repo / issues / release → Sem(10); search → independent Sem(3) (Search API: 30/min)
- Rule #6: No body text reads (README / issues are aggregated data — full content allowed, but no recursive external links)

[INPUT]
  - Tool: python3 scripts/github_fetch.py {subcmd} {args}
  - Pre-check: gh auth status shows Logged in

[OUTPUT · Done Criteria]
  - repo: {output_dir}/github/<owner>__<repo>/{meta.json, readme.md, issues.json, release.md}
  - trending: {output_dir}/github-trending/<lang>-<date>.md
  - Returns: {"subcmd": "...", "items": N, "bytes": B}

[QUALITY GATE]
  L1: paste gh CLI output
  L2: 403 rate-limit → exponential-backoff retry
  L3: --to-nlm flag enabled → NLM queue cap ≤ 30 (recommended Rule #10)

[ANTI-PATTERNS]
  ❌ Unauthenticated calls exceeding 60/hr (prompt user to run `gh auth login`)
  ❌ search concurrency > 3 (triggers Search API 30/min limit)
  ❌ --to-nlm appending > 30 entries (squeezes NLM 300-source main cap)

[FEEDBACK] [P8-{X}-COMPLETION] + JSON + sample gh output
[WORD LIMIT] 400
```

---

## §4. Multi-Agent Orchestration Topologies (main AI side)

### §4.1 Fan-out Parallel (independent channel dogfood)

```
Main AI / probe.py
  ├─ spawn T-PPLX-QUICK  (occupies Sem(3) slot 1)
  ├─ spawn T-PPLX-DEEP   (occupies Sem(3) slot 1)  ← share the same Sem
  ├─ spawn T-BIRD        (independent Sem(5))
  ├─ spawn T-WEB         (independent targetId concurrency)
  └─ spawn T-GH          (independent Sem(10))
  ↓
  wait for all [P8-x-COMPLETION]
  ↓
  fan-in → manifest.json aggregation
```

### §4.2 NLM 7-Phase Serial (internally ordered)

```
Main AI
  ├─ Phase 1: Discover (spawn multiple Explore — URL collection)  ← parallel
  ├─ Phase 2: Dedupe (local script, main AI runs directly)
  ├─ Phase 1.5: User Gate (human reviews prompts.json)
  ├─ Phase 3: spawn T-NLM-INJECT (serial, single agent)
  ├─ Phase 4: spawn T-NLM-WAIT (single-threaded agent poll)
  ├─ Phase 5: spawn T-NLM-GEN (concurrency=3 internally)
  └─ Phase 6: spawn T-NLM-DL (download, single agent)
```

### §4.3 Fan-in Aggregation (all COMPLETION signals collected)

- Main AI waits for all [P8-x-COMPLETION] signals
- Groups results by template ID (Perplexity block / NLM block / Bird block / ...)
- Writes `manifest.json` + generates consolidated report

---

## §5. Anti-Pattern Ironclad Checklist (strictly forbidden — violation = 3.25)

```
┌────────────────────────────────────────┬──────────────────────────────────┐
│ Anti-pattern                            │ Consequence                       │
├────────────────────────────────────────┼──────────────────────────────────┤
│ Perplexity total concurrency > 3        │ Account ban                       │
│ NLM source add in parallel (ThreadPool) │ NLM rate-limit 503 + possible ban │
│ NLM source add sleep < 1.5s            │ Triggers rate-limit               │
│ Chrome 9222 for Perplexity             │ Triggers hcaptcha                 │
│ Main AI reads source body text         │ Violates Rule #6; pollutes context │
│ Fire NLM report before sources ready   │ Shallow output (<300-word summary) │
│ NLM source injection exceeding 300     │ Rule #5 violation, exit 4          │
│ Hand-writing CDP instead of skill CLI  │ Violates "no reinvention" rule     │
│ Spawning agent for a <30s task         │ Spawn overhead exceeds benefit     │
│ Task Prompt missing any of 6 elements  │ Sub-agent improvises; output drifts│
└────────────────────────────────────────┴──────────────────────────────────┘
```

---

## §6. Usage Examples (real main AI invocations)

### Example 1: Single-channel research — "2026 AI Agent Frameworks"

```python
# Main AI assessment: single-channel Deep Research, 5–10 min → dispatch 1× T-PPLX-DEEP
spawn(
    subagent_type="Explore",
    template="T-PPLX-DEEP",
    variables={
        "query": "2026 AI Agent framework landscape comparison",
        "output_dir": "/tmp/agent-research",
        "slug": "agent-framework-2026",
        "budget_path": "/tmp/agent-research/budget.json"
    }
)
```

### Example 2: 5-channel dogfood (full NLM 7-phase pipeline)

```
Phase 1 Discover (parallel):
  spawn T-PPLX-QUICK (4 queries — URL collection)
  spawn T-BIRD (3 queries — X sentiment)
  spawn T-WEB (3 URLs — deep crawl + YouTube discovery)
  spawn T-GH (2 repos + 1 trending)
Phase 2 Dedupe (main AI runs locally)
Phase 1.5 User Gate (prompts.json review)
Phase 3 Inject: spawn T-NLM-INJECT (serial, 150 URLs)
Phase 4 Wait: spawn T-NLM-WAIT (15–30 min poll)
Phase 5 Generate: spawn T-NLM-GEN (15 prompts, concurrency=3)
Phase 6 Download: spawn T-NLM-DL (15 reports)
```

### Example 3: Point validation — Perplexity availability

```python
# Simple check; main AI runs directly — no agent dispatch
subprocess.run([
    "python3", "scripts/probe.py",
    "doctor", "--essential", "comet-9223,perplexity-auth"
])
```

---

## §8. Source Routing Decision Tree (SOP §2.0.1 canonical reference)

When the main AI receives a batch of URLs or data, it **must** consult the table below to decide "NLM or standalone channel." Misrouting = Ironclad Rule #8 violation.

```
┌─────────────────────────────┬──────────┬─────────────────────────────────┐
│ Data type                    │ Route to │ Rationale                        │
├─────────────────────────────┼──────────┼─────────────────────────────────┤
│ YouTube URL (video)          │ NLM      │ NLM auto-fetches transcript;      │
│                              │          │ deeper indexing                   │
│ PDF / academic paper         │ NLM      │ Full-text indexing (NLM strength) │
│ Web article (NLM-compatible) │ NLM      │ Title + body; high readability    │
│ GitHub README (--to-nlm)     │ NLM      │ ≤30 cap (Rule #10 recommended)    │
│ Perplexity Quick/Deep answer │ Standalone│ Already AI-synthesized; feeding  │
│                              │          │ to NLM = second-order AI noise    │
│ Bird (X/Twitter) tweets      │ Standalone│ X anti-scraping; NLM crawler hits│
│                              │          │ login wall                        │
│ Wikipedia / news sources     │ NLM      │ Well-structured; high index value │
│ GitHub Issues (JSON)         │ Standalone│ Structured data; main AI reads   │
│                              │          │ directly                          │
│ Perplexity Sources list refs │ NLM      │ These are the original URLs —     │
│                              │          │ not Perplexity AI answers         │
└─────────────────────────────┴──────────┴─────────────────────────────────┘
```

**Rule #5 cap**: NLM main channel ≤ 300 sources/notebook. After routing:
- 150 YouTube + 150 other NLM-compatible = exactly at the 300 cap
- Exceeding cap → fail-fast exit 4 (no truncation allowed — Phase 2 DEDUPE must filter first)

**Standalone channel persistence**: each channel writes under `research-raw/{perplexity_quick, perplexity_deep, bird, webaccess, github}/`; the main AI references these as "external supplementary sections" in the final consolidated report.

---

## §9. Global Budget Management (SOP §1.8 + §7.3 canonical)

```
┌────────────────────────┬───────────────┬────────────────────────────┐
│ Budget dimension        │ Default limit  │ On-breach behavior          │
├────────────────────────┼───────────────┼────────────────────────────┤
│ Wall time               │ 90 min (--budget)│ Graceful degrade; collect fan-in│
│ Perplexity Deep quota  │ 100/day        │ Per-task budget-file tracks remaining│
│ NLM 300 sources        │ 300/notebook   │ Fail-fast exit 4             │
│ NLM generate (reports)  │ 15/round       │ User manually approves prompts.json│
│ GitHub rate-limit       │ 5000/hr authed │ Exponential-backoff retry    │
│ Bird (Twitter anti-bot) │ < 50 queries/hr│ Zero-results → switch query  │
└────────────────────────┴───────────────┴────────────────────────────┘
```

**Budget written to manifest.json**:
```json
{
  "budget": {
    "wall_seconds_total": 5400,
    "wall_seconds_used": 2100,
    "perplexity_deep_remaining": 97,
    "nlm_sources_used": 287,
    "nlm_sources_cap": 300,
    "nlm_reports_fired": 10
  }
}
```

**On-breach behavior**: when `--budget` expires, probe.py triggers a deadline → `stage_wait_and_report`:
1. Already-fired async tasks (NLM / Deep) are allowed to finish — do not kill
2. Unfired channels are marked `skipped`
3. Main AI fan-in generates the best possible report from the current manifest state

---

## §10. Failure Degradation Matrix (SOP §8 canonical — must be covered by doctor)

```
┌────────────────┬──────────────────────┬────────────────────────────┐
│ Channel         │ Failure scenario      │ Degradation strategy        │
├────────────────┼──────────────────────┼────────────────────────────┤
│ Perplexity Quick│ Comet not running /   │ Skip → mark degraded; main  │
│                │ not authenticated     │ AI can still use WebAccess  │
│                │                      │ + GitHub as fallback         │
│ Perplexity Deep│ hcaptcha / 100 quota  │ Skip; run Quick (3-round)   │
│                │ exhausted             │ only                        │
│ NLM            │ notebooklm not        │ All NLM channels skip; main │
│                │ authenticated /       │ AI uses WebAccess +         │
│                │ 300 cap exceeded      │ Perplexity as standalone     │
│ NLM generate   │ 429 rate-limit        │ Reduce concurrency to 1;    │
│                │                      │ wait 5–10 min; retry         │
│ Bird           │ Cookie expired        │ Bird skip; prompt user to   │
│                │                      │ run bird login               │
│ WebAccess      │ Chrome 9222 not       │ Fall back to chrome-reader.py│
│                │ running /             │ for single-URL reads         │
│                │ DevToolsActivePort    │                             │
│                │ path mismatch (Rule #4)│                            │
│ GitHub         │ 403 rate-limit        │ Backoff 60s → 120s → 240s;  │
│                │ / 403 permission      │ re-authenticate if org SSO  │
│ Chrome-reader  │ URL fetch failed      │ Mark error; other URLs       │
│                │                      │ unaffected                   │
└────────────────┴──────────────────────┴────────────────────────────┘
```

**Fail-fast vs. Graceful Degradation** (SOP §7.4):
- **Must fail-fast**: Rule #5 (300 cap) / Rule #7 (fire report before ready) / Rule #8 (cross-channel pollution)
- **Must degrade gracefully**: single-channel auth failure / single-URL crawl error / account-level rate throttle
- **Decision rule**: if the violation **would cause a ban → fail-fast**; if it only reduces output completeness → **degrade**

**manifest.json status enum**:
- `success` — channel output meets expectation
- `degraded` — channel partially succeeded (below expectation but non-zero)
- `skipped` — channel did not run (doctor fail / graceful skip)
- `error` — channel crashed (requires user investigation)

---

## §11. Perplexity Two-Tier Decision Tree (SOP §1.0 canonical)

```
┌────────────────────────┬──────────────────┬────────────────────┐
│ Scenario                │ Choose Quick      │ Choose Deep         │
├────────────────────────┼──────────────────┼────────────────────┤
│ Speed requirement       │ < 60s            │ 5–10 min acceptable │
│ Answer depth required   │ 1–2 paragraphs   │ Thousands of words  │
│ Source count            │ 10–30            │ 100–300             │
│ Structured sources req  │ Optional         │ Required (## Sources)│
│ Quota consumed          │ Low (no Deep q.) │ 1/100 Deep quota    │
│ Typical use case        │ "What is X"      │ "X industry TAM"    │
│                        │ "Quick A vs B"   │ "Full Y tech stack"  │
└────────────────────────┴──────────────────┴────────────────────┘
```

**Decision heuristics**:
- "What is / define / quick intro" → Quick
- "TAM / competitive landscape / deep tech stack" → Deep
- **In a typical research run: 1–2 Deep + 5–8 Quick is the most common mix**

**Prohibited patterns** (SOP §1.0):
- ❌ Reaching for Perplexity when NLM reports already cover the question (duplication)
- ❌ Running Deep on questions Quick can answer (quota waste)
- ❌ Supplementing an insufficient Deep answer with Quick (order is reversed — Quick first to scope, then Deep to drill)

---

## §3.11 T-USER-GATE — Phase 1.5 Prompts Approval Gate (SOP §2.0)

> **Note**: this is not a real spawn template — it is a mandatory checkpoint the main AI runs itself. It is included in the template library to prevent it from being skipped.

```
After the main AI has designed prompts.json (15-dimension NLM report prompts), it **must**:

1. Display prompts.json in full for user review
   - Each prompt includes: [dimension name] [core query] [expected output]
   - Self-check against the 5 rubrics from prompt-library.md (mark each ✅/❌)
2. Wait for user response: ✅ approve / modify N items / ❌ redo
3. Phase 5 generate is strictly forbidden until approval is granted

[CONTEXT · ARGUS Ironclad Rules]
- SOP §2.0.2: with 300 sources injected into NLM, a prompt phrased as "summarize X for me"
  will return only a generic overview with 5 bullets
- This gate is the last line of defense against the main AI self-servingly firing low-quality
  prompts that waste NLM quota

[OUTPUT · Done Criteria]
- prompts.json has been displayed; user has responded
- Any requested modifications have been applied (diff shown)
- Main AI explicitly states: "Phase 1.5 Gate passed — proceeding to Phase 5"

[ANTI-PATTERNS]
❌ Main AI bypasses Gate and self-fires generate (violates SOP §2.0 Ironclad Rule)
❌ Pre-copying prompts.json to nlm_pipeline.py before Gate approval (soft-fire)
❌ Using a generic template prompt instead of a project-specific design (violates §2.0.2)
```

---

## §3.12 T-NLM-MEDIA — NLM Media Generation (SOP §2.4.2)

```
You are an ARGUS P8 sub-agent (template=T-NLM-MEDIA).

[GOAL] Generate {media_type} (audio / video / mindmap / quiz / flashcards /
       infographic / slide-deck / data-table) for notebook={nb_id}
       and persist to {output_dir}/nlm-media/.

[CONTEXT · ARGUS Ironclad Rules]
- SOP §2.4.2: media generation modes — reliable types (mindmap / slide-deck): sync;
  unreliable types (audio / video / quiz / flashcards): ≤1 concurrency + --retry 2
- Rule #6: Download only the generated media artifact — do not query source body text
- Media generation quota is independent of report quota and billed per content type

[INPUT]
  - Tool: notebooklm generate {media_type} [description]
  - Optional description: e.g. "focus on chapter 3" / "for ages 5 and up"

[OUTPUT · Done Criteria]
  - Single file output: {output_dir}/nlm-media/{media_type}.{ext}
  - audio: .mp3 / video: .mp4 / mindmap: .json / infographic: .png /
    slide-deck: .pdf / quiz / flashcards / data-table: .json or .csv
  - Returns: {"media_type": "...", "size_bytes": B, "artifact_id": "..."}

[QUALITY GATE]
  L1: paste generate + download subprocess output
  L2 unreliable types (audio / video / quiz / flashcards): concurrency=1 locked; error on any excess
  L3: artifact wait timeout → retry twice, then mark degraded — do not abort

[ANTI-PATTERNS]
  ❌ audio / video concurrency > 1 (unreliable types trigger rate control)
  ❌ Firing before Phase 4 Wait completes (Rule #7 applies here too)
  ❌ Skipping artifact wait and downloading immediately (artifact may not be ready)

[FEEDBACK] [P8-{X}-COMPLETION] + JSON + media file path
[WORD LIMIT] 300
```

---

## §7. Template Upgrade Protocol

- New channel added → add corresponding `T-XXX` template + update §2 mapping table
- Ironclad Rule changed → update [CONTEXT · Ironclad Rules] and [ANTI-PATTERNS] in all affected templates
- Version number lives at the top of this file; templates inherit it implicitly (no per-template versioning)
- After any change, run `verify_docs.sh` to check anchor consistency

---

> **One-liner**: when the main AI needs ARGUS and asks "what agent should I spawn?" → **read §2 mapping table → select T-XXX → fill variables → spawn**. The six elements, three red lines, and anti-patterns are all embedded in the templates — zero hand-composition needed.
