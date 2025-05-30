from pathlib import Path as p
import re
import glob
import os
import requests
import time

from google import genai
from bs4 import BeautifulSoup
from transistor import upload_episode_to_transistor

from config import Configuration, EPISODE_TITLE_FILENAME, EPISODE_DESC_FILENAME, EPISODE_URLS_FILENAME
from url_to_md import get_markdown_from_url


def call_genai_api(prompt):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash", contents=prompt
            )
            ret = response.text.strip()
            break  # Success, exit the loop
        except Exception as e:
            if attempt < max_retries - 1:  # If not the last attempt
                print(f"Attempt {attempt + 1} failed: {str(e)}. Retrying in 10 seconds...")
                time.sleep(10)  # Wait for 10 seconds before retrying
            else:
                print(f"All {max_retries} attempts failed. Last error: {str(e)}")
                raise  # Re-raise the last exception if all retries failed

    ret = response.text.strip()
    return ret

def create_episode_title(configuration: Configuration, titles: [], episode_number: int):
    prompt = configuration.prompt_for_episode_title_generation
    prompt += "\n".join(titles)
    title = "פרק " + str(episode_number) + " - " + call_genai_api(prompt)
    final_title = call_genai_api("Translate this title to " + configuration.output_language + " language: " + title + ". Provide only the translation without any additional text as a plain text. no newlines.")
    return final_title

def create_episode_description(configuration: Configuration, urls: [], titles: []):
    prompt = configuration.prompt_for_episode_description_generation + ". Format text " + configuration.text_direction + "."
    url_title_pairs = [f"{url} {title}" for url, title in zip(urls, titles)]
    prompt += "\n".join(url_title_pairs)
    desc = call_genai_api(prompt)
    final_desc = call_genai_api("Translate this text to " + configuration.output_language + " language: " + desc + ". Provide only the translation and links without any additional text. Keep html format.")
    return final_desc

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

def get_processed_urls(configuration: Configuration) -> set:
    """Read all URLs from episode folders' urls.txt files."""
    processed_urls = set()

    # Find all urls.txt files in episode folders
    url_files = glob.glob(str(p(configuration.podcast_root_folder) / "Episode_*" / "urls.txt"))
    for url_file in url_files:
        try:
            with open(url_file, 'r') as f:
                urls = [line.strip() for line in f if line.strip()]
                processed_urls.update(urls)
        except Exception as e:
            print(f"Error reading {url_file}: {str(e)}")

    print(f"Found {len(processed_urls)} processed URLs")
    return processed_urls

def get_next_episode_number(configuration: Configuration) -> int:
    """Get the next episode number based on existing episode folders."""
    podcast_dir = p(configuration.podcast_root_folder)
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

def create_episode_folder(configuration: Configuration):
    # Create episode directory if it doesn't exist
    configuration.episode_folder.mkdir(exist_ok=True)

    previous_summaries_file_path = create_previous_episodes_summaries(configuration)

    episode_dir = p(configuration.episode_folder)
    # Write podcast episode name to a file
    title_filename = episode_dir / EPISODE_TITLE_FILENAME
    with open(title_filename, 'w', encoding="utf-8") as f:
        f.write(configuration.episode_title)
    
    # Write podcast episode description a file
    desc_file = episode_dir / EPISODE_DESC_FILENAME
    with open(desc_file, 'w', encoding="utf-8") as f:
        f.write(configuration.episode_description)
    
    print(f"Created episode folder {episode_dir} with {len(configuration.episode_urls)} URLs")
    return previous_summaries_file_path

def upload_new_podcast_episode(configuration: Configuration):
    episode_folder = configuration.episode_folder
    print(f"Uploading new podcast episode from {str(episode_folder)}...")
    if not configuration.transistor_show_id == "0":
        upload_episode_to_transistor(configuration)
    print(f"Uploaded new podcast episode {configuration.episode_title} to Transistor.")

def generate_title_from_url(url):
    try:
        _, response = get_markdown_from_url(url)
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
    prompt = f"Generate a concise, informative title for this article URL. Don't print 'here is concise...', just give the title: {final_url if 'final_url' in locals() else url}"
    return True, call_genai_api(prompt), url

def create_previous_episodes_summaries(configuration: Configuration):

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

    root_folder = p(configuration.podcast_root_folder)
    output_file = os.path.join(configuration.episode_folder, "summaries.txt")

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
