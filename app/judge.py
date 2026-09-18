TOL = 0.01

_VALID_ACTIONS = {"charge", "discharge", "idle"}
_REQUIRED_KEYS = {
    "hour",
    "grid_kwh",
    "solar_used_kwh",
    "battery_action",
    "battery_kwh",
    "battery_energy_after_kwh",
}


def _is_finite_number(v) -> bool:
    if isinstance(v, bool):
        return False
    if not isinstance(v, (int, float)):
        return False
    f = float(v)
    return f == f and f not in (float("inf"), float("-inf"))


def _base_floor(battery: dict) -> float:
    return min(battery["minimum_energy_kwh"], battery["initial_energy_kwh"])


def _limits_from_directives(hours: list[dict], battery: dict, directives: list[dict]):
    n = 24
    eff_solar = [hours[h]["solar_kwh"] for h in range(n)]
    grid_cap = [float("inf")] * n
    base_floor = _base_floor(battery)
    floor = [base_floor] * n
    c_max = [battery["max_charge_kwh_per_hour"]] * n
    d_max = [battery["max_discharge_kwh_per_hour"]] * n

    solar_factor = [None] * n
    for d in directives:
        dtype = d.get("directive_type")
        dhours = d.get("hours") or []
        if dtype == "solar_reduction":
            factor = d.get("factor")
            for h in dhours:
                if solar_factor[h] is None or factor < solar_factor[h]:
                    solar_factor[h] = factor
        elif dtype == "max_grid_window":
            cap = d.get("max_grid_kwh")
            for h in dhours:
                grid_cap[h] = min(grid_cap[h], cap)
        elif dtype == "minimum_battery_reserve":
            m = d.get("minimum_energy_kwh")
            for h in dhours:
                floor[h] = max(floor[h], m)
        elif dtype == "no_charge_window":
            for h in dhours:
                c_max[h] = 0.0
        elif dtype == "no_discharge_window":
            for h in dhours:
                d_max[h] = 0.0

    for h in range(n):
        if solar_factor[h] is not None:
            eff_solar[h] = hours[h]["solar_kwh"] * solar_factor[h]

    return eff_solar, grid_cap, floor, c_max, d_max


def check(
    plan: list[dict],
    hours: list[dict],
    battery: dict,
    directives: list[dict],
    reported: dict | None = None,
) -> list[str]:
    violations: list[str] = []

    try:
        if not isinstance(plan, list) or len(plan) != 24:
            return ["plan does not contain exactly 24 entries"]

        for entry in plan:
            if not isinstance(entry, dict) or not _REQUIRED_KEYS.issubset(entry.keys()):
                return ["plan entry missing required keys"]

        hour_values = [e["hour"] for e in plan]
        if not all(isinstance(h, int) and not isinstance(h, bool) for h in hour_values):
            return ["hour values are not all integers"]
        if sorted(hour_values) != list(range(24)):
            return ["hour values are not exactly {0..23}"]
        if hour_values != sorted(hour_values):
            return ["plan entries are not ascending by hour"]

        for entry in plan:
            if entry["battery_action"] not in _VALID_ACTIONS:
                return [f"invalid battery_action at hour {entry['hour']}"]
            for field in ("grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh"):
                v = entry[field]
                if not _is_finite_number(v) or float(v) < 0:
                    return [f"{field} at hour {entry['hour']} is not a finite non-negative number"]

        by_hour = {e["hour"]: e for e in plan}

        for h in range(24):
            e = by_hour[h]
            is_idle = e["battery_action"] == "idle"
            is_zero = float(e["battery_kwh"]) == 0.0
            if is_idle != is_zero:
                violations.append(f"hour {h}: battery_kwh==0 iff idle violated")

        eff_solar, grid_cap, floor, c_max, d_max = _limits_from_directives(hours, battery, directives)

        for h in range(24):
            e = by_hour[h]
            if e["battery_action"] == "charge" and float(e["battery_kwh"]) > c_max[h] + TOL:
                violations.append(f"hour {h}: charge exceeds max_charge_kwh_per_hour or no_charge_window")
            if e["battery_action"] == "discharge" and float(e["battery_kwh"]) > d_max[h] + TOL:
                violations.append(f"hour {h}: discharge exceeds max_discharge_kwh_per_hour or no_discharge_window")

        prev = float(battery["initial_energy_kwh"])
        energy_after = {}
        for h in range(24):
            e = by_hour[h]
            action = e["battery_action"]
            mag = float(e["battery_kwh"])
            if action == "charge":
                expect = prev + mag
            elif action == "discharge":
                expect = prev - mag
            else:
                expect = prev
            reported_after = float(e["battery_energy_after_kwh"])
            if abs(reported_after - expect) > TOL:
                violations.append(f"hour {h}: battery transition inconsistent with reported energy_after")
            energy_after[h] = reported_after
            prev = reported_after

        capacity = float(battery["capacity_kwh"])
        for h in range(24):
            v = energy_after[h]
            if v < floor[h] - TOL or v > capacity + TOL:
                violations.append(f"hour {h}: battery_energy_after_kwh outside [floor, capacity]")

        for h in range(24):
            e = by_hour[h]
            if float(e["solar_used_kwh"]) > eff_solar[h] + TOL:
                violations.append(f"hour {h}: solar_used_kwh exceeds effective solar")

        for h in range(24):
            e = by_hour[h]
            charge = float(e["battery_kwh"]) if e["battery_action"] == "charge" else 0.0
            discharge = float(e["battery_kwh"]) if e["battery_action"] == "discharge" else 0.0
            lhs = float(e["grid_kwh"]) + float(e["solar_used_kwh"]) + discharge
            rhs = float(hours[h]["demand_kwh"]) + charge
            if abs(lhs - rhs) > TOL:
                violations.append(f"hour {h}: energy balance violated")

        if abs(energy_after[23] - float(battery["initial_energy_kwh"])) > TOL:
            violations.append("end-of-day battery neutrality violated")

        for h in range(24):
            e = by_hour[h]
            if float(e["grid_kwh"]) > grid_cap[h] + TOL:
                violations.append(f"hour {h}: grid_kwh exceeds max_grid_window cap")

        if reported is not None:
            total_grid = sum(float(by_hour[h]["grid_kwh"]) for h in range(24))
            total_cost = sum(
                float(by_hour[h]["grid_kwh"]) * float(hours[h]["tariff_bdt_per_kwh"]) for h in range(24)
            )
            peak_grid = max(float(by_hour[h]["grid_kwh"]) for h in range(24))

            if abs(total_grid - float(reported.get("total_grid_kwh", total_grid))) > TOL:
                violations.append("reported total_grid_kwh does not match recomputed value")
            if abs(total_cost - float(reported.get("total_cost_bdt", total_cost))) > TOL:
                violations.append("reported total_cost_bdt does not match recomputed value")
            if abs(peak_grid - float(reported.get("peak_grid_kwh", peak_grid))) > TOL:
                violations.append("reported peak_grid_kwh does not match recomputed value")

        return violations
    except Exception as exc:  # noqa: BLE001
        return [f"judge internal error: {exc}"]
