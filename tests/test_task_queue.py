import threading
import time

from goodprice.services.task_queue import TaskQueue


def _wait_until(pred, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_queue_runs_serially_in_submit_order():
    order = []
    q = TaskQueue(runner=lambda tid: order.append(tid), gap_seconds=0)
    q.start()
    q.submit(2)
    q.submit(1)
    q.submit(3)
    assert _wait_until(lambda: len(order) == 3)
    q.stop()
    assert order == [2, 1, 3]  # FIFO


def test_queue_dedupes_queued_duplicates():
    order = []
    q = TaskQueue(runner=lambda tid: order.append(tid), gap_seconds=0)
    q.start()
    q.submit(1)
    assert q.submit(1) is False  # 已排队再去重
    assert _wait_until(lambda: len(order) == 1)
    q.stop()
    assert order == [1]


def test_queue_queues_while_running():
    release = threading.Event()
    order = []

    def runner(tid):
        order.append(("start", tid))
        release.wait(2)
        order.append(("end", tid))

    q = TaskQueue(runner=runner, gap_seconds=0)
    q.start()
    q.submit(1)
    assert _wait_until(lambda: len(order) >= 1)
    assert q.submit(1) is True  # 运行中再触发 → 排队补跑一次
    assert q.submit(1) is False  # 已排队 → 去重
    release.set()
    assert _wait_until(lambda: len(order) == 4)
    q.stop()
    assert order == [("start", 1), ("end", 1), ("start", 1), ("end", 1)]


def test_queue_gap_between_tasks():
    times = []
    q = TaskQueue(runner=lambda tid: times.append(time.time()), gap_seconds=0.2)
    q.start()
    q.submit(1)
    q.submit(2)
    assert _wait_until(lambda: len(times) == 2, timeout=4)
    q.stop()
    assert times[1] - times[0] >= 0.18
