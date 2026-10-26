from dataclasses import replace

import httpx
import pytest

from goodprice.config import Settings
from goodprice.media.budget import MediaBudget
from goodprice.media.download import MediaDownloader
from goodprice.media.processing import MediaProcessor, prepare_image
from goodprice.media.types import MediaCandidate


def image_bytes(color="red"):
    from io import BytesIO

    from PIL import Image
    data = BytesIO()
    Image.new("RGB", (1600, 900), color).save(data, "PNG")
    return data.getvalue()


def test_image_is_decoded_resized_and_bad_files_rejected(tmp_path):
    from PIL import Image
    path = tmp_path / "input.part"
    path.write_bytes(image_bytes())
    output = prepare_image(path)
    with Image.open(output) as image:
        assert image.size == (1280, 720)
        assert image.format == "JPEG"
    path.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        prepare_image(path)


def test_signed_url_changes_same_content_only_decode_once(tmp_path, monkeypatch):
    import goodprice.media.processing as module
    calls = []
    original = module.prepare_image
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "prepare_image", tracked)
    budget = MediaBudget(tmp_path, 256 * 1024**2, 86400, safety_bytes=0)
    download = MediaDownloader(budget, httpx.MockTransport(lambda r: httpx.Response(200, content=image_bytes())), lambda h,p: ["93.184.216.34"])
    processor = MediaProcessor(budget, download)
    settings = Settings(_env_file=None)
    candidate = MediaCandidate("one", "image", "https://cdn.example/a?sig=one")
    first = processor.process([candidate], settings)
    second = processor.process([replace(candidate, url="https://cdn.example/a?sig=two")], settings)
    assert len(first) == len(second) == 1
    assert first[0].content_hash == second[0].content_hash
    assert len(calls) == 1
    assert budget.usage_bytes < 2 * 1024**2
    first[0].local_path.unlink()
    assert processor.process([candidate], settings)[0].local_path.is_file()


def test_video_disabled_and_failure_never_make_fake_frames(tmp_path):
    budget = MediaBudget(tmp_path, 256 * 1024**2, 86400, safety_bytes=0)
    def forbidden(request): raise AssertionError("disabled video downloaded")
    processor = MediaProcessor(budget, MediaDownloader(budget, httpx.MockTransport(forbidden)))
    assert processor.process([MediaCandidate("v", "video", "https://cdn.example/v.mp4")], Settings(_env_file=None)) == []
