from playwright.sync_api import expect


def test_load_more_detail_back_and_refresh_keep_context(live_app, browser_page):
    _, url, task_id = live_app
    page = browser_page
    page.goto(f"{url}/listings?task_id={task_id}&show=all&sort=newest")
    expect(page.locator("[data-listing-id]")).to_have_count(100)
    expect(page.locator("#listings-grid")).to_have_css("display", "grid")
    page.evaluate("window.incrementalNavigationMarker = 'preserve'")
    page.locator("#load-more").click()
    expect(page.locator("[data-listing-id]")).to_have_count(200)
    assert page.evaluate("window.incrementalNavigationMarker") == "preserve"
    expect(page).to_have_url(__import__('re').compile(r"loaded=200"))
    page.locator("[data-listing-id] .btn-primary").nth(150).click()
    page.get_by_role("link", name="← 返回命中列表").click()
    expect(page.locator("[data-listing-id]")).to_have_count(200)
    expect(page.locator("select[name=task_id]")).to_have_value(str(task_id))
    expect(page.locator("select[name=show]")).to_have_value("all")
    expect(page.locator("select[name=sort]")).to_have_value("newest")
    page.reload()
    expect(page.locator("[data-listing-id]")).to_have_count(200)
    page.locator("#load-more").click()
    expect(page.locator("[data-listing-id]")).to_have_count(205)
    ids = page.locator("[data-listing-id]").evaluate_all("els => els.map(el => el.dataset.listingId)")
    assert len(ids) == len(set(ids))


def test_native_back_and_block_keep_context(live_app, browser_page):
    _, url, task_id = live_app
    page = browser_page
    page.goto(f"{url}/listings?task_id={task_id}&show=all&sort=price_asc&loaded=200")
    page.locator("[data-listing-id] .btn-primary").first.click()
    page.go_back()
    expect(page.locator("[data-listing-id]")).to_have_count(200)
    page.locator("[data-listing-id] form[action*='/block'] button").first.click()
    expect(page.locator("select[name=show]")).to_have_value("all")
    expect(page.locator("[data-listing-id]")).to_have_count(200)
    page.locator("[data-listing-id] .btn-primary").first.click()
    page.get_by_role("link", name="← 返回命中列表").click()
    expect(page.locator("select[name=show]")).to_have_value("all")
    expect(page.locator("[data-listing-id]")).to_have_count(200)


def test_poll_does_not_clear_selection_or_scroll(live_app, browser_page):
    app, url, task_id = live_app
    app.state.guard.try_start(task_id)
    page = browser_page
    try:
        page.goto(f"{url}/listings?task_id={task_id}&show=all")
        page.locator(".row-check").first.check()
        page.evaluate("window.scrollTo(0, 400)")
        page.evaluate("htmx.ajax('GET', document.getElementById('list-poller').getAttribute('hx-get'), {source: document.getElementById('list-poller'), target:'#listings-grid', swap:'outerHTML'})")
        expect(page.locator("#new-list-results")).to_be_visible()
        expect(page.locator(".row-check").first).to_be_checked()
        assert page.evaluate("window.scrollY") >= 350
    finally:
        app.state.guard.finish(task_id)


def test_core_navigation_works_without_javascript(live_app, browser_page):
    _, url, task_id = live_app
    browser = browser_page.context.browser
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{url}/listings?task_id={task_id}&show=all&sort=newest")
        page.locator("#load-more").click()
        expect(page.locator("[data-listing-id]")).to_have_count(200)
        page.locator("[data-listing-id] .btn-primary").first.click()
        page.get_by_role("link", name="← 返回命中列表").click()
        expect(page.locator("[data-listing-id]")).to_have_count(200)
    finally:
        context.close()


def test_main_pages_fit_narrow_viewport(live_app, browser_page):
    _, url, _ = live_app
    page = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    for path in ["/", "/tasks", "/listings", "/settings", "/notifications"]:
        page.goto(url + path)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), path
