from pydantic import BaseModel, Field
from src.schemas.ai_schemas.algorithm_output import AlgorithmOutput


class AIOutput(BaseModel):
    """
    Output schema của thuật toán AI (MGMQ-PPO).

    Mỗi phần tử trong `algorithmOutputs` mang thêm `areaId` để trace policy nào
    đã được sử dụng cho cross tương ứng.
    """

    status: int = Field(..., description='1 = thành công, 0 = lỗi')
    numIntersections: int = Field(..., description="Số lượng nút giao được xử lý")
    areaIds: list[int] = Field(..., description="Danh sách areaId đã được route trong request này")
    algorithmOutputs: list[AlgorithmOutput] = Field(..., description="Danh sách kết quả tối ưu cho mỗi nút giao")
