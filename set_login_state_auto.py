import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(storage_state=str("state.json"))
    page = context.new_page()

    page.goto("https://notebooklm.google.com/")
    page.get_by_label("NotebookLM Homepage").click()
    time.sleep(5)

    storage = context.storage_state(path="state.json")

    print("Login state saved!")

    browser.close()
