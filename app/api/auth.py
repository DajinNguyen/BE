from fastapi import APIRouter, Cookie, Depends, Query, Response
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import auth_not_configured
from app.models.user import User
from app.schemas.auth import AuthUser
from app.services import auth_service
from app.services.auth_service import SESSION_COOKIE, STATE_COOKIE, AuthFailed

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, key: str, value: str, max_age: int, path: str = "/") -> None:
    response.set_cookie(
        key,
        value,
        max_age=max_age,
        path=path,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
    )


@router.get("/google")
def login_with_google(
    next: str | None = Query(None, description="로그인 후 돌아갈 프론트 주소"),
):
    """Google 로그인 화면으로 보낸다. 프론트는 이 주소로 페이지를 이동시키면 된다."""
    if not auth_service.is_configured():
        raise auth_not_configured()
    state, token = auth_service.make_state(auth_service.safe_next(next))
    response = RedirectResponse(auth_service.authorization_url(state), status_code=302)
    _set_cookie(
        response, STATE_COOKIE, token, int(auth_service.STATE_TTL.total_seconds()), "/api/auth"
    )
    return response


@router.get("/google/callback", include_in_schema=False)
def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    state_cookie: str | None = Cookie(None, alias=STATE_COOKIE),
    session: Session = Depends(get_session),
):
    """Google이 돌려보내는 곳. 성공하면 로그인 쿠키를 심고 프론트로, 실패하면 `?login_error=` 를 붙여 프론트로."""
    next_url = auth_service.safe_next(None)
    try:
        expected, next_url = auth_service.read_state(state_cookie)
        if error:  # 사용자가 취소한 경우 등
            raise AuthFailed("cancelled" if error == "access_denied" else "google_error")
        auth_service.check_state(expected, state)
        if not code:
            raise AuthFailed("google_error")
        user = auth_service.upsert_user(session, auth_service.fetch_google_profile(code))
    except AuthFailed as e:
        response = RedirectResponse(auth_service.with_query(next_url, login_error=e.code), 302)
    else:
        response = RedirectResponse(next_url, 302)
        days = get_settings().session_days
        _set_cookie(response, SESSION_COOKIE, auth_service.issue_session(user), days * 86400)
    response.delete_cookie(STATE_COOKIE, path="/api/auth")
    return response


@router.get("/me", response_model=AuthUser)
def me(user: User = Depends(current_user)):
    """로그인한 사용자. 로그인하지 않았으면 401 `NOT_LOGGED_IN`."""
    return AuthUser(email=user.email, name=user.name)


@router.post("/logout", status_code=204)
def logout():
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
