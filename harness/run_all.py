#!/usr/bin/env python3
"""
Bootstrap: starts the guardrail server, then runs all scenarios.
Usage: python harness/run_all.py [--dry-run]
"""

import argparse
import os
import sys
import threading
import time

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uvicorn
import httpx
from dotenv import load_dotenv

load_dotenv()

GUARDRAIL_PORT = 8001
GUARDRAIL_URL = f"http://127.0.0.1:{GUARDRAIL_PORT}"


def start_server():
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=GUARDRAIL_PORT, log_level="error")


def wait_for_server(timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{GUARDRAIL_URL}/health", timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def warm_up(url: str, max_attempts: int = 3) -> None:
    """Hit GET /health up to *max_attempts* times with short delays to
    absorb cold-start latency before real scenarios run."""
    print("[WARM] Warming up guardrail service...")
    for i in range(max_attempts):
        try:
            r = httpx.get(f"{url}/health", timeout=10)
            if r.status_code == 200:
                print(f"  [WARM] /health ok (attempt {i + 1})")
                return
        except Exception as exc:
            print(f"  [WARM] attempt {i + 1} failed: {exc}")
        if i < max_attempts - 1:
            time.sleep(2)


def check_api_key() -> None:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print(
            "[FATAL] GROQ_API_KEY not set.\n"
            "  Create a .env file with:\n"
            "    GROQ_API_KEY=your-key-here\n"
            "  Get a free key at https://console.groq.com\n"
        )
        sys.exit(1)


def _print_summary(results: list) -> None:
    print()
    print("+------------------------------------------------------------------+")
    print("|                   SCENARIO SUMMARY                               |")
    print("+--------------------+----------+----------+------------------------+")
    print("| Scenario           | Expected | Actual   | RESULT                 |")
    print("+--------------------+----------+----------+------------------------+")
    for r in results:
        marker = "PASS" if r["passed"] else "FAIL"
        detail = r.get("detail", {})
        follow_ups = detail.get("follow_up_failures", [])
        label = marker
        if follow_ups and r["passed"]:
            label = f"PASS ({len(follow_ups)} follow-up errors)"
        print(f"| {r['name']:18s} | {r['expected']:8s} | {str(r['actual']):8s} | {label:22s} |")
        for fu in follow_ups:
            print(f"|  follow-up issue: {fu:73s} |")
    print("+--------------------+----------+----------+------------------------+")
    print()

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    if passed == total:
        print(f"[RESULT] ALL {total} SCENARIOS PASSED v")
    else:
        print(f"[RESULT] {passed}/{total} PASSED, {total-passed} FAILED x")


def main():
    is_remote = os.environ.get("GUARDRAIL_API_URL", "").startswith("http")

    if not is_remote:
        check_api_key()

        parser = argparse.ArgumentParser()
        parser.add_argument("--dry-run", action="store_true")
        args = parser.parse_args()

        # Override guardrail URL for the test
        os.environ["GUARDRAIL_API_URL"] = GUARDRAIL_URL

        # Start server
        print("[BOOT] Starting guardrail server...")
        t = threading.Thread(target=start_server, daemon=True)
        t.start()

        if not wait_for_server():
            print("[FATAL] Guardrail server did not start in time")
            sys.exit(1)

        print(f"[BOOT] Guardrail server running at {GUARDRAIL_URL}")
        health = httpx.get(f"{GUARDRAIL_URL}/health").json()
        print(f"[BOOT] Health: {health}")
        print()

        warm_up(GUARDRAIL_URL)

        if args.dry_run:
            from harness.scenarios import run_dry_run_scenario
            result = run_dry_run_scenario()
            _print_summary([result])
        else:
            from harness.scenarios import (
                run_scenario_1,
                run_scenario_2,
                run_scenario_3,
                run_scenario_4,
            )
            _run_scenarios(
                [run_scenario_1, run_scenario_2, run_scenario_3, run_scenario_4]
            )
    else:
        # Remote mode: the server is already running at GUARDRAIL_API_URL
        check_api_key()

        warm_up(os.environ["GUARDRAIL_API_URL"])

        from harness.scenarios import (
            run_scenario_1,
            run_scenario_2,
            run_scenario_3,
            run_scenario_4,
        )
        _run_scenarios(
            [run_scenario_1, run_scenario_2, run_scenario_3, run_scenario_4]
        )


def _run_scenarios(runners: list) -> None:
    results = []
    for i, runner in enumerate(runners, 1):
        print(f"\n{'='*70}")
        print(f"  SCENARIO {i}")
        print(f"{'='*70}")
        try:
            result = runner()
        except Exception as e:
            print(f"\n[ERROR] Scenario {i} failed: {e}")
            import traceback
            traceback.print_exc()
            result = {
                "name": f"{i}: exception",
                "expected": "?",
                "actual": "error",
                "audit_found": 0,
                "passed": False,
                "detail": {"primary_assertion": {"error": str(e)}, "follow_up_failures": []},
            }
        results.append(result)
        time.sleep(2.0)

    _print_summary(results)

    # Verify audit log captures everything
    print("\n--- AUDIT LOG CHECK ---")
    try:
        audit = httpx.get(f"{os.environ['GUARDRAIL_API_URL']}/audit-log").json()
        print(f"Total audit records: {len(audit['records'])}")
        for r in audit["records"]:
            print(f"  {r['tool']:20s} | {r['outcome']:15s} | dry_run={r['dry_run']}")
    except Exception as exc:
        print(f"  (audit log query skipped: {exc})")

    all_pass = all(r["passed"] for r in results)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
