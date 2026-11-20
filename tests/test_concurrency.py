"""동시에 요청이 와도 ADK 가 한 번만 빠지고·모자란 만큼 더 쓰이지 않는지, 서버가 멈추지 않는지 본다.

ADK 는 돈과 같아서 같은 요청의 재전송·동시 요청에 안전해야 한다.
테스트용 메모리 DB 는 연결이 하나라 진짜 동시성을 볼 수 없어서, 이 파일의 ADK 테스트는
파일 DB(SQLite)를 따로 만들어 스레드마다 다른 연결로 부른다.
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import AppError
from app.main import app
from app.models.user import User
from app.models.wallet import AdkEntry
from app.services import news_search, wallet_service

THREADS = 12


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(
            google_sub="sub-race",
            email="r@example.com",
            name="경주",
            created_at=datetime.now(UTC),
            last_login_at=datetime.now(UTC),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id
    yield engine, user_id
    engine.dispose()


def _run_together(engine, user_id, action):
    """THREADS 개의 스레드가 (각자 다른 DB 연결로) 같이 출발해서 action 을 한다. (결과 또는 오류) 목록."""
    import threading

    barrier = threading.Barrier(THREADS)

    def worker(i):
        with Session(engine) as session:
            barrier.wait()
            try:
                return action(session, user_id, i)
            except Exception as e:  # noqa: BLE001 - 어떤 오류가 났는지 그대로 모은다
                return e

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        return list(pool.map(worker, range(THREADS)))


def _entries(engine, user_id, ref=None):
    with Session(engine) as session:
        query = select(AdkEntry).where(AdkEntry.user_id == user_id)
        if ref:
            query = query.where(AdkEntry.ref == ref)
        return session.exec(query).all()


def test_same_reward_requested_at_once_is_given_once(db):
    engine, user_id = db
    results = _run_together(
        engine,
        user_id,
        lambda s, uid, i: wallet_service.grant(
            s, uid, 20, "puzzle", "퍼즐 보상", "puzzle:2026-11-20:1"
        ),
    )
    assert not [r for r in results if isinstance(r, Exception)]
    assert results.count(True) == 1 and results.count(False) == THREADS - 1
    assert len(_entries(engine, user_id, "puzzle:2026-11-20:1")) == 1


def test_signup_bonus_requested_at_once_is_given_once(db):
    engine, user_id = db
    results = _run_together(engine, user_id, lambda s, uid, i: wallet_service.balance(s, uid))
    assert not [r for r in results if isinstance(r, Exception)]
    assert len(_entries(engine, user_id, wallet_service.SIGNUP_REF)) == 1
    assert set(results) == {wallet_service.SIGNUP_BONUS}


def test_same_spend_requested_at_once_takes_adk_once(db):
    engine, user_id = db
    with Session(engine) as session:
        wallet_service.balance(session, user_id)  # 가입 선물 50
    results = _run_together(
        engine,
        user_id,
        lambda s, uid, i: wallet_service.spend(s, uid, 7, "report", "AI 리포트", "spend:report:A"),
    )
    assert not [r for r in results if isinstance(r, Exception)]  # 500 이 나지 않는다
    assert set(results) == {43}
    assert len(_entries(engine, user_id, "spend:report:A")) == 1


def test_concurrent_spends_never_go_below_zero(db):
    """잔액 50 에 7 ADK 짜리 12건을 동시에 (서로 다른 ref): 7건(49)까지만 되고 나머지는 모자라다고 거절한다."""
    engine, user_id = db
    with Session(engine) as session:
        wallet_service.balance(session, user_id)
    results = _run_together(
        engine,
        user_id,
        lambda s, uid, i: wallet_service.hold(s, uid, 7, "report", "AI 리포트", f"report:{i}"),
    )
    refused = [r for r in results if isinstance(r, AppError)]
    other_errors = [r for r in results if isinstance(r, Exception) and not isinstance(r, AppError)]
    assert not other_errors
    assert len(refused) == THREADS - 7 and all(e.code == "INSUFFICIENT_BALANCE" for e in refused)
    with Session(engine) as session:
        assert wallet_service.balance(session, user_id) == 1  # 50 - 7*7
        total = session.exec(
            select(func.sum(AdkEntry.amount)).where(AdkEntry.user_id == user_id)
        ).one()
        assert total >= 0


def test_slow_news_search_does_not_freeze_other_requests(client, monkeypatch):
    """뉴스 검색은 동기 HTTP 호출이다. 서버가 그동안 멈추면 다른 사용자의 가벼운 요청도 같이 기다린다."""
    delay = 0.6

    def slow_search(query, start, size, stock_code):
        time.sleep(delay)  # 느린 외부 뉴스 서버
        return None

    monkeypatch.setattr(news_search, "company_news", slow_search)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            started = time.perf_counter()
            slow = asyncio.create_task(http.get("/api/companies/005930/news"))
            await asyncio.sleep(0.1)  # 느린 요청이 먼저 들어가 기다리는 중
            health = await http.get("/api/health")
            # 느린 요청이 서버를 막았다면 가벼운 요청은 delay 가 지난 뒤에야 끝난다
            health_seconds = time.perf_counter() - started
            return health.status_code, health_seconds, (await slow).status_code

    health_status, health_seconds, news_status = asyncio.run(scenario())
    assert (health_status, news_status) == (200, 200)
    assert health_seconds < delay * 0.8  # 막혔다면 delay 이상 걸린다
