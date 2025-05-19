from playwright.sync_api import sync_playwright

def refresh_google_session(state_path="state.json", url="https://notebooklm.google.com/"):
    with sync_playwright() as p:
        browser = p.chromium.launch(
			channel="chrome",
			headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )

        context = browser.new_context(
            storage_state=state_path,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York"
        )

        page = context.new_page()
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")  # faster and more stable
        page.wait_for_timeout(5000)  # let things settle

        if "accounts.google.com" in page.url:
            print("!! Session expired. Run save_google_state.py again.")
        else:
            print(">> Session refreshed successfully.")
            page.wait_for_timeout(5000)  # simulate activity
            context.storage_state(path=state_path)
            print(f">> Session state updated in {state_path}")

        browser.close()

# Run it
if __name__ == "__main__":
    refresh_google_session()
