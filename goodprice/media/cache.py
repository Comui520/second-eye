import hashlib
import json


def processing_key(content_hash: str, parameters: dict) -> str:
    return hashlib.sha256(json.dumps([content_hash, parameters], sort_keys=True).encode()).hexdigest()


def find_cached(budget, key):
    """Caller holds budget lock until cached inputs have been pinned."""
    for manifest in budget.root.glob(f"asset-*/{key}.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            paths = [budget.checked(manifest.parent / item["file"]) for item in data]
            if all(path.is_file() for path in paths):
                return data, paths
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None
