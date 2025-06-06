import os
import re

import dotenv
from google import genai
from google.genai import types
from logger import logger

from config import Configuration

dotenv.load_dotenv()

def generate_podcast_text(configuration: Configuration):
    logger.info(f"Generating podcast text for episode {configuration.episode_number} with title '{configuration.episode_title}'")
    prompt_suffix = "Use the following for the episode content:"
    prompt = configuration.prompt_for_podcast_generation
    prompt += "Podcast name: " + configuration.podcast_name + ".\n"
    prompt = prompt.replace("{man_speaker}", configuration.man_speaker_name).replace("{woman_speaker}", configuration.woman_speaker_name)
    prompt = prompt.replace("{podcast_tone}", configuration.podcast_tone)
    prompt = prompt.replace("{podcast_name}", configuration.podcast_name)
    lang_output_prompt = "Create the episode in " + configuration.output_language + " language."
    if configuration.output_language == "hebrew":
        lang_output_prompt += "כתוב את הטקסט בכתיב מלא."
    input = f"This is episode {configuration.episode_number}.\n\n{prompt}.\n\n{lang_output_prompt}.{prompt_suffix}"
    with open(configuration.episode_folder / "podcast_input.txt", "w", encoding="utf-8") as f:
        f.write(input)

    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    parts = [types.Part.from_text(text=input)]

    # Use content from configuration instead of calling create_markdown_from_url
    for content in configuration.episode_contents:
        if content:
            parts.append(types.Part.from_text(text=content))

    #model = "gemini-2.5-pro-preview-05-06"
    model = "gemini-2.5-flash-preview-05-20"
    contents = [
        types.Content(
            role="user",
            parts=parts,
        ),
    ]
    generate_content_config = types.GenerateContentConfig(
        response_mime_type="text/plain",
    )

    num_chunks = 0
    podcast_text = ""
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        num_chunks += 1
        if chunk and chunk.text:
            podcast_text += chunk.text
        else:
            logger.warning("Received empty chunk from the model, skipping.")

    if podcast_text.startswith("("):
        podcast_text = podcast_text.split("\n", 1)[1].strip()
    podcast_text = podcast_text.strip().replace("**", "")
    podcast_text = podcast_text.replace(" איתי " , " אִתִּי ")
    podcast_text = podcast_text.replace(" כל "," כּוֹל ")
    podcast_text = podcast_text.replace('ארה"ב',  'ארצות הברית')
    podcast_text = podcast_text.replace("עדכוני טכנולוגיה", configuration.podcast_name)
    podcast_text = re.sub("<[^>]+>", "", podcast_text)  # Remove HTML tags
    if not podcast_text.startswith(configuration.man_speaker_name) and \
       not podcast_text.startswith(configuration.woman_speaker_name):
        podcast_text = configuration.man_speaker_name + ": " + podcast_text

    with open(configuration.episode_folder / "podcast_text.txt", "w", encoding="utf-8") as f:
        f.write(podcast_text)

    logger.info(f"Created podcast text from {num_chunks} chunks: {podcast_text[:100]}... (length: {len(podcast_text)})")
    return podcast_text

def main():
    with open(r"c:\Users\meir\Dropbox\tech_podcast_hebrew\Episode_75\podcast_text.txt", "r", encoding="utf-8") as f:
        podcast_text = f.read()
        if podcast_text.startswith("("):
            podcast_text = podcast_text.split("\n", 1)[1].strip()
        print(podcast_text[:100])

if __name__ == "__main__":
    main()
