"""사진 파일이 업로드 규칙을 통과하는지 이 컴퓨터에서만 확인한다 (서버로 보내지 않고 파일도 바꾸지 않는다).

실제 휴대폰 사진(아이폰·갤럭시, 모션 사진, 큰 사진)이 올라가는지 시험할 때 쓴다:

    python -m app.scripts.check_images ~/Downloads/*.jpg

파일마다 형식, 크기, 통과 여부, 지운 바이트, 회전 정보를 남겼는지, 위치(GPS)가 지워졌는지를 보여 준다.
"""

import sys
from pathlib import Path

from app.services import image_clean, upload_service

_KINDS = {".jpg": "jpg", ".jpeg": "jpg", ".png": "png", ".webp": "webp"}


def _exif_orientation(data: bytes) -> int | None:
    start = data.find(b"Exif\x00\x00")
    return image_clean._orientation(data[start + 6 :]) if start >= 0 else None


def check(path: Path) -> dict:
    data = path.read_bytes()
    kind = upload_service._kind(data)
    result: dict = {
        "file": path.name,
        "kind": kind or _KINDS.get(path.suffix.lower(), "?"),
        "mb": round(len(data) / 1024 / 1024, 2),
        "too_big": len(data) > upload_service.MAX_BYTES,
    }
    if kind is None:
        result.update(ok=False, reason="jpg·png·webp 가 아니에요 (HEIC 등은 올릴 수 없어요)")
        return result
    try:
        cleaned = image_clean.clean(kind, data)
    except ValueError as e:
        result.update(ok=False, reason=f"구조를 읽을 수 없어요: {e}")
        return result
    result.update(
        ok=True,
        removed_kb=round((len(data) - len(cleaned)) / 1024, 1),
        orientation_before=_exif_orientation(data),
        orientation_after=_exif_orientation(cleaned),
        gps_before=b"GPS" in data,
        gps_after=b"GPS" in cleaned,
    )
    return result


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    failed = 0
    for name in argv:
        r = check(Path(name).expanduser())
        status = "통과" if r["ok"] and not r["too_big"] else "실패"
        failed += status == "실패"
        notes = []
        if r["too_big"]:
            notes.append(f"{r['mb']}MB: 5MB 제한을 넘어요")
        if not r["ok"]:
            notes.append(r["reason"])
        else:
            notes.append(f"메타데이터 {r['removed_kb']}KB 제거")
            if r["orientation_before"]:
                same = r["orientation_before"] == r["orientation_after"]
                notes.append(f"회전 {r['orientation_before']} {'유지' if same else '잃음!'}")
            if r["gps_before"]:
                notes.append("GPS 남음!" if r["gps_after"] else "GPS 제거")
        print(f"[{status}] {r['file']} ({r['kind']}, {r['mb']}MB) — {', '.join(notes)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
