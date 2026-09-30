from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """명세서 공통 오류 형식 `{ "detail": ..., "code": ... }` 으로 응답되는 예외."""

    def __init__(self, status_code: int, code: str, detail: str):
        self.status_code = status_code
        self.code = code
        self.detail = detail


def company_not_found() -> AppError:
    return AppError(404, "COMPANY_NOT_FOUND", "찾는 회사가 없어요. 종목코드를 다시 확인해 주세요.")


def report_not_ready() -> AppError:
    return AppError(404, "REPORT_NOT_READY", "이 회사는 아직 AI 리포트를 지원하지 않아요.")


def report_not_found() -> AppError:
    return AppError(404, "REPORT_NOT_FOUND", "아직 만들어진 리포트가 없어요.")


def report_generation_failed(
    detail: str = "리포트를 만드는 중에 문제가 생겼어요. 잠시 후 다시 시도해 주세요.",
) -> AppError:
    return AppError(502, "REPORT_GENERATION_FAILED", detail)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code}
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p not in ("query", "body"))
        detail = f"요청 값이 올바르지 않아요. ({field})" if field else "요청 값이 올바르지 않아요."
        return JSONResponse(status_code=422, content={"detail": detail, "code": "VALIDATION_ERROR"})
