import os

import dotenv
from google import genai
from google.genai import types

from config import Configuration

dotenv.load_dotenv()

def generate_podcast_text(configuration: Configuration):
    if configuration.batch_size > 1:
        prompt_prefix = f"Read all articles in these links: {configuration.episode_urls}"
    else:
        prompt_prefix = f"This episode focuses on the subject detailed in this link:: {configuration.episode_urls[0]}. Read the article in this link."

    prompt = configuration.prompt_for_podcast_generation
    prompt += "Podcast name: " + configuration.podcast_name + ".\n"
    prompt = prompt.replace("##man_speaker##", configuration.man_speaker_name).replace("##woman_speaker##", configuration.woman_speaker_name)
    prompt = prompt.replace("##podcast_tone##", configuration.podcast_tone)
    lang_output_prompt = "Create the episode in " + configuration.output_language + " language."
    input = f"{prompt_prefix}\n\nThis is episode {configuration.episode_number}.\n\n{prompt}.\n\n{lang_output_prompt}."
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    #model = "gemini-2.5-pro-preview-05-06"
    model = "gemini-2.5-flash-preview-05-20"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=input),
            ],
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

