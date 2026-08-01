import dotenv
import os
from pathlib import Path as p

from podcast_creator.logger import logger

dotenv.load_dotenv()

SINGLE_URL_LINKS_FILEPATH = os.environ.get("SINGLE_LINKS_FILEPATH")
MULTI_URL_LINKS_FILEPATH = os.environ.get("MULTI_LINKS_FILEPATH")
PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_TITLE_FILENAME = "prompt_for_single_url_podcast_episode_name.j2"
PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_TITLE_FILENAME = "prompt_for_multi_urls_podcast_episode_name.j2"
PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_DESC_FILENAME = "prompt_for_single_url_podcast_episode_desc.j2"
PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_DESC_FILENAME = "prompt_for_multi_urls_podcast_episode_desc.j2"
PROMPT_FOR_PODCAST_GENERATION = "prompt_for_podcast_generation.j2"

def language_setting(language: str, key: str, default: str = None) -> str:
    """Read a per-language setting from the environment, e.g. HEBREW_PODCAST_NAME.

    Show details, names and hosts live in .env rather than in this file so the project can be
    pointed at a different podcast without editing code. See .env.example for the full list.
    """
    variable = f"{language.upper()}_{key}"
    value = os.environ.get(variable, default)
    if value is None:
        logger.error(f"Missing environment variable {variable}. Every language you run needs "
                     f"its own settings - copy .env.example and fill in the {language.upper()}_* block.")
        exit(1)
    return value


EPISODE_TITLE_FILENAME = "episode_name.txt"
EPISODE_DESC_FILENAME = "episode_desc.txt"
EPISODE_URLS_FILENAME = "urls.txt"
EPISODE_TEXT = "podcast_text.txt"

class Configuration:
    output_language: str
    podcast_root_folder: str
    season_number: int
    episode_number: int
    episode_folder: p
    episode_title: str
    episode_description: str
    template_for_episode_title: str
    template_for_episode_description: str
    template_for_podcast_generation: str
    links_filename: str
    batch_size: int
    episode_urls: []
    episode_titles: []
    transistor_show_id: str
    transistor_show_identifier: str
    episode_audio_filename: str
    man_speaker_name: str
    woman_speaker_name: str
    podcast_name: str
    text_direction: str
    episode_contents: str
    hosts: list[str]
    episode_length: int

    def __init__(self, language: str):
        self.output_language = language
        self.podcast_root_folder = f'{os.environ.get("OUTPUT_FOLDER_PREFIX", "")}_{language}'
        self.season_number = 1
        self.hosts = ['male', 'female']
        self.episode_length = -1
        self.transistor_show_id = language_setting(language, "TRANSISTOR_SHOW_ID")
        self.transistor_show_identifier = language_setting(language, "TRANSISTOR_SHOW_IDENTIFIER")
        self.podcast_name = language_setting(language, "PODCAST_NAME")
        self.man_speaker_name = language_setting(language, "MAN_SPEAKER_NAME")
        self.woman_speaker_name = language_setting(language, "WOMAN_SPEAKER_NAME")
        self.text_direction = language_setting(language, "TEXT_DIRECTION", "left-to-right")

    def set_prompts(self, is_single_url: bool):
        # These hold Jinja template names, not template bodies - rendering happens in
        # podcast_creator.templates, which loads them from disk by name.
        self.template_for_podcast_generation = PROMPT_FOR_PODCAST_GENERATION
        if is_single_url:
            self.template_for_episode_title = PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_TITLE_FILENAME
            self.template_for_episode_description = PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_DESC_FILENAME
            self.links_filename = SINGLE_URL_LINKS_FILEPATH
            self.batch_size = 1
        else:
            self.template_for_episode_title = PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_TITLE_FILENAME
            self.template_for_episode_description = PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_DESC_FILENAME
            self.links_filename = MULTI_URL_LINKS_FILEPATH
            self.batch_size = 10
            self.episode_length = 20

    def set_episode_details(self, episode_number: int, episode_title: str, episode_description: str):
        self.episode_number = episode_number
        self.episode_folder = p(self.podcast_root_folder) / f"Episode_{episode_number}"
        self.episode_title = episode_title
        self.episode_description = episode_description
        self.episode_audio_filename = f"Episode_{episode_number}.mp3"

    def set_episode_urls(self, urls: []):
        self.episode_urls = urls

    def set_episode_titles(self, titles: []):
        self.episode_titles = titles

    def set_episode_contents(self, contents: []):
        self.episode_contents = contents

    def set_episode_length(self, length: int):
        self.episode_length = length
