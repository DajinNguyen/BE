"""재무 시드에 사업별 매출이 없는 회사의 "무엇으로 돈을 버나요?"를 미리 만든다.

    python -m app.scripts.generate_segments                  # 아직 없거나 새 사업보고서가 나온 회사
    python -m app.scripts.generate_segments 000660 035420    # 지정한 회사만 (이미 있어도 다시)
    python -m app.scripts.generate_segments --limit 10       # 최대 10곳만
    python -m app.scripts.generate_segments --interval 0     # 유료 키면 쉬지 않고

한 곳씩 차례로 만들고, 사이에 --interval 초(기본 6초)를 쉰다 (AI 분당 한도).
DART_API_KEY 와 AI 키(ANTHROPIC_API_KEY 또는 GEMINI_API_KEY)가 .env 에 있어야 한다.
"""

import argparse
import sys
import time

from app.core.db import init_db, new_session
from app.models.overview import CompanySegmentsRecord
from app.services import data_store, segment_service


def _targets(codes: list[str]) -> list[tuple[str, str]]:
    """(종목코드, 회사명). 코드를 지정하지 않으면 아직 없거나 새 사업보고서가 나온 회사만."""
    chosen = codes or [c.stock_code for c in data_store.all_companies()]
    targets = []
    with new_session() as session:
        for code in chosen:
            company = data_store.get_company(code)
            financials = data_store.find_financials(code)
            if not company.corp_code or (
                financials and not segment_service.needs_segments(financials)
            ):
                continue
            record = session.get(CompanySegmentsRecord, code)
            if (
                not codes
                and record is not None
                # AI 오류(한도 등)로 실패한 회사는 다시, 표가 없던 회사는 새 사업보고서가 나올 때까지 건너뛴다
                and (
                    record.status == "done"
                    or record.failure_reason == segment_service.NOT_FOUND_REASON
                )
                and not segment_service.is_outdated(record, company.corp_code)
            ):
                continue
            targets.append((code, company.name))
    return targets


def main(codes: list[str], limit: int | None, interval: float) -> int:
    writer = segment_service.get_segment_writer()
    if writer is None:
        print("AI 키가 없어요. .env 에 GEMINI_API_KEY 또는 ANTHROPIC_API_KEY 를 넣어 주세요.")
        return 1
    init_db()
    targets = _targets(codes)[:limit]
    print(f"{len(targets)}곳을 만들어요 (사이에 {interval:g}초씩 쉬어요)", flush=True)
    done = 0
    for i, (code, name) in enumerate(targets, start=1):
        if i > 1 and interval:
            time.sleep(interval)
        result = segment_service.run_generation(code, writer, new_session)
        done += result == "done"
        print(f"{'✓' if result == 'done' else '✗'} [{i}/{len(targets)}] {code} {name}", flush=True)
    print(f"만듦 {done} · 못 만듦 {len(targets) - done}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="*", help="종목코드 (없으면 아직 없는 회사 전부)")
    parser.add_argument("--limit", type=int, default=None, help="최대 몇 곳까지 만들지")
    parser.add_argument("--interval", type=float, default=6.0, help="회사 사이에 쉴 초")
    args = parser.parse_args()
    sys.exit(main(args.codes, args.limit, args.interval))
