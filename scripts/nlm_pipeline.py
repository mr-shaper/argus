#!/usr/bin/env python3
"""
ARGUS — NotebookLM Pipeline (NLM) 7-phase sub-process orchestration

Phases (NLM namespace, distinct from probe.py Stage namespace):
  discover  — collect candidate URLs from Bird/Perplexity/WebAccess results
  dedupe    — dedup + filter + 300-source hard cap (Ironclad Rule #5)
  inject    — bulk-import YouTube + Doc URLs into a NotebookLM notebook
  wait      — poll sources until all status==ready (Ironclad Rule #7 gate)
  generate  — fire NLM prompts in batch to generate reports (concurrency=3 reliable)
  download  — fetch generated note text and persist to disk

Usage:
  nlm_pipeline.py discover --output urls_raw.json
  nlm_pipeline.py dedupe   --input urls_raw.json --output urls_deduped.json
  nlm_pipeline.py inject   --notebook-id NB_ID --yt-urls yt.txt --doc-urls doc.txt [--max-sources 300]
  nlm_pipeline.py wait     --notebook-id NB_ID [--poll-interval 30] [--timeout 1800]
  nlm_pipeline.py generate --notebook-id NB_ID --prompts-file prompts.txt [--concurrency 3] [--enable-ask]
  nlm_pipeline.py download --notebook-id NB_ID --output-dir DIR

Exit codes:
  0  success
  1  general error
  4  exceeded 300-source hard cap (Ironclad Rule #5)
  5  prompts failed rubric check

Ironclad Rules in effect:
  #5  Check len(urls) ≤ 300 before inject; exit 4 if exceeded
  #7  Check all sources_ready before generate; exit 1 if not complete
  #8  This script only consumes its own URL list; does not read Perplexity/Bird channel files (independent channel principle)
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# NLM MCP tool path (invoked via claude CLI)
CLAUDE_BIN = "claude"

# NLM reliable concurrency (Ironclad Rule: reliable=3, unreliable=1)
NLM_RELIABLE_CONCURRENCY = 3
NLM_UNRELIABLE_CONCURRENCY = 1


# ─── URL helpers ───

def load_urls(path: str) -> list:
    """Load URLs from file, one per line; skip blank lines and # comments."""
    p = Path(path)
    if not p.exists():
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        sys.exit(1)
    lines = p.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def atomic_write_json(path: str, data: dict):
    """Atomically write JSON (tmp → rename, prevents partial writes on interruption)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.rename(tmp, path)


# ─── Phase handlers ───

def phase_discover(args):
    """
    Phase 1 DISCOVER: collect candidate URLs from Bird/Perplexity/WebAccess result directories.
    Note: Ironclad Rule #8 (independent channel) — NLM discover is independent URL discovery;
    it does not read Perplexity/Bird channel files. The caller (probe.py) is responsible for
    assembling the URL list separately. This phase outputs an empty template for the user to fill.
    """
    output = args.output
    template = {
        "phase": "discover",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "note": "Ironclad Rule #8: NLM independent channel. URL list must be provided by probe.py or manually; do not mix with Perplexity/Bird results.",
        "youtube_urls": [],
        "doc_urls": []
    }
    atomic_write_json(output, template)
    print(f"[NLM Phase 1 DISCOVER] Template written: {output}")
    print("[NLM Phase 1 DISCOVER] Fill in youtube_urls / doc_urls, then run the dedupe phase")
    sys.exit(0)


def phase_dedupe(args):
    """
    Phase 2 DEDUPE: dedup + filter + 300-source hard cap (Ironclad Rule #5).
    """
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    yt_urls = list(dict.fromkeys(data.get("youtube_urls", [])))  # dedup preserving order
    doc_urls = list(dict.fromkeys(data.get("doc_urls", [])))

    total = len(yt_urls) + len(doc_urls)
    print(f"[NLM Phase 2 DEDUPE] After dedup: YouTube={len(yt_urls)}, Docs={len(doc_urls)}, Total={total}")

    # Ironclad Rule #5: hard cap at 300 → exit 4
    if total > 300:
        print(f"[ERROR] Ironclad Rule #5: exceeded 300-source cap (current {total}), exit 4", file=sys.stderr)
        print("[ERROR] Reduce URL list to ≤ 300 and rerun dedupe", file=sys.stderr)
        sys.exit(4)

    output_data = {
        "phase": "dedupe",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "youtube_urls": yt_urls,
        "doc_urls": doc_urls,
        "total": total,
        "status": "ready_for_inject"
    }
    atomic_write_json(args.output, output_data)
    print(f"[NLM Phase 2 DEDUPE] Complete, written: {args.output}")
    sys.exit(0)


def phase_inject(args):
    """
    Phase 3 INJECT: bulk-add URLs to a NotebookLM notebook.
    Implementation (v1.1, 2026-04-22): subprocess calls `notebooklm source add` serially in a loop + sleep 1.5s.
    Top-level policy: delegates to shelf/notebooklm CLI; no reimplementation (policy locked).
    anti-ban policy: inject must be serial (ThreadPool not allowed); sleep 1.5s to respect NLM rate limits.
    Reference: TOOL-SOP §2.2 line 461 original command.
    """
    yt_urls = load_urls(args.yt_urls)
    doc_urls = load_urls(args.doc_urls)
    all_urls = yt_urls + doc_urls
    total = len(all_urls)

    # Ironclad Rule #5 double-check
    if total > args.max_sources:
        print(f"[ERROR] Ironclad Rule #5: {total} sources > cap {args.max_sources}, exit 4", file=sys.stderr)
        sys.exit(4)

    print(f"[NLM Phase 3 INJECT] notebook={args.notebook_id}")
    print(f"[NLM Phase 3 INJECT] YouTube: {len(yt_urls)} | Docs: {len(doc_urls)} | Total: {total}/{args.max_sources}")

    if args.dry_run:
        print("[DRY-RUN] Not executing inject; printing first 3 URLs:")
        for u in all_urls[:3]:
            print(f"  - notebooklm source add {u!r} -n {args.notebook_id} --json")
        sys.exit(0)

    # anti-ban policy: serial only (no ThreadPool); sleep 1.5s between each add to protect rate limits
    success_ids = []
    errors = []
    for i, url in enumerate(all_urls, 1):
        try:
            r = subprocess.run(
                ["notebooklm", "source", "add", url,
                 "-n", args.notebook_id, "--json"],
                capture_output=True, text=True, timeout=60
            )
            if r.returncode == 0:
                try:
                    data = json.loads(r.stdout)
                    sid = data.get("source_id") or data.get("id") or "unknown"
                    success_ids.append({"url": url, "source_id": sid})
                    print(f"  [{i}/{total}] ✅ {url[:60]}... → {sid[:12]}")
                except json.JSONDecodeError:
                    success_ids.append({"url": url, "source_id": "unparsed"})
                    print(f"  [{i}/{total}] ✅ {url[:60]}... (non-JSON output, treated as success)")
            else:
                errors.append({"url": url, "stderr": r.stderr[:200]})
                print(f"  [{i}/{total}] ⚠️ {url[:60]}... → failed (non-blocking, continuing)", file=sys.stderr)
        except subprocess.TimeoutExpired:
            errors.append({"url": url, "stderr": "timeout 60s"})
            print(f"  [{i}/{total}] ⏱️ {url[:60]}... timeout", file=sys.stderr)
        except Exception as e:
            errors.append({"url": url, "stderr": str(e)[:200]})
            print(f"  [{i}/{total}] ❌ {url[:60]}... exception: {e}", file=sys.stderr)

        # anti-ban policy: must sleep 1.5s before adding the next URL
        if i < total:
            time.sleep(1.5)

    print(f"\n[NLM Phase 3 INJECT] Complete: {len(success_ids)}/{total} succeeded, {len(errors)} failed")
    if errors:
        print(f"[NLM Phase 3 INJECT] Failed URLs recorded (non-blocking for Phase 4):")
        for e in errors[:5]:
            print(f"  - {e['url'][:60]}... ({e['stderr'][:80]})")
    # source add errors are non-blocking; Phase 4 wait will filter out sources that are not ready
    sys.exit(0)


def phase_wait(args):
    """
    Phase 4 WAIT: poll NLM notebook until all sources are ready (Ironclad Rule #7 gate).
    Implementation (v1.1, 2026-04-22): subprocess calls `notebooklm source list --json` in a polling loop.
    anti-ban policy: single-threaded polling (concurrent wait processes forbidden); poll_interval ≥ 30s.
    Reference: TOOL-SOP §2.3 lines 513-524.
    """
    print(f"[NLM Phase 4 WAIT] notebook={args.notebook_id}")
    print(f"[NLM Phase 4 WAIT] poll_interval={args.poll_interval}s, timeout={args.timeout}s")
    print(f"[NLM Phase 4 WAIT] Ironclad Rule #7: all status==ready required before generate")

    if args.dry_run:
        print("[DRY-RUN] Stub complete, assuming sources ready")
        sys.exit(0)

    # anti-ban policy: poll_interval lower bound is 30s
    poll_interval = max(30, args.poll_interval)
    if args.poll_interval < 30:
        print(f"[WARN] poll_interval {args.poll_interval}s < 30s lower bound, clamped to 30s (rate-limit protection)",
              file=sys.stderr)

    start = time.time()
    deadline = start + args.timeout
    last_ready = 0

    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["notebooklm", "source", "list", "-n", args.notebook_id, "--json"],
                capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            print(f"[NLM Phase 4 WAIT] source list timeout, retrying in {poll_interval}s",
                  file=sys.stderr)
            time.sleep(poll_interval)
            continue

        if r.returncode != 0:
            print(f"[ERROR] source list failed: {r.stderr[:200]}", file=sys.stderr)
            sys.exit(1)

        try:
            data = json.loads(r.stdout)
            sources = data.get("sources", data) if isinstance(data, dict) else data
        except json.JSONDecodeError:
            print(f"[ERROR] source list JSON parse failed: {r.stdout[:200]}", file=sys.stderr)
            sys.exit(1)

        total = len(sources)
        ready = sum(1 for s in sources if s.get("status", "").lower() == "ready")
        not_ready = [s for s in sources if s.get("status", "").lower() != "ready"]
        elapsed = int(time.time() - start)

        if ready != last_ready:
            print(f"[{elapsed}s] {ready}/{total} sources ready")
            last_ready = ready

        if total == 0:
            print(f"[ERROR] notebook {args.notebook_id} has 0 sources; run inject first",
                  file=sys.stderr)
            sys.exit(1)

        if ready == total:
            print(f"\n[NLM Phase 4 WAIT] ✅ All {total} sources ready, elapsed {elapsed}s")
            print(f"[NLM Phase 4 WAIT] Ironclad Rule #7 gate passed, proceed to Phase 5 Generate")
            sys.exit(0)

        time.sleep(poll_interval)

    # Timeout
    print(f"\n[NLM Phase 4 WAIT] ⏱️ timeout {args.timeout}s expired", file=sys.stderr)
    print(f"[NLM Phase 4 WAIT] {ready}/{total} ready, {len(not_ready)} not ready", file=sys.stderr)
    for s in not_ready[:10]:
        title = s.get("title", s.get("url", s.get("id", "?")))[:60]
        print(f"  - NOT READY: {title} (status={s.get('status', '?')})", file=sys.stderr)
    sys.exit(1)


def phase_generate(args):
    """
    Phase 5 GENERATE: fire prompts in batch to generate NLM reports.
    Implementation (v1.1, 2026-04-22): ThreadPoolExecutor max_workers=3 calling `notebooklm generate report`.
    Top-level policy: delegates to shelf/notebooklm CLI; no reimplementation (policy locked).
    Reference: TOOL-SOP §2.4 lines 541-548 + SOP §2.0.2 Prompt quality gate (caller is responsible for prompts passing User Gate).
    Ironclad Rule #7 assumption: caller has already run phase_wait, guaranteeing all sources ready.
    """
    import concurrent.futures

    if args.enable_ask:
        print("[WARN] --enable-ask enabled; NLM will allow ask mode during generation (not recommended, consumes quota fast)", file=sys.stderr)
    else:
        print("[NLM Phase 5 GENERATE] ask mode disabled (Ironclad Rule default)")

    prompts_file = Path(args.prompts_file)
    if not prompts_file.exists():
        print(f"[ERROR] prompts file not found: {args.prompts_file}", file=sys.stderr)
        sys.exit(1)

    prompts = [l.strip() for l in prompts_file.read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith("#")]

    if not prompts:
        print("[ERROR] prompts file is empty", file=sys.stderr)
        sys.exit(1)

    concurrency = min(args.concurrency, NLM_RELIABLE_CONCURRENCY)
    if args.concurrency > NLM_RELIABLE_CONCURRENCY:
        print(f"[WARN] --concurrency {args.concurrency} exceeds NLM reliable cap {NLM_RELIABLE_CONCURRENCY}, clamped to {NLM_RELIABLE_CONCURRENCY}", file=sys.stderr)

    print(f"[NLM Phase 5 GENERATE] notebook={args.notebook_id}")
    print(f"[NLM Phase 5 GENERATE] prompts={len(prompts)}, concurrency={concurrency}")

    if args.dry_run:
        print("[DRY-RUN] Will fire the following prompts:")
        for i, p in enumerate(prompts, 1):
            print(f"  {i}. notebooklm generate report --format custom --append {p[:60]!r}... -n {args.notebook_id}")
        sys.exit(0)

    def fire_one(idx_prompt):
        idx, prompt = idx_prompt
        try:
            r = subprocess.run(
                ["notebooklm", "generate", "report",
                 "--format", "custom",
                 "--append", prompt,
                 "-n", args.notebook_id,
                 "--json", "--retry", "2"],
                capture_output=True, text=True, timeout=900
            )
            if r.returncode != 0:
                return (idx, None, r.stderr[:200])
            try:
                data = json.loads(r.stdout)
                task_id = data.get("task_id") or data.get("artifact_id") or data.get("id")
                return (idx, task_id, None)
            except json.JSONDecodeError:
                return (idx, None, f"JSON parse fail: {r.stdout[:100]}")
        except subprocess.TimeoutExpired:
            return (idx, None, "timeout 900s")
        except Exception as e:
            return (idx, None, f"exception: {e}")

    task_ids = {}
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(fire_one, (i, p)) for i, p in enumerate(prompts, 1)]
        for f in concurrent.futures.as_completed(futures):
            idx, task_id, err = f.result()
            prompt_preview = prompts[idx - 1][:60]
            if task_id:
                task_ids[f"dim_{idx:02d}"] = task_id
                print(f"  [{idx}/{len(prompts)}] ✅ fired → task_id={task_id[:12]}... ({prompt_preview}...)")
            else:
                errors.append({"idx": idx, "prompt": prompt_preview, "error": err})
                print(f"  [{idx}/{len(prompts)}] ❌ failed: {err}", file=sys.stderr)

    print(f"\n[NLM Phase 5 GENERATE] Complete: fired {len(task_ids)}/{len(prompts)}, errors {len(errors)}")
    # Write task_ids to {notebook_id}-tasks.json for Phase 6 download
    out_file = Path(f"nlm-tasks-{args.notebook_id[:8]}.json")
    atomic_write_json(str(out_file), {"notebook_id": args.notebook_id, "task_ids": task_ids, "errors": errors})
    print(f"[NLM Phase 5 GENERATE] task_ids written: {out_file}")
    sys.exit(0 if not errors else 1)


def phase_download(args):
    """
    Phase 6 DOWNLOAD: for each task_id from Phase 5, run artifact wait + download report.
    Implementation (v1.1, 2026-04-22): subprocess calls `notebooklm artifact wait` + `notebooklm download report`.
    Ironclad Rule #6: only download note text (report markdown is already a summarized artifact); do not call source fulltext.
    Reference: TOOL-SOP §2.4 lines 319/588 + SKILL.md line 463 (artifact wait exit codes).
    Input: Phase 5 output nlm-tasks-{nb_id_short}.json (contains task_ids map).
    """
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[NLM Phase 6 DOWNLOAD] notebook={args.notebook_id}")
    print(f"[NLM Phase 6 DOWNLOAD] output_dir={output_dir}")
    print(f"[NLM Phase 6 DOWNLOAD] Ironclad Rule #6: only download note/report text; do not call source fulltext")

    # Locate Phase 5 task_ids file
    tasks_file = Path(f"nlm-tasks-{args.notebook_id[:8]}.json")
    if not tasks_file.exists():
        print(f"[ERROR] Phase 5 task_ids file not found: {tasks_file}", file=sys.stderr)
        print(f"[ERROR] Run phase_generate first to produce task_ids", file=sys.stderr)
        sys.exit(1)

    task_data = json.loads(tasks_file.read_text(encoding="utf-8"))
    task_ids = task_data.get("task_ids", {})

    if not task_ids:
        print(f"[WARN] task_ids is empty; Phase 5 may have failed entirely", file=sys.stderr)
        sys.exit(1)

    print(f"[NLM Phase 6 DOWNLOAD] Artifacts to download: {len(task_ids)}")

    if args.dry_run:
        print("[DRY-RUN] Will download the following artifacts:")
        for dim, tid in task_ids.items():
            print(f"  - notebooklm artifact wait {tid[:12]}... → download report {dim}.md")
        sys.exit(0)

    success = []
    errors = []
    for dim, task_id in task_ids.items():
        # Step 1: artifact wait (until ready)
        try:
            r_wait = subprocess.run(
                ["notebooklm", "artifact", "wait", task_id,
                 "-n", args.notebook_id,
                 "--timeout", "900", "--json"],
                capture_output=True, text=True, timeout=1000
            )
            if r_wait.returncode != 0:
                errors.append({"dim": dim, "task_id": task_id,
                               "stage": "wait", "error": r_wait.stderr[:200]})
                print(f"  [{dim}] ⏱️ artifact wait failed: {r_wait.stderr[:100]}",
                      file=sys.stderr)
                continue
        except subprocess.TimeoutExpired:
            errors.append({"dim": dim, "task_id": task_id,
                           "stage": "wait", "error": "timeout 1000s"})
            print(f"  [{dim}] ⏱️ artifact wait timeout", file=sys.stderr)
            continue

        # Step 2: download report (markdown)
        out_path = output_dir / f"{dim}.md"
        try:
            r_dl = subprocess.run(
                ["notebooklm", "download", "report", str(out_path),
                 "-n", args.notebook_id,
                 "-a", task_id, "--force"],
                capture_output=True, text=True, timeout=120
            )
            if r_dl.returncode != 0:
                errors.append({"dim": dim, "task_id": task_id,
                               "stage": "download", "error": r_dl.stderr[:200]})
                print(f"  [{dim}] ❌ download failed: {r_dl.stderr[:100]}", file=sys.stderr)
                continue
        except subprocess.TimeoutExpired:
            errors.append({"dim": dim, "task_id": task_id,
                           "stage": "download", "error": "timeout 120s"})
            print(f"  [{dim}] ⏱️ download timeout", file=sys.stderr)
            continue

        # Step 3: verify + quality gate
        if not out_path.exists():
            errors.append({"dim": dim, "task_id": task_id,
                           "stage": "verify", "error": "file not found"})
            continue
        size = out_path.stat().st_size
        if size < 3000:
            # quality_low: keep file but flag it
            success.append({"dim": dim, "path": str(out_path),
                            "size": size, "quality": "low"})
            print(f"  [{dim}] ⚠️ {size}B (quality_low, <3KB) → {out_path}")
        else:
            success.append({"dim": dim, "path": str(out_path),
                            "size": size, "quality": "ok"})
            print(f"  [{dim}] ✅ {size}B → {out_path}")

    print(f"\n[NLM Phase 6 DOWNLOAD] Complete: {len(success)}/{len(task_ids)} downloaded, {len(errors)} failed")
    quality_low = sum(1 for s in success if s.get("quality") == "low")
    if quality_low:
        print(f"[NLM Phase 6 DOWNLOAD] ⚠️ {quality_low} report(s) quality_low (<3KB); recommend reviewing in Orient phase")

    # Write manifest for caller to aggregate
    out_manifest = output_dir / "nlm-download-manifest.json"
    atomic_write_json(str(out_manifest), {
        "notebook_id": args.notebook_id,
        "success": success,
        "errors": errors,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })
    print(f"[NLM Phase 6 DOWNLOAD] manifest written: {out_manifest}")
    sys.exit(0 if success else 1)


def main():
    parser = argparse.ArgumentParser(
        description="ARGUS NLM Pipeline — 7-phase NotebookLM orchestration (Ironclad Rules #5/#7/#8)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan only, do not execute")
    sub = parser.add_subparsers(dest="phase", help="NLM phase")
    sub.required = True

    # discover
    p_discover = sub.add_parser("discover", help="Phase 1: collect candidate URLs (template)")
    p_discover.add_argument("--output", required=True, help="Output JSON file path")

    # dedupe
    p_dedupe = sub.add_parser("dedupe", help="Phase 2: dedup + 300-source cap check (Ironclad Rule #5)")
    p_dedupe.add_argument("--input", required=True, help="discover output JSON")
    p_dedupe.add_argument("--output", required=True, help="Deduped JSON output path")

    # inject
    p_inject = sub.add_parser("inject", help="Phase 3: inject sources into NLM notebook")
    p_inject.add_argument("--notebook-id", required=True, help="NLM notebook ID")
    p_inject.add_argument("--yt-urls", required=True, help="YouTube URL list file (one per line)")
    p_inject.add_argument("--doc-urls", required=True, help="Doc URL list file (one per line)")
    p_inject.add_argument("--max-sources", type=int, default=300, help="Maximum sources cap (Ironclad Rule #5)")

    # wait
    p_wait = sub.add_parser("wait", help="Phase 4: wait for sources ready (Ironclad Rule #7 gate)")
    p_wait.add_argument("--notebook-id", required=True, help="NLM notebook ID")
    p_wait.add_argument("--poll-interval", type=int, default=30, help="Poll interval in seconds")
    p_wait.add_argument("--timeout", type=int, default=1800, help="Max wait seconds")

    # generate
    p_gen = sub.add_parser("generate", help="Phase 5: batch generate reports (concurrency=3)")
    p_gen.add_argument("--notebook-id", required=True, help="NLM notebook ID")
    p_gen.add_argument("--prompts-file", required=True, help="One prompt per line")
    p_gen.add_argument("--concurrency", type=int, default=3, help="Concurrency (reliable ≤ 3)")
    p_gen.add_argument("--enable-ask", action="store_true", default=False,
                       help="Allow ask mode (disabled by default)")

    # download
    p_dl = sub.add_parser("download", help="Phase 6: download generated results to disk")
    p_dl.add_argument("--notebook-id", required=True, help="NLM notebook ID")
    p_dl.add_argument("--output-dir", required=True, help="Output directory")

    args = parser.parse_args()

    phase_map = {
        "discover": phase_discover,
        "dedupe": phase_dedupe,
        "inject": phase_inject,
        "wait": phase_wait,
        "generate": phase_generate,
        "download": phase_download,
    }
    phase_map[args.phase](args)


if __name__ == "__main__":
    main()
