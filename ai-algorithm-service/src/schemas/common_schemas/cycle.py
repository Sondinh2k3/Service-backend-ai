from pydantic import BaseModel, Field, ConfigDict

class Cycle(BaseModel):
    """Schema representing a signal cycle."""

    id: int = Field(ge=1, description="Id (must be >= 1)")
    createdDate: str | None = Field(..., description="Timestamp when this cycle was created.")
    crossName: str | None = Field(None, description="Name of the intersection this cycle belongs to.")
    cycleLength: float = Field(ge=1, description="Total cycle length of the signal plan (in seconds)")

    model_config = ConfigDict(extra="ignore")
