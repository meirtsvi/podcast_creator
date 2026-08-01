import os
import time

import dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from podcast_creator.logger import logger
from podcast_creator.main import process_languages

dotenv.load_dotenv()

GEN_PODCAST_TRIGGER_FILENAME = os.environ.get("GENERATE_PODCAST_TRIGGER_FILEPATH")


class TriggerFileHandler(FileSystemEventHandler):
    """Runs the podcast pipeline whenever the trigger file is created or written.

    Watchdog reports events for a whole directory, so every event is checked against the one
    path we actually care about. Both sides use realpath, not abspath: watchdog reports paths
    with symlinks already resolved, and abspath leaves them intact, so on any path crossing a
    symlink (/tmp and /var on macOS, or a mounted volume alias) the two would never compare
    equal and the trigger would silently never fire.
    """

    def __init__(self, trigger_path):
        self.trigger_path = os.path.realpath(trigger_path)

    def _run_if_triggered(self, event):
        if os.path.realpath(event.src_path) != self.trigger_path:
            return
        logger.info(f"Change detected in trigger file {self.trigger_path}, running process_languages()...")
        process_languages("")

    def on_created(self, event):
        self._run_if_triggered(event)

    def on_modified(self, event):
        self._run_if_triggered(event)


def main():
    if not GEN_PODCAST_TRIGGER_FILENAME:
        logger.error("GENERATE_PODCAST_TRIGGER_FILEPATH is not set, so there is nothing to "
                     "monitor. Exiting.")
        return

    trigger_path = os.path.realpath(GEN_PODCAST_TRIGGER_FILENAME)
    watch_dir = os.path.dirname(trigger_path)

    # Checked explicitly: scheduling a watch on a missing directory does not raise on every
    # platform, and the observer would then sit in its loop forever watching nothing.
    if not os.path.isdir(watch_dir):
        logger.error(f"Cannot monitor {trigger_path}: directory {watch_dir} does not exist. Exiting.")
        return

    observer = Observer()
    try:
        observer.schedule(TriggerFileHandler(trigger_path), path=watch_dir, recursive=False)
    except Exception as e:
        logger.error(f"Cannot monitor {watch_dir}: {e}. Exiting.")
        return

    observer.start()
    logger.info(f"Monitoring {trigger_path} for changes. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user.")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
