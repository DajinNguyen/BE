"""재무 상세 "사업별 성과"·"왜 이렇게 됐나요?"를 미리 만든다 (화면을 처음 열어도 바로 보이게).

    python -m app.scripts.generate_performance                  # 아직 없거나 새 분기 보고서가 나온 회사
    python -m app.scripts.generate_performance 000660 035420    # 지정한 회사만
    python -m app.scripts.generate_performance --limit 300      # 앞에서부터 300곳 (목록 = 대략 시가총액 순)
    python -m app.scripts.generate_performance --interval 0     # 유료 키면 쉬지 않고

회사마다 AI를 2~3번 부르고(이유 1번, 사업별 성과 1~2번), 사이에 --interval 초(기본 10초)를 쉰다.
AI가 혼잡하면 몇 번 다시 시도하고, 그래도 연속으로 실패하면(하루 한도 등) 멈춘다.
다음에 다시 실행하면 이어서 만든다. 이미 만든 회사는 건너뛴다.
"""

import argparse
import logging
import sys
import time

from app.core.db import init_db, new_session
from app.core.errors import AppError
from app.services import data_store, performance_jobs, performance_service
from app.services.performance_service import get_reason_writer
from app.services.quarter_segment_service import get_quarter_segment_writer

STOP_AFTER_FAILURES = 3  # 연속으로 이만큼 실패하면 한도에 걸린 것으로 보고 멈춘다


def main(codes: list[str], limit: int | None, interval: float) -> int:
    writer, segment_writer = get_reason_writer(), get_quarter_segment_writer()
    if writer is None and segment_writer is None:
        print("AI 키가 없어요. .env 에 GEMINI_API_KEY 또는 ANTHROPIC_API_KEY 를 넣어 주세요.")
        return 1
    init_db()
    chosen = codes or [c.stock_code for c in data_store.all_companies()]
    counts = {"done": 0, "failed": 0, "skipped": 0}
    streak = 0
    made = 0
    for code in chosen:
        if limit is not None and made + counts["skipped"] >= limit:
            break
        name = data_store.get_company(code).name
        try:
            with new_session() as session:
                _, rcept_no = performance_service.get_detail(session, code)
                jobs = performance_jobs.start_jobs(session, code, writer, segment_writer, rcept_no)
        except AppError:  # 실적 자료가 없는 회사
            counts["skipped"] += 1
            continue
        if not jobs:  # 이미 있다
            counts["skipped"] += 1
            continue
        if made and interval:
            time.sleep(interval)
        made += 1
        results = [job() for job in jobs]
        ok = "done" in results
        counts["done" if ok else "failed"] += 1
        streak = 0 if ok else streak + 1
        print(f"{'✓' if ok else '✗'} {code} {name} {results}", flush=True)
        if streak >= STOP_AFTER_FAILURES:
            print(
                "연속으로 실패해서 멈춰요 (AI 하루 한도일 수 있어요). 나중에 같은 명령으로 이어서 만들어요."
            )
            break
    print(
        f"만듦 {counts['done']} · 실패 {counts['failed']} · 건너뜀(이미 있음·자료 없음) {counts['skipped']}"
    )
    return 0 if streak < STOP_AFTER_FAILURES else 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="*", help="종목코드 (없으면 아직 없는 회사 전부)")
    parser.add_argument("--limit", type=int, default=None, help="앞에서부터 몇 곳까지 볼지")
    parser.add_argument("--interval", type=float, default=10.0, help="회사 사이에 쉴 초")
    args = parser.parse_args()
    sys.exit(main(args.codes, args.limit, args.interval))
