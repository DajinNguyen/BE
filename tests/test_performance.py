from datetime import date, timedelta

import pytest

from app.core.db import new_session
from app.models.performance import CompanyPerformanceRecord
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
    def __init__(self, reasons, summary=""):
        self.reasons = reasons
        self.summary = summary
        self.contexts = []

    def write(self, context):
        self.contexts.append(context)
        return ReasonsDraft(reasons=self.reasons, summary=self.summary)


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
    searched = []

    def search(query, start, display, stock_code=None, sort="date", between=None):
        searched.append((query, sort, between))
        return SearchResult(total=len(NEWS), articles=NEWS)

    monkeypatch.setattr(news_search, "search", search)
    writer = FakeReasonWriter(
        [
            ReasonDraft(title="AI 메모리가 많이 팔렸어요", description="…", news_numbers=[1, 9]),
            ReasonDraft(title="근거 없는 이유", description="…", news_numbers=[]),
            ReasonDraft(title="지금 매수할 때예요", description="…", news_numbers=[1]),
        ],
        summary="AI용 메모리가 많이 팔려서  매출과 이익이 함께 늘었어요",
    )
    use_writer(client, writer)

    first = client.get("/api/companies/005930/performance").json()
    assert first["reasons"] == []  # 백그라운드에서 만들고 다음 요청부터 채워진다
    assert first["ai_summary"] is None
    # 분기가 끝난 날부터 75일을 15일씩 나눠서, 실적 발표가 몰리는 15~45일 뒤부터 찾는다
    # (한 번에 찾으면 최근 기사만 온다)
    assert searched == [
        ("삼성전자 2분기 실적", "sim", (date(2026, 7, 15), date(2026, 7, 30))),
        ("삼성전자 2분기 실적", "sim", (date(2026, 7, 30), date(2026, 8, 14))),
        ("삼성전자 2분기 실적", "sim", (date(2026, 8, 14), date(2026, 8, 29))),
        ("삼성전자 2분기 실적", "sim", (date(2026, 8, 29), date(2026, 9, 13))),
        ("삼성전자 2분기 실적", "sim", (date(2026, 6, 30), date(2026, 7, 15))),
    ]
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
    assert body["ai_summary"] == "AI용 메모리가 많이 팔려서 매출과 이익이 함께 늘었어요"
    assert len(writer.contexts) == 1  # 같은 분기 보고서로는 다시 만들지 않는다


def test_ai_summary_is_dropped_without_grounded_reason_or_with_numbers_or_advice():
    draft = ReasonsDraft(reasons=[], summary="메모리가 많이 팔려서 이익이 늘었어요")
    reason = performance_service.PerformanceReason(title="t", description="d", evidence=[])
    assert performance_service.to_summary(draft, []) is None  # 근거가 된 이유가 없다
    assert performance_service.to_summary(draft, [reason]) == draft.summary
    for bad in ("매출이 23% 늘었어요", "지금이 매수 기회예요", "가" * 61, "  "):
        draft = ReasonsDraft(reasons=[], summary=bad)
        assert performance_service.to_summary(draft, [reason]) is None


def test_old_saved_reasons_without_summary_still_read(client, use_dart):
    use_dart()
    reason = {"title": "예전 기록", "description": "…", "evidence": []}
    with new_session() as session:
        rcept_no = performance_service.get_detail(session, "005930")[1]
        session.add(
            CompanyPerformanceRecord(
                stock_code="005930", status="done", content=[reason], rcept_no=rcept_no
            )
        )
        session.commit()
    body = client.get("/api/companies/005930/performance").json()
    assert body["reasons"] == [reason] and body["ai_summary"] is None


def _latest(year=2026, q=3):
    return performance_service.Quarter(year, q, 1, 1, "x")


def test_quarter_news_puts_earnings_articles_first_and_drops_forecasts(monkeypatch):
    monkeypatch.setattr(performance_service, "_today", lambda: date(2026, 11, 19))
    pages = {
        date(2026, 9, 30): [
            _article(1, "2026-10-01", "삼성전자 3분기 영업익 74조 전망"),  # 발표 전 예상
        ],
        date(2026, 10, 15): [
            _article(2, "2026-10-29", "삼성전자 3분기 영업이익 83조…메모리 판매 늘어"),
            _article(
                3, "2026-10-29", "삼성전자  3분기 영업이익 83조…메모리 판매 늘어"
            ),  # 같은 제목
        ],
        date(2026, 11, 14): [
            _article(4, "2026-11-16", "코스피 상장사 실적 급증…삼성전자가 이끌어"),
        ],
    }

    def search(query, start, display, stock_code=None, sort="date", between=None):
        return SearchResult(total=0, articles=pages.get(between[0], []))

    monkeypatch.setattr(news_search, "search", search)
    news = performance_service.quarter_news("005930", _latest())
    assert [a.id for a in news] == ["n2", "n4"]  # 실적 발표 기사가 먼저, 전망·같은 제목은 빠진다


def test_quarter_news_only_searches_until_today(monkeypatch):
    monkeypatch.setattr(performance_service, "_today", lambda: date(2026, 10, 20))
    searched = []

    def search(query, start, display, stock_code=None, sort="date", between=None):
        searched.append(between)
        return SearchResult(total=0, articles=[])

    monkeypatch.setattr(news_search, "search", search)
    assert performance_service.quarter_news("005930", _latest()) == []
    assert searched == [
        (date(2026, 10, 15), date(2026, 10, 20)),
        (date(2026, 9, 30), date(2026, 10, 15)),
    ]


def test_quarter_news_stops_when_enough_earnings_articles(monkeypatch):
    monkeypatch.setattr(performance_service, "_today", lambda: date(2026, 12, 31))
    searched = []
    announced = [
        _article(n, "2026-10-29", f"삼성전자 3분기 영업이익 83조 기사{n}") for n in range(9)
    ]

    def search(query, start, display, stock_code=None, sort="date", between=None):
        searched.append(between)
        return SearchResult(total=9, articles=announced)

    monkeypatch.setattr(news_search, "search", search)
    news = performance_service.quarter_news("005930", _latest())
    assert len(news) == performance_service.NEWS_COUNT
    assert searched == [(date(2026, 10, 15), date(2026, 10, 30))]  # 한 번으로 충분하다


def test_blocked_news_search_is_retried_soon(client, use_dart, monkeypatch):
    use_dart()
    monkeypatch.setattr(news_search, "search", lambda *a, **k: None)  # Google 뉴스가 막혔다
    writer = FakeReasonWriter([])
    use_writer(client, writer)
    client.get("/api/companies/005930/performance")
    with new_session() as session:
        record = session.get(CompanyPerformanceRecord, "005930")
        assert record.status == "failed"  # 기사가 없는 것(done)과 달리 실패로 남긴다
        rcept_no = record.rcept_no
        later = record.updated_at.replace(tzinfo=performance_service.UTC)
        start = performance_service.start_generation
        assert not start(session, "005930", rcept_no, later + timedelta(minutes=10))
        assert start(session, "005930", rcept_no, later + timedelta(minutes=31))
    assert writer.contexts == []
