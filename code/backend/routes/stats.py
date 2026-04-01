from fastapi import APIRouter
from pathlib import Path
import shutil
from datetime import datetime
from typing import Dict, Any

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

@router.get("/photo-on-this-day")
async def get_photo_on_this_day() -> Dict[str, Any]:
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
        # To avoid always returning from the first folder (A-Z), gather all dirs and shuffle
        all_dirs = [x[0] for x in os.walk(vault_root)]
        random.shuffle(all_dirs)
        
        # Check up to 50 random directories looking for a match
        dirs_to_check = all_dirs[:50]
        
        for root in dirs_to_check:
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
