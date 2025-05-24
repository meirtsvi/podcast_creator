import os
from google import genai
from google.genai import types
import dotenv

dotenv.load_dotenv()

def generate_podcast_text(prompt_prefix, prompt_filename, episode_number):
    with open(prompt_filename, "r", encoding="utf-8") as f:
        prompt = f.read()

    input = f"{prompt_prefix}\n\nזהו פרק מספר {episode_number}\n\n{prompt}"
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    model = "gemini-2.5-pro-preview-05-06"
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

