from pathlib import Path as p
import re
import glob
import os
from dotenv import load_dotenv
import requests

from google import genai
from bs4 import BeautifulSoup

from transistor import upload_episode_to_transistor

# Load environment variables from .env file
load_dotenv()

# Constants
MULTI_URLS_LINKS_FILENAME = "sources/website_multi_links.csv"
SINGLE_URL_LINKS_FILENAME = "sources/website_single_links.csv"
PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_NAME_FILENAME = "prompt_for_multi_urls_podcast_episode_name.txt"
PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_NAME_FILENAME = "prompt_for_single_url_podcast_episode_name.txt"
PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_DESC_FILENAME = "prompt_for_multi_urls_podcast_episode_desc.txt"
PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_DESC_FILENAME = "prompt_for_single_url_podcast_episode_desc.txt"
PROMPT_FOR_MULTI_URLS_PODCAST_GENERATION = "prompt_for_multi_urls_podcast_generation.txt"
PROMPT_FOR_SINGLE_URL_PODCAST_GENERATION = "prompt_for_single_url_podcast_generation.txt"
EPISODE_NAME_FILENAME = "episode_name.txt"
EPISODE_DESC_FILENAME = "episode_desc.txt"
EPISODE_URLS_FILENAME = "urls.txt"
OUTPUT_DIR = os.getenv("OUTPUT_DIR")

def call_genai_api(prompt):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model="gemini-2.0-flash", contents=prompt
    )
    ret = response.text.strip()
    return ret

def create_episode_name(titles, episode_number):
    prompt_filename = PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_NAME_FILENAME if len(titles) > 1 else PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_NAME_FILENAME
    with open(prompt_filename, "r", encoding="utf-8") as f:
        prompt = f.read()
    prompt = prompt + "\n".join(titles)
    return "פרק " + str(episode_number) + " - " + call_genai_api(prompt)

def create_episode_description(urls, titles):
    prompt_filename = PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_DESC_FILENAME if len(urls) > 1 else PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_DESC_FILENAME
    with open(prompt_filename, "r", encoding="utf-8") as f:
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
    podcast_dir = p(OUTPUT_DIR)
    episode_folders = glob.glob(str(podcast_dir / "Episode_*"))
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

    previous_summaries_file_path = create_previous_episodes_summaries(episode_dir)

    # Write podcast episode name to a file
    name_file = episode_dir / EPISODE_NAME_FILENAME
    with open(name_file, 'w', encoding="utf-8") as f:
        f.write(notebook_name)
    
    # Write podcast episode description a file
    desc_file = episode_dir / EPISODE_DESC_FILENAME
    with open(desc_file, 'w', encoding="utf-8") as f:
        f.write(podcast_description)
    
    print(f"Created episode folder {episode_dir} with {len(urls)} URLs")
    return previous_summaries_file_path, episode_dir

def upload_new_podcast_episode(episode_folder, episode_full_path, episode_audio_file_path):
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

    upload_episode_to_transistor("1", episode_number, episode_name, episode_desc, episode_audio_file_path)
    print(f"Uploaded new podcast episode {episode_name} to Transisotr.")

def generate_title_from_url(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.202 Safari/537.36'
        }
        response = requests.get(url, verify=False, allow_redirects=True, timeout=30, headers=headers)
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
    prompt = f"Generate a concise, informative title for this article URL. Don't print 'here is consise...', just give the title: {final_url if 'final_url' in locals() else url}"
    return True, call_genai_api(prompt), url

def create_previous_episodes_summaries(episode_folder):

    def read_file_with_fallback(path):
        encodings = ['utf-8', 'cp1255', 'iso-8859-1']
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read().strip()
            except UnicodeDecodeError:
                continue
        # If all encodings fail, read as binary and replace errors
        with open(path, "rb") as f:
            return f.read().decode('utf-8', errors='replace').strip()

    root_folder = p(OUTPUT_DIR)
    output_file = os.path.join(episode_folder, "summaries.txt")

    with open(output_file, "w", encoding="utf-8") as out_f:
        for subfolder in os.listdir(root_folder):
            match = re.match(r"Episode_(\d+)", subfolder)
            if match:
                episode_number = match.group(1)
                episode_desc_path = os.path.join(root_folder, subfolder, "episode_desc.txt")
                if os.path.isfile(episode_desc_path):
                    desc_content = read_file_with_fallback(episode_desc_path)
                    out_f.write(f"Episode {episode_number} summary:\n{desc_content}\n-------\n")

    print(f"Summaries written to: {output_file}")
    return output_file
