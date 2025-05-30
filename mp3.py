from pydub import AudioSegment
from logger import logger

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
