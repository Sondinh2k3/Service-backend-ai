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
    model_config = ConfigDict(extra="ignore")
