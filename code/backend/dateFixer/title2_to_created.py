import shutil
import subprocess
import re
from pathlib import Path
from datetime import datetime

# --- CONFIGURATION ---
FOLDER_PATH = "/Volumes/T7/vault/RAW/0title"

MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".flv", ".webm",
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff", ".bmp"
}

# Verwacht patroon zoals:
# 20160315_095506000_iOS.MOV
FILENAME_PATTERN = re.compile(r"^(\d{8})_(\d{6})\d{3}_.*$")


# --- EXTRACT DATETIME FROM FILENAME ---
def extract_datetime_from_filename(filename: str) -> datetime | None:
    match = FILENAME_PATTERN.match(filename)
    if not match:
        return None

    date_part = match.group(1)   # YYYYMMDD
    time_part = match.group(2)   # HHMMSS

    try:
        return datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S")
    except ValueError:
        return None


# --- SET MACOS CREATION DATE ---
def set_macos_creation_date(file_path: Path, dt: datetime) -> bool:
    formatted_date = dt.strftime("%m/%d/%Y %H:%M:%S")
    setfile_path = shutil.which("SetFile")

    if not setfile_path:
        print("[ERROR] SetFile niet gevonden in PATH.")
        print("Voer dit uit in Terminal: xcode-select --install")
        return False

    try:
        subprocess.run(
            [setfile_path, "-d", formatted_date, str(file_path)],
            check=True,
            capture_output=True,
            text=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Kon creation date niet aanpassen voor {file_path.name}")
        print(f"[ERROR] stdout: {e.stdout.strip()}")
        print(f"[ERROR] stderr: {e.stderr.strip()}")
        return False


# --- MAIN ---
def main():
    folder = Path(FOLDER_PATH)

    print("Map bestaat:", folder.exists())
    print("Is directory:", folder.is_dir())

    if not folder.exists() or not folder.is_dir():
        print("[ERROR] Ongeldige map.")
        return

    processed = 0
    updated = 0
    skipped = 0
    failed = 0

    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        processed += 1

        dt = extract_datetime_from_filename(file_path.name)
        if dt is None:
            print(f"[SKIP] Geen geldige datum in bestandsnaam: {file_path.name}")
            skipped += 1
            continue

        print(f"Updating: {file_path.name} -> {dt}")

        if set_macos_creation_date(file_path, dt):
            print(f"[OK] Updated: {file_path.name}")
            updated += 1
        else:
            print(f"[FAIL] Failed: {file_path.name}")
            failed += 1

    print("\n--- SUMMARY ---")
    print("Processed:", processed)
    print("Updated  :", updated)
    print("Skipped  :", skipped)
    print("Failed   :", failed)


if __name__ == "__main__":
    main()