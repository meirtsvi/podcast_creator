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

dotenv.load_dotenv()

# Define available API keys and tracking variables
GEMINI_API_KEYS = [
    os.environ.get("GEMINI_API_KEY_WORK"),
    os.environ.get("GEMINI_API_KEY_VAZAZON"),
    os.environ.get("GEMINI_API_KEY_IAC"),
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
    print(f"File saved to: {file_name}")

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
    last_error = None
    
    for attempt in range(max_retries):
        print(f"Attempt {attempt + 1}/{max_retries} for generating content...")
        try:
            chunk_count = 0

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
                # print(f"  Received chunk {chunk_count}...") # Uncomment for very verbose logging
                yield chunk
            print(f"  Successfully received {chunk_count} chunks.")
            return  # Success, exit the function
            
        except ServerError as se:
            last_error = se
            error_message = str(se).lower()
            print(f"  Attempt {attempt + 1} failed with ServerError: {se}")

            if attempt < max_retries - 1:
                print(f"  Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2
            else:
                print(f"  All {max_retries} attempts failed due to ServerError.")
                raise  # Re-raise the last error

        except Exception as e:
            last_error = e
            error_message = str(e).lower()
            print(f"  Attempt {attempt + 1} failed with unexpected error: {e}")

            # Check if this is a rate limit error (HTTP 429)
            #   Attempt 1 failed with unexpected error: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_requests_per_model_per_day', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel'}]}, {'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}]}}
            if "429" in error_message or "rate limit" in error_message or "quota" in error_message:
                print(f"  Rate limit exceeded. Rotating to next API key...")
                # Get the next API key
                next_key = get_next_api_key()
                print(f"  Switched to a different API key {next_key[1:10]}. Retrying...")

            elif attempt < max_retries - 1:
                print(f"  Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2
            else:
                print(f"  All {max_retries} attempts failed due to unexpected error.")
                raise  # Re-raise the last error

def generate_podcast_episode_audio_from_text(podcast_text, episode_file_path, speaker_names):
    print("Generating podcast episode audio from text...")

    model = "gemini-2.5-flash-preview-tts"
    
    # Split text into lines and filter out empty lines
    lines = [line.strip() for line in podcast_text.split('\n') if line.strip()]
    
    # List to store generated WAV files
    generated_files = []

    NO_LINES_TO_PROCESS=3

    # Remove empty lines
    lines = [line for line in lines if line.strip()]

    # Process NO_LINES_TO_PROCESS lines at a time
    for i in range(0, len(lines), NO_LINES_TO_PROCESS):
        # Get next NO_LINES_TO_PROCESS lines (or remaining lines if less than NO_LINES_TO_PROCESS)
        chunk_lines = lines[i:i+NO_LINES_TO_PROCESS]
        # Join the lines with newlines
        chunk_text = '\n'.join(chunk_lines)
        
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=chunk_text),
                ],
            ),
        ]
        
        generate_content_config = types.GenerateContentConfig(
            temperature=1,
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
                                    voice_name="Charon"
                                )
                            ),
                        ),
                        types.SpeakerVoiceConfig(
                            speaker=speaker_names[1],
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name="Kore"
                                )
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
                max_retries=3,
                initial_delay=10
            ):
                if (
                    chunk.candidates is None
                    or chunk.candidates[0].content is None
                    or chunk.candidates[0].content.parts is None
                ):
                    continue
                if chunk.candidates[0].content.parts[0].inline_data:
                    file_name = f"output_{i//NO_LINES_TO_PROCESS}"  # Use chunk index for file naming
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
                    print(chunk.text)
        except Exception as e:
            print(f"Failed to generate audio for chunk {i//NO_LINES_TO_PROCESS}: {e}")
            raise e

    # Merge all generated WAV files
    if generated_files:
        merge_wav_files(generated_files, episode_file_path)
        print("Merged all files into final_output.wav")
        
        # Clean up individual files
        for file in generated_files:
            try:
                os.remove(file)
                print(f"Removed temporary file: {file}")
            except Exception as e:
                print(f"Error removing file {file}: {e}")

def convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    """Generates a WAV file header for the given audio data and parameters.

    Args:
        audio_data: The raw audio data as a bytes object.
        mime_type: Mime type of the audio data.

    Returns:
        A bytes object representing the WAV file header.
    """
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
    with open(r"c:\Users\meir\Dropbox\tech_podcast_english\Episode_5\podcast_text.txt", "r", encoding="utf-8") as f:
        podcast_text = f.read()
    episode_file_path = r"c:\Users\meir\Dropbox\tech_podcast_english\Episode_5\Episode_5.mp3"
    speaker_names = ["Amit", "Yuval"]
    generate_podcast_episode_audio_from_text(podcast_text, episode_file_path, speaker_names)

if __name__ == "__main__":
    main()
