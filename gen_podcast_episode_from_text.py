import mimetypes
import os
import re
import struct
import time
from google import genai
from google.genai import types
import dotenv
import wave
from google.genai.errors import ServerError
from logger import logger

dotenv.load_dotenv()

# Define available API keys and tracking variables
GEMINI_API_KEYS = [
    os.environ.get("GEMINI_API_KEY_IAC"),
    os.environ.get("GEMINI_API_KEY_WORK"),
    os.environ.get("GEMINI_API_KEY_VAZAZON"),
    os.environ.get("GEMINI_API_KEY_PERSONAL"),
]
# Remove None values in case any environment variables aren't set
GEMINI_API_KEYS = [key for key in GEMINI_API_KEYS if key]
current_key_index = 0

def get_next_api_key():
    """Rotate to the next available API key."""
    global current_key_index
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    return GEMINI_API_KEYS[current_key_index]

def get_current_api_key():
    """Get the current API key."""
    return GEMINI_API_KEYS[current_key_index]

def save_binary_file(file_name, data):
    f = open(file_name, "wb")
    f.write(data)
    f.close()
    logger.info(f"File saved to: {file_name}")

def merge_wav_files(input_files, output_file):
    """Merge multiple WAV files into a single WAV file.
    
    Args:
        input_files: List of input WAV file paths
        output_file: Path to the output WAV file
    """
    # Get parameters from first file
    with wave.open(input_files[0], 'rb') as first_file:
        params = first_file.getparams()
    
    # Create output file
    with wave.open( str(output_file), 'wb') as output:
        output.setparams(params)
        
        # Write data from each input file
        for input_file in input_files:
            with wave.open(input_file, 'rb') as w:
                output.writeframes(w.readframes(w.getnframes()))

def generate_with_retry(model, contents, config, max_retries=3, initial_delay=1):
    """Generate content with retry mechanism.
    
    Args:
        model: The model name
        contents: The content to generate
        config: The generation config
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        
    Returns:
        The generated content or None if all retries failed
    """
    global current_key_index
    delay = initial_delay

    for attempt in range(max_retries):
        logger.info(f"Attempt {attempt + 1}/{max_retries} for generating content...")
        try:
            chunk_count = 0

            # Refresh client with the current API key
            client = genai.Client(
                api_key=get_current_api_key(),
            )

            # Ensure the stream is actually being iterated
            stream_iterator = client.models.generate_content_stream(
                model=model,
                contents=contents,
                config=config,
            )
            for chunk in stream_iterator:
                chunk_count += 1
                # logger.debug(f"  Received chunk {chunk_count}...") # Uncomment for very verbose logging
                yield chunk
            logger.info(f"  Successfully received {chunk_count} chunks.")
            return  # Success, exit the function
            
        except ServerError as se:
            logger.error(f"  Attempt {attempt + 1} failed with ServerError: {se}")
            if attempt < max_retries - 1:
                logger.info(f"  Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2
            else:
                logger.error(f"  All {max_retries} attempts failed due to ServerError.")
                raise  # Re-raise the last error

        except Exception as e:
            error_message = str(e).lower()
            logger.error(f"  Attempt {attempt + 1} failed with unexpected error: {e}")

            # Check if this is a rate limit error (HTTP 429)
            if "429" in error_message or "rate limit" in error_message or "quota" in error_message:
                logger.info(f"  Rate limit exceeded. Rotating to next API key...")
                # Get the next API key
                next_key = get_next_api_key()
                logger.info(f"  Switched to a different API key {next_key[1:10]}. Retrying...")
            elif attempt < max_retries - 1:
                logger.info(f"  Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2

            if attempt == max_retries:
                logger.error(f"  All {max_retries} attempts failed due to unexpected error.")
                raise  # Re-raise the last error

def split_text_into_chunks(text, max_chars_per_chunk=4000):
    SEPARATOR = "\n"
    """Splits text into chunks, trying to respect sentence boundaries."""
    chunks = []
    current_chunk = ""
    sentences = text.split(SEPARATOR) # Simple split, can be improved with smarter tokenization

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 <= max_chars_per_chunk: # +1 for SEPARATOR
            current_chunk += sentence + SEPARATOR
        else:
            if current_chunk: # Add previous chunk if not empty
                chunks.append(current_chunk.strip())
            current_chunk = sentence + SEPARATOR
    if current_chunk: # Add the last chunk
        chunks.append(current_chunk.strip())
    return chunks

def generate_podcast_episode_audio_from_text(podcast_text, episode_file_path, speaker_names):
    logger.info("Generating podcast episode audio from text...")

    model = "gemini-2.5-flash-preview-tts"
    #model = "gemini-2.5-pro-preview-tts"
    # List to store generated WAV files
    generated_files = []

    podcast_text = re.sub(r'\n+', '\n', podcast_text).strip()  # Remove extra newlines
    chunks = split_text_into_chunks(podcast_text)
    for i,chunk_text in enumerate(chunks):
        logger.info(chunk_text)
        chunk_text = ("Read the following script as natural-sounding speech. "
                      "If there are cues in parentheses (like (laughing), (whispering), (angry)),"
                      " use them to guide HOW you say the lines. "
                      "Only speak the main lines, but let the cues influence your delivery."
                      "DO NOT say the words in parentheses out loud.\n\nScript:\n") + chunk_text.strip()
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=chunk_text),
                ],
            ),
        ]
        
        generate_content_config = types.GenerateContentConfig(
            temperature=0.5,
            response_modalities=[
                "audio",
            ],
            speech_config=types.SpeechConfig(
                multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                    speaker_voice_configs=[
                        types.SpeakerVoiceConfig(
                            speaker=speaker_names[0],
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    #voice_name="Charon",
                                    voice_name="Puck",
                               ),
                            ),
                        ),
                        types.SpeakerVoiceConfig(
                            speaker=speaker_names[1],
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    #voice_name="Zephyr",
                                    voice_name="Kore",
                                ),
                            ),
                        ),
                    ]
                ),
            ),
        )

        try:
            for chunk in generate_with_retry(
                model=model,
                contents=contents,
                config=generate_content_config,
                max_retries=len(GEMINI_API_KEYS),
                initial_delay=10
            ):
                if (
                    chunk.candidates is None
                    or chunk.candidates[0].content is None
                    or chunk.candidates[0].content.parts is None
                ):
                    continue
                if chunk.candidates[0].content.parts[0].inline_data:
                    file_name = f"output_{i}"  # Use chunk index for file naming
                    inline_data = chunk.candidates[0].content.parts[0].inline_data
                    data_buffer = inline_data.data
                    file_extension = mimetypes.guess_extension(inline_data.mime_type)
                    if file_extension is None:
                        file_extension = ".wav"
                        data_buffer = convert_to_wav(inline_data.data, inline_data.mime_type)
                    output_file = f"{file_name}{file_extension}"
                    save_binary_file(output_file, data_buffer)
                    generated_files.append(output_file)
                else:
                    logger.info(chunk.text)
        except Exception as e:
            logger.error(f"Failed to generate audio for chunk {i}: {e}")
            raise e

    # Merge all generated WAV files
    if generated_files:
        merge_wav_files(generated_files, episode_file_path)
        logger.info("Merged all files into final_output.wav")

        # Clean up individual files
        for file in generated_files:
            try:
                os.remove(file)
                logger.info(f"Removed temporary file: {file}")
            except Exception as e:
                logger.error(f"Error removing file {file}: {e}")

def convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    parameters = parse_audio_mime_type(mime_type)
    bits_per_sample = parameters["bits_per_sample"]
    sample_rate = parameters["rate"]
    num_channels = 1
    data_size = len(audio_data)
    bytes_per_sample = bits_per_sample // 8
    block_align = num_channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    chunk_size = 36 + data_size  # 36 bytes for header fields before data chunk size

    # http://soundfile.sapp.org/doc/WaveFormat/

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",          # ChunkID
        chunk_size,       # ChunkSize (total file size - 8 bytes)
        b"WAVE",          # Format
        b"fmt ",          # Subchunk1ID
        16,               # Subchunk1Size (16 for PCM)
        1,                # AudioFormat (1 for PCM)
        num_channels,     # NumChannels
        sample_rate,      # SampleRate
        byte_rate,        # ByteRate
        block_align,      # BlockAlign
        bits_per_sample,  # BitsPerSample
        b"data",          # Subchunk2ID
        data_size         # Subchunk2Size (size of audio data)
    )
    return header + audio_data

def parse_audio_mime_type(mime_type: str) -> dict[str, int | None]:
    """Parses bits per sample and rate from an audio MIME type string.

    Assumes bits per sample is encoded like "L16" and rate as "rate=xxxxx".

    Args:
        mime_type: The audio MIME type string (e.g., "audio/L16;rate=24000").

    Returns:
        A dictionary with "bits_per_sample" and "rate" keys. Values will be
        integers if found, otherwise None.
    """
    bits_per_sample = 16
    rate = 24000

    # Extract rate from parameters
    parts = mime_type.split(";")
    for param in parts: # Skip the main type part
        param = param.strip()
        if param.lower().startswith("rate="):
            try:
                rate_str = param.split("=", 1)[1]
                rate = int(rate_str)
            except (ValueError, IndexError):
                # Handle cases like "rate=" with no value or non-integer value
                pass # Keep rate as default
        elif param.startswith("audio/L"):
            try:
                bits_per_sample = int(param.split("L", 1)[1])
            except (ValueError, IndexError):
                pass # Keep bits_per_sample as default if conversion fails

    return {"bits_per_sample": bits_per_sample, "rate": rate}

def main():
    with open(r"c:\temp\nopunct.txt", "r", encoding='utf-8') as f:
    # with open(r"c:\src\debug\1.txt ", "r", encoding="utf-8") as f:
        podcast_text = f.read()
    episode_file_path = r"c:\temp\temp.mp3"
    speaker_names = ["יוּבָל","עָמִית"]
    logger.info("Starting podcast episode generation...")
    generate_podcast_episode_audio_from_text(podcast_text, episode_file_path, speaker_names)
    logger.info("Podcast episode generation completed.")

if __name__ == "__main__":
    main()

