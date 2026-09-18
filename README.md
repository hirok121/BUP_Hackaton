# GridWise — LLM-Assisted Smart Campus Energy Optimization API

BUP CSE Fest 2026 Hackathon — Online Preliminary submission.

One HTTP service that receives a 24-hour campus energy scenario plus 1-3 natural-language
operator notes, interprets the notes with an LLM, validates the interpretation deterministically,
and returns a cost-minimal 24-hour battery/grid/solar schedule that obeys every applicable directive.

## Architecture

```
HTTP request
    |
[1] Request validator     pydantic v2; 400 structural, 422 semantic
    |
[2] Interpreter           N concurrent LLM calls, one per note, stage deadline
    |
[3] Guardrails            repair -> API entries -> flat internal directives
    |
[4] Optimizer             two-stage linear program; maximal feasible directive subset
    |
[5] Planner               netting, rounding, drift repair, totals, summary
    |
[6] Judge                 independent replay validator of every invariant
    |                             |
    |  clean                      |  violations
    v                             v
HTTP 200 response         Arithmetic safety-net plan --> Judge --> HTTP 200 response
```

The service always returns HTTP 200 with a valid, judge-approved plan — including when the LLM
provider is completely unavailable (every note degrades to `no_op` and the optimizer still returns
a cost-minimal plan for the directive-free case).

Full design rationale is in `GRIDWISE_ARCHITECTURE(2).md`.

## Project layout

```
app/
  main.py            FastAPI app, routes, orchestration
  schemas.py         pydantic request/response models
  config.py          settings from environment, LLM prompt text
  interpreter.py     LLM calls, concurrency, retry, bounded cache
  guardrails.py      repair, API-entry assembly, flat-directive conversion
  optimizer.py       limits, two-stage LP (scipy HiGHS), maximal feasible subset
  planner.py         netting, rounding, drift repair, totals, summary
  judge.py           independent replay validator (no imports from optimizer/planner)
  fallback.py        arithmetic safety-net plan
tests/
  public_cases.json  organizer's public sample cases (copied, unmodified)
  test_judge.py       12 judge mutation tests
  test_optimizer.py   LP cost vs. the 10 public reference costs
  test_runner.py      end-to-end self-scoring against a live URL
  test_faults.py      fault-input matrix
Dockerfile
requirements.txt
.env.example
```

## Local quickstart

```bash
git clone <this-repo>
cd BUP_hackaton
pip install -r requirements.txt

cp .env.example .env
# edit .env and set LLM_API_KEY to a real key (any OpenAI-compatible provider)

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify readiness:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Run one public sample case:

```bash
curl -s -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @- <<'EOF'
{
  "scenario_id": "GRID-101",
  "operator_notes": ["Solar output will drop to about 20% from 1 PM to 3 PM."],
  "hours": [ ... 24 entries ... ],
  "battery": { "capacity_kwh": 500, "initial_energy_kwh": 200, "minimum_energy_kwh": 50,
               "max_charge_kwh_per_hour": 100, "max_discharge_kwh_per_hour": 100 }
}
EOF
```

Or run a real case from the public fixture:

```bash
python -c "
import json, urllib.request
data = json.load(open('tests/public_cases.json'))
case = data['cases'][0]['input']
req = urllib.request.Request('http://localhost:8000/optimize-energy',
    data=json.dumps(case).encode(), headers={'Content-Type':'application/json'}, method='POST')
print(urllib.request.urlopen(req).read().decode())
"
```

## Running the test suite

With the service running on `localhost:8000` (or any URL):

```bash
python tests/test_optimizer.py           # LP cost vs. reference costs, no server needed
python tests/test_judge.py               # 12 judge mutation checks, no server needed
python tests/test_runner.py http://localhost:8000    # end-to-end self-scoring
python tests/test_faults.py http://localhost:8000    # fault-input matrix
```

Expected end-to-end result with a working LLM key:

```
TOTAL      interpretation 18/18   validity 10/10   optimization 10.00/10   p95 <5s
```

## Model / provider

- **LLM role:** `app/interpreter.py` calls the configured LLM once per operator note (all notes
  dispatched concurrently) to classify it into one of six directive types. The model performs
  classification only; all arithmetic (percent-to-kWh conversion, factor clamping, hour
  normalization) happens in deterministic code (`app/guardrails.py`).
- **Provider:** any OpenAI-compatible chat-completions endpoint (`LLM_PROVIDER=openai`, or
  `groq`/`local` against a compatible base URL). Native adapters also exist for `anthropic` and
  `google`; see `app/interpreter.py`.
- **Model used for this submission:** `gpt-4o-mini`, temperature 0, JSON response mode.
- **Guardrails:** `app/guardrails.py` rejects any output that is not one of the six allowed
  directive types, normalizes/validates hours, clamps numeric fields, and downgrades anything
  malformed to `no_op` rather than inventing a rule.

## Environment variables

See `.env.example` for the full list. Nothing is required at startup — `/health` answers with no
configuration present, and `/optimize-energy` degrades every note to `no_op` (still returning a
valid, cost-minimal plan) if `LLM_API_KEY` is unset or the provider is unreachable.

| Variable | Required | Default |
|---|---|---|
| `LLM_PROVIDER` | no | `openai` |
| `LLM_API_KEY` | no (but needed for real interpretation) | `""` |
| `LLM_MODEL` | no | `gpt-4o-mini` |
| `LLM_BASE_URL` | no | provider default |
| `LLM_TIMEOUT_S` | no | `3.5` |
| `LLM_MAX_RETRIES` | no | `1` |
| `LLM_STAGE_BUDGET_S` | no | `8.0` |
| `PORT` | no | `8000` |

## Docker

Build and run locally:

```bash
docker build -t gridwise:latest .
docker run -p 8000:8000 \
  -e LLM_PROVIDER=openai -e LLM_MODEL=gpt-4o-mini -e LLM_API_KEY=<key> \
  gridwise:latest
curl http://localhost:8000/health
```

Pull-and-run from a registry (fill in the real published reference before submission):

```bash
docker pull <registry>/<user>/gridwise:v1.0.0
docker run -p 8000:8000 \
  -e LLM_PROVIDER=openai -e LLM_MODEL=gpt-4o-mini -e LLM_API_KEY=<key> \
  <registry>/<user>/gridwise:v1.0.0
curl http://localhost:8000/health
```

The image binds `0.0.0.0`, exposes the port declared by `PORT` (default 8000), and contains no
baked-in secrets — the key is supplied at `docker run` time via `-e`.

## Known limitations

- **`initial_energy_kwh < minimum_energy_kwh`.** This combination is mathematically infeasible
  against the stated floor for the full 24-hour horizon (end-of-day neutrality forces the battery
  back to `initial_energy_kwh`, which would then be below the stated minimum at hour 23). Rather
  than rejecting the request, the service lowers the effective floor to
  `min(minimum_energy_kwh, initial_energy_kwh)` for the whole horizon and returns a valid plan.
  When `initial >= minimum` (every normal scenario) this changes nothing.
- **Determinism** is guaranteed for identical requests sent sequentially to the same warm process
  (temperature 0 + bounded LLM response cache + deterministic solver). It is not guaranteed across
  cold starts or simultaneous first-time cache misses of the same note, since no LLM provider at
  temperature 0 is contractually deterministic.
- **Optimality** (`total_cost_bdt` minimality) is established by `tests/test_optimizer.py` against
  the ten published reference costs, not by a runtime oracle inside the service.
