"""
math_utils.py — Small statistical helpers used by layout inference.

"""

from __future__ import annotations
import math


def median(values: list[float]) -> float:
    """Return the median of *values*.  Returns 0 for an empty list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2 != 0:
        return sorted_vals[mid]
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def max_deviation(values: list[float]) -> float:
    """Return the maximum absolute deviation from the mean.  Returns 0 for an empty list."""
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    return max(abs(v - avg) for v in values)


def is_consistent(values: list[float], tolerance: float) -> bool:
    """Return True if all values are within *tolerance* of each other (via max_deviation)."""
    return max_deviation(values) <= tolerance


def rad_to_deg(rad: float) -> float:
    """Convert radians to degrees."""
    return rad * (180.0 / math.pi)
