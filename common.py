from pathlib import Path as p
import re
import glob
from time import sleep
import os
from dotenv import load_dotenv
import requests

from playwright.sync_api import expect
from google import genai
from bs4 import BeautifulSoup

# Load environment variables from .env file
load_dotenv()

# Constants
URLS_TO_MONITOR_PATH = p(__file__).parent / "urls_to_monitor.txt"
PROMPT_FOR_PODCAST_EPISODE_NAME_FILENAME = "prompt_for_podcast_episode_name.txt"
PROMPT_FOR_PODCAST_EPISODE_DESC_FILENAME = "prompt_for_podcast_episode_desc.txt"
PROMPT_FOR_PODCAST_GENERATION = "prompt_for_podcast_generation.txt"
EPISODE_NAME_FILENAME = "episode_name.txt"
EPISODE_DESC_FILENAME = "episode_desc.txt"
OUTPUT_DIR = os.getenv("OUTPUT_DIR")

def call_genai_api(prompt):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model="gemini-2.0-flash", contents=prompt
    )
    ret = response.text.strip()
    return ret

def create_notebook_name(titles):
    with open(PROMPT_FOR_PODCAST_EPISODE_NAME_FILENAME, "r", encoding="utf-8") as f:
        prompt = f.read()
    prompt = prompt + "\n".join(titles)
    return call_genai_api(prompt)

def create_podcast_description(urls, titles):
    with open(PROMPT_FOR_PODCAST_EPISODE_DESC_FILENAME, "r", encoding="utf-8") as f:
        prompt = f.read()
    url_title_pairs = [f"{url} {title}" for url, title in zip(urls, titles)]
    prompt = prompt + "\n".join(url_title_pairs)
    return call_genai_api(prompt)

def create_source_list(source_type, batch_size=10) -> list:
    root_directory = p(__file__).parent
    file_name = root_directory / "sources" / f"{source_type}_links.csv"
    if not file_name.exists():
        raise ValueError(
            f"{source_type}_links.csv doesn't exist or is in the wrong location."
        )

    with open(str(file_name), mode="r", encoding="utf-8", newline="") as contents:
        # Read URLs in batches
        urls = []
        batch = []
        for line in contents:
            link = line.strip()
            if link.endswith("/"):
                link = link[:-1]
            if link:
                batch.append(link)
                if len(batch) >= batch_size:
                    urls.append(batch)
                    batch = []
        
        # Add remaining URLs if any
        if batch:
            urls.append(batch)

        if not urls:
            raise ValueError(f"Error: {source_type}_links.csv does not contain any records.")

        print(f"Found {len(urls)} batches of URLs to process.\n")
        return urls

def add_source_links_to_notebook(source_type: str, urls: list, page) -> None:
    url_count = len(urls)
    is_first = 0
    is_last = url_count - 1

    print(f"Attempting to add {url_count} sources from provided {source_type}_links.csv file...")

    page.goto("https://notebooklm.google.com/")

    for i, u in enumerate(urls):
        if i == is_first:
            new_notebook_button = page.get_by_role("button", name="Create new notebook")
            new_notebook_button.wait_for(state="attached")
            new_notebook_button.click()

        link_button = page.locator(
            "span.mdc-evolution-chip__text-label", has_text=re.compile(f"{source_type}",re.I)
        )
        link_button.wait_for(state="attached")
        link_button.click()

        link_url_input = page.locator("[formcontrolname='newUrl']")
        link_url_input.wait_for(state="attached")
        link_url_input.fill(u)

        insert_button = page.get_by_role("button", name="Insert")
        expect(insert_button).to_be_enabled()
        insert_button.click()

        source_container = page.locator("div.single-source-container").last
        source_container.wait_for(state="attached")

        loading_spinner = source_container.locator(".mat-mdc-progress-spinner")
        loading_spinner.wait_for(state="detached")

        checkbox = source_container.locator(
            "input.mdc-checkbox__native-control.mdc-checkbox--selected"
        )
        checkbox.wait_for(state="attached")
        expect(checkbox).not_to_have_attribute("ariaLabel", u)

        page.wait_for_timeout(1200)

        if i < is_last:
            add_source_button = page.get_by_role("button", name="Add source")
            add_source_button.wait_for(state="attached")
            add_source_button.click()

        print(f"Source {i+1}/{url_count} ({u}) added.")

def get_processed_urls():
    """Read all URLs from episode folders' urls.txt files."""
    processed_urls = set()

    # Find all urls.txt files in episode folders
    url_files = glob.glob(str(p(OUTPUT_DIR) / "Episode_*" / "urls.txt"))
    for url_file in url_files:
        try:
            with open(url_file, 'r') as f:
                urls = [line.strip() for line in f if line.strip()]
                processed_urls.update(urls)
        except Exception as e:
            print(f"Error reading {url_file}: {str(e)}")
    
    return processed_urls

def get_next_episode_number():
    """Get the next episode number based on existing episode folders."""
    episode_folders = glob.glob(str(p(OUTPUT_DIR) / "Episode_*"))
    
    if not episode_folders:
        return 1
    
    # Extract numbers from folder names and find the highest
    episode_numbers = []
    for folder in episode_folders:
        try:
            num = int(folder.split("Episode_")[-1])
            episode_numbers.append(num)
        except ValueError:
            continue
    
    return max(episode_numbers) + 1 if episode_numbers else 1

def create_episode_folder(episode_number, urls, notebook_name, podcast_description):
    """Create a new episode folder with urls.txt, name.txt and description.txt files."""
    episode_dir = p(OUTPUT_DIR) / f"Episode_{episode_number}"
    
    # Create episode directory if it doesn't exist
    episode_dir.mkdir(exist_ok=True)
    
    # Write URLs to urls.txt
    urls_file = episode_dir / "urls.txt"
    with open(urls_file, 'w') as f:
        for url in urls:
            f.write(f"{url}\n")
    
    # Write podcast episode name to a file
    name_file = episode_dir / EPISODE_NAME_FILENAME
    with open(name_file, 'w', encoding="utf-8") as f:
        f.write(notebook_name)
    
    # Write podcast episode description a file
    desc_file = episode_dir / EPISODE_DESC_FILENAME
    with open(desc_file, 'w', encoding="utf-8") as f:
        f.write(podcast_description)
    
    print(f"Created episode folder {episode_dir} with {len(urls)} URLs")

def add_url_to_monitor(page, episode_folder):
    """Write notebook URL and episode folder name to monitoring file."""
    with open(URLS_TO_MONITOR_PATH, "a") as f:
        f.write(f"{page.url}|{episode_folder}\n")
    
    print(f"The URL {page.url} has been added to the monitoring file for {episode_folder}")

def remove_url_from_monitor(url):
    """Remove a processed URL from the monitoring list."""
    with open(URLS_TO_MONITOR_PATH, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    
    # Find and remove the line containing the URL
    updated_lines = [line for line in lines if not line.startswith(url + "|")]
    
    with open(URLS_TO_MONITOR_PATH, "w") as f:
        f.write("\n".join(updated_lines) + "\n")
    print(f"Removed {url} from monitoring list")

def upload_new_podcast_episode(browser, episode_folder, episode_full_path, episode_audio_file_path):
    print(f"Uploading new podcast episode from {episode_full_path}...")
    match = re.search(r"Episode_(\d+)", episode_folder)
    if not match:
        raise ValueError(f"Episode number not found in {episode_folder}")
    episode_number = match.group(1)
    episode_name_file = episode_full_path / EPISODE_NAME_FILENAME
    with open(episode_name_file, 'r', encoding="utf-8") as f:
        episode_name = f.read()

    desc_file = episode_full_path / EPISODE_DESC_FILENAME
    with open(desc_file, 'r', encoding="utf-8") as f:
        episode_desc = f.read()

    login_state_path = "spotify_state.json"
    context = browser.new_context(storage_state=str(login_state_path))
    page = context.new_page()
    page.goto("https://creators.spotify.com/pod/dashboard/home")
    page.wait_for_load_state()
    page.get_by_role("link", name="Create a new episode").click()
    page.locator('input[type="file"]').set_input_files(episode_audio_file_path)
    page.get_by_placeholder("Give your episode a name").click()
    page.get_by_placeholder("Give your episode a name").fill(episode_name)
    page.get_by_label("Episode info").get_by_role("paragraph").click()
    page.get_by_role("textbox").nth(1).fill(episode_desc)
    page.locator("#season-number").click()
    page.locator("#season-number").fill("1")
    page.locator("#episode-number").click()
    page.locator("#episode-number").fill(episode_number)
    page.get_by_label("Cookie banner").get_by_label("Close").click()
    page.get_by_role("button", name="Next").click()
    page.locator("label").filter(has_text="Now").locator("span").first.click()
    page.get_by_role("button", name="Publish").click()
    sleep(10)
    context.close()
    print(f"Uploaded new podcast episode {episode_name} to Spotify.")

def generate_title_from_url(url):
    try:
        response = requests.get(url, verify=False, allow_redirects=True, timeout=30)
        final_url = response.url
        print(f"Original URL: {url}")
        print(f"Final URL after redirects: {final_url}")
        if "github.com" in final_url:
            print(f"Skipping {url} because it's a GitHub page")
            return False, "", ""
        soup = BeautifulSoup(response.text, 'html.parser')
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            if " - by " in title:
                title = title.split(" - by ")[0].strip()
            soup.title.string = title
            return True, soup.title.string.strip(), final_url
    except Exception as e:
        print(f"Error extracting title from {url}: {str(e)}")
        return False, "", ""
    prompt = f"Generate a concise, informative title for this article URL: {final_url if 'final_url' in locals() else url}"
    return True, call_genai_api(prompt), url
