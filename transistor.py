import os
import requests
import dotenv
from pathlib import Path as p

import sendmail
from logger import logger

from config import Configuration
from utils import read_file_content

dotenv.load_dotenv()

TRANSISTOR_API_KEY = os.getenv("TRANSISTOR_API_KEY")

def upload_new_podcast_episode(configuration: Configuration):
    if configuration.transistor_show_id == "0":
        logger.warning("Transistor show ID is not set. Skipping upload.")
        return

    episode_folder = configuration.episode_folder
    logger.info(f"Uploading new podcast episode from {str(episode_folder)}...")
    upload_episode_to_transistor(configuration)
    message = f"New podcast {configuration.output_language} episode #{configuration.episode_number} uploaded to Transistor.fm"
    logger.info(message)
    drafts_link = f"https://dashboard.transistor.fm/shows/{configuration.transistor_show_identifier}/episodes"
    sendmail.send_email(send_to=os.getenv("GMAIL_SEND_TO"), subject=message, body=drafts_link)


def authorize_audio_upload(audio_file_path):
    url = "https://api.transistor.fm/v1/episodes/authorize_upload"
    headers = {'x-api-key': TRANSISTOR_API_KEY, 'accept': 'application/json'}
    params = {'filename': audio_file_path}
    response = requests.get(url, headers=headers, params=params, verify=False)
    response.raise_for_status()
    result = response.json()
    upload_url = result["data"]["attributes"]["upload_url"]
    audio_url = result["data"]["attributes"]["audio_url"]
    content_type = result["data"]["attributes"]["content_type"]
    logger.info(f"Presigned upload URL: {upload_url}")
    logger.info(f"Resulting audio URL (for episode): {audio_url}")
    logger.info(f"Content-Type: {content_type}")
    return upload_url, audio_url, content_type

def upload_audio_file(upload_url, audio_file_path, content_type):
    logger.info("Uploading audio file to presigned URL...")
    with open(audio_file_path, 'rb') as f:
        audio_data = f.read()
    headers = {"Content-Type": content_type}
    response = requests.put(upload_url, data=audio_data, headers=headers, verify=False)
    logger.info(f"Upload status: {response.status_code}")
    logger.info(f"Upload response: {response.text}")
    response.raise_for_status()
    logger.info("Audio uploaded successfully.")

def create_episode_with_audio(season, episode, title, description, audio_url, show_id):
    url = "https://api.transistor.fm/v1/episodes"
    headers = {'x-api-key': TRANSISTOR_API_KEY, 'accept': 'application/json'}
    data = {
        'episode[show_id]': show_id,
        'episode[title]': title,
        'episode[season]': season,
        'episode[number]': episode,
        'episode[type]': "full",
        'episode[explicit]': "false",
        'episode[description]': description,
        'episode[audio_url]': audio_url,
    }
    response = requests.post(url, headers=headers, data=data, verify=False)
    logger.info(f"Episode creation status: {response.status_code}")
    logger.info(f"Episode creation response: {response.text}")
    response.raise_for_status()
    episode = response.json()
    logger.info(f"Episode created with ID: {episode['data']['id']}")
    return episode

def upload_episode_to_transistor(configuration: Configuration):
    season = configuration.season_number
    episode = configuration.episode_number
    title = configuration.episode_title
    description = configuration.episode_description
    episode_audio_file_path = p(configuration.episode_folder) / configuration.episode_audio_filename
    upload_url, audio_url, content_type = authorize_audio_upload(episode_audio_file_path)
    upload_audio_file(upload_url, episode_audio_file_path, content_type)
    create_episode_with_audio(season, episode, title, description, audio_url, configuration.transistor_show_id)

if __name__ == "__main__":
    configuration = Configuration("russian")
    AUDIO_FILE_PATH = r"c:\Users\meir\Dropbox\tech_podcast_russian\Episode_45\Episode_45.mp3"
    audio_file_path = os.path.basename(AUDIO_FILE_PATH)
    episiode_number = "45"
    season = "1"
    episode_title = read_file_content(os.path.join(r"c:\Users\meir\Dropbox\tech_podcast_russian\Episode_45", "episode_name.txt"))
    episode_desc = read_file_content(os.path.join(r"c:\Users\meir\Dropbox\tech_podcast_russian\Episode_45", "episode_desc.txt"))
    configuration.set_episode_details("45", episode_title, episode_desc)
    configuration.episode_audio_filename = audio_file_path
    upload_episode_to_transistor(configuration)

