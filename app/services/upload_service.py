"""커뮤니티 사진 올리기. 규칙은 프론트 `frontend/src/config/communityRules.ts` 의 IMAGE_UPLOAD 와 같다.

올린 사진은 `upload_dir` 에 저장하고 `/uploads/<이름>` 으로 보여준다.
"""

import logging
import re
import time
import uuid
from pathlib import Path

from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.errors import invalid_image
from app.models.community import Post, PostComment
from app.services import image_clean

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
URL_PATH = "/uploads/"
_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")


def upload_dir() -> Path:
    path = Path(get_settings().upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _kind(data: bytes) -> str | None:
    """파일 앞부분으로 형식을 본다 (Content-Type 은 보내는 쪽이 마음대로 바꿀 수 있다)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_image(data: bytes) -> str:
    """사진을 저장하고 파일 이름을 돌려준다."""
    if len(data) > MAX_BYTES:
        raise invalid_image(f"{MAX_BYTES // 1024 // 1024}MB 이하 사진만 올릴 수 있어요.")
    kind = _kind(data)
    if kind is None:
        raise invalid_image("jpg·png·webp 사진만 올릴 수 있어요.")
    try:
        # 위치(GPS) 같은 메타데이터를 지우고, 사진 구조가 맞는지·뒤에 다른 내용이 붙지 않았는지 본다
        data = image_clean.clean(kind, data)
    except ValueError:
        raise invalid_image("사진을 읽을 수 없어요. 다른 사진으로 올려 주세요.") from None
    name = f"{uuid.uuid4().hex}.{kind}"
    (upload_dir() / name).write_bytes(data)
    return name


def public_base(request_base_url: str) -> str:
    """사진 주소의 앞부분. 설정(PUBLIC_BASE_URL)이 있으면 그걸 쓰고, 없을 때만 요청의 주소를 쓴다.

    요청의 Host 헤더는 보내는 쪽이 바꿀 수 있어서, 그걸로 만든 주소가 글에 저장되면
    열람자의 브라우저가 엉뚱한 서버에서 사진을 받아 가게 만들 수 있다.
    """
    return get_settings().public_base_url.strip() or request_base_url


def image_url(base_url: str, name: str) -> str:
    return f"{base_url.rstrip('/')}{URL_PATH}{name}"


def check_image_url(url: str | None, base_url: str) -> str | None:
    """이 서버에 올린 사진 주소만 받는다 (다른 사이트 사진 주소는 막는다)."""
    if not url:
        return None
    prefix = image_url(base_url, "")
    name = url.removeprefix(prefix) if url.startswith(prefix) else ""
    if not _NAME.match(name) or not (upload_dir() / name).is_file():
        raise invalid_image("사진을 다시 올려 주세요.")
    return url


# ---------------------------------------------------------------- 사진 파일의 수명
# 글·댓글을 지우거나 사진을 바꾸면 파일도 지운다. 같은 사진 주소를 다른 글이 쓰고 있으면 남긴다.


def _file_name(url: str | None) -> str | None:
    name = url.rsplit("/", 1)[-1] if url else ""
    return name if _NAME.match(name) else None


def _referenced(session: Session, names: set[str]) -> set[str]:
    """names 중 아직 어떤 글·댓글이 쓰고 있는 파일 이름."""
    used: set[str] = set()
    for name in names:
        for model in (Post, PostComment):
            hit = session.exec(
                select(model.id).where(col(model.image_url).like(f"%{URL_PATH}{name}")).limit(1)
            ).first()
            if hit is not None:
                used.add(name)
                break
    return used


def _unlink(name: str) -> None:
    try:
        (upload_dir() / name).unlink(missing_ok=True)
    except OSError as e:  # 파일을 못 지워도 글·댓글 삭제는 끝낸다 (고아 파일은 정리 작업이 지운다)
        logger.warning("사진 파일을 지우지 못했어요: %s (%s)", name, e)


def remove_unreferenced(session: Session, urls: list[str | None]) -> None:
    """글·댓글을 지운 뒤(DB 저장 뒤) 부른다. 더 쓰는 곳이 없는 사진 파일만 지운다."""
    names = {n for n in map(_file_name, urls) if n}
    if not names:
        return
    for name in names - _referenced(session, names):
        _unlink(name)


def orphan_files(session: Session, older_than_seconds: float = 24 * 60 * 60) -> list[str]:
    """어떤 글·댓글도 쓰지 않는 사진 파일 이름.

    방금 올려서 아직 글에 붙이기 전인 사진을 지우지 않도록, 올린 지 `older_than_seconds` 가
    지난 것만 본다.
    """
    cutoff = time.time() - older_than_seconds
    files = [
        p.name
        for p in upload_dir().iterdir()
        if _NAME.match(p.name) and p.is_file() and p.stat().st_mtime < cutoff
    ]
    return sorted(set(files) - _referenced(session, set(files)))


def delete_orphans(session: Session, older_than_seconds: float = 24 * 60 * 60) -> list[str]:
    names = orphan_files(session, older_than_seconds)
    for name in names:
        _unlink(name)
    return names
