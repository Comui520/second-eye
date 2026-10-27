"""In-flight file references; no database objects or browser objects cross lanes."""
import hashlib
import threading
from contextlib import contextmanager

from goodprice.media.budget import MediaBudget
from goodprice.media.processing import MediaProcessor


class MediaRegistry:
    def __init__(self, processor_factory=None):
        self._entries = {}
        self._lock = threading.RLock()
        self._processor_factory = processor_factory

    def has(self, key):
        with self._lock:
            return key in self._entries

    def restore(self, key, metadata, settings):
        from goodprice.media.types import MediaEvidence
        budget = MediaBudget(settings.data_root / "media-cache", settings.media_cache_mib * 1024**2,
                             settings.media_retention_days * 86400)
        evidence = []
        try:
            with budget.state.lock:
                for e in metadata["evidence"]:
                    path = budget.checked(budget.root / e["path"])
                    if not path.is_file():
                        return False
                    with path.open("rb") as source:
                        if hashlib.file_digest(source, "sha256").hexdigest() != e["hash"]:
                            return False
                    evidence.append(MediaEvidence(e["id"], e["kind"], e["hash"], path, e["offer_key"],
                                                   e["video_id"], e["timestamp_ms"]))
                pin = budget.pin([e.local_path for e in evidence])
                pin.__enter__()
        except (ValueError, OSError):
            return False
        with self._lock:
            if key in self._entries:
                pin.__exit__(None, None, None)
            else:
                self._entries[key] = (pin, budget, tuple(evidence), tuple(metadata.get("limitations", [])))
        return True

    def prepare(self, key, candidates, settings):
        with self._lock:
            if key in self._entries:
                return self._metadata(self._entries[key])
        processor = (self._processor_factory(settings) if self._processor_factory else
            MediaProcessor(MediaBudget(settings.data_root / "media-cache", settings.media_cache_mib * 1024**2,
                                       settings.media_retention_days * 86400)))
        context = processor.session(candidates, settings)
        evidence, limitations = context.__enter__()
        entry = (context, processor.budget, tuple(evidence), tuple(limitations))
        with self._lock:
            if key in self._entries:
                context.__exit__(None, None, None)
            else:
                self._entries[key] = entry
            return self._metadata(self._entries[key])

    def _metadata(self, entry):
        _, budget, evidence, limitations = entry
        return {"evidence": [{"id": e.id, "kind": e.kind, "hash": e.content_hash,
            "path": e.local_path.relative_to(budget.root).as_posix(), "offer_key": e.offer_key,
            "video_id": e.video_id, "timestamp_ms": e.timestamp_ms} for e in evidence],
            "limitations": list(limitations)}

    @contextmanager
    def pin(self, key):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise LookupError("媒体证据需恢复")
            _, budget, evidence, limitations = entry
            pin = budget.pin([e.local_path for e in evidence])
            pin.__enter__()
        try:
            yield evidence, limitations
        finally:
            pin.__exit__(None, None, None)

    def retain(self, needed):
        with self._lock:
            unused = [key for key in self._entries if key not in needed]
            for key in unused:
                context, *_ = self._entries.pop(key)
                context.__exit__(None, None, None)

    def close(self):
        self.retain(set())
