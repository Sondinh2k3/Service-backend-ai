from pydantic import BaseModel, Field
from src.schemas.ai_schemas.algorithm_output import AlgorithmOutput


class AIOutput(BaseModel):
    """
    Compact output schema của thuật toán AI (MGMQ-PPO).

    Response chỉ giữ phần controller cần để điều khiển đèn: nút giao, cycle,
    cycle length và thời gian từng phase.
    """

    commands: list[AlgorithmOutput] = Field(..., description="Danh sách lệnh điều khiển cho từng nút giao")
