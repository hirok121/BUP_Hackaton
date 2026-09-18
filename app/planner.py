from app.optimizer import build_limits

N = 24
ZERO = 1e-6


def _round6(v: float) -> float:
    return round(float(v) + 0.0, 6)


def build_plan(lp, hours: list[dict], battery: dict, directives: list[dict]):
    if lp is None:
        return None

    limits = build_limits(hours, battery, directives)
    capacity = battery["capacity_kwh"]
    e0 = battery["initial_energy_kwh"]

    net = [_round6(lp["c"][h] - lp["d"][h]) for h in range(N)]
    s_round = [_round6(lp["s"][h]) for h in range(N)]

    def action_of(net_h: float) -> str:
        if net_h > ZERO:
            return "charge"
        if net_h < -ZERO:
            return "discharge"
        return "idle"

    actions = [action_of(net[h]) for h in range(N)]
    magnitudes = [0.0 if actions[h] == "idle" else abs(net[h]) for h in range(N)]

    solar_used = [max(0.0, min(s_round[h], limits.eff_solar[h])) for h in range(N)]

    grid = []
    for h in range(N):
        charge = magnitudes[h] if actions[h] == "charge" else 0.0
        discharge = magnitudes[h] if actions[h] == "discharge" else 0.0
        g = hours[h]["demand_kwh"] + charge - discharge - solar_used[h]
        if -0.01 < g < 0:
            g = 0.0
        if g < -0.01:
            return None
        grid.append(max(0.0, g))

    def walk_state(nets):
        state = [0.0] * N
        prev = e0
        for h in range(N):
            prev = prev + nets[h]
            state[h] = prev
        return state

    state = walk_state(net)

    drift = state[N - 1] - e0
    if abs(drift) > ZERO:
        delta = -drift
        fixed = False
        for h in range(N - 1, -1, -1):
            new_net = net[h] + delta
            c_max_h = limits.c_max[h]
            d_max_h = limits.d_max[h]
            if new_net > c_max_h + 1e-9 or -new_net > d_max_h + 1e-9:
                continue

            ok = True
            for k in range(h, N):
                shifted = state[k] + delta
                if shifted < limits.floor[k] - 0.01 or shifted > capacity + 0.01:
                    ok = False
                    break
            if not ok:
                continue

            new_grid_h = grid[h] + delta
            if new_grid_h < -0.01 or new_grid_h > limits.grid_cap[h] + 0.01:
                continue

            net[h] = new_net
            actions[h] = action_of(new_net)
            magnitudes[h] = 0.0 if actions[h] == "idle" else abs(new_net)
            grid[h] = max(0.0, new_grid_h)
            state = walk_state(net)
            fixed = True
            break

        if not fixed:
            return None

    plan = []
    for h in range(N):
        plan.append(
            {
                "hour": h,
                "grid_kwh": _round6(grid[h]),
                "solar_used_kwh": _round6(solar_used[h]),
                "battery_action": actions[h],
                "battery_kwh": _round6(magnitudes[h]),
                "battery_energy_after_kwh": _round6(state[h]),
            }
        )

    totals = compute_totals(plan, hours)
    return plan, totals


def compute_totals(plan: list[dict], hours: list[dict]) -> dict:
    total_grid = sum(p["grid_kwh"] for p in plan)
    total_cost = sum(p["grid_kwh"] * hours[p["hour"]]["tariff_bdt_per_kwh"] for p in plan)
    peak_grid = max(p["grid_kwh"] for p in plan)
    return {
        "total_grid_kwh": _round6(total_grid),
        "total_cost_bdt": _round6(total_cost),
        "peak_grid_kwh": _round6(peak_grid),
    }


def summarize(plan: list[dict], entries: list[dict], enforced: list[dict], hours: list[dict]) -> str:
    charge_hours = [p["hour"] for p in plan if p["battery_action"] == "charge"]
    discharge_hours = [p["hour"] for p in plan if p["battery_action"] == "discharge"]

    applicable = [e for e in entries if e.get("applies")]
    enforced_types = {d.get("directive_type") for d in enforced}

    parts = []
    if charge_hours:
        parts.append(f"charged during hours {charge_hours}")
    if discharge_hours:
        parts.append(f"discharged during hours {discharge_hours}")
    if not parts:
        parts.append("kept the battery idle all day")

    summary = "The plan " + " and ".join(parts) + " to minimize grid electricity cost."

    if len(enforced) < len(applicable):
        summary += (
            f" {len(applicable) - len(enforced)} directive(s) could not be jointly satisfied and were relaxed."
        )
    elif enforced_types:
        summary += f" Applied directives: {', '.join(sorted(enforced_types))}."

    return summary
