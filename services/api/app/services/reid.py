"""Vehicle re-identification: candidate matching for an unreadable plate.

See `app.schemas.reid` for the product argument (ranked suggestions an
operator reviews, never a silent auto-merge) and for why `similarity` is
null on every candidate in a real database today — no ReID model exists
anywhere in `ai-lab`.

## The candidate set

A query detection anchors a time window (`MAX_TIME_GAP` either side) and a
camera. Every other detection inside that window, at a *different* camera,
is a candidate — narrowed further by `app.services.correlator`'s own
physical-plausibility ceiling (`MAX_PLAUSIBLE_KMPH`, reused directly rather
than redefined) before anything about appearance is considered. That is
deliberate: bounding the candidate set by physics first is what keeps this
tractable without a vector index (see migration 0006's docstring) and
correct even in plausibility-only mode.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.schemas.reid import MatchFactor, ReidCandidate, ReidMatchResponse
from app.services.correlator import MAX_PLAUSIBLE_KMPH, haversine_m

log = get_logger(__name__)

#: How far either side of the query detection's timestamp a candidate may
#: fall. Narrower than the correlator's own `UNOBSERVED_GAP` (1 hour): that
#: constant tolerates a gap in an *already plate-anchored* journey, whereas
#: this search has no plate anchoring it at all, so a wide window would
#: produce an unmanageably large, mostly-irrelevant candidate set.
MAX_TIME_GAP = timedelta(minutes=30)

#: Below this cosine similarity, a candidate is dropped rather than ranked
#: low. Unverified against real embeddings — there are none to verify
#: against yet — chosen only as a conservative "clearly not the same
#: vehicle" floor. Must be recalibrated the day a real ReID model exists;
#: see BUILD_STATE.md's P11 section.
MIN_SIMILARITY = 0.6

#: Bounds the work done per request and the candidates fetched from the DB.
MAX_CANDIDATES_FETCHED = 200
MAX_CANDIDATES_RETURNED = 25

_DETECTION_WITH_CAMERA_SQL = """
    SELECT
        d.id, d.ts, d.camera_id, d.vehicle_type, d.plate_normalised, d.appearance_embedding,
        c.camera_code, c.name AS camera_name, c.corridor,
        ST_Y(c.location::geometry) AS lat, ST_X(c.location::geometry) AS lon
    FROM detections d
    JOIN cameras c ON c.id = d.camera_id
    WHERE d.id = :detection_id AND d.ts = :detection_ts
"""

_CANDIDATES_SQL = """
    SELECT
        d.id, d.ts, d.camera_id, d.vehicle_type, d.plate_normalised, d.appearance_embedding,
        c.camera_code, c.name AS camera_name, c.corridor,
        ST_Y(c.location::geometry) AS lat, ST_X(c.location::geometry) AS lon
    FROM detections d
    JOIN cameras c ON c.id = d.camera_id
    WHERE d.ts >= :since AND d.ts <= :until
      AND d.camera_id IS DISTINCT FROM :camera_id
      AND NOT (d.id = :detection_id AND d.ts = :detection_ts)
    ORDER BY d.ts
    LIMIT :limit
"""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity, no numpy — these vectors are always short
    enough (and the candidate set always small enough, per the module
    docstring) that pure Python is the honest choice, not a compromise."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _plausibility_factor(distance_m: float, elapsed_s: float, implied_kmph: float) -> MatchFactor:
    return MatchFactor(
        factor="spatiotemporal_plausibility",
        detail=(
            f"{distance_m / 1000.0:.1f} km in {elapsed_s / 60.0:.1f} min "
            f"implies {implied_kmph:.0f} km/h — within the correlator's own "
            f"{MAX_PLAUSIBLE_KMPH:.0f} km/h plausible-hop ceiling."
        ),
    )


async def _load_detection_with_camera(
    session: AsyncSession, detection_id: uuid.UUID, detection_ts: datetime
) -> Any:
    result = await session.execute(
        text(_DETECTION_WITH_CAMERA_SQL),
        {"detection_id": detection_id, "detection_ts": detection_ts},
    )
    return result.mappings().first()


async def _load_candidates(session: AsyncSession, query_row: Any) -> list[Any]:
    since = query_row["ts"] - MAX_TIME_GAP
    until = query_row["ts"] + MAX_TIME_GAP
    rows = await session.execute(
        text(_CANDIDATES_SQL),
        {
            "since": since,
            "until": until,
            "camera_id": query_row["camera_id"],
            "detection_id": query_row["id"],
            "detection_ts": query_row["ts"],
            "limit": MAX_CANDIDATES_FETCHED,
        },
    )
    return list(rows.mappings())


async def find_reid_matches(
    session: AsyncSession, detection_id: uuid.UUID, detection_ts: datetime
) -> ReidMatchResponse:
    query_row = await _load_detection_with_camera(session, detection_id, detection_ts)
    if query_row is None:
        return ReidMatchResponse(
            found=False,
            query_detection_id=detection_id,
            embedding_available=False,
            candidates=[],
            note="No detection with this id and timestamp.",
        )

    query_embedding = query_row["appearance_embedding"]
    candidate_rows = await _load_candidates(session, query_row)

    scored: list[ReidCandidate] = []
    used_embedding = False
    for row in candidate_rows:
        distance_m = haversine_m(query_row["lat"], query_row["lon"], row["lat"], row["lon"])
        elapsed_s = abs((row["ts"] - query_row["ts"]).total_seconds())
        if elapsed_s == 0:
            # Same instant at a different camera: not a physically possible
            # hop, and dividing by zero below would be meaningless anyway.
            continue
        implied_kmph = (distance_m / 1000.0) / (elapsed_s / 3600.0)
        if implied_kmph > MAX_PLAUSIBLE_KMPH:
            continue

        similarity: float | None = None
        if query_embedding is not None and row["appearance_embedding"] is not None:
            similarity = round(cosine_similarity(query_embedding, row["appearance_embedding"]), 3)
            if similarity < MIN_SIMILARITY:
                continue
            used_embedding = True

        plausibility_score = round(max(0.0, 1.0 - implied_kmph / MAX_PLAUSIBLE_KMPH), 3)

        factors = [_plausibility_factor(distance_m, elapsed_s, implied_kmph)]
        if row["vehicle_type"] and row["vehicle_type"] == query_row["vehicle_type"]:
            factors.append(
                MatchFactor(
                    factor="vehicle_type_match",
                    detail=f"Both read as {row['vehicle_type']}.",
                )
            )
        if similarity is not None:
            factors.append(
                MatchFactor(
                    factor="appearance_embedding",
                    detail=(
                        f"Cosine similarity {similarity:.2f} — uncalibrated, no real "
                        "embedding model exists yet (see BUILD_STATE.md's P11 section)."
                    ),
                )
            )

        scored.append(
            ReidCandidate(
                detection_id=row["id"],
                detection_ts=row["ts"],
                camera_code=row["camera_code"],
                camera_name=row["camera_name"],
                corridor=row["corridor"],
                vehicle_type=row["vehicle_type"],
                plate=row["plate_normalised"],
                distance_km=round(distance_m / 1000.0, 3),
                elapsed_s=round(elapsed_s, 1),
                implied_kmph=round(implied_kmph, 1),
                similarity=similarity,
                plausibility_score=plausibility_score,
                matched_on=factors,
            )
        )

    scored.sort(key=lambda c: (c.similarity is None, -(c.similarity or 0.0), -c.plausibility_score))
    top = scored[:MAX_CANDIDATES_RETURNED]

    if used_embedding:
        note = (
            f"{len(top)} candidate(s), ranked by appearance similarity where available, "
            "spatiotemporal plausibility otherwise."
        )
    elif query_embedding is None:
        note = (
            "This detection has no appearance embedding — nothing in ai-lab computes one "
            "yet. Candidates below are ranked by spatiotemporal plausibility only, not "
            "appearance; treat them as leads, not matches."
        )
    else:
        note = (
            "No candidate in the time window had an appearance embedding either. "
            "Candidates below are ranked by spatiotemporal plausibility only."
        )

    return ReidMatchResponse(
        found=True,
        query_detection_id=detection_id,
        query_ts=query_row["ts"],
        query_camera_code=query_row["camera_code"],
        embedding_available=used_embedding,
        candidates=top,
        note=note,
    )
