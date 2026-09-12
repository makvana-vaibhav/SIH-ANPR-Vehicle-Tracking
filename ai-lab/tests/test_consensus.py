"""Multi-frame consensus."""

from __future__ import annotations

from ailab.aggregate.consensus import consensus, is_confirmed
from ailab.config import ConsensusConfig


def test_the_scenario_from_the_brief(make_read) -> None:
    """The exact case the requirements describe.

        GJ03AB1234 - 92%
        GJ03AB1234 - 95%
        GJ03AB1284 - 61%
        GJ03AB1234 - 94%

    The outlier must not be discarded — it has to survive in the record — but it
    must not change the answer either.
    """
    reads = [
        make_read("GJ03AB1234", 0.92, frame_index=10),
        make_read("GJ03AB1234", 0.95, frame_index=14),
        make_read("GJ03AB1284", 0.61, frame_index=18),
        make_read("GJ03AB1234", 0.94, frame_index=22),
    ]
    result = consensus(reads, ConsensusConfig())

    assert result.text == "GJ03AB1234"
    assert result.method == "char_vote"
    assert result.reads_total == 4
    assert result.reads_agreeing == 3
    assert result.grammar_valid

    # Every observation is retained as a candidate, the wrong one included.
    texts = {c.text for c in result.candidates}
    assert texts == {"GJ03AB1234", "GJ03AB1284"}

    # The disputed position (index 8) must be the least confident character.
    assert result.char_confidences[8] == min(result.char_confidences)
    assert result.char_confidences[0] > result.char_confidences[8]


def test_a_sharp_crop_outvotes_several_blurry_ones(make_read) -> None:
    """Crop quality weights the vote, not just OCR confidence.

    Three tiny blurry reads agreeing with each other should not beat one large
    sharp read, because OCR confidence on a 30px blur is close to meaningless.
    """
    reads = [
        make_read("GJ03AB1284", 0.70, sharpness=8.0, width=30, frame_index=1),
        make_read("GJ03AB1284", 0.70, sharpness=9.0, width=32, frame_index=2),
        make_read("GJ03AB1234", 0.88, sharpness=400.0, width=220, frame_index=3),
    ]
    result = consensus(reads, ConsensusConfig())
    assert result.text == "GJ03AB1234"


def test_single_read_is_scored_on_its_own_confidence(make_read) -> None:
    result = consensus([make_read("GJ03AB1234", 0.83)], ConsensusConfig())
    assert result.text == "GJ03AB1234"
    assert result.method == "single_read"
    assert result.confidence == 0.83
    assert result.agreement == 1.0


class TestIsConfirmed:
    """The predicate that decides "reading" vs "confirmed" on the wire.

    Must mirror `AnprOverlay.tsx`'s `isConfirmed()` exactly: this is the
    function the frontend's own `plate.status` field is derived from, and the
    whole point of sending it is that the two cannot disagree.
    """

    def test_no_result_is_not_confirmed(self) -> None:
        assert is_confirmed(None) is False

    def test_empty_text_is_not_confirmed(self) -> None:
        result = consensus([], ConsensusConfig())
        assert result.text == ""
        assert is_confirmed(result) is False

    def test_a_single_high_confidence_read_is_confirmed_immediately(
        self, make_read
    ) -> None:
        """The fast path: one clean read is enough, no need to wait for a
        second frame's worth of evidence."""
        result = consensus([make_read("GJ03AB1234", 0.92)], ConsensusConfig())
        assert is_confirmed(result) is True

    def test_a_read_below_the_confidence_bar_is_only_reading(self, make_read) -> None:
        result = consensus([make_read("GJ03AB1234", 0.42)], ConsensusConfig())
        assert result.text == "GJ03AB1234"  # usable — a fast-path consumer
        # can still show it — just not with a checkmark yet.
        assert is_confirmed(result) is False

    def test_grammar_invalid_never_confirms_however_confident(self, make_read) -> None:
        result = consensus(
            [make_read("NOTAPLATE1", 0.99, grammar_valid=False)], ConsensusConfig()
        )
        assert is_confirmed(result) is False

    def test_ambiguous_never_confirms_however_confident(self, make_read) -> None:
        # Two genuinely rival candidates — not a classic same-class confusion
        # like 3/8, which `grammar.confusable` would suppress the flag for —
        # at equal weight, so neither wins clearly.
        reads = [
            make_read("GJ03AB1234", 0.90, frame_index=1),
            make_read("GJ05CD5678", 0.90, frame_index=2),
        ]
        result = consensus(reads, ConsensusConfig())
        assert result.ambiguous is True
        assert is_confirmed(result) is False


def test_no_reads_yields_no_answer() -> None:
    result = consensus([], ConsensusConfig())
    assert result.text == ""
    assert result.method == "none"
    assert result.confidence == 0.0


def test_genuine_disagreement_is_flagged_ambiguous(make_read) -> None:
    """Two plausible, materially different plates must not be reported as fact."""
    reads = [
        make_read("GJ03AB1234", 0.85, frame_index=1),
        make_read("MH14XY9876", 0.84, frame_index=2),
    ]
    result = consensus(reads, ConsensusConfig())
    assert result.ambiguous
    assert result.disagreement > 0.3


def test_confusable_difference_is_not_ambiguity(make_read) -> None:
    """One difficult plate is not two rival answers.

    GJ03AB1234 vs GJ03AB1284 differ only by a classic 3/8 confusion, so this is
    one plate read imperfectly — flagging it ambiguous would bury the real
    ambiguous cases in noise.
    """
    reads = [
        make_read("GJ03AB1234", 0.86, frame_index=1),
        make_read("GJ03AB1284", 0.85, frame_index=2),
    ]
    result = consensus(reads, ConsensusConfig())
    assert not result.ambiguous


def test_confusion_correction_repairs_a_consensus(make_read) -> None:
    """Every frame misread the same character; grammar fixes what voting cannot."""
    reads = [
        make_read("GJ03A81234", 0.90, frame_index=i, grammar_valid=False)
        for i in range(4)
    ]
    result = consensus(reads, ConsensusConfig(apply_confusion_correction=True))
    assert result.text == "GJ03AB1234"
    assert result.corrected_from == "GJ03A81234"
    assert result.grammar_valid
    # A repaired character carries less certainty than an observed one.
    assert result.char_confidences[5] < result.char_confidences[0]


def test_correction_can_be_disabled(make_read) -> None:
    reads = [make_read("GJ03A81234", 0.90, frame_index=i, grammar_valid=False) for i in range(3)]
    result = consensus(reads, ConsensusConfig(apply_confusion_correction=False))
    assert result.text == "GJ03A81234"
    assert not result.grammar_valid
    assert result.corrected_from is None


def test_weight_floor_excludes_but_does_not_erase(make_read) -> None:
    """A read below the floor stops voting but stays in the count.

    reads_total covering every read is what makes the CSV and the summary agree.
    """
    reads = [
        make_read("GJ03AB1234", 0.95, sharpness=400.0, width=220, frame_index=1),
        make_read("XXXXXXXXXX", 0.05, sharpness=2.0, width=26, frame_index=2, grammar_valid=False),
    ]
    result = consensus(reads, ConsensusConfig(relative_weight_floor=0.5))
    assert result.text == "GJ03AB1234"
    assert result.reads_total == 2


def test_require_grammar_prefers_a_valid_runner_up(make_read) -> None:
    reads = [
        make_read("ZZ99ZZ9999", 0.70, frame_index=1, grammar_valid=False),
        make_read("!!!!!!", 0.95, frame_index=2, grammar_valid=False),
        make_read("GJ03AB1234", 0.60, frame_index=3),
    ]
    result = consensus(reads, ConsensusConfig(require_grammar=True))
    assert result.grammar_valid


def test_min_reads_gate(make_read) -> None:
    result = consensus([make_read("GJ03AB1234")], ConsensusConfig(min_reads=3))
    assert result.text == ""
    assert "minimum is 3" in result.grammar_note
