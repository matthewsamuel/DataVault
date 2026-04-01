"""
Progress tracking for long-running operations
"""
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ScanProgress:
    """Track progress of duplicate scan operation"""
    status: str  # 'idle', 'indexing', 'scanning', 'complete', 'error', 'cancelled'
    phase: str  # Description of current phase
    total_files: int = 0
    processed_files: int = 0
    duplicates_found: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "status": self.status,
            "phase": self.phase,
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "duplicates_found": self.duplicates_found,
            "progress_percent": int((self.processed_files / self.total_files * 100) if self.total_files > 0 else 0),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message
        }

# Global progress tracker (single instance for now)
_current_scan_progress: Optional[ScanProgress] = None
_scan_cancelled: bool = False

def get_scan_progress() -> Optional[ScanProgress]:
    """Get current scan progress"""
    return _current_scan_progress

def set_scan_progress(progress: Optional[ScanProgress]) -> None:
    """Set current scan progress"""
    global _current_scan_progress
    _current_scan_progress = progress

def update_scan_progress(**kwargs) -> None:
    """Update fields of current scan progress"""
    global _current_scan_progress
    if _current_scan_progress:
        for key, value in kwargs.items():
            if hasattr(_current_scan_progress, key):
                setattr(_current_scan_progress, key, value)

def is_scan_cancelled() -> bool:
    """Check if scan has been cancelled"""
    return _scan_cancelled

def cancel_scan() -> None:
    """Cancel the current scan"""
    global _scan_cancelled
    _scan_cancelled = True

def reset_scan_cancellation() -> None:
    """Reset the cancellation flag"""
    global _scan_cancelled
    _scan_cancelled = False

