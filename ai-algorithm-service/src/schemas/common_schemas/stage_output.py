from pydantic import BaseModel, Field

class StageOutput(BaseModel):
    """Compact signal command for one stage."""

    stageId: int = Field(ge=1, description="Id (must be >= 1)")
    greenTime: int = Field(ge=1, description="Assigned green time for the stage (in seconds)")
    yellowTime: int = Field(ge=1, description="Assigned yellow time for the stage (in seconds)")
    redClearTime: int = Field(ge=0, description="Assigned red-clear time for the stage (in seconds)")
