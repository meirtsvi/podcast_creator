import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(storage_state=str("spotify_state.json"))
    page = context.new_page()

    page.goto("https://creators.spotify.com/pod/dashboard/home")
    page.get_by_label("Episodes").click()
    time.sleep(5)

    storage = context.storage_state(path="spotify_state.json")

    print("Login state saved!")

    browser.close()
