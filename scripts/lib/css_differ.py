"""css_differ.py — CSS 属性差值计算，用于 merge_responsive.py Phase 3。"""
from __future__ import annotations
import re

POSITIONAL_RESET_PROPS = frozenset({
    'position', 'top', 'right', 'bottom', 'left',
    'transform', 'zIndex', 'float', 'clear',
})

# Layout-control properties: when PC has a fixed-px value but H5 lacks the property entirely,
# reset to neutral values in the responsive override. This prevents PC canvas positioning
# (e.g. margin-left: 120px, margin-top: 203px) from persisting on mobile.
# Real case: EU deposit hero-content-wrapper (250:2623) has margin-left: 120px in PC layout
# but H5 equivalent has no margin-left → without this reset, content is pushed off-screen on mobile.
_LAYOUT_RESET_PROPS: dict = {
    'margin-left': '0px', 'margin-right': '0px',
    'margin-top': '0px', 'margin-bottom': '0px',
    'max-width': '100%', 'max-height': 'none',
    'height': 'unset',  # PC fixed heights must not persist when H5 is text-only
}

# Visual-appearance properties: when PC has these but H5 lacks them entirely, reset to neutral.
# Real case: EU deposit campaign 250:2631 (by_primary-buttons-dark) is a button in PC
# (height=48px, bg=#ff9c2e, border-radius=24px) but becomes text-only in H5. Without resets,
# the outer H5 button wrapper height = padding(24) + inner PC height(48) = 72px (should be 48px).
_VISUAL_RESET_PROPS: dict = {
    'background-color': 'transparent',
    'border-radius': '0',
    'padding': '0',       # PC button padding must not persist when H5 node is text-only
    'white-space': 'normal',  # PC nowrap must not persist on mobile (text must wrap)
}


def _is_fixed_px(value: str) -> bool:
    return bool(re.match(r'^\d+(\.\d+)?px$', str(value or '')))


# Maximum px value considered a "specific content size" (icon, logo, badge).
# Values >= this threshold are treated as viewport-sized and don't need a
# responsive override — `width: 100%` already covers them.
# Smallest mobile viewport is ~320px, so 250px leaves a safe margin.
_VIEWPORT_SIZE_THRESHOLD_PX = 250.0


def _is_small_fixed_px(value: str) -> bool:
    """Return True for fixed-px values that represent specific content sizes (< 250px).

    Used to decide whether H5 fixed widths must be preserved in responsive
    overrides (e.g. 48px logo) vs dropped in favour of width:100% (e.g. 393px section).
    """
    m = re.match(r'^(\d+(?:\.\d+)?)px$', str(value or ''))
    return bool(m) and float(m.group(1)) < _VIEWPORT_SIZE_THRESHOLD_PX


def css_diff(base_css: dict, supplement_css: dict) -> tuple[dict, dict]:
    """
    Compute diff between base (PC) and supplement (H5) CSS dicts.

    Returns:
        base_styles:   CSS properties for the normal rule block (PC defaults)
        responsive:    CSS properties for the @media override block (H5 values)

    Cases:
        1. Both have prop, same value  → base_styles only
        2. Both have prop, diff value  → base_styles=PC, responsive=H5
        3. Base only, positional prop  → base_styles=PC, responsive='unset'
        4. Base only, other prop       → base_styles only
        5. Supplement only             → responsive only
    """
    base_styles: dict = {}
    responsive: dict = {}

    all_props = set(base_css) | set(supplement_css)
    for prop in all_props:
        bv = base_css.get(prop)
        sv = supplement_css.get(prop)

        if bv is not None and sv is not None:
            if bv == sv:
                base_styles[prop] = bv
            else:
                base_styles[prop] = bv
                responsive[prop] = sv
        elif bv is not None:
            base_styles[prop] = bv
            if prop in POSITIONAL_RESET_PROPS:
                responsive[prop] = 'unset'
            elif prop in _LAYOUT_RESET_PROPS and _is_fixed_px(str(bv)):
                responsive[prop] = _LAYOUT_RESET_PROPS[prop]
            elif prop in _VISUAL_RESET_PROPS:
                responsive[prop] = _VISUAL_RESET_PROPS[prop]
        else:
            # U-434: skip supplement-only text-align:left when PC base is a flex container
            # with justify-content:center. Flex centering handles visual alignment; adding
            # text-align:left from the H5 text-node supplement makes button text left-align
            # inside a 100%-wide flex child (e.g. hero CTA button 250:2631 "Register Now").
            if (prop == 'text-align' and sv == 'left' and
                    base_css.get('display') == 'flex' and
                    base_css.get('justify-content') == 'center'):
                pass
            else:
                responsive[prop] = sv

    # Convert fixed root width to responsive-friendly form
    if 'width' in base_styles and _is_fixed_px(base_styles['width']):
        base_styles['max-width'] = base_styles['width']
        base_styles['width'] = '100%'
        h5_width = str(responsive.get('width', ''))
        if _is_small_fixed_px(h5_width):
            # Preserve H5 small fixed-px width (e.g. 48px logo tile) in responsive.
            pass
        else:
            responsive.pop('width', None)
            # U-431: when H5 has a flexible/percentage width (e.g. width:100%), it
            # explicitly wants to span the full parent. Without this branch, the PC
            # max-width cap (e.g. max-width:228px) persists in H5, truncating the
            # element. Cancel it by injecting max-width:none — unless the H5 side
            # already contributes its own max-width override via the normal diff path.
            # Real case: earn-steps text nodes (250:2640 etc.) — PC width=228px, H5
            # width=100% → text-align:center over 228px in a 353px container looks
            # left-biased. max-width:none lets the element fill the 353px parent.
            if h5_width and not _is_fixed_px(h5_width) and 'max-width' not in responsive:
                responsive['max-width'] = 'none'

    return base_styles, responsive
