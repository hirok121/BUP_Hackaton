import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import judge, planner
from app.optimizer import solve_maximal


def _directives_from_expected(expected_entries):
    directives = []
    for e in expected_entries:
        if not e["applies"]:
            continue
        adj = e["structured_adjustment"]
        directives.append(
            {
                "directive_type": e["directive_type"],
                "hours": adj["hours"],
                "factor": adj.get("factor"),
                "minimum_energy_kwh": adj.get("minimum_energy_kwh"),
                "max_grid_kwh": adj.get("max_grid_kwh"),
            }
        )
    return directives


def main():
    with open(os.path.join(os.path.dirname(__file__), "public_cases.json")) as f:
        data = json.load(f)

    all_pass = True
    for case in data["cases"]:
        cid = case["id"]
        inp = case["input"]
        expected = case["expected_output"]

        hours = sorted(inp["hours"], key=lambda h: h["hour"])
        battery = inp["battery"]
        directives = _directives_from_expected(expected["directive_interpretation"])

        lp, enforced = solve_maximal(hours, battery, directives)
        result = planner.build_plan(lp, hours, battery, enforced)
        if result is None:
            print(f"{cid}: FAIL build_plan returned None")
            all_pass = False
            continue
        plan, totals = result

        violations = judge.check(plan, hours, battery, enforced, totals)
        expected_cost = expected["total_cost_bdt"]
        cost_ok = abs(totals["total_cost_bdt"] - expected_cost) <= 0.01

        status = "PASS" if (not violations and cost_ok) else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(
            f"{cid}: {status}  cost={totals['total_cost_bdt']:.2f} expected={expected_cost:.2f} "
            f"violations={violations}"
        )

    print("ALL PASS" if all_pass else "SOME FAILED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
