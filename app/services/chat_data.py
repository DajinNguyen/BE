"""챗봇에게 넘길 자료 모으기: 질문이 무엇을 묻는지에 맞춰 화면·회사·산업·시장 자료를 찾는다.

숫자는 모두 서버가 실제 자료(DART·시세·기사)에서 가져오거나 계산한다. AI는 이 자료 안에서만 대답한다.
"""

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session

from app.core.errors import AppError
from app.models.performance import CompanyPerformanceRecord
from app.models.report import ReportRecord
from app.schemas.chat import ChatContext
from app.services import (
    credit_service,
    data_store,
    market_service,
    naver,
    naver_index,
    news_search,
    overview_service,
    performance_service,
    price_service,
    wallet_service,
)
from app.services.chat_plan import Plan, Want

logger = logging.getLogger(__name__)

INDUSTRY_COMPANIES = 8
_ASKS_NEWS = re.compile(r"뉴스|소식|기사")


NEWS_COUNT = 5
PEER_COUNT = 3
_PRICE_WORDS = re.compile(
    r"주가|급락|하락|약세|폭락|신저가|급등|상승|강세|폭등|신고가|매도|매수|목표가|공매도|쇼크|우려|\d+%"
)
PRICE_NEWS_DAYS = 30
PRICE_NEWS_COUNT = 6
BIG_MOVES = 3


def _price_trend(stock_code: str) -> dict[str, Any] | None:
    """최근 주가 흐름: 기간별 변화율과 최근 한 달 동안 크게 움직인 날 (기사와 날짜를 맞춰 보게)."""
    try:
        history = price_service.get_price_history(stock_code, "3m")
    except Exception as e:  # noqa: BLE001
        logger.warning("챗봇용 주가 흐름을 받지 못했어요 (%s): %s", stock_code, e)
        return None
    points = history.points
    if history.is_sample or len(points) < 2:
        return None  # 예시로 만든 흐름은 근거로 쓰지 않는다
    last = points[-1]

    def since(days: int) -> dict[str, Any] | None:
        start = last.date - timedelta(days=days)
        base = next((p for p in points if p.date >= start), None)
        if base is None or base.date == last.date:
            return None
        return {
            "from_date": base.date.isoformat(),
            "from_price_won": base.close,
            "change_percent": _change(last.close, base.close),
        }

    month = [p for p in points if p.date >= last.date - timedelta(days=30)]
    daily = [
        {"date": now.date.isoformat(), "change_percent": _change(now.close, before.close)}
        for before, now in zip(points, points[1:], strict=False)
        if now in month
    ]
    high, low = max(month, key=lambda p: p.high), min(month, key=lambda p: p.low)
    return {
        "as_of": last.date.isoformat(),
        "price_won": last.close,
        "change_over_1_week": since(7),
        "change_over_1_month": since(30),
        "change_over_3_months": since(91),
        "highest_in_last_month": {"date": high.date.isoformat(), "price_won": high.high},
        "lowest_in_last_month": {"date": low.date.isoformat(), "price_won": low.low},
        "biggest_daily_drops_in_last_month": sorted(daily, key=lambda d: d["change_percent"] or 0)[
            :BIG_MOVES
        ],
        "biggest_daily_rises_in_last_month": sorted(
            daily, key=lambda d: d["change_percent"] or 0, reverse=True
        )[:BIG_MOVES],
    }


# 종목을 찍어 주거나 크게 오른다고 부추기는 기사, 사라·팔라·버티라고 권하는 기사는 근거로 쓰지 않는다
# (AI가 "기사에 따르면 손절 말고 버티라고 해요"처럼 권유를 그대로 옮기는 일을 막는다)
_HYPE = re.compile(
    r"\d+배|목표가|목표주가|추천주|유망주|급등주|대박|사야 ?할|담아라|주목할 ?종목|톱픽|수익률 ?\d"
    r"|버텨|버티(세요|라)|손절 ?(말|하지 ?마)|저평가|고평가|줍줍|물타기|매수 ?(기회|타이밍|추천)"
    r"|(사|파|매수하|매도하)세요|팔아라|사라[\"'”’ ,.!…]|지금이 ?기회|바닥 ?(잡|찍)|살 ?때|팔 ?때"
)


def _usable(articles: list[Any]) -> list[Any]:
    return [
        a
        for a in articles
        if not _HYPE.search(a.title) and not _HYPE.search(getattr(a, "summary_short", "") or "")
    ]


def _article(a: Any) -> dict[str, Any]:
    return {
        "title": a.title,
        "press": a.press,
        "date": a.published_at[:10],
        "summary": a.summary_short or None,
        "url": a.url,  # AI에게는 넘기지 않고, 답변의 출처로 쓴다
    }


def _news_about_price(stock_code: str) -> list[dict[str, Any]]:
    """최근 한 달 동안 이 회사 주가를 다룬 기사 (제목에 회사 이름과 주가 관련 낱말이 있는 것)."""
    name = data_store.news_query(stock_code)
    today = datetime.now(UTC).date()
    period = (today - timedelta(days=PRICE_NEWS_DAYS), today + timedelta(days=1))
    try:
        result = news_search.search(f'"{name}" 주가', 1, 50, stock_code, "date", period)
    except Exception as e:  # noqa: BLE001
        logger.warning("챗봇용 주가 기사를 받지 못했어요 (%s): %s", stock_code, e)
        return []
    if result is None:
        return []
    needle = "".join(name.split()).lower()
    about = [
        a
        for a in result.articles
        if needle in "".join(a.title.split()).lower() and _PRICE_WORDS.search(a.title)
    ]
    return [_article(a) for a in _usable(about)[:PRICE_NEWS_COUNT]]


_DROP = re.compile(r"떨어|내렸|내린|하락|급락|폭락|빠졌|빠진")
_RISE = re.compile(r"올랐|오른|올라|상승|급등|폭등|뛰었")
MOVE_DAYS = 2  # 크게 움직인 날을 몇 개까지 찾아볼지
MOVE_NEWS = 4  # 그날 기사 몇 건


def _news_around_big_moves(
    stock_code: str, trend: dict[str, Any] | None, question: str
) -> list[dict[str, Any]]:
    """주가가 크게 움직인 날과 그 뒤 이틀의 기사. 왜 움직였는지는 대개 그날 기사에 있다.

    떨어진 이유를 물으면 크게 떨어진 날, 오른 이유를 물으면 크게 오른 날을 본다 (둘 다 아니면 하루씩).
    """
    if not trend:
        return []
    drops = trend["biggest_daily_drops_in_last_month"]
    rises = trend["biggest_daily_rises_in_last_month"]
    if _DROP.search(question) and not _RISE.search(question):
        days = drops[:MOVE_DAYS]
    elif _RISE.search(question) and not _DROP.search(question):
        days = rises[:MOVE_DAYS]
    else:
        days = drops[:1] + rises[:1]
    days = [d for d in days if d["change_percent"] is not None]
    name = data_store.news_query(stock_code)
    needle = "".join(name.split()).lower()

    def articles_for(day: dict[str, Any]) -> dict[str, Any]:
        start = date.fromisoformat(day["date"])
        try:
            result = news_search.search(
                f'intitle:"{name}"', 1, 40, stock_code, "date", (start, start + timedelta(days=3))
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("챗봇용 급등락일 기사를 받지 못했어요 (%s): %s", stock_code, e)
            result = None
        found = [
            a
            for a in (result.articles if result else [])
            if needle in "".join(a.title.split()).lower()
            and day["date"] <= a.published_at[:10] <= (start + timedelta(days=2)).isoformat()
        ]
        # 주가를 다룬 기사를 앞에 둔다
        found.sort(key=lambda a: _PRICE_WORDS.search(a.title) is None)
        return {
            "date": day["date"],
            "price_change_percent_that_day": day["change_percent"],
            "articles": [_article(a) for a in _usable(found)[:MOVE_NEWS]],
        }

    with ThreadPoolExecutor(max_workers=max(len(days), 1)) as pool:
        return [m for m in pool.map(articles_for, days) if m["articles"]]


def _change(now: float, before: float) -> float | None:
    return round((now - before) / abs(before) * 100, 1) if before else None


def _yearly(annual: list[Any]) -> list[dict[str, Any]]:
    """연도별 숫자에 영업이익률과 전년 대비 변화율을 계산해서 붙인다 (AI가 직접 계산하지 않게)."""
    rows = []
    by_year = {a.fiscal_year: a for a in annual}
    for a in sorted(annual, key=lambda a: a.fiscal_year):
        last = by_year.get(a.fiscal_year - 1)
        rows.append(
            {
                "fiscal_year": a.fiscal_year,
                "revenue": a.revenue,
                "operating_income": a.operating_income,
                "operating_margin_percent": (
                    round(a.operating_income / a.revenue * 100, 1) if a.revenue > 0 else None
                ),
                "revenue_change_from_last_year_percent": (
                    _change(a.revenue, last.revenue) if last else None
                ),
                "operating_income_change_from_last_year_percent": (
                    _change(a.operating_income, last.operating_income)
                    if last and last.operating_income > 0
                    else None
                ),
            }
        )
    return rows


def _recent_news(stock_code: str) -> list[dict[str, Any]]:
    """최근 기사 (제목·언론사·날짜·요약). 실제 기사를 받지 못하면 시드 기사."""
    try:
        live = news_search.company_news(
            data_store.news_query(stock_code), 1, NEWS_COUNT, stock_code
        )
    except Exception as e:  # noqa: BLE001 - 기사를 못 받아도 대답은 한다
        logger.warning("챗봇용 기사를 받지 못했어요 (%s): %s", stock_code, e)
        live = None
    articles = (
        live.articles
        if live is not None
        else data_store.to_articles(stock_code, data_store.get_news(stock_code))
    )
    return [_article(a) for a in _usable(list(articles))[:NEWS_COUNT]]


def _peer_numbers(stock_code: str) -> list[dict[str, Any]]:
    """같은 산업에서 시가총액이 가까운 회사들의 숫자 ("이 정도면 높은 편이에요?"에 견줄 기준)."""
    seeds = data_store._seed_companies()
    me = seeds.get(stock_code)
    base = me.market_cap.value if me and me.market_cap else None
    # 회사의 업종 이름과 맞는 산업의 회사들을 먼저 본다 (SK하이닉스 → "AI"보다 "반도체" 회사들과 견준다)
    mine = [r for r in data_store.industry_records() if stock_code in r["companies"]]
    sector = me.sector if me else ""
    matching = [r for r in mine if any(part in sector for part in r["name"].split("·"))]
    candidates = {
        code
        for record in (matching or mine)
        for code in record["companies"]
        if code != stock_code and code in seeds and seeds[code].market_cap
    }
    if not base or not candidates:
        return []
    nearest = sorted(candidates, key=lambda c: abs(seeds[c].market_cap.value - base))
    peers = []
    for code in nearest[: PEER_COUNT * 3]:  # 재무제표가 없는 회사를 건너뛸 여유
        try:
            financials = data_store.find_financials(code)
        except Exception as e:  # noqa: BLE001
            logger.warning("챗봇용 비교 회사 재무를 받지 못했어요 (%s): %s", code, e)
            continue
        if financials is None or not financials.annual:
            continue
        latest = _yearly(financials.annual)[-1]
        indicators = financials.indicators
        peers.append(
            {
                "name": seeds[code].name,
                "fiscal_year": latest["fiscal_year"],
                "operating_margin_percent": latest["operating_margin_percent"],
                "revenue_change_from_last_year_percent": latest[
                    "revenue_change_from_last_year_percent"
                ],
                "debt_ratio_percent": (
                    indicators.debt_ratio.value if indicators.debt_ratio else None
                ),
                "roe_percent": indicators.roe.value if indicators.roe else None,
                "per": indicators.per.value if indicators.per else None,
                "pbr": indicators.pbr.value if indicators.pbr else None,
            }
        )
        if len(peers) == PEER_COUNT:
            break
    return peers


def _company_screen(
    session: Session,
    stock_code: str,
    tab: str | None,
    wants: set[Want] = frozenset(),
    question: str = "",
) -> dict[str, Any]:
    company = overview_service.apply_overview(session, data_store.get_company(stock_code))
    screen: dict[str, Any] = {
        "name": company.name,
        "stock_code": company.stock_code,
        "market": company.market,
        "sector": company.sector,
        "one_liner": company.one_liner,
        "is_financial_company": company.sector_group == "finance",
        "industries": [i.name for i in data_store.industries_of(stock_code)],
        "current_price_won": company.current_price.value,
        "change_rate_percent": company.change_rate.value,
        "price_as_of": company.price_as_of,
        "market_cap_trillion_won": company.market_cap.value if company.market_cap else None,
        "business_description": company.business_description or None,
        "business_segments": [
            {"name": s.name, "description": s.description} for s in company.business_segments
        ],
    }
    financials = data_store.find_financials(stock_code)
    if financials is None:
        screen["financials"] = None  # 재무제표를 아직 불러오지 못했다
    else:
        f = data_store.in_readable_unit(financials)
        screen["financials"] = {
            "unit": f.unit,
            "basis": f.basis,
            "source": f.source,
            "annual": _yearly(f.annual),
            "latest_quarter": f.latest_quarter.model_dump() if f.latest_quarter else None,
            "revenue_by_business": (
                [{"name": i.name, "revenue": i.revenue} for i in f.segments.items]
                if f.segments
                else []
            ),
            "roe_percent": f.indicators.roe.value if f.indicators.roe else None,
            "debt_ratio_percent": (
                f.indicators.debt_ratio.value if f.indicators.debt_ratio else None
            ),
            "per": f.indicators.per.value if f.indicators.per else None,
            "pbr": f.indicators.pbr.value if f.indicators.pbr else None,
            "indicators_as_of": f.indicators_as_of,
        }
    record = session.get(ReportRecord, stock_code)
    if record is not None and record.content:
        # 저장한 AI 리포트의 글 (숫자는 위 재무 자료가 기준)
        content = record.content
        screen["ai_report"] = {
            "headline": content.get("headline"),
            "key_points": [
                {k: p.get(k) for k in ("category", "title", "reason", "meaning")}
                for p in content.get("key_points", [])
            ],
            "vision_summary": content.get("vision_summary"),
        }
    performance = session.get(CompanyPerformanceRecord, stock_code)
    if performance is not None and performance.content:
        # 최근 분기 실적이 왜 그렇게 나왔는지 기사로 정리해 둔 것
        reasons, summary = performance_service.saved(performance)
        screen["why_latest_quarter_results"] = {
            "summary": summary,
            "reasons": [{"title": r.title, "description": r.description} for r in reasons],
        }
    if wants & {"price_move", "overview"}:
        trend = _price_trend(stock_code)
        if trend is not None and not company.change_rate.is_sample:
            trend = {"change_today_percent": company.change_rate.value, **trend}
        screen["price_trend"] = trend
        # 시장 전체가 같이 움직인 날인지 (한 회사만의 일이 아닐 수 있다)
        screen["whole_market_today"] = _indices()
        if "price_move" in wants:
            screen["news_on_days_price_moved_most"] = _news_around_big_moves(
                stock_code, trend, question
            )
            screen["news_about_price"] = _news_about_price(stock_code)
    if (tab and "news" in tab) or wants & {"price_move", "news", "overview"}:
        screen["recent_news"] = _recent_news(stock_code)
    if "level" in wants:
        screen["same_industry_companies_for_reference"] = _peer_numbers(stock_code)
    return screen


INDUSTRY_MOVERS = 6  # 한 달 흐름을 살펴보는 큰 회사 수
MARKET_NEWS = 6
INDUSTRY_EXTREMES = 3  # 오늘 가장 많이 오르고 떨어진 회사 몇 곳
INDUSTRY_NEWS = 6
INDUSTRY_MENTION_MAX = 2
# 산업 이름과 다르게 부르는 말
INDUSTRY_ALIASES = {
    "battery": ("2차전지", "이차전지", "배터리"),
    "ai": ("ai", "인공지능"),
    "internet": ("인터넷", "플랫폼"),
    "bio": ("바이오", "제약"),
    "defense": ("방산", "방위산업"),
    "auto": ("자동차", "완성차"),
    "finance": ("금융", "은행", "증권주", "보험주"),
    "entertainment": ("엔터", "미디어"),
}


def mentioned_industries(question: str, exclude: set[str]) -> list[str]:
    """질문에 이름이 나온 산업의 id (예: "반도체 회사들" → semiconductor)."""
    text = "".join(question.split()).lower()
    # 회사 이름 속 낱말은 산업으로 보지 않는다 ("삼성전자"의 "전자", "LG화학"의 "화학")
    seeds = data_store._seed_companies()
    for code in mentioned_companies(question, set()):
        text = text.replace("".join(seeds[code].name.split()).lower(), " ")
    found = []
    for record in data_store.industry_records():
        industry_id = record["industry_id"]
        names = INDUSTRY_ALIASES.get(industry_id) or tuple(
            n for n in re.split(r"[·/]", record["name"].lower()) if len(n) >= 2
        )
        if industry_id not in exclude and any(name in text for name in names):
            found.append(industry_id)
    return found[:INDUSTRY_MENTION_MAX]


def _one_month_change(stock_code: str) -> float | None:
    try:
        history = price_service.get_price_history(stock_code, "1m")
    except Exception:  # noqa: BLE001
        return None
    points = history.points
    if history.is_sample or len(points) < 2:
        return None
    return _change(points[-1].close, points[0].close)


def _industry_news_about_price(name: str) -> list[dict[str, Any]]:
    """최근 일주일 동안 이 산업의 주가를 다룬 기사 (예: "반도체주 급락")."""
    try:
        result = news_search.search(f"{name}주 주가", 1, 40, None, "date")
    except Exception as e:  # noqa: BLE001
        logger.warning("챗봇용 산업 기사를 받지 못했어요 (%s): %s", name, e)
        return []
    if result is None:
        return []
    about = [a for a in result.articles if name in a.title and _PRICE_WORDS.search(a.title)]
    return [_article(a) for a in _usable(about)[:INDUSTRY_NEWS]]


def _industry_recent_news(record: dict[str, Any]) -> list[dict[str, Any]]:
    """이 산업의 최근 소식 (산업 화면의 "최근 소식"과 같은 검색어)."""
    query = record.get("news_query") or f"{record['name'].replace('·', ' ')} 산업"
    try:
        articles = news_search.latest(query, INDUSTRY_NEWS * 2)
    except Exception as e:  # noqa: BLE001
        logger.warning("챗봇용 산업 소식을 받지 못했어요 (%s): %s", record["name"], e)
        return []
    usable = _usable(articles or [])
    # 검색어만 스친 기사(시장 전망 등)를 빼고, 제목에 산업 이름이 있는 기사를 먼저 쓴다
    name = record["name"].split("·")[0]
    named = [a for a in usable if name in a.title]
    return [_article(a) for a in (named if len(named) >= 2 else usable)[:INDUSTRY_NEWS]]


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _today_summary(codes: list[str], seeds: dict[str, Any]) -> dict[str, Any] | None:
    """산업에 속한 회사 전체의 오늘 주가를 한눈에: 평균, 오른 곳·떨어진 곳 수, 가장 많이 움직인 곳."""
    try:
        quotes = naver.quotes(codes)
    except Exception as e:  # noqa: BLE001
        logger.warning("챗봇용 산업 시세를 받지 못했어요: %s", e)
        return None
    if not quotes:
        return None
    rates = {code: q.change_rate for code, q in quotes.items()}
    ordered = sorted(rates, key=lambda c: rates[c])

    def named(code_list: list[str]) -> list[dict[str, Any]]:
        return [{"name": seeds[c].name, "change_today_percent": rates[c]} for c in code_list]

    return {
        "price_as_of": max(q.traded_at for q in quotes.values()).date().isoformat(),
        "companies_counted": len(rates),
        "average_change_today_percent": _average(list(rates.values())),
        "companies_that_rose_today": sum(1 for r in rates.values() if r > 0),
        "companies_that_fell_today": sum(1 for r in rates.values() if r < 0),
        "companies_unchanged_today": sum(1 for r in rates.values() if r == 0),
        "fell_the_most_today": named([c for c in ordered[:INDUSTRY_EXTREMES] if rates[c] < 0]),
        "rose_the_most_today": named(
            [c for c in reversed(ordered[-INDUSTRY_EXTREMES:]) if rates[c] > 0]
        ),
    }


def _industry_screen(industry_id: str, wants: set[Want] = frozenset()) -> dict[str, Any] | None:
    record = next(
        (r for r in data_store.industry_records() if r["industry_id"] == industry_id), None
    )
    if record is None:
        return None
    seeds = data_store._seed_companies()  # 시세를 붙이지 않은 시드 (회사 이름·한 줄 소개만 쓴다)
    members = sorted(
        (seeds[c] for c in record["companies"] if c in seeds),
        key=lambda c: c.market_cap.value if c.market_cap else 0,
        reverse=True,
    )
    screen: dict[str, Any] = {
        "name": record["name"],
        "description": record["description"],
        "company_count": len(members),
        "biggest_companies": [
            {"name": c.name, "one_liner": c.one_liner} for c in members[:INDUSTRY_COMPANIES]
        ],
    }
    if not wants & {"overview", "price_move", "news", "level"}:
        return screen
    # 산업 전체가 요즘 어떤지: 속한 회사 전체의 오늘 주가, 큰 회사들의 한 달 흐름, 이 산업 주가를 다룬 기사
    codes = [c.stock_code for c in members[:INDUSTRY_MOVERS]]
    with ThreadPoolExecutor(max_workers=len(codes) + 3) as pool:
        news_job = pool.submit(_industry_news_about_price, record["name"].split("·")[0])
        recent_job = pool.submit(_industry_recent_news, record) if "news" in wants else None
        today_job = pool.submit(_today_summary, [c.stock_code for c in members], seeds)
        month_changes = list(pool.map(_one_month_change, codes))
        screen["all_companies_today"] = today_job.result()
        monthly = [
            {"name": seeds[code].name, "change_over_1_month_percent": month}
            for code, month in zip(codes, month_changes, strict=True)
            if month is not None
        ]
        # 회사를 다 나열하지 않게, 평균과 눈에 띄는 곳(가장 큰 회사, 가장 많이 떨어진·오른 곳)만 넘긴다
        by_change = sorted(monthly, key=lambda m: m["change_over_1_month_percent"])
        notable = monthly[:1] + by_change[:1] + by_change[-1:]
        screen["biggest_companies_over_1_month"] = {
            "companies_counted": len(monthly),
            "average_change_percent": _average([m["change_over_1_month_percent"] for m in monthly]),
            "notable_companies": list({m["name"]: m for m in notable}.values()),
        }
        screen["news_about_this_industry_price"] = news_job.result()
        if recent_job is not None:
            screen["recent_news_about_this_industry"] = recent_job.result()
    return screen


def _indices() -> list[dict[str, Any]]:
    """코스피·코스닥의 오늘 움직임."""
    try:
        quotes = naver_index.quotes()
    except Exception as e:  # noqa: BLE001
        logger.warning("챗봇용 지수를 받지 못했어요: %s", e)
        return []
    return [
        {
            "name": "코스피" if code == "KOSPI" else "코스닥",
            "value": q.value,
            "change_today_percent": q.change_rate,
            "as_of": q.traded_at.date().isoformat(),
            "market_is_open_now": q.is_open,
        }
        for code, q in quotes.items()
    ]


def _market_news() -> list[dict[str, Any]]:
    try:
        news = asyncio.run(market_service.latest_market_news())
    except Exception as e:  # noqa: BLE001
        logger.warning("챗봇용 시장 기사를 받지 못했어요: %s", e)
        return []
    return [_article(n) for n in _usable(list(news))[:MARKET_NEWS]]


def _industry_moves_today() -> dict[str, Any]:
    try:
        rates = sorted(market_service._industry_rates(), key=lambda r: r.change_rate)
    except Exception as e:  # noqa: BLE001
        logger.warning("챗봇용 산업별 등락률을 받지 못했어요: %s", e)
        return {}
    return {
        "industries_that_fell_most_today": [
            {"name": r.name, "average_change_today_percent": r.change_rate}
            for r in rates[:3]
            if r.change_rate < 0
        ],
        "industries_that_rose_most_today": [
            {"name": r.name, "average_change_today_percent": r.change_rate}
            for r in reversed(rates[-3:])
            if r.change_rate > 0
        ],
    }


def _market_screen() -> dict[str, Any] | None:
    """주식 시장 전체: 코스피·코스닥, 시장 기사, 오늘 많이 오르고 떨어진 산업.

    메인 화면의 "오늘의 시장" AI 한마디는 기다리지 않는다 (만드는 데 10초 넘게 걸릴 때가 있다).
    """
    with ThreadPoolExecutor(max_workers=3) as pool:
        indices, news, moves = (
            pool.submit(_indices),
            pool.submit(_market_news),
            pool.submit(_industry_moves_today),
        )
        return {"indices": indices.result(), "market_news": news.result(), **moves.result()}


def _term_screen(term_id: str) -> dict[str, Any] | None:
    term = next((t for t in data_store.get_terms() if t.id == term_id), None)
    if term is None:
        return None
    return {
        "term": term.term,
        "also_called": term.aliases,
        "easy_explanation": term.easy,
        "example": term.example,
    }


# 다진 서비스 자체를 묻는 질문("다진은 어떻게 써요?", "커뮤니티 규칙 알려 줘")에 넘기는 안내
SERVICE_GUIDE = {
    "what_dajin_is": "주식을 처음 시작하는 사람이 기업을 쉬운 말로 이해하도록 돕는 서비스예요. "
    "교육용 정보이고 투자 추천을 하지 않아요.",
    "how_to_use": [
        "첫 화면에서 기업 이름이나 종목코드로 검색하면 기업 화면으로 가요.",
        "기업 화면은 개요, AI 분석, 재무 상세, 뉴스 상세, 커뮤니티, 비교하기 탭으로 나뉘어요. "
        "처음이라면 개요와 AI 분석부터 보면 쉬워요.",
        "비교하기 탭에서는 두 회사의 매출·영업이익·영업이익률·매출 증가율·부채비율을 같은 해 기준으로 나란히 봐요.",
        "하트를 누르면 관심 기업에 담기고, 관심 기업 화면에서 모아 볼 수 있어요.",
        "첫 화면에서 오늘의 시장 한마디, 관심이 몰리는 산업, 거래가 많은 기업을 볼 수 있어요.",
        "점선 밑줄이 있는 낱말을 누르면 쉬운 설명이 나와요.",
    ],
    "how_to_learn_terms": "용어 카드 화면에 낱말퀴즈, 카드 학습, 문제 세 가지가 있어요. "
    "카드로 뜻을 익히고, 문제와 낱말퀴즈로 확인하면 기억에 오래 남아요. 로그인하면 학습 기록이 저장돼요.",
    "community_rules": [
        "종목 추천, 매수·매도 권유, 유료 리딩방 홍보는 금지돼요.",
        "오픈채팅방·텔레그램 같은 외부 채팅방으로 끌어가는 글과 욕설은 쓸 수 없어요.",
        "글과 댓글은 500자까지 쓸 수 있고, 사진과 이모지를 하나씩 붙일 수 있어요.",
        "규칙에 맞지 않는 글은 신고할 수 있고, 신고가 쌓이면 숨겨져요.",
        "글을 쓰려면 Google 로그인이 필요해요.",
    ],
    "adk_points": {
        "what": "앰똥깡(ADK)은 다진 안에서만 쓰는 포인트예요. 실제 돈이 아니고 사거나 바꿀 수 없어요.",
        "earn": f"가입하면 {wallet_service.SIGNUP_BONUS} ADK를 받고, 낱말퀴즈 "
        f"{credit_service.PUZZLES_PER_REWARD}개를 풀 때마다 {wallet_service.PUZZLE_REWARD} ADK를 받아요 "
        f"(하루 {credit_service.DAILY_MAX_REWARDS}번까지).",
        "spend": f"AI 리포트 만들기 {wallet_service.REPORT_PRICE} ADK, "
        f"AI 챗봇 질문 {wallet_service.CHAT_PRICE} ADK예요.",
    },
}
_ABOUT_SERVICE = re.compile(
    r"다진|커뮤니티|규칙|앰똥깡|adk|낱말 ?퀴즈|용어 ?카드|관심 ?기업|사용법|어떻게 ?(쓰|써|사용|이용)"
    r"|(용어|낱말|단어).*(익히|외우|공부|배우)",
    re.IGNORECASE,
)
STARTER_TERMS = 8  # 낱말을 정하지 않고 물었을 때 예로 넘기는 기초 낱말 수


def _starter_terms() -> list[dict[str, Any]]:
    """용어 화면에서 낱말을 정하지 않고 물었을 때("이 용어를 예시로 설명해 줘") 예로 쓸 기초 낱말."""
    wanted = (
        "stock",
        "market_cap",
        "revenue",
        "operating_income",
        "operating_margin",
        "per",
        "pbr",
    )
    terms = {t.id: t for t in data_store.get_terms()}
    picked = [terms[i] for i in wanted if i in terms]
    picked += [t for t in terms.values() if t.category == "basic" and t not in picked]
    return [
        {"term": t.term, "easy_explanation": t.easy, "example": t.example}
        for t in picked[:STARTER_TERMS]
    ]


MENTION_MAX = 3  # 질문에서 찾아 자료를 붙이는 회사 수
MENTION_AMBIGUOUS = (
    5  # 낱말 하나가 이보다 많은 회사 이름에 들어 있으면 회사 이름으로 보지 않는다 (예: "반도체")
)
# 이름과 다르게 부르는 말 (줄임말, 영어 이름의 한글 표기)
NICKNAMES = {
    "삼전": "005930",
    "하닉": "000660",
    "현차": "005380",
    "엘지엔솔": "373220",
    "네이버": "035420",
}
_JOSA = (
    "이랑", "하고", "에서", "으로", "보다", "까지", "부터", "한테", "이나", "이고", "이며",
    "랑", "과", "와", "의", "은", "는", "이", "가", "을", "를", "도", "만", "로", "에",
)  # fmt: skip
_WORD = re.compile(r"[0-9A-Za-z가-힣&]+")


def _without_josa(word: str) -> list[str]:
    """낱말과, 끝의 조사를 뗀 모양들 (삼성전자랑 → 삼성전자)."""
    forms = [word]
    for josa in _JOSA:
        if word.endswith(josa) and len(word) - len(josa) >= 2:
            forms.append(word[: -len(josa)])
    return forms


def mentioned_companies(question: str, exclude: set[str]) -> list[str]:
    """질문에 이름이 나온 회사의 종목코드 (나온 순서, 최대 `MENTION_MAX`).

    회사 이름 전체("삼성전자"), 종목코드, 이름의 일부("하이닉스" → SK하이닉스), 흔한 줄임말("삼전")을 찾는다.
    이름의 일부는 3글자 이상이고 몇 안 되는 회사에만 들어 있을 때만 쓴다. 여러 곳이면 시가총액이 큰 회사.
    """
    seeds = data_store._seed_companies()
    by_name = {c.name.lower(): code for code, c in seeds.items()}
    found: list[str] = []

    def add(code: str | None) -> bool:
        if code and code in seeds and code not in exclude and code not in found:
            found.append(code)
        return bool(code)

    for word in _WORD.findall(question):
        for form in _without_josa(word.lower()):
            if add(by_name.get(form) or NICKNAMES.get(form) or (form if form in seeds else None)):
                break
            if len(form) < 3 or form.isdigit():
                continue
            partial = [code for name, code in by_name.items() if form in name]
            if 0 < len(partial) <= MENTION_AMBIGUOUS:
                partial.sort(
                    key=lambda c: seeds[c].market_cap.value if seeds[c].market_cap else 0,
                    reverse=True,
                )
                add(partial[0])
                break
    return found[:MENTION_MAX]


def resolve_companies(names: list[str]) -> list[str]:
    """회사 이름들(의도 읽기가 정리한 것)을 종목코드로. 찾지 못한 이름은 뺀다."""
    return mentioned_companies(" ".join(names), set()) if names else []


def _company_or_none(
    session: Session,
    stock_code: str | None,
    tab: str | None,
    wants: set[Want] = frozenset(),
    question: str = "",
) -> dict | None:
    if not stock_code:
        return None
    try:
        return _company_screen(session, stock_code, tab, wants, question)
    except AppError:  # 없는 회사
        return None


def _stub(stock_code: str | None) -> dict[str, Any] | None:
    """질문이 화면의 회사에 대한 것이 아닐 때: 어느 화면을 보고 있는지만 알려준다."""
    company = data_store._seed_companies().get(stock_code or "")
    if company is None:
        return None
    return {
        "name": company.name,
        "sector": company.sector,
        "industries": [i.name for i in data_store.industries_of(company.stock_code)],
    }


def screen_summary(context: ChatContext) -> dict[str, Any]:
    """의도 읽기에 넘기는 "지금 보고 있는 화면" 한 줄 정보."""
    seeds = data_store._seed_companies()
    summary: dict[str, Any] = {"page": context.page, "tab": context.tab}
    if context.page in ("company", "report") and context.stock_code in seeds:
        summary["company"] = seeds[context.stock_code].name
    if context.page == "compare":
        summary["companies"] = [
            seeds[c].name for c in (context.right_code, context.left_code) if c in seeds
        ]
    if context.page == "industry":
        summary["industry_id"] = context.industry_id
    return {k: v for k, v in summary.items() if v}


def build_screen(
    session: Session, context: ChatContext, question: str, plan: Plan
) -> dict[str, Any]:
    """AI에게 넘기는 자료. 질문의 의도(`plan`)가 가리키는 회사·산업·시장의 자료를 찾는다.

    - 질문이 화면의 회사·산업에 대한 것이면 그 자료를 깊게, 아니면 어느 화면인지만 알려준다.
      (산업 전체를 물었는데 화면의 회사 숫자만 길게 붙어 있으면 그 회사 얘기만 하게 된다)
    - 찾지 못한 회사·산업·용어는 빼고 넘긴다. 화면 정보가 이상해도 대답은 한다.
    """
    wants = set(plan.wants)
    if _ASKS_NEWS.search(question):
        wants.add("news")  # "뉴스 정리해 줘"는 의도 읽기가 놓쳐도 기사를 찾는다
    asked = list(
        dict.fromkeys(resolve_companies(plan.companies) + mentioned_companies(question, set()))
    )
    asked_industries = list(
        dict.fromkeys(
            [
                i
                for i in plan.industry_ids
                if any(r["industry_id"] == i for r in data_store.industry_records())
            ]
            + mentioned_industries(question, set())
        )
    )[:INDUSTRY_MENTION_MAX]
    # 질문이 산업·시장 전체에 대한 것이면 화면의 회사는 깊게 넘기지 않는다
    about_wider = plan.subject in ("industry", "market") and bool(
        asked_industries or plan.subject == "market"
    )

    screen: dict[str, Any] = {"page": context.page}
    shown: set[str] = set()
    if context.page in ("company", "report"):
        tab = "report" if context.page == "report" else context.tab
        screen["tab"] = tab
        code = context.stock_code or ""
        about_others = plan.subject == "company" and bool(asked) and code not in asked
        if (about_wider or about_others) and code not in asked:
            screen["company_on_screen"] = _stub(code)
        else:
            screen["company"] = _company_or_none(session, code, tab, wants, question)
            shown.add(code)
    elif context.page == "compare":
        codes = [context.right_code or "", context.left_code or ""]
        about_others = plan.subject == "company" and bool(asked) and not set(codes) & set(asked)
        if (about_wider or about_others) and not set(codes) & set(asked):
            screen["companies_on_screen"] = [c for code in codes if (c := _stub(code))]
        else:
            # 비교 화면: 두 회사의 자료를 같이 넘긴다 (숫자는 각자의 재무 자료가 기준)
            screen["companies_being_compared"] = [
                c for code in codes if (c := _company_or_none(session, code, None, wants, question))
            ]
            shown |= set(codes)
    elif context.page == "industry" and context.industry_id:
        if context.industry_id not in asked_industries and not asked and plan.subject != "market":
            asked_industries.insert(0, context.industry_id)  # "이 산업 어때?"
        elif context.industry_id not in asked_industries:
            screen["industry_on_screen"] = (_industry_screen(context.industry_id) or {}).get("name")
    elif context.page == "learn":
        # 프론트는 용어 화면에서 어느 낱말인지 보내지 않을 때가 많다. 그때는 기초 낱말을 예로 넘긴다
        screen["term"] = _term_screen(context.term_id) if context.term_id else None
        if screen["term"] is None:
            screen["basic_terms_for_beginners"] = _starter_terms()
    if _ABOUT_SERVICE.search(question):
        screen["about_dajin_service"] = SERVICE_GUIDE

    # 화면에 없는 회사를 물어도 대답할 수 있게, 질문이 가리키는 회사의 자료를 찾아 붙인다
    others = [code for code in asked if code not in shown][:MENTION_MAX]
    mentioned = [
        c for code in others if (c := _company_or_none(session, code, None, wants, question))
    ]
    if mentioned:
        screen["companies_asked_about"] = mentioned
    # "반도체 회사들", "바이오 산업"처럼 산업을 물으면 그 산업 전체의 자료를 붙인다
    industry_wants = wants | ({"overview"} if plan.subject == "industry" else set())
    industries = [
        i
        for industry_id in asked_industries[:INDUSTRY_MENTION_MAX]
        if (i := _industry_screen(industry_id, industry_wants))
    ]
    if industries:
        screen["industries_asked_about"] = industries
    # 시장 전체를 묻거나, 누구를 가리키는지 없이 "요즘 뭐가 올랐어?"처럼 묻는 질문
    no_target = not asked and not asked_industries and context.page in ("main", "other", "learn")
    if plan.subject == "market" or (no_target and wants & {"price_move", "overview", "news"}):
        screen["stock_market_today"] = _market_screen()
    return {key: value for key, value in screen.items() if value not in (None, [], {})}
