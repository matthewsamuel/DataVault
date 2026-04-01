"""
DataVault Dashboard - Configuration Loader
Loads paths from config.json with graceful degradation
"""
import json
import os
from pathlib import Path
from typing import Dict, Any

class Config:
    """Configuration loaded from config.json"""
    
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self._load_config()
        self._init_paths()
        
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

        self.VAULT_ROOT = Path(data["vault_root"])
        self.RAW_FOLDER = Path(data["raw_folder"])
        
        # Duplicates folder
        duplicates_folder = data.get('duplicates_folder')
        self.DUPLICATES_FOLDER = Path(duplicates_folder) if duplicates_folder else self.VAULT_ROOT / 'RAW_DUPLICATES'
        
        # Trash folder
        trash_folder = data.get('trash_folder')
        self.TRASH_FOLDER = Path(trash_folder) if trash_folder else self.VAULT_ROOT / 'TRASH'

        self.CACHE_FOLDER = Path(data["cache_folder"])
        
        # Derived paths
        self.DB_PATH = self.CACHE_FOLDER / "datavault.db"
        self.THUMB_CACHE_PATH = self.CACHE_FOLDER / "thumbs"
    
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
    
    def ensure_cache_dirs(self):
        """Create cache directories if they don't exist"""
        try:
            self.CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
            self.THUMB_CACHE_PATH.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Could not create cache directories: {e}")
            return False
    
    def ensure_duplicates_folder(self):
        """Create duplicates folder if it doesn't exist"""
        try:
            self.DUPLICATES_FOLDER.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Could not create duplicates folder: {e}")
            return False

# Supported media extensions
PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.tiff', '.webp', '.gif', '.bmp', '.raw', '.cr2', '.nef', '.arw', '.dng'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.3gp', '.mpg', '.mpeg', '.mts', '.m2ts'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma'}
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# Folders to exclude from scanning
EXCLUDED_FOLDER_NAMES = {
    'RAW_DUPLICATES',
    '.datavault_cache',
    'dashboard'
}

# Thumbnail settings
THUMB_SIZE = (300, 300)
THUMB_QUALITY = 85

def validate_path_in_vault(path: Path, vault_root: Path) -> bool:
    """
    Security-critical: Validate that a path is inside the vault.
    Prevents directory traversal attacks.
    """
    try:
        # Resolve to absolute paths
        abs_path = path.resolve()
        abs_vault = vault_root.resolve()
        
        # Check if path is inside vault
        return abs_path.is_relative_to(abs_vault)
    except (ValueError, RuntimeError):
        return False

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
