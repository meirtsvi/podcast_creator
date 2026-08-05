from urllib.parse import quote

from podcast_creator.logger import logger
from pydub import AudioSegment, silence
from mutagen.id3 import ID3, COMM, CHAP, CTOC, CTOCFlags, TIT2, WXXX, ID3NoHeaderError
from mutagen.mp3 import MP3

try:
    # Only used to measure a frame's serialised size - see _build_chap_frames(). These are
    # mutagen internals, so losing them must not stop the module from importing.
    from mutagen.id3._tags import save_frame
    from mutagen.id3._util import ID3SaveConfig
except ImportError:  # pragma: no cover
    save_frame = None

# add_pre_and_post_audio() puts 3s of intro music before the speech starts and appends a 4s
# outro after it. Chapter timings are offset by these, so they are named rather than repeated.
INTRO_LEAD_MS = 3000
OUTRO_TAIL_MS = 4000

# Reserved URL characters that must survive percent-encoding, plus '%' so an already-encoded
# URL is not encoded a second time.
SAFE_URL_CHARS = ":/?#[]@!$&'()*+,;=%~"


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


def _chap_frame(index, title, start_ms, end_ms, url, padding=0):
    sub_frames = [TIT2(encoding=1, text=[title])]
    if url:
        # WXXX.url is Latin-1 only, so a non-ASCII URL has to be percent-encoded first.
        sub_frames.append(WXXX(encoding=1, desc="", url=quote(url, safe=SAFE_URL_CHARS)))
    return CHAP(
        element_id=f"chp{index:02d}" + "_" * padding,
        start_time=int(start_ms),
        end_time=int(end_ms),
        sub_frames=sub_frames,
    )


def _build_chap_frames(chapters):
    """Build the CHAP frames, padded so they are written in chronological order.

    mutagen sorts frames on save by (priority, serialised length, hash key), and every CHAP
    shares one priority - so without help they land in the file ordered by title length
    (mutagen issue #506). The start times stay correct either way, but anything that trusts
    file order (ffmpeg, and so probably whatever the host runs) then lists them scrambled.

    Padding each element_id until every frame serialises to the same length pushes the sort
    onto the hash key, which is the zero-padded index. The element_id is only an internal
    cross-reference from the CTOC, so the padding is never visible to a listener.
    """
    frames = [_chap_frame(i, *chapter) for i, chapter in enumerate(chapters)]
    if save_frame is None:
        return frames
    try:
        config = ID3SaveConfig(3, "/")
        sizes = [len(save_frame(frame, config=config)) for frame in frames]
        target = max(sizes)
        if len(set(sizes)) > 1:
            frames = [_chap_frame(i, *chapter, padding=target - size)
                      for i, (chapter, size) in enumerate(zip(chapters, sizes))]
    except Exception as e:
        # save_frame/ID3SaveConfig are mutagen internals. If they move, chapters are still
        # correct - just possibly out of order in the file.
        logger.warning(f"Could not equalise chapter frame sizes, order may be off: {e}")
    return frames


def add_chapters_to_mp3(mp3_path, chapters):
    """Write ID3 chapter frames (CHAP plus a CTOC index) into the MP3.

    `chapters` is a list of (title, start_ms, end_ms, url) tuples; url may be None.

    Saved as ID3v2.3, not v2.4: the ID3v2 Chapter Frame Addendum targets v2.3 and that is the
    version players actually read. Hebrew and Russian titles survive because v2.3 carries them
    as UTF-16 (encoding 1) - mutagen downgrades the encoding for us, recursively into the CHAP
    sub-frames. Only encoding 0 (Latin-1) would fail on them.
    """
    if not chapters:
        logger.warning(f"No chapters to write to {mp3_path}")
        return

    tags = _load_id3(mp3_path)

    # Drop any chapters from a previous run so re-processing does not accumulate them.
    tags.delall("CHAP")
    tags.delall("CTOC")

    # An empty TIT2 serialises to nothing at all, which would leave an untitled chapter.
    usable = [chapter for chapter in chapters if chapter[0]]
    if len(usable) < len(chapters):
        logger.warning(f"Skipping {len(chapters) - len(usable)} untitled chapter(s) of {mp3_path}")
    if not usable:
        logger.warning(f"No usable chapters to write to {mp3_path}")
        return

    frames = _build_chap_frames(usable)
    for frame in frames:
        tags.add(frame)

    tags.add(CTOC(
        element_id="toc",
        flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
        child_element_ids=[f.element_id for f in frames],
        sub_frames=[TIT2(encoding=1, text=["Chapters"])],
    ))

    # Must run after the frames are added: it converts v2.4-only frames that pydub's export may
    # have left behind, and recurses into CHAP.
    tags.update_to_v23()
    tags.save(mp3_path, v2_version=3)
    logger.info(f"Added {len(frames)} chapters to {mp3_path}")


if __name__ == '__main__':
    add_pre_and_post_audio(r"c:\Users\meir\Dropbox\tech_podcast_hebrew\Episode_96\Episode_96.mp3")