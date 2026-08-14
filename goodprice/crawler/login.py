import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import sync_playwright

from goodprice.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

LOGIN_PROFILE_DIR = PROJECT_ROOT / "data" / "browser_profile"
GOOFISH_HOME = "https://www.goofish.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 淘宝系真正的登录态 cookie（未登录时不会出现）；`t`/`cookie2` 匿名访问也会种，不能作为登录依据
LOGIN_COOKIES = ("unb", "sn", "uc1", "lgc")

CONFIRM_SCRIPT = r"""
(function () {
  function addBtn() {
    if (document.getElementById('se-login-done')) return;
    var btn = document.createElement('div');
    btn.id = 'se-login-done';
    btn.textContent = '\u2714 登录完成，点这里保存';
    btn.style.cssText = 'position:fixed;top:16px;right:16px;z-index:999999;' +
      'background:#16a34a;color:#fff;padding:10px 16px;border-radius:8px;' +
      'font:14px/1.4 sans-serif;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.3);';
    btn.addEventListener('click', function () {
      document.documentElement.setAttribute('data-se-login-done', '1');
      btn.textContent = '\u5df2\u786e\u8ba4\uff0c\u6b63\u5728\u4fdd\u5b58...';
    });
    document.documentElement.appendChild(btn);
  }
  addBtn();
  document.addEventListener('DOMContentLoaded', addBtn);
})();
"""


class LoginSession:
    """一键登录：弹出真实浏览器窗口让用户自己完成登录（密码不经过本程序），
    登录成功后自动抓取闲鱼 Cookie 存入设置。使用持久化 profile，下次可免登录。"""

    def __init__(
        self,
        settings_service,
        profile_dir: Path = LOGIN_PROFILE_DIR,
        timeout_seconds: float = 300.0,
        poll_interval: float = 3.0,
        playwright_factory: Optional[Callable] = None,
    ):
        self._settings_service = settings_service
        self._profile_dir = Path(profile_dir)
        self._timeout_seconds = timeout_seconds
        self._poll_interval = poll_interval
        self._playwright_factory = playwright_factory or sync_playwright
        self._status = "idle"
        self._message = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def status(self) -> tuple[str, str]:
        with self._lock:
            return self._status, self._message

    def _set_status(self, status: str, message: str) -> None:
        with self._lock:
            self._status = status
            self._message = message

    def start(self) -> None:
        with self._lock:
            if self._status == "running":
                return
            self._status = "running"
            self._message = "正在打开浏览器窗口…"
        self._stop.clear()
        threading.Thread(target=self._run, daemon=True, name="goofish-login").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            with self._playwright_factory() as p:
                context = p.chromium.launch_persistent_context(
                    str(self._profile_dir),
                    headless=False,
                    user_agent=USER_AGENT,
                    viewport={"width": 1280, "height": 900},
                )
                context.add_init_script(CONFIRM_SCRIPT)
                closed = threading.Event()
                context.on("close", lambda: closed.set())
                try:
                    page = context.new_page()
                    page.goto(GOOFISH_HOME, wait_until="domcontentloaded", timeout=45000)
                    self._set_status("running", "浏览器窗口已打开，请扫码或登录，完成后会自动保存")
                    deadline = time.time() + self._timeout_seconds
                    while time.time() < deadline:
                        if self._stop.is_set():
                            self._set_status("error", "登录已取消")
                            return
                        if closed.is_set():
                            self._set_status("error", "浏览器窗口已关闭，未完成登录")
                            return
                        cookies = context.cookies()
                        if self._has_login_cookie(cookies) or self._confirmed(page):
                            cookie_str = self._serialize(cookies)
                            self._settings_service.set_many({"xianyu_cookie": cookie_str})
                            logger.info(
                                "闲鱼一键登录成功，Cookie 已保存（%s 个字段）",
                                len(cookie_str.split("; ")) if cookie_str else 0,
                            )
                            self._set_status("success", "登录成功，Cookie 已保存")
                            return
                        time.sleep(self._poll_interval)
                    self._set_status("error", "登录超时，请重新点击登录并尽快完成")
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
        except Exception as exc:
            logger.exception("闲鱼一键登录失败")
            self._set_status("error", f"登录失败：{exc}"[:200])

    @staticmethod
    def _has_login_cookie(cookies) -> bool:
        return any(
            c.get("name") in LOGIN_COOKIES and c.get("value") for c in cookies
        )

    @staticmethod
    def _confirmed(page) -> bool:
        try:
            return (
                page.evaluate(
                    "document.documentElement.getAttribute('data-se-login-done') === '1'"
                )
                is True
            )
        except Exception:
            return False

    @staticmethod
    def _serialize(cookies) -> str:
        parts = []
        for c in cookies:
            domain = c.get("domain", "")
            if domain.endswith("goofish.com") and c.get("value"):
                parts.append(f"{c['name']}={c['value']}")
        return "; ".join(parts)
