"""OpenDART(금융감독원 전자공시) 연동.

- 고유번호 목록(corpCode.xml): 상장 회사의 DART 고유번호·공식 회사명·영문명. 하루 한 번 받아서 파일로 캐시한다.
- 기업개황(company.json): 홈페이지 등 회사 기본 정보. 회사별로 캐시한다.
- 단일회사 주요계정(fnlttSinglAcnt.json): 매출·영업이익·순이익·부채·자본. 보고서별로 하루 캐시한다.

키가 없거나 DART가 응답하지 않으면 빈 값을 돌려주고, 호출하는 쪽은 시드 데이터만으로 동작한다.
"""

import io
import json
import logging
import threading
import time
import zipfile
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://opendart.fss.or.kr/api"
CORP_CODES_TTL = 24 * 60 * 60  # 하루
OVERVIEW_TTL = 7 * 24 * 60 * 60  # 일주일
ACCOUNTS_TTL = 24 * 60 * 60  # 하루 (새 보고서가 공시되면 다음 날 반영)
TIMEOUT = 10.0
RETRY_AFTER = (
    10 * 60
)  # 받기에 실패하면 이 시간 동안은 다시 시도하지 않는다 (검색이 매번 막히지 않게)


@dataclass(frozen=True)
class DartCorp:
    corp_code: str
    corp_name: str  # 공식 회사명 (예: 현대자동차)
    corp_eng_name: str  # 공식 영문명 (예: HYUNDAI MOTOR CO)


class DartClient:
    def __init__(self, api_key: str, cache_dir: Path):
        self._key = api_key
        self._cache_dir = cache_dir
        self._lock = threading.Lock()
        self._corps: dict[str, DartCorp] | None = None
        self._corps_loaded_at = 0.0
        self._corps_retry_at = 0.0
        self._overviews: dict[str, tuple[float, dict[str, Any]]] = {}
        self._accounts: dict[str, tuple[float, list[dict]]] = {}

    # ------------------------------------------------------------------ 캐시 파일

    def _read_cache(self, name: str, ttl: float) -> Any | None:
        path = self._cache_dir / name
        try:
            if time.time() - path.stat().st_mtime > ttl:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_cache(self, name: str, data: Any) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            (self._cache_dir / name).write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        except OSError:
            logger.warning("DART 캐시 파일을 쓰지 못했어요: %s", name)

    # ------------------------------------------------------------------ 고유번호 목록

    def _download_corps(self) -> dict[str, DartCorp]:
        res = httpx.get(f"{BASE_URL}/corpCode.xml", params={"crtfc_key": self._key}, timeout=60)
        res.raise_for_status()
        try:
            archive = zipfile.ZipFile(io.BytesIO(res.content))
        except zipfile.BadZipFile:
            # 키가 잘못되면 zip 대신 오류 XML/JSON이 온다
            raise ValueError(f"DART 고유번호 응답 오류: {res.text[:200]}") from None
        root = ElementTree.fromstring(archive.read(archive.namelist()[0]))
        corps = {}
        for item in root.iter("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            if stock_code:  # 상장(종목코드가 있는) 회사만
                corps[stock_code] = DartCorp(
                    corp_code=(item.findtext("corp_code") or "").strip(),
                    corp_name=(item.findtext("corp_name") or "").strip(),
                    corp_eng_name=(item.findtext("corp_eng_name") or "").strip(),
                )
        return corps

    def corp_index(self) -> dict[str, DartCorp]:
        """종목코드 → DART 고유번호 정보. 실패하면 마지막으로 받은 목록(없으면 빈 dict)."""
        if self._corps is not None and time.time() - self._corps_loaded_at < CORP_CODES_TTL:
            return self._corps
        if time.time() < self._corps_retry_at:
            return self._corps or {}
        with self._lock:
            if self._corps is not None and time.time() - self._corps_loaded_at < CORP_CODES_TTL:
                return self._corps
            if time.time() < self._corps_retry_at:
                return self._corps or {}
            cached = self._read_cache("corp_codes.json", CORP_CODES_TTL)
            if cached is not None:
                corps = {code: DartCorp(**c) for code, c in cached.items()}
            else:
                try:
                    corps = self._download_corps()
                except (httpx.HTTPError, ValueError, ElementTree.ParseError) as e:
                    logger.warning("DART 고유번호 목록을 받지 못했어요: %s", e)
                    self._corps_retry_at = time.time() + RETRY_AFTER
                    return self._corps or {}
                self._write_cache("corp_codes.json", {k: asdict(v) for k, v in corps.items()})
            self._corps, self._corps_loaded_at = corps, time.time()
            return corps

    # ------------------------------------------------------------------ 기업개황

    def company_overview(self, corp_code: str) -> dict[str, Any] | None:
        hit = self._overviews.get(corp_code)
        if hit and time.time() - hit[0] < OVERVIEW_TTL:
            return hit[1]
        name = f"company_{corp_code}.json"
        data = self._read_cache(name, OVERVIEW_TTL)
        if data is None:
            try:
                res = httpx.get(
                    f"{BASE_URL}/company.json",
                    params={"crtfc_key": self._key, "corp_code": corp_code},
                    timeout=TIMEOUT,
                )
                res.raise_for_status()
                data = res.json()
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("DART 기업개황을 받지 못했어요 (%s): %s", corp_code, e)
                return None
            if data.get("status") != "000":
                logger.warning("DART 기업개황 오류 (%s): %s", corp_code, data.get("message"))
                return None
            self._write_cache(name, data)
        self._overviews[corp_code] = (time.time(), data)
        return data

    # ------------------------------------------------------------------ 재무제표 주요계정

    def single_accounts(self, corp_code: str, year: int, reprt_code: str) -> list[dict] | None:
        """단일회사 주요계정(fnlttSinglAcnt). 보고서가 없으면 [], 받지 못하면 None."""
        key = f"{corp_code}_{year}_{reprt_code}"
        hit = self._accounts.get(key)
        if hit and time.time() - hit[0] < ACCOUNTS_TTL:
            return hit[1]
        name = f"accounts_{key}.json"
        rows = self._read_cache(name, ACCOUNTS_TTL)
        if rows is None:
            try:
                res = httpx.get(
                    f"{BASE_URL}/fnlttSinglAcnt.json",
                    params={
                        "crtfc_key": self._key,
                        "corp_code": corp_code,
                        "bsns_year": str(year),
                        "reprt_code": reprt_code,
                    },
                    timeout=TIMEOUT,
                )
                res.raise_for_status()
                data = res.json()
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("DART 재무제표를 받지 못했어요 (%s): %s", key, e)
                return None
            status = data.get("status")
            if status == "013":  # 조회된 데이터 없음 (아직 공시 전 등)
                rows = []
            elif status == "000":
                rows = data.get("list") or []
            else:
                logger.warning("DART 재무제표 오류 (%s): %s", key, data.get("message"))
                return None
            self._write_cache(name, rows)
        self._accounts[key] = (time.time(), rows)
        return rows


@lru_cache
def get_dart() -> DartClient | None:
    settings = get_settings()
    if not settings.dart_api_key:
        return None
    return DartClient(settings.dart_api_key, Path(settings.dart_cache_dir))


def corp_index() -> dict[str, DartCorp]:
    client = get_dart()
    return client.corp_index() if client else {}


def company_overview(corp_code: str) -> dict[str, Any] | None:
    client = get_dart()
    return client.company_overview(corp_code) if client else None


def single_accounts(corp_code: str, year: int, reprt_code: str) -> list[dict] | None:
    client = get_dart()
    return client.single_accounts(corp_code, year, reprt_code) if client else None


def warm_up() -> None:
    """서버 시작 시 고유번호 목록을 미리 받아 둔다 (첫 검색이 느려지지 않게)."""
    corp_index()


def homepage_url(overview: dict[str, Any]) -> str:
    """DART는 "www.samsung.com/sec"처럼 주소만 주므로 https://를 붙인다."""
    url = (overview.get("hm_url") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url
