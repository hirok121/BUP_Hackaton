from itertools import combinations

import numpy as np
from scipy.optimize import linprog

N = 24


def _base_floor(battery: dict) -> float:
    return min(battery["minimum_energy_kwh"], battery["initial_energy_kwh"])


class Limits:
    __slots__ = ("demand", "tariff", "eff_solar", "grid_cap", "floor", "c_max", "d_max")

    def __init__(self, demand, tariff, eff_solar, grid_cap, floor, c_max, d_max):
        self.demand = demand
        self.tariff = tariff
        self.eff_solar = eff_solar
        self.grid_cap = grid_cap
        self.floor = floor
        self.c_max = c_max
        self.d_max = d_max


def build_limits(hours: list[dict], battery: dict, directives: list[dict]) -> Limits:
    demand = [hours[h]["demand_kwh"] for h in range(N)]
    tariff = [hours[h]["tariff_bdt_per_kwh"] for h in range(N)]
    base_solar = [hours[h]["solar_kwh"] for h in range(N)]

    solar_factor = [None] * N
    grid_cap = [float("inf")] * N
    base_floor = _base_floor(battery)
    floor = [base_floor] * N
    c_max = [battery["max_charge_kwh_per_hour"]] * N
    d_max = [battery["max_discharge_kwh_per_hour"]] * N

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

    eff_solar = [
        base_solar[h] * solar_factor[h] if solar_factor[h] is not None else base_solar[h]
        for h in range(N)
    ]

    return Limits(demand, tariff, eff_solar, grid_cap, floor, c_max, d_max)


def _build_lp(hours: list[dict], battery: dict, limits: Limits):
    capacity = battery["capacity_kwh"]
    e0 = battery["initial_energy_kwh"]

    # variable order: g(24) s(24) c(24) d(24)
    nvar = 4 * N

    def idx_g(h):
        return h

    def idx_s(h):
        return N + h

    def idx_c(h):
        return 2 * N + h

    def idx_d(h):
        return 3 * N + h

    A_eq = []
    b_eq = []

    # E1: g+s+d-c = demand
    for h in range(N):
        row = [0.0] * nvar
        row[idx_g(h)] = 1.0
        row[idx_s(h)] = 1.0
        row[idx_d(h)] = 1.0
        row[idx_c(h)] = -1.0
        A_eq.append(row)
        b_eq.append(limits.demand[h])

    # E2: sum c - sum d = 0
    row = [0.0] * nvar
    for h in range(N):
        row[idx_c(h)] = 1.0
        row[idx_d(h)] = -1.0
    A_eq.append(row)
    b_eq.append(0.0)

    A_ub = []
    b_ub = []

    # I1: cumulative net <= capacity - E0
    for h in range(N):
        row = [0.0] * nvar
        for k in range(h + 1):
            row[idx_c(k)] += 1.0
            row[idx_d(k)] -= 1.0
        A_ub.append(row)
        b_ub.append(capacity - e0)

    # I2: -cumulative net <= E0 - floor[h]
    for h in range(N):
        row = [0.0] * nvar
        for k in range(h + 1):
            row[idx_c(k)] -= 1.0
            row[idx_d(k)] += 1.0
        A_ub.append(row)
        b_ub.append(e0 - limits.floor[h])

    bounds = []
    for h in range(N):
        cap = limits.grid_cap[h]
        hi = cap if cap != float("inf") else None
        bounds.append((0.0, hi))
    for h in range(N):
        bounds.append((0.0, limits.eff_solar[h]))
    for h in range(N):
        bounds.append((0.0, limits.c_max[h]))
    for h in range(N):
        bounds.append((0.0, limits.d_max[h]))

    return A_eq, b_eq, A_ub, b_ub, bounds, idx_g, idx_s, idx_c, idx_d, nvar


def solve(hours: list[dict], battery: dict, directives: list[dict]) -> dict | None:
    try:
        limits = build_limits(hours, battery, directives)
        A_eq, b_eq, A_ub, b_ub, bounds, idx_g, idx_s, idx_c, idx_d, nvar = _build_lp(
            hours, battery, limits
        )

        c1 = np.zeros(nvar)
        for h in range(N):
            c1[idx_g(h)] = limits.tariff[h]

        res1 = linprog(
            c1, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs"
        )
        if not res1.success:
            return None

        c_star = float(res1.fun)

        c2 = np.zeros(nvar)
        for h in range(N):
            c2[idx_c(h)] = 1.0
            c2[idx_d(h)] = 1.0

        A_ub2 = list(A_ub)
        b_ub2 = list(b_ub)
        cost_row = list(c1)
        A_ub2.append(cost_row)
        b_ub2.append(c_star + 1e-7 * max(1.0, abs(c_star)))

        res2 = linprog(
            c2, A_ub=A_ub2, b_ub=b_ub2, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs"
        )

        chosen = res2 if res2.success else res1
        x = chosen.x

        g = [float(x[idx_g(h)]) for h in range(N)]
        s = [float(x[idx_s(h)]) for h in range(N)]
        c = [float(x[idx_c(h)]) for h in range(N)]
        d = [float(x[idx_d(h)]) for h in range(N)]

        return {"g": g, "s": s, "c": c, "d": d}
    except Exception:  # noqa: BLE001
        return None


def solve_maximal(
    hours: list[dict], battery: dict, directives: list[dict]
) -> tuple[dict, list[dict]]:
    n = len(directives)
    for k in range(n, -1, -1):
        for subset_idx in combinations(range(n), k):
            subset = [directives[i] for i in subset_idx]
            r = solve(hours, battery, subset)
            if r is not None:
                return r, subset
    r = solve(hours, battery, [])
    return r, []
