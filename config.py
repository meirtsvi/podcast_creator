import dotenv
import os
from pathlib import Path as p

from logger import logger
from utils import read_file_content

SINGLE_URL_LINKS_FILENAME = "sources/website_single_links.csv"
MULTI_URL_LINKS_FILENAME = "sources/website_multi_links.csv"
PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_TITLE_FILENAME = "prompt_for_single_url_podcast_episode_name.txt"
PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_TITLE_FILENAME = "prompt_for_multi_urls_podcast_episode_name.txt"
PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_DESC_FILENAME = "prompt_for_single_url_podcast_episode_desc.txt"
PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_DESC_FILENAME = "prompt_for_multi_urls_podcast_episode_desc.txt"
PROMPT_FOR_SINGLE_URL_PODCAST_GENERATION = "prompt_for_single_url_podcast_generation.txt"
PROMPT_FOR_MULTI_URLS_PODCAST_GENERATION = "prompt_for_multi_urls_podcast_generation.txt"

EPISODE_TITLE_FILENAME = "episode_name.txt"
EPISODE_DESC_FILENAME = "episode_desc.txt"
EPISODE_URLS_FILENAME = "urls.txt"
EPISODE_TEXT = "podcast_text.txt"

dotenv.load_dotenv()

class Configuration:
    output_language: str
    podcast_root_folder: str
    season_number: int
    episode_number: int
    episode_folder: str
    episode_title: str
    episode_description: str
    prompt_for_episode_title_generation: str
    prompt_for_episode_description_generation: str
    prompt_for_podcast_generation: str
    links_filename: str
    batch_size: int
    episode_urls: []
    episode_titles: []
    transistor_show_id: str
    episode_audio_filename: str
    man_speaker_name: str
    woman_speaker_name: str
    podcast_tone: str
    podcast_name: str
    text_direction: str

    def __init__(self, language: str):
        self.output_language = language
        self.podcast_root_folder = f'{os.environ.get("OUTPUT_FOLDER_PREFIX", "")}_{language}'
        self.season_number = 1
        if language == "hebrew":
            self.transistor_show_id = "64672"
            self.man_speaker_name = "יוּבָל"
            self.woman_speaker_name = "עָמִית"
            self.podcast_name = "עִדְכּוּנֵי טֶכְנוֹלוֹגְיָה"
            self.text_direction = "right-to-left"
            self.podcast_tone = """
               ייצר קצב דיבור כמו בפודקאסט אמיתי.
               Introduce disfluencies to make it sound like a real conversation.
               Make speakers react to what the other person is saying using phrases like, "Oh?" and "yeah?".
               Include natural speech elements (filler words, feedback responses).
               NaturalTraits: Sometimes use filler words such as um, uh, you know and some stuttering. NOT TOO MUCH (up to 5 times), just enough to make it sound like a real conversation.
               """
        elif language == "english":
            self.transistor_show_id = "64687"
            self.man_speaker_name = "Yuval"
            self.woman_speaker_name = "Amit"
            self.podcast_name = "Tech Updates"
            self.text_direction = "left-to-right"
            self.podcast_tone = """
                הוסף הפסקות טבעיות (אמממ, הא, מממ) וקצב דיבור כמו בפודקאסט אמיתי
               Introduce disfluencies to make it sound like a real conversation.
               Make speakers react to what the other person is saying using phrases like, "Oh?" and "yeah?".
               Include natural speech elements (filler words, feedback responses).
               NaturalTraits: Sometimes use filler words such as um, uh, you know and some stuttering. NOT TOO MUCH (up to 5 times), just enough to make it sound like a real conversation.
               """
        elif language == "russian":
            self.transistor_show_id = "64812"
            self.man_speaker_name = "Sasha"
            self.woman_speaker_name = "Zhenia"
            self.podcast_name = "Tech Updates"
            self.text_direction = "left-to-right"
            self.podcast_tone = "Никогда не используй слово 'ну'!"
        else:
            logger.error(f"Cannot set transistor show id for language: {language}")
            exit(1)

    def set_prompts(self, is_single_url: bool):
        if is_single_url:
            self.prompt_for_episode_title_generation = read_file_content(PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_TITLE_FILENAME)
            self.prompt_for_episode_description_generation = read_file_content(PROMPT_FOR_SINGLE_URL_PODCAST_EPISODE_DESC_FILENAME)
            self.prompt_for_podcast_generation = read_file_content(PROMPT_FOR_SINGLE_URL_PODCAST_GENERATION)
            self.links_filename = SINGLE_URL_LINKS_FILENAME
            self.batch_size = 1
        else:
            self.prompt_for_episode_title_generation = read_file_content(PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_TITLE_FILENAME)
            self.prompt_for_episode_description_generation = read_file_content(PROMPT_FOR_MULTI_URLS_PODCAST_EPISODE_DESC_FILENAME)
            self.prompt_for_podcast_generation = read_file_content(PROMPT_FOR_MULTI_URLS_PODCAST_GENERATION)
            self.links_filename = MULTI_URL_LINKS_FILENAME
            self.batch_size = 10

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
