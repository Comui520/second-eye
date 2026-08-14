import heapq
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_GAP_SECONDS = 300.0  # 任务之间默认间隔 5 分钟


class TaskQueue:
    """单消费者串行任务队列：同一时刻只跑一个任务，任务之间间隔 gap_seconds。

    排队规则：按提交顺序（FIFO）；同一任务已排队时去重；任务运行中再次触发会排队补跑一次，
    避免运行超过间隔时被丢弃。
    """

    def __init__(self, runner: Callable[[int], None], gap_seconds: float = DEFAULT_GAP_SECONDS):
        self._runner = runner
        self._gap_seconds = max(0.0, gap_seconds)
        self._heap: list[tuple[int, int]] = []  # (seq, task_id)
        self._seq = 0
        self._queued: set[int] = set()
        self._running: set[int] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="task-queue")
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def submit(self, task_id: int) -> bool:
        """入队。已排队返回 False；运行中允许补排一次；空闲直接入队。"""
        with self._lock:
            if task_id in self._queued:
                return False
            self._seq += 1
            heapq.heappush(self._heap, (self._seq, task_id))
            self._queued.add(task_id)
            logger.info("任务 %s 已入队（当前排队 %s 个）", task_id, len(self._heap))
        self._wake.set()
        return True

    def queued_ids(self) -> list[int]:
        with self._lock:
            return [t for _, t in sorted(self._heap)]

    def running_ids(self) -> set[int]:
        with self._lock:
            return set(self._running)

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if self._heap:
                    _, task_id = heapq.heappop(self._heap)
                    self._queued.discard(task_id)
                    self._running.add(task_id)
                else:
                    task_id = None
            if task_id is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            try:
                self._runner(task_id)
            except Exception:
                logger.exception("队列任务 %s 执行异常", task_id)
            finally:
                with self._lock:
                    self._running.discard(task_id)
            if not self._stop.is_set() and self._gap_seconds > 0:
                self._stop.wait(self._gap_seconds)
