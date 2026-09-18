import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import guardrails
from app.interpreter import interpret

TOL = 0.01


async def main():
    with open(os.path.join(os.path.dirname(__file__), "adversarial.json")) as f:
        data = json.load(f)

    capacity = data["capacity_kwh"]
    cases = data["cases"]

    notes = [c["note"] for c in cases]
    raw = await interpret(notes, capacity)
    entries = guardrails.validate(raw, capacity, len(notes))

    all_ok = True
    for case, entry in zip(cases, entries):
        ok = entry["directive_type"] == case["expected_type"]
        if ok and case["expected_type"] != "no_op":
            adj = entry["structured_adjustment"] or {}
            if adj.get("hours") != case.get("expected_hours"):
                ok = False
            for key in ("expected_factor", "expected_minimum_energy_kwh", "expected_max_grid_kwh"):
                if key in case:
                    field = key.replace("expected_", "")
                    v = adj.get(field)
                    if v is None or abs(float(v) - float(case[key])) > TOL:
                        ok = False

        if not ok:
            all_ok = False
        status = "PASS" if ok else "FAIL"
        print(f"{status}: '{case['note'][:60]}...' -> {entry['directive_type']} {entry.get('structured_adjustment')}")

    print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
