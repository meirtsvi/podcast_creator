import os
import re
import csv
import concurrent.futures

import dotenv
from google import genai
from google.genai import types

from podcast_creator.logger import logger
from podcast_creator.config import Configuration

dotenv.load_dotenv()


def apply_translations(podcast_text, configuration):
    # --- Apply translations from translations.csv ---
    translations_path = os.path.join(os.path.dirname(__file__), 'translations.csv')
    if os.path.exists(translations_path):
        translations_3col = []
        with open(translations_path, encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if len(row) == 3:
                    src, tgt, gender = row
                    if gender.strip() == "":
                        src = src.lstrip('\ufeff')
                        pattern = r'(?<!\w)' + re.escape(src) + r'(?!\w)'
                        podcast_text = re.sub(pattern, tgt, podcast_text)
                    else:
                        src, tgt, gender = row
                        translations_3col.append((src.lstrip('\ufeff'), tgt, gender.strip().lower()))
        # Now process 3-column translations line by line
        lines = podcast_text.splitlines()
        for i, line in enumerate(lines):
            for src, tgt, gender in translations_3col:
                if gender == "male" and line.startswith(configuration.man_speaker_name + ":"):
                    pattern = r'(?<!\w)' + re.escape(src) + r'(?!\w)'
                    lines[i] = re.sub(pattern, tgt, line)
                elif gender == "female" and line.startswith(configuration.woman_speaker_name + ":"):
                    pattern = r'(?<!\w)' + re.escape(src) + r'(?!\w)'
                    lines[i] = re.sub(pattern, tgt, line)
        podcast_text = "\n".join(lines)
        return podcast_text

def cleanup_text(podcast_text: str, configuration: Configuration):
    logger.info(f"Cleaning up text. podcast_text: {podcast_text}")
    if podcast_text.startswith("("):
        podcast_text = podcast_text.split("\n", 1)[1].strip()
    podcast_text = podcast_text.strip().replace("**", "")
    podcast_text = podcast_text.replace("עדכוני טכנולוגיה", configuration.podcast_name)
    podcast_text = podcast_text.replace("יוּבָב:", f"{configuration.man_speaker_name}:")
    podcast_text = re.sub("<[^>]+>", "", podcast_text)  # Remove HTML tags
    podcast_text = podcast_text.replace("(Outro music begins)", "")
    podcast_text = podcast_text.replace("(Podcast intro music fades in and then fades to a background hum)", "")
    podcast_text = podcast_text.replace("(Podcast outro music fades in)", "")
    podcast_text = podcast_text.replace("(Podcast intro music fades in and then fades to background)", "")
    podcast_text = re.sub("^[\n]+", "", podcast_text)  # Remove empty lines
    podcast_text = re.sub("\n\n", "\n", podcast_text)
    if not podcast_text.startswith(configuration.man_speaker_name) and \
       not podcast_text.startswith(configuration.woman_speaker_name):
        podcast_text = configuration.man_speaker_name + ": " + podcast_text
    podcast_text = re.sub(rf'(?<!^)(?<!\n)({configuration.man_speaker_name}:|{configuration.woman_speaker_name}:)', r'\n\1', podcast_text)
    logger.info(f"Cleaned up text. podcast_text: {podcast_text}")
    return podcast_text

def process_conditional_text(content, conditions):
    for condition_name, include in conditions.items():
        pattern = f'<!--CONDITIONAL:{condition_name}-->(.*?)<!--END:{condition_name}-->'

        if include:
            # Keep the content but remove the markers
            content = re.sub(pattern, r'\1', content, flags=re.DOTALL)
        else:
            # Remove the entire conditional block
            content = re.sub(pattern, '', content, flags=re.DOTALL)

    # Clean up any remaining empty lines
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
    return content

def generate_podcast_text(configuration: Configuration, num_of_retries: int = 3):
    if num_of_retries > 1:
        # Run generate_podcast_text_inner in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_of_retries) as executor:
            futures = [executor.submit(generate_podcast_text_inner, configuration) for _ in range(num_of_retries)]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]

        # Calculate target length based on configuration.episode_contents
        target_length = sum(len(content) for content in configuration.episode_contents)
        target_min = int(target_length * 0.65)
        target_max = int(target_length * 0.85)

        # Find the result that best matches 65-85% of target length
        best_result = None
        best_score = float('inf')

        for result in results:
            result_length = len(result)
            if target_min <= result_length <= target_max:
                # Calculate how close to the middle of the range (75%)
                target_ideal = int(target_length * 0.75)
                score = abs(result_length - target_ideal)
                if score < best_score:
                    best_score = score
                    best_result = result

        # If no result is in the target range, pick the one closest to 75%
        if best_result is None:
            target_ideal = int(target_length * 0.75)
            best_result = min(results, key=lambda r: abs(len(r) - target_ideal))

        podcast_text = best_result
    else:
        podcast_text = generate_podcast_text_inner(configuration)

    with open(configuration.episode_folder / "podcast_text_original.txt", "w", encoding="utf-8") as f:
        f.write(podcast_text)
    podcast_text = cleanup_text(podcast_text, configuration)
    podcast_text = apply_translations(podcast_text, configuration)
    with open(configuration.episode_folder / "podcast_text.txt", "w", encoding="utf-8") as f:
        f.write(podcast_text)

    logger.info(f"Created podcast text from {podcast_text[:100]}... (length: {len(podcast_text)})")
    return podcast_text

def generate_podcast_text_inner(configuration: Configuration):
    logger.info(f"Generating podcast text for episode {configuration.episode_number} with title '{configuration.episode_title}'")

    if len(configuration.hosts) > 1:
        conditions = { 'TWO_HOSTS': True, 'SINGLE_HOST': False }
    else:
        conditions = { 'TWO_HOSTS': False, 'SINGLE_HOST': True}
    prompt_for_podcast_generation = process_conditional_text(configuration.prompt_for_podcast_generation, conditions)
    prompt = prompt_for_podcast_generation + "\n"
    prompt = prompt.replace("{man_speaker}", configuration.man_speaker_name).replace("{woman_speaker}", configuration.woman_speaker_name)
    prompt = prompt.replace("{host1}", configuration.hosts[0]).replace("{host2}", configuration.hosts[1] if len(configuration.hosts) > 1 else configuration.hosts[0])
    if len(configuration.hosts) > 1:
        prompt = prompt.replace("{podcast_tone}", configuration.podcast_tone_two_hosts)
    else:
        prompt = prompt.replace("{podcast_tone}", configuration.podcast_tone_single_host)
    prompt = prompt.replace("{podcast_name}", configuration.podcast_name)
    if configuration.episode_number != -1:
        prompt += (f"Structure the episode as follows: Start by {configuration.man_speaker_name} announcing podcast name {configuration.podcast_name}, "
                   f"the episode number ({configuration.episode_number}), "
                   f"then remind the listener to follow the podcast on the podcast app so they can get new episodes,"
                   f"then do an introduction with the hosts’ names, and only then continue with a smooth and engaging broadcast."
                   f"Podcast name: {configuration.podcast_name}")
    else:
        prompt += "DON'T annouce and DON'T mention podcast name, host names, episode number."

    lang_output_prompt = "Create the episode in " + configuration.output_language + " language."
    if configuration.output_language == "hebrew":
        lang_output_prompt += "כתוב את הטקסט בכתיב מלא."

    prompt_suffix = "Use the following for the episode content:"
    input = f"{prompt}.\n{lang_output_prompt}.{prompt_suffix}"
    with open(configuration.episode_folder / "podcast_input.txt", "w", encoding="utf-8") as f:
        f.write(input)
    with open(configuration.episode_folder / "podcast_content.txt", "w", encoding="utf-8") as f:
        f.write('\n'.join(configuration.episode_contents))

    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    parts = [types.Part.from_text(text=input)]

    # Use content from configuration instead of calling create_markdown_from_url
    for content in configuration.episode_contents:
        if content:
            parts.append(types.Part.from_text(text=content))

    #model = "gemini-2.5-pro-preview-05-06"
    model = "gemini-2.5-flash"
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

    return podcast_text

def main():
    from pathlib import Path as p

    configuration = Configuration("hebrew")
    configuration.set_episode_details(episode_number=95, episode_title="מטהורס", episode_description="עדכונים על מטהורס")
    configuration.episode_folder = p("/tmp/ep/")
    configuration.hosts = ['female', 'male']
    configuration.podcast_name = "עִדְכּוּנֵי טֶכְנוֹלוֹגְיָה"
    with open("/tmp/ep/podcast_content.txt", "r", encoding="utf-8") as f:
        article_text = f.readlines()
        configuration.episode_contents = article_text
    configuration.set_prompts(is_single_url=True)
    podcast_text = generate_podcast_text(configuration, 1)
    print(f"Podcast text: {podcast_text}")

if __name__ == "__main__":
    main()
