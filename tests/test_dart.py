import io
import zipfile

import httpx
import pytest

from app.services import dart

CORP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
    <list>
        <corp_code>00164742</corp_code>
        <corp_name>현대자동차</corp_name>
        <corp_eng_name>HYUNDAI MOTOR CO</corp_eng_name>
        <stock_code>005380</stock_code>
        <modify_date>20250101</modify_date>
    </list>
    <list>
        <corp_code>00106641</corp_code>
        <corp_name>기아</corp_name>
        <corp_eng_name>KIA CORPORATION</corp_eng_name>
        <stock_code>000270</stock_code>
        <modify_date>20250101</modify_date>
    </list>
    <list>
        <corp_code>00434003</corp_code>
        <corp_name>다코</corp_name>
        <corp_eng_name>Daco corporation</corp_eng_name>
        <stock_code> </stock_code>
        <modify_date>20170630</modify_date>
    </list>
</result>
"""

OVERVIEW = {
    "status": "000",
    "message": "정상",
    "corp_code": "00106641",
    "corp_name": "기아(주)",
    "hm_url": "www.kia.com",
}


def _zip(xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


class FakeDartApi:
    """httpx.get 대신 쓰는 가짜 OpenDART."""

    def __init__(self, corp_body: bytes | None = None, fail: bool = False):
        self.corp_body = _zip(CORP_XML) if corp_body is None else corp_body
        self.fail = fail
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append(url.rsplit("/", 1)[-1])
        request = httpx.Request("GET", url)
        if self.fail:
            raise httpx.ConnectError("down", request=request)
        if url.endswith("corpCode.xml"):
            return httpx.Response(200, content=self.corp_body, request=request)
        return httpx.Response(200, json=OVERVIEW, request=request)


@pytest.fixture
def use_dart(tmp_path, monkeypatch):
    """가짜 OpenDART에 붙은 DartClient로 바꿔 끼운다."""

    def install(api: FakeDartApi) -> dart.DartClient:
        monkeypatch.setattr(dart.httpx, "get", api)
        client = dart.DartClient("test-key", tmp_path)
        monkeypatch.setattr(dart, "get_dart", lambda: client)
        return client

    return install


def test_corp_index_keeps_only_listed_companies_and_caches(use_dart, tmp_path):
    api = FakeDartApi()
    client = use_dart(api)
    index = client.corp_index()
    assert set(index) == {"005380", "000270"}
    assert index["005380"] == dart.DartCorp("00164742", "현대자동차", "HYUNDAI MOTOR CO")
    assert (tmp_path / "corp_codes.json").exists()

    # 두 번째부터는 다시 받지 않는다 (메모리 → 파일 캐시)
    client.corp_index()
    assert dart.DartClient("test-key", tmp_path).corp_index() == index
    assert api.calls == ["corpCode.xml"]


def test_wrong_key_response_is_ignored(use_dart):
    api = FakeDartApi(corp_body='{"status":"010","message":"등록되지 않은 키입니다."}'.encode())
    assert use_dart(api).corp_index() == {}


def test_download_failure_backs_off(use_dart):
    api = FakeDartApi(fail=True)
    client = use_dart(api)
    assert client.corp_index() == {}
    assert client.corp_index() == {}
    assert api.calls == ["corpCode.xml"]  # 실패 후 한동안 다시 시도하지 않는다


def test_search_by_official_names(client, use_dart):
    use_dart(FakeDartApi())
    for query in ["현대자동차", "hyundai motor", "KIA CORPORATION"]:
        codes = [
            c["stock_code"] for c in client.get("/api/companies", params={"query": query}).json()
        ]
        assert codes and codes[0] in {"005380", "000270"}, query


def test_corp_code_filled_from_dart(client, use_dart):
    use_dart(FakeDartApi())
    kia = client.get("/api/companies", params={"query": "기아"}).json()[0]
    assert kia["corp_code"] == "00106641"
    by_codes = client.get("/api/companies", params={"stock_codes": "000270"}).json()
    assert by_codes[0]["corp_code"] == "00106641"


def test_detail_fills_empty_homepage_from_overview(client, use_dart):
    api = FakeDartApi()
    use_dart(api)
    assert client.get("/api/companies/000270").json()["homepage_url"] == "https://www.kia.com"
    # 시드에 홈페이지가 있는 회사는 그대로 두고 기업개황을 부르지 않는다
    assert (
        client.get("/api/companies/005930").json()["homepage_url"] == "https://www.samsung.com/sec/"
    )
    assert api.calls.count("company.json") == 1


def test_works_without_dart(client):
    assert dart.get_dart() is None
    assert client.get("/api/companies", params={"query": "현대자동차"}).json() == []
    assert client.get("/api/companies/000270").json()["corp_code"] is None
