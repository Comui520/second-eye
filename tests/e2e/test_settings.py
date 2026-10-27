from playwright.sync_api import expect


def test_category_save_and_invalid_input_keep_other_settings(live_app, browser_page):
    app, url, _ = live_app
    app.state.settings_service.set_many({"feishu_enabled": "1", "llm_api_key": "private"})
    page = browser_page
    page.goto(url + "/settings#models")
    page.locator("#llm_model").fill("offline-test-model")
    page.get_by_role("button", name="保存模型设置", exact=True).click()
    expect(page.locator("#llm_model")).to_have_value("offline-test-model")
    assert app.state.settings_service.get().feishu_enabled
    assert app.state.settings_service.get().llm_api_key == "private"
    page.locator("details").filter(has=page.locator("input[name=gotify_priority]")).locator("summary").click()
    page.locator("input[name=gotify_priority]").fill("11")
    # Force the server boundary rather than relying on HTML min/max validation.
    page.locator("#notifications").evaluate("form => {form.noValidate = true;}")
    page.get_by_role("button", name="保存通知设置", exact=True).click()
    expect(page.get_by_role("alert")).to_be_visible()
