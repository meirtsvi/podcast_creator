from pathlib import Path as p
import sys
import time
import os
import shutil
import traceback
from playwright.sync_api import sync_playwright

from common import (
    add_source_links_to_notebook, remove_url_from_monitor, add_url_to_monitor,
    create_notebook_name, create_podcast_description,
    URLS_TO_MONITOR_PATH, create_episode_folder, get_processed_urls, get_next_episode_number,
    upload_new_podcast_episode, OUTPUT_DIR, PROMPT_FOR_PODCAST_GENERATION, generate_title_from_url
)
from mp3 import add_pre_and_post_audio

# Set environment variable to ignore SSL
os.environ["PYTHONHTTPSVERIFY"] = "0"

def set_podcast_params_and_run(page):
        with open(PROMPT_FOR_PODCAST_GENERATION, "r", encoding="utf-8") as f:
            prompt = f.read()
        print("Setting podcast params and running.\n")
        page.get_by_role("button").filter(has_text="tune").click()
        page.get_by_role("radio", name="Analyst prompt button").click()
        page.get_by_role("radio", name="Verbose style guide button").click()
        page.get_by_role("button", name="Button to save the settings").click()
        page.get_by_role("button", name="Customize").click()
        page.get_by_role("textbox", name="'Text area for steering the").click()
        page.get_by_role("textbox", name="'Text area for steering the").fill(prompt)
        page.get_by_role("textbox", name="'Text area for steering the").click()
        page.get_by_role("button", name="Generate").click()
        page.wait_for_timeout(1000)
        print("Podcast generation started.\n")

def create_notebook_from_sources(page, urls, notebook_name):
        add_source_links_to_notebook("website", urls, page)
        print("Finished adding sources.")
        title_box = page.locator(".title-input")
        title_box.click()
        page.keyboard.press("Control+A")
        title_box.fill(notebook_name)
        title_box.press("Enter")
        page.wait_for_timeout(1000)
        print("Title updated!\n")

def monitor_notebooks(browser, login_state_path):
    with open(URLS_TO_MONITOR_PATH, "r") as f:
        urls = [line.strip().split("|") for line in f if line.strip()]
    
    if not urls:
        print("No URLs to monitor")
        return False

    for url, episode_folder in urls:
        try:
            print(f"Processing URL: {url} for {episode_folder}\n")
            context = browser.new_context(storage_state=str(login_state_path))
            page = context.new_page()
            page.goto(url)
            page.wait_for_load_state()
            
            try:
                audio_button = page.get_by_role("button", name="Load the audio overview")
                audio_button.wait_for(state="visible", timeout=15000)
                audio_button.click()
            except Exception as e:
                print(f"Audio button not found or not visible after 15 seconds for {url}: {str(e)}")
                traceback.print_exc()
                continue
                
            try:
                # Wait for the options button to appear
                options_button = page.get_by_role("button", name="See more options for audio")
                options_button.wait_for(state="visible", timeout=15000)
                options_button.click()
                print("Options button clicked!")
                
                # Download the audio
                with page.expect_download() as download_info:
                    page.get_by_role("menuitem", name="Download").click()
                    print("Download button clicked!")
                    
                    # Get the download object and wait for it to complete
                    download = download_info.value
                    print(f"Download_info: {download_info}")
                    print(f"value: {download}")
                    
                    # Get the downloaded file path
                    download_path = p(download.path())
                    print(f"Download completed: {download_path.name}")
                    
                    # Move to episode folder with episode name
                    episode_full_path = p(OUTPUT_DIR) / episode_folder
                    episode_audio_file_path = episode_full_path / f"{episode_folder}.mp3"
                    
                    try:
                        shutil.copy2(download_path, episode_audio_file_path)
                        print(f"File copied successfully to: {episode_audio_file_path}")
                    except Exception as e:
                        print(f"Error copying file: {str(e)}")
                        traceback.print_exc()

                add_pre_and_post_audio(episode_audio_file_path)
                upload_new_podcast_episode(browser, episode_folder, episode_full_path, episode_audio_file_path)

                print(f"Processing complete for {url}")
                remove_url_from_monitor(url)
                
            except Exception as e:
                print(f"Error processing audio for {url}: {str(e)}")
                traceback.print_exc()
                continue
            
            finally:
                # Close the context/page
                context.close()
                
        except Exception as e:
            print(f"Error processing URL {url}: {str(e)}")
            traceback.print_exc()
            continue
            
        # Add small delay between URLs
        time.sleep(2)
    return True

def process_batch(batch_number, urls, browser, login_state_path, episode_number, titles):
    print(f"Processing batch {batch_number}")
    print(f"# of URLs in this batch: {len(urls)}")

    notebook_name = create_notebook_name(titles)
    print(f'Notebook name: {notebook_name}')

    podcast_description = create_podcast_description(urls, titles)
    print(f'Podcast description: {podcast_description}')

    # Create episode folder and write URLs, name and description
    episode_folder = f"Episode_{episode_number}"
    create_episode_folder(episode_number, urls, notebook_name, podcast_description)
    
    context = browser.new_context(storage_state=str(login_state_path))
    page = context.new_page()

    page.goto("https://notebooklm.google.com/")
    page.wait_for_load_state()

    create_notebook_from_sources(page, urls, notebook_name)
    set_podcast_params_and_run(page)
    add_url_to_monitor(page, episode_folder)
    context.close()
    
    print(f"Completed processing batch {batch_number}")

def main():
    try:
        # Get processed URLs and next episode number
        processed_urls = get_processed_urls()
        next_episode_number = get_next_episode_number()
        print(f"Found {len(processed_urls)} processed URLs")
        
        # Get all URLs from website_links.csv
        all_urls = []
        all_titles = []
        with open("sources/website_links.csv", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split(',', 1)
                    url = parts[0]
                    title = parts[1] if len(parts) > 1 else ""
                    if title == "":
                        valid, title, url = generate_title_from_url(url)
                        if not valid:
                            continue
                    all_urls.append(url.rstrip('/'))
                    all_titles.append(title)
        
        # Filter out processed URLs
        remaining_urls = []
        remaining_titles = []
        for url, title in zip(all_urls, all_titles):
            if url not in processed_urls:
                remaining_urls.append(url)
                remaining_titles.append(title)
        print(f"Found {len(remaining_urls)} remaining URLs to process")
        
        # Process remaining URLs in batches of 10
        url_batches = [remaining_urls[i:i + 10] for i in range(0, len(remaining_urls), 10)]
        title_batches = [remaining_titles[i:i + 10] for i in range(0, len(remaining_titles), 10)]
        login_state_path = p(__file__).parent / "state.json"
        
        with sync_playwright() as sp:
            browser = sp.chromium.launch(headless=True, channel="chrome")
            for batch_number, (urls, titles) in enumerate(zip(url_batches, title_batches), 1):
                process_batch(batch_number, urls, browser, login_state_path, next_episode_number + batch_number - 1, titles)
                # Small delay between batches
                time.sleep(5)
            
            # After all notebooks are created, start monitoring them
            print("Starting to monitor notebooks for completion...")
            while monitor_notebooks(browser, login_state_path):
                time.sleep(10)
            browser.close()
            
    except ValueError as e:
        print(e)
        traceback.print_exc()
        sys.exit()

if __name__ == "__main__":
    main()
