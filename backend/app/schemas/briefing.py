"""Inputs for the real briefing tools; analysis is always an existing observation run."""

from pydantic import BaseModel

from app.schemas.display import DailyBriefing


class GenerateBriefingInput(BaseModel):
    run_id: str


class GenerateBriefingOutput(BaseModel):
    status: str = "succeeded"
    briefing: DailyBriefing


class ReviewBriefingInput(BaseModel):
    briefing: DailyBriefing


class ReviewBriefingOutput(BaseModel):
    status: str = "succeeded"
    briefing: DailyBriefing
    quality: dict


class SaveBriefingInput(ReviewBriefingInput):
    supersedes_id: int | None = None


class SaveBriefingOutput(BaseModel):
    status: str = "succeeded"
    report_id: int
