import sys
from types import SimpleNamespace

import httpx
import pytest

from goodprice.analysis.jev_typesafe import TypeSafeJudger
from goodprice.analysis.llm import LLMClient


@pytest.mark.parametrize("api_format", ["chat_completions", "responses"])
def test_single_attempt_client_leaves_rate_retries_to_coordinator(api_format):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"error": "limited"})
    client = LLMClient("https://model.example/v1", "fake", "model", api_format=api_format,
        transport=httpx.MockTransport(handler), max_attempts=1, retry_delay=0)
    with pytest.raises(httpx.HTTPStatusError) as error:
        client.analyze_requirement("fixture")
    assert len(calls) == 1
    assert error.value.response.headers["retry-after"] == "30"


@pytest.mark.parametrize("fails", [False, True])
def test_sdk_single_attempt_policy_and_connection_cleanup(monkeypatch, fails):
    events = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["retry"].max_retries == 0
            assert kwargs["timeout"] <= 60
        def system_one(self, **kwargs):
            if fails:
                raise RuntimeError("controlled failure")
            return SimpleNamespace(answers={"verdict": SimpleNamespace(noul=.9)})
        def close(self): events.append("closed")
    class Retry:
        def __init__(self, max_retries): self.max_retries = max_retries
    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(TypeSafeClient=Client, RetryPolicy=Retry))
    judger = TypeSafeJudger(api_key="fake", single_attempt=True)
    if fails:
        with pytest.raises(RuntimeError):
            judger.analyze_requirement("fixture")
    else:
        assert judger.analyze_requirement("fixture").matched
    assert events == ["closed"]


def test_sdk_constructor_failure_never_changes_process_credentials(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "original")
    def broken(**kwargs): raise TypeError("SDK incompatible")
    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(TypeSafeClient=broken))
    with pytest.raises(TypeError):
        TypeSafeJudger(api_key="replacement")._make_client()
    import os
    assert os.environ["TYPESAFE_API_KEY"] == "original"
