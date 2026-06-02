from pydantic import BaseModel, Field, ConfigDict
from src.schemas.common_schemas.cross import Cross


class AIInput(BaseModel):
    """
    Input cho thuật toán AI (MGMQ-PPO).

    Production contract khuyến nghị gửi `areaId` ở top-level và mỗi cross chỉ
    gửi trạng thái đèn + nhu cầu giao thông. Payload legacy vẫn có thể đặt
    `areaId` trong từng Cross.
    """

    areaId: int | None = Field(
        default=None,
        ge=1,
        description="Area / network id for compact production runtime payloads.",
    )
    timestamp: str | None = Field(
        default=None,
        description="Observation timestamp for audit/freshness checks by caller.",
    )
    crosses: list[Cross] = Field(
        ...,
        description="Danh sách các nút giao với trạng thái đèn hiện tại và nhu cầu giao thông."
    )
    yellowTime: int = Field(
        default=3, ge=1, le=10,
        description="Thời gian đèn vàng mỗi pha (giây)."
    )
    minGreen: int = Field(
        default=15, ge=1, le=30,
        description="Thời gian đèn xanh tối thiểu mỗi pha (giây)."
    )
    maxGreen: int = Field(
        default=60, ge=1, le=120,
        description="Thời gian đèn xanh tối đa mỗi pha (giây)."
    )
    greenTimeStep: int = Field(
        default=5, ge=1, le=15,
        description="Bước điều chỉnh thời gian đèn xanh (giây)."
    )

    model_config = ConfigDict(extra="ignore")
