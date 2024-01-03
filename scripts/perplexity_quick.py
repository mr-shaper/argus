#!/usr/bin/env python3
"""
ARGUS — Perplexity Quick CDP wrapper (subprocess wrapper → perplexity-reader)

Architecture (policy locked): perplexity-reader = single source of truth.
This file is a subprocess wrapper that internally calls perplexity-reader.py quick-search.
The CLI interface (as called by probe.py) is unchanged.

Usage:
  perplexity_quick.py "query1" "query2" --output-dir DIR
  perplexity_quick.py "query1" --output-dir DIR --rounds 2 --concurrency 3

Exit codes:
  0  all queries succeeded
  1  internal error
  2  partial — at least one query failed (others succeeded)
"""

import argparse
import json
import os
import re
import sys
import threading
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── Ironclad Rule #9: global Semaphore (Quick + Deep share concurrency cap 3) ───
_GLOBAL_SEM = threading.Semaphore(3)

COMET_PORT = int(os.environ.get("COMET_PORT", "9223"))

PERPLEXITY_READER = (
    Path(__file__).parent.parent
    / "sister-skills" / "perplexity-reader" / "scripts" / "perplexity-reader.py"
)


def slugify(text: str, maxlen: int = 50) -> str:
    """Convert a query string to a valid filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug[:maxlen] or "query"


def run_quick_single(query: str, rounds: int, output_dir: Path, sem: threading.Semaphore) -> dict:
    """
    Run Quick search for a single query over the specified number of rounds
    (subprocess → perplexity-reader quick-search).
    Returns {"query": str, "status": "ok"|"error", "file": str, "error": str}
    """
    with sem:
        def log(m):
            print(f"  [Quick] {m}", flush=True)

        log(f"query='{query[:60]}' rounds={rounds}")

        if not PERPLEXITY_READER.exists():
            return {"query": query, "status": "error",
                    "error": f"perplexity-reader.py not found: {PERPLEXITY_READER}"}

        cmd = [
            str(PERPLEXITY_READER), "quick-search",
            query,
            "--output-dir", str(output_dir),
            "--rounds", str(rounds),
        ]

        log(f"subprocess: {' '.join(str(c) for c in cmd[:4])}...")
        result = subprocess.run(cmd, text=True)
        rc = result.returncode

        # Locate the persisted output file (perplexity-reader names it <slug>-<ts>.md)
        pq_dir = output_dir / "perplexity_quick"
        slug = slugify(query)
        candidates = sorted(pq_dir.glob(f"{slug}-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        out_file = str(candidates[0]) if candidates else f"{pq_dir}/{slug}-unknown.md"

        if rc == 0:
            return {"query": query, "status": "ok", "file": out_file, "rounds_ok": rounds}
        elif rc == 2:
            return {"query": query, "status": "ok", "file": out_file,
                    "rounds_ok": "partial", "returncode": rc}
        else:
            return {"query": query, "status": "error", "file": out_file,
                    "error": f"perplexity-reader exit {rc}"}


def main():
    parser = argparse.ArgumentParser(
        description="Perplexity Quick wrapper (subprocess → perplexity-reader quick-search)"
    )
    parser.add_argument("queries", nargs="+", help="One or more search queries")
    parser.add_argument("--output-dir", required=True, help="Root directory for persisted results")
    parser.add_argument("--rounds", type=int, default=3,
                        help="Number of Quick rounds per query (default: 3)")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Concurrency cap (Ironclad Rule #9: Quick+Deep shared, max 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the execution plan only, do not run")
    args = parser.parse_args()

    # Enforce concurrency cap <= 3 (Ironclad Rule #9)
    concurrency = min(args.concurrency, 3)
    if args.concurrency > 3:
        print(f"[WARN] --concurrency {args.concurrency} exceeds Ironclad Rule #9 cap; clamped to 3", file=sys.stderr)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print(f"[DRY-RUN] Will run {args.rounds} Quick rounds for each of {len(args.queries)} queries")
        print(f"[DRY-RUN] concurrency: {concurrency} | output root: {output_dir}")
        for q in args.queries:
            print(f"  - {q!r} → {output_dir}/perplexity_quick/{slugify(q)}-<ts>.md")
        sys.exit(0)

    sem = threading.Semaphore(concurrency)
    results = [None] * len(args.queries)
    threads = []

    def worker(idx, query):
        results[idx] = run_quick_single(query, args.rounds, output_dir, sem)

    print(f"[Perplexity Quick] Starting {len(args.queries)} queries, "
          f"concurrency={concurrency}, rounds={args.rounds}")

    for i, q in enumerate(args.queries):
        t = threading.Thread(target=worker, args=(i, q), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Summarize results
    ok_count = sum(1 for r in results if r and r["status"] == "ok")
    err_count = len(results) - ok_count

    for r in results:
        if r:
            status_icon = "✓" if r["status"] == "ok" else "✗"
            print(f"  {status_icon} {r['query']!r} → {r.get('file', 'N/A')}")

    print(f"\n[Perplexity Quick] Done: {ok_count}/{len(results)} succeeded")

    # Write summary JSON (compatible with probe.py expected output_dir/_summary.json)
    summary_file = output_dir / "_summary.json"
    with open(summary_file, "w") as f:
        json.dump({
            "channel": "perplexity_quick",
            "version": "3.0-subprocess",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "queries_total": len(results),
            "queries_ok": ok_count,
            "queries_failed": err_count,
            "results": results
        }, f, ensure_ascii=False, indent=2)

    if err_count > 0 and ok_count > 0:
        sys.exit(2)  # partial
    elif err_count > 0:
        sys.exit(1)  # all failed
    sys.exit(0)


if __name__ == "__main__":
    main()
