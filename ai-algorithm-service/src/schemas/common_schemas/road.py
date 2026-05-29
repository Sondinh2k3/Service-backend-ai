from pydantic import BaseModel, Field, ConfigDict


class Road(BaseModel):
    """Schema representing a road segment within the traffic network."""

    id: int = Field(ge=1, description="Unique identifier for the road (must be >= 1)")
    direction: int | None = Field(
        None,
        description="Direction of the road: 1=North, 2=East, 3=South, 4=West"
    )
    toCrossId: int | None = Field(
        default=None,
        ge=1,
        description="If this road connects to another controlled intersection, its id. Used to build neighbor graph."
    )
    saturationFlow: float = Field(gt=0, description="Maximum sustainable flow under ideal conditions (vehicles/hour)")
    averageSpeed: float = Field(
        ge=0,
        description="Average speed of vehicles on this road (unit in averageSpeedUnit, default m/s)"
    )
    occupancySpace: float = Field(
        ..., ge=0, le=100,
        description="Percentage of road space occupied by vehicles (0-100)"
    )
    totalVehicle: int | None = Field(
        default=None,
        ge=0,
        description="Vehicle count within the reporting window (for flow/density derivation)"
    )
    windowSeconds: float | None = Field(
        default=None,
        gt=0,
        description="Reporting window length in seconds (for flow/density derivation)"
    )
    averageSpeedUnit: str | None = Field(
        default=None,
        description="Unit for averageSpeed: 'm/s' or 'km/h'. Defaults to 'm/s' if omitted."
    )
    queueLength: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Queue length on this road (meters). If <= 1.0 and road length is known, "
            "treated as normalized ratio (0-1)."
        ),
    )
    density: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Traffic density. Prefer normalized (0-1). If > 1.0, runtime assumes vehicles/km "
            "and normalizes by lane count."
        ),
    )

    model_config = ConfigDict(extra="ignore")
