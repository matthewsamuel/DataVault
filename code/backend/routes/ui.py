"""
Choices about how the page itself looks and feels, kept with the other app settings
(app_settings in the database, so they survive a restart and a new browser).

GET/PUT /api/ui/settings
    motion: 'volledig' | 'rustig' | 'uit', or null when the user never chose
            ('Beweging' in Instellingen; null means: Volledig, unless macOS asks for less
            motion). The page keeps a copy in localStorage so the first paint already
            uses the right mode; see frontend/motion.js and docs/MOTION.md.
"""
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()

SETTING_MOTION = "ui_motion"
MOTION_MODES = ("volledig", "rustig", "uit")

config = None
db = None


def init_router(cfg, database):
    global config, db
    config = cfg
    db = database


def _motion() -> Any:
    value = db.get_setting(SETTING_MOTION, None)
    return value if value in MOTION_MODES else None


@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    return {"motion": _motion()}


@router.put("/settings")
def put_settings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if "motion" in payload:
        value = payload["motion"]
        if value is not None and value not in MOTION_MODES:
            raise HTTPException(status_code=400, detail="Beweging moet volledig, rustig of uit zijn")
        db.set_setting(SETTING_MOTION, value)
    return {"motion": _motion()}
