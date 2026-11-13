from datetime import date

import pytest

from app.schemas.news import NewsArticle
from app.services import dart, news_search, performance_service
from app.services.naver_news import SearchResult
from app.services.performance_service import ReasonDraft, ReasonsDraft

TRILLION = 1_000_000_000_000
TODAY = date(2026, 11, 13)

# (연도, 분기) → (매출, 영업이익) 3개월 값, 조 원
QUARTERS = {
    (2024, 1): (70, 6),
    (2024, 2): (74, 10),
    (2024, 3): (79, 9),
    (2024, 4): (76, 6),
    (2025, 1): (79, 7),
    (2025, 2): (75, 5),
    (2025, 3): (86, 12),
    (2025, 4): (94, 20),
    (2026, 1): (134, 57),
    (2026, 2): (150, 60),
}
CODES = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


def _rows(year, q, scale=TRILLION):
    """DART 주요계정 응답 흉내. 4분기(사업보고서)는 연간 합계, 3분기는 누적도 준다."""
    if (year, q) not in QUARTERS:
        return []
    rows = []
    for i, name in enumerate(("매출액", "영업이익")):
        upto = [QUARTERS[(year, n)][i] for n in range(1, q + 1)]
        row = {
            "fs_div": "CFS",
            "account_nm": name,
            "rcept_no": f"{year + (q == 4)}0{q}14000001",
            "thstrm_amount": str((sum(upto) if q == 4 else upto[-1]) * scale),
        }
        if q == 3:
            row["thstrm_add_amount"] = str(sum(upto) * scale)
        rows.append(row)
    return rows


@pytest.fixture
def use_dart(monkeypatch):
    def install(scale=TRILLION):
        monkeypatch.setattr(performance_service, "_today", lambda: TODAY)
        monkeypatch.setattr(
            dart, "single_accounts", lambda corp, year, code: _rows(year, CODES[code], scale)
        )

    return install


def _article(n, day, title):
    return NewsArticle(
        id=f"n{n}",
        title=title,
        press="한국경제",
        published_at=f"{day}T09:00:00+09:00",
        url=f"https://www.hankyung.com/{n}",
        summary_short="",
        company_stock_code="005930",
        sentiment="neutral",
        sentiment_reason="",
    )


NEWS = [
    _article(1, "2026-07-07", "삼성전자 2분기 영업이익 60조…AI 메모리 판매 늘어"),
    _article(2, "2026-07-08", "코스피 상승 마감"),  # 회사 이름이 없다
    _article(3, "2026-05-01", "삼성전자 1분기 실적 발표"),  # 분기가 끝나기 전
]


class FakeReasonWriter:
    def __init__(self, reasons):
        self.reasons = reasons
        self.contexts = []

    def write(self, context):
        self.contexts.append(context)
        return ReasonsDraft(reasons=self.reasons)


def use_writer(client, writer):
    client.app.dependency_overrides[performance_service.get_reason_writer] = lambda: writer


def test_performance_from_dart_quarters(client, use_dart):
    use_dart()
    body = client.get("/api/companies/005930/performance").json()
    assert body["period_label"] == "2026년 2분기"
    assert body["announced_at"] == "2026-02-14"  # 접수번호 앞 8자리
    assert body["unit"] == "조 원"
    assert body["summary"] == {"revenue": 150, "operating_income": 60, "operating_margin": 40}
    assert body["yoy"] == {"revenue_rate": 100, "operating_income_rate": 1100}
    assert body["qoq"] == {"revenue_rate": 11.9, "operating_income_rate": 5.3}
    # 최대 8개, 오래된 분기부터. 4분기는 연간에서 3분기 누적을 뺀 값
    assert [q["quarter"] for q in body["quarterly"]] == [
        "24.3Q",
        "24.4Q",
        "25.1Q",
        "25.2Q",
        "25.3Q",
        "25.4Q",
        "26.1Q",
        "26.2Q",
    ]
    assert body["quarterly"][5] == {
        "quarter": "25.4Q",
        "revenue": 94,
        "operating_income": 20,
        "operating_margin": 21.3,
    }
    assert body["source"]["title"] == "삼성전자 2026년 반기보고서"
    assert body["source"]["url"].endswith("rcpNo=20260214000001")
    assert body["segments"] == [] and body["reasons"] == []


def test_small_company_uses_hundred_million_won(client, use_dart):
    use_dart(scale=TRILLION // 100)  # 분기 매출 1.5조 원
    body = client.get("/api/companies/005930/performance").json()
    assert body["unit"] == "억 원"
    assert body["summary"]["revenue"] == 15000


def test_rate_is_null_when_base_is_loss():
    assert performance_service._rate(5, -3) is None
    assert performance_service._rate(5, None) is None


def test_no_quarterly_data_is_404(client, monkeypatch):
    monkeypatch.setattr(dart, "single_accounts", lambda corp, year, code: [])
    res = client.get("/api/companies/005930/performance")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DATA_NOT_FOUND"
    assert client.get("/api/companies/999999/performance").status_code == 404


def test_reasons_are_written_from_quarter_news(client, use_dart, monkeypatch):
    use_dart()
    searched = {}

    def search(query, start, display, stock_code=None, sort="date", between=None):
        searched.update(query=query, sort=sort, between=between)
        return SearchResult(total=len(NEWS), articles=NEWS)

    monkeypatch.setattr(news_search, "search", search)
    writer = FakeReasonWriter(
        [
            ReasonDraft(title="AI 메모리가 많이 팔렸어요", description="…", news_numbers=[1, 9]),
            ReasonDraft(title="근거 없는 이유", description="…", news_numbers=[]),
            ReasonDraft(title="지금 매수할 때예요", description="…", news_numbers=[1]),
        ]
    )
    use_writer(client, writer)

    first = client.get("/api/companies/005930/performance").json()
    assert first["reasons"] == []  # 백그라운드에서 만들고 다음 요청부터 채워진다
    assert searched == {
        "query": "삼성전자 2분기 실적",
        "sort": "sim",
        "between": (date(2026, 6, 30), date(2026, 9, 13)),
    }
    # 분기가 끝난 뒤 나온, 제목에 회사 이름이 있는 실적 기사만 AI에게 보여준다
    assert [n["title"] for n in writer.contexts[0]["news"]] == [NEWS[0].title]

    body = client.get("/api/companies/005930/performance").json()
    assert body["reasons"] == [
        {
            "title": "AI 메모리가 많이 팔렸어요",
            "description": "…",
            "evidence": [
                {
                    "type": "news",
                    "label": "한국경제",
                    "date": "2026-07-07",
                    "url": "https://www.hankyung.com/1",
                }
            ],
        }
    ]
    assert len(writer.contexts) == 1  # 같은 분기 보고서로는 다시 만들지 않는다


def test_no_news_skips_ai(client, use_dart, monkeypatch):
    use_dart()
    monkeypatch.setattr(news_search, "search", lambda *a, **k: None)
    writer = FakeReasonWriter([])
    use_writer(client, writer)
    client.get("/api/companies/005930/performance")
    assert client.get("/api/companies/005930/performance").json()["reasons"] == []
    assert writer.contexts == []
