import os

import dotenv
from google import genai
from google.genai import types

from config import Configuration
from url_to_md import create_markdown_from_url

dotenv.load_dotenv()

def generate_podcast_text(configuration: Configuration):
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
    for url in configuration.episode_urls:
        md_text = create_markdown_from_url(url)
        if not md_text:
            continue
        parts.append(types.Part.from_text(text=md_text))

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

    ret = ""
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        ret += chunk.text

    return ret

