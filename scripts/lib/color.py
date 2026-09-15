from __future__ import annotations
"""
color.py — Figma color value → CSS string helpers.


A Figma color dict has keys r, g, b, a (all floats in [0, 1]).
"""



def figma_color_to_hex(color: dict) -> str:
    """Convert a Figma color to a hex string, e.g. '#ff8c00'."""
    r = format(round(color["r"] * 255), "02x")
    g = format(round(color["g"] * 255), "02x")
    b = format(round(color["b"] * 255), "02x")
    return f"#{r}{g}{b}"


def figma_color_to_rgba(color: dict, opacity: float = 1.0) -> str:
    """Convert a Figma color to an rgba() CSS string."""
    r = round(color["r"] * 255)
    g = round(color["g"] * 255)
    b = round(color["b"] * 255)
    a = round(color["a"] * opacity, 3)
    return f"rgba({r}, {g}, {b}, {a})"


def figma_color_to_css(
    color: dict,
    opacity: float = 1.0,
    sass_var_name: str | None = None,
) -> str:
    """
    Return the most compact CSS representation of a Figma color.

    - If *sass_var_name* is provided it is returned verbatim (e.g. '$primary').
    - If the effective alpha is >= 0.999 the hex form is used (shorter).
    - Otherwise rgba() is used.
    """
    if sass_var_name:
        return sass_var_name
    effective_alpha = color["a"] * opacity
    if effective_alpha >= 0.999:
        return figma_color_to_hex(color)
    return figma_color_to_rgba(color, opacity)


def px(value: float) -> str:
    """Convert a numeric pixel value to a CSS px string, e.g. 12 → '12px'.

    Preserves up to 2 decimal places for non-integer values so that sub-pixel
    Figma dimensions (e.g. 147.67px) are faithfully reproduced in CSS.
    Rounding to integers caused 0.33–0.5px overflows that browsers render as
    visible 1px artifacts at element boundaries.
    """
    rounded = round(value, 2)
    if rounded == int(rounded):
        return f"{int(rounded)}px"
    return f"{rounded:g}px"


def clamp_value(design_px: float, vp_min: int = 1024, vp_max: int = 1440) -> str:
    """Convert a design px value to clamp() that scales linearly between vp_min~vp_max."""
    if design_px == 0:
        return '0'
    if abs(design_px) <= 2:
        return px(design_px)

    min_px = round(design_px * vp_min / vp_max, 2)
    max_px = design_px

    v = round((max_px - min_px) / (vp_max - vp_min) * 100, 4)
    r = round(min_px - v * vp_min / 100, 4)

    min_str = f"{min_px:g}px"
    max_str = f"{max_px:g}px"

    if r >= 0:
        preferred = f"{v:g}vw + {r:g}px"
    else:
        preferred = f"{v:g}vw - {abs(r):g}px"

    return f"clamp({min_str}, {preferred}, {max_str})"


def should_adaptive(root_node: dict) -> bool:
    """Determine if a page should use adaptive output based on Figma signals."""
    layout_mode = root_node.get('layoutMode')
    if layout_mode not in ('VERTICAL', 'HORIZONTAL'):
        return False
    children = root_node.get('children', [])
    if not children:
        return False
    fill_count = sum(
        1 for c in children
        if c.get('layoutSizingHorizontal') == 'FILL'
    )
    return fill_count / len(children) >= 0.5
