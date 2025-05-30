import logging
import datetime

def setup_logging():
    """Configure logging for the podcast creator application."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger('podcast_creator')

logger = setup_logging()
