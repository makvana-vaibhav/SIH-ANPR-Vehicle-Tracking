"""Indian licence-plate grammar.

Grammar is what turns a generic text recogniser into an ANPR system. `GJ03A81234`
is not a plate — position 5 of that format must be a letter — and knowing that
lets us repair the read instead of discarding the vehicle. The correction is
*position-aware*: `8`→`B` is right in a letter slot and catastrophic in a digit
slot, so a global find-and-replace would introduce as many errors as it fixes.

Formats accepted
----------------
standard   SS DD L{1,3} NNNN     GJ 03 AB 1234   — the overwhelming majority
bh_series  DD BH NNNN L{1,2}     22 BH 1234 AB   — since 2021, non-transferable
legacy     SSS NNNN              rare pre-1990s residual

Invalid strings are flagged, never dropped. A plate the grammar rejects is still
reported with `grammar_valid=false`, because a genuine misread is evidence about
the model and a genuinely unusual plate (military, diplomatic, a damaged plate)
is evidence about the road.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Format patterns ──────────────────────────────────────────────────
STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$")
BH_SERIES = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")
LEGACY = re.compile(r"^[A-Z]{3}[0-9]{4}$")

# Every current state/UT registration prefix. A read starting with something
# else is almost certainly an OCR error on the first two characters.
STATE_CODES = frozenset({
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ",
    "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP",
    "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "UK",
    "UP", "WB", "UA", "BH",
})

# Gujarat RTO districts run GJ-01 to GJ-39. Sentinel-GJ is a Gujarat system, so
# a GJ plate with an out-of-range district code is worth flagging even though
# the string is otherwise well-formed.
GUJARAT_RTO_MAX = 39

# ── Confusion pairs ──────────────────────────────────────────────────
# Applied only in the direction the slot demands.
LETTER_TO_DIGIT = {
    "O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "J": "3",
    "A": "4", "S": "5", "G": "6", "T": "7", "B": "8", "E": "8", "P": "9",
}
DIGIT_TO_LETTER = {
    "0": "O", "1": "I", "2": "Z", "3": "J", "4": "A", "5": "S",
    "6": "G", "7": "T", "8": "B", "9": "P",
}

# Glyph pairs that look alike within the same class, so no slot rule can
# separate them. Used only to judge whether two readings disagree — never to
# rewrite a character, because there is no principled direction to rewrite in.
VISUALLY_SIMILAR = frozenset(
    frozenset(pair)
    for pair in (
        ("3", "8"), ("0", "8"), ("6", "8"), ("5", "6"), ("1", "7"), ("2", "7"),
        ("O", "Q"), ("O", "D"), ("B", "R"), ("C", "G"), ("M", "N"), ("U", "V"),
        ("K", "X"), ("E", "F"), ("P", "R"), ("I", "J"),
    )
)

# Recognisers frequently emit the country marker embossed on newer plates, and
# separators that carry no information.
_NOISE_PREFIX = re.compile(r"^(IND|IIND|1ND|INO)")
_NON_ALNUM = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True, slots=True)
class GrammarCheck:
    valid: bool
    fmt: str          # "standard" | "bh_series" | "legacy" | "invalid"
    note: str


def normalise(text: str) -> str:
    """Uppercase, strip separators and the embossed IND marker."""
    if not text:
        return ""
    cleaned = _NON_ALNUM.sub("", text.upper())
    cleaned = _NOISE_PREFIX.sub("", cleaned)
    return cleaned


def validate(plate: str) -> GrammarCheck:
    """Classify a normalised plate string."""
    if not plate:
        return GrammarCheck(False, "invalid", "empty")
    if len(plate) < 6:
        return GrammarCheck(False, "invalid", f"too short ({len(plate)} chars)")
    if len(plate) > 11:
        return GrammarCheck(False, "invalid", f"too long ({len(plate)} chars)")

    if BH_SERIES.match(plate):
        return GrammarCheck(True, "bh_series", "")

    if STANDARD.match(plate):
        state = plate[:2]
        if state not in STATE_CODES:
            return GrammarCheck(False, "standard", f"unknown state code '{state}'")
        if state == "GJ":
            district = int(re.match(r"^GJ([0-9]{1,2})", plate).group(1))  # type: ignore[union-attr]
            if district < 1 or district > GUJARAT_RTO_MAX:
                return GrammarCheck(
                    False, "standard", f"GJ district {district:02d} outside GJ-01..GJ-{GUJARAT_RTO_MAX}"
                )
        return GrammarCheck(True, "standard", "")

    if LEGACY.match(plate):
        return GrammarCheck(True, "legacy", "legacy 3-letter format")

    return GrammarCheck(False, "invalid", "matches no known Indian plate format")


def slot_templates(length: int) -> list[str]:
    """Every plausible letter/digit layout for a plate of this length.

    'A' means the slot must be a letter, 'N' a digit. A 10-character string
    could be GJ-03-AB-1234 (AANNAANNNN) or GJ-3-ABC-1234 (AANAAANNNN); both are
    generated and the one that best fits the observed characters wins.
    """
    templates: list[str] = []

    # standard: 2 letters + 1-2 digits + 1-3 letters + 4 digits
    for district in (1, 2):
        for series in (1, 2, 3):
            if 2 + district + series + 4 == length:
                templates.append("AA" + "N" * district + "A" * series + "NNNN")

    # bh_series: 2 digits + BH + 4 digits + 1-2 letters
    for suffix in (1, 2):
        if 2 + 2 + 4 + suffix == length:
            templates.append("NN" + "AA" + "NNNN" + "A" * suffix)

    if length == 7:
        templates.append("AAANNNN")

    return templates


def _fit_score(text: str, template: str) -> int:
    """How many characters already sit in the right kind of slot."""
    return sum(
        1
        for char, kind in zip(text, template, strict=False)
        if (kind == "A" and char.isalpha()) or (kind == "N" and char.isdigit())
    )


def coerce(text: str) -> tuple[str, str, list[int]]:
    """Repair a near-miss using position-aware confusion correction.

    Returns (corrected, template_used, corrected_positions). When no template
    fits the length, the text is returned untouched — inventing a correction for
    a string that is not plate-shaped would manufacture a false plate.
    """
    plate = normalise(text)
    if not plate:
        return plate, "", []

    templates = slot_templates(len(plate))
    if not templates:
        return plate, "", []

    # Try every template and prefer the one that produces a genuinely valid
    # plate. Picking purely by how many characters already fit would choose
    # GJ-0-EAB-1034 over GJ-08-AB-1034 — the first leaves more characters
    # untouched, but GJ-0 is not a real RTO code and the second is.
    best: tuple[tuple[int, int, int], str, str, list[int]] | None = None
    for template in templates:
        corrected, changed = _apply_template(plate, template)
        score = (
            1 if validate(corrected).valid else 0,
            _fit_score(plate, template),
            -len(changed),
        )
        if best is None or score > best[0]:
            best = (score, corrected, template, changed)

    assert best is not None
    _, corrected, template, changed = best
    return corrected, template, changed


def _apply_template(plate: str, template: str) -> tuple[str, list[int]]:
    """Coerce each character into the kind of value its slot requires."""
    chars = list(plate)
    changed: list[int] = []
    for i, (char, kind) in enumerate(zip(chars, template, strict=False)):
        if kind == "A" and char.isdigit():
            replacement = DIGIT_TO_LETTER.get(char)
            if replacement:
                chars[i] = replacement
                changed.append(i)
        elif kind == "N" and char.isalpha():
            replacement = LETTER_TO_DIGIT.get(char)
            if replacement:
                chars[i] = replacement
                changed.append(i)
    return "".join(chars), changed


def confusable(a: str, b: str) -> bool:
    """Could these two characters be the same glyph misread?

    Broader than the correction maps above, and deliberately so. Those maps are
    used to *change* characters, so they only cross the letter/digit boundary
    where a slot dictates the answer. This is used to decide whether two
    competing readings are a real disagreement or one difficult plate, and there
    the common same-class confusions matter just as much: 3/8 and O/Q are the
    two most frequent plate misreads and neither crosses that boundary.
    """
    if a == b:
        return True
    if (
        LETTER_TO_DIGIT.get(a) == b or DIGIT_TO_LETTER.get(a) == b
        or LETTER_TO_DIGIT.get(b) == a or DIGIT_TO_LETTER.get(b) == a
    ):
        return True
    return frozenset((a, b)) in VISUALLY_SIMILAR


def describe_plate(plate: str) -> dict[str, str]:
    """Break a valid plate into its parts, for display and for search."""
    check = validate(plate)
    if not check.valid:
        return {"format": check.fmt, "note": check.note}
    if check.fmt == "standard":
        m = re.match(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{1,3})([0-9]{4})$", plate)
        if m:
            return {
                "format": "standard",
                "state": m.group(1),
                "rto": m.group(2),
                "series": m.group(3),
                "number": m.group(4),
            }
    if check.fmt == "bh_series":
        m = re.match(r"^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$", plate)
        if m:
            return {
                "format": "bh_series",
                "year": m.group(1),
                "series": m.group(2),
                "number": m.group(3),
                "suffix": m.group(4),
            }
    return {"format": check.fmt}
