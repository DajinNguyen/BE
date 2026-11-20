"""동시에 요청이 와도 ADK 가 한 번만 빠지고·모자란 만큼 더 쓰이지 않는지, 서버가 멈추지 않는지 본다.

ADK 는 돈과 같아서 같은 요청의 재전송·동시 요청에 안전해야 한다.
테스트용 메모리 DB 는 연결이 하나라 진짜 동시성을 볼 수 없어서, 이 파일의 ADK 테스트는
스레드마다 다른 연결로 부른다. SQLite 파일 DB 로 한 번, PostgreSQL 로 한 번 돈다.

PostgreSQL 은 `TEST_POSTGRES_URL` 이 있을 때만 돈다 (없으면 건너뛴다). 테스트가 테이블을 모두 만들고 지우므로
**운영 DB 주소를 절대 넣지 마라** — 로컬(localhost/127.0.0.1)의 이름에 "test" 가 든 DB 만 받는다.

    docker run -d --rm --name dajin-pgtest -e POSTGRES_USER=pgtest -e POSTGRES_PASSWORD=pgtest-only \\
        -e POSTGRES_DB=pgtest -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:16-alpine
    TEST_POSTGRES_URL=postgresql+psycopg://pgtest:pgtest-only@127.0.0.1:55432/pgtest pytest tests/test_concurrency.py
"""

import asyncio
import contextlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import AppError
from app.main import app
from app.models.user import User
from app.models.wallet import AdkEntry
from app.services import news_search, wallet_service

THREADS = 12


POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")


def _check_test_database(url: str) -> None:
    """테스트가 테이블을 지우므로, 로컬의 '테스트용' DB 가 아니면 시작도 하지 않는다."""
    parsed = make_url(url)
    local = parsed.host in ("localhost", "127.0.0.1", "::1")
    if not (local and "test" in (parsed.database or "")):
        pytest.fail(
            f"TEST_POSTGRES_URL 은 로컬의 이름에 'test' 가 든 DB 여야 해요: {parsed.host}/{parsed.database}"
        )


@pytest.fixture(params=["sqlite", "postgres"])
def db(request, tmp_path):
    if request.param == "sqlite":
        engine = create_engine(
            f"sqlite:///{tmp_path / 'race.db'}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
    else:
        if not POSTGRES_URL:
            pytest.skip("TEST_POSTGRES_URL 이 없어서 PostgreSQL 동시성 테스트를 건너뛰어요")
        _check_test_database(POSTGRES_URL)
        engine = create_engine(POSTGRES_URL, pool_size=THREADS + 2, pool_pre_ping=True)
        SQLModel.metadata.drop_all(engine)
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
    if request.param == "postgres":
        SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def no_process_lock(monkeypatch, db):
    """프로세스 안 잠금을 끈다: 서버를 여러 개 띄웠을 때처럼 DB 가 혼자 막아야 하는 상황.

    PostgreSQL 의 사용자 행 잠금(SELECT … FOR UPDATE)이 제대로 일하는지 본다 (SQLite 는 행 잠금이 없다).
    """
    monkeypatch.setattr(
        wallet_service, "_locks", __import__("collections").defaultdict(contextlib.nullcontext)
    )


def _run_together(engine, user_id, action):
    """THREADS 개의 스레드가 (각자 다른 DB 연결로) 같이 출발해서 action 을 한다. (결과 또는 오류) 목록."""
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


# ---------------------------------------------------------------- 환불·확정 경합


def _fund(engine, user_id):
    with Session(engine) as session:
        wallet_service.balance(session, user_id)  # 가입 선물 50


def _hold(engine, user_id, ref="report:A", price=7):
    with Session(engine) as session:
        return wallet_service.hold(session, user_id, price, "report", "AI 리포트", ref)


def _balance(engine, user_id):
    with Session(engine) as session:
        return wallet_service.balance(session, user_id)


def test_same_hold_released_at_once_is_refunded_once(db):
    engine, user_id = db
    _fund(engine, user_id)
    hold_id = _hold(engine, user_id)
    assert _balance(engine, user_id) == 43
    results = _run_together(engine, user_id, lambda s, uid, i: wallet_service.release(s, hold_id))
    assert not [r for r in results if isinstance(r, Exception)]
    assert _balance(engine, user_id) == 50  # 7 이 한 번만 돌아온다 (더 불어나지 않는다)
    assert _entries(engine, user_id, "report:A") == []


def test_same_hold_settled_at_once_is_charged_once(db):
    engine, user_id = db
    _fund(engine, user_id)
    hold_id = _hold(engine, user_id)
    results = _run_together(engine, user_id, lambda s, uid, i: wallet_service.settle(s, hold_id))
    assert not [r for r in results if isinstance(r, Exception)]
    assert _balance(engine, user_id) == 43
    (entry,) = _entries(engine, user_id, "report:A")
    assert entry.status == "done"


def test_settle_and_release_racing_on_the_same_hold_end_consistent(db):
    """확정과 환불이 동시에 와도: 오류가 나지 않고, 결과는 '차감됨' 아니면 '환불됨' 둘 중 하나."""
    engine, user_id = db
    _fund(engine, user_id)
    hold_id = _hold(engine, user_id)

    def action(session, uid, i):
        return (wallet_service.settle if i % 2 else wallet_service.release)(session, hold_id)

    results = _run_together(engine, user_id, action)
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, errors
    assert _balance(engine, user_id) in (43, 50)
    assert len(_entries(engine, user_id, "report:A")) <= 1


def test_refunded_hold_cannot_be_settled_later(db):
    """AI 가 실패해서 돌려준 뒤에 늦게 온 '확정'이 있어도, 다시 차감되거나 오류가 나지 않는다."""
    engine, user_id = db
    _fund(engine, user_id)
    hold_id = _hold(engine, user_id)
    with Session(engine) as session:
        wallet_service.release(session, hold_id)
    with Session(engine) as session:
        wallet_service.settle(session, hold_id)  # 이미 없는 잡아 둔 금액
    assert _balance(engine, user_id) == 50


def test_stale_hold_is_returned_and_cannot_be_charged_later(db):
    """10분이 지나도록 결과가 없던 잡아 둔 금액은 돌려놓는다."""
    engine, user_id = db
    _fund(engine, user_id)
    hold_id = _hold(engine, user_id)
    with Session(engine) as session:
        entry = session.get(AdkEntry, hold_id)
        entry.created_at = (
            datetime.now(UTC) - wallet_service.HOLD_TTL - wallet_service.timedelta(seconds=1)
        )
        session.add(entry)
        session.commit()
    assert _balance(engine, user_id) == 50  # 잔액을 볼 때 오래된 hold 가 정리된다
    with Session(engine) as session:
        wallet_service.settle(session, hold_id)  # 늦게 도착한 확정은 아무 일도 하지 않는다
    assert _balance(engine, user_id) == 50


def test_mixed_flow_never_loses_or_creates_adk(db):
    """12건이 동시에 잡고, 절반은 확정·절반은 환불: 확정한 만큼만 빠지고 잔액은 음수가 되지 않는다."""
    engine, user_id = db
    _fund(engine, user_id)

    def flow(session, uid, i):
        hold_id = wallet_service.hold(session, uid, 3, "report", "AI 리포트", f"report:{i}")
        (wallet_service.settle if i % 2 == 0 else wallet_service.release)(session, hold_id)
        return i

    results = _run_together(engine, user_id, flow)
    assert not [r for r in results if isinstance(r, Exception)], results
    settled = THREADS // 2
    assert _balance(engine, user_id) == 50 - 3 * settled
    assert all(e.status == "done" for e in _entries(engine, user_id) if e.amount < 0)


@pytest.mark.usefixtures("no_process_lock")
def test_postgres_row_lock_alone_prevents_overspending(db):
    """프로세스 안 잠금 없이(서버가 여러 개일 때처럼) 7 ADK 12건을 동시에 잡아도 잔액을 넘지 않는다."""
    engine, user_id = db
    if engine.dialect.name != "postgresql":
        pytest.skip("SQLite 에는 행 잠금(FOR UPDATE)이 없어서 PostgreSQL 에서만 확인해요")
    _fund(engine, user_id)
    results = _run_together(
        engine,
        user_id,
        lambda s, uid, i: wallet_service.hold(s, uid, 7, "report", "AI 리포트", f"report:{i}"),
    )
    other = [r for r in results if isinstance(r, Exception) and not isinstance(r, AppError)]
    assert not other, other
    granted = [r for r in results if isinstance(r, int)]
    assert len(granted) == 7  # 50 ADK 로 7 ADK 는 7번까지
    assert _balance(engine, user_id) == 1
