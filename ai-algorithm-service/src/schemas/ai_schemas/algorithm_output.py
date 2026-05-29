from pydantic import BaseModel, Field
from src.schemas.common_schemas.stage_output import StageOutput


class AlgorithmOutput(BaseModel):
    """Output cho mỗi nút giao sau khi chạy thuật toán AI."""

    cycleLength: int = Field(
        ge=1,
        description="Total cycle length of the signal plan (in seconds)"
    )
    phases: list[StageOutput] = Field(
        ...,
        description="List of signal phases with optimized green times"
    )
    crossId: int = Field(
        ...,
        description="Id of the intersection this output belongs to."
    )
    areaId: int = Field(
        ...,
        description="Id of the area/policy used to compute this output."
    )
    crossName: str | None = Field(
        ...,
        description="Name of the intersection this output belongs to."
    )
    cycleId: int | None = Field(
        ...,
        description="Id of the cycle this output belongs to."
    )
    createdDate: str | None = Field(
        ...,
        description="Timestamp when this output was created."
    )
