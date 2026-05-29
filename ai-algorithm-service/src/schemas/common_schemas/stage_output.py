from pydantic import BaseModel, Field

class StageOutput(BaseModel):
    """Output schema for a stage."""

    stageId: int = Field(ge=1, description="Id (must be >= 1)")
    stageCode: str = Field(..., description="Stage code used in the signal plan")
    oldId: str = Field(..., description="Legacy identifier for backward compatibility")
    greenTime: int = Field(ge=1, description="Assigned green time for the stage (in seconds)")
    redClearTime: int = Field(ge=1, description="Assigned red-clear time for the stage (in seconds)")
    yellowTime: int = Field(ge=1, description="Assigned yellow time for the stage (in seconds)")
