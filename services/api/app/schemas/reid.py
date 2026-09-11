"""Vehicle re-identification (P11) — the response contract.

## What this is, and what it is not

The PS goal is a vehicle linking across cameras even when its plate is
unreadable. The honest way to build the half of that this session can
verify: rank *candidate* detections at other cameras, within a physically
plausible time/distance window of one query detection, and let an operator
decide — never silently fold a candidate into a journey. Auto-merging
identity on an unverified signal is exactly the kind of fabricated
confidence CLAUDE.md's coding conventions forbid; a ranked suggestion list
an operator reviews is not.

**`similarity` is null on every candidate today.** No ReID model exists
anywhere in `ai-lab` — nothing computes `Detection.appearance_embedding`, so
it is `NULL` on every row in a real database right now. When it is null on
*either* the query detection or a candidate, ranking falls back to
`plausibility_score` alone: how physically plausible the hop is, purely
from implied speed between the two cameras. That fallback is real and
useful today (it is exactly the same physics `app/services/correlator.py`
already uses to flag implausible journey legs), but it is **not**
re-identification — `embedding_available` on the response says plainly
which mode produced the candidates, and nothing here should be presented to
a user as "matched by appearance" when it was actually "matched by being
physically reachable in time."
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MatchFactor(BaseModel):
    """Why one candidate ranked where it did. Same `{factor, detail}` shape
    as `app.services.anomaly.Reason` and `app.schemas.predictions.
    ContributingFactor` — kept as its own copy rather than a shared import so
    this phase's schema does not couple to P5's or P10's."""

    factor: str
    detail: str


class ReidCandidate(BaseModel):
    detection_id: uuid.UUID
    detection_ts: datetime
    camera_code: str
    camera_name: str
    corridor: str | None = None
    vehicle_type: str | None = None
    #: Surfaced in case this candidate itself has a readable plate — a real
    #: find that ends the need for appearance matching entirely.
    plate: str | None = None
    distance_km: float
    elapsed_s: float
    implied_kmph: float
    similarity: float | None = Field(
        default=None,
        description=(
            "Cosine similarity of appearance embeddings, 0-1. Null unless both "
            "the query detection and this candidate have one — which, today, is never."
        ),
    )
    plausibility_score: float = Field(
        description=(
            "1 - (implied_kmph / 150). A plain, bounded, deterministic ratio — "
            "not a probability, not a model output. Falls off linearly from 1.0 "
            "(implied crawl speed) to 0.0 (at the correlator's own implausible-leg "
            "ceiling)."
        )
    )
    matched_on: list[MatchFactor]


class ReidMatchResponse(BaseModel):
    found: bool = Field(description="False when the query detection itself does not exist.")
    query_detection_id: uuid.UUID
    query_ts: datetime | None = None
    query_camera_code: str | None = None
    embedding_available: bool = Field(
        description=(
            "True only when the query detection has an appearance_embedding and "
            "at least one candidate does too, so real cosine-similarity ranking "
            "happened. False means every candidate below is plausibility-only."
        )
    )
    candidates: list[ReidCandidate]
    note: str
