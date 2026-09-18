from app.optimizer import build_limits

N = 24


def _round6(v: float) -> float:
    return round(float(v) + 0.0, 6)


def safe_plan(hours: list[dict], battery: dict, directives: list[dict]) -> list[dict]:
    limits = build_limits(hours, battery, directives)
    e0 = battery["initial_energy_kwh"]

    plan = []
    for h in range(N):
        demand = hours[h]["demand_kwh"]
        solar_used = min(limits.eff_solar[h], demand)
        solar_used = max(0.0, solar_used)
        grid = max(0.0, demand - solar_used)
        plan.append(
            {
                "hour": h,
                "grid_kwh": _round6(grid),
                "solar_used_kwh": _round6(solar_used),
                "battery_action": "idle",
                "battery_kwh": 0.0,
                "battery_energy_after_kwh": _round6(e0),
            }
        )
    return plan
