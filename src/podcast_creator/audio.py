from podcast_creator.logger import logger
from pydub import AudioSegment, silence
from mutagen.id3 import ID3, COMM, CHAP, CTOC, CTOCFlags, TIT2, ID3NoHeaderError
from mutagen.mp3 import MP3

# add_pre_and_post_audio() puts 3s of intro music before the speech starts and appends a 4s
# outro after it. Chapter timings are offset by these, so they are named rather than repeated.
INTRO_LEAD_MS = 3000
OUTRO_TAIL_MS = 4000


def convert_wav_to_mp3(audio_file_path):
    logger.info(f"Converting {audio_file_path} WAV to MP3...")
    audio = AudioSegment.from_file(audio_file_path)
    audio.export(audio_file_path, format="mp3")

def add_pre_and_post_audio(podcast_mp3_path):
    logger.info("Adding pre and post audio to podcast...")
    # Load MP3 files
    pre = AudioSegment.from_file("pre_and_post.mp3")
    post = AudioSegment.from_file(podcast_mp3_path)

    # First 3s of pre
    pre_intro = pre[:INTRO_LEAD_MS]

    # Pre fade-out from 3s to 11s
    pre_fade = pre[3000:11000].fade_out(8000)

    # Post: first 8s, boosted and faded in
    post_boosted = post[:8000].apply_gain(+6).fade_in(3000)

    # Overlay post fade-in on top of pre fade-out
    overlap = pre_fade.overlay(post_boosted)

    # Remaining post audio after 8s
    post_tail = post[8000:]

    # Outro: First 4s of pre, with fade-out
    pre_outro = pre[:OUTRO_TAIL_MS].fade_out(OUTRO_TAIL_MS)

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

def add_comment_to_mp3(mp3_path, comment_text):
    # 1. Try to load existing ID3 tags, or create new ones if they don't exist
    try:
        audio = ID3(mp3_path)
    except ID3NoHeaderError:
        # If no ID3 header found, create a new one and attach it to the file
        audio = MP3(mp3_path)
        audio.add_tags()
        audio = audio.tags # Now 'audio' refers to the ID3Tags object

    # 2. Define the comment frame
    new_comment_text = comment_text
    comment_frame = COMM(encoding=3, lang='eng', desc='', text=[new_comment_text])

    # 3. Add the comment frame to the ID3 tags
    # This will replace any existing COMM frame with the same language and description
    audio.add(comment_frame)

    # 4. Save the changes
    # If we used the ID3() constructor initially, calling save() works directly
    audio.save()
    logger.info(f"Added comment to {mp3_path}")

def _load_id3(mp3_path):
    """Return a writable ID3 tag object for `mp3_path`, creating one if absent."""
    try:
        return ID3(mp3_path)
    except ID3NoHeaderError:
        audio = MP3(mp3_path)
        audio.add_tags()
        return audio.tags


def get_audio_duration_ms(audio_file_path) -> int:
    """Length of an audio file in milliseconds."""
    return len(AudioSegment.from_file(audio_file_path))


def add_chapters_to_mp3(mp3_path, chapters):
    """Write ID3 chapter frames (CHAP plus a CTOC index) into the MP3.

    `chapters` is a list of (title, start_ms, end_ms) tuples. Saved as ID3v2.4 so that the
    UTF-8 titles survive - ID3v2.3 cannot hold UTF-8 text frames.
    """
    if not chapters:
        logger.warning(f"No chapters to write to {mp3_path}")
        return

    tags = _load_id3(mp3_path)

    # Drop any chapters from a previous run so re-processing does not accumulate them.
    tags.delall("CHAP")
    tags.delall("CTOC")

    element_ids = []
    for index, (title, start_ms, end_ms) in enumerate(chapters):
        element_id = f"chp{index}"
        element_ids.append(element_id)
        tags.add(CHAP(
            element_id=element_id,
            start_time=int(start_ms),
            end_time=int(end_ms),
            sub_frames=[TIT2(encoding=3, text=[title])],
        ))

    tags.add(CTOC(
        element_id="toc",
        flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
        child_element_ids=element_ids,
        sub_frames=[TIT2(encoding=3, text=["Chapters"])],
    ))

    tags.save(mp3_path, v2_version=4)
    logger.info(f"Added {len(chapters)} chapters to {mp3_path}")


if __name__ == '__main__':
    add_pre_and_post_audio(r"c:\Users\meir\Dropbox\tech_podcast_hebrew\Episode_96\Episode_96.mp3")