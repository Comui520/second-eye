"""Browser actors close Playwright on the same thread that created it."""
import logging
import queue
import threading
import time
from concurrent.futures import Future

logger = logging.getLogger(__name__)
_local = threading.local()


def current_browser(key, factory):
    actor = getattr(_local, "actor", None)
    if actor is None:
        raise RuntimeError("Browser access requires its owning worker")
    return actor.browser(key, factory)


class BrowserActor:
    def __init__(self, name):
        self.name = name
        self.queue = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._thread = None
        self._busy = False
        self._stopped = False
        self._context = self._browser = self._key = None
        self._uses, self._opened = 0, 0

    def available(self):
        with self._lock:
            return not self._busy and not self._stopped

    def submit(self, fn):
        with self._lock:
            if self._busy or self._stopped:
                raise RuntimeError("Browser worker full or stopped")
            self._busy = True
            future = Future()
            self.queue.put_nowait((fn, future))
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name=self.name)
                self._thread.start()
            return future

    def browser(self, key, factory):
        if threading.current_thread() is not self._thread:
            raise RuntimeError("Browser ownership violation")
        if self._context is not None and (key != self._key or self._uses >= 100 or time.monotonic() - self._opened >= 600):
            self._close_browser()
        if self._context is None:
            context = factory()
            browser = context.__enter__()
            self._context, self._browser = context, browser
            self._key, self._opened, self._uses = key, time.monotonic(), 0
        self._uses += 1
        return self._browser

    def _close_browser(self):
        context, self._context = self._context, None
        self._browser, self._key = None, None
        if context:
            context.__exit__(None, None, None)

    def _loop(self):
        _local.actor = self
        try:
            while True:
                work = self.queue.get()
                if work is None:
                    break
                fn, future = work
                if self._stopped:
                    future.cancel()
                    break
                if not future.set_running_or_notify_cancel():
                    with self._lock:
                        self._busy = False
                    continue
                result = error = None
                try:
                    result = fn()
                except BaseException as exc:
                    error = exc
                    try:
                        self._close_browser()
                    except Exception as close_error:
                        logger.warning("浏览器回收失败 (%s)", type(close_error).__name__)
                with self._lock:
                    self._busy = False
                if error:
                    future.set_exception(error)
                else:
                    future.set_result(result)
        finally:
            try:
                self._close_browser()
            finally:
                _local.actor = None

    def stop(self, timeout=10):
        with self._lock:
            self._stopped = True
            try:
                self.queue.put_nowait(None)
            except queue.Full:
                pass
        if self._thread:
            self._thread.join(timeout)
