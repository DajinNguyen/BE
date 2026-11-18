import pytest

from app.core.db import new_session
from app.services import ai_retry, quarter_segment_service
from tests.test_quarter_segments import BUSINESS, NOTE, TOTALS, _extraction


class Flaky:
    """처음 몇 번은 Gemini 혼잡 오류를 낸다."""

    def __init__(self, failures, error="503 UNAVAILABLE. This model is experiencing high demand"):
        self.failures = failures
        self.error = error
        self.calls = 0

    def write(self, source):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.error)
        return _extraction()


def test_busy_errors_are_retried():
    flaky = Flaky(failures=2)
    assert ai_retry.call(flaky.write, "x") == _extraction()
    assert flaky.calls == 3


def test_gives_up_after_the_last_try():
    flaky = Flaky(failures=10)
    with pytest.raises(RuntimeError):
        ai_retry.call(flaky.write, "x")
    assert flaky.calls == len(ai_retry.RETRY_DELAYS_SECONDS) + 1


def test_other_errors_are_not_retried():
    flaky = Flaky(failures=1, error="400 INVALID_ARGUMENT")
    with pytest.raises(RuntimeError):
        ai_retry.call(flaky.write, "x")
    assert flaky.calls == 1


def test_segments_survive_a_busy_gemini(client, monkeypatch):
    """IPARK현대산업개발: 503 한 번에 30분 동안 "사업별 성과"가 비어 있던 문제."""
    monkeypatch.setattr(
        quarter_segment_service,
        "_read_source",
        lambda rcept_no: quarter_segment_service.source_text(NOTE, BUSINESS),
    )
    flaky = Flaky(failures=1)
    status = quarter_segment_service.run_generation(
        "005930",
        flaky,
        new_session,
        company_name=TOTALS["company_name"],
        rcept_no="r",
        revenue=TOTALS["revenue"],
        operating_income=TOTALS["operating_income"],
        prior_revenue=TOTALS["prior_revenue"],
    )
    assert (status, flaky.calls) == ("done", 2)
