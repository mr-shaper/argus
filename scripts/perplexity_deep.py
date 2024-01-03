#!/usr/bin/env python3
"""
ARGUS — Perplexity Deep Research CLI (subprocess wrapper → perplexity-reader)

Architecture (policy locked): perplexity-reader = single source of truth.
This file is a subprocess wrapper that internally calls perplexity-reader.py deep-search.
The CLI interface (as called by probe.py) is unchanged.

Usage:
  perplexity_deep.py check                              # preflight Comet 9223 + login
  perplexity_deep.py fetch "<query>"                    # default --dry-run (stops at query fill)
  perplexity_deep.py fetch "<query>" --submit           # real submit (consumes 1/100 quota)
  perplexity_deep.py fetch "<query>" --submit --output r.md --timeout 900
  perplexity_deep.py fetch "<query>" --submit --wait-stable-s 10 --keep-tab

Exit codes: 0 OK / 1 error / 2 timeout / 3 auth fail / 4 dry-run stopped / 5 budget exhausted

Anti-ban policy: ARGUS global Perplexity concurrency <= 3 (Quick + Deep share Semaphore(3))
  - One Deep Research call occupies 1 slot; 2 slots remain available for Quick
  - The caller must hold the semaphore externally; this script has no built-in semaphore
    (keeps ARGUS global scheduling unified)
"""
import argparse
import json
import os
import sys
import subprocess
import time
import urllib.request
from pathlib import Path
from datetime import datetime

COMET_PORT = int(os.environ.get("COMET_PORT", "9223"))
COMET = f"http://localhost:{COMET_PORT}"

PERPLEXITY_READER = (
    Path(__file__).parent.parent
    / "sister-skills" / "perplexity-reader" / "scripts" / "perplexity-reader.py"
)


def comet_alive():
    try:
        urllib.request.urlopen(f"{COMET}/json/version", timeout=2).read()
        return True
    except Exception:
        return False


def cmd_check(args):
    print("=== ARGUS Perplexity Deep Research Preflight ===")
    if not comet_alive():
        print(f"❌ Comet {COMET_PORT} unreachable")
        print(f"   Run: comet-debug (~/.zshrc alias)")
        return 3
    print(f"✅ Comet {COMET_PORT} alive")
    print("ℹ️  For login session check, run perplexity-reader.py check directly")
    return 0


def cmd_fetch(args):
    def log(m):
        print(m, flush=True)

    if not comet_alive():
        print("Comet not online; perplexity-reader will attempt auto-start...", flush=True)

    # Budget preflight (if manifest provided)
    if args.budget_file:
        try:
            bm = json.loads(Path(args.budget_file).read_text())
            used = bm.get("deep_research_used_today", 0)
            quota = args.daily_quota
            if used + 1 > quota:
                print(f"❌ Budget exceeded: used {used}/{quota}", file=sys.stderr)
                return 5
            log(f"📊 Budget: {used}/{quota} used today (this call will add +1)")
        except FileNotFoundError:
            pass

    if not PERPLEXITY_READER.exists():
        print(f"❌ perplexity-reader.py not found: {PERPLEXITY_READER}", file=sys.stderr)
        return 1

    # Build the perplexity-reader.py deep-search command
    cmd = [
        str(PERPLEXITY_READER), "deep-search",
        args.query,
        "--output", str(args.output),
        "--timeout", str(args.timeout),
        "--wait-stable-s", str(args.wait_stable_s),
        "--poll-interval", str(args.poll_interval),
    ]
    if args.submit:
        cmd.append("--submit")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.keep_tab:
        cmd.append("--keep-tab")

    log(f"[ARGUS perplexity_deep] → subprocess: {' '.join(str(c) for c in cmd[:5])}...")
    result = subprocess.run(cmd, text=True)
    rc = result.returncode

    # Accumulate budget counter (only on real success)
    if rc == 0 and args.budget_file:
        try:
            bm = json.loads(Path(args.budget_file).read_text()) if Path(args.budget_file).exists() else {}
            today = datetime.now().strftime("%Y-%m-%d")
            if bm.get("date") != today:
                bm = {"date": today, "deep_research_used_today": 0}
            bm["deep_research_used_today"] = bm.get("deep_research_used_today", 0) + 1
            Path(args.budget_file).write_text(json.dumps(bm, indent=2))
            log(f"📊 Budget updated: {bm['deep_research_used_today']}/{args.daily_quota}")
        except Exception as e:
            log(f"⚠️ Budget write failed: {e}")

    return rc


def main():
    parser = argparse.ArgumentParser(
        description="ARGUS Perplexity Deep Research CLI (subprocess wrapper → perplexity-reader)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    # check
    sub.add_parser("check", help="Preflight Comet + login session")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Run a Deep Research query (default: dry-run)")
    p_fetch.add_argument("query", help="Research query (recommended 50-300 chars with framework instructions)")
    p_fetch.add_argument("--output", "-o", default="./deep_research_result.md", help="Output markdown path")
    mode_group = p_fetch.add_mutually_exclusive_group()
    mode_group.add_argument("--submit", action="store_true", help="Real submit (consumes 1/100 quota)")
    mode_group.add_argument("--dry-run", action="store_true", default=True,
                            help="Fill query and stop without submitting (default, zero quota cost)")
    p_fetch.add_argument("--timeout", type=int, default=900, help="Poll timeout in seconds (default: 15min)")
    p_fetch.add_argument("--wait-stable-s", type=int, default=10, help="Seconds answer must stay unchanged to be considered complete (default: 10)")
    p_fetch.add_argument("--poll-interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    p_fetch.add_argument("--keep-tab", action="store_true", help="Keep browser tab open for manual review after completion")
    p_fetch.add_argument("--budget-file", default=None, help="Budget JSON path (tracks daily cumulative usage)")
    p_fetch.add_argument("--daily-quota", type=int, default=80, help="Daily quota cap (default: 80, reserves 20 for user)")

    args = parser.parse_args()

    # --submit flips dry_run off
    if args.cmd == "fetch" and args.submit:
        args.dry_run = False

    if args.cmd == "check":
        sys.exit(cmd_check(args))
    elif args.cmd == "fetch":
        if len(args.query) < 15:
            print("⚠️ query too short (< 15 chars); Deep Research results will be poor", file=sys.stderr)
        sys.exit(cmd_fetch(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
