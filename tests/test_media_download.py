import httpx
import pytest

from goodprice.media.budget import MediaBudget
from goodprice.media.download import MediaDownloader, MediaDownloadError, PinnedBackend
from goodprice.media.types import MediaCandidate


def downloader(tmp_path, handler, resolver=lambda host, port: ["93.184.216.34"]):
    return MediaDownloader(MediaBudget(tmp_path, 1024, 86400, safety_bytes=0),
                           transport=httpx.MockTransport(handler), resolver=resolver)


@pytest.mark.parametrize("url", ["http://127.0.0.1/file", "http://[::1]/file", "file:///secret", "http://user:pass@example.com/a"])
def test_bad_targets_rejected_before_request(tmp_path, url):
    def forbidden(request):
        raise AssertionError("unsafe request sent")
    service = downloader(tmp_path, forbidden)
    with pytest.raises(MediaDownloadError):
        service.download(MediaCandidate("a", "image", url), 20, 10)


def test_redirect_to_metadata_rejected_and_no_credentials(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    with pytest.raises(MediaDownloadError):
        downloader(tmp_path, handler).download(MediaCandidate("a", "video", "https://cdn.example/a?token=secret"), 50, 10)
    assert len(calls) == 1
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("length", [None, "1", "200"])
def test_stream_size_enforced_even_without_or_false_length(tmp_path, length):
    headers = {"content-length": length} if length else {}
    service = downloader(tmp_path, lambda r: httpx.Response(200, content=b"x" * 30, headers=headers))
    with pytest.raises(MediaDownloadError) as error:
        service.download(MediaCandidate("a", "image", "https://cdn.example/a?token=secret"), 20, 10)
    assert "secret" not in str(error.value)
    assert not list(tmp_path.rglob("*.part"))
    assert service.budget.usage_bytes == 0


def test_valid_download_and_private_dns_rejected(tmp_path):
    service = downloader(tmp_path, lambda r: httpx.Response(200, content=b"image"))
    path = service.download(MediaCandidate("a", "image", "https://cdn.example/a"), 20, 10)
    assert path.read_bytes() == b"image"
    unsafe = downloader(tmp_path, lambda r: httpx.Response(200), lambda h, p: ["93.184.216.34", "::ffff:127.0.0.1"])
    with pytest.raises(MediaDownloadError):
        unsafe.download(MediaCandidate("b", "image", "https://cdn.example/a"), 20, 10)


def test_connection_uses_pinned_numeric_ip_without_second_dns(monkeypatch):
    calls = []
    class Socket:
        def settimeout(self, value): pass
        def setsockopt(self, *args): pass
        def connect(self, address): calls.append(address)
        def close(self): pass
    monkeypatch.setattr("goodprice.media.download.socket.socket", lambda *args: Socket())
    backend = PinnedBackend({("cdn.example", 443): "93.184.216.34"})
    backend.connect_tcp("cdn.example", 443, timeout=1)
    assert calls == [("93.184.216.34", 443)]
    with pytest.raises(MediaDownloadError):
        backend.connect_tcp("other.example", 443)

def test_blocked_dns_obeys_download_deadline_and_releases_disk_reservation(tmp_path):
    import threading
    import time
    release = threading.Event()
    def blocked(host, port):
        release.wait(3)
        return ["93.184.216.34"]
    service = downloader(tmp_path, lambda r: httpx.Response(200, content=b"image"), blocked)
    started = time.monotonic()
    try:
        with pytest.raises(MediaDownloadError):
            service.download(MediaCandidate("blocked", "image", "https://cdn.example/a"), 20, .05)
        assert time.monotonic()-started < .75
        assert service.budget.usage_bytes == 0
    finally:
        release.set()
