import time

from goodprice.crawler.login import LoginSession
from goodprice.services.settings_service import SettingsService


class FakePage:
    def goto(self, url, **kwargs):
        pass

    def __init__(self):
        self.confirmed = False

    def evaluate(self, expr):
        if "data-se-login-done" in expr:
            return self.confirmed
        return None


class FakeContext:
    def __init__(self, sequences):
        self._seq = list(sequences)
        self.closed = False
        self._close_handlers = []

    def new_page(self):
        self.page = FakePage()
        return self.page

    def add_init_script(self, script):
        self.init_script = script

    def on(self, event, handler):
        if event == "close":
            self._close_handlers.append(handler)

    def cookies(self):
        if len(self._seq) > 1:
            return self._seq.pop(0)
        return self._seq[0] if self._seq else []

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, sequences):
        self.sequences = sequences
        self.launch_count = 0

    def launch_persistent_context(self, user_data_dir, **kwargs):
        self.launch_count += 1
        self.last_context = FakeContext(self.sequences)
        return self.last_context


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = browser

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _wait_status(login, expected, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if login.status()[0] == expected:
            return True
        time.sleep(0.02)
    return False


def _login(settings_service, sequences, tmp_path, **kwargs):
    browser = FakeBrowser(sequences)
    kwargs.setdefault("timeout_seconds", 5)
    kwargs.setdefault("poll_interval", 0.05)
    login = LoginSession(
        settings_service,
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywright(browser),
        **kwargs,
    )
    return login, browser


def test_login_success_saves_goofish_cookie(session_factory, base_settings, tmp_path):
    settings_service = SettingsService(session_factory, base=base_settings)
    sequences = [
        [],
        [
            {"name": "t", "value": "abc", "domain": ".goofish.com"},
            {"name": "unb", "value": "dXNlcg==", "domain": ".taobao.com"},
            {"name": "ck", "value": "x", "domain": ".taobao.com"},
        ],
    ]
    login, browser = _login(settings_service, sequences, tmp_path)
    login.start()
    assert _wait_status(login, "success")
    assert browser.launch_count == 1
    assert settings_service.get().xianyu_cookie == "t=abc"


def test_anonymous_cookies_do_not_trigger_success(session_factory, base_settings, tmp_path):
    """回归：未登录时闲鱼也会种 t/cookie2/_tb_token_，绝不能误判成功。"""
    settings_service = SettingsService(session_factory, base=base_settings)
    anonymous = [
        {"name": "t", "value": "anon", "domain": ".goofish.com"},
        {"name": "cookie2", "value": "anon", "domain": ".goofish.com"},
        {"name": "_tb_token_", "value": "anon", "domain": ".goofish.com"},
    ]
    login, _ = _login(settings_service, [anonymous], tmp_path, timeout_seconds=0.3)
    login.start()
    assert _wait_status(login, "error")
    assert "登录超时" in login.status()[1]
    assert settings_service.get().xianyu_cookie == ""


def test_manual_confirm_saves_cookie(session_factory, base_settings, tmp_path):
    settings_service = SettingsService(session_factory, base=base_settings)
    sequences = [
        [
            {"name": "t", "value": "auth", "domain": ".goofish.com"},
        ]
    ]
    login, browser = _login(settings_service, sequences, tmp_path, timeout_seconds=5)
    login.start()
    assert _wait_status(login, "running")
    browser.last_context.page.confirmed = True  # 用户点击了窗口里的"登录完成"按钮
    assert _wait_status(login, "success")
    assert settings_service.get().xianyu_cookie == "t=auth"


def test_login_error_surfaces_message(session_factory, base_settings, tmp_path):
    settings_service = SettingsService(session_factory, base=base_settings)

    def boom():
        raise RuntimeError("无法启动浏览器")

    login = LoginSession(
        settings_service,
        profile_dir=tmp_path / "profile",
        playwright_factory=boom,
    )
    login.start()
    assert _wait_status(login, "error")
    assert "无法启动浏览器" in login.status()[1]


def test_login_timeout_reports_error(session_factory, base_settings, tmp_path):
    settings_service = SettingsService(session_factory, base=base_settings)
    login, _ = _login(settings_service, [[]], tmp_path, timeout_seconds=0.3)
    login.start()
    assert _wait_status(login, "error")
    assert "登录超时" in login.status()[1]


def test_login_ignores_duplicate_start(session_factory, base_settings, tmp_path):
    settings_service = SettingsService(session_factory, base=base_settings)
    login, browser = _login(settings_service, [[]], tmp_path, timeout_seconds=5)
    login.start()
    login.start()
    assert browser.launch_count == 1
    login.stop()


def test_login_status_exposed(session_factory, base_settings, tmp_path):
    settings_service = SettingsService(session_factory, base=base_settings)
    login, _ = _login(settings_service, [[]], tmp_path, timeout_seconds=5)
    assert login.status() == ("idle", "")
    login.start()
    assert login.status()[0] == "running"
    login.stop()
