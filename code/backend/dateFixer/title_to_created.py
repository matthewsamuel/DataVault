import re
import subprocess
from pathlib import Path
from datetime import datetime
import os

# --- CONFIGURATION ---
FOLDER_PATH = "/Users/matthewsamuel1/Desktop/OGreis2025/01vertrek/exported"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".flv", ".webm"}
FILENAME_PATTERN = re.compile(r".*?(\d{8})_(\d{6}).*")


def extract_datetime_from_filename(filename: str) -> datetime | None:
    match = FILENAME_PATTERN.match(filename)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def set_macos_creation_date(file_path: Path, dt: datetime) -> None:
    formatted_date = dt.strftime("%m/%d/%Y %H:%M:%S")
    subprocess.run(["SetFile", "-d", formatted_date, str(file_path)], check=True)


def set_modification_date(file_path: Path, dt: datetime) -> None:
    timestamp = dt.timestamp()
    os.utime(file_path, (timestamp, timestamp))


def main():
    folder = Path(FOLDER_PATH)

    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        dt = extract_datetime_from_filename(file_path.name)
        if dt is None:
            print(f"Skipped: {file_path.name}")
            continue

        try:
            set_macos_creation_date(file_path, dt)
            set_modification_date(file_path, dt)
            print(f"Updated: {file_path.name} -> {dt}")
        except Exception as e:
            print(f"Error for {file_path.name}: {e}")


if __name__ == "__main__":
    main()