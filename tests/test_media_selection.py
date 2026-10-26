from pathlib import Path

import pytest
from pydantic import ValidationError

from goodprice.config import Settings
from goodprice.media.selection import select_media
from goodprice.media.types import MediaEvidence


def evidence(kind, count):
    return [MediaEvidence(f"{kind}{i}", kind, f"hash{i}", Path(f"{i}.jpg")) for i in range(count)]


def test_default_mixed_selection_is_four_images_four_frames():
    result = select_media(evidence("image", 8), evidence("frame", 6), 4, 4, 8)
    assert [item.kind for item in result.items] == ["image", "frame"] * 4
    assert len(result.omitted_ids) == 6


def test_single_type_borrows_unused_quota():
    result = select_media(evidence("image", 10), [], 4, 4, 8)
    assert len(result.items) == 8
    assert len(select_media(evidence("image", 1), evidence("frame", 10), 4, 4, 8).items) == 8


def test_zero_quota_reports_omitted_type():
    result = select_media(evidence("image", 8), evidence("frame", 4), 4, 0, 8)
    assert all(item.kind == "image" for item in result.items)
    assert "frames_disabled" in result.limitations
    assert set(result.omitted_ids) >= {"frame0", "frame1", "frame2", "frame3"}


@pytest.mark.parametrize("values", [dict(media_total_limit=9), dict(media_total_limit=17),
    dict(media_video_max_mib=201), dict(media_image_quota=-1),
    dict(media_cache_mib=256, media_video_max_mib=200, media_frames_per_video=24, media_video_count=3)])
def test_invalid_or_over_model_limit_rejected(values):
    with pytest.raises((ValueError, ValidationError)):
        Settings(_env_file=None, **values)


def test_explicit_model_limit_allows_sixteen():
    assert Settings(_env_file=None, media_model_limit=16, media_total_limit=16).media_total_limit == 16


def test_old_install_keeps_four_and_explicit_value_survives(session_factory, base_settings):
    from goodprice.models import SchemaVersion
    from goodprice.services.settings_service import SettingsService
    with session_factory() as session:
        session.add(SchemaVersion(id=1, version=1, legacy_install=True))
        session.commit()
    service = SettingsService(session_factory, base_settings)
    assert service.get().media_total_limit == 4
    assert service.get().video_analysis_enabled is False
    assert service.set_many({"media_total_limit": "6"}).media_total_limit == 6


def test_invalid_save_is_atomic(session_factory, base_settings):
    from goodprice.services.settings_service import SettingsService
    service = SettingsService(session_factory, base_settings)
    with pytest.raises(ValidationError):
        service.set_many({"media_total_limit": "17", "llm_model": "uncommitted"})
    assert service.get().llm_model != "uncommitted"
