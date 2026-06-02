from pydantic import BaseModel, Field, ConfigDict, model_validator
from src.schemas.common_schemas.cycle import Cycle
from src.schemas.common_schemas.road import Road
from src.schemas.common_schemas.stage_input import StageInput


class Cross(BaseModel):
    """Schema representing an intersection (cross)"""

    id: int = Field(..., ge=1, description="Id (must be >= 1).")
    areaId: int | None = Field(
        default=None,
        ge=1,
        description="Area / network id. Can be omitted when top-level AIInput.areaId is provided.",
    )
    x: float | None = Field(default=None, description="Geographic coordinate (longitude/x). Used once to auto-build neighbor graph.")
    y: float | None = Field(default=None, description="Geographic coordinate (latitude/y). Used once to auto-build neighbor graph.")
    cycle: Cycle | None = Field(default=None, description="The cycle information.")
    cycleId: int | None = Field(default=None, ge=1, description="Compact runtime cycle id.")
    cycleLength: float | None = Field(default=None, ge=1, description="Compact runtime cycle length in seconds.")
    stages: list[StageInput] = Field(default_factory=list, description="List of signal stages included in the plan")
    roads: list[Road] = Field(default_factory=list, description="List of roads connected to this intersection.")

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _accept_compact_aliases(cls, data):
        if isinstance(data, dict) and "id" not in data and "crossId" in data:
            data = dict(data)
            data["id"] = data["crossId"]
        return data
