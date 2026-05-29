from pydantic import BaseModel, Field, ConfigDict
from src.schemas.common_schemas.cycle import Cycle
from src.schemas.common_schemas.road import Road
from src.schemas.common_schemas.stage_input import StageInput


class Cross(BaseModel):
    """Schema representing an intersection (cross)"""

    id: int = Field(..., ge=1, description="Id (must be >= 1).")
    areaId: int = Field(..., ge=1, description="Area / network id. Routes the request to the matching trained policy.")
    x: float | None = Field(default=None, description="Geographic coordinate (longitude/x). Used once to auto-build neighbor graph.")
    y: float | None = Field(default=None, description="Geographic coordinate (latitude/y). Used once to auto-build neighbor graph.")
    cycle: Cycle = Field(..., description="The cycle information.")
    stages: list[StageInput] = Field(..., description="List of signal stages included in the plan")
    roads: list[Road] = Field(..., description="List of roads connected to this intersection.")

    model_config = ConfigDict(extra="ignore")
