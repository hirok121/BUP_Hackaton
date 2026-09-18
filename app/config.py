import os

from dotenv import load_dotenv

load_dotenv()


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.llm_provider: str = os.environ.get("LLM_PROVIDER", "openai")
        self.llm_api_key: str = os.environ.get("LLM_API_KEY", "")
        self.llm_model: str = os.environ.get("LLM_MODEL", "gpt-4o")
        self.llm_base_url: str | None = os.environ.get("LLM_BASE_URL") or None
        self.llm_timeout_s: float = _float_env("LLM_TIMEOUT_S", 3.5)
        self.llm_max_retries: int = _int_env("LLM_MAX_RETRIES", 1)
        self.llm_stage_budget_s: float = _float_env("LLM_STAGE_BUDGET_S", 8.0)

        self.fallback_provider: str | None = os.environ.get("FALLBACK_PROVIDER") or None
        self.fallback_api_key: str | None = os.environ.get("FALLBACK_API_KEY") or None
        self.fallback_model: str | None = os.environ.get("FALLBACK_MODEL") or None

        self.port: int = _int_env("PORT", 8000)
        self.log_level: str = os.environ.get("LOG_LEVEL", "info")

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def fallback_configured(self) -> bool:
        return bool(self.fallback_provider and self.fallback_api_key and self.fallback_model)


settings = Settings()


SYSTEM_PROMPT = """You convert one campus operator note into one structured energy directive.
Return ONLY a JSON object. No prose, no markdown fences.

DIRECTIVE TYPES (choose exactly one):
- solar_reduction          usable solar is reduced during specific hours
- minimum_battery_reserve  battery energy must stay at or above a level during specific hours
- no_charge_window         battery charging unavailable during specific hours
- no_discharge_window      battery discharging unavailable during specific hours
- max_grid_window          grid import capped during specific hours
- no_op                    the note does not change today's 24-hour energy schedule

OUTPUT KEYS (all keys always present):
directive_type                   string, one of the six above
hours                            array of ints, or null
factor                           number 0-1, or null
minimum_energy_kwh               number, or null
max_grid_kwh                     number, or null
reserve_is_percent_of_capacity   true or false
explanation                      one short sentence

TIME RULES (critical):
- Convert to 24-hour integers. Start hour INCLUDED, end hour EXCLUDED.
- "1 PM to 3 PM" -> [13,14].   "noon until 2 PM" -> [12,13].
  "from 2 AM until 5 AM" -> [2,3,4].   "between 11 AM and 2 PM" -> [11,12,13].
- A window crossing midnight lists both parts, sorted ascending:
  "10 PM to 2 AM" -> [0,1,22,23].
- If the start and end are the same hour, output that single hour: "3 PM to 3 PM" -> [15].
- If a time is not on the hour, round the start DOWN and the end UP:
  "1:30 PM to 3:30 PM" -> [13,14,15].
- Hours must be unique integers 0-23 in ascending order.
- If no time is stated but the note clearly applies all day, use 0..23.

NUMBER RULES (critical):
- solar_reduction factor = the fraction of solar that REMAINS, between 0 and 1.
  "drops to 20%" -> 0.2.   "80% reduction" -> 0.2.   "about a quarter" -> 0.25.
  "roughly half" -> 0.5.   "one-fifth of normal" -> 0.2.   Never output 20 or 80.
- minimum_battery_reserve: if the note gives kWh, output that number and set
  reserve_is_percent_of_capacity = false. If it gives a PERCENT OF CAPACITY,
  output the percent as a fraction (0.5 for "50% of capacity") and set
  reserve_is_percent_of_capacity = true.
- max_grid_kwh: the stated per-hour cap in kWh.

NO_OP RULES:
Use no_op when the note is about anything other than today's electricity
schedule: room bookings, deadlines, menus, notices, staffing, events, or work
scheduled for a different day ("next week", "next month", "tomorrow").
Work scheduled for a different day is ALWAYS no_op even if it is electrical.
For no_op set every numeric field to null.

ONE DIRECTIVE PER NOTE:
Output exactly one directive type. If a note contains more than one instruction,
choose the one with the clearer effect on today's electricity schedule.
Do not invent demand, tariff, battery limits, or any type not listed.
If the note is ambiguous, pick the single most likely listed type.

BATTERY CAPACITY FOR THIS SCENARIO: {capacity_kwh} kWh

OPERATOR NOTE:
{note_text}
"""

FEW_SHOT = """EXAMPLES

Note: "Facilities will wash the panels from noon until 2 PM. Treat usable solar as roughly 25% of forecast."
{"directive_type":"solar_reduction","hours":[12,13],"factor":0.25,"minimum_energy_kwh":null,"max_grid_kwh":null,"reserve_is_percent_of_capacity":false,"explanation":"Panel washing leaves a quarter of forecast solar from 12:00 to 14:00."}

Note: "Keep at least 50% of the battery capacity stored from 6 PM until 9 PM."
{"directive_type":"minimum_battery_reserve","hours":[18,19,20],"factor":null,"minimum_energy_kwh":0.5,"max_grid_kwh":null,"reserve_is_percent_of_capacity":true,"explanation":"Reserve of half of capacity required in the evening window."}

Note: "Expect an 80% reduction in rooftop solar between 11 AM and 2 PM."
{"directive_type":"solar_reduction","hours":[11,12,13],"factor":0.2,"minimum_energy_kwh":null,"max_grid_kwh":null,"reserve_is_percent_of_capacity":false,"explanation":"An 80 percent cut leaves 20 percent of solar from 11:00 to 14:00."}

Note: "The sports office moved next month's registration deadline."
{"directive_type":"no_op","hours":null,"factor":null,"minimum_energy_kwh":null,"max_grid_kwh":null,"reserve_is_percent_of_capacity":false,"explanation":"Administrative notice with no effect on today's energy schedule."}
"""
