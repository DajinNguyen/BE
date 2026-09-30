import os

os.environ["DATABASE_URL"] = "sqlite://"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app.api.deps import get_report_writer  # noqa: E402
from app.core.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.report import ReportContent  # noqa: E402


class FakeReportWriter:
    def __init__(self):
        self.calls = []

    async def write(self, context):
        self.calls.append(context)
        return ReportContent(
            summary="3년 동안 매출과 이익이 크게 늘었어요.",
            key_points=[
                {
                    "id": "kp-performance",
                    "category": "performance",
                    "title": "실적",
                    "body": "2025년에 매출 333.6조 원을 벌었어요.",
                    "status": "good",
                    "source": "삼성전자 실적 발표 · 2025년 연간 · 연결 기준",
                    "base_date": "2026-01-15",
                }
            ],
            charts=[
                {"id": "chart-1", "type": "annual_revenue", "title": "매출", "caption": "늘었어요."}
            ],
            connection={"chain": ["비전", "소식", "숫자"], "conclusion": "같은 방향이에요."},
            strengths=["매출이 꾸준히 늘었어요."],
            watch_points=["2023년에는 이익이 줄었어요."],
            quiz=[
                {
                    "id": "q1",
                    "question": "2025년 매출은?",
                    "options": ["258.94조 원", "300.9조 원", "333.6조 원"],
                    "answer_index": 2,
                    "explanation": "333.6조 원이었어요.",
                },
                {
                    "id": "q2",
                    "question": "잘못된 정답 번호",
                    "options": ["a", "b"],
                    "answer_index": 5,
                    "explanation": "걸러져야 해요.",
                },
            ],
        )


@pytest.fixture
def fake_writer():
    return FakeReportWriter()


@pytest.fixture
def client(fake_writer):
    SQLModel.metadata.drop_all(engine)
    app.dependency_overrides[get_report_writer] = lambda: fake_writer
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
