# ARGUS × OODC — Observe Phase Integration Spec

> Read me when: ARGUS is being invoked as the OODC Observe-phase execution layer, or when you need to understand how probe.py integrates with the harness PROJECT_STATE handoff.

Source: TOOL-SOP.md §7 (async orchestration) + §9 (probe.py pseudocode) + ARCHITECTURE.md §3 (recommended approach) + §7 (manifest schema) + `~/.claude/workflows/oodc-loop.md`

---

## ARGUS in the OODC Lifecycle

OODC state machine: `IDLE → OBSERVE → ORIENT → DECIDE → CREATE → CLOSURE → IDLE`

**ARGUS = the execution layer for the OBSERVE phase.**

```
OODC OBSERVE phase
    │
    ├── Main AI calls probe.py run ...       ← triggers ARGUS full-channel parallel collection
    │     (Stage 0 DOCTOR → Stage 1–4)
    │
    ├── probe.py writes _manifest.json       ← state anchor
    │
    └── probe.py completes → main AI reads manifest  ← OBSERVE complete
         (manifest only — never source body text — Ironclad Rule #6)
         → [OBSERVE_RESULT] written
         → oodc-loop.md state updated: step=ORIENT
```

**ORIENT phase**: main AI reads manifest summary + per-channel file-title lists → synthesizes analysis → emits `[ORIENT_VERDICT]`.
Main AI never touches `research-raw/` body content (Ironclad Rule #6 — absolute boundary).

---

## Two Integration Modes

Per ARCHITECTURE.md §3, the current recommended approach is **Mode A (pure script orchestration)**; Mode B is the Phase 4+ upgrade path.

### Mode A — Pure Script Invocation (recommended, current implementation)

```
OODC OBSERVE triggered
    │
    ▼
Main AI: Bash → python3 scripts/probe.py run \
               --topic "X" --budget 5400 --manifest /tmp/argus_run/manifest.json
    │
    ▼ (probe.py Stage 0–4 complete)
Main AI: Read /tmp/argus_run/manifest.json   ← manifest ONLY, never source body text
Main AI: [OBSERVE_RESULT] filled in
    │
    ▼
oodc-loop.md: sed 's/step=.*/step=ORIENT/' ~/.claude/.oodc-state-{project}
```

**OBSERVE_RESULT fill-in spec** (main AI extracts from manifest):
```
channels_completed: perplexity_quick, bird, webaccess
channels_skipped: [list from manifest.skipped]
nlm_sources_count: {manifest.channels.nlm.sources}
nlm_phases_complete: {manifest.channels.nlm.phase}
total_files: {sum of all channel file counts}
budget_used_s: {5400 - manifest.budget_remaining_s}
```

### Mode B — Harness Plugin Embedding (Phase 4+ upgrade path)

probe.py runs as a harness plugin; OODC PROJECT_STATE drives stage transitions.

**Integration interface** (future implementation reference):
```python
# harness passes PROJECT_STATE
project_state = {
    "oodc_step": "OBSERVE",
    "project": "<your-topic>",
    "oodc_state_file": "~/.claude/.oodc-state-<your-topic>",
    "topic": "<research topic>",
    "target_decision": "<decision to inform>",
}

# probe.py reads it and writes back at Stage 4
project_state["oodc_observe_done"] = True
project_state["manifest_path"] = "/tmp/argus_run/manifest.json"
```

Not enabled yet (ARCHITECTURE.md §3 decision: Mode A POC first; Mode B evaluated after Phase 4).

---

## manifest.json Schema (OBSERVE phase output)

Full schema is in ARCHITECTURE.md §7. Fields relevant to OODC integration:

```json
{
  "topic": "string",
  "stage": "Stage 4 REPORT",
  "started_at": "ISO8601",
  "budget_remaining_s": 1200,
  "channels": {
    "perplexity_quick": {
      "status": "done|pending|skipped|error",
      "files": ["research-raw/perplexity_quick/q1.md", "..."]
    },
    "perplexity_deep": {
      "status": "done",
      "used_quota": 5,
      "files": ["research-raw/perplexity_deep/d1.md"]
    },
    "nlm": {
      "status": "done|pending",
      "phase": "Phase 6 DOWNLOAD",
      "notebook_id": "abc123",
      "sources": 285,
      "sources_ready": true,
      "reports": ["reports/pricing_matrix.md", "reports/tam_estimation.md"]
    },
    "bird": {
      "status": "done",
      "count": 45,
      "files": ["research-raw/bird/q1.txt"]
    },
    "webaccess": {
      "status": "done",
      "pages": 12,
      "files": ["research-raw/webaccess/page1.md"]
    }
  },
  "skipped": ["chrome_reader"],
  "artifacts": []
}
```

**How OODC ORIENT consumes the manifest** (Ironclad Rule #6 enforcement):

```python
# Orient phase — main AI performs only these operations
def orient_from_manifest(manifest_path):
    m = json.load(open(manifest_path))

    # ALLOWED: read manifest structure
    channels_done = [ch for ch, v in m["channels"].items() if v["status"] == "done"]
    file_list = [f for ch in channels_done for f in m["channels"][ch].get("files", [])]
    report_list = m["channels"]["nlm"].get("reports", [])

    # ALLOWED: read NLM report files (already AI-synthesized — these are the summary layer)
    for report_path in report_list:
        content = open(report_path).read()  # OK — NLM reports are structured analysis

    # FORBIDDEN: reading raw source content under research-raw/
    # open("research-raw/perplexity_quick/q1.md")  # violates Ironclad Rule #6
    # open("research-raw/bird/q1.txt")             # violates Ironclad Rule #6
    # open("research-raw/webaccess/q1.json")       # violates Ironclad Rule #6
```

---

## Async Orchestration and OODC Interaction Points

TOOL-SOP.md §7 describes two async patterns and where they fit within the OODC boundary:

### Sub-agent Wait Mode (single operation > 10 min)

```
OODC OBSERVE (main AI)
    │
    ├── Main AI: probe.py fire NLM research deep --no-wait
    ├── Main AI: manifest writes nlm.status=pending
    ├── Main AI: Spawn sub-agent → "Wait for NLM Phase 4, update manifest"
    │
    ├── Main AI continues: Bird / WebAccess complete in parallel
    │
    └── Sub-agent completes → manifest.nlm.status=done
        Main AI polls manifest → OBSERVE complete
        → [OBSERVE_RESULT] written
```

**OODC hook safeguard**: While `.oodc-state-{project}` exists with `step=OBSERVE`, `oodc-guard.sh` blocks unauthorized Write/Edit operations. probe.py's manifest writes are authorized operations (the manifest is not project source code).

### Manifest Poll Mode (multiple 1–5 min operations)

```python
# probe.py Stage 2–3 internal implementation
def poll_manifest_until_done(manifest_path, budget_s):
    start = time.time()
    while time.time() - start < budget_s:
        m = json.load(open(manifest_path))
        pending = [ch for ch, v in m["channels"].items() if v["status"] == "pending"]
        if not pending:
            break
        for ch in pending:
            check_channel_status(ch, m)  # updates manifest
        time.sleep(60)
    write_manifest(m)
```

Main AI waits for `probe.py run` to complete, then **reads the manifest once** — no repeated polling (avoids multiple Bash calls that pollute OODC session context).

---

## Budget Management and OODC Time Allocation

| OODC step | Typical duration | probe.py counterpart |
|-----------|-----------------|----------------------|
| OBSERVE (ARGUS) | 30–90 min | `--budget 5400` (90 min hard cap) |
| ORIENT | 10–20 min | Main AI analyzes manifest + NLM reports |
| DECIDE | 5–15 min | User reviews [ORIENT_VERDICT] |
| CREATE | 30–60 min | Produces output documents / decisions |
| CLOSURE | 10–20 min | doc-sync + KB + memory |

**`budget_remaining_s` drives graceful degradation** (probe.py §7.3):
```python
if remaining() < 300 and channel in ("nlm",):
    mark_skip(channel, "budget exhausted")
    # manifest marks channel as skipped; OBSERVE can still complete
```

---

## Ironclad Rule #6 Enforcement Within the OODC Framework

**Rule**: main AI never reads source body text — only URLs, titles, and snippets.

| Object being read | Allowed | Notes |
|------------------|---------|-------|
| `manifest.json` | Yes | State anchor; contains metadata only |
| `reports/*.md` (NLM-generated) | Yes | NLM has already synthesized these — they are the summary layer output |
| `prompts.json` | Yes | Phase 1b design artifact |
| `research-raw/perplexity_*/` | No | Raw Perplexity markdown; violates Rule #6 |
| `research-raw/bird/` | No | Raw Bird text; violates Rule #6 |
| `research-raw/webaccess/` | No | Raw crawled HTML/MD; violates Rule #6 |

**Only valid ORIENT-phase inputs**: manifest metadata + NLM reports.
If raw channel data is needed, it must be processed into a summary inside ARGUS and written to the manifest; the main AI then reads that summary.
