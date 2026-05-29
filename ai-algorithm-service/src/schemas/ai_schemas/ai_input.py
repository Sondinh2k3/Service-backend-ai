from pydantic import BaseModel, Field, ConfigDict
from src.schemas.common_schemas.cross import Cross


class AIInput(BaseModel):
    """
    Input cho thuật toán AI (MGMQ-PPO).

    Mỗi Cross mang `areaId` để service route sang đúng policy ONNX đã train cho
    khu vực đó. Các cross trong cùng một request có thể thuộc nhiều area khác
    nhau; service sẽ nhóm theo areaId và chạy inference riêng cho từng nhóm.
    """

    crosses: list[Cross] = Field(
        ...,
        description="Danh sách các nút giao (Cross) với trạng thái giao thông hiện tại. Mỗi Cross phải có areaId."
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
