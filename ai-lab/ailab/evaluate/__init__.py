"""Evaluation against ground truth."""

from ailab.evaluate.groundtruth import GroundTruth, evaluate_predictions, load, summarise
from ailab.evaluate.metrics import (
    PlateComparison,
    calibration_table,
    character_error_rate,
    error_analysis,
    levenshtein,
    threshold_sweep,
)

__all__ = [
    "GroundTruth",
    "PlateComparison",
    "calibration_table",
    "character_error_rate",
    "error_analysis",
    "evaluate_predictions",
    "levenshtein",
    "load",
    "summarise",
    "threshold_sweep",
]
