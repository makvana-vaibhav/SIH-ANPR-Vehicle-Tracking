"""Multi-frame plate consensus.

A vehicle crossing a camera's field of view yields ten to sixty plate crops of
wildly varying quality. Taking the single highest-confidence read is the obvious
approach and it is measurably worse than voting, because OCR confidence is
poorly calibrated: a sharp crop read wrongly with 0.95 confidence beats three
correct reads at 0.7 every time.

So this votes per character position, weighting each read by
`ocr_confidence x crop_quality`. Given

    GJ03AB1234  0.92    GJ03AB1234  0.95
    GJ03AB1284  0.61    GJ03AB1234  0.94

position 8 sees `3` three times at high weight and `8` once at low weight, and
the consensus is `GJ03AB1234` with position 8 marked as the least certain
character. Every read is retained in the output, including the outlier — the
disagreement is the evidence that the plate detector saw a difficult plate, and
that crop is exactly what a future fine-tune should train on.

Nothing here is discarded silently. Reads below the weight floor still appear in
`plate_reads.csv`; they merely do not vote.
"""

from __future__ import annotations

from collections import defaultdict

from ailab.aggregate import grammar
from ailab.config import ConsensusConfig
from ailab.types import PlateCandidate, PlateConsensus, PlateRead


def consensus(reads: list[PlateRead], config: ConsensusConfig) -> PlateConsensus:
    """Reduce every read of one vehicle to a single answer plus its evidence."""
    usable = [r for r in reads if r.text]
    if not usable:
        return PlateConsensus(
            text="", confidence=0.0, method="none",
            reads_total=len(reads), reads_agreeing=0,
            grammar_note="no readable plate text on any frame",
        )

    if len(usable) < config.min_reads:
        return PlateConsensus(
            text="", confidence=0.0, method="none",
            reads_total=len(usable), reads_agreeing=0,
            grammar_note=f"only {len(usable)} read(s), minimum is {config.min_reads}",
        )

    # ── Weight floor: keep everything, but let only credible reads vote ──
    max_weight = max(r.vote_weight for r in usable) or 1.0
    voters = [r for r in usable if r.vote_weight >= config.relative_weight_floor * max_weight]
    if not voters:
        voters = usable

    # ── Candidate tally over exact strings ──
    by_text: dict[str, float] = defaultdict(float)
    support: dict[str, int] = defaultdict(int)
    for read in voters:
        by_text[read.text] += read.vote_weight
        support[read.text] += 1

    total_weight = sum(by_text.values()) or 1.0
    candidates = sorted(
        (
            PlateCandidate(
                text=text,
                score=weight / total_weight,
                support=support[text],
                grammar_valid=grammar.validate(text).valid,
            )
            for text, weight in by_text.items()
        ),
        key=lambda c: -c.score,
    )

    # ── Character-position vote, among reads of the dominant length ──
    length_weight: dict[int, float] = defaultdict(float)
    for read in voters:
        length_weight[len(read.text)] += read.vote_weight
    modal_length = max(length_weight.items(), key=lambda kv: kv[1])[0]
    aligned = [r for r in voters if len(r.text) == modal_length]

    # `vote_shares` is how much the frames agreed, per position; it is tracked
    # separately from `char_confidences` (what the engine claimed) because a
    # lone read agrees with itself completely and must not be scored as though
    # it had lost a vote.
    if len(aligned) > 1:
        text, vote_shares = _vote_per_character(aligned, modal_length)
        char_confidences = list(vote_shares)
        method = "char_vote"
    else:
        best = max(voters, key=lambda r: r.vote_weight)
        text = best.text
        char_confidences = (
            list(best.char_confidences)
            if len(best.char_confidences) == len(text)
            else [best.ocr_confidence] * len(text)
        )
        vote_shares = [1.0] * len(text)
        method = "single_read"

    # ── Position-aware repair ──
    corrected_from: str | None = None
    if config.apply_confusion_correction:
        repaired, _template, changed = grammar.coerce(text)
        if repaired != text:
            corrected_from = text
            # A repaired character is less certain than one the models agreed
            # on; reflect that rather than inheriting the original confidence.
            for position in changed:
                if position < len(char_confidences):
                    char_confidences[position] *= 0.85
                if position < len(vote_shares):
                    vote_shares[position] *= 0.85
            text = repaired

    check = grammar.validate(text)

    # ── Grammar gate: prefer a valid runner-up when asked to ──
    if config.require_grammar and not check.valid:
        valid_alternative = next((c for c in candidates if c.grammar_valid), None)
        if valid_alternative:
            text = valid_alternative.text
            check = grammar.validate(text)
            method = "grammar_fallback"
            char_confidences = [valid_alternative.score] * len(text)
            vote_shares = [valid_alternative.score] * len(text)
            corrected_from = None

    # ── Confidence and uncertainty ──
    agreeing = [r for r in usable if r.text == text or grammar.normalise(r.text) == text]
    evidence = (
        sum(r.ocr_confidence * r.vote_weight for r in agreeing)
        / (sum(r.vote_weight for r in agreeing) or 1.0)
        if agreeing
        else max(r.ocr_confidence for r in voters)
    )
    vote_agreement = sum(vote_shares) / len(vote_shares) if vote_shares else 0.0
    # Evidence quality scaled by how unanimous the vote was. A single read gets
    # vote_agreement = 1.0 and so is scored purely on its own OCR confidence.
    confidence = min(1.0, evidence * (0.5 + 0.5 * vote_agreement))

    top = candidates[0].score if candidates else 0.0
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    ambiguous = bool(
        len(candidates) > 1
        and (top - runner_up) < config.ambiguity_margin
        # Two candidates that differ only by a classic OCR confusion are one
        # difficult plate, not two rival answers.
        and not _only_confusable_difference(candidates[0].text, candidates[1].text)
    )

    return PlateConsensus(
        text=text,
        confidence=round(float(confidence), 4),
        method=method,
        reads_total=len(usable),
        reads_agreeing=len(agreeing),
        candidates=candidates[:5],
        char_confidences=[round(float(c), 4) for c in char_confidences],
        grammar_valid=check.valid,
        grammar_note=check.note,
        corrected_from=corrected_from,
        ambiguous=ambiguous,
        disagreement=round(1.0 - top, 4),
    )


def _vote_per_character(reads: list[PlateRead], length: int) -> tuple[str, list[float]]:
    """Weighted plurality vote at each character position.

    The returned per-character confidence is the winning character's share of
    the weight at that position — a direct, inspectable measure of how much the
    frames agreed, position by position. It is the number that tells an operator
    "character 8 is the one to double-check".
    """
    chars: list[str] = []
    shares: list[float] = []

    for position in range(length):
        weights: dict[str, float] = defaultdict(float)
        for read in reads:
            char = read.text[position]
            # Prefer a genuine per-character confidence when the engine gives
            # one; most do not, so fall back to the read-level weight.
            if len(read.char_confidences) == len(read.text):
                weights[char] += read.char_confidences[position] * read.quality.weight()
            else:
                weights[char] += read.vote_weight

        total = sum(weights.values()) or 1.0
        winner, winning_weight = max(weights.items(), key=lambda kv: kv[1])
        chars.append(winner)
        shares.append(winning_weight / total)

    return "".join(chars), shares


def _only_confusable_difference(a: str, b: str) -> bool:
    """True when two candidates differ only in characters that look alike."""
    if len(a) != len(b) or a == b:
        return False
    differences = [(x, y) for x, y in zip(a, b, strict=False) if x != y]
    if not differences or len(differences) > 2:
        return False
    return all(grammar.confusable(x, y) for x, y in differences)
