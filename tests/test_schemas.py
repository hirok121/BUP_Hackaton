import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from app.schemas import OptimizeRequest


def _valid_base():
    hours = [
        {"hour": h, "demand_kwh": 100.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0}
        for h in range(24)
    ]
    return {
        "scenario_id": "T-1",
        "operator_notes": ["a note"],
        "hours": hours,
        "battery": {
            "capacity_kwh": 100.0,
            "initial_energy_kwh": 50.0,
            "minimum_energy_kwh": 10.0,
            "max_charge_kwh_per_hour": 20.0,
            "max_discharge_kwh_per_hour": 20.0,
        },
    }


def _classify(exc: ValidationError) -> int:
    codes = set()
    for err in exc.errors():
        etype = err.get("type", "")
        if etype in {"json_invalid", "missing", "model_attributes_type"} or etype.endswith(
            "_type"
        ) or etype.endswith("_parsing"):
            codes.add(400)
        else:
            codes.add(422)
    return 400 if 400 in codes else 422


def expect(body, expected_code, name):
    try:
        OptimizeRequest.model_validate(body)
        status = 200
    except ValidationError as e:
        status = _classify(e)
    ok = status == expected_code
    print(f"{name}: {'PASS' if ok else 'FAIL'} (got {status}, expected {expected_code})")
    return ok


def main():
    results = []

    results.append(expect(_valid_base(), 200, "valid base request"))

    body = _valid_base()
    del body["scenario_id"]
    results.append(expect(body, 400, "missing scenario_id"))

    body = _valid_base()
    body["operator_notes"] = []
    results.append(expect(body, 422, "empty operator_notes"))

    body = _valid_base()
    body["operator_notes"] = ["a", "b", "c", "d"]
    results.append(expect(body, 422, "4 operator_notes"))

    body = _valid_base()
    body["operator_notes"] = ["   "]
    results.append(expect(body, 422, "whitespace-only note"))

    body = _valid_base()
    body["hours"] = body["hours"][:23]
    results.append(expect(body, 422, "23 hours"))

    body = _valid_base()
    body["hours"][0]["hour"] = "0"
    results.append(expect(body, 400, "hour as string"))

    body = _valid_base()
    body["hours"][0]["hour"] = 0.0
    results.append(expect(body, 400, "hour as float"))

    body = _valid_base()
    body["hours"][0]["demand_kwh"] = -5.0
    results.append(expect(body, 422, "negative demand_kwh"))

    body = _valid_base()
    body["battery"]["minimum_energy_kwh"] = 1000.0
    results.append(expect(body, 422, "minimum_energy_kwh > capacity"))

    body = _valid_base()
    body["battery"]["initial_energy_kwh"] = 5.0
    body["battery"]["minimum_energy_kwh"] = 10.0
    results.append(expect(body, 200, "initial < minimum (accepted)"))

    body = _valid_base()
    body["battery"]["capacity_kwh"] = 0.0
    results.append(expect(body, 422, "capacity_kwh == 0"))

    all_ok = all(results)
    print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
