def read_file_content(filename: str) -> str:
    with open(filename, "r", encoding="utf-8") as file:
        return file.read().strip()

