"""시드에 개요가 없는 회사의 AI 개요를 미리 만든다.

    python -m app.scripts.generate_overviews                  # 아직 없는 회사 전부
    python -m app.scripts.generate_overviews 000660 035420    # 지정한 회사만 (이미 있어도 다시)
    python -m app.scripts.generate_overviews --limit 10       # 최대 10곳만
    python -m app.scripts.generate_overviews --interval 0     # 유료 키면 쉬지 않고

한 곳씩 차례로 만들고, 사이에 --interval 초(기본 13초)를 쉰다.
Gemini 무료 등급은 분당 5회·하루 20회라서 쉬지 않으면 바로 한도에 걸린다.
AI 사용 한도(429)에 걸리면 나머지도 같은 결과라서 거기서 멈춘다. 다음에 다시 실행하면 이어서 만든다.

DART_API_KEY 와 AI 키(ANTHROPIC_API_KEY 또는 GEMINI_API_KEY)가 .env 에 있어야 한다.
"""

import argparse
import asyncio
import sys

from app.api.deps import get_overview_writer
from app.core.db import init_db, new_session
from app.models.overview import CompanyOverviewRecord
from app.services import data_store, overview_service


def _targets(codes: list[str]) -> list[tuple[str, str]]:
    """(종목코드, 회사명). 코드를 지정하지 않으면 아직 개요가 없는 회사만 (목록 순서 = 대략 시가총액 순)."""
    chosen = codes or [c.stock_code for c in data_store.all_companies()]
    targets = []
    with new_session() as session:
        for code in chosen:
            company = data_store.get_company(code)
            if not overview_service.needs_overview(company) or not company.corp_code:
                continue
            record = session.get(CompanyOverviewRecord, code)
            if not codes and record is not None and record.status == "done":
                continue
            targets.append((code, company.name))
    return targets


async def main(codes: list[str], limit: int | None, interval: float) -> int:
    writer = get_overview_writer()
    if writer is None:
        print("AI 키가 없어요. .env 에 GEMINI_API_KEY 또는 ANTHROPIC_API_KEY 를 넣어 주세요.")
        return 1
    init_db()
    targets = _targets(codes)[:limit]
    print(f"{len(targets)}곳을 만들어요 (사이에 {interval:g}초씩 쉬어요)", flush=True)
    counts = {"done": 0, "failed": 0, "quota": 0}
    for i, (code, name) in enumerate(targets, start=1):
        if i > 1 and interval:
            await asyncio.sleep(interval)
        result = await overview_service.run_generation(code, writer, new_session)
        counts[result] += 1
        mark = {"done": "✓", "failed": "✗", "quota": "⏸"}[result]
        print(f"{mark} [{i}/{len(targets)}] {code} {name}", flush=True)
        if result == "quota":
            print(
                "AI 사용 한도에 걸려서 멈춰요. 한도가 다시 차면 같은 명령으로 이어서 만들 수 있어요."
            )
            break
    print(
        f"만듦 {counts['done']} · 실패 {counts['failed']} · 남음 {len(targets) - sum(counts.values())}"
    )
    return 0 if counts["failed"] == 0 and counts["quota"] == 0 else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="*", help="종목코드 (없으면 아직 없는 회사 전부)")
    parser.add_argument("--limit", type=int, default=None, help="최대 몇 곳까지 만들지")
    parser.add_argument("--interval", type=float, default=13.0, help="회사 사이에 쉴 초")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.codes, args.limit, args.interval)))
