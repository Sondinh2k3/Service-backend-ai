from pydantic import BaseModel, Field, ConfigDict, model_validator


class StageInput(BaseModel):
    """Schema representing a signal stage at an intersection"""

    id: int = Field(..., ge=1, description="Id (must be >= 1).")
    stageCode: str | None = Field(default=None, description="Code representing the stage.")
    oldId: str | None = Field(default=None, description="Legacy identifier of the stage, if available.")
    yellow: int | None = Field(
        default=None,
        ge=0,
        description="Duration of the yellow light for this stage (seconds). Hydrated from snapshot if omitted.",
    )
    redClear: int | None = Field(
        default=None,
        ge=0,
        description="Duration of the red-clear interval after yellow (seconds). Hydrated from snapshot if omitted.",
    )
    duration: int | None = Field(
        default=None,
        ge=1,
        description="Duration of the stage = green + yellow + redClear",
    )
    greenTime: int | None = Field(
        default=None,
        ge=0,
        description="Optional compact input. If duration is omitted, runtime derives duration = greenTime + yellow + redClear.",
    )

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _accept_compact_aliases(cls, data):
        if isinstance(data, dict) and "id" not in data and "stageId" in data:
            data = dict(data)
            data["id"] = data["stageId"]
        return data
