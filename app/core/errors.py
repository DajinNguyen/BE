from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """공통 오류 형식 `{ "error": { "code": ..., "message": ... } }` 으로 응답되는 예외.

    `message` 는 화면에 그대로 보여줄 수 있는 쉬운 문장이다.
    """

    def __init__(self, status_code: int, code: str, detail: str):
        self.status_code = status_code
        self.code = code
        self.detail = detail


def company_not_found() -> AppError:
    return AppError(404, "COMPANY_NOT_FOUND", "찾는 회사가 없어요. 종목코드를 다시 확인해 주세요.")


def data_not_found() -> AppError:
    return AppError(404, "DATA_NOT_FOUND", "이 회사의 자료를 아직 불러오지 못했어요.")


def puzzle_not_found() -> AppError:
    return AppError(404, "PUZZLE_NOT_FOUND", "퍼즐을 찾지 못했어요.")


def industry_not_found() -> AppError:
    return AppError(404, "INDUSTRY_NOT_FOUND", "찾는 산업이 없어요. 주소를 다시 확인해 주세요.")


def invalid_param(detail: str) -> AppError:
    return AppError(400, "INVALID_PARAM", detail)


def login_required() -> AppError:
    return AppError(401, "LOGIN_REQUIRED", "로그인이 필요해요.")


def auth_not_configured() -> AppError:
    return AppError(503, "AUTH_NOT_CONFIGURED", "Google 로그인이 아직 설정되지 않았어요.")


def report_generation_failed(
    detail: str = "리포트를 만드는 중에 문제가 생겼어요. 잠시 후 다시 시도해 주세요.",
) -> AppError:
    return AppError(502, "REPORT_GENERATION_FAILED", detail)


# 커뮤니티


def post_not_found() -> AppError:
    return AppError(404, "POST_NOT_FOUND", "글을 찾지 못했어요. 지워졌을 수 있어요.")


def comment_not_found() -> AppError:
    return AppError(404, "COMMENT_NOT_FOUND", "댓글을 찾지 못했어요. 지워졌을 수 있어요.")


def report_target_not_found() -> AppError:
    return AppError(404, "TARGET_NOT_FOUND", "신고할 글이나 댓글을 찾지 못했어요.")


def not_owner(detail: str) -> AppError:
    return AppError(403, "NOT_OWNER", detail)


def invalid_length(detail: str) -> AppError:
    return AppError(400, "INVALID_LENGTH", detail)


def banned_word(label: str) -> AppError:
    return AppError(400, "BANNED_WORD", f"{label}은(는) 쓸 수 없어요.")


def too_frequent(detail: str) -> AppError:
    return AppError(429, "TOO_FREQUENT", f"{detail} 잠시 후 다시 시도해 주세요.")


def already_reported() -> AppError:
    return AppError(409, "ALREADY_REPORTED", "이미 신고했어요.")


def invalid_image(detail: str) -> AppError:
    return AppError(400, "INVALID_IMAGE", detail)


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc.code, exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p not in ("query", "body"))
        detail = f"요청 값이 올바르지 않아요. ({field})" if field else "요청 값이 올바르지 않아요."
        return JSONResponse(status_code=422, content=_error_body("VALIDATION_ERROR", detail))
