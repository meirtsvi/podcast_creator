import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os
import dotenv

from podcast_creator.logger import logger
from podcast_creator.main import process_languages

dotenv.load_dotenv()

GEN_PODCAST_TRIGGER_FILENAME = os.environ.get("GENERATE_PODCAST_TRIGGER_FILEPATH")

class FileChangeHandler(FileSystemEventHandler):
    def on_modified(self, event):
        event_path_abs = os.path.abspath(event.src_path)
        if GEN_PODCAST_TRIGGER_FILENAME and event_path_abs == os.path.abspath(GEN_PODCAST_TRIGGER_FILENAME):
            logger.info(f"Change detected in trigger file {GEN_PODCAST_TRIGGER_FILENAME}, running process_languages()...")
            process_languages("")
            return # Event handled as trigger file modification

    def on_created(self, event):
        event_path_abs = os.path.abspath(event.src_path)
        if GEN_PODCAST_TRIGGER_FILENAME and event_path_abs == os.path.abspath(GEN_PODCAST_TRIGGER_FILENAME):
            logger.info(f"Creation detected for trigger file {GEN_PODCAST_TRIGGER_FILENAME}, running process_languages()...")
            process_languages("")



            return # Event handled as trigger file creation

if __name__ == "__main__":
    event_handler = FileChangeHandler()
    observer = Observer()

    watched_items_log = []

    # Setup monitoring for the trigger file (GEN_PODCAST_TRIGGER_FILENAME)
    if GEN_PODCAST_TRIGGER_FILENAME:
        abs_trigger_file_path = os.path.abspath(GEN_PODCAST_TRIGGER_FILENAME)
        dir_to_watch_trigger = os.path.dirname(abs_trigger_file_path)
        try:
            # Schedule even if it's the same directory as data_file_to_monitor; watchdog handles duplicates.
            observer.schedule(event_handler, path=dir_to_watch_trigger, recursive=False)
            watched_items_log.append(f"trigger file ({abs_trigger_file_path}) in directory {dir_to_watch_trigger}")
        except Exception as e:
            logger.error(f"Error scheduling monitoring for trigger file directory {dir_to_watch_trigger}: {e}")
    else:
        logger.warning(f"Trigger file (from env var GENERATE_PODCAST_TRIGGER_FILEPATH) is not configured.")

    if not watched_items_log:
        logger.error("Error: No valid files/directories configured for monitoring. Exiting.")
    else:
        observer.start()
        logger.info(f"Monitoring started for: {'; '.join(watched_items_log)}")
        logger.info("Press Ctrl+C to stop.")
        try:

            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\\nMonitoring stopped by user.")
            observer.stop()
        observer.join()
