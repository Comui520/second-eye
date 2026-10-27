import hashlib
import json
import math
import shutil
import subprocess
import time
from pathlib import Path

from goodprice.media.types import FrameFile, VideoInfo

FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi"
MAX_PIXELS = 24_000_000
MAX_OUTPUT_BYTES = 2 * 1024**2


class MediaDecodeError(ValueError):
    pass


def _local(path):
    if not isinstance(path, Path) or not path.is_file() or path.is_symlink():
        raise MediaDecodeError("仅处理已下载的本地媒体文件")
    return str(path.resolve())


def _run(tool, args, timeout):
    binary = shutil.which(tool)
    if not binary:
        raise MediaDecodeError("FFmpeg/ffprobe 未安装，视频未分析")
    try:
        result = subprocess.run([binary, *args], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=max(.01, timeout),
                                check=True, text=True, encoding="utf-8")
    except subprocess.TimeoutExpired:
        raise MediaDecodeError("媒体解码超时") from None
    except (subprocess.CalledProcessError, OSError):
        raise MediaDecodeError("媒体文件解码失败") from None
    return result.stdout


def probe_video(path: Path, timeout_seconds: int) -> VideoInfo:
    path = _local(path)
    raw = _run("ffprobe", ["-v", "error", "-protocol_whitelist", "file", "-format_whitelist", FORMATS,
                           "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                           "-of", "json", path], timeout_seconds)
    try:
        data = json.loads(raw)
        stream = data["streams"][0]
        info = VideoInfo(float(data["format"]["duration"]), int(stream["width"]), int(stream["height"]))
        if (not math.isfinite(info.duration_seconds) or info.duration_seconds <= 0
                or min(info.width, info.height) <= 0 or info.width * info.height > MAX_PIXELS):
            raise ValueError()
        return info
    except (ValueError, TypeError, KeyError, IndexError):
        raise MediaDecodeError("媒体时长或分辨率无效/超限") from None


def extract_frames(path: Path, count: int, output_dir: Path, timeout_seconds: int,
                   threads: int, max_seconds: int = 180) -> list[FrameFile]:
    if not 1 <= count <= 24 or not 1 <= threads <= 4:
        raise MediaDecodeError("抽帧数量或线程配置无效")
    started = time.monotonic()
    info = probe_video(path, timeout_seconds)
    if info.duration_seconds > max_seconds:
        raise MediaDecodeError("视频时长超过上限，未分析")
    output_dir.mkdir(parents=True, exist_ok=True)
    result, seen = [], set()
    # Last point precedes the end by 250ms to avoid an empty seek at EOF.
    end = max(0, info.duration_seconds - .25)
    timestamps = [round(1000 * end * i / max(1, count - 1)) for i in range(count)]
    try:
        for i, timestamp in enumerate(timestamps):
            output = output_dir / f"frame-{i}.jpg"
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise MediaDecodeError("媒体解码超时")
            _run("ffmpeg", ["-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file",
                            "-format_whitelist", FORMATS, "-threads", str(threads),
                            "-ss", str(timestamp / 1000), "-i", _local(path),
                            "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1",
                            "-vf", "scale=1280:1280:force_original_aspect_ratio=decrease",
                            "-filter_threads", str(threads), "-threads", str(threads),
                            "-fs", str(MAX_OUTPUT_BYTES), str(output)], remaining)
            if not output.is_file() or output.stat().st_size > MAX_OUTPUT_BYTES:
                raise MediaDecodeError("视频帧输出无效或超过预算")
            from PIL import Image, ImageFilter, ImageStat
            with Image.open(output) as image:
                image.verify()
            with Image.open(output) as image:
                # Flat or very blurred frames add no reliable visual evidence.
                variance = ImageStat.Stat(image.convert("L").filter(ImageFilter.FIND_EDGES)).var[0]
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            if digest in seen or variance < 8:
                output.unlink()
                continue
            seen.add(digest)
            result.append(FrameFile(output, timestamp, digest))
        return result
    except Exception:
        for output in output_dir.glob("frame-*.jpg"):
            output.unlink(missing_ok=True)
        raise
