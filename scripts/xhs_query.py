"""
xhs_query.py — Xiaohongshu (XHS) MCP search client for argus probe pipeline.

Connects directly to a local XHS MCP server at localhost:18060 (Streamable HTTP).
Local-only: connects directly to localhost MCP, no remote tunneling.

Usage:
    python3 xhs_query.py "query1" "query2" \
        --output-dir /path/to/dir \
        [--detail] \
        [--sleep-between SEC] \
        [--dry-run]

Exit codes:
    0 — all queries succeeded
    1 — all queries failed
    2 — partial success (some queries failed)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("[xhs_query] ERROR: 'requests' not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MCP_URL = "http://localhost:18060/mcp"
MCP_URL = os.environ.get("ARGUS_XHS_MCP_URL", DEFAULT_MCP_URL)
MCP_TIMEOUT = int(os.environ.get("ARGUS_XHS_TIMEOUT", "30"))
DEFAULT_SLEEP_BETWEEN = 2.0  # seconds between queries (account rate-control)

# ---------------------------------------------------------------------------
# Serial lock — XHS account is sensitive to concurrent requests
# ---------------------------------------------------------------------------

_serial_lock = threading.Lock()


# ---------------------------------------------------------------------------
# MCP session management
# ---------------------------------------------------------------------------


def _mcp_post(session: requests.Session, mcp_session_id: str | None, payload: dict) -> dict:
    """Send a JSON-RPC request to the MCP endpoint, return parsed response."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if mcp_session_id:
        headers["Mcp-Session-Id"] = mcp_session_id

    resp = session.post(MCP_URL, json=payload, headers=headers, timeout=MCP_TIMEOUT)
    resp.raise_for_status()

    # Handle SSE/text-event-stream responses: extract first data: line
    content_type = resp.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data_str = line[5:].strip()
                if data_str and data_str != "[DONE]":
                    return json.loads(data_str)
        return {}

    return resp.json()


def _open_mcp_session(http_session: requests.Session) -> str:
    """Perform MCP initialize + notifications/initialized handshake.

    Returns the Mcp-Session-Id header value for subsequent calls.
    """
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "argus-xhs", "version": "1.0"},
        },
    }

    init_resp = http_session.post(
        MCP_URL,
        json=init_payload,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        timeout=MCP_TIMEOUT,
    )
    init_resp.raise_for_status()

    mcp_session_id = init_resp.headers.get("Mcp-Session-Id", "")

    # Send initialized notification (fire-and-forget, no id field)
    notif_payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if mcp_session_id:
        headers["Mcp-Session-Id"] = mcp_session_id
    try:
        http_session.post(MCP_URL, json=notif_payload, headers=headers, timeout=MCP_TIMEOUT)
    except Exception:
        pass  # notification; ignore response errors

    return mcp_session_id


def _call_tool(http_session: requests.Session, mcp_session_id: str, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool and return the parsed result dict."""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    response = _mcp_post(http_session, mcp_session_id, payload)

    if "error" in response:
        raise RuntimeError(f"MCP tool error [{tool_name}]: {response['error']}")

    result = response.get("result", {})
    content = result.get("content", [])

    # Extract text payload from content array
    for item in content:
        if item.get("type") == "text":
            text = item.get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    return {}


# ---------------------------------------------------------------------------
# Search + detail helpers
# ---------------------------------------------------------------------------


def search_feeds(http_session: requests.Session, mcp_session_id: str, keyword: str) -> list[dict]:
    """Search XHS feeds by keyword. Returns list of post dicts."""
    result = _call_tool(http_session, mcp_session_id, "search_feeds", {"keyword": keyword})
    feeds = result.get("feeds", [])
    if not isinstance(feeds, list):
        feeds = []
    return feeds


def get_feed_detail(http_session: requests.Session, mcp_session_id: str, feed_id: str) -> dict:
    """Fetch full detail for a single XHS post by ID."""
    result = _call_tool(http_session, mcp_session_id, "get_feed_detail", {"id": feed_id})
    return result


# ---------------------------------------------------------------------------
# Per-query worker
# ---------------------------------------------------------------------------


def run_query(
    query: str,
    output_dir: Path,
    fetch_detail: bool,
    sleep_before: float,
) -> dict[str, Any]:
    """Execute a single XHS query under the serial lock.

    Returns a result dict with keys: query, status, posts, error.
    """
    result: dict[str, Any] = {"query": query, "status": "ok", "posts": [], "error": None}

    with _serial_lock:
        if sleep_before > 0:
            time.sleep(sleep_before)

        try:
            http_session = requests.Session()
            mcp_session_id = _open_mcp_session(http_session)

            feeds = search_feeds(http_session, mcp_session_id, query)
            print(f"[xhs_query] '{query}' → {len(feeds)} posts from {MCP_URL}", flush=True)

            enriched_posts = []
            for i, feed in enumerate(feeds):
                post = dict(feed)
                feed_id = feed.get("id") or feed.get("note_id") or feed.get("noteId")

                if fetch_detail and feed_id:
                    if i > 0:
                        time.sleep(sleep_before if sleep_before > 0 else 1.0)
                    try:
                        detail = get_feed_detail(http_session, mcp_session_id, feed_id)
                        post["_detail"] = detail
                    except Exception as detail_err:
                        post["_detail_error"] = str(detail_err)

                enriched_posts.append(post)

            result["posts"] = enriched_posts

            # Write per-query JSON artifact
            safe_name = query.replace(" ", "_").replace("/", "-")[:60]
            out_file = output_dir / f"xhs_{safe_name}.json"
            out_file.write_text(
                json.dumps({"query": query, "posts": enriched_posts}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[xhs_query] wrote {out_file}", flush=True)

        except requests.exceptions.ConnectionError:
            msg = f"Cannot connect to XHS MCP at {MCP_URL}. Is the server running?"
            print(f"[xhs_query] ERROR: {msg}", file=sys.stderr)
            result["status"] = "error"
            result["error"] = msg
        except Exception as exc:
            msg = str(exc)
            print(f"[xhs_query] ERROR for '{query}': {msg}", file=sys.stderr)
            result["status"] = "error"
            result["error"] = msg

    return result


# ---------------------------------------------------------------------------
# Summary writer
# ---------------------------------------------------------------------------


def write_summary(output_dir: Path, results: list[dict]) -> Path:
    """Write _summary.json aggregating all query results."""
    total_posts = sum(len(r.get("posts", [])) for r in results)
    success = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] != "ok"]

    summary = {
        "tool": "xhs_query",
        "mcp_url": MCP_URL,
        "queries_total": len(results),
        "queries_success": len(success),
        "queries_failed": len(failed),
        "total_posts": total_posts,
        "results": results,
    }

    summary_path = output_dir / "_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[xhs_query] summary → {summary_path}", flush=True)
    return summary_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="xhs_query.py",
        description=(
            "Search Xiaohongshu via local XHS MCP server (localhost:18060). "
            "Queries are executed serially to avoid account rate-limiting."
        ),
    )
    parser.add_argument("queries", nargs="+", metavar="QUERY", help="One or more search keywords")
    parser.add_argument(
        "--output-dir",
        required=True,
        metavar="DIR",
        help="Directory to write per-query JSON files and _summary.json",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        default=False,
        help="Fetch full post detail for each result (slower, more data)",
    )
    parser.add_argument(
        "--sleep-between",
        type=float,
        default=DEFAULT_SLEEP_BETWEEN,
        metavar="SEC",
        help=f"Seconds to sleep between queries (default: {DEFAULT_SLEEP_BETWEEN}). "
             "Do not set to 0 — XHS enforces rate limits.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be done without contacting localhost:18060",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)

    if args.dry_run:
        print(f"[xhs_query] dry-run mode — would connect to {MCP_URL}")
        print(f"[xhs_query] output-dir: {output_dir}")
        print(f"[xhs_query] queries ({len(args.queries)}): {args.queries}")
        print(f"[xhs_query] --detail={args.detail}  --sleep-between={args.sleep_between}s")
        print("[xhs_query] dry-run complete, no network calls made.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for i, query in enumerate(args.queries):
        # Sleep before every query except the first one
        sleep_sec = args.sleep_between if i > 0 else 0.0
        result = run_query(
            query=query,
            output_dir=output_dir,
            fetch_detail=args.detail,
            sleep_before=sleep_sec,
        )
        results.append(result)

    write_summary(output_dir, results)

    success_count = sum(1 for r in results if r["status"] == "ok")
    fail_count = len(results) - success_count

    if fail_count == 0:
        return 0  # all succeeded
    if success_count == 0:
        return 1  # all failed
    return 2  # partial success


if __name__ == "__main__":
    sys.exit(main())
