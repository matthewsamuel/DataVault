import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# --- CONFIGURATION ---
FOLDER_PATH = "/Volumes/T7/vault/library/fam/fotos"

MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".flv", ".webm",
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff", ".bmp"
}


# --- SET MACOS CREATION DATE ---
def set_macos_creation_date(file_path: Path, dt: datetime) -> bool:
    formatted_date = dt.strftime("%m/%d/%Y %H:%M:%S")
    setfile_path = shutil.which("SetFile")

    if not setfile_path:
        print("[ERROR] SetFile niet gevonden in PATH.")
        print("Voer dit uit in Terminal: xcode-select --install")
        return False

    print(f"[DEBUG] SetFile path: {setfile_path}")
    print(f"[DEBUG] Date used   : {formatted_date}")

    try:
        result = subprocess.run(
            [setfile_path, "-d", formatted_date, str(file_path)],
            check=True,
            capture_output=True,
            text=True
        )

        if result.stdout.strip():
            print(f"[DEBUG] stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            print(f"[DEBUG] stderr: {result.stderr.strip()}")

        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Kon creation date niet aanpassen voor {file_path.name}")
        print(f"[ERROR] Command : {' '.join(e.cmd)}")
        print(f"[ERROR] Code    : {e.returncode}")
        print(f"[ERROR] stdout  : {e.stdout.strip()}")
        print(f"[ERROR] stderr  : {e.stderr.strip()}")
        return False


# --- MAIN ---
def main():
    folder = Path(FOLDER_PATH)

    print("Map bestaat:", folder.exists())
    print("Is directory:", folder.is_dir())
    print("Resolved path:", folder.resolve())

    if not folder.exists() or not folder.is_dir():
        print("[ERROR] Ongeldige map.")
        return

    processed = 0
    updated = 0
    failed = 0

    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        processed += 1
        modified_dt = datetime.fromtimestamp(file_path.stat().st_mtime)

        print(f"Updating: {file_path.name} -> {modified_dt}")
        print(f"[DEBUG] Full path: {file_path}")

        if set_macos_creation_date(file_path, modified_dt):
            print(f"[OK] Updated: {file_path.name}")
            updated += 1
        else:
            print(f"[FAIL] Failed: {file_path.name}")
            failed += 1

    print("\n--- SUMMARY ---")
    print("Processed:", processed)
    print("Updated  :", updated)
    print("Failed   :", failed)


if __name__ == "__main__":
    main()