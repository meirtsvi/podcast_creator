import logging
import sys
import dotenv
import os
from colorama import init, Fore, Style

dotenv.load_dotenv()
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH")

init(autoreset=True)

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.INFO: Fore.LIGHTBLACK_EX,   # Gray
        logging.WARNING: Fore.YELLOW,       # Orange/Yellow
        logging.ERROR: Fore.RED,            # Red
        logging.CRITICAL: Fore.RED + Style.BRIGHT
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        message = super().format(record)
        return f"{color}{message}{Style.RESET_ALL}"

def setup_logging(logfile=LOG_FILE_PATH):
    logger = logging.getLogger('podcast_creator')
    logger.setLevel(logging.INFO)

    # Console handler with color
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(ColorFormatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))

    # File handler without color
    fh = logging.FileHandler(logfile, encoding='utf-8')
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))

    # Avoid duplicate handlers
    if not logger.hasHandlers():
        logger.addHandler(ch)
        logger.addHandler(fh)
    else:
        logger.handlers.clear()
        logger.addHandler(ch)
        logger.addHandler(fh)
    return logger

logger = setup_logging()
