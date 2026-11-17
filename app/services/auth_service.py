"""Google 로그인 (OAuth 2.0 Authorization Code 방식).

1. GET /api/auth/google: state 값을 만들어 짧게 사는 쿠키에 넣고 Google 로그인 화면으로 보낸다.
2. Google이 /api/auth/google/callback?code=…&state=… 로 돌려보낸다.
3. state가 쿠키와 같은지 확인하고, code를 토큰으로 바꾼 뒤 이메일·이름만 받는다.
4. 사용자를 저장하고, 서버가 서명한 JWT를 HttpOnly 쿠키로 심은 뒤 프론트로 돌려보낸다.

비밀번호는 다루지 않는다. Google이 확인한 이메일만 받는다.
"""

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import jwt
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.errors import invalid_param, nickname_taken
from app.models.user import RevokedSession, User

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SESSION_COOKIE = "dajin_session"
STATE_COOKIE = "dajin_oauth_state"
STATE_TTL = timedelta(minutes=10)
ALGORITHM = "HS256"
TIMEOUT = 10.0


class AuthFailed(Exception):
    """로그인 실패. code는 프론트로 돌려보낼 때 `login_error` 값이 된다."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GoogleProfile:
    sub: str
    email: str
    name: str


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.google_client_id and settings.google_client_secret)


@lru_cache
def _secret() -> str:
    secret = get_settings().jwt_secret
    if secret:
        return secret
    logger.warning("JWT_SECRET이 없어서 임시 키를 만들었어요. 서버를 다시 켜면 모두 로그아웃돼요.")
    return secrets.token_urlsafe(32)


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------
# 돌아갈 주소
# --------------------------------------------------------------------------


def safe_next(next_url: str | None) -> str:
    """허용한 프론트 주소로만 돌려보낸다 (다른 사이트로 보내는 오픈 리다이렉트 방지)."""
    origins = get_settings().frontend_origins
    if next_url:
        parts = urlsplit(next_url)
        if f"{parts.scheme}://{parts.netloc}" in origins:
            return next_url
    return f"{origins[0]}/"


def next_from_path(path: str | None, referer: str | None) -> str:
    """프론트가 보낸 화면 경로(`/companies/005930?tab=community`)를 돌아갈 전체 주소로 바꾼다.

    프론트 주소는 요청을 보낸 화면(Referer)이 허용 목록에 있으면 그 주소, 아니면 첫 번째 주소.
    `//evil.com`, `https://…` 처럼 우리 사이트 밖을 가리키는 값이면 첫 화면(`/`)으로 보낸다.
    """
    origins = get_settings().frontend_origins
    origin = origins[0]
    if referer:
        parts = urlsplit(referer)
        if f"{parts.scheme}://{parts.netloc}" in origins:
            origin = f"{parts.scheme}://{parts.netloc}"
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        path = "/"
    return f"{origin}{path}"


def with_query(url: str, **params: str) -> str:
    parts = urlsplit(url)
    query = urlencode(parse_qsl(parts.query) + list(params.items()))
    return urlunsplit(parts._replace(query=query))


# --------------------------------------------------------------------------
# state (CSRF 방지)
# --------------------------------------------------------------------------


def make_state(next_url: str) -> tuple[str, str]:
    """(Google에 보낼 state, 쿠키에 넣을 서명된 값)"""
    state = secrets.token_urlsafe(24)
    token = jwt.encode(
        {"typ": "oauth_state", "state": state, "next": next_url, "exp": _now() + STATE_TTL},
        _secret(),
        algorithm=ALGORITHM,
    )
    return state, token


def read_state(cookie: str | None) -> tuple[str, str]:
    """쿠키에서 (state, 돌아갈 주소)를 꺼낸다."""
    if not cookie:
        raise AuthFailed("state_missing")
    try:
        data = jwt.decode(cookie, _secret(), algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        raise AuthFailed("state_invalid") from None
    if data.get("typ") != "oauth_state":
        raise AuthFailed("state_invalid")
    return data["state"], safe_next(data.get("next"))


def check_state(expected: str, received: str | None) -> None:
    if not received or not secrets.compare_digest(expected, received):
        raise AuthFailed("state_mismatch")


# --------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------


def authorization_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def fetch_google_profile(code: str) -> GoogleProfile:
    """code를 토큰으로 바꾸고 이메일·이름을 받는다."""
    settings = get_settings()
    try:
        token = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=TIMEOUT,
        )
        token.raise_for_status()
        info = httpx.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {token.json()['access_token']}"},
            timeout=TIMEOUT,
        )
        info.raise_for_status()
        data = info.json()
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning("Google 로그인 처리 중 오류: %s", e)
        raise AuthFailed("google_error") from None

    if not data.get("sub") or not data.get("email") or data.get("email_verified") is not True:
        raise AuthFailed("email_not_verified")
    email = data["email"]
    return GoogleProfile(sub=data["sub"], email=email, name=data.get("name") or email.split("@")[0])


# --------------------------------------------------------------------------
# 사용자 · 로그인 쿠키
# --------------------------------------------------------------------------


def upsert_user(session: Session, profile: GoogleProfile) -> User:
    now = _now()
    user = session.exec(select(User).where(User.google_sub == profile.sub)).first()
    if user is None:
        user = User(
            google_sub=profile.sub,
            email=profile.email,
            name=profile.name,
            created_at=now,
            last_login_at=now,
        )
    else:
        user.email, user.name, user.last_login_at = profile.email, profile.name, now
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


_NICK_ADJECTIVES = [
    "부지런한", "반짝이는", "꼼꼼한", "든든한", "차분한", "씩씩한", "궁금한", "느긋한",
    "똑똑한", "용감한", "다정한", "성실한", "호기심많은", "침착한", "새내기", "알뜰한",
]  # fmt: skip
_NICK_NOUNS = [
    "다람쥐", "고래", "부엉이", "거북이", "펭귄", "수달", "여우", "판다",
    "코알라", "돌고래", "사슴", "참새", "햄스터", "고슴도치", "라쿤", "알파카",
]  # fmt: skip


def nickname_for(user: User) -> str:
    """커뮤니티에 보이는 이름. 실명(Google 이름) 대신 사용자 번호로 만든다 (10대 사용자 보호).

    번호를 붙여서 겹치지 않고, 저장하지 않아도 늘 같은 이름이 나온다.
    """
    if user.nickname:
        return user.nickname
    return default_nickname(user)


def default_nickname(user: User) -> str:
    n = user.id or 0
    adjective = _NICK_ADJECTIVES[n % len(_NICK_ADJECTIVES)]
    noun = _NICK_NOUNS[(n // len(_NICK_ADJECTIVES)) % len(_NICK_NOUNS)]
    return f"{adjective}{noun}{n}"


NICKNAME_MIN, NICKNAME_MAX = 2, 12
_NICKNAME_RE = re.compile(r"^[0-9A-Za-z가-힣_]+$")
_DEFAULT_NICKNAME_RE = re.compile(
    "^(" + "|".join(_NICK_ADJECTIVES) + ")(" + "|".join(_NICK_NOUNS) + r")\d+$"
)


def update_nickname(session: Session, user: User, raw: str | None) -> User:
    """닉네임을 바꾼다. 비우면 자동 닉네임으로 돌아간다.

    2~12자의 한글·영문·숫자·밑줄만 쓸 수 있고, 다른 사람이 쓰는 닉네임(대소문자 무시)은 안 된다.
    자동 닉네임 모양("다정한고래12")은 다른 사람 행세를 할 수 있어서 직접 정할 수 없다.
    """
    nickname = (raw or "").strip() or None
    if nickname is not None:
        if not NICKNAME_MIN <= len(nickname) <= NICKNAME_MAX:
            raise invalid_param(f"닉네임은 {NICKNAME_MIN}~{NICKNAME_MAX}자로 정해 주세요.")
        if not _NICKNAME_RE.match(nickname):
            raise invalid_param("닉네임에는 한글, 영문, 숫자, 밑줄(_)만 쓸 수 있어요.")
        if _DEFAULT_NICKNAME_RE.match(nickname) and nickname != default_nickname(user):
            raise invalid_param("이 모양의 닉네임은 자동으로 만들어지는 이름이라 쓸 수 없어요.")
        taken = session.exec(
            select(User).where(
                func.lower(col(User.nickname)) == nickname.lower(), col(User.id) != user.id
            )
        ).first()
        if taken is not None:
            raise nickname_taken()
    user.nickname = nickname
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def issue_session(user: User) -> str:
    expires = _now() + timedelta(days=get_settings().session_days)
    return jwt.encode(
        # jti: 로그인할 때마다 다른 쿠키가 되게 한다 (로그아웃한 쿠키만 골라서 막으려고)
        {"typ": "session", "sub": str(user.id), "exp": expires, "jti": secrets.token_urlsafe(8)},
        _secret(),
        algorithm=ALGORITHM,
    )


def user_from_session(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    try:
        data = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    if data.get("typ") != "session" or not str(data.get("sub", "")).isdigit():
        return None
    if session.get(RevokedSession, _token_hash(token)) is not None:
        return None  # 로그아웃한 쿠키
    return session.get(User, int(data["sub"]))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def revoke_session(session: Session, token: str | None) -> None:
    """로그아웃: 이 로그인 쿠키를 다시 쓰지 못하게 한다. (다른 기기의 로그인은 그대로 둔다)"""
    if not token:
        return
    try:
        data = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return  # 이미 끝났거나 우리 쿠키가 아니면 막을 것도 없다
    if data.get("typ") != "session":
        return
    now = _now()
    # 어차피 끝난 쿠키의 기록은 지운다
    for old in session.exec(
        select(RevokedSession).where(col(RevokedSession.expires_at) < now)
    ).all():
        session.delete(old)
    key = _token_hash(token)
    if session.get(RevokedSession, key) is None:
        session.add(
            RevokedSession(token_hash=key, expires_at=datetime.fromtimestamp(data["exp"], UTC))
        )
    session.commit()
