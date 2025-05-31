import os

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
    prompt = prompt.replace("##man_speaker##", configuration.man_speaker_name).replace("##woman_speaker##", configuration.woman_speaker_name)
    prompt = prompt.replace("##podcast_tone##", configuration.podcast_tone)
    lang_output_prompt = "Create the episode in " + configuration.output_language + " language."
    input = f"This is episode {configuration.episode_number}.\n\n{prompt}.\n\n{lang_output_prompt}.{prompt_suffix}"
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
    ret = ""
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        num_chunks += 1
        if chunk and chunk.text:
            ret += chunk.text
        else:
            logger.warning("Received empty chunk from the model, skipping.")

    logger.info(f"Created podcast text from {num_chunks}: {ret[:100]}... (length: {len(ret)})")
    return ret

