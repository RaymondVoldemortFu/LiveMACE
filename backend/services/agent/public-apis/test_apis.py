#!/usr/bin/env python3
"""
Test script: iterate over all available APIs (excluding UNSUPPORTED),
call each with empty params, and verify the response has expected shape and no exception.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api_server import _discover_available, _run_api, UNSUPPORTED_APIS


def main():
    available = _discover_available()
    print(f"Testing {len(available)} APIs (excluding {len(UNSUPPORTED_APIS)} unsupported)...")
    ok = 0
    fail = 0
    errors = []

    for name in available:
        result = _run_api(name, {})
        if not isinstance(result, dict):
            fail += 1
            errors.append((name, "response is not a dict"))
            continue
        if "status" not in result:
            fail += 1
            errors.append((name, "missing 'status' in response"))
            continue
        if result["status"] not in ("ok", "error"):
            fail += 1
            errors.append((name, f"unexpected status: {result['status']}"))
            continue
        ok += 1

    print(f"OK: {ok}, Failed: {fail}")
    if errors:
        print("\nFailures:")
        for name, msg in errors[:30]:
            print(f"  {name}: {msg}")
        if len(errors) > 30:
            print(f"  ... and {len(errors) - 30} more")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
