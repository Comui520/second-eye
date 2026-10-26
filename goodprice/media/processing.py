import hashlib
import json
import warnings
from contextlib import ExitStack, contextmanager
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from goodprice.media.cache import find_cached, processing_key
from goodprice.media.download import MediaDownloader, MediaDownloadError
from goodprice.media.frames import MAX_OUTPUT_BYTES, MAX_PIXELS, extract_frames
from goodprice.media.types import MediaEvidence


def prepare_image(path: Path, max_edge: int = 1280) -> Path:
    output = path.with_suffix(".jpg")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > MAX_PIXELS:
                    raise ValueError("图片格式或解码像素超过上限")
                image.load()
                image.thumbnail((max_edge, max_edge))
                image.convert("RGB").save(output, "JPEG", quality=85)
        if output.stat().st_size > MAX_OUTPUT_BYTES:
            raise ValueError("图片输出超过预算")
        return output
    except (OSError, UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        output.unlink(missing_ok=True)
        raise ValueError("图片解码失败或像素超限") from None


class MediaProcessor:
    def __init__(self, budget, downloader=None):
        self.budget = budget
        self.downloader = downloader or MediaDownloader(budget)

    def process(self, candidates, settings):
        with self.session(candidates, settings) as (evidence, limitations):
            return evidence

    @contextmanager
    def session(self, candidates, settings):
        """Pins every selected file through the consumer's model request."""
        evidence, limitations = [], []
        counts = {"image": 0, "video": 0}
        with ExitStack() as pins:
            with self.budget.processing_slot(settings.execution_media_workers):
                for candidate in candidates:
                    limit = settings.media_image_count if candidate.kind == "image" else settings.media_video_count
                    if counts[candidate.kind] >= limit:
                        limitations.append("retention_limit")
                        continue
                    counts[candidate.kind] += 1
                    if candidate.status == "unsupported":
                        limitations.append("unsupported_media")
                        continue
                    if candidate.kind == "video" and not settings.video_analysis_enabled:
                        limitations.append("video_disabled")
                        continue
                    download_size = (settings.media_video_max_mib if candidate.kind == "video" else 10) * 1024**2
                    output_size = MAX_OUTPUT_BYTES * (settings.media_frames_per_video if candidate.kind == "video" else 1)
                    try:
                        with self.budget.reserve(candidate.key, download_size + output_size + 65536) as reservation:
                            raw = self.downloader.download(candidate, download_size, settings.media_download_timeout,
                                                           reservation=reservation)
                            with raw.open("rb") as source:
                                source_hash = hashlib.file_digest(source, "sha256").hexdigest()
                            parameters = {"version": 1, "kind": candidate.kind, "edge": 1280,
                                          "frames": settings.media_frames_per_video,
                                          "max_seconds": settings.media_video_max_seconds}
                            key = processing_key(source_hash, parameters)
                            with self.budget.state.lock:
                                cached = find_cached(self.budget, key)
                                if cached:
                                    data, paths = cached
                                    pins.enter_context(self.budget.pin(paths))
                            if not cached:
                                if candidate.kind == "image":
                                    outputs = [prepare_image(raw)]
                                    data = [{"file": outputs[0].name, "timestamp": None,
                                             "hash": hashlib.sha256(outputs[0].read_bytes()).hexdigest()}]
                                else:
                                    frames = extract_frames(raw, settings.media_frames_per_video, reservation.directory / "frames",
                                        settings.media_decode_timeout, settings.media_ffmpeg_threads,
                                        max_seconds=settings.media_video_max_seconds)
                                    outputs = [f.path for f in frames]
                                    data = [{"file": f.path.relative_to(reservation.directory).as_posix(), "timestamp": f.timestamp_ms,
                                             "hash": f.content_hash} for f in frames]
                                raw.unlink(missing_ok=True)
                                manifest = reservation.directory / f"{key}.json"
                                manifest.write_text(json.dumps(data), encoding="utf-8")
                                with self.budget.state.lock:
                                    self.budget.commit(reservation.token, outputs + [manifest])
                                    paths = reservation.committed_paths[:-1]
                                    pins.enter_context(self.budget.pin(paths))
                            for row, path in zip(data, paths, strict=True):
                                identity = hashlib.sha256(f"{row['hash']}:{candidate.offer_key}:{row['timestamp']}".encode()).hexdigest()[:32]
                                evidence.append(MediaEvidence(identity, "image" if candidate.kind == "image" else "frame",
                                    row["hash"], path, candidate.offer_key,
                                    candidate.key if candidate.kind == "video" else None, row["timestamp"]))
                    except (ValueError, RuntimeError, OSError, MediaDownloadError):
                        limitations.append(f"{candidate.kind}_unavailable")
            yield evidence, tuple(dict.fromkeys(limitations))
