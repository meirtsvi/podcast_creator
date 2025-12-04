import os
import re
import csv

import dotenv
from google import genai
from google.genai import types

from podcast_creator.logger import logger
from podcast_creator.config import Configuration

from podcast_creator.common import process_conditional_text

dotenv.load_dotenv()

WORDS_PER_MINUTE = int(os.getenv('WORDS_PER_MINUTE'))

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

def count_words(content: list) -> int:
    count = 0
    for line in content:
        count += len(line.split())
    return count

def generate_podcast_text(configuration: Configuration):
    logger.info(f"Generating podcast text for episode {configuration.episode_number} with title '{configuration.episode_title}'")

    if len(configuration.hosts) > 1:
        conditions = { 'TWO_HOSTS': True, 'SINGLE_HOST': False }
    else:
        conditions = { 'TWO_HOSTS': False, 'SINGLE_HOST': True}
    if configuration.episode_number != -1:
        conditions = { **conditions, 'EPISODE_IN_SERIES': True, 'NOT_EPISODE_IN_SERIES': False }
    else:
        conditions = { **conditions, 'EPISODE_IN_SERIES': False, 'NOT_EPISODE_IN_SERIES': True }
    prompt_for_podcast_generation = process_conditional_text(configuration.prompt_for_podcast_generation, conditions)
    prompt = prompt_for_podcast_generation + "\n"
    prompt = prompt.replace("{man_speaker}", configuration.man_speaker_name).replace("{woman_speaker}", configuration.woman_speaker_name)
    prompt = prompt.replace("{speaker}", configuration.man_speaker_name if configuration.hosts[0].lower() == "male" else configuration.woman_speaker_name)
    prompt = prompt.replace("{host1}", configuration.hosts[0]).replace("{host2}", configuration.hosts[1] if len(configuration.hosts) > 1 else configuration.hosts[0])
    if len(configuration.hosts) > 1:
        prompt = prompt.replace("{podcast_tone}", configuration.podcast_tone_two_hosts)
    else:
        prompt = prompt.replace("{podcast_tone}", configuration.podcast_tone_single_host)
    prompt = prompt.replace("{podcast_name}", configuration.podcast_name)
    prompt = prompt.replace("{episode_number}", str(configuration.episode_number))
    prompt = prompt.replace("[PASTE YOUR LONG TEXT HERE]", str(configuration.episode_contents))
    prompt = prompt.replace("{language}", configuration.output_language)

    logger.info(f"episode_length: {configuration.episode_length}")
    target_word_count = count_words(configuration.episode_contents)
    if configuration.episode_length != -1:
        target_word_count = int(int(configuration.episode_length) * int(WORDS_PER_MINUTE))
    else:
        if target_word_count > 4200:
            target_word_count = 4200

    logger.info(f"Target word count: {target_word_count}")
    min_n_words = int(target_word_count * 0.85)
    max_n_words = int(target_word_count * 1.15)

    # Calculate required tokens with generous buffer
    # Hebrew words might use more tokens
    estimated_tokens = int(max_n_words * 2.0)  if configuration.output_language=="hebrew" else int(max_n_words) # Very generous for Hebrew
    prompt = prompt.replace("{min_n_words}", str(min_n_words))
    prompt = prompt.replace("{max_n_words}", str(max_n_words))

    with open(configuration.episode_folder / "podcast_input.txt", "w", encoding="utf-8") as f:
        f.write(prompt)
    with open(configuration.episode_folder / "podcast_content.txt", "w", encoding="utf-8") as f:
        f.write('\n'.join(configuration.episode_contents))

    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    parts = [types.Part.from_text(text=prompt)]
    for content in configuration.episode_contents:
        if content:
            parts.append(types.Part.from_text(text=content))

    # Use Pro model for better instruction following
    model = "gemini-3-pro-preview"

    contents = [
        types.Content(
            role="user",
            parts=parts,
        ),
    ]

    generate_content_config = types.GenerateContentConfig(
        response_mime_type="text/plain",
        temperature=0.8,  # Higher temperature for more elaboration
        max_output_tokens=estimated_tokens,
    )

    logger.info(f"Starting generation with max_output_tokens={estimated_tokens}")
    logger.info(f"Target: {min_n_words}-{max_n_words} words")

    # Use the retry logic
    podcast_text = generate_podcast_text_with_retry(
        client=client,
        model=model,
        contents=contents,
        generate_content_config=generate_content_config,
        configuration=configuration,
        min_words=min_n_words,
        max_retries=10,  # Increased retries
    )

    # Final verification
    final_word_count = len(podcast_text.split())
    is_complete = verify_text_completeness(podcast_text)

    logger.info(f"\n{'=' * 60}")
    logger.info(f"FINAL RESULTS:")
    logger.info(f"Word count: {final_word_count} (target: {min_n_words}-{max_n_words})")
    logger.info(f"Percentage of target: {(final_word_count / min_n_words) * 100:.1f}%")
    logger.info(f"Text complete: {is_complete}")
    logger.info(f"{'=' * 60}\n")

    if not is_complete:
        logger.info("WARNING: Text may be incomplete!")

    if final_word_count < min_n_words * 0.85:
        logger.info(f"WARNING: Word count significantly below target!")

    logger.info(f"podcast text: {podcast_text[:100]}")

    with open(configuration.episode_folder / "podcast_text_original.txt", "w", encoding="utf-8") as f:
        f.write(podcast_text)
    podcast_text = cleanup_text(podcast_text, configuration)
    podcast_text = apply_translations(podcast_text, configuration)
    with open(configuration.episode_folder / "podcast_text.txt", "w", encoding="utf-8") as f:
        f.write(podcast_text)

    logger.info(f"Created podcast text from {podcast_text[:100]}... (length: {len(podcast_text)})")
    return podcast_text

def generate_podcast_text_with_retry(client, model, contents, generate_content_config, configuration: Configuration,
                                     min_words, max_retries=3):
    """Generate podcast text with truncation detection and retry logic."""

    for attempt in range(max_retries):
        num_chunks = 0
        podcast_text = ""
        finish_reason = None
        last_logged_word_count = 0

        try:
            for chunk in client.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=generate_content_config,
            ):
                num_chunks += 1
                if chunk and chunk.text:
                    podcast_text += chunk.text

                    # Log progress every 500 words
                    current_word_count = len(podcast_text.split())
                    if current_word_count - last_logged_word_count >= 500:
                        logger.info(f"Progress: {current_word_count} words generated...")
                        last_logged_word_count = current_word_count

                # Check if we have candidates with finish_reason
                if hasattr(chunk, 'candidates') and chunk.candidates:
                    candidate = chunk.candidates[0]
                    if hasattr(candidate, 'finish_reason'):
                        finish_reason = candidate.finish_reason

        except Exception as e:
            logger.error(f"Error during generation attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                continue
            else:
                raise

        # Fix pattern where host name and colon are on one line, and text is on the next line
        lines = podcast_text.splitlines()
        fixed_lines = []
        found_pattern = False
        i = 0
        while i < len(lines):
            current_line = lines[i].strip()
            
            # Check if current line is only "host_name:"
            if i < len(lines) - 1 and (
                current_line.strip() == f"{configuration.man_speaker_name}:" or 
                current_line.strip() == f"{configuration.woman_speaker_name}:"
            ):
                next_line = lines[i + 1].strip()
                # Check if next line doesn't start with "host_name: "
                if next_line and \
                   not next_line.startswith(f"{configuration.man_speaker_name}: ") and \
                   not next_line.startswith(f"{configuration.woman_speaker_name}: "):
                    # Combine the two lines
                    combined_line = f"{current_line} {next_line}"
                    fixed_lines.append(combined_line)
                    found_pattern = True
                    i += 2  # Skip the next line since we already processed it
                    continue
            
            fixed_lines.append(lines[i])
            i += 1
        
        # Only update podcast_text if we actually found and fixed the pattern
        if found_pattern:
            podcast_text = "\n".join(fixed_lines)
            logger.info(f"Fixed podcast_text by combining host name lines with following text lines")

        found_illegal_line = False
        for line in podcast_text.splitlines():
            if line.strip() == "":
                continue
            if not (line.startswith(configuration.man_speaker_name)
                or line.startswith(configuration.woman_speaker_name)):
                logger.info(f"Found line that doesn't start with host name: {line}")
                found_illegal_line = True
                break
        if found_illegal_line:
            logger.info(f"The generated text is not formatted with host name that begins each line. Trying again...")
            continue

        # Check if generation completed successfully
        word_count = len(podcast_text.split())
        is_complete = verify_text_completeness(podcast_text)

        logger.info(f"Attempt {attempt + 1}: Generated {word_count} words, "
                    f"finish_reason={finish_reason}, complete={is_complete}")

        # Check for truncation
        if finish_reason == "MAX_TOKENS" or str(finish_reason) == "FinishReason.MAX_TOKENS":
            logger.warning(f"Output was truncated (MAX_TOKENS reached). Retrying with higher limit...")
            generate_content_config.max_output_tokens = int(generate_content_config.max_output_tokens * 1.5)
            continue

        # Check if text ends properly
        if not is_complete:
            logger.warning(f"Text appears incomplete (doesn't end with proper punctuation). Retrying...")
            continue

        # Check if we're reasonably close to target word count
        if word_count < min_words * 0.85:  # Relaxed to 85%
            logger.warning(f"Word count too low ({word_count} < {min_words * 0.85}). Retrying...")
            continue

        # Success!
        logger.info(f"Successfully generated {word_count} words")
        return podcast_text

    # If we exhausted retries, return best attempt
    logger.warning(f"Exhausted {max_retries} retries. Returning last attempt with {word_count} words")
    return podcast_text


def verify_text_completeness(text):
    """Verify that text ends with a complete sentence."""
    if not text:
        return False

    text = text.strip()

    # Check if ends with sentence-ending punctuation
    if text[-1] in '.!?':
        return True

    # Hebrew/RTL punctuation
    if text[-1] in '"\'"':
        if len(text) > 1 and text[-2] in '.!?':
            return True

    # Check for common closing markers
    closing_patterns = ['!', '?', '.', '...']
    for pattern in closing_patterns:
        if text.endswith(pattern):
            return True

    return False

def main():
    from pathlib import Path as p

    for ep_num in range(200, 248):
        try:
            ep_folder = p(f"/tmp/ep/{ep_num}/")
            ep_folder.mkdir(parents=True, exist_ok=True)
            configuration = Configuration("hebrew")
            configuration.set_episode_details(episode_number=ep_num, episode_title=f"מטהורס {ep_num}", episode_description="עדכונים על מטהורס")
            configuration.episode_folder = ep_folder
            configuration.hosts = ['male', 'female']
            configuration.podcast_name = "עִדְכּוּנֵי טֶכְנוֹלוֹגְיָה"
            with open(f"/Users/meirt/Dropbox/tech_podcast_hebrew/Episode_{ep_num}/podcast_content.txt", "r", encoding="utf-8") as f:
                article_text = f.readlines()
                configuration.episode_contents = article_text
            configuration.set_prompts(is_single_url=True)
            podcast_text = generate_podcast_text(configuration)
            print(f"Podcast text for episode {ep_num}: {podcast_text[:100]}... (length: {len(podcast_text)})")
        except Exception as e:
            print(f"Error generating podcast text for episode {ep_num}: {e}")

if __name__ == "__main__":
    main()
