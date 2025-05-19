from playwright.sync_api import sync_playwright

def save_google_session(state_path="state.json", url="https://notebooklm.google.com/"):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,  # headful to avoid CAPTCHA
            args=["--disable-blink-features=AutomationControlled"]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York"
        )

        page = context.new_page()
        page.goto(url)
        page.wait_for_load_state("networkidle")

        print(">> Please complete Google login manually...")
        input(">> Press Enter once you're fully logged in...")

        context.storage_state(path=state_path)
        print(f">> Session saved to {state_path}")
        browser.close()

# Run it
if __name__ == "__main__":
    save_google_session()

