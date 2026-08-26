"""Multi-frame aggregation: plate grammar and consensus voting."""

from ailab.aggregate.consensus import consensus
from ailab.aggregate.grammar import coerce, describe_plate, normalise, validate

__all__ = ["coerce", "consensus", "describe_plate", "normalise", "validate"]
