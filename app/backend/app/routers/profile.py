"""Account profile API: age, sex, height, weight, activity level.

Needed for DRI life-stage lookup (age/sex) and the sex-based water goal
default — see nutrition-diary-design.md. Saving a profile re-runs DRI
seeding (non-custom targets only, see nutrition_targets.seed_dri_targets)
so target defaults stay in sync with the user's current profile without
ever overwriting a target the user explicitly customized."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ..routers.auth import get_current_user
from ..db.profile import query as profile_query
from ..nutrition_targets import seed_dri_targets
from ..routers.water import default_water_target_ml

router = APIRouter()

VALID_ACTIVITY_LEVELS = {"sedentary", "light", "moderate", "active", "very_active"}


class ProfileRequest(BaseModel):
    age: int
    sex: str
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    activity_level: Optional[str] = None
    # Explicit water target override; if omitted, the sex-based default is
    # computed and stored so /water/log always has a concrete value to
    # read rather than resolving the default at read-time indefinitely.
    water_target_ml: Optional[float] = None

    @field_validator("sex")
    @classmethod
    def _validate_sex(cls, v: str) -> str:
        if v.lower() not in ("male", "female"):
            raise ValueError("sex must be 'male' or 'female' (matches DRI table convention)")
        return v.lower()

    @field_validator("activity_level")
    @classmethod
    def _validate_activity(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_ACTIVITY_LEVELS:
            raise ValueError(f"activity_level must be one of {sorted(VALID_ACTIVITY_LEVELS)}")
        return v

    @field_validator("age")
    @classmethod
    def _validate_age(cls, v: int) -> int:
        if v < 0 or v > 130:
            raise ValueError("age must be between 0 and 130")
        return v


@router.put("")
async def set_profile(req: ProfileRequest, user_id: int = Depends(get_current_user)):
    water_target = req.water_target_ml if req.water_target_ml is not None else default_water_target_ml(req.sex)

    await profile_query.upsert_profile(
        user_id, req.age, req.sex, req.height_cm, req.weight_kg, req.activity_level, water_target,
    )

    seeded_count = await seed_dri_targets(user_id, req.sex, req.age)
    return {"status": "saved", "water_target_ml": water_target, "dri_targets_seeded": seeded_count}


@router.get("")
async def get_profile(user_id: int = Depends(get_current_user)):
    row = await profile_query.get_profile(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No profile set yet")
    return {
        "age": row["age"],
        "sex": row["sex"],
        "height_cm": row["height_cm"],
        "weight_kg": row["weight_kg"],
        "activity_level": row["activity_level"],
        "water_target_ml": row["water_target_ml"],
        "updated_at": row["updated_at"].isoformat(),
    }
