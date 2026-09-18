import copy
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def post_raw(base_url: str, body_bytes: bytes, timeout: float = 30.0):
    req = urllib.request.Request(
        base_url.rstrip("/") + "/optimize-energy",
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = None
        return e.code, body


def load_base_case():
    with open(os.path.join(os.path.dirname(__file__), "public_cases.json")) as f:
        data = json.load(f)
    return copy.deepcopy(data["cases"][0]["input"])


def main(base_url: str):
    results = []

    # 1. body that is not JSON
    status, body = post_raw(base_url, b"not json at all")
    ok = status == 400 and body is not None and "error" in body
    results.append(("not JSON", ok, status))

    # 2. operator_notes: []
    case = load_base_case()
    case["operator_notes"] = []
    status, body = post_raw(base_url, json.dumps(case).encode())
    results.append(("operator_notes empty", status == 422, status))

    # 3. 4 notes
    case = load_base_case()
    case["operator_notes"] = ["a", "b", "c", "d"]
    status, body = post_raw(base_url, json.dumps(case).encode())
    results.append(("operator_notes 4 items", status == 422, status))

    # 4. 23 hours
    case = load_base_case()
    case["hours"] = case["hours"][:23]
    status, body = post_raw(base_url, json.dumps(case).encode())
    results.append(("23 hours", status == 422, status))

    # 5. duplicate hour
    case = load_base_case()
    case["hours"][23]["hour"] = 22
    status, body = post_raw(base_url, json.dumps(case).encode())
    results.append(("duplicate hour", status == 422, status))

    # 6. minimum_energy_kwh > capacity_kwh
    case = load_base_case()
    case["battery"]["minimum_energy_kwh"] = case["battery"]["capacity_kwh"] + 100
    status, body = post_raw(base_url, json.dumps(case).encode())
    results.append(("minimum_energy_kwh > capacity", status == 422, status))

    # 7. hour as string
    case = load_base_case()
    case["hours"][0]["hour"] = "5"
    status, body = post_raw(base_url, json.dumps(case).encode())
    results.append(('"hour": "5"', status == 400, status))

    # 8. hour as float
    case = load_base_case()
    case["hours"][0]["hour"] = 5.0
    case["hours"] = [h for h in case["hours"] if h["hour"] != 0] if False else case["hours"]
    status, body = post_raw(base_url, json.dumps(case).encode())
    results.append(('"hour": 5.0', status == 400, status))

    # 9. initial_energy_kwh < minimum_energy_kwh -> 200
    case = load_base_case()
    case["battery"]["initial_energy_kwh"] = 10
    case["battery"]["minimum_energy_kwh"] = 50
    status, body = post_raw(base_url, json.dumps(case).encode())
    results.append(("initial < minimum -> 200", status == 200, status))

    # 10. Bangla / emoji / long note
    case = load_base_case()
    case["operator_notes"] = ["সৌর শক্তি কমে যাবে", "cafeteria menu changed 🍽️", "x" * 5000]
    status, body = post_raw(base_url, json.dumps(case).encode())
    ok = status == 200 and body is not None and len(body.get("hourly_plan", [])) == 24
    results.append(("bangla/emoji/long note", ok, status))

    # 11. contradictory notes
    case = load_base_case()
    case["operator_notes"] = [
        "Do not charge the battery at any point today.",
        "Keep at least 95% of battery capacity in reserve all day.",
    ]
    status, body = post_raw(base_url, json.dumps(case).encode())
    ok = status == 200
    results.append(("contradictory notes -> 200", ok, status))

    # 12. repeat same request 20 times
    case = load_base_case()
    payload = json.dumps(case).encode()
    all_200 = True
    for _ in range(20):
        status, body = post_raw(base_url, payload)
        if status != 200:
            all_200 = False
    results.append(("20x repeat all 200", all_200, "n/a"))

    all_ok = True
    for name, ok, status in results:
        if not ok:
            all_ok = False
        print(f"{name}: {'PASS' if ok else 'FAIL'} (status={status})")

    print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
    raise SystemExit(main(url))
