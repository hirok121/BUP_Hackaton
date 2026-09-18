import copy
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


def get_case(data, case_id):
    for c in data["cases"]:
        if c["id"] == case_id:
            return c
    raise KeyError(case_id)


def main():
    with open(os.path.join(os.path.dirname(__file__), "public_cases.json")) as f:
        data = json.load(f)

    case = get_case(data, "SAMPLE-04")  # has a no_discharge_window directive
    inp = case["input"]
    expected = case["expected_output"]
    hours = sorted(inp["hours"], key=lambda h: h["hour"])
    battery = inp["battery"]
    directives = _directives_from_expected(expected["directive_interpretation"])

    lp, enforced = solve_maximal(hours, battery, directives)
    plan, totals = planner.build_plan(lp, hours, battery, enforced)

    base_violations = judge.check(plan, hours, battery, enforced, totals)
    assert base_violations == [], f"negative control failed: {base_violations}"
    print("negative control: PASS (clean plan -> [])")

    results = []

    # 1. delete hour 7
    p = copy.deepcopy(plan)
    p = [e for e in p if e["hour"] != 7]
    v = judge.check(p, hours, battery, enforced, totals)
    results.append(("delete hour 7", bool(v)))

    # 2. duplicate hour 12
    p = copy.deepcopy(plan)
    p.append(copy.deepcopy(p[12]))
    p = [e for e in p if e["hour"] != 23] + [x for x in p if x["hour"] == 23]
    v = judge.check(p, hours, battery, enforced, totals)
    results.append(("duplicate hour 12", bool(v)))

    # 3. grid_kwh as string
    p = copy.deepcopy(plan)
    p[0]["grid_kwh"] = "not a number"
    v = judge.check(p, hours, battery, enforced, totals)
    results.append(("grid_kwh as string", bool(v)))

    # 4. negate one grid_kwh
    p = copy.deepcopy(plan)
    p[0]["grid_kwh"] = -abs(p[0]["grid_kwh"]) - 1
    v = judge.check(p, hours, battery, enforced, totals)
    results.append(("negate grid_kwh", bool(v)))

    # 5. idle with battery_kwh=5
    p = copy.deepcopy(plan)
    idle_idx = next(i for i, e in enumerate(p) if e["battery_action"] == "idle")
    p[idle_idx]["battery_kwh"] = 5.0
    v = judge.check(p, hours, battery, enforced, totals)
    results.append(("idle with nonzero battery_kwh", bool(v)))

    # 6. charge above max_charge_kwh_per_hour
    p = copy.deepcopy(plan)
    charge_idx = next((i for i, e in enumerate(p) if e["battery_action"] == "charge"), None)
    if charge_idx is not None:
        p[charge_idx]["battery_kwh"] = battery["max_charge_kwh_per_hour"] + 50
        v = judge.check(p, hours, battery, enforced, totals)
        results.append(("charge above rate limit", bool(v)))

    # 7. charge in a no_charge_window hour (use SAMPLE-06 which has one)
    case6 = get_case(data, "SAMPLE-06")
    inp6 = case6["input"]
    expected6 = case6["expected_output"]
    hours6 = sorted(inp6["hours"], key=lambda h: h["hour"])
    battery6 = inp6["battery"]
    directives6 = _directives_from_expected(expected6["directive_interpretation"])
    lp6, enforced6 = solve_maximal(hours6, battery6, directives6)
    plan6, totals6 = planner.build_plan(lp6, hours6, battery6, enforced6)
    nc_directive = next(d for d in directives6 if d["directive_type"] == "no_charge_window")
    nc_hour = nc_directive["hours"][0]
    p6 = copy.deepcopy(plan6)
    p6[nc_hour]["battery_action"] = "charge"
    p6[nc_hour]["battery_kwh"] = 10.0
    v = judge.check(p6, hours6, battery6, enforced6, totals6)
    results.append(("charge during no_charge_window", bool(v)))

    # 8. shift one battery_energy_after_kwh by +10
    p = copy.deepcopy(plan)
    p[5]["battery_energy_after_kwh"] += 10
    v = judge.check(p, hours, battery, enforced, totals)
    results.append(("shift energy_after by 10", bool(v)))

    # 9. drop energy_after below active reserve (SAMPLE-03 has a reserve)
    case3 = get_case(data, "SAMPLE-03")
    inp3 = case3["input"]
    expected3 = case3["expected_output"]
    hours3 = sorted(inp3["hours"], key=lambda h: h["hour"])
    battery3 = inp3["battery"]
    directives3 = _directives_from_expected(expected3["directive_interpretation"])
    lp3, enforced3 = solve_maximal(hours3, battery3, directives3)
    plan3, totals3 = planner.build_plan(lp3, hours3, battery3, enforced3)
    reserve_directive = next(d for d in directives3 if d["directive_type"] == "minimum_battery_reserve")
    r_hour = reserve_directive["hours"][0]
    p3 = copy.deepcopy(plan3)
    p3[r_hour]["battery_energy_after_kwh"] = 0.0
    v = judge.check(p3, hours3, battery3, enforced3, totals3)
    results.append(("drop energy_after below reserve", bool(v)))

    # 10. solar_used above effective solar in a reduction hour (SAMPLE-01)
    case1 = get_case(data, "SAMPLE-01")
    inp1 = case1["input"]
    expected1 = case1["expected_output"]
    hours1 = sorted(inp1["hours"], key=lambda h: h["hour"])
    battery1 = inp1["battery"]
    directives1 = _directives_from_expected(expected1["directive_interpretation"])
    lp1, enforced1 = solve_maximal(hours1, battery1, directives1)
    plan1, totals1 = planner.build_plan(lp1, hours1, battery1, enforced1)
    sr_directive = next(d for d in directives1 if d["directive_type"] == "solar_reduction")
    sr_hour = sr_directive["hours"][0]
    p1 = copy.deepcopy(plan1)
    p1[sr_hour]["solar_used_kwh"] = hours1[sr_hour]["solar_kwh"]  # full solar, above reduced eff
    v = judge.check(p1, hours1, battery1, enforced1, totals1)
    results.append(("solar_used above effective solar", bool(v)))

    # 11. add 10 to one grid_kwh only (breaks balance)
    p = copy.deepcopy(plan)
    p[0]["grid_kwh"] += 10
    v = judge.check(p, hours, battery, enforced, totals)
    results.append(("add 10 to one grid_kwh", bool(v)))

    # 12. report total_cost_bdt 100 too high
    bad_totals = dict(totals)
    bad_totals["total_cost_bdt"] += 100
    v = judge.check(plan, hours, battery, enforced, bad_totals)
    results.append(("wrong reported total_cost_bdt", bool(v)))

    all_ok = True
    for name, caught in results:
        status = "PASS" if caught else "FAIL"
        if not caught:
            all_ok = False
        print(f"{name}: {status}")

    print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
