import csv
from pathlib import Path
from fastapi import APIRouter, Depends
from ..routers.auth import get_current_user

router = APIRouter()

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def user_data_dir(user_id: int) -> Path:
    d = BACKEND_ROOT / "app_data" / f"user_{user_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/bmr")
async def get_bmr(user_id: int = Depends(get_current_user)):
    """Get current calculated BMR."""
    csv_path = user_data_dir(user_id) / "tdee_tracking_log.csv"
    if not csv_path.exists():
        return {"bmr": None, "message": "No data yet. Run a sync first."}

    import sys
    sys.path.insert(0, str(BACKEND_ROOT))
    from tdee import calculate_bmr

    bmr = calculate_bmr(str(csv_path))
    return {"bmr": bmr if isinstance(bmr, (int, float)) else None, "message": str(bmr)}


@router.get("/tdee-log")
async def get_tdee_log(user_id: int = Depends(get_current_user)):
    """Get TDEE tracking log as JSON."""
    csv_path = user_data_dir(user_id) / "tdee_tracking_log.csv"
    if not csv_path.exists():
        return {"entries": []}

    with open(csv_path) as f:
        entries = list(csv.DictReader(f))
    return {"entries": entries}


@router.get("/workouts")
async def get_workouts(user_id: int = Depends(get_current_user), limit: int = 20):
    """Get recent Hevy workouts."""
    csv_path = user_data_dir(user_id) / "hevy_workouts.csv"
    if not csv_path.exists():
        return {"workouts": []}

    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    return {"workouts": rows[-limit:]}
