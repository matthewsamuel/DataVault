from fastapi import APIRouter
from pathlib import Path
import shutil
from datetime import datetime
from typing import Dict, Any, List
import os
import subprocess

from backend.config import Config
from backend.routes.spotify import parse_spotify_data

router = APIRouter()
config: Config = None

def init_router(cfg: Config):
    global config
    config = cfg

@router.get("/storage")
async def get_storage_stats() -> Dict[str, Any]:
    if not config:
        return {"error": "Config not loaded"}
        
    vault_path = config.VAULT_ROOT
    if not vault_path.exists():
         return {"error": "Vault path does not exist"}
         
    total, used, free = shutil.disk_usage(vault_path)
    
    return {
        "total_gb": round(total / (2**30), 2),
        "used_gb": round(used / (2**30), 2),
        "free_gb": round(free / (2**30), 2),
        "percent_used": round((used / total) * 100, 1)
    }
@router.get("/storage-breakdown")
async def get_storage_breakdown() -> Dict[str, Any]:
    if not config:
        return {"error": "Config not loaded"}
        
    vault_path = config.VAULT_ROOT
    if not vault_path.exists():
         return {"error": "Vault path does not exist"}
         
    import os
    breakdown = {}
    # Calculate sizes for immediate subdirectories using `du` for speed
    import subprocess
    try:
        # du -sm * calculates size in MB for all items in the current directory
        # We run it inside the vault path
        cmd = ["du", "-sm"] + [entry.name for entry in os.scandir(vault_path) if entry.is_dir() and not entry.name.startswith('.')]
        
        result = subprocess.run(
            cmd,
            cwd=str(vault_path),
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0 or result.returncode == 1:
            # Parse output: "1234\tFolderName"
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        size_mb = int(parts[0])
                        folder_name = parts[1]
                        gb_size = round(size_mb / 1024, 2)
                        
                        if gb_size > 0:
                            breakdown[folder_name] = gb_size
                            
        # Sort by size descending
        sorted_breakdown = dict(sorted(breakdown.items(), key=lambda item: item[1], reverse=True))
        return {"breakdown": sorted_breakdown}

    except Exception as e:
        return {"error": str(e)}

@router.get("/storage-breakdown-nested")
async def get_storage_breakdown_nested(folder_path: str = None) -> Dict[str, Any]:
    """Get storage breakdown for a specific folder and its subfolders."""
    if not config:
        return {"error": "Config not loaded"}

    vault_path = config.VAULT_ROOT
    target_path = vault_path / folder_path if folder_path else vault_path

    if not target_path.exists() or not target_path.is_dir():
        return {"error": "Path does not exist"}

    try:
        target_path.relative_to(vault_path)
    except ValueError:
        return {"error": "Path is outside vault"}

    breakdown = {}
    try:
        entries = [entry for entry in os.scandir(target_path) if entry.is_dir() and not entry.name.startswith('.')]

        if not entries:
            return {"breakdown": {}, "path": folder_path or "/"}

        cmd = ["du", "-sm"] + [entry.name for entry in entries]
        result = subprocess.run(cmd, cwd=str(target_path), capture_output=True, text=True, check=False)

        if result.returncode == 0 or result.returncode == 1:
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        size_mb = int(parts[0])
                        folder_name = parts[1]
                        gb_size = round(size_mb / 1024, 2)
                        if gb_size > 0:
                            breakdown[folder_name] = gb_size

        sorted_breakdown = dict(sorted(breakdown.items(), key=lambda item: item[1], reverse=True))
        return {"breakdown": sorted_breakdown, "path": folder_path or "/"}

    except Exception as e:
        return {"error": str(e)}

@router.get("/largest-files")
async def get_largest_files(folder_path: str = None, limit: int = 20) -> Dict[str, Any]:
    """Get largest files in a specific folder."""
    if not config:
        return {"error": "Config not loaded"}

    vault_path = config.VAULT_ROOT
    target_path = vault_path / folder_path if folder_path else vault_path

    if not target_path.exists() or not target_path.is_dir():
        return {"error": "Path does not exist"}

    try:
        target_path.relative_to(vault_path)
    except ValueError:
        return {"error": "Path is outside vault"}

    files_with_sizes = []

    try:
        for root, _, files in os.walk(target_path):
            for filename in files:
                file_path = Path(root) / filename
                try:
                    size_bytes = file_path.stat().st_size
                    size_mb = round(size_bytes / (1024 * 1024), 2)

                    if size_mb > 0:
                        rel_path = str(file_path.relative_to(vault_path))
                        files_with_sizes.append({
                            "name": filename,
                            "path": rel_path,
                            "size_mb": size_mb,
                            "size_gb": round(size_mb / 1024, 2)
                        })
                except Exception:
                    pass

        sorted_files = sorted(files_with_sizes, key=lambda x: x['size_mb'], reverse=True)[:limit]
        return {"files": sorted_files, "path": folder_path or "/"}

    except Exception as e:
        return {"error": str(e)}

@router.get("/photo-on-this-day")
async def get_photo_on_this_day(allowed_folders: str = None) -> Dict[str, Any]:
    if not config:
        return {"error": "Config not loaded"}
         
    now = datetime.now()
    target_month = now.month
    target_day = now.day
    
    import os
    import random
    from backend.config import MEDIA_EXTENSIONS
    
    valid_photos = []
    vault_root = config.VAULT_ROOT
    
    try:
        if allowed_folders:
            # allow multiple comma-separated bases
            bases = [Path(p.strip()) for p in allowed_folders.split(',')]
            all_dirs = []
            for base in bases:
                if base.exists() and base.is_dir():
                    all_dirs.extend([x[0] for x in os.walk(base)])
            all_dirs = list(set(all_dirs))
        else:
            all_dirs = [x[0] for x in os.walk(vault_root)]
            
        random.shuffle(all_dirs)

        for root in all_dirs:
            try:
                for filename in os.listdir(root):
                    file_path = Path(root) / filename
                    if file_path.is_file() and file_path.suffix.lower() in MEDIA_EXTENSIONS:
                        stat = file_path.stat()
                        mtime_dt = datetime.fromtimestamp(stat.st_mtime)
                        
                        if mtime_dt.month == target_month and mtime_dt.day == target_day:
                            if mtime_dt.year != now.year:
                                valid_photos.append({
                                    "path": str(file_path),
                                    "name": filename,
                                    "year": mtime_dt.year,
                                    "type": "photo"
                                })
            except Exception:
                continue
            
            if len(valid_photos) >= 10:
                break
                
        if valid_photos:
             # Sort by year ascending
             valid_photos.sort(key=lambda x: x['year'])
             return {"photos": valid_photos[:10]}
        else:
             return {"photos": []}
             
    except Exception as e:
         return {"error": str(e)}

@router.get("/spotify-on-this-day")
async def get_spotify_on_this_day() -> Dict[str, Any]:
    now = datetime.now()
    date_filter = (now.month, now.day)
    
    try:
         data = parse_spotify_data(None, date_filter)
         tracks = data.get('on_this_day', [])
         if tracks:
              now_minutes = now.hour * 60 + now.minute
              def time_to_minutes(t):
                  h, m = map(int, t['time'].split(':'))
                  return h * 60 + m
              closest = min(tracks, key=lambda t: abs(time_to_minutes(t) - now_minutes))
              return {"track": closest}
         return {"track": None}
    except Exception as e:
         return {"error": str(e)}

@router.get("/photos-by-date")
async def get_photos_by_date(date: str) -> Dict[str, Any]:
    """
    Find up to 20 photos corresponding to a specific date (YYYY-MM-DD).
    """
    if not config:
        return {"error": "Config not loaded"}
        
    try:
        target_dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"error": "Invalid date format, expected YYYY-MM-DD"}
        
    target_year = target_dt.year
    target_month = target_dt.month
    target_day = target_dt.day
    
    import os
    from backend.config import MEDIA_EXTENSIONS
    
    valid_photos = []
    vault_root = config.VAULT_ROOT
    
    try:
        # We need to search the vault. Since the AI might ask for any date, 
        # we do a full walk but bail out early if we find enough photos.
        for root, _, files in os.walk(vault_root):
            for filename in files:
                file_path = Path(root) / filename
                if file_path.suffix.lower() in MEDIA_EXTENSIONS:
                    try:
                        stat = file_path.stat()
                        mtime_dt = datetime.fromtimestamp(stat.st_mtime)
                        
                        if mtime_dt.year == target_year and mtime_dt.month == target_month and mtime_dt.day == target_day:
                            valid_photos.append({
                                "path": str(file_path),
                                "name": filename,
                                "type": "photo"
                            })
                            
                            if len(valid_photos) >= 20:
                                return {"photos": valid_photos}
                    except Exception:
                        pass
        
        return {"photos": valid_photos}

    except Exception as e:
         return {"error": str(e)}


@router.get("/search-photos")
async def search_photos(query: str, limit: int = 5) -> Dict[str, Any]:
    """Zoek foto's op bestandsnaam die de query bevat (case-insensitive)."""
    if not config or not query.strip():
        return {"photos": []}

    query_lower = query.lower()
    results = []

    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.gif', '.bmp', '.webp',
                        '.tiff', '.tif', '.raw', '.cr2', '.nef', '.arw', '.dng', '.raf'}

    try:
        for root, dirs, files in os.walk(config.VAULT_ROOT):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for filename in files:
                ext = Path(filename).suffix.lower()
                if ext in IMAGE_EXTENSIONS and query_lower in filename.lower():
                    results.append({
                        "path": str(Path(root) / filename),
                        "name": filename
                    })
                    if len(results) >= limit:
                        return {"photos": results}
    except Exception:
        pass

    return {"photos": results}
