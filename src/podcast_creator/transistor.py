import os
import requests
import dotenv
from datetime import datetime, timedelta, timezone
from pathlib import Path as p

from  podcast_creator.sendmail import send_email
from podcast_creator.logger import logger
from podcast_creator.config import Configuration
from podcast_creator.utils import read_file_content

dotenv.load_dotenv()

TRANSISTOR_API_KEY = os.getenv("TRANSISTOR_API_KEY")

# How long an episode sits in "scheduled" before it goes live - the window in which you can
# still open the dashboard and delete it.
PUBLISH_DELAY_MINUTES = 10
# Transistor renders and accepts timestamps as "2020-07-01 00:00:00 UTC".
TRANSISTOR_TIME_FORMAT = "%Y-%m-%d %H:%M:%S UTC"

def upload_new_podcast_episode(configuration: Configuration):
    if configuration.transistor_show_id == "0":
        logger.warning("Transistor show ID is not set. Skipping upload.")
        return

    episode_folder = configuration.episode_folder
    logger.info(f"Uploading new podcast episode from {str(episode_folder)}...")
    episode_id, scheduled_for = upload_episode_to_transistor(configuration)
    message = (f"New podcast {configuration.output_language} episode #{configuration.episode_number} "
               f"scheduled on Transistor.fm")
    logger.info(message)
    episode_link = (f"https://dashboard.transistor.fm/shows/"
                    f"{configuration.transistor_show_identifier}/episodes/{episode_id}")
    body = (f"Publishing at {scheduled_for} (about {PUBLISH_DELAY_MINUTES} minutes from now).\n\n"
            f"Delete it before then if you don't want it to go out:\n{episode_link}")
    send_email(send_to=os.getenv("MAIL_SEND_TO"), subject=message, body=body)


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

def schedule_episode_publish(episode_id, delay_minutes=PUBLISH_DELAY_MINUTES):
    """Move an episode straight from draft to scheduled.

    Transistor always creates episodes as drafts - POST /v1/episodes takes no status - so the
    closest thing to publishing without a draft step is to schedule it the moment it exists.
    """
    publish_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
    url = f"https://api.transistor.fm/v1/episodes/{episode_id}/publish"
    headers = {'x-api-key': TRANSISTOR_API_KEY, 'accept': 'application/json'}
    data = {
        'episode[status]': "scheduled",
        'episode[published_at]': publish_at.strftime(TRANSISTOR_TIME_FORMAT),
    }
    response = requests.patch(url, headers=headers, data=data, verify=False)
    logger.info(f"Episode scheduling status: {response.status_code}")
    logger.info(f"Episode scheduling response: {response.text}")
    response.raise_for_status()
    # Read the time back instead of trusting the one we sent: published_at is interpreted in the
    # show's time zone, so a time zone mismatch would surface here rather than silently.
    attributes = response.json()["data"]["attributes"]
    scheduled_for = attributes["published_at"]
    logger.info(f"Episode {episode_id} is now {attributes['status']}, publishing at {scheduled_for}")
    return scheduled_for

def upload_episode_to_transistor(configuration: Configuration):
    season = configuration.season_number
    episode = configuration.episode_number
    title = configuration.episode_title
    description = configuration.episode_description
    episode_audio_file_path = p(configuration.episode_folder) / configuration.episode_audio_filename
    upload_url, audio_url, content_type = authorize_audio_upload(episode_audio_file_path)
    upload_audio_file(upload_url, episode_audio_file_path, content_type)
    created_episode = create_episode_with_audio(season, episode, title, description, audio_url,
                                                configuration.transistor_show_id)
    episode_id = created_episode["data"]["id"]
    scheduled_for = schedule_episode_publish(episode_id)
    return episode_id, scheduled_for

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


