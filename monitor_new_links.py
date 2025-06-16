from logger import logger
from config import SINGLE_URL_LINKS_FILEPATH, MULTI_URL_LINKS_FILEPATH
from main import process_languages
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os
import json
import dotenv

dotenv.load_dotenv()

HN_FILENAME_TO_MONITOR_FILENAME = os.environ.get("HN_SORTER_FILEPATH")
GEN_PODCAST_TRIGGER_FILENAME = os.environ.get("GENERATE_PODCAST_TRIGGER_FILEPATH")

class HNFileChangeHandler(FileSystemEventHandler):
    def __init__(self):
        self.monitored_filename = HN_FILENAME_TO_MONITOR_FILENAME # Use the env var value directly
        self.last_content = self._read_file()

    def _read_file(self):
        if not self.monitored_filename:
            return None
        try:
            with open(self.monitored_filename, 'r') as f:
                return f.read().strip()
        except Exception:
            return None

    def on_modified(self, event):
        event_path_abs = os.path.abspath(event.src_path)

        # Check 1: Monitored data file (path from HN_FILENAME_TO_MONITOR_FILENAME)
        if self.monitored_filename and event_path_abs == os.path.abspath(self.monitored_filename):
            new_content_json_str = self._read_file()
            if new_content_json_str != self.last_content:
                self.last_content = new_content_json_str
                logger.info(f"Change detected in data file {self.monitored_filename}. Processing URLs...")

                try:
                    data = json.loads(new_content_json_str)

                    podcast1_urls = [item['url'] for item in data.get('podcast1', []) if 'url' in item]
                    podcast2_urls = [item['url'] for item in data.get('podcast2', []) if 'url' in item]

                    # Ensure the 'sources' directory exists
                    os.makedirs(os.path.dirname(SINGLE_URL_LINKS_FILEPATH), exist_ok=True)
                    os.makedirs(os.path.dirname(MULTI_URL_LINKS_FILEPATH), exist_ok=True)

                    with open(MULTI_URL_LINKS_FILEPATH, 'a', encoding='utf-8') as f:
                        for url in podcast1_urls:
                            f.write(f"{url}\n")
                    logger.info(f"Wrote {len(podcast1_urls)} URLs to {MULTI_URL_LINKS_FILEPATH}")

                    with open(SINGLE_URL_LINKS_FILEPATH, 'a', encoding='utf-8') as f:
                        for url in podcast2_urls:
                            f.write(f"{url}\n")
                    logger.info(f"Wrote {len(podcast2_urls)} URLs to {SINGLE_URL_LINKS_FILEPATH}")

                except json.JSONDecodeError:
                    logger.error(f"Error: Content of {self.monitored_filename} is not valid JSON.")
                    return # Stop processing this event
                except Exception as e:
                    logger.error(f"An error occurred during URL extraction: {e}")
                    return # Stop processing this event

                logger.info(f"Running process_languages()...")
                process_languages("")
            return # Event handled as data file modification

        # Check 2: GEN_PODCAST_TRIGGER_FILENAME (if it's a different file and modified)
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
    event_handler = HNFileChangeHandler()
    observer = Observer()

    watched_items_log = []

    # Setup monitoring for the data file (path from HN_FILENAME_TO_MONITOR_FILENAME)
    data_file_to_monitor = event_handler.monitored_filename
    if data_file_to_monitor:
        abs_data_file_path = os.path.abspath(data_file_to_monitor)
        dir_to_watch_data = os.path.dirname(abs_data_file_path)
        try:
            observer.schedule(event_handler, path=dir_to_watch_data, recursive=False)
            watched_items_log.append(f"data file ({abs_data_file_path}) in directory {dir_to_watch_data}")
        except Exception as e:
            logger.error(f"Error scheduling monitoring for data file directory {dir_to_watch_data}: {e}")
    else:
        logger.warning(f"Data file to monitor (from env var HN_SORTER_FILEPATH='{HN_FILENAME_TO_MONITOR_FILENAME}') is not configured or accessible.")

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
