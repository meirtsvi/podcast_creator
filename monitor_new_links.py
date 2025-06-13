from config import SINGLE_URL_LINKS_FILENAME, MULTI_URL_LINKS_FILENAME
from main import process_languages
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os
import json

HN_FILENAME_TO_MONITOR_FILENAME = "hn_filename_to_monitor.txt"

def get_monitored_filename():
    try:
        with open(HN_FILENAME_TO_MONITOR_FILENAME, 'r') as f:
            return f.read().strip()
    except Exception:
        return None

class HNFileChangeHandler(FileSystemEventHandler):
    def __init__(self):
        self.monitored_filename = get_monitored_filename()
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
        # If the file that stores the monitored filename changes, reload it
        if os.path.abspath(event.src_path) == os.path.abspath(HN_FILENAME_TO_MONITOR_FILENAME):
            self.monitored_filename = get_monitored_filename()
            self.last_content = self._read_file()
            print(f"Monitored filename updated to: {self.monitored_filename}")
            return

        # If the monitored file changes, process it
        if self.monitored_filename and os.path.abspath(event.src_path) == os.path.abspath(self.monitored_filename):
            new_content_json_str = self._read_file()
            if new_content_json_str != self.last_content:
                self.last_content = new_content_json_str
                print(f"Change detected in {self.monitored_filename}. Processing URLs...")

                try:
                    data = json.loads(new_content_json_str)

                    podcast1_urls = [item['url'] for item in data.get('podcast1', []) if 'url' in item]
                    podcast2_urls = [item['url'] for item in data.get('podcast2', []) if 'url' in item]

                    # Ensure the 'sources' directory exists
                    os.makedirs(os.path.dirname(SINGLE_URL_LINKS_FILENAME), exist_ok=True)
                    os.makedirs(os.path.dirname(MULTI_URL_LINKS_FILENAME), exist_ok=True)

                    with open(MULTI_URL_LINKS_FILENAME, 'a', encoding='utf-8') as f:
                        for url in podcast1_urls:
                            f.write(f"{url}\n")
                    print(f"Wrote {len(podcast1_urls)} URLs to {MULTI_URL_LINKS_FILENAME}")

                    with open(SINGLE_URL_LINKS_FILENAME, 'a', encoding='utf-8') as f:
                        for url in podcast2_urls:
                            f.write(f"{url}\n")
                    print(f"Wrote {len(podcast2_urls)} URLs to {SINGLE_URL_LINKS_FILENAME}")

                except json.JSONDecodeError:
                    print(f"Error: Content of {self.monitored_filename} is not valid JSON.")
                    return
                except Exception as e:
                    print(f"An error occurred during URL extraction: {e}")
                    return

                print(f"Running process_languages()...")
                process_languages("")

if __name__ == "__main__":
    event_handler = HNFileChangeHandler()
    observer = Observer()
    monitored_file = get_monitored_filename()
    if monitored_file and os.path.dirname(monitored_file):
        observer.schedule(event_handler, path=os.path.dirname(monitored_file), recursive=False)
    observer.start()
    print(f"Monitoring {monitored_file} for changes...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
