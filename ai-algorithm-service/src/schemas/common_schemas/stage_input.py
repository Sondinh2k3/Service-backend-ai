from pydantic import BaseModel, Field, ConfigDict


class StageInput(BaseModel):
    """Schema representing a signal stage at an intersection"""

    id: int = Field(..., ge=1, description="Id (must be >= 1).")
    stageCode: str = Field(..., description="Code representing the stage.")
    oldId: str = Field(..., description="Legacy identifier of the stage, if available.")
    yellow: int = Field(..., ge=1, description="Duration of the yellow light for this stage (seconds).")
    redClear: int = Field(..., ge=1, description="Duration of the red-clear interval after yellow (seconds).")
    duration: int = Field(..., ge=1, description="Duration of the stage = green + yellow + redClear")

    model_config = ConfigDict(extra="ignore")
