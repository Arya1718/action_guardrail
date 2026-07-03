#!/usr/bin/env python3
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.guardrail_client import (
    GuardrailConnectionError,
    GuardrailResponseError,
    approve_hitl,
    list_pending_hitl,
    reject_hitl,
)

POLL_INTERVAL = 3


def _mask(val: str | None) -> str:
    if not val:
        return "NOT SET"
    if len(val) <= 4:
        return val + "..."
    return val[:4] + "..."


def _env_diagnostics() -> None:
    dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    print(f"[ENV] Loaded .env from: {dotenv_path}  (exists: {dotenv_path.exists()})")
    print(f"[ENV] GUARDRAIL_API_URL = {os.environ.get('GUARDRAIL_API_URL', 'NOT SET')}")
    print(f"[ENV] GUARDRAIL_API_KEY = {_mask(os.environ.get('GUARDRAIL_API_KEY'))}")
    print(f"[ENV] API_KEY           = {_mask(os.environ.get('API_KEY'))}")
    print(f"[ENV] GROQ_API_KEY      = {_mask(os.environ.get('GROQ_API_KEY'))}")

    missing: list[str] = []
    for var in ("GUARDRAIL_API_URL", "GUARDRAIL_API_KEY"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        print(
            f"\n[FATAL] Missing required env var(s): {', '.join(missing)}.\n"
            f"  Ensure {dotenv_path} contains:\n"
            f"    GUARDRAIL_API_URL=http://127.0.0.1:8001\n"
            f"    GUARDRAIL_API_KEY=your-key-here\n"
        )
        sys.exit(1)


def _print_card(req: dict, idx: int, total: int) -> None:
    tc = req.get("tool_call", {})
    td = req.get("decision", {})
    params = tc.get("parameters", {})
    rule = td.get("matched_rule_id", "?")
    reason = td.get("reason", "")
    created = req.get("created_at", "")

    print("  " + "=" * 40)
    print(f"  Pending Review [{idx + 1}/{total}]  id={req['id']}")
    print(f"  Tool:        {tc.get('tool', '?')}")
    print(f"  Parameters:  {params}")
    print(f"  Matched Rule: {rule}")
    print(f"  Reason:      {reason}")
    print(f"  Requested:   {created}")
    print("  " + "=" * 40)


def _do_approve(req_id: str, resolved_by: str) -> str:
    try:
        resp = approve_hitl(req_id, resolved_by=resolved_by)
        ts = resp.get("request", {}).get("resolved_at", datetime.now(timezone.utc).isoformat())
        print(f"  -> Approved by {resolved_by} at {ts}")
        return "approved"
    except GuardrailResponseError as e:
        print(f"  -> FAILED ({e.status_code}): {e.body}")
        return "error"


def _do_reject(req_id: str, resolved_by: str) -> str:
    try:
        resp = reject_hitl(req_id, resolved_by=resolved_by)
        ts = resp.get("request", {}).get("resolved_at", datetime.now(timezone.utc).isoformat())
        print(f"  -> Rejected by {resolved_by} at {ts}")
        return "rejected"
    except GuardrailResponseError as e:
        print(f"  -> FAILED ({e.status_code}): {e.body}")
        return "error"


def _prompt_reviewer(default_reviewer: str) -> str:
    name = input(f"  Reviewer name [{default_reviewer}]: ").strip()
    return name if name else default_reviewer


def _process_items(items: list[dict], reviewer: str) -> dict:
    counts: dict[str, int] = {"approved": 0, "rejected": 0, "skipped": 0}
    for i, req in enumerate(items):
        _print_card(req, i, len(items))
        while True:
            try:
                choice = input("  Approve / Reject / Skip? [a/r/s]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return counts

            if choice in ("a", "approve"):
                name = _prompt_reviewer(reviewer)
                result = _do_approve(req["id"], name)
                if result == "approved":
                    counts["approved"] += 1
                break
            if choice in ("r", "reject"):
                name = _prompt_reviewer(reviewer)
                result = _do_reject(req["id"], name)
                if result == "rejected":
                    counts["rejected"] += 1
                break
            if choice in ("s", "skip"):
                counts["skipped"] += 1
                break
            print("  Invalid choice.")
    return counts


def _print_summary(counts: dict, total: int) -> None:
    processed = counts["approved"] + counts["rejected"] + counts["skipped"]
    print(
        f"\nReviewed {processed} of {total} pending items. "
        f"{counts['approved']} approved, {counts['rejected']} rejected, "
        f"{counts['skipped']} skipped."
    )


def review_once(reviewer: str) -> None:
    try:
        items = list_pending_hitl()
    except (GuardrailConnectionError, GuardrailResponseError) as e:
        print(f"[FATAL] {e}")
        sys.exit(1)
    if not items:
        print("No pending reviews.")
        return
    counts = _process_items(items, reviewer)
    _print_summary(counts, len(items))


def watch_loop(reviewer: str) -> None:
    seen_ids: set[str] = set()
    print(f"[WATCHING] Polling every {POLL_INTERVAL}s for pending HITL requests...")
    try:
        while True:
            try:
                items = list_pending_hitl()
            except (GuardrailConnectionError, GuardrailResponseError):
                items = []
            new_items = [i for i in items if i["id"] not in seen_ids]
            if new_items:
                for i in new_items:
                    seen_ids.add(i["id"])
                counts = _process_items(new_items, reviewer)
                _print_summary(counts, len(new_items))
            else:
                now = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"[WATCHING] No new pending reviews... (checked at {now})")
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nExiting.")


def main() -> None:
    _env_diagnostics()

    parser = argparse.ArgumentParser(
        description="Interactive HITL request reviewer for Action Guardrail."
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Continuously poll for new pending HITL requests every 3s",
    )
    parser.add_argument(
        "--reviewer", type=str, default=None,
        help="Reviewer name (default: $REVIEWER_NAME or 'reviewer')",
    )
    args = parser.parse_args()

    reviewer = (
        args.reviewer
        or os.environ.get("REVIEWER_NAME")
        or "reviewer"
    )

    try:
        if args.watch:
            watch_loop(reviewer)
        else:
            review_once(reviewer)
    except GuardrailConnectionError as e:
        print(f"[FATAL] Cannot reach guardrail API: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nExiting.")


if __name__ == "__main__":
    main()
