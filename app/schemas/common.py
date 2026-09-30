from pydantic import BaseModel


class SampleValue(BaseModel):
    """확인 전 숫자는 `is_sample: true` 로 표시한다."""

    value: int | float
    is_sample: bool = True
