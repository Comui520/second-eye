from dataclasses import replace
from datetime import datetime, timezone

from goodprice.execution.limits import ResourceLimits
from goodprice.execution.protection import ResourceGuard
from goodprice.resources import ResourceSnapshot


def test_pressure_only_reduces_effective_limits_and_preserves_saved_values():
    state = [ResourceSnapshot(8, 8 * 1024**3, 100 * 1024**2, "host", (), datetime.now(timezone.utc))]
    now = [0.0]
    guard = ResourceGuard(detector=lambda: state[0], clock=lambda: now[0])
    wanted = ResourceLimits(model_workers=4, media_workers=2)
    current = guard.evaluate(wanted)
    assert current.limits.model_workers == current.limits.media_workers == 1
    assert current.paused == {"crawler", "media", "model"}
    assert wanted.model_workers == 4 and wanted.media_workers == 2
    state[0] = replace(state[0], available_memory_bytes=4 * 1024**3)
    now[0] += 6
    restored = guard.evaluate(wanted)
    assert not restored.paused and restored.limits == wanted

def test_restore_rejects_missing_or_corrupted_media_before_reusing_verdict(tmp_path, base_settings):
    import hashlib

    from goodprice.execution.media_registry import MediaRegistry
    root = base_settings.data_root / "media-cache"
    root.mkdir(parents=True)
    file = root / "fixture.jpg"
    metadata = {"evidence": [{"id": "fixture", "kind": "image", "hash": hashlib.sha256(b"original").hexdigest(),
        "path": "fixture.jpg", "offer_key": None, "video_id": None, "timestamp_ms": None}]}
    registry = MediaRegistry()
    try:
        assert not registry.restore("fixture", metadata, base_settings)
        file.write_bytes(b"corrupted")
        assert not registry.restore("fixture", metadata, base_settings)
        file.write_bytes(b"original")
        assert registry.restore("fixture", metadata, base_settings)
    finally:
        registry.close()
