"""「测试连接」地址校验（SSRF 防护）的单元测试。"""
import pytest

from goodprice.web.routes import _safe_test_url


def test_empty_returns_empty():
    assert _safe_test_url("") == ""
    assert _safe_test_url("   ") == ""


def test_allows_public_https():
    url = "https://open.bigmodel.cn/api/paas/v4"
    assert _safe_test_url(url) == url


def test_allows_loopback_and_private_lan():
    assert _safe_test_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/v1"
    assert _safe_test_url("http://192.168.1.5:1234/v1") == "http://192.168.1.5:1234/v1"


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://example.com/v1",
        "javascript:alert(1)",
        "http://metadata.google.internal/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0:8000",
    ],
)
def test_blocks_unsafe_targets(bad):
    with pytest.raises(ValueError):
        _safe_test_url(bad)
