#!/usr/bin/env python3
"""
Entrypoint: run all 4 scenarios (or --dry-run) against the guardrail API.
"""

import argparse
import sys
import time
from pathlib import Path

# Ensure the project root is on sys.path so `from harness...` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check_server() -> None:
    """Quick connectivity check before running scenarios."""
    import httpx
    from harness.guardrail_client import _api_url

    url = _api_url()
    try:
        resp = httpx.get(f"{url}/health", timeout=5)
        if resp.status_code != 200:
            print(f"[WARN] Guardrail health check returned {resp.status_code}")
        else:
            data = resp.json()
            print(f"[OK] Guardrail server at {url} — "
                  f"{data.get('policies_loaded', '?')} policies loaded")
    except Exception:
        print(
            f"[ERROR] Cannot reach guardrail API at {url}.\n"
            f"  Start the server first:\n"
            f"    cd guardrail && uvicorn app.main:app --reload\n"
        )
        sys.exit(1)


def print_summary(results: list[dict]) -> None:
    print()
    print("╔════════════════════════════════════════════════════════════════════╗")
    print("║                         SCENARIO SUMMARY                         ║")
    print("╠══════════════════════════╤══════════╤══════════╤══════╗")
    print("║ Scenario                 │ Expected │ Actual   │ PASS ║")
    print("╠══════════════════════════╪══════════╪══════════╪══════╣")
    all_pass = True
    for r in results:
        marker = "PASS" if r["passed"] else "FAIL"
        if not r["passed"]:
            all_pass = False
        print(f"║ {r['name']:24s} │ {r['expected']:8s} │ {str(r['actual']):8s} │ {marker:4s} ║")
    print("╚══════════════════════════╧══════════╧══════════╧══════╝")
    print()
    if all_pass:
        print("[RESULT] ALL SCENARIOS PASSED ✓")
    else:
        print("[RESULT] SOME SCENARIOS FAILED ✗")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['name']}: expected={r['expected']}, "
                      f"actual={r['actual']}, audit_found={r.get('audit_found', '?')}")


def main() -> None:
    check_server()

    parser = argparse.ArgumentParser(description="Run guardrail harness scenarios")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run scenario 1 in dry-run mode (simulated enforcement)",
    )
    args = parser.parse_args()

    if args.dry_run:
        from harness.scenarios import run_dry_run_scenario

        print("\n═══ RUNNING DRY-RUN SCENARIO ═══\n")
        result = run_dry_run_scenario()
        print_summary([result])
        return

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
            print(f"\n[ERROR] Scenario {i} failed with exception: {e}")
            result = {
                "name": f"{i}: exception",
                "expected": "?",
                "actual": "error",
                "audit_found": 0,
                "passed": False,
                "detail": {"error": str(e)},
            }
            import traceback
            traceback.print_exc()
        results.append(result)
        # Brief pause so audit-log ordering is clean between scenarios
        time.sleep(0.5)

    print_summary(results)

    if not all(r["passed"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
