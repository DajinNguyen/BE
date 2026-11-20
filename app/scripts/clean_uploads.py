"""어떤 글·댓글도 쓰지 않는 사진 파일(고아 파일)을 찾아서 지운다.

사진은 먼저 올리고 나중에 글에 붙이기 때문에, 올리기만 하고 글을 쓰지 않았거나 글을 쓰다 나가면
파일만 남는다. 기본은 목록만 보여 주고(지우지 않는다), `--delete` 를 줘야 지운다.

    python -m app.scripts.clean_uploads                  # 목록만 (지우지 않는다)
    python -m app.scripts.clean_uploads --delete         # 24시간이 지난 고아 파일을 지운다
    python -m app.scripts.clean_uploads --delete --hours 6

방금 올려서 아직 글에 붙이기 전인 사진을 지우지 않도록, 올린 지 `--hours`(기본 24)시간이 지난 것만 본다.
Docker 로 돌릴 때는 `docker compose exec backend python -m app.scripts.clean_uploads --delete`.
"""

import argparse

from app.core.db import new_session
from app.services import upload_service


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--delete", action="store_true", help="목록만 보지 않고 실제로 지운다")
    parser.add_argument(
        "--hours", type=float, default=24, help="올린 지 이 시간이 지난 것만 (기본 24)"
    )
    args = parser.parse_args()

    with new_session() as session:
        seconds = args.hours * 60 * 60
        if args.delete:
            names = upload_service.delete_orphans(session, seconds)
            print(f"고아 사진 {len(names)}장을 지웠어요.")
        else:
            names = upload_service.orphan_files(session, seconds)
            print(f"고아 사진 {len(names)}장이 있어요 (지우지 않았어요. 지우려면 --delete).")
        for name in names:
            print(" ", name)


if __name__ == "__main__":
    main()
