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


def wait_for_server(timeout: int = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{GUARDRAIL_URL}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


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


def main():
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
        results = []
        for i, runner in enumerate(
            [run_scenario_1, run_scenario_2, run_scenario_3, run_scenario_4], 1
        ):
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
                }
            results.append(result)
            time.sleep(2.0)

        _print_summary(results)

        # Verify audit log captures everything
        print("\n--- AUDIT LOG CHECK ---")
        audit = httpx.get(f"{GUARDRAIL_URL}/audit-log").json()
        print(f"Total audit records: {len(audit['records'])}")
        for r in audit["records"]:
            print(f"  {r['tool']:20s} | {r['outcome']:15s} | dry_run={r['dry_run']}")

        all_pass = all(r["passed"] for r in results)
        sys.exit(0 if all_pass else 1)


def _print_summary(results: list) -> None:
    print()
    print("+------------------------------------------------------------------+")
    print("|                   SCENARIO SUMMARY                               |")
    print("+--------------------+----------+----------+------------------------+")
    print("| Scenario           | Expected | Actual   | RESULT                 |")
    print("+--------------------+----------+----------+------------------------+")
    for r in results:
        marker = "PASS" if r["passed"] else "FAIL"
        print(f"| {r['name']:18s} | {r['expected']:8s} | {str(r['actual']):8s} | {marker:22s} |")
    print("+--------------------+----------+----------+------------------------+")
    print()

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    if passed == total:
        print(f"[RESULT] ALL {total} SCENARIOS PASSED v")
    else:
        print(f"[RESULT] {passed}/{total} PASSED, {total-passed} FAILED x")


if __name__ == "__main__":
    main()
