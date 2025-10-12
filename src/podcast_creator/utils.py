import importlib.resources as resources

def read_file_content(filename: str) -> str:
    with resources.files("podcast_creator").joinpath(filename).open("r", encoding="utf-8") as file:
        return file.read().strip()

