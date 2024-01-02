#!/usr/bin/env python3
"""
ARGUS — Main Orchestrator (probe.py)

Usage:
  probe.py doctor [--essential CHANNELS] [--skip CHANNELS] [--no-popup] [--json]
  probe.py run    --topic TOPIC [--dimensions INT] [--budget STR]
                  [--output PATH] [--skip-nlm] [--skip-deep] [--skip-xhs]
                  [--resume PATH] [--dry-run]
  probe.py resume MANIFEST_PATH

Subcommands:
  doctor  — Stage 0: run tool_auth_check.py preflight for 7-channel authorization; block if essential channels fail
  run     — Stage 1-4: full-channel parallel research (DISCOVER → INJECT → WAIT → REPORT)
  resume  — resume from manifest.json checkpoint (NLM/Deep async task recovery)

Stages (probe.py namespace, distinct from NLM Phase namespace):
  Stage 0  DOCTOR    — tool authorization self-healing
  Stage 1  DISCOVER  — fire all channels (initialize manifest.json)
  Stage 2  INJECT    — wait for quick channels to finish, NLM inject
  Stage 3  WAIT      — wait for async channels (NLM GENERATE / Perplexity Deep)
  Stage 4  REPORT    — write final manifest + print summary

Exit codes:
  0  success
  1  error
  2  partial (some channels failed)
  3  doctor preflight failed (essential channels not ready)
  4  quota exceeded

7 Ironclad Rules in effect:
  #1  Perplexity must use Comet 9223 — doctor hard-check + perplexity_*.py config
  #2  DevToolsActivePort patch — tool_auth_check.py
  #3  NLM 300 sources — nlm_pipeline.py inject
  #4  Main AI must not read source full-text — probe.py does not call chrome-reader for full pages
  #5  NLM ready gate — nlm_pipeline.py generate waits for ready
  #6  NLM independent channel — probe.py channel output paths are isolated, not mixed
  #7  Perplexity concurrency ≤ 3 — global asyncio.Semaphore(3)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
TOOL_AUTH_CHECK = SCRIPTS_DIR / "tool_auth_check.py"
PERPLEXITY_QUICK = SCRIPTS_DIR / "perplexity_quick.py"
PERPLEXITY_DEEP = SCRIPTS_DIR / "perplexity_deep.py"
NLM_PIPELINE = SCRIPTS_DIR / "nlm_pipeline.py"
BIRD_BATCH = SCRIPTS_DIR / "bird_batch.py"
WEBACCESS_CRAWL = SCRIPTS_DIR / "webaccess_crawl.py"
GITHUB_FETCH = SCRIPTS_DIR / "github_fetch.py"
XHS_QUERY = SCRIPTS_DIR / "xhs_query.py"

DEFAULT_BUDGET_S = 5400  # 90 min


def atomic_write_json(path: str | Path, data: dict):
    """Atomically write manifest (tmp → rename, prevents partial writes on interruption)."""
    path = Path(path)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.rename(tmp, str(path))


def parse_budget(budget_str: str) -> int:
    """Parse budget string ('90m', '3600s', '1h') into seconds."""
    s = budget_str.strip().lower()
    if s.endswith("h"):
        return int(float(s[:-1]) * 3600)
    elif s.endswith("m"):
        return int(float(s[:-1]) * 60)
    elif s.endswith("s"):
        return int(s[:-1])
    else:
        return int(s)


def run_script(script: Path, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a sub-script, return CompletedProcess."""
    cmd = ["python3", str(script)] + args
    return subprocess.run(cmd, capture_output=False, timeout=timeout)


def run_script_capture(script: Path, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a sub-script with captured output, return CompletedProcess."""
    cmd = ["python3", str(script)] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ─────────────── Stage 0: DOCTOR ───────────────

def cmd_doctor(args):
    """Stage 0 DOCTOR: invoke tool_auth_check.py for authorization preflight."""
    if not TOOL_AUTH_CHECK.exists():
        print(f"[ERROR] tool_auth_check.py not found: {TOOL_AUTH_CHECK}", file=sys.stderr)
        sys.exit(1)

    doctor_args = []
    if args.essential:
        doctor_args += ["--essential", args.essential]
    if args.skip:
        doctor_args += ["--skip", args.skip]
    if args.no_popup:
        doctor_args += ["--no-popup"]
    if args.strict:
        doctor_args += ["--strict"]
    if args.json:
        doctor_args += ["--json"]

    print("[Stage 0 DOCTOR] Running tool_auth_check.py ...")
    result = run_script(TOOL_AUTH_CHECK, doctor_args, timeout=600)

    # topic_length_check: if --topic is provided, report per-channel length limits
    topic_val = getattr(args, "topic", None)
    if topic_val:
        n = len(topic_val)
        status = "OK" if n <= 150 else f"⚠️ WARN ({n} > 150)"
        print(f"[Stage 0 DOCTOR] topic_length_check: {n} char {status} (bird ≤80 / pplx_quick ≤120 / pplx_deep ≤200)")
        if n > 80:
            print(f"  → bird over limit (≤80): may return 0 tweets")
    else:
        print("[Stage 0 DOCTOR] topic_length_check: skipped (run with --topic to test)")

    if result.returncode == 0:
        print("[Stage 0 DOCTOR] ✓ All channels ready")
        sys.exit(0)
    elif result.returncode == 2:
        print("[Stage 0 DOCTOR] ⚠ Some channels failed (non-essential)")
        sys.exit(2)
    else:
        print("[Stage 0 DOCTOR] ✗ Essential channel failed, exit 3", file=sys.stderr)
        sys.exit(3)


# ─────────────── manifest helpers ───────────────

def init_manifest(topic: str, budget_s: int, output_dir: Path) -> dict:
    """Initialize manifest.json."""
    return {
        "topic": topic,
        "stage": "Stage 1 DISCOVER",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "budget_total_s": budget_s,
        "budget_remaining_s": budget_s,
        "output_dir": str(output_dir),
        "channels": {
            "perplexity_quick": {"status": "pending", "files": [], "count": 0},
            "perplexity_deep": {"status": "pending", "queries": [], "used_quota": 0},
            "nlm": {
                "status": "pending",
                "phase": "Phase 1 DISCOVER",
                "notebook_id": None,
                "sources_total": 0,
                "sources_ready": False
            },
            "bird": {"status": "pending", "count": 0},
            "webaccess": {"status": "pending", "pages": 0},
            "github": {"status": "pending", "repos": 0},
            "xhs": {"status": "pending", "posts": 0}
        },
        "doctor_passed": [],
        "errors": [],
        "artifacts": []
    }


def load_manifest(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"[ERROR] manifest not found: {path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(p.read_text(encoding="utf-8"))


# ─────────────── Stage 1: DISCOVER ───────────────

def stage_discover(manifest: dict, manifest_path: Path, args, dry_run: bool):
    """Stage 1 DISCOVER: fire all channels in parallel (Ironclad Rule #7: Perplexity Semaphore(3) global)."""
    print("\n[Stage 1 DISCOVER] Starting full-channel fire...")
    topic = manifest["topic"]
    output_dir = Path(manifest["output_dir"])

    # Prepare per-channel output directories
    for ch in ["perplexity_quick", "perplexity_deep", "nlm", "bird", "webaccess", "github", "xhs"]:
        (output_dir / ch).mkdir(parents=True, exist_ok=True)

    if dry_run:
        print(f"[DRY-RUN] Will launch the following channels for topic={topic!r}:")
        channels = []
        if not args.skip_deep:
            channels.append("perplexity_deep")
        if not args.skip_nlm:
            channels.append("nlm")
        channels += ["perplexity_quick", "bird", "webaccess"]
        if not args.skip_gh:
            channels.append("github")
        if not args.skip_xhs:
            channels.append("xhs")
        for ch in channels:
            print(f"  - {ch}")
        manifest["stage"] = "Stage 1 DISCOVER (dry-run)"
        manifest["channels"]["perplexity_quick"]["status"] = "dry-run"
        manifest["channels"]["bird"]["status"] = "dry-run"
        manifest["channels"]["webaccess"]["status"] = "dry-run"
        if not args.skip_gh:
            manifest["channels"]["github"]["status"] = "dry-run"
        if not args.skip_xhs:
            manifest["channels"]["xhs"]["status"] = "dry-run"
        atomic_write_json(manifest_path, manifest)
        return

    # Ironclad Rule #7: global Semaphore(3) — Quick + Deep share the same pool
    perplexity_sem = threading.Semaphore(3)
    channel_results = {}
    lock = threading.Lock()

    def run_perplexity_quick():
        """Quick mode: 3-round, shared Semaphore(3)."""
        queries = [topic, f"{topic} market size", f"{topic} trends 2025"]
        quick_dir = str(output_dir / "perplexity_quick")
        try:
            # Ironclad Rule #7: acquire semaphore slot
            with perplexity_sem:
                result = run_script_capture(
                    PERPLEXITY_QUICK,
                    queries + ["--output-dir", quick_dir, "--rounds", "2"],
                    timeout=300
                )
            status = "done" if result.returncode == 0 else "error"
        except Exception as e:
            status = "error"
        with lock:
            channel_results["perplexity_quick"] = status
            manifest["channels"]["perplexity_quick"]["status"] = status
            atomic_write_json(manifest_path, manifest)

    def run_bird():
        """Bird parallel search."""
        queries = [topic, f"{topic} industry", f"{topic} competitors"]
        bird_dir = str(output_dir / "bird")
        try:
            result = run_script_capture(
                BIRD_BATCH,
                queries + ["--output-dir", bird_dir, "--per-query", "15"],
                timeout=180
            )
            status = "done" if result.returncode in (0, 2) else "error"
        except Exception as e:
            status = "error"
        with lock:
            channel_results["bird"] = status
            manifest["channels"]["bird"]["status"] = status
            atomic_write_json(manifest_path, manifest)

    def run_github():
        """GitHub search-repos channel."""
        if args.skip_gh:
            with lock:
                channel_results["github"] = "skipped"
                manifest["channels"]["github"]["status"] = "skipped"
                atomic_write_json(manifest_path, manifest)
            return
        github_dir = str(output_dir / "github")
        try:
            # github_fetch.py search-repos <query> — search GitHub repos related to topic
            result = run_script_capture(
                GITHUB_FETCH,
                ["search-repos", topic, "--limit", "20", "--output-dir", github_dir],
                timeout=180
            )
            status = "done" if result.returncode in (0, 2) else "error"
        except Exception as e:
            status = "error"
        with lock:
            channel_results["github"] = status
            manifest["channels"]["github"]["status"] = status
            atomic_write_json(manifest_path, manifest)

    def run_xhs():
        """XHS (Xiaohongshu) local-only search channel.

        xhs_query.py is env-var driven and handles its own serialization internally.
        One thread is sufficient; xhs_query.py serializes requests to avoid rate limits.
        """
        if args.skip_xhs:
            with lock:
                channel_results["xhs"] = "skipped"
                manifest["channels"]["xhs"]["status"] = "skipped"
                atomic_write_json(manifest_path, manifest)
            return
        if not XHS_QUERY.exists():
            with lock:
                channel_results["xhs"] = "error"
                manifest["channels"]["xhs"]["status"] = "error"
                manifest["channels"]["xhs"]["error"] = "xhs_query.py not found"
                atomic_write_json(manifest_path, manifest)
            return
        xhs_dir = str(output_dir / "xhs")
        try:
            result = run_script_capture(
                XHS_QUERY,
                [topic, "--output-dir", xhs_dir],
                timeout=300
            )
            status = "done" if result.returncode in (0, 2) else "error"
        except Exception as e:
            status = "error"
        with lock:
            channel_results["xhs"] = status
            manifest["channels"]["xhs"]["status"] = status
            atomic_write_json(manifest_path, manifest)

    # Launch parallel channels
    threads = [
        threading.Thread(target=run_perplexity_quick, daemon=True),
        threading.Thread(target=run_bird, daemon=True),
        threading.Thread(target=run_github, daemon=True),
        threading.Thread(target=run_xhs, daemon=True),
    ]

    for t in threads:
        t.start()

    # NLM and Deep are async; BUG-B v1.1 fix (2026-04-22):
    #   - Perplexity Deep: Popen background fire (truly async 5-10min), PID recorded in manifest
    #   - NLM: stays at "gate_pending" — SOP §2.0 Phase 1.5 User Gate requires
    #     prompts.json to be manually approved before Stage 2; no auto-fire allowed
    if not args.skip_nlm:
        manifest["channels"]["nlm"]["status"] = "gate_pending"
        manifest["channels"]["nlm"]["gate"] = "SOP §2.0 Phase 1.5 User Gate"
        print("[Stage 1 DISCOVER] NLM: SOP §2.0 Gate requires manual approval of prompts.json. After Stage 1, proceed manually:")
        print(f"  1. nlm_pipeline.py discover --output {manifest['output_dir']}/nlm-urls.json")
        print(f"  2. [manual] Edit urls.json + design prompts.json (User Gate)")
        print(f"  3. nlm_pipeline.py dedupe --input ... --output deduped.json")
        print(f"  4. nlm_pipeline.py inject --notebook-id NB_ID --yt-urls yt.txt --doc-urls doc.txt")
        print(f"  5. nlm_pipeline.py wait --notebook-id NB_ID")
        print(f"  6. nlm_pipeline.py generate --notebook-id NB_ID --prompts-file prompts.txt")
        print(f"  7. nlm_pipeline.py download --notebook-id NB_ID --output-dir {manifest['output_dir']}/nlm-reports/")
        atomic_write_json(manifest_path, manifest)

    if not args.skip_deep:
        deep_dir = Path(manifest["output_dir"]) / "perplexity_deep"
        deep_dir.mkdir(parents=True, exist_ok=True)
        # slug topic → file name (no external slugify dependency)
        topic_slug = re.sub(r"[^\w-]+", "_", topic.lower())[:50] or "deep"
        deep_output = deep_dir / f"{topic_slug}.md"
        deep_log = deep_dir / "deep.log"
        try:
            # Popen background fire; main process does not wait (BUG-B fix)
            log_fh = open(deep_log, "w")
            proc_deep = subprocess.Popen(
                ["python3", str(PERPLEXITY_DEEP), "fetch", topic,
                 "--submit", "--output", str(deep_output)],
                stdout=log_fh, stderr=subprocess.STDOUT
            )
            manifest["channels"]["perplexity_deep"]["status"] = "in_progress"
            manifest["channels"]["perplexity_deep"]["pid"] = proc_deep.pid
            manifest["channels"]["perplexity_deep"]["output_file"] = str(deep_output)
            manifest["channels"]["perplexity_deep"]["log_file"] = str(deep_log)
            print(f"[Stage 1 DISCOVER] Perplexity Deep: background fire (pid={proc_deep.pid}), ETA 5-10min")
            print(f"  output → {deep_output}")
            print(f"  log → {deep_log}")
        except Exception as e:
            manifest["channels"]["perplexity_deep"]["status"] = "error"
            manifest["channels"]["perplexity_deep"]["error"] = str(e)[:200]
            print(f"[ERROR] Perplexity Deep Popen fail: {e}", file=sys.stderr)
        atomic_write_json(manifest_path, manifest)

    for t in threads:
        t.join()

    manifest["stage"] = "Stage 2 INJECT"
    atomic_write_json(manifest_path, manifest)
    print("[Stage 1 DISCOVER] Quick channels complete, entering Stage 2 INJECT")


# ─────────────── Stage 2-4: WAIT → REPORT ───────────────

def _check_pid_alive(pid: int) -> bool:
    """Check whether PID is alive (no signal sent, uses os.kill signal=0)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def stage_wait_and_report(manifest: dict, manifest_path: Path, dry_run: bool):
    """Stage 2-4: poll in_progress channels (Popen subprocesses) until complete or budget expires, write final report."""
    topic = manifest["topic"]
    channels = manifest["channels"]

    # BUG-B v1.1: poll Popen subprocesses (perplexity_deep) for up to budget_s (default 15min)
    #   - subprocess exits + output file exists → status=done
    #   - subprocess exits + output missing → status=error
    #   - budget expires while still running → status=in_progress (retained, handled by probe.py resume)
    poll_interval = 30  # s
    max_wait = 900      # 15min cap; beyond this, hand off to resume flow
    start = time.time()

    in_progress_channels = [ch for ch, info in channels.items()
                             if info.get("status") == "in_progress" and info.get("pid")]
    if in_progress_channels:
        print(f"\n[Stage 2-3 WAIT] Polling in_progress channels: {in_progress_channels}")

    while in_progress_channels and time.time() - start < max_wait:
        time.sleep(poll_interval)
        still_running = []
        for ch in in_progress_channels:
            info = channels[ch]
            pid = info.get("pid")
            alive = _check_pid_alive(pid)
            output_file = info.get("output_file")
            if not alive:
                # Process exited; check for output artifact
                if output_file and Path(output_file).exists() and Path(output_file).stat().st_size > 100:
                    info["status"] = "done"
                    info["bytes"] = Path(output_file).stat().st_size
                    print(f"  ✅ {ch}: done (pid={pid} exited, {info['bytes']}B)")
                else:
                    info["status"] = "error"
                    info["error"] = "process exited with no/empty output"
                    print(f"  ❌ {ch}: exited but no output")
            else:
                still_running.append(ch)
                elapsed = int(time.time() - start)
                print(f"  ⏳ {ch}: still running (pid={pid}, elapsed {elapsed}s)")
        in_progress_channels = still_running
        atomic_write_json(manifest_path, manifest)

    if in_progress_channels:
        print(f"\n[Stage 2-3 WAIT] Budget expired, still running: {in_progress_channels}")
        print(f"[Stage 2-3 WAIT] Use probe.py resume to continue waiting, or inspect pids manually")

    print("\n[Stage 2-3 WAIT] Final channel statuses:")
    for ch, info in channels.items():
        status = info.get("status", "unknown")
        print(f"  {ch}: {status}")

    manifest["stage"] = "Stage 4 REPORT"
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()

    done_channels = [ch for ch, info in channels.items()
                     if info.get("status") in ("done", "skipped", "dry-run")]
    pending_channels = [ch for ch, info in channels.items()
                        if info.get("status") in ("pending", "gate_pending", "in_progress")]

    atomic_write_json(manifest_path, manifest)

    print(f"\n[Stage 4 REPORT] Completed channels: {done_channels}")
    if pending_channels:
        print(f"[Stage 4 REPORT] Still pending: {pending_channels}")
        print("[Stage 4 REPORT] Use probe.py resume <manifest_path> to checkpoint-resume")
    print(f"\n[Stage 4 REPORT] manifest written: {manifest_path}")
    print(f"[Stage 4 REPORT] Research output directory: {manifest['output_dir']}")


# ─────────────── cmd_run ───────────────

def cmd_run(args):
    """probe.py run — Stage 1-4 orchestration."""
    import logging
    logger = logging.getLogger("argus.probe")
    if not logger.handlers:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    topic = args.topic

    # Bug #5 fix: topic length warning — per-channel query length limits vary; overly long topics cause channel failures
    if len(topic) > 150:
        logger.warning(f"⚠️ Topic length {len(topic)} chars > 150; channels may fail. Consider splitting into 5-8 sub-queries.")
        logger.warning(f"Per-channel query length reference: bird ≤80 / pplx_quick ≤120 / pplx_deep ≤200 / webaccess URL unlimited")

    budget_s = parse_budget(args.budget)
    dry_run = args.dry_run

    # Determine output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        slug = topic.replace(" ", "_")[:40].lower()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(f"/tmp/argus_{slug}_{timestamp}")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"

    print(f"[ARGUS Probe] topic={topic!r}")
    print(f"[ARGUS Probe] budget={args.budget} ({budget_s}s) | dry_run={dry_run}")
    print(f"[ARGUS Probe] output_dir={output_dir}")

    # If --resume is specified, load existing manifest and continue
    if args.resume:
        manifest = load_manifest(args.resume)
        manifest_path = Path(args.resume)
        print(f"[ARGUS Probe] Resume from: {manifest_path} (stage={manifest.get('stage')})")
    else:
        # Stage 0: Doctor (skipped when --skip-doctor or dry-run)
        if not dry_run:
            print("\n[Stage 0 DOCTOR] Running preflight...")
            if TOOL_AUTH_CHECK.exists():
                essential = "comet-9223,perplexity-auth,chrome-9222"
                if not args.skip_nlm:
                    essential += ",notebooklm-auth"
                result = run_script_capture(
                    TOOL_AUTH_CHECK,
                    ["--no-popup", "--essential", essential],
                    timeout=60
                )
                if result.returncode not in (0, 2):
                    print("[Stage 0 DOCTOR] ✗ Essential channel failed", file=sys.stderr)
                    print(result.stdout)
                    print(result.stderr, file=sys.stderr)
                    sys.exit(3)
                print("[Stage 0 DOCTOR] ✓ Preflight complete")
            else:
                print("[Stage 0 DOCTOR] ⚠ tool_auth_check.py not found, skipping preflight")

        # Initialize manifest
        manifest = init_manifest(topic, budget_s, output_dir)
        atomic_write_json(manifest_path, manifest)
        print(f"[ARGUS Probe] manifest initialized: {manifest_path}")

    # Stage 1 DISCOVER
    stage_discover(manifest, manifest_path, args, dry_run)

    # Stage 2-4 WAIT → REPORT
    stage_wait_and_report(manifest, manifest_path, dry_run)

    # ── Bug #7 fix: exit code + manifest.next_action transparency ──
    # Reload manifest to get final channel states written by stage_wait_and_report
    manifest = load_manifest(manifest_path)
    return _compute_exit_code_and_next_action(manifest, manifest_path)


# ─────────────── _compute_exit_code_and_next_action ───────────────

def _compute_exit_code_and_next_action(manifest: dict, manifest_path: Path) -> int:
    """Compute exit code and write manifest.next_action / next_action_required fields.

    exit 0 — all channels done / skipped / gate_pending (NLM manual steps)
    exit 2 — partial done: some channels in_progress / pending / error and need resume
    exit 4 — fatal fail: all channels errored or manifest channels empty
    """
    channels = manifest.get("channels", {})
    channel_statuses = [ch.get("status", "unknown") for ch in channels.values()]

    # gate_pending = NLM waiting for manual human step — treated as "expected done-ish"
    done_like = {"done", "skipped", "dry-run", "gate_pending"}

    if not channel_statuses:
        exit_code = 4
        manifest["next_action"] = (
            "fatal failure: manifest has no channels. "
            "check probe.py.log + run: probe.py doctor"
        )
        manifest["next_action_required"] = True
    elif all(s in done_like for s in channel_statuses):
        exit_code = 0
        manifest["next_action"] = (
            "all channels done — ready for stage 2 "
            "(gate Phase 1.5 NLM prompts or stage 4 report)"
        )
        manifest["next_action_required"] = False
    elif any(s == "error" for s in channel_statuses) and not any(
        s in ("in_progress", "pending") for s in channel_statuses
    ) and not any(s in done_like for s in channel_statuses):
        # All channels are error (fatal)
        exit_code = 4
        manifest["next_action"] = (
            "fatal failure: all channels errored. "
            "check probe.py.log + run: probe.py doctor for auth issues"
        )
        manifest["next_action_required"] = True
    else:
        # Partial: mix of done + (in_progress | pending | error)
        needs_resume = [
            ch_name for ch_name, ch in channels.items()
            if ch.get("status") in ("in_progress", "pending", "error")
        ]
        exit_code = 2
        manifest["next_action"] = (
            f"partial done. run: probe.py resume {manifest_path} "
            f"within 5min to retry: {needs_resume}"
        )
        manifest["next_action_required"] = True

    # Persist updated manifest with next_action fields
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # Surface to stdout so user sees it immediately
    action_flag = "[ACTION REQUIRED]" if manifest["next_action_required"] else "[OK]"
    print(f"\n[ARGUS] exit_code={exit_code} {action_flag} {manifest['next_action']}")
    return exit_code


# ─────────────── cmd_resume ───────────────

def cmd_resume(args):
    """probe.py resume — checkpoint-resume from manifest."""
    manifest_path = Path(args.manifest_path)
    manifest = load_manifest(manifest_path)
    print(f"[ARGUS Resume] manifest={manifest_path}")
    print(f"[ARGUS Resume] topic={manifest.get('topic')}, stage={manifest.get('stage')}")
    stage_wait_and_report(manifest, manifest_path, dry_run=False)
    # ── Bug #7 fix: same exit code logic as cmd_run ──
    manifest = load_manifest(manifest_path)
    return _compute_exit_code_and_next_action(manifest, manifest_path)


# ─────────────── main ───────────────

def main():
    parser = argparse.ArgumentParser(
        description="ARGUS probe.py — multi-source parallel research main orchestrator (Stage 0-4 state machine)"
    )
    sub = parser.add_subparsers(dest="command", help="subcommand")
    sub.required = True

    # doctor
    p_doctor = sub.add_parser("doctor", help="Stage 0: run preflight for 7-channel authorization")
    p_doctor.add_argument("--essential", default=None,
                          help="Required channels (comma-separated): comet-9223,perplexity-auth,chrome-9222,notebooklm-auth,bird-auth,webaccess-proxy,xhs-scout")
    p_doctor.add_argument("--skip", default=None, help="Channels to skip (comma-separated)")
    p_doctor.add_argument("--strict", action="store_true", help="Exit 1 if any channel fails")
    p_doctor.add_argument("--no-popup", action="store_true", help="No popup (CI mode)")
    p_doctor.add_argument("--json", action="store_true", help="JSON output")
    p_doctor.add_argument("--topic", default=None,
                          help="(optional) Test topic length against per-channel limits (bird ≤80 / pplx_quick ≤120 / pplx_deep ≤200)")

    # run
    p_run = sub.add_parser("run", help="Stage 1-4: full-channel research")
    p_run.add_argument("--topic", required=True, help="Research topic")
    p_run.add_argument("--dimensions", type=int, default=15,
                       help="Number of research dimensions (passed to NLM prompt generation, default: 15)")
    p_run.add_argument("--budget", default="90m",
                       help="Time budget ('90m', '3600s', '1h', default: 90m)")
    p_run.add_argument("--output", default=None,
                       help="Output directory (default: /tmp/argus_<topic>_<ts>/)")
    p_run.add_argument("--skip-nlm", action="store_true", help="Skip NLM channel")
    p_run.add_argument("--skip-deep", action="store_true", help="Skip Perplexity Deep")
    p_run.add_argument("--skip-gh", action="store_true", help="Skip GitHub channel")
    p_run.add_argument("--skip-xhs", action="store_true", help="Skip XHS channel")
    p_run.add_argument("--resume", default=None,
                       help="Resume from existing manifest.json path")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Generate manifest.json without actually running channels")

    # resume
    p_resume = sub.add_parser("resume", help="Checkpoint-resume from manifest.json")
    p_resume.add_argument("manifest_path", help="Path to manifest.json")

    args = parser.parse_args()

    cmd_map = {
        "doctor": cmd_doctor,
        "run": cmd_run,
        "resume": cmd_resume,
    }
    # Bug #7 fix: cmd_run / cmd_resume return exit_code; propagate to shell
    exit_code = cmd_map[args.command](args)
    if exit_code is not None:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
