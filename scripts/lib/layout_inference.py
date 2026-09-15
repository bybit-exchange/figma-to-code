from __future__ import annotations
"""
layout_inference.py — Flex-layout detection from Figma children geometry.


Given a list of sibling Figma nodes and their parent, this module tries to
infer whether they form a row or column flex container and returns a layout
descriptor dict (or None when no consistent layout can be detected).
"""

from typing import Callable

from .math_utils import median, max_deviation
from .color import px


AXIS_TOLERANCE = 4   # px: maximum spread on the cross-axis to still call it a line
GAP_MAX_VAR = 5      # px: maximum gap variance to accept as "uniform gaps"
MAX_PAGE_FLOW_GAP_PX = 400  # px: gaps larger than this indicate fixed-height page, not flow


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _nw(node: dict) -> float:
    """Node width — prefer node.width, fall back to absoluteBoundingBox."""
    abb = node.get("absoluteBoundingBox") or {}
    return node.get("width") if node.get("width") is not None else abb.get("width", 0)


def _nh(node: dict) -> float:
    """Node height — prefer node.height, fall back to absoluteBoundingBox."""
    abb = node.get("absoluteBoundingBox") or {}
    return node.get("height") if node.get("height") is not None else abb.get("height", 0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_flex_layout(
    children: list[dict],
    parent: dict,
    parent_abs_x: float = 0.0,
    parent_abs_y: float = 0.0,
) -> dict | None:
    """
    Try to infer a flex layout from *children* within *parent*.

    Returns a layout descriptor dict on success, None otherwise.

    *parent_abs_x/Y* are the parent's canvas-global coordinates used to
    convert children's global coordinates into parent-relative ones.
    """
    # Relative-coordinate accessors (prefer node.x/y, fall back to global − parent)
    def nx(n: dict) -> float:
        abb = n.get("absoluteBoundingBox") or {}
        return n["x"] if n.get("x") is not None else (abb.get("x", 0) - parent_abs_x)

    def ny(n: dict) -> float:
        abb = n.get("absoluteBoundingBox") or {}
        return n["y"] if n.get("y") is not None else (abb.get("y", 0) - parent_abs_y)

    if len(children) < 2:
        return None

    # Bail out if any child is rotated (rotation is in degrees in Figma)
    if any(c.get("rotation") and abs(c["rotation"]) > 0.5 for c in children):
        return None

    if _has_overlap(children, nx, ny):
        return None

    pw = _nw(parent)
    ph = _nh(parent)

    # --- Try row: y-coordinates should be nearly equal ---
    by_x = sorted(children, key=nx)
    if max_deviation([ny(c) for c in children]) <= AXIS_TOLERANCE:
        gaps = _consecutive_gaps(by_x, "h", nx, ny)
        if gaps and max_deviation(gaps) <= GAP_MAX_VAR:
            return _build("row", by_x, gaps, pw, ph, nx, ny)

    # --- Try column: x-coordinates should be nearly equal ---
    by_y = sorted(children, key=ny)
    if max_deviation([nx(c) for c in children]) <= AXIS_TOLERANCE:
        gaps = _consecutive_gaps(by_y, "v", nx, ny)
        if gaps and max_deviation(gaps) <= GAP_MAX_VAR:
            return _build("column", by_y, gaps, pw, ph, nx, ny)

    return None


def infer_page_flow(
    children: list[dict],
    parent: dict,
    parent_abs_x: float = 0.0,
    parent_abs_y: float = 0.0,
) -> dict | None:
    """
    Page-level flow detection: non-overlapping vertical stack with variable gaps.
    Only safe to call when ctx.isRoot == True (root page frame children).
    Returns per-child (margin_top, margin_left, margin_right, centered, max_width).
    """
    def nx(n: dict) -> float:
        abb = n.get("absoluteBoundingBox") or {}
        return n["x"] if n.get("x") is not None else (abb.get("x", 0) - parent_abs_x)

    def ny(n: dict) -> float:
        abb = n.get("absoluteBoundingBox") or {}
        return n["y"] if n.get("y") is not None else (abb.get("y", 0) - parent_abs_y)

    if len(children) < 2:
        return None
    if _has_overlap(children, nx, ny):
        return None

    pw = _nw(parent)
    by_y = sorted(children, key=ny)
    margins = []
    for i, c in enumerate(by_y):
        if i == 0:
            mt = max(0, round(ny(c)))
        else:
            prev = by_y[i - 1]
            mt = max(0, round(ny(c) - (ny(prev) + _nh(prev))))
        ml = max(0, round(nx(c)))
        mr = max(0, round(pw - nx(c) - _nw(c)))
        # 左右对称偏移（误差≤4px）且有实际偏移 → 居中容器
        centered = abs(ml - mr) <= AXIS_TOLERANCE and ml > 0
        margins.append({
            'id': c.get('id', ''),
            'mt': mt,
            'ml': ml,
            'mr': mr,
            'centered': centered,
            'max_width': round(_nw(c)),
        })

    # Guard: if any gap exceeds MAX_PAGE_FLOW_GAP_PX the parent is likely a
    # fixed-height page where elements are placed at absolute Y positions.
    # Converting such large offsets to margin-top inflates the page height.
    if any(m['mt'] > MAX_PAGE_FLOW_GAP_PX for m in margins):
        return None

    return {
        'direction': 'column',
        'mode': 'page_flow',
        'per_child_margins': margins,
        'confidence': 'high',
    }


def inferred_flex_to_css(inf: dict) -> dict:
    """Convert an inferred flex layout descriptor to a CSS property dict."""
    # page_flow mode: container CSS only — variable gaps and x-offsets are NOT
    # handled here. The caller (ir_builder._build_node) is responsible for
    # iterating inf['per_child_margins'] and injecting margin-top / max-width /
    # margin-left:auto on each child after built_children is populated.
    if inf.get('mode') == 'page_flow':
        # No align-items here: page_flow containers have no Auto Layout in Figma
        # so counterAxisAlignItems is absent from the design spec. ir_builder
        # pops the css_extractor default (MIN → flex-start) to avoid hardcoding.
        return {
            'display': 'flex',
            'flex-direction': 'column',
        }

    css: dict[str, str] = {
        "display": "flex",
        "flex-direction": inf["direction"],
        "align-items": inf["alignItems"],
    }
    if inf["gap"] > 0:
        css["gap"] = px(inf["gap"])
    if inf.get("justifyContent"):
        css["justify-content"] = inf["justifyContent"]

    pt = inf["paddingTop"]
    pr = inf["paddingRight"]
    pb = inf["paddingBottom"]
    pl = inf["paddingLeft"]
    if pt or pr or pb or pl:
        css["padding"] = " ".join(px(v) for v in (pt, pr, pb, pl))

    return css


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build(
    direction: str,
    sorted_children: list[dict],
    gaps: list[float],
    pw: float,
    ph: float,
    nx: Callable,
    ny: Callable,
) -> dict:
    gap = round(median(gaps))
    first = sorted_children[0]
    last = sorted_children[-1]

    if direction == "row":
        pl = nx(first)
        pr = pw - (nx(last) + _nw(last))
        pt = min(ny(c) for c in sorted_children)
        pb = min(ph - ny(c) - _nh(c) for c in sorted_children)
    else:
        pt = ny(first)
        pb = ph - (ny(last) + _nh(last))
        pl = min(nx(c) for c in sorted_children)
        pr = min(pw - nx(c) - _nw(c) for c in sorted_children)

    return {
        "direction": direction,
        "gap": gap,
        "paddingTop":    max(0, round(pt)),
        "paddingRight":  max(0, round(pr)),
        "paddingBottom": max(0, round(pb)),
        "paddingLeft":   max(0, round(pl)),
        "alignItems": _infer_alignment(sorted_children, direction, nx, ny),
        "justifyContent": (
            "space-between"
            if _detect_space_between(sorted_children, direction, pw, ph, nx, ny)
            else None
        ),
        "confidence": "high" if max_deviation(gaps) <= 1 else "low",
    }


def _infer_alignment(
    sorted_children: list[dict],
    direction: str,
    nx: Callable,
    ny: Callable,
) -> str:
    """Determine flex align-items from child positions on the cross-axis."""
    if direction == "row":
        # Check top edges, bottom edges, then center points
        if max_deviation([ny(c) for c in sorted_children]) <= AXIS_TOLERANCE:
            return "flex-start"
        if max_deviation([ny(c) + _nh(c) for c in sorted_children]) <= AXIS_TOLERANCE:
            return "flex-end"
        if max_deviation([ny(c) + _nh(c) / 2 for c in sorted_children]) <= AXIS_TOLERANCE:
            return "center"
        return "flex-start"
    else:
        # Check left edges, right edges, then center points
        if max_deviation([nx(c) for c in sorted_children]) <= AXIS_TOLERANCE:
            return "flex-start"
        if max_deviation([nx(c) + _nw(c) for c in sorted_children]) <= AXIS_TOLERANCE:
            return "flex-end"
        if max_deviation([nx(c) + _nw(c) / 2 for c in sorted_children]) <= AXIS_TOLERANCE:
            return "center"
        return "flex-start"


def _detect_space_between(
    sorted_children: list[dict],
    direction: str,
    pw: float,
    ph: float,
    nx: Callable,
    ny: Callable,
) -> bool:
    """Return True if gaps match a space-between distribution."""
    if len(sorted_children) < 2:
        return False
    container = pw if direction == "row" else ph
    total_child = sum(
        _nw(c) if direction == "row" else _nh(c)
        for c in sorted_children
    )
    expected = (container - total_child) / (len(sorted_children) - 1)
    axis = "h" if direction == "row" else "v"
    gaps = _consecutive_gaps(sorted_children, axis, nx, ny)
    return all(abs(g - expected) <= AXIS_TOLERANCE for g in gaps)


def _consecutive_gaps(
    sorted_children: list[dict],
    axis: str,
    nx: Callable,
    ny: Callable,
) -> list[float]:
    """Return the gaps between each consecutive pair of children."""
    gaps = []
    for i, b in enumerate(sorted_children[1:]):
        a = sorted_children[i]
        if axis == "h":
            gaps.append(nx(b) - (nx(a) + _nw(a)))
        else:
            gaps.append(ny(b) - (ny(a) + _nh(a)))
    return gaps


def _has_overlap(
    children: list[dict],
    nx: Callable,
    ny: Callable,
) -> bool:
    """AABB overlap check — return True if any two children overlap."""
    for i in range(len(children)):
        for j in range(i + 1, len(children)):
            a, b = children[i], children[j]
            if (
                nx(a) < nx(b) + _nw(b)
                and nx(a) + _nw(a) > nx(b)
                and ny(a) < ny(b) + _nh(b)
                and ny(a) + _nh(a) > ny(b)
            ):
                return True
    return False
