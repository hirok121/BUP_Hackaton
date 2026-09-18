import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import judge as judge_mod

TOL = 0.01


def post(base_url: str, payload: dict, timeout: float = 30.0):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/optimize-energy",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        data = json.loads(resp.read())
    elapsed = time.time() - start
    return status, data, elapsed


def _entries_match(returned: dict, expected: dict) -> bool:
    if returned.get("note_index") != expected.get("note_index"):
        return False
    if returned.get("applies") != expected.get("applies"):
        return False
    if returned.get("directive_type") != expected.get("directive_type"):
        return False
    r_adj = returned.get("structured_adjustment")
    e_adj = expected.get("structured_adjustment")
    if e_adj is None:
        return r_adj is None
    if r_adj is None:
        return False
    if r_adj.get("hours") != e_adj.get("hours"):
        return False
    for key in ("factor", "minimum_energy_kwh", "max_grid_kwh"):
        if key in e_adj:
            rv = r_adj.get(key)
            ev = e_adj.get(key)
            if rv is None or abs(float(rv) - float(ev)) > TOL:
                return False
    return True


def main(base_url: str):
    with open(os.path.join(os.path.dirname(__file__), "public_cases.json")) as f:
        data = json.load(f)

    total_notes = 0
    matched_notes = 0
    valid_count = 0
    ratio_sum = 0.0
    all_latencies = []

    for round_num in range(3):
        for case in data["cases"]:
            cid = case["id"]
            inp = case["input"]
            expected = case["expected_output"]

            status, resp, elapsed = post(base_url, inp)
            all_latencies.append(elapsed)

            if round_num > 0:
                continue

            total_notes += len(expected["directive_interpretation"])
            for e_entry in expected["directive_interpretation"]:
                r_entries = [
                    e for e in resp.get("directive_interpretation", [])
                    if e.get("note_index") == e_entry["note_index"]
                ]
                if r_entries and _entries_match(r_entries[0], e_entry):
                    matched_notes += 1

            hours = sorted(inp["hours"], key=lambda h: h["hour"])
            battery = inp["battery"]
            directives = []
            for e in expected["directive_interpretation"]:
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

            plan = resp.get("hourly_plan", [])
            reported = {
                "total_grid_kwh": resp.get("total_grid_kwh"),
                "total_cost_bdt": resp.get("total_cost_bdt"),
                "peak_grid_kwh": resp.get("peak_grid_kwh"),
            }
            violations = judge_mod.check(plan, hours, battery, directives, reported)
            is_valid = status == 200 and not violations

            ref_cost = expected["total_cost_bdt"]
            returned_cost = resp.get("total_cost_bdt", float("inf"))
            ratio = 0.0
            if is_valid and returned_cost > 0:
                ratio = min(1.0, ref_cost / returned_cost)
            if is_valid:
                valid_count += 1
            ratio_sum += ratio

            print(
                f"{cid}  interp {sum(1 for e in expected['directive_interpretation'] if _entries_match((next((r for r in resp.get('directive_interpretation', []) if r.get('note_index')==e['note_index']), {})), e))}/"
                f"{len(expected['directive_interpretation'])}  valid {'YES' if is_valid else 'NO'}  "
                f"cost {returned_cost:.2f} / {ref_cost:.2f}  ratio {ratio:.3f}  {elapsed:.2f}s"
            )

    all_latencies.sort()
    p95_idx = min(len(all_latencies) - 1, int(round(0.95 * (len(all_latencies) - 1))))
    p95 = all_latencies[p95_idx]

    print(
        f"TOTAL      interpretation {matched_notes}/{total_notes}   "
        f"validity {valid_count}/{len(data['cases'])}   "
        f"optimization {10 * ratio_sum / len(data['cases']):.2f}/10   p95 {p95:.2f}s"
    )


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
    main(url)
