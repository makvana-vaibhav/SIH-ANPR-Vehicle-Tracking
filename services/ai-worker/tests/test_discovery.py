"""Camera assignment across workers.

Sharding decides which worker owns which camera with no coordinator, so the
properties that matter are: every camera is owned by exactly one worker, and
the assignment does not churn when the fleet changes.
"""

from __future__ import annotations

import pytest

from ai_worker.discovery import owns, shard_of


class TestSharding:
    def test_single_worker_owns_everything(self) -> None:
        assert all(owns(f"CAM-{i:04d}", 0, 1) for i in range(200))

    def test_every_camera_is_owned_exactly_once(self) -> None:
        """No camera unwatched, and no stream opened by two workers."""
        for shards in (2, 3, 4, 8):
            for i in range(300):
                code = f"GJ-RJT-{i:04d}"
                owners = [w for w in range(shards) if owns(code, w, shards)]
                assert len(owners) == 1, f"{code} owned by {owners} of {shards}"

    def test_assignment_is_stable(self) -> None:
        """Same camera, same shard, every time — including across processes."""
        for code in ("GJ-RJT-0001", "cam-00034", "AHM-JUNCTION-7"):
            assert len({shard_of(code, 4) for _ in range(50)}) == 1

    def test_load_is_reasonably_even(self) -> None:
        """A hash that piles every camera onto one worker would be useless."""
        shards = 4
        counts = [0] * shards
        for i in range(400):
            counts[shard_of(f"CAM-{i:04d}", shards)] += 1
        assert min(counts) > 400 / shards * 0.6, f"uneven distribution: {counts}"

    def test_adding_a_camera_does_not_reshuffle_the_others(self) -> None:
        """Positional assignment would tear down working streams for nothing."""
        fleet = [f"CAM-{i:04d}" for i in range(50)]
        before = {c: shard_of(c, 4) for c in fleet}
        fleet.insert(0, "CAM-NEW")
        after = {c: shard_of(c, 4) for c in fleet}
        assert all(before[c] == after[c] for c in before)

    @pytest.mark.parametrize("code", ["", "x", "GJ-RJT-0001", "a" * 200])
    def test_handles_any_code(self, code: str) -> None:
        assert 0 <= shard_of(code, 4) < 4
