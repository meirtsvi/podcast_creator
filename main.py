from pathlib import Path as p
import sys
import os
import traceback

from common import (
    create_episode_name, create_episode_description, create_previous_episodes_summaries,
    create_episode_folder, get_processed_urls, get_next_episode_number,
    upload_new_podcast_episode, OUTPUT_DIR, generate_title_from_url,
    PROMPT_FOR_SINGLE_URL_PODCAST_GENERATION, MULTI_URLS_LINKS_FILENAME,
    SINGLE_URL_LINKS_FILENAME, PROMPT_FOR_MULTI_URLS_PODCAST_GENERATION, EPISODE_URLS_FILENAME
)
from mp3 import add_pre_and_post_audio
from gen_podcast_text import generate_podcast_text
from gen_podcast_episode_from_text import generate_podcast_episode_audio_from_text


def process_batch(batch_number, episode_number, urls, titles):
    print(f"Processing batch {batch_number}")
    print(f"# of URLs in this batch: {len(urls)}")

    episode_name = create_episode_name(titles, episode_number)
    print(f'Episode name: {episode_name}')

    episode_description = create_episode_description(urls, titles)
    episode_description = episode_description.replace("```html", "").replace("```", "").replace("\n", "")
    print(f'Episode description: {episode_description}')

    # Create episode folder and write URLs, name and description
    episode_folder = f"Episode_{episode_number}"
    previous_summaries_file_path, episode_dir = create_episode_folder(episode_number, urls, episode_name, episode_description)

    episode_urls_file_path = episode_dir / EPISODE_URLS_FILENAME
    with open(episode_urls_file_path, 'w') as f:
        for url in urls:
            f.write(f"{url}\n")

    episode_full_path = p(OUTPUT_DIR) / episode_folder
    episode_audio_file_path = episode_full_path / f"{episode_folder}.mp3"

    if len(urls) > 1:
        prompt_prefix = f"קרא את כל הכתבות בלינקים האלו.: {urls}"
    else:
        prompt_prefix = f"הפרק הזה מוקדש לנושא אחד המפורט בכתבה הזו: {urls[0]}. קרא את הכתבה שבלינק הזה."
    podcast_text = generate_podcast_text(prompt_prefix,
                                         PROMPT_FOR_MULTI_URLS_PODCAST_GENERATION if len(urls) > 1 else PROMPT_FOR_SINGLE_URL_PODCAST_GENERATION, 
                                         episode_number)
    with open(episode_dir / "podcast_text.txt", "w", encoding="utf-8") as f:
        f.write(podcast_text)
    generate_podcast_episode_audio_from_text(podcast_text, episode_audio_file_path)
    add_pre_and_post_audio(episode_audio_file_path)
    upload_new_podcast_episode(episode_folder, episode_full_path, episode_audio_file_path)

    print(f"Completed processing batch {batch_number}")

def process_links(links_filename, batch_size, processed_urls):
    next_episode_number = get_next_episode_number()
    
    all_urls = []
    all_titles = []
    with open(links_filename, "r", encoding="utf-8") as f:
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
    
    # Process unprocessed URLs in batches of <batch_size>
    url_batches = [remaining_urls[i:i + batch_size] for i in range(0, len(remaining_urls), batch_size)]
    title_batches = [remaining_titles[i:i + batch_size] for i in range(0, len(remaining_titles), batch_size)]
    
    for batch_number, (urls, titles) in enumerate(zip(url_batches, title_batches), 1):
        process_batch(batch_number, next_episode_number + batch_number - 1, urls, titles)


def main():
    try:
        processed_urls = get_processed_urls()
        print(f"Found {len(processed_urls)} processed URLs")

        process_links(SINGLE_URL_LINKS_FILENAME, 1, processed_urls)
        process_links(MULTI_URLS_LINKS_FILENAME, 10, processed_urls)

    except ValueError as e:
        print(e)
        traceback.print_exc()
        sys.exit()

if __name__ == "__main__":
    os.environ["PYTHONHTTPSVERIFY"] = "0"
    main()
