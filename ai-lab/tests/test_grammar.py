"""Indian plate grammar."""

from __future__ import annotations

import pytest

from ailab.aggregate import grammar


class TestNormalise:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("gj 03 ab 1234", "GJ03AB1234"),
            ("GJ-03-AB-1234", "GJ03AB1234"),
            ("IND GJ03AB1234", "GJ03AB1234"),
            ("  gj03ab1234  ", "GJ03AB1234"),
            ("GJ03AB1234", "GJ03AB1234"),
            ("", ""),
        ],
    )
    def test_normalise(self, raw: str, expected: str) -> None:
        assert grammar.normalise(raw) == expected


class TestValidate:
    @pytest.mark.parametrize(
        "plate",
        ["GJ03AB1234", "GJ1AB1234", "MH12DE1433", "GJ18A1234", "DL8CAF5031"],
    )
    def test_accepts_standard(self, plate: str) -> None:
        assert grammar.validate(plate).valid, plate

    def test_accepts_bh_series(self) -> None:
        check = grammar.validate("22BH1234AB")
        assert check.valid and check.fmt == "bh_series"

    @pytest.mark.parametrize(
        "plate,reason",
        [
            ("", "empty"),
            ("GJ03", "too short"),
            ("GJ03AB123456789", "too long"),
            ("XX03AB1234", "unknown state"),
            ("1234567890", "no known format"),
        ],
    )
    def test_rejects(self, plate: str, reason: str) -> None:
        assert not grammar.validate(plate).valid, f"{plate} ({reason})"

    def test_rejects_out_of_range_gujarat_rto(self) -> None:
        # Gujarat runs GJ-01 to GJ-39; GJ-77 is well-formed but not real.
        check = grammar.validate("GJ77AB1234")
        assert not check.valid
        assert "GJ-01" in check.note


class TestCoerce:
    def test_repairs_digit_in_letter_slot(self) -> None:
        # Slot 5 must be a letter, so '8' there is a misread 'B'.
        corrected, template, changed = grammar.coerce("GJ03A81234")
        assert corrected == "GJ03AB1234"
        assert template == "AANNAANNNN"
        assert changed == [5]

    def test_repairs_letter_in_digit_slot(self) -> None:
        # Trailing four slots are digits, so 'O' there is a misread '0'.
        corrected, _, changed = grammar.coerce("GJ03AB123O")
        assert corrected == "GJ03AB1230"
        assert changed == [9]

    def test_correction_is_position_aware(self) -> None:
        """The same character is corrected in one slot and left alone in another.

        This is the property that makes correction safe: a global 0→O rule would
        destroy the digits in 'GJ03AB1034'.
        """
        corrected, _, _ = grammar.coerce("GJ0EAB1034")
        # Slot 3 is a digit slot: 'E' → '8'. The '0' at slot 7 is already
        # correct for its digit slot and must survive.
        assert corrected == "GJ08AB1034"

    def test_leaves_unfittable_strings_alone(self) -> None:
        # Nothing plate-shaped is 5 characters long, so inventing a correction
        # would manufacture a plate that was never seen.
        assert grammar.coerce("ABC12")[0] == "ABC12"

    def test_valid_plate_is_untouched(self) -> None:
        corrected, _, changed = grammar.coerce("GJ03AB1234")
        assert corrected == "GJ03AB1234"
        assert changed == []


class TestConfusable:
    @pytest.mark.parametrize("a,b", [("0", "O"), ("8", "B"), ("1", "I"), ("5", "S"), ("A", "A")])
    def test_confusable_pairs(self, a: str, b: str) -> None:
        assert grammar.confusable(a, b)

    @pytest.mark.parametrize("a,b", [("A", "X"), ("3", "7"), ("M", "W")])
    def test_distinct_characters(self, a: str, b: str) -> None:
        assert not grammar.confusable(a, b)


def test_describe_splits_a_plate() -> None:
    parts = grammar.describe_plate("GJ03AB1234")
    assert parts["state"] == "GJ"
    assert parts["rto"] == "03"
    assert parts["series"] == "AB"
    assert parts["number"] == "1234"
