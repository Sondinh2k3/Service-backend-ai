from pydantic import BaseModel, Field, ConfigDict

class Command(BaseModel):
    crossId: int = Field(ge=1, description="Id of the intersection this command is for (must be >= 1).")
    crossName: str = Field(..., description="Name of the intersection this command is for.")
    vendorId: int = Field(ge=1, description="Id of the vendor this command is for (must be >= 1).")
    vendorCode: str = Field(..., description="Code of the vendor this command is for.")
    vendorName: str = Field(..., description="Name of the vendor this command is for.")
    version: str = Field(..., description="Version of the command schema.")
    oldId: str = Field(..., description="Legacy identifier for backward compatibility.")
    command: str = Field(..., description="The command to be executed by the traffic signal controller.")

    model_config = ConfigDict(extra="ignore")
