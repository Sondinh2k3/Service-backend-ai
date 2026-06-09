from pydantic import BaseModel, Field
from src.schemas.common_schemas.stage_output import StageOutput


class AlgorithmOutput(BaseModel):
    """Compact runtime command for one intersection."""

    crossId: int = Field(
        ...,
        description="Id of the intersection this command belongs to."
    )
    cycleId: int | None = Field(
        ...,
        description="Id of the cycle this command updates."
    )
    cycleLength: int = Field(
        ge=1,
        description="Total cycle length of the signal plan (in seconds)"
    )
    phases: list[StageOutput] = Field(
        ...,
        description="List of signal phases with optimized green times"
    )
