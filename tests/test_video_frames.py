import json
import subprocess

import pytest

from goodprice.media.frames import MediaDecodeError, extract_frames, probe_video


def test_missing_tools_and_remote_input_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr("goodprice.media.frames.shutil.which", lambda tool: None)
    path = tmp_path / "file.mp4"
    path.write_bytes(b"video")
    with pytest.raises(MediaDecodeError, match="FFmpeg"):
        probe_video(path, 10)
    with pytest.raises(MediaDecodeError):
        probe_video("https://example.com/video.mp4", 10)


def test_probe_uses_local_only_protocols_and_checks_dimensions(tmp_path, monkeypatch):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"a")
    monkeypatch.setattr("goodprice.media.frames.shutil.which", lambda tool: tool)
    calls = []
    def fake(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps({"streams": [{"width": 640, "height": 480}], "format": {"duration": "10"}}))
    monkeypatch.setattr("goodprice.media.frames.subprocess.run", fake)
    assert probe_video(path, 10).duration_seconds == 10
    assert calls[0][0][calls[0][0].index("-protocol_whitelist") + 1] == "file"
    assert calls[0][1]["timeout"] == 10
    assert not calls[0][1].get("shell")


def test_probe_timeout_and_excessive_duration_not_extracted(tmp_path, monkeypatch):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"a")
    monkeypatch.setattr("goodprice.media.frames.shutil.which", lambda tool: tool)
    def fake(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 10)
    monkeypatch.setattr("goodprice.media.frames.subprocess.run", fake)
    with pytest.raises(MediaDecodeError, match="超时"):
        probe_video(path, 10)


def test_real_generated_video_samples_start_middle_end(tmp_path):
    import shutil
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg absent on host; required in Docker validation")
    path = tmp_path / "test.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=4", "-t", "3", "-pix_fmt", "yuv420p", str(path)], check=True, timeout=20)
    frames = extract_frames(path, 3, tmp_path / "frames", 20, 1)
    assert len(frames) == 3
    assert [f.timestamp_ms for f in frames] == [0, 1375, 2750]
    assert all(f.path.is_file() and len(f.content_hash) == 64 for f in frames)
