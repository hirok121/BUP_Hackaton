import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import guardrails, judge, planner
from app.fallback import safe_plan
from app.interpreter import interpret
from app.optimizer import solve, solve_maximal
from app.schemas import OptimizeRequest, OptimizeResponse

logger = logging.getLogger("gridwise")
logging.basicConfig(level=logging.INFO)

app = FastAPI()

_REQUEST_DEADLINE_S = 25.0

# pydantic error types that indicate a structural/type problem (-> 400) rather
# than a semantic range/count problem (-> 422).
_STRUCTURAL_TYPES = {
    "json_invalid",
    "missing",
    "model_attributes_type",
}


def _classify_errors_pydantic(exc: ValidationError) -> int:
    codes = set()
    for err in exc.errors():
        etype = err.get("type", "")
        if etype in _STRUCTURAL_TYPES or etype.endswith("_type") or etype.endswith("_parsing"):
            codes.add(400)
        else:
            codes.add(422)
    return 400 if 400 in codes else 422


@app.get("/health")
def health():
    return {"status": "ok"}


_SAMPLE_REQUEST = {
    "scenario_id": "GRID-101",
    "operator_notes": [
        "Solar output will drop to about 20% from 1 PM to 3 PM.",
        "Do not charge the battery between 2 PM and 4 PM.",
    ],
    "hours": [
        {
            "hour": h,
            "demand_kwh": 100.0,
            "solar_kwh": 50.0 if 8 <= h <= 17 else 0.0,
            "tariff_bdt_per_kwh": 6.0,
        }
        for h in range(24)
    ],
    "battery": {
        "capacity_kwh": 500.0,
        "initial_energy_kwh": 200.0,
        "minimum_energy_kwh": 50.0,
        "max_charge_kwh_per_hour": 100.0,
        "max_discharge_kwh_per_hour": 100.0,
    },
}


_REQUEST_SCHEMA, _REQUEST_DEFS = OptimizeRequest.model_json_schema(
    ref_template="#/components/schemas/{model}"
), None
_REQUEST_DEFS = _REQUEST_SCHEMA.pop("$defs", {})


@app.post(
    "/optimize-energy",
    response_model=OptimizeResponse,
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": _REQUEST_SCHEMA,
                    "example": _SAMPLE_REQUEST,
                }
            },
            "required": True,
        }
    },
)
async def optimize_energy(request: Request):
    try:
        return await asyncio.wait_for(_handle_optimize(request), timeout=_REQUEST_DEADLINE_S)
    except asyncio.TimeoutError:
        return await _timeout_fallback(request)
    except Exception:
        logger.exception("unhandled error in optimize_energy")
        return JSONResponse(status_code=500, content={"error": "internal error"})


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema.setdefault("components", {}).setdefault("schemas", {}).update(_REQUEST_DEFS)
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi


async def _handle_optimize(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "malformed JSON body"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "request body must be a JSON object"})

    try:
        parsed = OptimizeRequest.model_validate(body)
    except ValidationError as exc:
        status_code = _classify_errors_pydantic(exc)
        return JSONResponse(status_code=status_code, content={"error": "invalid request"})

    hours = sorted((h.model_dump() for h in parsed.hours), key=lambda h: h["hour"])
    battery = parsed.battery.model_dump()
    notes = parsed.operator_notes
    capacity = battery["capacity_kwh"]

    raw = await interpret(notes, capacity)
    entries = guardrails.validate(raw, capacity, len(notes))
    directives = guardrails.to_directives(entries)

    lp = None
    enforced: list[dict] = []
    result = None
    try:
        lp, enforced = solve_maximal(hours, battery, directives)
        result = planner.build_plan(lp, hours, battery, enforced)
    except Exception:
        lp, enforced, result = None, [], None

    if result is None:
        lp2 = solve(hours, battery, [])
        if lp2 is not None:
            enforced = []
            result = planner.build_plan(lp2, hours, battery, [])

    if result is None:
        plan = safe_plan(hours, battery, directives)
        enforced = []
        totals = planner.compute_totals(plan, hours)
    else:
        plan, totals = result

    violations = judge.check(plan, hours, battery, enforced, totals)

    if violations:
        logger.warning("judge violations on primary plan, falling back: %s", violations)
        plan = safe_plan(hours, battery, directives)
        enforced = []
        totals = planner.compute_totals(plan, hours)
        violations = judge.check(plan, hours, battery, enforced, totals)

    if violations:
        logger.error("judge violations on safety-net plan (unreachable path): %s", violations)
        return JSONResponse(status_code=500, content={"error": "internal error"})

    summary = planner.summarize(plan, entries, enforced, hours)

    response = OptimizeResponse(
        scenario_id=parsed.scenario_id,
        directive_interpretation=entries,
        hourly_plan=plan,
        total_grid_kwh=totals["total_grid_kwh"],
        total_cost_bdt=totals["total_cost_bdt"],
        peak_grid_kwh=totals["peak_grid_kwh"],
        plan_summary=summary,
    )
    return JSONResponse(status_code=200, content=response.model_dump())


async def _timeout_fallback(request: Request):
    try:
        body = await request.json()
        parsed = OptimizeRequest.model_validate(body)
    except Exception:
        return JSONResponse(status_code=500, content={"error": "internal error"})

    hours = sorted((h.model_dump() for h in parsed.hours), key=lambda h: h["hour"])
    battery = parsed.battery.model_dump()
    plan = safe_plan(hours, battery, [])
    totals = planner.compute_totals(plan, hours)
    entries = guardrails.validate([], battery["capacity_kwh"], len(parsed.operator_notes))
    summary = planner.summarize(plan, entries, [], hours)

    response = OptimizeResponse(
        scenario_id=parsed.scenario_id,
        directive_interpretation=entries,
        hourly_plan=plan,
        total_grid_kwh=totals["total_grid_kwh"],
        total_cost_bdt=totals["total_cost_bdt"],
        peak_grid_kwh=totals["peak_grid_kwh"],
        plan_summary=summary,
    )
    return JSONResponse(status_code=200, content=response.model_dump())
