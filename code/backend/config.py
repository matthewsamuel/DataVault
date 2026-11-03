"""
DataVault Dashboard - Configuration Loader
Loads paths from config.json with graceful degradation
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

# Default config location: code/config.json (next to the backend package)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
DEFAULT_MODELS_FOLDER = Path(__file__).resolve().parent.parent / "models"


def resolve_config_path(explicit: Optional[str] = None) -> Path:
    """
    Pick the config file: explicit argument > $DATAVAULT_CONFIG > code/config.json.
    The environment override lets tests and development point at a test vault
    without touching the real config.
    """
    if explicit:
        return Path(explicit)
    env_path = os.environ.get("DATAVAULT_CONFIG")
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


# Where database backups go unless config.json says "backup_folder" (see backend/backup.py)
DEFAULT_BACKUP_FOLDER = Path.home() / "Library" / "Application Support" / "DataVault" / "backups"


def _positive_int(value: Any, default: int) -> int:
    """A count from config.json (at least 1), or the default when it is missing or not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(1, int(value))


def _expand(value: str) -> Path:
    """The path with a leading '~' turned into the home folder (kept as written without one)."""
    try:
        return Path(value).expanduser()
    except RuntimeError:
        return Path(value)


def _real_path(value: str) -> Path:
    """
    The configured path with symlinks resolved, so every path the app walks, compares or
    stores uses one spelling (a vault_root through a symlink would otherwise give two).
    A leading '~' is the user's home folder (as the README writes the backup folder), not a
    folder named '~' below wherever the app was started.
    A disk that is not mounted yet resolves as far as it exists; on failure keep it as given.
    """
    path = _expand(value)
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


class Config:
    """Configuration loaded from config.json"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = resolve_config_path(config_path)
        self._load_config()
        self._init_paths()
        self._guard_test_mode()

    def _load_config(self):
        """Load configuration from JSON file"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}\n"
                "Please create config.json with vault paths."
            )
        
        with open(self.config_path, 'r') as f:
            self._data = json.load(f)
    
    def _init_paths(self):
        """Initialize paths from configuration"""
        data = self._data

        self.VAULT_ROOT = _real_path(data["vault_root"])
        self.RAW_FOLDER = _real_path(data["raw_folder"])
        
        # Duplicates folder
        duplicates_folder = data.get('duplicates_folder')
        self.DUPLICATES_FOLDER = _real_path(duplicates_folder) if duplicates_folder else self.VAULT_ROOT / 'RAW_DUPLICATES'
        
        # Trash folder
        trash_folder = data.get('trash_folder')
        self.TRASH_FOLDER = _real_path(trash_folder) if trash_folder else self.VAULT_ROOT / 'TRASH'

        self.CACHE_FOLDER = _real_path(data["cache_folder"])

        # Database backups (backend/backup.py): on the Mac's own disk, not next to the
        # database on the T7. In test mode the default stays inside the temp directory.
        # A relative backup_folder would depend on the folder the app was started from (and
        # put the backups next to the app on the T7): it is not used, and Instellingen says so.
        backup_folder = data.get('backup_folder')
        self.BACKUP_FOLDER_NOTE = None
        if backup_folder and not _expand(backup_folder).is_absolute():
            self.BACKUP_FOLDER_NOTE = (
                f'De back-upmap "{backup_folder}" in config.json is geen volledig pad, dus de app gebruikt '
                'de standaardmap. Schrijf het pad helemaal uit (beginnend met / of ~/).')
            backup_folder = None
        if backup_folder:
            self.BACKUP_FOLDER = _real_path(backup_folder)
        elif os.environ.get("DATAVAULT_TESTING") == "1":
            self.BACKUP_FOLDER = self.CACHE_FOLDER / "backups"
        else:
            self.BACKUP_FOLDER = _real_path(str(DEFAULT_BACKUP_FOLDER))
        self.BACKUP_KEEP_DAILY = _positive_int(data.get('backup_keep_daily'), 7)
        self.BACKUP_KEEP_WEEKLY = _positive_int(data.get('backup_keep_weekly'), 4)
        # Downloaded ML models (the 'Slim zoeken' model): code/models/ next to the backend
        # by default (on the T7 with the app), or wherever 'models_folder' points. Models
        # are only ever downloaded when the user asks (backend/semantic.py).
        models_folder = data.get('models_folder')
        self.MODELS_FOLDER = _real_path(models_folder) if models_folder else DEFAULT_MODELS_FOLDER
        
        # Derived paths
        self.DB_PATH = self.CACHE_FOLDER / "datavault.db"
        self.THUMB_CACHE_PATH = self.CACHE_FOLDER / "thumbs"

        # DataGatherer (backend/datagatherer/): the person folder under <vault>/data that raw
        # exports go into (default: the first folder there), and extra folders the Downloads
        # watcher looks in (besides ~/Downloads) while an export is pending
        owner = data.get("data_owner")
        self.DATA_OWNER = owner if isinstance(owner, str) and owner.strip() and "/" not in owner \
            and not owner.startswith(".") else None
        watch = data.get("datagatherer_watch_folders")
        self.GATHER_WATCH_FOLDERS = [_real_path(p) for p in watch if isinstance(p, str) and p] \
            if isinstance(watch, list) else None

    def _guard_test_mode(self):
        """
        Safety net: with DATAVAULT_TESTING=1 every configured folder must live
        inside the system temp directory, so a test run can never reach a real archive.
        """
        if os.environ.get("DATAVAULT_TESTING") != "1":
            return
        temp_root = Path(tempfile.gettempdir()).resolve()
        for name, path in [
            ("vault_root", self.VAULT_ROOT),
            ("raw_folder", self.RAW_FOLDER),
            ("duplicates_folder", self.DUPLICATES_FOLDER),
            ("trash_folder", self.TRASH_FOLDER),
            ("cache_folder", self.CACHE_FOLDER),
            ("backup_folder", self.BACKUP_FOLDER),
        ]:
            if not path.resolve().is_relative_to(temp_root):
                raise RuntimeError(
                    f"DATAVAULT_TESTING=1 but {name} ({path}) is outside {temp_root}; "
                    "refusing to run tests against a real archive."
                )
    
    def validate_paths(self) -> Dict[str, bool]:
        """
        Validate that paths exist and are accessible.
        Returns dict of path names and their availability.
        Does NOT raise exceptions - allows graceful degradation.
        """
        results = {
            "vault_root": self.VAULT_ROOT.exists(),
            "raw_folder": self.RAW_FOLDER.exists(),
            "duplicates_folder": self.DUPLICATES_FOLDER.exists(),
            "cache_folder": self.CACHE_FOLDER.exists()
        }
        return results
    
    def _make_dir(self, path: Path) -> None:
        """
        mkdir that never makes the vault root itself: below VAULT_ROOT each folder is
        made one at a time, so with the vault renamed or unplugged this fails
        (FileNotFoundError) instead of creating an empty vault at the old path.
        A folder outside the vault (a separate cache folder) is made as before.
        """
        try:
            rel = path.relative_to(self.VAULT_ROOT)
        except ValueError:
            path.mkdir(parents=True, exist_ok=True)
            return
        current = self.VAULT_ROOT
        for part in rel.parts:
            current = current / part
            current.mkdir(exist_ok=True)

    def ensure_cache_dirs(self):
        """Create cache directories if they don't exist"""
        try:
            self._make_dir(self.CACHE_FOLDER)
            self._make_dir(self.THUMB_CACHE_PATH)
            return True
        except Exception as e:
            print(f"Warning: Could not create cache directories: {e}")
            return False
    
    def ensure_duplicates_folder(self):
        """Create duplicates folder if it doesn't exist"""
        try:
            self._make_dir(self.DUPLICATES_FOLDER)
            return True
        except Exception as e:
            print(f"Warning: Could not create duplicates folder: {e}")
            return False

# Supported media extensions: the ONE list every part of the app uses (viewer, timeline,
# Keurer, Shows, on this day, search, duplicate scan, thumbnails). Add a format here only.
RAW_PHOTO_EXTENSIONS = {'.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.raf', '.orf', '.rw2'}
PHOTO_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.heic', '.heif', '.hif', '.tif', '.tiff', '.webp', '.gif', '.bmp',
} | RAW_PHOTO_EXTENSIONS
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.3gp', '.mpg', '.mpeg', '.mts', '.m2ts'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma'}
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# Content types Python's mimetypes does not know (it would fall back to text/plain).
# A browser cannot show RAW photos itself; the viewer then shows the thumbnail.
MEDIA_CONTENT_TYPES = {
    '.heic': 'image/heic', '.heif': 'image/heif', '.hif': 'image/heif',
    '.raw': 'image/x-raw', '.cr2': 'image/x-canon-cr2', '.cr3': 'image/x-canon-cr3',
    '.nef': 'image/x-nikon-nef', '.arw': 'image/x-sony-arw', '.dng': 'image/x-adobe-dng',
    '.raf': 'image/x-fuji-raf', '.orf': 'image/x-olympus-orf', '.rw2': 'image/x-panasonic-rw2',
    '.mts': 'video/mp2t', '.m2ts': 'video/mp2t', '.mkv': 'video/x-matroska',
    '.flac': 'audio/flac', '.m4a': 'audio/mp4', '.wma': 'audio/x-ms-wma',
}


def media_type(path: Path) -> str:
    """'photo', 'video', 'audio' or 'other' (a document, archive, ...: never shown as a photo)."""
    ext = path.suffix.lower()
    if ext in PHOTO_EXTENSIONS:
        return 'photo'
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    if ext in AUDIO_EXTENSIONS:
        return 'audio'
    return 'other'


def content_type(path: Path) -> str:
    """The Content-Type to serve a vault file with; unknown files are a download, never text/plain."""
    import mimetypes
    ext = path.suffix.lower()
    if ext in MEDIA_CONTENT_TYPES:
        return MEDIA_CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or 'application/octet-stream'

# Thumbnail settings
THUMB_SIZE = (300, 300)
THUMB_QUALITY = 85

def is_media_file(path: Path) -> bool:
    """Check if file is a supported media type"""
    return path.suffix.lower() in MEDIA_EXTENSIONS

def is_photo(path: Path) -> bool:
    """Check if file is a photo"""
    return path.suffix.lower() in PHOTO_EXTENSIONS

def is_video(path: Path) -> bool:
    """Check if file is a video"""
    return path.suffix.lower() in VIDEO_EXTENSIONS

def is_audio(path: Path) -> bool:
    """Check if file is an audio file"""
    return path.suffix.lower() in AUDIO_EXTENSIONS
