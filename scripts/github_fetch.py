#!/usr/bin/env python3
"""
ARGUS — GitHub data collection wrapper (v1.1)

Usage:
  github_fetch.py repo <owner/repo> [--output-dir DIR] [--dry-run] [--to-nlm]
  github_fetch.py trending [--lang LANG] [--window daily|weekly|monthly] [--limit N] [--output-dir DIR] [--dry-run] [--to-nlm]
  github_fetch.py issues <owner/repo> [--state open|closed|all] [--limit N] [--output-dir DIR] [--dry-run]
  github_fetch.py release <owner/repo> [--output-dir DIR] [--dry-run] [--to-nlm]
  github_fetch.py search-repos <query> [--limit N] [--output-dir DIR] [--dry-run] [--to-nlm]

Description:
  Uses subprocess to call `gh` CLI and collect GitHub data to disk.
  Rate-limit: detects "rate limit" or 403 → exponential backoff (60s→120s→240s), max 3 retries.
  --to-nlm: appends README URL to {output_dir}/nlm_queue.json (cap 30).

Exit codes:
  0  success
  1  error (gh CLI unauthorized, network failure, etc.)
  2  partial failure (partial results)
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string (timezone-aware, no deprecation)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# ─── Rate-limit retry parameters ─────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAYS = [60, 120, 240]   # exponential backoff in seconds


# ─── Utility functions ──────────────────────────────────────────────────────────────

def check_gh_auth() -> bool:
    """Check gh auth status; print instructions and return False if unauthorized."""
    result = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("[ERROR] gh not authorized; please run: gh auth login", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return False
    return True


def run_gh(args: list, timeout: int = 60) -> subprocess.CompletedProcess:
    """
    Execute a gh CLI command with rate-limit retry.
    Detects "rate limit" or returncode==403 → exponential backoff, max 3 retries.
    """
    last_result = None
    for attempt in range(MAX_RETRIES + 1):
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True, timeout=timeout
        )
        last_result = result

        # Success
        if result.returncode == 0:
            return result

        stderr_lower = result.stderr.lower()
        # Rate-limit detection
        is_rate_limited = (
            "rate limit" in stderr_lower
            or "rate_limit" in stderr_lower
            or result.returncode == 403
            or "secondary rate limit" in stderr_lower
            or "api rate limit exceeded" in stderr_lower
        )

        if is_rate_limited and attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[attempt]
            print(
                f"[GitHub] Rate-limit detected, waiting {delay}s before retry "
                f"({attempt + 1}/{MAX_RETRIES})...",
                file=sys.stderr
            )
            time.sleep(delay)
            continue

        # Other error or max retries reached → return last result
        return result

    return last_result


def append_to_nlm_queue(output_dir: Path, url: str, cap: int = 30) -> None:
    """Append URL to nlm_queue.json; trim to cap if exceeded."""
    queue_file = output_dir / "nlm_queue.json"
    queue = []
    if queue_file.exists():
        try:
            queue = json.loads(queue_file.read_text(encoding="utf-8"))
            if not isinstance(queue, list):
                queue = []
        except Exception:
            queue = []

    if url not in queue:
        queue.append(url)
    if len(queue) > cap:
        queue = queue[-cap:]

    queue_file.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[GitHub] nlm_queue.json updated: {len(queue)} URLs (cap {cap})")


def write_json(path: Path, data: dict | list) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ─── Subcommand: repo ─────────────────────────────────────────────────────────

def cmd_repo(args) -> int:
    """
    Collect repo metadata + README.
    Output: {output_dir}/repo_{owner}_{repo}.json + README.md
    """
    repo = args.repo
    output_dir = Path(args.output_dir)
    safe_name = repo.replace("/", "_")

    if args.dry_run:
        print(f"[DRY-RUN] repo {repo}")
        print(f"  → {output_dir}/repo_{safe_name}.json")
        print(f"  → {output_dir}/repo_{safe_name}_readme.md")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. repo metadata
    print(f"[GitHub] Fetching repo metadata: {repo}")
    json_fields = ",".join([
        "name", "nameWithOwner", "description", "url", "homepageUrl",
        "stargazerCount", "forkCount", "watchers", "primaryLanguage",
        "repositoryTopics", "latestRelease", "pushedAt", "createdAt",
        "updatedAt", "isArchived", "isFork", "visibility", "licenseInfo"
    ])
    result = run_gh(["repo", "view", repo, "--json", json_fields])
    if result.returncode != 0:
        print(f"[ERROR] gh repo view failed: {result.stderr.strip()}", file=sys.stderr)
        return 1

    meta = json.loads(result.stdout)
    meta["_fetched_at"] = _now_iso()
    meta["_channel"] = "github"

    meta_file = output_dir / f"repo_{safe_name}.json"
    write_json(meta_file, meta)
    print(f"  ✓ metadata → {meta_file}")

    # 2. README (via gh api, base64 decode)
    print(f"[GitHub] Fetching README: {repo}")
    readme_result = run_gh(["api", f"repos/{repo}/readme", "--jq", ".content"])
    if readme_result.returncode == 0 and readme_result.stdout.strip():
        try:
            # content is base64; may contain newlines that need to be stripped
            b64 = readme_result.stdout.strip().replace("\n", "")
            readme_text = base64.b64decode(b64).decode("utf-8", errors="replace")
            readme_file = output_dir / f"repo_{safe_name}_readme.md"
            write_md(readme_file, readme_text)
            print(f"  ✓ README ({len(readme_text)} chars) → {readme_file}")

            # --to-nlm: append README URL
            if getattr(args, "to_nlm", False):
                readme_url = f"https://github.com/{repo}/blob/HEAD/README.md"
                append_to_nlm_queue(output_dir, readme_url)
        except Exception as e:
            print(f"  ⚠ README decode failed: {e}", file=sys.stderr)
    else:
        print(f"  ⚠ README not found (may not exist)", file=sys.stderr)

    print(f"\n[GitHub] repo done: {repo}")
    return 0


# ─── Subcommand: trending ─────────────────────────────────────────────────────

def cmd_trending(args) -> int:
    """
    Simulate GitHub Trending: gh search repos --sort stars --updated ">DATE" [--language LANG]
    Output: {output_dir}/trending_{lang}_{window}.json + .md
    Note: gh has no native trending command; this approximates it via search repos.
    """
    output_dir = Path(args.output_dir)
    lang = getattr(args, "lang", None) or "all"
    window = getattr(args, "window", "daily") or "daily"
    limit = getattr(args, "limit", 25) or 25

    # Compute updated date threshold
    window_days = {"daily": 1, "weekly": 7, "monthly": 30}
    days = window_days.get(window, 1)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    safe_lang = lang.lower().replace(" ", "_").replace("+", "plus")
    out_base = f"trending_{safe_lang}_{window}"

    if args.dry_run:
        print(f"[DRY-RUN] trending lang={lang} window={window} limit={limit}")
        print(f"  cutoff date: >={cutoff}")
        print(f"  → {output_dir}/{out_base}.json")
        print(f"  → {output_dir}/{out_base}.md")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    # Build gh search repos command
    gh_args = [
        "search", "repos",
        "--sort", "stars",
        "--order", "desc",
        "--updated", f">={cutoff}",
        "--limit", str(min(limit, 100)),
        "--json", "fullName,description,stargazersCount,forksCount,language,url,pushedAt,updatedAt,openIssuesCount"
    ]
    if lang and lang.lower() != "all":
        gh_args += ["--language", lang]

    print(f"[GitHub] Fetching trending: lang={lang} window={window} limit={limit} cutoff>={cutoff}")
    result = run_gh(gh_args, timeout=60)

    if result.returncode != 0:
        print(f"[ERROR] gh search repos failed: {result.stderr.strip()}", file=sys.stderr)
        return 1

    repos = json.loads(result.stdout)
    data = {
        "_channel": "github",
        "_type": "trending",
        "_fetched_at": _now_iso(),
        "_params": {"lang": lang, "window": window, "limit": limit, "cutoff": cutoff},
        "count": len(repos),
        "repos": repos
    }

    json_file = output_dir / f"{out_base}.json"
    write_json(json_file, data)
    print(f"  ✓ {len(repos)} repos → {json_file}")

    # Markdown version
    md_lines = [
        f"# GitHub Trending — {lang} ({window})",
        f"> Fetched: {data['_fetched_at']} | Cutoff: >={cutoff} | Count: {len(repos)}",
        ""
    ]
    for i, r in enumerate(repos, 1):
        desc = r.get("description") or ""
        stars = r.get("stargazersCount", 0)
        forks = r.get("forksCount", 0)
        rl = r.get("language") or ""
        md_lines.append(f"## {i}. [{r['fullName']}]({r['url']})")
        md_lines.append(f"**{stars:,} ⭐ | {forks:,} forks | {rl}**")
        if desc:
            md_lines.append(f"\n{desc}")
        md_lines.append("")

    md_file = output_dir / f"{out_base}.md"
    write_md(md_file, "\n".join(md_lines))
    print(f"  ✓ Markdown → {md_file}")

    # --to-nlm: append top-10 repo README URLs
    if getattr(args, "to_nlm", False):
        for r in repos[:10]:  # limit to top 10 to avoid queue overflow
            fn = r.get("fullName", "")
            if fn:
                url = f"https://github.com/{fn}/blob/HEAD/README.md"
                append_to_nlm_queue(output_dir, url)

    print(f"\n[GitHub] trending done: {len(repos)} repos")
    return 0


# ─── Subcommand: issues ───────────────────────────────────────────────────────

def cmd_issues(args) -> int:
    """
    Collect repo issues.
    Output: {output_dir}/issues_{owner}_{repo}_{state}.json + .md
    """
    repo = args.repo
    state = getattr(args, "state", "open") or "open"
    limit = getattr(args, "limit", 30) or 30
    output_dir = Path(args.output_dir)
    safe_name = repo.replace("/", "_")

    if args.dry_run:
        print(f"[DRY-RUN] issues {repo} state={state} limit={limit}")
        print(f"  → {output_dir}/issues_{safe_name}_{state}.json")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[GitHub] Fetching issues: {repo} state={state} limit={limit}")
    result = run_gh([
        "issue", "list",
        "--repo", repo,
        "--state", state,
        "--limit", str(min(limit, 100)),
        "--json", "number,title,state,author,createdAt,updatedAt,url,labels,body"
    ], timeout=60)

    if result.returncode != 0:
        print(f"[ERROR] gh issue list failed: {result.stderr.strip()}", file=sys.stderr)
        return 1

    issues = json.loads(result.stdout)
    data = {
        "_channel": "github",
        "_type": "issues",
        "_fetched_at": _now_iso(),
        "_params": {"repo": repo, "state": state, "limit": limit},
        "count": len(issues),
        "issues": issues
    }

    json_file = output_dir / f"issues_{safe_name}_{state}.json"
    write_json(json_file, data)
    print(f"  ✓ {len(issues)} issues → {json_file}")

    # Markdown version
    md_lines = [
        f"# GitHub Issues — {repo} ({state})",
        f"> Fetched: {data['_fetched_at']} | Count: {len(issues)}",
        ""
    ]
    for iss in issues:
        labels = ", ".join(lb.get("name", "") for lb in iss.get("labels", []))
        author = (iss.get("author") or {}).get("login", "unknown")
        md_lines.append(f"## #{iss['number']}: {iss['title']}")
        md_lines.append(f"**State**: {iss['state']} | **Author**: {author} | **Labels**: {labels or 'none'}")
        md_lines.append(f"**URL**: {iss['url']}")
        body = (iss.get("body") or "").strip()
        if body:
            # Truncate overly long bodies
            preview = body[:500] + ("..." if len(body) > 500 else "")
            md_lines.append(f"\n{preview}")
        md_lines.append("")

    md_file = output_dir / f"issues_{safe_name}_{state}.md"
    write_md(md_file, "\n".join(md_lines))
    print(f"  ✓ Markdown → {md_file}")

    print(f"\n[GitHub] issues done: {len(issues)} issues")
    return 0


# ─── Subcommand: release ─────────────────────────────────────────────────────

def cmd_release(args) -> int:
    """
    Collect latest release information.
    Output: {output_dir}/release_{owner}_{repo}.json + .md
    """
    repo = args.repo
    output_dir = Path(args.output_dir)
    safe_name = repo.replace("/", "_")

    if args.dry_run:
        print(f"[DRY-RUN] release {repo}")
        print(f"  → {output_dir}/release_{safe_name}.json")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[GitHub] Fetching latest release: {repo}")
    result = run_gh([
        "release", "view",
        "--repo", repo,
        "--json", "tagName,name,publishedAt,body,url,isDraft,isPrerelease,assets"
    ], timeout=30)

    if result.returncode != 0:
        print(f"[ERROR] gh release view failed: {result.stderr.strip()}", file=sys.stderr)
        # Try release list as fallback
        print("[GitHub] Trying release list as fallback...", file=sys.stderr)
        list_result = run_gh([
            "release", "list",
            "--repo", repo,
            "--limit", "5",
            "--json", "tagName,name,publishedAt,isDraft,isPrerelease"
        ], timeout=30)
        if list_result.returncode == 0 and list_result.stdout.strip():
            releases = json.loads(list_result.stdout)
            data = {
                "_channel": "github",
                "_type": "release_list",
                "_fetched_at": _now_iso(),
                "_params": {"repo": repo},
                "releases": releases
            }
            json_file = output_dir / f"release_{safe_name}.json"
            write_json(json_file, data)
            print(f"  ✓ {len(releases)} releases (list fallback) → {json_file}")
            return 0
        return 1

    release = json.loads(result.stdout)
    data = {
        "_channel": "github",
        "_type": "release",
        "_fetched_at": _now_iso(),
        "_params": {"repo": repo},
        **release
    }

    json_file = output_dir / f"release_{safe_name}.json"
    write_json(json_file, data)
    print(f"  ✓ release {release.get('tagName', 'unknown')} → {json_file}")

    # Markdown version
    md_lines = [
        f"# GitHub Release — {repo}",
        f"> Tag: {release.get('tagName')} | Published: {release.get('publishedAt')}",
        f"> URL: {release.get('url')}",
        "",
        release.get("body", "").strip(),
        ""
    ]
    md_file = output_dir / f"release_{safe_name}.md"
    write_md(md_file, "\n".join(md_lines))
    print(f"  ✓ Markdown → {md_file}")

    # --to-nlm
    if getattr(args, "to_nlm", False):
        release_url = release.get("url", "")
        if release_url:
            append_to_nlm_queue(output_dir, release_url)

    print(f"\n[GitHub] release done: {repo}")
    return 0


# ─── Subcommand: search-repos ────────────────────────────────────────────────

def cmd_search_repos(args) -> int:
    """
    General repo search.
    Output: {output_dir}/search_repos_{slug}.json + .md
    """
    query = args.query
    limit = getattr(args, "limit", 30) or 30
    output_dir = Path(args.output_dir)
    # Generate safe filename
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in query)[:40]

    if args.dry_run:
        print(f"[DRY-RUN] search-repos {query!r} limit={limit}")
        print(f"  → {output_dir}/search_repos_{slug}.json")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[GitHub] Searching repos: {query!r} limit={limit}")
    result = run_gh([
        "search", "repos",
        query,
        "--limit", str(min(limit, 100)),
        "--sort", "stars",
        "--order", "desc",
        "--json", "fullName,description,stargazersCount,forksCount,language,url,pushedAt,updatedAt"
    ], timeout=60)

    if result.returncode != 0:
        print(f"[ERROR] gh search repos failed: {result.stderr.strip()}", file=sys.stderr)
        return 1

    repos = json.loads(result.stdout)
    data = {
        "_channel": "github",
        "_type": "search_repos",
        "_fetched_at": _now_iso(),
        "_params": {"query": query, "limit": limit},
        "count": len(repos),
        "repos": repos
    }

    json_file = output_dir / f"search_repos_{slug}.json"
    write_json(json_file, data)
    print(f"  ✓ {len(repos)} repos → {json_file}")

    # Markdown version
    md_lines = [
        f"# GitHub Search Repos — {query!r}",
        f"> Fetched: {data['_fetched_at']} | Count: {len(repos)}",
        ""
    ]
    for i, r in enumerate(repos, 1):
        desc = r.get("description") or ""
        stars = r.get("stargazersCount", 0)
        forks = r.get("forksCount", 0)
        rl = r.get("language") or ""
        md_lines.append(f"## {i}. [{r['fullName']}]({r['url']})")
        md_lines.append(f"**{stars:,} ⭐ | {forks:,} forks | {rl}**")
        if desc:
            md_lines.append(f"\n{desc}")
        md_lines.append("")

    md_file = output_dir / f"search_repos_{slug}.md"
    write_md(md_file, "\n".join(md_lines))
    print(f"  ✓ Markdown → {md_file}")

    # --to-nlm: append top-10 repo README URLs
    if getattr(args, "to_nlm", False):
        for r in repos[:10]:
            fn = r.get("fullName", "")
            if fn:
                url = f"https://github.com/{fn}/blob/HEAD/README.md"
                append_to_nlm_queue(output_dir, url)

    print(f"\n[GitHub] search-repos done: {len(repos)} repos")
    return 0


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ARGUS GitHub data collection wrapper — uses subprocess to call gh CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Subcommands:
  repo <owner/repo>            Collect repo metadata + README
  trending                     GitHub Trending approximation (gh search repos by stars+updated)
  issues <owner/repo>          Collect issues list
  release <owner/repo>         Collect latest release
  search-repos <query>         General repo search

Examples:
  github_fetch.py repo anthropics/claude-code --output-dir /tmp/gh
  github_fetch.py trending --lang python --window daily --limit 10 --output-dir /tmp/gh
  github_fetch.py issues anthropics/claude-code --state open --limit 20 --output-dir /tmp/gh
  github_fetch.py release anthropics/claude-code --output-dir /tmp/gh
  github_fetch.py search-repos "agentic coding" --limit 20 --output-dir /tmp/gh
        """
    )

    subparsers = parser.add_subparsers(dest="subcmd", help="subcommand")
    subparsers.required = True

    # Common arguments helper
    def add_common(p):
        p.add_argument("--output-dir", default="/tmp/argus-github", help="Output directory (default: /tmp/argus-github)")
        p.add_argument("--dry-run", action="store_true", help="Print plan only, do not execute")

    # ── repo ──
    p_repo = subparsers.add_parser("repo", help="Collect repo metadata + README")
    p_repo.add_argument("repo", help="owner/repo format, e.g.: anthropics/claude-code")
    p_repo.add_argument("--to-nlm", action="store_true", help="Append README URL to nlm_queue.json")
    add_common(p_repo)

    # ── trending ──
    p_trend = subparsers.add_parser("trending", help="GitHub Trending approximation (search repos by stars+updated)")
    p_trend.add_argument("--lang", default=None, help="Language filter (e.g.: python, typescript, all)")
    p_trend.add_argument("--window", default="daily", choices=["daily", "weekly", "monthly"],
                         help="Time window (default: daily)")
    p_trend.add_argument("--limit", type=int, default=25, help="Result count limit (default: 25)")
    p_trend.add_argument("--to-nlm", action="store_true", help="Append top-10 repo README URLs to nlm_queue.json")
    add_common(p_trend)

    # ── issues ──
    p_issues = subparsers.add_parser("issues", help="Collect repo issues")
    p_issues.add_argument("repo", help="owner/repo format")
    p_issues.add_argument("--state", default="open", choices=["open", "closed", "all"],
                          help="Issue state (default: open)")
    p_issues.add_argument("--limit", type=int, default=30, help="Result count limit (default: 30)")
    add_common(p_issues)

    # ── release ──
    p_release = subparsers.add_parser("release", help="Collect latest release")
    p_release.add_argument("repo", help="owner/repo format")
    p_release.add_argument("--to-nlm", action="store_true", help="Append release URL to nlm_queue.json")
    add_common(p_release)

    # ── search-repos ──
    p_search = subparsers.add_parser("search-repos", help="General repo search")
    p_search.add_argument("query", help="Search keywords")
    p_search.add_argument("--limit", type=int, default=30, help="Result count limit (default: 30)")
    p_search.add_argument("--to-nlm", action="store_true", help="Append top-10 repo README URLs to nlm_queue.json")
    add_common(p_search)

    args = parser.parse_args()

    # Check gh authorization in non-dry-run mode
    if not args.dry_run:
        if not check_gh_auth():
            sys.exit(1)

    dispatch = {
        "repo": cmd_repo,
        "trending": cmd_trending,
        "issues": cmd_issues,
        "release": cmd_release,
        "search-repos": cmd_search_repos,
    }

    handler = dispatch.get(args.subcmd)
    if not handler:
        print(f"[ERROR] Unknown subcommand: {args.subcmd}", file=sys.stderr)
        sys.exit(1)

    rc = handler(args)
    sys.exit(rc)


if __name__ == "__main__":
    main()
