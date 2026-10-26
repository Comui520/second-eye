import threading
import time
from concurrent.futures import ThreadPoolExecutor

from goodprice.config import Settings
from goodprice.media.budget import MediaBudget
from goodprice.media.processing import MediaProcessor
from goodprice.media.types import MediaCandidate


def test_all_processors_share_configured_decode_slots(tmp_path):
    from test_media_processing import image_bytes
    active = peak = 0
    lock = threading.Lock()
    class Downloader:
        def download(self, candidate, max_bytes, timeout_seconds, reservation):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.08)
            path = reservation.directory / "input.part"
            path.write_bytes(image_bytes())
            with lock:
                active -= 1
            return path
    settings = Settings(_env_file=None, execution_media_workers=1)
    def process(index):
        processor = MediaProcessor(MediaBudget(tmp_path, 256 * 1024**2, 86400, safety_bytes=0), Downloader())
        return processor.process([MediaCandidate(str(index), "image", f"https://cdn.example/{index}")], settings)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(process, range(4)))
    assert all(len(output) == 1 for output in outputs)
    assert peak == 1
