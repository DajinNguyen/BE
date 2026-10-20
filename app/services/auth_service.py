"""Google 로그인 (OAuth 2.0 Authorization Code 방식).

1. GET /api/auth/google: state 값을 만들어 짧게 사는 쿠키에 넣고 Google 로그인 화면으로 보낸다.
2. Google이 /api/auth/google/callback?code=…&state=… 로 돌려보낸다.
3. state가 쿠키와 같은지 확인하고, code를 토큰으로 바꾼 뒤 이메일·이름만 받는다.
4. 사용자를 저장하고, 서버가 서명한 JWT를 HttpOnly 쿠키로 심은 뒤 프론트로 돌려보낸다.

비밀번호는 다루지 않는다. Google이 확인한 이메일만 받는다.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import jwt
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models.user import User

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


def issue_session(user: User) -> str:
    expires = _now() + timedelta(days=get_settings().session_days)
    return jwt.encode(
        {"typ": "session", "sub": str(user.id), "exp": expires}, _secret(), algorithm=ALGORITHM
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
    return session.get(User, int(data["sub"]))
