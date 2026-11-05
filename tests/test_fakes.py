from tests.fakes import AwaitableMock


async def test_an_unconfigured_await_reports_nothing_found() -> None:
    page = AwaitableMock()
    assert await page.locator("x").count() == 0
    assert not await page.locator("x").first.is_visible()
    assert list(await page.evaluate("js")) == []
    assert (await page.evaluate("js")).get("key", "fallback") == "fallback"
