"""
DataVault Dashboard - Thumbnail Generator
Generate and cache thumbnails for photos and videos
"""
import hashlib
from pathlib import Path
from typing import Optional
from PIL import Image
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False

from backend.config import THUMB_SIZE, THUMB_QUALITY

def get_thumbnail_cache_path(file_path: Path, thumb_cache_dir: Path) -> Path:
    """
    Get cache path for thumbnail.
    Uses hash of file path to avoid name conflicts.
    All thumbnails are saved as JPEG.
    """
    # Create hash of file path
    path_hash = hashlib.md5(str(file_path).encode()).hexdigest()
    
    # ALL thumbnails are JPEG
    cache_filename = f"{path_hash}.jpg"
    return thumb_cache_dir / cache_filename

def is_thumbnail_valid(thumb_path: Path, original_path: Path) -> bool:
    """
    Check if cached thumbnail is still valid.
    Valid if it exists and is newer than original file.
    """
    if not thumb_path.exists():
        return False
    
    if not original_path.exists():
        return False
    
    thumb_mtime = thumb_path.stat().st_mtime
    original_mtime = original_path.stat().st_mtime
    
    return thumb_mtime >= original_mtime

def generate_photo_thumbnail(file_path: Path, output_path: Path) -> bool:
    """
    Generate thumbnail for photo.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        with Image.open(file_path) as img:
            # Convert RGBA to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                # Create white background
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Create thumbnail
            img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
            
            # Save
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, 'JPEG', quality=THUMB_QUALITY, optimize=True)
            
            return True
    
    except Exception as e:
        print(f"Error generating photo thumbnail for {file_path}: {e}")
        return False

def generate_video_thumbnail(file_path: Path, output_path: Path) -> bool:
    """
    Generate thumbnail for video using ffmpeg if available.
    
    Returns:
        True if successful, False if ffmpeg not available
    """
    import subprocess
    import shutil
    
    # Check if ffmpeg is available
    if not shutil.which('ffmpeg'):
        return False
    
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use absolute path to ffmpeg for reliability
        ffmpeg_path = shutil.which('ffmpeg') or '/usr/local/bin/ffmpeg'
        
        # Extract frame at 1 second (or first frame if shorter)
        cmd = [
            ffmpeg_path,
            '-i', str(file_path),
            '-ss', '00:00:01',
            '-vframes', '1',
            '-vf', f'scale={THUMB_SIZE[0]}:{THUMB_SIZE[1]}:force_original_aspect_ratio=decrease',
            '-y',  # Overwrite
            str(output_path)
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )

        if result.returncode != 0:
            pass
        
        # Return True if ffmpeg succeeded and output file exists
        return result.returncode == 0 and output_path.exists()
    
    except Exception as e:
        print(f"Error generating video thumbnail for {file_path}: {e}")
        return False

def create_placeholder_thumbnail(output_path: Path, is_video: bool = False) -> bool:
    """
    Create a placeholder thumbnail.
    """
    try:
        # Create simple colored placeholder
        if is_video:
            color = (100, 100, 150)
        else:
            color = (150, 150, 150)
            
        img = Image.new('RGB', THUMB_SIZE, color)
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, 'JPEG', quality=THUMB_QUALITY)
        
        return True
    except Exception as e:
        print(f"Error creating placeholder: {e}")
        return False

def get_or_create_thumbnail(
    file_path: Path,
    thumb_cache_dir: Path,
    is_video: bool = False,
    is_audio: bool = False
) -> Optional[Path]:
    """
    Get cached thumbnail or create new one.
    
    Args:
        file_path: Path to original media file
        thumb_cache_dir: Directory for thumbnail cache
        is_video: True if file is a video
        is_audio: True if file is audio
    
    Returns:
        Path to thumbnail file, or None if failed
    """
    # Get cache path
    cache_path = get_thumbnail_cache_path(file_path, thumb_cache_dir)
    
    # Check if valid cached version exists
    if is_thumbnail_valid(cache_path, file_path):
        return cache_path
    
    # Generate new thumbnail
    success = False
    
    if is_audio:
        # Instantly create a placeholder for audio files
        success = create_placeholder_thumbnail(cache_path, is_video=False)
    elif is_video:
        success = generate_video_thumbnail(file_path, cache_path)

        # Fallback to placeholder if ffmpeg not available
        if not success:
            success = create_placeholder_thumbnail(cache_path, is_video=True)
    else:
        # Photo thumbnail
        success = generate_photo_thumbnail(file_path, cache_path)
        
        # Fallback to placeholder
        if not success:
            success = create_placeholder_thumbnail(cache_path, is_video=False)
    
    if success and cache_path.exists():
        return cache_path
    
    return None
