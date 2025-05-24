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

def generate_with_retry(client, model, contents, config, max_retries=3, initial_delay=1):
    """Generate content with retry mechanism.
    
    Args:
        client: The Gemini client
        model: The model name
        contents: The content to generate
        config: The generation config
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        
    Returns:
        The generated content or None if all retries failed
    """
    delay = initial_delay
    last_error = None
    
    for attempt in range(max_retries):
        try:
            for chunk in client.models.generate_content_stream(
                model=model,
                contents=contents,
                config=config,
            ):
                yield chunk
            return  # Success, exit the function
            
        except ServerError as e:
            last_error = e
            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                print(f"Attempt {attempt + 1} failed with error: {e}")
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                print(f"All {max_retries} attempts failed")
                raise last_error

def generate_podcast_episode_audio_from_text(podcast_text, episode_file_path):
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    model = "gemini-2.5-pro-preview-tts"
    
    # Split text into lines and filter out empty lines
    lines = [line.strip() for line in podcast_text.split('\n') if line.strip()]
    
    # List to store generated WAV files
    generated_files = []
    
    # Process 10 lines at a time
    for i in range(0, len(lines), 10):
        # Get next 10 lines (or remaining lines if less than 10)
        chunk_lines = lines[i:i+10]
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
                            speaker="יובל",
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name="Charon"
                                )
                            ),
                        ),
                        types.SpeakerVoiceConfig(
                            speaker="עמית",
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
                client=client,
                model=model,
                contents=contents,
                config=generate_content_config,
                max_retries=3,
                initial_delay=1
            ):
                if (
                    chunk.candidates is None
                    or chunk.candidates[0].content is None
                    or chunk.candidates[0].content.parts is None
                ):
                    continue
                if chunk.candidates[0].content.parts[0].inline_data:
                    file_name = f"output_{i//10}"  # Use chunk index for file naming
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
            print(f"Failed to generate audio for chunk {i//10}: {e}")
            continue
    
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

