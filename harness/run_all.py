#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# .env must be loaded BEFORE any project module that reads os.environ at
# import time.  This explicit-path approach is CWD-independent.
from dotenv import load_dotenv

_dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_dotenv_path)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import uvicorn

GUARDRAIL_PORT = 8001
GUARDRAIL_URL = f"http://127.0.0.1:{GUARDRAIL_PORT}"


def _mask(val: str | None) -> str:
    if not val:
        return "NOT SET"
    if len(val) <= 4:
        return val + "..."
    return val[:4] + "..."


def _env_diagnostics() -> None:
    dotenv_exists = _dotenv_path.exists()
    print(f"[ENV] Loaded .env from: {_dotenv_path}  (exists: {dotenv_exists})")
    print(f"[ENV] GUARDRAIL_API_URL = {os.environ.get('GUARDRAIL_API_URL', 'NOT SET')}")
    print(f"[ENV] GUARDRAIL_API_KEY = {_mask(os.environ.get('GUARDRAIL_API_KEY'))}")
    print(f"[ENV] API_KEY           = {_mask(os.environ.get('API_KEY'))}")
    print(f"[ENV] GROQ_API_KEY      = {_mask(os.environ.get('GROQ_API_KEY'))}")

    missing: list[str] = []
    for var in ("GROQ_API_KEY",):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        print(
            f"\n[FATAL] Missing required env var(s): {', '.join(missing)}.\n"
            f"  Create {_dotenv_path} with:\n"
            f"    GROQ_API_KEY=your-key-here\n"
            f"    GUARDRAIL_API_KEY=dev-placeholder-key\n"
        )
        sys.exit(1)


def _guardrail_headers() -> dict[str, str]:
    return {
        "X-API-Key": os.environ.get(
            "GUARDRAIL_API_KEY",
            os.environ.get("API_KEY", "dev-placeholder-key"),
        )
    }


def _probe_server(url: str, timeout: float = 3.0) -> str | None:
    """Return 'guardrail' if health responds as expected, 'unknown' if
    something responds on the port, or None if nothing responds."""
    try:
        r = httpx.get(f"{url}/health", timeout=timeout)
        if r.status_code == 200 and r.json().get("status") == "ok":
            return "guardrail"
        return "unknown"
    except httpx.ConnectError:
        return None
    except httpx.TimeoutException:
        return None
    except Exception:
        return "unknown"


def _verify_api_key(url: str, timeout: float = 5.0) -> bool:
    """Make an authenticated test call to confirm the current API_KEY works.

    Returns True if the server responds 200 (key is compatible).
    Returns False on any 401 (key mismatch) or other failure.
    """
    try:
        r = httpx.get(
            f"{url}/audit-log?limit=1",
            headers=_guardrail_headers(),
            timeout=timeout,
        )
        if r.status_code == 200:
            return True
        # 401 specifically means wrong API key — the server is alive but
        # was started with a different key than what we loaded.
        return False
    except httpx.RequestError:
        # Network-level failure — server may have gone away between the
        # health check and this call.  Treat as incompatible so we fall
        # through to the fresh-boot path.
        return False


def start_server() -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(GUARDRAIL_PORT),
            "--log-level",
            "error",
        ]
    )


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


def main():
    # Sync API_KEY from GUARDRAIL_API_KEY before diagnostics, so the
    # spawned server and harness client agree on the same key.
    if not os.environ.get("API_KEY") and os.environ.get("GUARDRAIL_API_KEY"):
        os.environ["API_KEY"] = os.environ["GUARDRAIL_API_KEY"]
    os.environ["GUARDRAIL_API_URL"] = GUARDRAIL_URL

    _env_diagnostics()
    print()

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve HITL (default: wait for human via dashboard)")
    args = parser.parse_args()
    os.environ["GUARDRAIL_AUTO_APPROVE"] = "1" if args.auto_approve else "0"

    # Start or reuse server
    probe = _probe_server(GUARDRAIL_URL)
    if probe == "guardrail":
        if not _verify_api_key(GUARDRAIL_URL):
            print(
                f"[BOOT] Found an existing server on port {GUARDRAIL_PORT}, "
                "but it rejected the current API_KEY (401).\n"
                "  This is likely a stale server from a previous run with "
                "different config.\n"
                "  Kill it and retry:\n"
                f"    netstat -ano | findstr :{GUARDRAIL_PORT}\n"
                "    Stop-Process -Id <pid> -Force\n"
            )
            sys.exit(1)
        print(f"[BOOT] Reusing existing guardrail server at {GUARDRAIL_URL}")
        server_proc = None
    elif probe == "unknown":
        print(
            f"[FATAL] Port {GUARDRAIL_PORT} is occupied by a non-guardrail process.\n"
            f"  Find it:  netstat -ano | findstr :{GUARDRAIL_PORT}\n"
            f"  Kill it:  Stop-Process -Id <PID> -Force\n"
        )
        sys.exit(1)
    else:
        print("[BOOT] Starting guardrail server...")
        server_proc = start_server()
        if not wait_for_server():
            if server_proc.poll() is not None:
                print(
                    f"[FATAL] Guardrail server exited early with code {server_proc.returncode}"
                )
            else:
                print("[FATAL] Guardrail server did not start in time")
            sys.exit(1)

    print(f"[BOOT] Guardrail server running at {GUARDRAIL_URL}")
    health = httpx.get(f"{GUARDRAIL_URL}/health").json()
    print(f"[BOOT] Health: {health}")
    print()

    try:
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
            audit_resp = httpx.get(
                f"{GUARDRAIL_URL}/audit-log",
                headers=_guardrail_headers(),
            )
            if audit_resp.status_code != 200:
                print(
                    f"[ERROR] Audit-log fetch returned {audit_resp.status_code}: "
                    f"{audit_resp.text[:200]}"
                )
            else:
                audit = audit_resp.json()
                records = audit.get("records")
                if records is None:
                    print(
                        f"[ERROR] Audit-log response missing 'records' key: "
                        f"{str(audit)[:200]}"
                    )
                else:
                    print(f"Total audit records: {len(records)}")
                    for r in records:
                        print(
                            f"  {r.get('tool','?'):20s} "
                            f"| {r.get('outcome','?'):15s} "
                            f"| dry_run={r.get('dry_run','?')}"
                        )

            all_pass = all(r["passed"] for r in results)
            sys.exit(0 if all_pass else 1)
    finally:
        if server_proc is not None and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_proc.kill()


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
