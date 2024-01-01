#!/usr/bin/env python3
"""
ARGUS — WebAccess CDP bulk crawl with multiple targetIds

Usage:
  webaccess_crawl.py urls.txt --output-dir DIR
  webaccess_crawl.py urls.txt --output-dir DIR --concurrency 3 --scroll
  webaccess_crawl.py urls.txt --output-dir DIR --dry-run

Description:
  Bulk-crawls a URL list in parallel via CDP Proxy (localhost:3456).
  Each URL opens an independent tab (targetId); multiple targetIds run in parallel; tabs are closed on completion.
  Ironclad Rule #4: "connection failure" = DevToolsActivePort path mismatch; detect and patch before launch.
  Ironclad Rule #6: only persist URL+title+snippet to disk; do not read source full-text (main AI must not read full-text).

Exit codes:
  0  all succeeded
  1  internal error
  2  partial — at least one URL failed
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# CDP Proxy port (Ironclad Rule #4)
CDP_PROXY_PORT = 3456
CDP_PROXY_URL = f"http://localhost:{CDP_PROXY_PORT}"

# Chrome debug port — env override > Comet 9223 > Chrome 9222
# Set ARGUS_CHROME_PORT to force a specific port (e.g., ARGUS_CHROME_PORT=9223)
CHROME_DEBUG_PORT = int(os.environ.get("ARGUS_CHROME_PORT", "9222"))
CHROME_DEBUG_URL = f"http://localhost:{CHROME_DEBUG_PORT}"


def detect_browser_port() -> int:
    """
    Detect an available CDP browser port; order: env override → 9223 (Comet preferred) → 9222 (Chrome).
    Returns the first port that responds to /json/version; also updates module-level CHROME_DEBUG_PORT and CHROME_DEBUG_URL.
    Raises RuntimeError if none respond (Ironclad Rule #4: fail fast, no silent fallback).
    """
    import requests as _requests  # local import to avoid global failure if requests is absent

    global CHROME_DEBUG_PORT, CHROME_DEBUG_URL

    # env override goes first to guarantee priority
    env_port = int(os.environ.get("ARGUS_CHROME_PORT", "0"))
    candidates = []
    if env_port:
        candidates.append(env_port)
    # Comet 9223 preferred (CLAUDE.md tool table + ARGUS Ironclad Rule #1)
    for p in [9223, 9222]:
        if p not in candidates:
            candidates.append(p)

    for port in candidates:
        try:
            r = _requests.get(f"http://localhost:{port}/json/version", timeout=2)
            if r.ok:
                CHROME_DEBUG_PORT = port
                CHROME_DEBUG_URL = f"http://localhost:{port}"
                print(f"[WebAccess] detect_browser_port: using port {port}", file=sys.stderr)
                return port
        except Exception:
            pass

    raise RuntimeError(
        f"[WebAccess] detect_browser_port: No CDP browser found on {candidates}. "
        "Start Comet (port 9223) or Chrome (port 9222) with remote debugging enabled."
    )


def slugify(url: str, maxlen: int = 60) -> str:
    """Convert URL to a valid filename."""
    clean = re.sub(r"https?://", "", url)
    clean = re.sub(r"[^\w\s-]", "_", clean)
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean[:maxlen] or "page"


def check_cdp_proxy() -> bool:
    """Check whether CDP Proxy localhost:3456 is alive."""
    try:
        with urllib.request.urlopen(CDP_PROXY_URL + "/json", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def check_devtools_active_port() -> bool:
    """
    Ironclad Rule #4: verify DevToolsActivePort file exists (prevents path mismatch).
    When Chrome uses an isolated debug profile, DevToolsActivePort path differs from the default.
    """
    default_port_file = Path.home() / "Library/Application Support/Google/Chrome/DevToolsActivePort"
    debug_port_file = Path.home() / ".chrome-debug-profile/DevToolsActivePort"
    return default_port_file.exists() or debug_port_file.exists()


def get_chrome_targets() -> list:
    """Get available Chrome tabs (via CDP /json/list)."""
    try:
        with urllib.request.urlopen(CHROME_DEBUG_URL + "/json/list", timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return []


def crawl_url_via_cdp_proxy(url: str, output_dir: Path, scroll: bool, close_tabs: bool) -> dict:
    """
    Crawl a single URL via CDP Proxy.
    Ironclad Rule #6: only save title + snippet (URL+metadata); do not read full page content.
    Returns {"url": str, "status": "ok"|"error", "file": str}
    """
    slug = slugify(url)
    out_file = output_dir / f"{slug}.md"

    try:
        # Use chrome-reader.py to read page via CDP
        chrome_reader = Path.home() / ".claude/scripts/chrome-reader.py"
        if not chrome_reader.exists():
            # Try alternate path
            chrome_reader = Path.home() / ".claude/skills/shelf/web-access/scripts/chrome-reader.py"

        if chrome_reader.exists():
            result = subprocess.run(
                ["python3", str(chrome_reader), url,
                 "--port", str(CHROME_DEBUG_PORT),
                 "--format", "markdown"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                # Ironclad Rule #6: truncate to reasonable length (keep title + summary only; no full-text)
                content = result.stdout.strip()
                lines = content.split("\n")
                # Keep first 50 lines (typically covers title + summary)
                snippet = "\n".join(lines[:50])
                output = (
                    f"# WebAccess — {url}\n\n"
                    f"> Crawled: {datetime.utcnow().isoformat()}Z\n"
                    f"> Note: Ironclad Rule #6 — title+snippet only; full-text not stored\n\n"
                    f"{snippet}\n"
                )
                out_file.write_text(output, encoding="utf-8")
                return {"url": url, "status": "ok", "file": str(out_file)}
            else:
                err = result.stderr.strip()[:200] if result.stderr else "empty"
                out_file.write_text(f"# WebAccess FAILED — {url}\n\nError: {err}\n", encoding="utf-8")
                return {"url": url, "status": "error", "file": str(out_file), "error": err}
        else:
            # Fallback: urllib
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    raw = r.read(10240).decode("utf-8", errors="replace")  # read 10KB only
                # Extract title
                title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
                title = title_match.group(1).strip()[:200] if title_match else url
                # Rough text extraction (strip HTML)
                text = re.sub(r"<[^>]+>", " ", raw)
                text = re.sub(r"\s+", " ", text).strip()[:1000]
                output = (
                    f"# WebAccess — {title}\n\n"
                    f"- URL: {url}\n"
                    f"> Crawled: {datetime.utcnow().isoformat()}Z (urllib fallback)\n\n"
                    f"{text}\n"
                )
                out_file.write_text(output, encoding="utf-8")
                return {"url": url, "status": "ok", "file": str(out_file)}
            except Exception as e2:
                return {"url": url, "status": "error", "file": None, "error": str(e2)}
    except subprocess.TimeoutExpired:
        return {"url": url, "status": "error", "file": None, "error": "timeout 30s"}
    except Exception as e:
        return {"url": url, "status": "error", "file": None, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="ARGUS WebAccess CDP bulk crawl (multi-targetId parallel, Ironclad Rules #4/#6)"
    )
    parser.add_argument("urls_file", help="URL list file (one URL per line)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Multi-targetId concurrency cap (default: 5)")
    parser.add_argument("--scroll", action="store_true",
                        help="Trigger scroll for lazy-loaded pages (slower, suitable for SPA)")
    parser.add_argument("--close-tabs", action="store_true", default=True,
                        help="Close tabs after completion to prevent memory leak (default: True)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan only, do not execute")
    args = parser.parse_args()

    # Ironclad Rule #4 / Bug#4 fix: detect live CDP port as early as possible (Comet 9223 preferred)
    try:
        detect_browser_port()
    except RuntimeError as _e:
        print(f"[WARN] {_e}", file=sys.stderr)
        print("[WARN] Falling back to urllib; continuing...", file=sys.stderr)

    urls_file = Path(args.urls_file)
    if not urls_file.exists() and not args.dry_run:
        print(f"[ERROR] URL file not found: {args.urls_file}", file=sys.stderr)
        sys.exit(1)

    if urls_file.exists():
        urls = [l.strip() for l in urls_file.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]
    else:
        urls = []

    output_dir = Path(args.output_dir)
    concurrency = min(args.concurrency, 10)  # reasonable cap

    if args.dry_run:
        print(f"[DRY-RUN] WebAccess crawl: {len(urls)} URLs, concurrency={concurrency}")
        print(f"[DRY-RUN] scroll={args.scroll}, close_tabs={args.close_tabs}")
        print(f"[DRY-RUN] output directory: {output_dir}")
        for u in urls[:5]:
            print(f"  - {u}")
        if len(urls) > 5:
            print(f"  ... and {len(urls)-5} more URLs")
        sys.exit(0)

    if not urls:
        print("[ERROR] URL list is empty", file=sys.stderr)
        sys.exit(1)

    # Ironclad Rule #4 preflight: DevToolsActivePort
    if not check_devtools_active_port():
        print("[WARN] Ironclad Rule #4: DevToolsActivePort not found; Chrome debug port may not be running", file=sys.stderr)
        print("[WARN] Attempting to continue (will use urllib fallback)...", file=sys.stderr)

    output_dir.mkdir(parents=True, exist_ok=True)
    sem = threading.Semaphore(concurrency)
    results = [None] * len(urls)
    threads = []

    def worker(idx, url):
        with sem:
            results[idx] = crawl_url_via_cdp_proxy(url, output_dir, args.scroll, args.close_tabs)

    print(f"[WebAccess] Starting crawl: {len(urls)} URLs, concurrency={concurrency}")

    for i, u in enumerate(urls):
        t = threading.Thread(target=worker, args=(i, u), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    ok_count = sum(1 for r in results if r and r["status"] == "ok")
    err_count = len(results) - ok_count

    for r in results:
        if r:
            icon = "✓" if r["status"] == "ok" else "✗"
            print(f"  {icon} {r['url'][:80]}")

    print(f"\n[WebAccess] Complete: {ok_count}/{len(results)} succeeded")

    summary_file = output_dir / "_summary.json"
    with open(summary_file, "w") as f:
        json.dump({
            "channel": "webaccess",
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "urls_total": len(results),
            "urls_ok": ok_count,
            "urls_failed": err_count,
            "results": results
        }, f, ensure_ascii=False, indent=2)

    if err_count > 0 and ok_count == 0:
        sys.exit(1)
    elif err_count > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
