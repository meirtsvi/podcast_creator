from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://podcasters.spotify.com/login")

    input("Log in manually and press ENTER when done...")

    storage = context.storage_state(path="spotify_state.json")

    print("Login state saved!")

    browser.close()
