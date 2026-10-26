"""Public HTTP media only; no environment proxy, cookies or second DNS lookup."""
import ipaddress
import logging
import socket
import time
import uuid
from contextvars import ContextVar
from urllib.parse import urljoin, urlsplit

import httpx
from httpcore import ConnectError, ReadTimeout
from httpcore._backends.base import NetworkStream
from httpcore._backends.sync import SyncBackend, SyncStream

from goodprice.media.budget import MediaBudget
from goodprice.media.types import MediaCandidate


class MediaDownloadError(RuntimeError):
    pass


_media_request = ContextVar("media_request", default=False)


class _RedactMediaRequest(logging.Filter):
    def filter(self, record):
        if _media_request.get():
            record.msg, record.args = "媒体 HTTP 请求（地址已隐藏）", ()
        return True


_log_filter = _RedactMediaRequest()
logging.getLogger("httpx").addFilter(_log_filter)


def resolve_public(host, port):
    return list({row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def public_addresses(url, resolver):
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in {None, 80, 443}):
        raise MediaDownloadError("媒体地址不受支持")
    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        addresses = resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise MediaDownloadError("媒体地址必须指向公网")
    # Reject IPv4-mapped/private transition targets as well.
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
            raise MediaDownloadError("媒体地址类型不受支持")
    return host, parsed.port or (443 if parsed.scheme == "https" else 80), addresses[0]


class DeadlineStream(NetworkStream):
    def __init__(self, stream, deadline):
        self.stream, self.deadline = stream, deadline

    def _timeout(self, timeout):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ReadTimeout("媒体下载超时")
        return min(timeout, remaining) if timeout is not None else remaining

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, self._timeout(timeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, self._timeout(timeout))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return DeadlineStream(self.stream.start_tls(ssl_context, server_hostname, self._timeout(timeout)), self.deadline)

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class PinnedBackend(SyncBackend):
    def __init__(self, pins, deadline=None):
        self.pins = pins
        self.deadline = deadline

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addr = self.pins.get((host, port))
        if addr is None:
            raise MediaDownloadError("未经验证的媒体目标")
        ip = ipaddress.ip_address(addr)
        sock = socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            for option in socket_options or []:
                sock.setsockopt(*option)
            # socket.connect receives a numeric address; hostname cannot rebind.
            sock.connect((str(ip), port))
            stream = SyncStream(sock)
            return DeadlineStream(stream, self.deadline) if self.deadline else stream
        except OSError as exc:
            sock.close()
            raise ConnectError("媒体连接失败") from exc


class MediaDownloader:
    def __init__(self, budget: MediaBudget, transport=None, resolver=None):
        self.budget = budget
        self.transport = transport
        self.resolver = resolver or resolve_public

    def download(self, candidate: MediaCandidate, max_bytes: int, timeout_seconds: int,
                 reservation=None):
        own = reservation is None
        reservation = reservation or self.budget.reserve(candidate.key, max_bytes)
        path = reservation.directory / f"{uuid.uuid4().hex}.part"
        deadline = time.monotonic() + timeout_seconds
        logging_token = _media_request.set(True)
        try:
            url = candidate.url
            for _ in range(6):
                host, port, addr = public_addresses(url, self.resolver)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MediaDownloadError("媒体下载超时")
                transport = self.transport
                if transport is None:
                    transport = httpx.HTTPTransport(trust_env=False, retries=0)
                    # httpcore 1.x backend preserves original Host and TLS SNI;
                    # only the socket destination is replaced with verified IP.
                    transport._pool._network_backend = PinnedBackend({(host, port): addr}, deadline)
                with httpx.Client(transport=transport, trust_env=False, timeout=remaining,
                                  follow_redirects=False) as client:
                    with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        response.raise_for_status()
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise MediaDownloadError("媒体响应压缩格式不受支持")
                        length = response.headers.get("content-length")
                        if length and (not length.isdigit() or int(length) > max_bytes):
                            raise MediaDownloadError("媒体文件超过大小上限")
                        size = 0
                        with path.open("xb") as output:
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > max_bytes or time.monotonic() > deadline:
                                    raise MediaDownloadError("媒体下载超过字节或时间预算")
                                output.write(chunk)
                        if not size:
                            raise MediaDownloadError("媒体文件为空")
                        if own:
                            self.budget.commit(reservation.token, [path])
                            return reservation.committed_paths[0]
                        return path
            raise MediaDownloadError("媒体重定向次数超过限制")
        except Exception as exc:
            path.unlink(missing_ok=True)
            if isinstance(exc, MediaDownloadError):
                raise
            # Never propagate signed URL, cookies, DNS details or response body.
            raise MediaDownloadError("媒体下载失败") from None
        finally:
            _media_request.reset(logging_token)
            if own:
                self.budget.release(reservation.token)
