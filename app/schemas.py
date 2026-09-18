from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HourIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hour: int = Field(strict=True, ge=0, le=23)
    demand_kwh: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    solar_kwh: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    tariff_bdt_per_kwh: float = Field(ge=0, le=1e9, allow_inf_nan=False)


class BatteryIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capacity_kwh: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    initial_energy_kwh: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    minimum_energy_kwh: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    max_charge_kwh_per_hour: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    max_discharge_kwh_per_hour: float = Field(ge=0, le=1e9, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_bounds(self):
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh exceeds capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh exceeds capacity_kwh")
        return self


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(min_length=1)
    operator_notes: list[str] = Field(min_length=1, max_length=3)
    hours: list[HourIn] = Field(min_length=24, max_length=24)
    battery: BatteryIn

    @field_validator("operator_notes")
    @classmethod
    def _notes_non_empty(cls, v: list[str]) -> list[str]:
        cleaned = []
        for note in v:
            stripped = note.strip()
            if not stripped:
                raise ValueError("operator note is empty")
            cleaned.append(stripped[:2000])
        return cleaned

    @model_validator(mode="after")
    def _check_hour_set(self):
        hour_values = [h.hour for h in self.hours]
        if sorted(hour_values) != list(range(24)):
            raise ValueError("hours must contain exactly one entry for each hour 0..23")
        return self


class DirectiveEntryOut(BaseModel):
    note_index: int
    applies: bool
    directive_type: str
    structured_adjustment: dict | None
    explanation: str


class PlanHourOut(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: str
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveEntryOut]
    hourly_plan: list[PlanHourOut]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
