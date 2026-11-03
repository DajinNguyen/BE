"""메인 "오늘의 시장": 지수·한마디·움직인 이유, 시장 뉴스.

네이버 지수를 받으면 실제 지수·당일 흐름(`is_sample: false`)을 쓰고,
한마디·이유는 AI가 지수·산업 등락률·시장 기사를 보고 쓴다 (`market_brief`).
AI 글은 장중에는 30분마다, 장이 끝나면 한 번 더 백그라운드에서 다시 쓰고, 그 사이에는 저장해 둔 글을 쓴다.
AI 글이 아직 없거나 AI 키가 없으면 지수로 만든 기본 문장을 쓰고 이유는 비운다.
산업 등락률은 산업에 속한 회사들의 오늘 등락률 평균이다.

지수를 받지 못하면 `app/data/market.json` 의 예시 값을 쓴다 (`is_sample: true`). 예측·투자 권유 표현은 넣지 않는다.
예시 이유에 붙는 기사는 예시 기사마다 정해 둔 검색어(`query`)로 실제 뉴스를 찾아
가장 잘 맞는 기사로 바꾼다 (받지 못하면 예시 기사). 시장 뉴스 목록도 실제 뉴스 검색을 먼저 쓴다.
"""

import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime

from app.schemas.market import (
    IntradayPoint,
    MarketIndex,
    MarketReason,
    MarketSummary,
    ReasonIndustry,
)
from app.schemas.news import NewsArticle
from app.services import market_brief, naver, naver_index, news_search
from app.services.data_store import industry_records, load_json
from app.services.market_clock import KST, last_close
from app.services.sample_news import latest_first, sample_article

MARKET_NEWS_QUERY = "증시"
MARKET_NEWS_COUNT = 10
INTRADAY_STEPS = 39  # 09:00 ~ 15:30, 10분 간격
BRIEF_TTL = 30 * 60  # 장중 AI 글을 다시 쓰는 간격 (초). flash-lite 하루 한도 안쪽
BRIEF_RETRY = 5 * 60  # AI 글을 만들지 못했으면 이만큼 기다렸다가 다시 해 본다
INDEX_NAMES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}

logger = logging.getLogger(__name__)


def _intraday(open_value: float, close: float, seed: int) -> list[IntradayPoint]:
    """예시 당일 흐름. 마지막 값은 종가와 같다."""
    points = []
    for i in range(INTRADAY_STEPS + 1):
        minutes = 9 * 60 + i * 10
        wave = math.sin(i * 0.7 + seed) * (abs(close - open_value) * 0.25 + 2)
        value = (
            close
            if i == INTRADAY_STEPS
            else open_value + (close - open_value) * i / INTRADAY_STEPS + wave
        )
        points.append(
            IntradayPoint(time=f"{minutes // 60:02d}:{minutes % 60:02d}", value=round(value, 2))
        )
    return points


def market_news(now: datetime | None = None) -> list[NewsArticle]:
    """시장 기사 (최신순). 예시 기사라 날짜와 상관없이 같은 목록이다."""
    return latest_first(
        [
            sample_article(
                article_id=n["id"],
                title=n["title"],
                summary=n["summary"],
                hours_ago=n["hours_ago"],
                url=f"https://example.com/market/{n['id']}",
                now=now,
            )
            for n in load_json("market.json")["news"]
        ]
    )


def latest_market_news(now: datetime | None = None) -> list[NewsArticle]:
    """시장 뉴스 목록. 실제 뉴스를 받지 못하면 예시 기사."""
    live = news_search.latest(MARKET_NEWS_QUERY, MARKET_NEWS_COUNT)
    return live if live is not None else market_news(now)


def reason_news(now: datetime | None = None) -> dict[str, NewsArticle]:
    """예시 기사 id → 이유 카드에 붙일 기사. 검색은 동시에 한다."""
    samples = market_news(now)
    queries = {n["id"]: n.get("query") or n["title"] for n in load_json("market.json")["news"]}
    with ThreadPoolExecutor(max_workers=len(samples) or 1) as pool:
        found = list(pool.map(news_search.most_relevant, [queries[s.id] for s in samples]))
    return {sample.id: live or sample for sample, live in zip(samples, found, strict=True)}


# ---------------------------------------------------------------- 실제 지수 + AI 한마디


@dataclass(frozen=True)
class _Brief:
    day: date  # 지수 거래일
    final: bool  # 장이 끝난 뒤 쓴 글이면 true (그날은 더 다시 쓰지 않는다)
    made_at: float
    headline: str
    reasons: list[MarketReason]


class _BriefStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.brief: _Brief | None = None
        self.running = False
        self.retry_at = 0.0


_store = _BriefStore()


def _run_in_background(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True).start()


def _industry_rates() -> list[ReasonIndustry]:
    """산업별 오늘 등락률: 속한 회사들의 등락률 평균. 시세를 받은 회사가 없는 산업은 뺀다."""
    records = industry_records()
    quotes = naver.quotes(sorted({c for r in records for c in r["companies"]}))
    result = []
    for r in records:
        rates = [quotes[c].change_rate for c in r["companies"] if c in quotes]
        if rates:
            result.append(
                ReasonIndustry(
                    industry_id=r["industry_id"],
                    name=r["name"],
                    change_rate=round(sum(rates) / len(rates), 2),
                )
            )
    return result


def _write_brief(day: date, is_open: bool, indices: list[MarketIndex]) -> None:
    """AI에게 한마디·이유를 받아 저장한다 (백그라운드). 못 받으면 잠시 뒤 다시 해 본다."""
    try:
        writer = market_brief.get_brief_writer()
        industries = _industry_rates()
        news = news_search.latest(MARKET_NEWS_QUERY, MARKET_NEWS_COUNT) or []
        context = {
            "is_open": is_open,
            "indices": [
                {
                    "name": INDEX_NAMES[i.code],
                    "value": i.value,
                    "change": i.change,
                    "change_rate": i.change_rate,
                }
                for i in indices
            ],
            "industries": [i.model_dump() for i in industries],
            "news": [
                {"number": n, "title": a.title, "summary": a.summary_short, "press": a.press}
                for n, a in enumerate(news, start=1)
            ],
        }
        content = writer.write(context) if writer else None
        if content is None or not content.headline.strip():
            _store.retry_at = time.time() + BRIEF_RETRY
            return
        by_id = {i.industry_id: i for i in industries}
        reasons = [
            MarketReason(
                title=r.title,
                description=r.description,
                industries=[by_id[i] for i in dict.fromkeys(r.industry_ids) if i in by_id][:2],
                news=[news[n - 1] for n in dict.fromkeys(r.news_numbers) if 1 <= n <= len(news)][
                    :2
                ],
            )
            for r in content.reasons[:3]
            if r.title.strip()
        ]
        _store.brief = _Brief(
            day=day,
            final=not is_open,
            made_at=time.time(),
            headline=content.headline.strip(),
            reasons=reasons,
        )
    except Exception:  # noqa: BLE001 - 백그라운드 작업이라 실패해도 화면은 기본 문장으로 뜬다
        logger.exception("시장 한마디를 만들지 못했어요")
        _store.retry_at = time.time() + BRIEF_RETRY
    finally:
        _store.running = False


def _current_brief(day: date, is_open: bool, indices: list[MarketIndex]) -> _Brief | None:
    """그날 저장한 AI 글. 오래됐으면 백그라운드에서 새로 쓰게 하고, 그동안은 있던 글을 쓴다."""
    brief = _store.brief
    same_day = brief is not None and brief.day == day
    fresh = same_day and (brief.final or (is_open and time.time() - brief.made_at < BRIEF_TTL))
    if not fresh and market_brief.get_brief_writer() and time.time() >= _store.retry_at:
        with _store.lock:
            start = not _store.running
            _store.running = True
        if start:
            _run_in_background(lambda: _write_brief(day, is_open, indices))
    return brief if same_day else None


def _change_text(rate: float, is_open: bool, last: bool) -> str:
    if rate == 0:
        return "제자리예요" if last else "제자리이고"
    verb = (
        ("오르고 있" if is_open else "올랐") if rate > 0 else ("내리고 있" if is_open else "내렸")
    )
    return f"{abs(rate):g}% {verb}{'어요' if last else '고'}"


def _plain_headline(indices: list[MarketIndex], is_open: bool) -> str:
    """AI 글이 없을 때: "코스피는 1.2% 올랐고, 코스닥은 0.3% 내렸어요" """
    parts = []
    for n, i in enumerate(indices):
        name = INDEX_NAMES[i.code]
        particle = "는" if name == "코스피" else "은"
        parts.append(
            f"{name}{particle} {_change_text(i.change_rate, is_open, n == len(indices) - 1)}"
        )
    return ", ".join(parts)


def _live_summary(now: datetime | None = None) -> MarketSummary | None:
    """네이버 지수로 만든 응답. 지수를 다 받지 못하면 None (예시 값을 쓴다)."""
    quotes = naver_index.quotes()
    if any(code not in quotes for code in naver_index.INDEX_CODES):
        return None
    picked = [quotes[code] for code in naver_index.INDEX_CODES]
    traded_at = max(q.traded_at for q in picked).astimezone(KST)
    is_open = any(q.is_open for q in picked)
    indices = []
    for q in picked:
        points = naver_index.intraday(q.code, traded_at.date())
        indices.append(
            MarketIndex(
                code=q.code,
                value=q.value,
                change=q.change,
                change_rate=q.change_rate,
                is_sample=False,
                intraday=[IntradayPoint(time=t, value=v) for t, v in points] if points else None,
            )
        )
    brief = _current_brief(traded_at.date(), is_open, indices)
    today = (now or datetime.now(KST)).astimezone(KST).date()
    return MarketSummary(
        as_of=traded_at.isoformat(),
        is_previous_session=traded_at.date() != today,
        headline=brief.headline if brief else _plain_headline(indices, is_open),
        indices=indices,
        reasons=brief.reasons if brief else [],
    )


# ---------------------------------------------------------------- 예시 값


def get_summary(now: datetime | None = None) -> MarketSummary:
    live = _live_summary(now)
    if live is not None:
        return live
    data = load_json("market.json")
    as_of, is_previous = last_close(now)
    news = reason_news(now)
    return MarketSummary(
        as_of=as_of,
        is_previous_session=is_previous,
        headline=data["headline"],
        indices=[
            MarketIndex(
                code=i["code"],
                value=i["value"],
                change=i["change"],
                change_rate=i["change_rate"],
                is_sample=True,
                intraday=_intraday(i["open"], i["value"], seed),
            )
            for seed, i in enumerate(data["indices"], start=1)
        ],
        reasons=[
            MarketReason(
                title=r["title"],
                description=r["description"],
                industries=[ReasonIndustry(**ind) for ind in r["industries"]],
                news=[news[i] for i in r["news_ids"] if i in news],
            )
            for r in data["reasons"]
        ],
    )
