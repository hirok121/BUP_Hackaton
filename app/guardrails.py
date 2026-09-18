import re

_VALID_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}

_INT_STR_RE = re.compile(r"^-?\d+$")

_FALLBACK_EXPLANATION = "Interpreted deterministically from the operator note."


def _normalize_hours(value) -> list[int]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, float):
            if item.is_integer():
                out.append(int(item))
        elif isinstance(item, str):
            if _INT_STR_RE.match(item):
                out.append(int(item))
    out = [h for h in out if 0 <= h <= 23]
    out = sorted(set(out))
    return out


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_finite(v: float) -> bool:
    f = float(v)
    return f == f and f not in (float("inf"), float("-inf"))


def validate(raw: list[dict], capacity_kwh: float, note_count: int) -> list[dict]:
    entries: list[dict] = []

    for i in range(note_count):
        item = raw[i] if i < len(raw) else None
        entries.append(_repair_one(item, capacity_kwh))

    for idx, entry in enumerate(entries):
        entry["note_index"] = idx

    return entries


def _repair_one(item, capacity_kwh: float) -> dict:
    if not isinstance(item, dict):
        return _no_op_entry()

    dtype = item.get("directive_type")
    if dtype not in _VALID_TYPES:
        return _no_op_entry()

    if dtype == "no_op":
        return _no_op_entry()

    hours = _normalize_hours(item.get("hours"))
    if not hours:
        return _no_op_entry()

    explanation = item.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        explanation = _FALLBACK_EXPLANATION

    if dtype == "solar_reduction":
        factor = item.get("factor")
        if not _is_number(factor) or not _is_finite(factor):
            return _no_op_entry()
        factor = float(factor)
        if factor > 1:
            factor = factor / 100.0
        factor = max(0.0, min(1.0, factor))
        return {
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": hours, "factor": factor},
            "explanation": explanation,
        }

    if dtype == "minimum_battery_reserve":
        v = item.get("minimum_energy_kwh")
        if not _is_number(v) or not _is_finite(v):
            return _no_op_entry()
        v = float(v)
        if item.get("reserve_is_percent_of_capacity") is True:
            if v <= 1:
                v = v * capacity_kwh
            else:
                v = v / 100.0 * capacity_kwh
        v = max(0.0, min(capacity_kwh, v))
        return {
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": hours, "minimum_energy_kwh": v},
            "explanation": explanation,
        }

    if dtype == "no_charge_window":
        return {
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": hours},
            "explanation": explanation,
        }

    if dtype == "no_discharge_window":
        return {
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": hours},
            "explanation": explanation,
        }

    if dtype == "max_grid_window":
        v = item.get("max_grid_kwh")
        if not _is_number(v) or not _is_finite(v) or v < 0:
            return _no_op_entry()
        return {
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": hours, "max_grid_kwh": float(v)},
            "explanation": explanation,
        }

    return _no_op_entry()


def _no_op_entry() -> dict:
    return {
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": _FALLBACK_EXPLANATION,
    }


def to_directives(entries: list[dict]) -> list[dict]:
    directives = []
    for e in entries:
        if not e.get("applies"):
            continue
        adj = e["structured_adjustment"] or {}
        directives.append(
            {
                "directive_type": e["directive_type"],
                "hours": adj.get("hours", []),
                "factor": adj.get("factor"),
                "minimum_energy_kwh": adj.get("minimum_energy_kwh"),
                "max_grid_kwh": adj.get("max_grid_kwh"),
            }
        )
    return directives
