from pathlib import Path as p
import re
import glob
import os
import requests
import time

from google import genai
from bs4 import BeautifulSoup

from config import Configuration, EPISODE_TITLE_FILENAME, EPISODE_DESC_FILENAME, EPISODE_URLS_FILENAME
from url_to_md import get_markdown_from_url
from logger import logger
from youtube_content_extractor import extract_content_from_youtube

def call_genai_api(prompt):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"Calling GenAI API with prompt: {prompt}")
            response = client.models.generate_content(
                #model="gemini-2.0-flash", contents=prompt
               model="gemini-2.5-pro", contents=prompt
            )
            ret = response.text
            break  # Success, exit the loop
        except Exception as e:
            if attempt < max_retries - 1:  # If not the last attempt
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}. Retrying in 10 seconds...")
                time.sleep(10)  # Wait for 10 seconds before retrying
            else:
                logger.error(f"All {max_retries} attempts failed. Last error: {str(e)}")
                raise  # Re-raise the last exception if all retries failed

    logger.info(f"GenAI API returned: {ret}")
    ret = response.text.strip()
    return ret

def create_episode_title(configuration: Configuration, titles: [], episode_number: int):
    prompt = configuration.prompt_for_episode_title_generation
    prompt += "\n".join(titles)
    title = "פרק " + str(episode_number) + " - " + call_genai_api(prompt)
    final_title = call_genai_api(f"Translate the following to {configuration.output_language}: "
                                 f"'{title}'."
                                 "Provide only the translated text without any additional text."
                                 "Keep as a plain text. no newlines.")
    return final_title

def create_episode_description(configuration: Configuration, urls: [], titles: []):
    prompt = configuration.prompt_for_episode_description_generation + ". Format text " + configuration.text_direction + "."
    url_title_pairs = [f"{url} {title}" for url, title in zip(urls, titles)]
    prompt += "\n".join(url_title_pairs)
    desc = call_genai_api(prompt)
    final_desc = call_genai_api(f"Translate the following text to {configuration.output_language}: "
                                f": '{desc}'."
                                f"Provide only the translated text and links without any additional text."
                                f"Keep html format. Each link in a separate line.")
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

        logger.info(f"Found {len(urls)} batches of URLs to process.\n")
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
            logger.error(f"Error reading {url_file}: {str(e)}")

    logger.info(f"Found {len(processed_urls)} processed {configuration.output_language} URLs")
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
    
    logger.info(f"Created episode folder {episode_dir} with {len(configuration.episode_urls)} URLs")
    return previous_summaries_file_path

def generate_title_from_url(url):
    try:
        _, response = get_markdown_from_url(url)
        final_url = response.url
        logger.info(f"Original URL: {url}")
        logger.info(f"Final URL after redirects: {final_url}")
        if "github.com" in final_url:
            logger.info(f"Skipping {url} because it's a GitHub page")
            return False, "", ""
        if "youtube.com" in final_url or "youtu.be" in final_url:
            title, _, _ = extract_content_from_youtube(final_url)
            return True, title, final_url
        soup = BeautifulSoup(response.text, 'html.parser')
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            if " - by " in title:
                title = title.split(" - by ")[0].strip()
            soup.title.string = title
            return True, soup.title.string.strip(), final_url
    except Exception as e:
        logger.error(f"Error extracting title from {url}: {str(e)}")
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

    logger.info(f"Created previous episodes summaries file: {output_file}")
    return output_file

def get_episodes_with_missing_audio(configuration: Configuration) -> list:
    """Get a list of episodes that have no audio files."""
    missing_audio_episodes = []
    podcast_dir = p(configuration.podcast_root_folder)
    episode_folders = glob.glob(str(podcast_dir / "Episode_*"))
    for folder in episode_folders:
        episode_number = folder.split("Episode_")[-1]
        configuration.set_episode_details(episode_number, "", "")
        audio_file_path = p(folder) / configuration.episode_audio_filename
        if not audio_file_path.exists():
            missing_audio_episodes.append((episode_number, p(folder)))

    return missing_audio_episodes