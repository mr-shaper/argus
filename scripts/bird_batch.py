#!/usr/bin/env python3
"""
ARGUS — Bird batch parallel search wrapper

Usage:
  bird_batch.py "query1" "query2" --output-dir DIR
  bird_batch.py "query1" --output-dir DIR --per-query 20 --concurrency 3
  bird_batch.py "query1" --output-dir DIR --dry-run

Description:
  Runs bird CLI (BIRD skill) in parallel for multiple queries, persisting N results
  per query to disk. A single failing query is skipped by default (--skip-fail)
  without interrupting other queries. Concurrency cap is 5 (ARGUS standard).

Exit codes:
  0  all succeeded
  1  internal error
  2  partial — at least one query failed, others succeeded
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

# Bird CLI path (bird skill)
# v1.1 (2026-04-22) fix: bird 0.8.0+ is a Node.js CLI (/opt/homebrew/bin/bird),
# not a Python script. find_bird_bin() prefers `bird` on PATH; build_bird_cmd()
# decides whether to prepend python3 based on the binary type (BUG-C fix).
BIRD_BIN_PY_LEGACY = Path.home() / ".claude/skills/shelf/bird/scripts/bird.py"


def slugify(text: str, maxlen: int = 50) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug[:maxlen] or "query"


def find_bird_bin() -> str:
    """Find the bird CLI. Prefers `bird` on PATH (official Node.js CLI); falls back to legacy .py."""
    # Preferred: bird on PATH (Node.js CLI, official install at /opt/homebrew/bin/bird)
    result = subprocess.run(["which", "bird"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    # Legacy fallback: Python bird.py
    if BIRD_BIN_PY_LEGACY.exists():
        return str(BIRD_BIN_PY_LEGACY)
    for candidate in [
        Path.home() / ".claude/skills/shelf/bird/bird.py",
        Path.home() / ".claude/scripts/bird.py",
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def build_bird_cmd(bird_bin: str, *args) -> list:
    """Build the subprocess command based on bird_bin type (Node script direct; .py gets python3 prefix)."""
    if bird_bin.endswith(".py"):
        return ["python3", bird_bin] + list(args)
    return [bird_bin] + list(args)


def run_bird_query(query: str, per_query: int, output_dir: Path,
                   bird_bin: str, sem: threading.Semaphore, skip_fail: bool) -> dict:
    """
    Run a Bird search for a single query.
    Returns {"query": str, "status": "ok"|"error", "file": str, "count": int}

    v1.1 fix: uses the real bird CLI flag -n (not --limit); removes --format markdown
    (bird 0.8.0 default text output is already human-readable).
    """
    slug = slugify(query)
    out_file = output_dir / f"{slug}.md"

    with sem:
        try:
            result = subprocess.run(
                build_bird_cmd(bird_bin, "search", query, "-n", str(per_query)),
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0 and result.stdout.strip():
                content = (
                    f"# Bird Search — {query}\n\n"
                    f"> Generated: {datetime.utcnow().isoformat()}Z | "
                    f"per_query={per_query}\n\n"
                    f"{result.stdout.strip()}\n"
                )
                out_file.write_text(content, encoding="utf-8")
                lines = result.stdout.strip().split("\n")
                # Simple result count estimate (each result typically starts with ##)
                count = sum(1 for l in lines if l.startswith("##"))
                return {"query": query, "status": "ok", "file": str(out_file), "count": max(count, 1)}
            else:
                err = result.stderr.strip()[:300] if result.stderr else "empty output"
                if not skip_fail:
                    return {"query": query, "status": "error", "file": None, "error": err}
                # skip_fail=True: write error file and mark as skipped
                out_file.write_text(f"# Bird Search FAILED — {query}\n\nError: {err}\n", encoding="utf-8")
                return {"query": query, "status": "skipped", "file": str(out_file), "error": err}
        except subprocess.TimeoutExpired:
            if skip_fail:
                return {"query": query, "status": "skipped", "file": None, "error": "timeout 60s"}
            return {"query": query, "status": "error", "file": None, "error": "timeout 60s"}
        except Exception as e:
            if skip_fail:
                return {"query": query, "status": "skipped", "file": None, "error": str(e)}
            return {"query": query, "status": "error", "file": None, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="ARGUS Bird batch parallel search (concurrency<=5)"
    )
    parser.add_argument("queries", nargs="+", help="One or more search queries")
    parser.add_argument("--output-dir", required=True, help="Output directory for persisted results")
    parser.add_argument("--per-query", type=int, default=15, help="Results to fetch per query (default: 15)")
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="Concurrency cap (default: 5)"
    )
    parser.add_argument(
        "--skip-fail", action="store_true", default=True,
        help="Skip failed queries and continue others (default: True)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print execution plan only, do not run")
    args = parser.parse_args()

    concurrency = min(args.concurrency, 5)
    output_dir = Path(args.output_dir)

    if args.dry_run:
        print(f"[DRY-RUN] Bird batch: {len(args.queries)} queries, concurrency={concurrency}, per_query={args.per_query}")
        print(f"[DRY-RUN] output directory: {output_dir}")
        for q in args.queries:
            print(f"  - {q!r} → {output_dir / (slugify(q) + '.md')}")
        sys.exit(0)

    bird_bin = find_bird_bin()
    if not bird_bin:
        print("[ERROR] bird CLI not found", file=sys.stderr)
        print("[ERROR] Verify bird skill is installed: ls ~/.claude/skills/shelf/bird/", file=sys.stderr)
        sys.exit(1)

    print(f"[Bird Batch] bird_bin={bird_bin}")

    output_dir.mkdir(parents=True, exist_ok=True)
    sem = threading.Semaphore(concurrency)
    results = [None] * len(args.queries)
    threads = []

    def worker(idx, query):
        results[idx] = run_bird_query(
            query, args.per_query, output_dir, bird_bin, sem, args.skip_fail
        )

    print(f"[Bird Batch] Starting {len(args.queries)} queries, concurrency={concurrency}")

    for i, q in enumerate(args.queries):
        t = threading.Thread(target=worker, args=(i, q), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    ok_count = sum(1 for r in results if r and r["status"] == "ok")
    skip_count = sum(1 for r in results if r and r["status"] == "skipped")
    err_count = sum(1 for r in results if r and r["status"] == "error")

    for r in results:
        if r:
            icon = {"ok": "✓", "skipped": "⚠", "error": "✗"}.get(r["status"], "?")
            extra = f"({r.get('count', 0)} hits)" if r["status"] == "ok" else f"({r.get('error', '')})"
            print(f"  {icon} {r['query']!r} {extra}")

    print(f"\n[Bird Batch] Done: {ok_count} succeeded / {skip_count} skipped / {err_count} failed")

    # Write summary
    summary_file = output_dir / "_summary.json"
    with open(summary_file, "w") as f:
        json.dump({
            "channel": "bird",
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "queries_total": len(results),
            "queries_ok": ok_count,
            "queries_skipped": skip_count,
            "queries_failed": err_count,
            "results": results
        }, f, ensure_ascii=False, indent=2)

    if err_count > 0 and ok_count == 0:
        sys.exit(1)
    elif err_count > 0 or skip_count > 0:
        sys.exit(2)  # partial
    sys.exit(0)


if __name__ == "__main__":
    main()
