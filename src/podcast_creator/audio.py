from podcast_creator.logger import logger
from pydub import AudioSegment, silence

def convert_wav_to_mp3(audio_file_path, tags = None):
    logger.info(f"Converting {audio_file_path} WAV to MP3...")
    audio = AudioSegment.from_file(audio_file_path)
    audio.export(audio_file_path, format="mp3", tags=tags)

def add_pre_and_post_audio(podcast_mp3_path):
    logger.info("Adding pre and post audio to podcast...")
    # Load MP3 files
    pre = AudioSegment.from_file("pre_and_post.mp3")
    post = AudioSegment.from_file(podcast_mp3_path)

    # First 3s of pre
    pre_intro = pre[:3000]

    # Pre fade-out from 3s to 11s
    pre_fade = pre[3000:11000].fade_out(8000)

    # Post: first 8s, boosted and faded in
    post_boosted = post[:8000].apply_gain(+6).fade_in(3000)

    # Overlay post fade-in on top of pre fade-out
    overlap = pre_fade.overlay(post_boosted)

    # Remaining post audio after 8s
    post_tail = post[8000:]

    # Outro: First 4s of pre, with fade-out
    pre_outro = pre[:4000].fade_out(4000)

    # Final composition
    final_audio = pre_intro + overlap + post_tail + pre_outro

    # Export
    final_audio.export(podcast_mp3_path, format="mp3")


def detect_silence_in_wav(wav_file_path):
    # Load the WAV file
    audio = AudioSegment.from_wav(wav_file_path)

    # Detect silences longer than 5 seconds (5000 ms)
    # silence_thresh: threshold in dBFS below which is considered silence (typical is audio.dBFS - 16)
    # min_silence_len: minimum length of a silence to be considered, in ms
    silent_sections = silence.detect_silence(
        audio,
        min_silence_len=5000,
        silence_thresh=audio.dBFS - 16,
        seek_step=100  # Default is 1 ms; try 50-200 ms for speedup, with some accuracy loss
    )

    return len(silent_sections) > 0  # Return True if any silence detected, else False

if __name__ == '__main__':
    add_pre_and_post_audio(r"c:\Users\meir\Dropbox\tech_podcast_hebrew\Episode_96\Episode_96.mp3")