"""커뮤니티 사진 올리기. 규칙은 프론트 `frontend/src/config/communityRules.ts` 의 IMAGE_UPLOAD 와 같다.

올린 사진은 `upload_dir` 에 저장하고 `/uploads/<이름>` 으로 보여준다.
"""

import re
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import invalid_image

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
    name = f"{uuid.uuid4().hex}.{kind}"
    (upload_dir() / name).write_bytes(data)
    return name


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
