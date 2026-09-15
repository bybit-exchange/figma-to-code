"""
Script Layer: deterministic CSS extraction — same input always yields same output.
"""

import math
import re

from .color import figma_color_to_css, figma_color_to_hex, px, clamp_value
from .math_utils import rad_to_deg

_THEME_SENSITIVE_TEXT_TOKENS = frozenset({
    'bds-gray-t2', 'bds-gray-t3', 'bds-gray-t4', 'bds-gray-t4-dis', 'bds-gray-t5',
})

def _is_theme_sensitive_text_token(var_str: str) -> bool:
    """True if var() references a token whose text color changes drastically between themes."""
    import re as _re
    m = _re.search(r'--([^)]+)\)', var_str)
    return bool(m and m.group(1) in _THEME_SENSITIVE_TEXT_TOKENS)

# ─── Variable miss tracking ────────────────────────────────────────────────────
# Module-level counter for Figma variable IDs that had no BDS mapping.
# convert.py calls clear_var_miss() before each conversion and get_var_miss() after.
_var_miss: dict = {}  # {var_id: int}

def clear_var_miss() -> None:
    """Reset miss counter. Call before each build_ir invocation."""
    _var_miss.clear()

def get_var_miss() -> dict:
    """Return copy of current miss counts: {var_id: count}."""
    return dict(_var_miss)

def _record_var_miss(var_id: str) -> None:
    _var_miss[var_id] = _var_miss.get(var_id, 0) + 1


def _adjust_gradient_rotation(css: dict, rotation_deg: float) -> None:
    """Bake element rotation into CSS gradient angle instead of using CSS rotate().

    CSS rotate() applied on top of a gradient would change the perceived gradient direction.
    Instead, compute the screen-space angle: screen_angle = (local_angle - rotation_deg + 360) % 360
    Example: local=180deg, element rotated -180deg → screen_angle=(180-(-180)+360)%360=0deg.
    linear-gradient(0deg) = bottom→top direction = black at bottom, transparent at top ✓
    """
    for prop in ('background', 'background-image'):
        val = css.get(prop)
        if not val or 'gradient' not in val:
            continue

        def _replace_angle(m, rd=rotation_deg):
            local_deg = float(m.group(1))
            # Correct formula: screen_angle = local_angle + css_rotation_deg
            # Rotating element by θ rotates the gradient by the same θ (same direction).
            # Example: local=180deg(top→bottom), rotate(-90deg) → gradient goes left→right = 90deg ✓
            adjusted = (local_deg + rd + 360) % 360
            # Round to 2dp to collapse floating-point near-zero to exactly 0.
            # Without rounding, (180 + -180 + 360) % 360 ≈ 5e-06 → "5e-06deg" is
            # scientific notation that browsers reject as an invalid CSS angle.
            adjusted = round(adjusted, 2)
            return f'linear-gradient({adjusted:g}deg,'

        css[prop] = re.sub(r'linear-gradient\(([0-9.]+)deg,', _replace_angle, val)
        return


# sassVars: dict mapping variableId -> '$sass-var-name'
def extract_css(node: dict, ctx: dict, sass_vars: dict = None, var_id_to_bds: dict = None, style_id_to_bds: dict = None, force_css_fills: bool = False) -> dict:
    if sass_vars is None:
        sass_vars = {}

    css = {}

    _extract_sizing(css, node, ctx)
    _extract_position(css, node, ctx)
    _extract_flex_container(css, node, ctx)
    _extract_flex_child(css, node, ctx)
    _extract_visual(css, node, sass_vars, var_id_to_bds, style_id_to_bds, force_css_fills=force_css_fills)

    # RECTANGLE + IMAGE fill → rendered as <img>.
    # NOTE: object-fit and dimension normalization are handled at the split layer
    # (_generate_leaf_tsx / generate_css) where varying-prop context is available.
    # No CSS override here to avoid breaking standalone image nodes.

    if ctx.get('isRoot'):
        # Root page frame: remove overflow:hidden so browser text-reflow can
        # expand sections beyond the design frame height without clipping content.
        # (_extract_corner_radius may have added it above via clipsContent logic)
        css.pop('overflow', None)
        css.pop('overflow-x', None)
        # But respect horizontal clip: designer-enabled clipsContent means
        # decorative elements (e.g. off-edge butterflies) must not widen the
        # document. Keep overflow-x:hidden while leaving overflow-y unrestricted.
        if node.get('clipsContent'):
            css['overflow-x'] = 'hidden'

    # Flex containers must be position:relative so absolutely-positioned
    # descendants anchor to this container, not an outer ancestor.
    _layout_mode = node.get('layoutMode')
    if _layout_mode and _layout_mode != 'NONE' and 'position' not in css:
        css['position'] = 'relative'

    if node.get('type') == 'TEXT':
        _extract_typography(css, node, ctx)

    rotation = node.get('rotation')
    if rotation and abs(rotation) > 0.01:
        _abb_for_rot = node.get('absoluteBoundingBox') or {}
        _rot_node_type = node.get('type', '')
        # VECTOR/BOOLEAN/etc. nodes lack direct width/height; use absoluteBoundingBox.
        # FRAME instances may also lack width/height when they are outline-only shapes
        # (e.g. G-13 regression) — do NOT treat their abb as explicit dims.
        # RECTANGLE/ELLIPSE/GROUP nodes accessed via Figma nodes API may also lack direct
        # width/height fields; use absoluteBoundingBox for those shape types too.
        _vector_family = _rot_node_type in (
            'VECTOR', 'BOOLEAN_OPERATION', 'REGULAR_POLYGON', 'STAR', 'LINE',
        )
        _shape_family = _rot_node_type in ('RECTANGLE', 'ELLIPSE', 'GROUP', 'TEXT')
        has_explicit_dims = (
            node.get('width') is not None or node.get('height') is not None
            or ((_vector_family or _shape_family) and bool(_abb_for_rot.get('width')))
        )
        bg = css.get('background', '') or css.get('background-image', '') or ''
        has_bg_gradient = 'gradient' in bg
        # Vector-family nodes (VECTOR, BOOLEAN_OPERATION, etc.) are exported as SVG
        # assets with rotation baked into the path data. Applying CSS rotation would
        # double-rotate the image. Only rotate non-vector nodes (FRAMEs, GROUPs, etc.)
        # or vector nodes with backdrop blur (rendered as divs, not SVG imgs).
        _has_backdrop_blur = any(
            e.get('visible') is not False and e.get('type') == 'BACKGROUND_BLUR'
            for e in (node.get('effects') or [])
        )
        _skip_rotation_for_svg = _vector_family and not _has_backdrop_blur
        # Gradient-fill elements: don't apply CSS rotate() — instead bake the rotation
        # into the gradient angle so the visual direction is preserved exactly.
        # screen_angle = (local_angle - css_rotation_deg + 360) % 360
        # Example: local=180deg, rotation=-180deg → screen=0deg (black at bottom ✓).
        # Without baking: CSS rotate(-180deg) would flip a 180deg gradient to 0deg visually,
        # but removing rotate() without adjusting angle leaves it at 180deg (black at top ✗).
        # IMAGE fill nodes are exported by Figma node ID: the PNG already bakes in rotation.
        # Applying CSS rotate() on top would double-rotate the image.
        _has_image_fill = any(
            f.get('type') == 'IMAGE' and f.get('visible') is not False
            for f in (node.get('fills') or [])
        )
        should_rotate = (
            node.get('type') != 'LINE'
            and not _skip_rotation_for_svg
            and not _has_image_fill  # IMAGE fill nodes: rotation baked into Figma node export
            and has_explicit_dims
            and not has_bg_gradient  # gradient angle adjusted below instead
        )
        if should_rotate:
            deg = rad_to_deg(rotation)
            existing = css.get('transform', '')
            css['transform'] = (existing + f' rotate({round(deg, 2):g}deg)').strip()
        elif has_bg_gradient and has_explicit_dims and not _skip_rotation_for_svg:
            deg = rad_to_deg(rotation)
            # Right-anchored gradient: flip rotation direction so dark end faces right edge.
            if css.get('right') is not None and css.get('left') is None:
                deg = -deg
            _adjust_gradient_rotation(css, deg)

    blend_mode = node.get('blendMode')
    if blend_mode and blend_mode not in ('NORMAL', 'PASS_THROUGH'):
        BLEND_MAP = {'LINEAR_DODGE': 'plus-lighter', 'LINEAR_BURN': 'color-burn'}
        css['mix-blend-mode'] = BLEND_MAP.get(
            blend_mode,
            blend_mode.lower().replace('_', '-'),
        )

    opacity = node.get('opacity')
    # U-467: Root page FRAME with opacity < 1 would make the entire page semi-transparent.
    # This is a Figma design-time artifact (e.g. draft marker) and must not reach production CSS.
    if isinstance(opacity, (int, float)) and opacity < 0.999 and not ctx.get('isRoot'):
        css['opacity'] = str(round(opacity, 3)).rstrip('0').rstrip('.')
        # Ensure at least one decimal if it was e.g. "0.500"
        css['opacity'] = f'{opacity:.3f}'.rstrip('0').rstrip('.')
        # Match JS: String(+opacity.toFixed(3))
        css['opacity'] = str(float(f'{opacity:.3f}'))

    return css


# ─── Sizing ────────────────────────────────────────────────────────────────────

def _is_image_fill_node(node: dict) -> bool:
    """True if node is a RECTANGLE with an IMAGE fill (rendered as <img>)."""
    if node.get('type') != 'RECTANGLE':
        return False
    fills = node.get('fills') or []
    return any(f.get('type') == 'IMAGE' and f.get('visible') is not False for f in fills)


def _extract_sizing(css: dict, node: dict, ctx: dict) -> None:
    hw = node.get('layoutSizingHorizontal')
    vw = node.get('layoutSizingVertical')
    abb = node.get('absoluteBoundingBox') or {}
    w = node.get('width') if node.get('width') is not None else abb.get('width', 0)
    h = node.get('height') if node.get('height') is not None else abb.get('height', 0)
    if w is None:
        w = 0
    if h is None:
        h = 0

    # Shape nodes in a flex container have no layoutSizing fields but have definite
    # intrinsic sizes — force explicit width/height so they don't collapse to 0×0.
    shape_types = {'ELLIPSE', 'RECTANGLE', 'STAR', 'REGULAR_POLYGON', 'LINE'}
    is_shape_node = (
        not hw and not vw
        and w > 0 and h > 0
        and ctx.get('layoutMode') != 'NONE'
        and node.get('type') in shape_types
    )

    # Nodes rendered as position:absolute do NOT expand their parent via flex HUG/FILL.
    # In that context the bounding-box size must always be emitted explicitly, regardless
    # of the layoutSizing mode (HUG/FILL only make sense inside a flex parent).
    # Cases:
    #   • ctx.layoutMode == NONE  → parent is an absolute-layout container; all children
    #                               get position:absolute (see _extract_position)
    #   • layoutPositioning == ABSOLUTE → node is explicitly pinned inside a flex parent
    is_absolute_self = (
        ctx.get('layoutMode') == 'NONE'
        or node.get('layoutPositioning') == 'ABSOLUTE'
    )

    _adaptive = ctx.get('_adaptive', False)

    if hw == 'FIXED' or is_shape_node or is_absolute_self:
        parent_w = ctx.get('width', 0)
        # Flex child that exactly fills its parent → use width:100% so it can
        # shrink with the viewport. Absolute children keep explicit px (they need
        # precise coordinates). Shape nodes keep explicit px (SVG sizing).
        # Don't use width:100% when parent is HUG — the parent has no explicit CSS
        # width, so 100% has no reference and causes collapse.
        _parent_is_hug_w = (
            ctx.get('_parentSizingH') == 'HUG'
            or (ctx.get('layoutMode') == 'VERTICAL' and ctx.get('_parentSizingH') == 'HUG')
        )
        if (not is_absolute_self
                and not is_shape_node
                and hw == 'FIXED'
                and parent_w > 0
                and abs(w - parent_w) <= 0.5
                and not _parent_is_hug_w):
            css['width'] = '100%'
        elif (_adaptive
                and not is_absolute_self
                and not is_shape_node
                and hw == 'FIXED'
                and parent_w > 0
                and w < parent_w * 0.97
                and ctx.get('layoutMode') != 'HORIZONTAL'):
            # Adaptive: FIXED content area narrower than parent → elastic centering
            # Skip for HORIZONTAL (flex-row) parents: margin:auto absorbs flex space
            # and pushes siblings away, breaking alignment.
            css['max-width'] = px(w)
            css['width'] = '100%'
            css['margin-left'] = 'auto'
            css['margin-right'] = 'auto'
        else:
            css['width'] = px(w)
    elif hw == 'FILL' and ctx.get('layoutMode') in ('HORIZONTAL', 'GRID'):
        css['flex'] = '1'
    elif hw == 'FILL' and _adaptive and ctx.get('layoutMode') == 'VERTICAL':
        css['width'] = '100%'
    elif hw == 'HUG' and not is_absolute_self:
        parent_w = ctx.get('width', 0)
        # HUG element whose design width equals the parent width:
        # the content fills the full parent cross-axis → use width:100%.
        if parent_w > 0 and abs(w - parent_w) <= 0.5:
            css['width'] = '100%'
    # HUG with width != parent_w: do not write width (content-driven)

    # HUG vertical + own layoutMode (flex container) → no explicit height.
    # A flex container's height is driven by its flow children (CSS height:auto).
    # Setting height:{bb.height}px creates an overflow:hidden trap: CSS renders
    # HUG containers slightly differently from Figma (border, font metrics), so
    # the explicit height can clip children when flex-shrink:0 is in effect.
    # Exception: HUG with layoutMode=NONE — all children are absolute, so no flex
    # flow exists to drive height; must keep the explicit height (see U-100).
    _own_flex = node.get('layoutMode') and node.get('layoutMode') != 'NONE'
    is_hug_v = vw == 'HUG' and bool(_own_flex)  # This also guards the pure-text height-pinning path below.
    if (vw == 'FIXED' or is_shape_node or is_absolute_self) and not is_hug_v:
        if ctx.get('isRoot'):
            # Root page frame: use min-height so browser text-reflow doesn't
            # clip content below the design's fixed frame height.
            # Figma height is border-box; CSS min-height is content-box (no global
            # box-sizing:border-box reset). Subtract vertical padding so the root
            # element's rendered height matches the Figma frame dimensions.
            _pad_top = node.get('paddingTop', 0) or 0
            _pad_bottom = node.get('paddingBottom', 0) or 0
            _content_h = max(0.0, h - _pad_top - _pad_bottom)
            css['min-height'] = px(_content_h) if _content_h > 0 else '0'
        elif (_adaptive
                and hw == 'FILL' and vw == 'FIXED'
                and w > 0 and h > 0
                and not is_absolute_self
                and _is_image_fill_node(node)):
            # Adaptive: FILL-width image with fixed height → aspect-ratio preserves
            # proportions as width scales, preventing squish/stretch.
            ratio = round(w / h, 4)
            css['aspect-ratio'] = f'{ratio:g}'
        else:
            css['height'] = px(h)
    # HUG/FILL vertical: do not write height (FILL handled by align-self: stretch)

    if node.get('minWidth') is not None:
        css['min-width'] = px(node['minWidth'])
    if node.get('maxWidth') is not None:
        css['max-width'] = px(node['maxWidth'])
    if node.get('minHeight') is not None:
        css['min-height'] = px(node['minHeight'])
    if node.get('maxHeight') is not None:
        css['max-height'] = px(node['maxHeight'])

    # ── Pure-text HUG container: pin height to Figma bbox ──────────────────────
    # Figma measures text by glyph-bounds; CSS renders text with full line-height
    # boxes, adding ~2-4px half-leading per container. For flex containers whose
    # only visible children are TEXT nodes (HUG-sized vertically), pin the CSS
    # height to the Figma bbox so cumulative drift doesn't push down downstream
    # content (e.g. task cards causing FAQ to exceed tab bar boundary).
    layout_mode_local = node.get('layoutMode')
    if layout_mode_local and layout_mode_local != 'NONE':
        if not css.get('height') and not css.get('min-height') and not is_hug_v:
            if vw in ('HUG', None) and h > 0:
                visible_ch = [
                    c for c in (node.get('children') or [])
                    if c.get('visible') is not False
                ]
                # Skip when node has vertical padding: padding itself provides
                # the height (e.g. a button with paddingTop=12/paddingBottom=12).
                # Pinning height to the Figma bbox then subtracting padding in
                # _post_process_frame yields a zero content-box height that makes
                # the button look completely flat under box-sizing:border-box.
                _has_v_padding = (
                    (node.get('paddingTop') or 0) + (node.get('paddingBottom') or 0) > 0
                )
                if visible_ch and all(c.get('type') == 'TEXT' for c in visible_ch) \
                        and not _has_v_padding:
                    css['height'] = px(h)


# ─── Position ──────────────────────────────────────────────────────────────────

def _extract_position(css: dict, node: dict, ctx: dict) -> None:
    if ctx.get('isRoot'):
        return

    is_absolute_child = (
        node.get('layoutPositioning') == 'ABSOLUTE'
        or ctx.get('layoutMode') == 'NONE'
    )
    if not is_absolute_child:
        return

    css['position'] = 'absolute'

    abs_x = ctx.get('absX') or 0
    abs_y = ctx.get('absY') or 0
    abb = node.get('absoluteBoundingBox') or {}

    x = node.get('x') if node.get('x') is not None else ((abb.get('x') or 0) - abs_x)
    y = node.get('y') if node.get('y') is not None else ((abb.get('y') or 0) - abs_y)
    w = node.get('width') if node.get('width') is not None else abb.get('width', 0)
    h = node.get('height') if node.get('height') is not None else abb.get('height', 0)
    if w is None:
        w = 0
    if h is None:
        h = 0

    pw = ctx.get('width') or 0
    ph = ctx.get('height') or 0
    hc = (node.get('constraints') or {}).get('horizontal', 'LEFT')
    vc = (node.get('constraints') or {}).get('vertical', 'TOP')

    # ── Horizontal constraint ──
    if hc == 'LEFT':
        css['left'] = px(x)
    elif hc == 'RIGHT':
        css['right'] = px(pw - x - w)
    elif hc == 'CENTER':
        h_offset = (x + w / 2) - pw / 2
        if pw <= 0:
            css['left'] = px(x)
        elif abs(h_offset) <= 3:
            # ±3px: design-tool alignment precision, treat as exact center
            css['left'] = '50%'
            existing = css.get('transform', '')
            css['transform'] = (existing + ' translateX(-50%)').strip()
        elif abs(h_offset) / pw < 0.50:
            # measurable offset but still CENTER-constrained → preserve exact position
            css['left'] = f'calc(50% + {round(h_offset, 2):g}px)'
            existing = css.get('transform', '')
            css['transform'] = (existing + ' translateX(-50%)').strip()
        else:
            css['left'] = px(x)
    elif hc == 'SCALE':
        if pw > 0:
            lp = round((x / pw) * 100, 2)
            wp = round((w / pw) * 100, 2)
            css['left'] = f'{lp:g}%' if -200 <= lp <= 300 else px(x)
            css['width'] = f'{wp:g}%' if 0 <= wp <= 300 else px(w)
        else:
            css['left'] = px(x)
            css['width'] = px(w)
    elif hc == 'LEFT_RIGHT':
        css['left'] = px(x)
        css['right'] = px(pw - x - w)

    # ── Vertical constraint ──
    # CSS top/bottom is measured from the PADDING EDGE of the containing block.
    # When the parent has an INSIDE stroke (border drawn inside its bbox), the padding
    # edge is inset by the border width. Figma positions children relative to the BBOX
    # edge, so we must subtract the border offset to get the correct CSS value.
    _inside_border = (
        ctx.get('strokeAlign') == 'INSIDE'
        and (ctx.get('strokeWeight') or 0) > 0
    )
    _border_w = ctx.get('strokeWeight', 0) if _inside_border else 0

    if vc == 'TOP':
        css['top'] = px(y - _border_w)
    elif vc == 'BOTTOM':
        css['bottom'] = px(ph - y - h - _border_w)
    elif vc == 'CENTER':
        v_offset = (y + h / 2) - ph / 2
        v_abs_off = abs(v_offset)
        v_rel_off = v_abs_off / ph if ph > 0 else 1
        if ph <= 0 or v_abs_off <= 3:
            # ±3px: design-tool alignment precision, treat as exact center
            css['top'] = '50%'
            existing = css.get('transform', '')
            css['transform'] = (existing + ' translateY(-50%)').strip()
        elif v_rel_off < 0.30:
            # measurable offset within 30% of parent height → preserve exact position
            css['top'] = f'calc(50% + {round(v_offset, 2):g}px)'
            existing = css.get('transform', '')
            css['transform'] = (existing + ' translateY(-50%)').strip()
        else:
            css['top'] = px(y)
    elif vc == 'SCALE':
        if ph > 0:
            tp = round((y / ph) * 100, 2)
            hp = round((h / ph) * 100, 2)
            css['top'] = f'{tp:g}%' if -200 <= tp <= 300 else px(y)
            css['height'] = f'{hp:g}%' if 0 <= hp <= 300 else px(h)
        else:
            css['top'] = px(y)
            css['height'] = px(h)
    elif vc == 'TOP_BOTTOM':
        css['top'] = px(y)
        css['bottom'] = px(ph - y - h)


def _has_overflow_child(node: dict) -> bool:
    """True if any visible child's bbox extends BELOW or to the RIGHT of the parent's bounds.
    Only bottom/right overflow blocks overflow:hidden — these indicate intentional decorative
    elements (e.g. arrow/caret indicators peeking below a pill).
    Top/left overflows (e.g. background images with negative top offset) are intentionally
    clipped by clipsContent=True frames, so they do NOT block overflow:hidden.
    """
    parent_bb = node.get('absoluteBoundingBox') or {}
    pw = parent_bb.get('width') or 0
    ph = parent_bb.get('height') or 0
    px_val = parent_bb.get('x') or 0
    py_val = parent_bb.get('y') or 0
    if pw <= 0 or ph <= 0:
        return False
    TOLERANCE = 2
    for child in (node.get('children') or []):
        if child.get('visible') is False:
            continue
        cbb = child.get('absoluteBoundingBox') or {}
        if not cbb:
            continue
        rel_x = (cbb.get('x') or 0) - px_val
        rel_y = (cbb.get('y') or 0) - py_val
        cw = cbb.get('width') or 0
        ch = cbb.get('height') or 0
        # Only check BOTTOM and RIGHT extension; top/left overflows are clipped by design.
        if rel_y + ch > ph + TOLERANCE or rel_x + cw > pw + TOLERANCE:
            return True
    return False


# ─── Flex container ────────────────────────────────────────────────────────────

def _extract_flex_container(css: dict, node: dict, ctx: dict = None) -> None:
    layout_mode = node.get('layoutMode')
    if not layout_mode or layout_mode == 'NONE':
        return

    _clamp = (ctx or {}).get('_adaptive_clamp', False)
    _unit = clamp_value if _clamp else px

    css['display'] = 'flex'
    # GRID = Figma wrap auto-layout (horizontal wrap); HORIZONTAL = row; VERTICAL = column
    if layout_mode in ('HORIZONTAL', 'GRID'):
        css['flex-direction'] = 'row'
    else:
        css['flex-direction'] = 'column'

    justify_map = {
        'MIN': 'flex-start',
        'CENTER': 'center',
        'MAX': 'flex-end',
        'SPACE_BETWEEN': 'space-between',
    }
    align_map = {
        'MIN': 'flex-start',
        'CENTER': 'center',
        'MAX': 'flex-end',
        'BASELINE': 'baseline',
    }

    # Only write justify-content/align-items when the design explicitly sets the value.
    # Absent → CSS default (flex-start for both, same as Figma default MIN) applies.
    primary = node.get('primaryAxisAlignItems')
    counter = node.get('counterAxisAlignItems')
    if primary:
        css['justify-content'] = justify_map.get(primary, 'flex-start')
    elif layout_mode == 'HORIZONTAL':
        # U-468: No explicit primaryAxisAlignItems. Infer center when padding is symmetric
        # and there is a single child. Symmetric L/R padding = designer centering intent;
        # single child excludes multi-item rows (nav bars) where center would be wrong.
        _pl = node.get('paddingLeft') or 0
        _pr = node.get('paddingRight') or 0
        _visible_children = [c for c in (node.get('children') or [])
                              if c.get('visible') is not False]
        if _pl == _pr and _pl > 0 and len(_visible_children) == 1:
            css['justify-content'] = 'center'
    counter_val = counter or 'MIN'
    css['align-items'] = align_map.get(counter_val, 'flex-start')

    if node.get('layoutWrap') == 'WRAP':
        css['flex-wrap'] = 'wrap'
        if node.get('itemSpacing'):
            css['column-gap'] = _unit(node['itemSpacing'])
        if node.get('counterAxisSpacing'):
            css['row-gap'] = _unit(node['counterAxisSpacing'])
        if node.get('counterAxisAlignContent') == 'SPACE_BETWEEN':
            css['align-content'] = 'space-between'
    else:
        # space-between: itemSpacing is meaningless in Figma; CSS gap under
        # space-between forces a minimum gap that doesn't match Figma behaviour.
        is_space_between = node.get('primaryAxisAlignItems') == 'SPACE_BETWEEN'
        item_spacing = node.get('itemSpacing') or 0
        if item_spacing > 0 and not is_space_between:
            css['gap'] = _unit(item_spacing)
        # negative itemSpacing = overlapping flex items; CSS gap cannot be negative, skip
        # U-320: layoutMode=GRID (Figma CSS Grid) uses gridColumnGap/gridRowGap instead of
        # itemSpacing. Read these fields to generate column-gap/row-gap for the flex fallback.
        # Real case: 6777:32842 Container — gridColumnGap=24 for 2-column card layout.
        elif layout_mode == 'GRID':
            _grid_col_gap = node.get('gridColumnGap') or 0
            _grid_row_gap = node.get('gridRowGap') or 0
            if _grid_col_gap > 0 and _grid_row_gap > 0 and _grid_col_gap == _grid_row_gap:
                css['gap'] = _unit(_grid_col_gap)
            elif _grid_col_gap > 0:
                css['column-gap'] = _unit(_grid_col_gap)
            if _grid_row_gap > 0 and _grid_row_gap != _grid_col_gap:
                css['row-gap'] = _unit(_grid_row_gap)

    pt = node.get('paddingTop') or 0
    pr = node.get('paddingRight') or 0
    pb = node.get('paddingBottom') or 0
    pl = node.get('paddingLeft') or 0

    if pt or pr or pb or pl:
        abb = node.get('absoluteBoundingBox') or {}
        w = node.get('width') if node.get('width') is not None else abb.get('width', 0) or 0
        h = node.get('height') if node.get('height') is not None else abb.get('height', 0) or 0
        h_pad_overflow = w > 0 and pl + pr > w
        v_pad_overflow = h > 0 and pt + pb > h
        if not h_pad_overflow and not v_pad_overflow:
            # Validate horizontal padding against actual child positions.
            # In Figma auto-layout, API-reported paddingLeft/Right may be larger than
            # the actual visual spacing when a child overflows the padding zone (e.g.
            # a centered child wider than the content area). Use the minimum actual
            # child offset when it's smaller than the API value.
            # Fix: Portal Revised node 6777:33071 — text child at 20px, not 40px.
            if (pl or pr) and abb:
                parent_x = abb.get('x', 0) or 0
                _is_horizontal_layout = node.get('layoutMode') == 'HORIZONTAL'
                # In HORIZONTAL flex containers, children cannot physically overlap
                # in x-space. If they do, it indicates a special out-of-flow element
                # (e.g. a decorative logo that straddles the content boundary).
                # Such elements must be excluded from padding correction.
                # Note: VERTICAL layouts allow x-overlap (different-width children),
                # so we skip this detection for VERTICAL (e.g. U-318 Portal Revised).
                # Fix: Brand6PreKyc footer — "Brand" child at 158px overlaps "About"
                # child at 273.5px, incorrectly shrinking paddingLeft from 398px.
                _overlapping_ids: set = set()
                if _is_horizontal_layout:
                    _hflex_children = [
                        _c for _c in (node.get('children') or [])
                        if _c.get('layoutPositioning') != 'ABSOLUTE'
                        and (_c.get('absoluteBoundingBox') or {}).get('x') is not None
                    ]
                    for _i, _ci in enumerate(_hflex_children):
                        _ci_abb = _ci.get('absoluteBoundingBox') or {}
                        _ci_x = _ci_abb.get('x', 0)
                        _ci_w = _ci_abb.get('width', 0)
                        for _j, _cj in enumerate(_hflex_children):
                            if _i == _j:
                                continue
                            _cj_x = (_cj.get('absoluteBoundingBox') or {}).get('x', 0)
                            if _ci_x < _cj_x < _ci_x + _ci_w:
                                _overlapping_ids.add(_ci.get('id', id(_ci)))
                                _overlapping_ids.add(_cj.get('id', id(_cj)))
                _pl_api = pl  # remember API value to detect if pl was corrected
                for _c in (node.get('children') or []):
                    if _c.get('layoutPositioning') == 'ABSOLUTE':
                        continue
                    if _c.get('visible') is False:
                        continue
                    if _c.get('id', id(_c)) in _overlapping_ids:
                        continue
                    _cabb = _c.get('absoluteBoundingBox') or {}
                    _cx = _cabb.get('x')
                    _cw = _cabb.get('width')
                    if _cx is None or _cw is None:
                        continue
                    _actual_pl = _cx - parent_x
                    _actual_pr = (parent_x + w) - (_cx + _cw)
                    if pl > 0 and 0 < _actual_pl < pl:
                        pl = _actual_pl
                    if pr > 0 and 0 < _actual_pr < pr:
                        pr = _actual_pr
                # For centered HORIZONTAL layouts: symmetric padding is required for
                # correct centering. If only one side was corrected (asymmetric result),
                # equalize to the smaller value so both sides produce the same center.
                if (_is_horizontal_layout
                        and node.get('primaryAxisAlignItems') == 'CENTER'
                        and pl != pr and pl == _pl_api):
                    pl = pr  # pl was not corrected → match corrected pr for symmetry
            css['padding'] = ' '.join(_unit(v) for v in (pt, pr, pb, pl))

    has_radius = (
        (node.get('cornerRadius') or 0) > 0
        or (node.get('topLeftRadius') or 0) > 0
        or (node.get('topRightRadius') or 0) > 0
        or (node.get('bottomLeftRadius') or 0) > 0
        or (node.get('bottomRightRadius') or 0) > 0
    )
    if node.get('clipsContent') and has_radius and not _has_overflow_child(node):
        css['overflow'] = 'hidden'


# ─── Flex child ────────────────────────────────────────────────────────────────

def _extract_flex_child(css: dict, node: dict, ctx: dict) -> None:
    layout_mode = ctx.get('layoutMode')
    if layout_mode == 'NONE' or node.get('layoutPositioning') == 'ABSOLUTE':
        return

    if node.get('layoutGrow') == 1:
        css['flex-grow'] = '1'
        # In a horizontal flex container min-width:auto can prevent shrinking past
        # content size, causing row overflow. Force min-width:0 to allow it.
        if layout_mode in ('HORIZONTAL', 'GRID'):
            css['min-width'] = '0'

    is_horizontal_ctx = layout_mode in ('HORIZONTAL', 'GRID')
    # FIXED: explicit pixel size — must not shrink below designed value.
    # HUG:   sizes to content — Figma guarantees it always shows at content height/width;
    #        CSS flex-shrink:1 (default) can violate this by compressing the element when
    #        the parent runs out of space. Setting flex-shrink:0 preserves HUG semantics.
    no_shrink_sizing = ('FIXED', 'HUG')
    fixed_or_hug_on_main_axis = (
        (is_horizontal_ctx and node.get('layoutSizingHorizontal') in no_shrink_sizing)
        or (layout_mode == 'VERTICAL' and node.get('layoutSizingVertical') in no_shrink_sizing)
    )
    if fixed_or_hug_on_main_axis:
        css['flex-shrink'] = '0'

    align_self_map = {
        'STRETCH': 'stretch',
        'MIN': 'flex-start',
        'CENTER': 'center',
        'MAX': 'flex-end',
    }
    la = node.get('layoutAlign')
    if la and la != 'INHERIT' and la in align_self_map:
        css['align-self'] = align_self_map[la]

    if node.get('layoutSizingVertical') == 'FILL' and is_horizontal_ctx:
        # FILL-height in a row parent. When the node has vertical padding,
        # align-self:stretch can be overridden by min-height:auto (= content + padding)
        # causing the item to exceed the parent's cross-size. Use explicit height
        # (project uses global border-box, so height = total rendered size).
        _vpad = (node.get('paddingTop') or 0) + (node.get('paddingBottom') or 0)
        _abb = node.get('absoluteBoundingBox') or {}
        _bb_h = _abb.get('height', 0) or 0
        if _vpad > 0 and _bb_h > 0:
            css['height'] = px(_bb_h)
        else:
            css['align-self'] = 'stretch'


# ─── Visual properties ─────────────────────────────────────────────────────────

def _extract_visual(css: dict, node: dict, sass_vars: dict, var_id_to_bds: dict = None, style_id_to_bds: dict = None, force_css_fills: bool = False) -> None:
    _extract_fills(css, node, sass_vars, var_id_to_bds, style_id_to_bds, force_css_fills=force_css_fills)
    _extract_strokes(css, node)
    _extract_effects(css, node)
    _extract_corner_radius(css, node)


def _image_fill_filters_to_css(figma_filters: dict) -> str:
    """Convert Figma IMAGE fill adjustment filters to a CSS filter string."""
    parts = []
    sat = figma_filters.get('saturation', 0)
    if sat != 0:
        parts.append(f'saturate({round((1 + sat) * 100, 1)}%)')
    exposure = figma_filters.get('exposure', 0)
    if exposure != 0:
        parts.append(f'brightness({round((1 + exposure * 0.5) * 100, 1)}%)')
    contrast = figma_filters.get('contrast', 0)
    if contrast != 0:
        parts.append(f'contrast({round((1 + contrast) * 100, 1)}%)')
    highlights = figma_filters.get('highlights', 0)
    if highlights != 0:
        # Figma highlights targets only upper luminance (Lightroom-style non-linear curve).
        # Approximate: brightness + aggressive contrast to match perceived effect.
        parts.append(f'brightness({round((1 + highlights * 0.5) * 100, 1)}%)')
        if abs(highlights) > 0.1:
            parts.append(f'contrast({round((1 + abs(highlights) * 0.9) * 100, 1)}%)')
    shadows = figma_filters.get('shadows', 0)
    if shadows != 0:
        parts.append(f'brightness({round((1 + shadows * 0.25) * 100, 1)}%)')
    return ' '.join(parts)


def _extract_fills(css: dict, node: dict, sass_vars: dict, var_id_to_bds: dict = None, style_id_to_bds: dict = None, force_css_fills: bool = False) -> None:
    # node.styles.fill = Color Style ID (older Figma styling system, pre-Variables)
    node_fill_style_id = (node.get('styles') or {}).get('fill', '')
    fills = [f for f in (node.get('fills') or []) if f.get('visible') is not False]
    if not fills:
        if node.get('type') == 'TEXT':
            css['color'] = 'transparent'
        return

    # Format B: node.boundVariables.fills[i] stores the variable binding at the node level
    # (common in INSTANCE/COMPONENT overrides) rather than inside fill.boundVariables.color.
    # Promote node-level bindings into the fill object so _paint_to_css can resolve them.
    node_fills_bv = (node.get('boundVariables') or {}).get('fills', [])
    if node_fills_bv:
        enriched = []
        for i, fill in enumerate(fills):
            if i < len(node_fills_bv) and not (fill.get('boundVariables') or {}).get('color'):
                var_alias = node_fills_bv[i]
                if isinstance(var_alias, dict) and var_alias.get('id'):
                    fill = dict(fill)
                    fill['boundVariables'] = dict(fill.get('boundVariables') or {})
                    fill['boundVariables']['color'] = var_alias
            enriched.append(fill)
        fills = enriched

    # RECTANGLE with only IMAGE fills → renders as <img>, no background CSS needed.
    # Figma exports these nodes via /images (node ID), which BAKES IN IMAGE fill color
    # adjustments (exposure, highlights, shadows). DO NOT apply CSS filter — it would
    # double-process the adjustments and produce an over-processed image.
    if node.get('type') == 'RECTANGLE' and all(f.get('type') == 'IMAGE' for f in fills):
        return

    # VECTOR/BOOLEAN_OPERATION → exported as SVG with embedded fill.
    # Exception: BACKGROUND_BLUR effects make it a glassmorphism <div> that needs CSS fills.
    has_backdrop_blur = any(
        e.get('visible') is not False and e.get('type') == 'BACKGROUND_BLUR'
        for e in (node.get('effects') or [])
    )
    node_type = node.get('type')
    # force_css_fills=True: VECTOR rendered as div without SVG asset — needs CSS fills.
    if node_type in ('VECTOR', 'BOOLEAN_OPERATION') and not has_backdrop_blur and not force_css_fills:
        return

    abb = node.get('absoluteBoundingBox') or {}
    n_w = node.get('width') if node.get('width') is not None else abb.get('width', 1) or 1
    n_h = node.get('height') if node.get('height') is not None else abb.get('height', 1) or 1

    if len(fills) == 1:
        fill = fills[0]
        v = _paint_to_css(fill, sass_vars, var_id_to_bds, style_id_to_bds, node_fill_style_id, n_w, n_h)
        if v:
            fill_opacity = fill.get('opacity')
            is_image_with_opacity = (
                fill.get('type') == 'IMAGE'
                and fill_opacity is not None
                and fill_opacity < 0.999
                and node_type != 'RECTANGLE'
            )
            if fills[0].get('type') == 'SOLID':
                prop = 'color' if node_type == 'TEXT' else 'background-color'
                if prop == 'background-color' and 'bds-gray-t1-title' in v:
                    _bg_hex = ''
                    c = fills[0].get('color')
                    if c:
                        _bg_hex = figma_color_to_hex(c)
                    v = _revert_text_token_for_bg(v, _bg_hex)
                css[prop] = v
                if prop == 'background-color' and v.startswith('var('):
                    c = fills[0].get('color')
                    if c:
                        css['_rawBgHex'] = figma_color_to_hex(c)
                if prop == 'color' and v.startswith('var('):
                    c = fills[0].get('color')
                    if c:
                        css['_rawColorHex'] = figma_color_to_hex(c)
            elif node_type == 'TEXT':
                # Gradient text fill: use background-image (not background shorthand) to avoid
                # postcss-rtlcss shifting background-clip when the shorthand resets it.
                css['background-image'] = v
                css['background-color'] = 'transparent'
                css['-webkit-background-clip'] = 'text'
                css['background-clip'] = 'text'
                css['-webkit-text-fill-color'] = 'transparent'
                css['color'] = 'transparent'
            elif is_image_with_opacity:
                css['_before:background'] = v
                css['_before:opacity'] = str(round(fill_opacity, 3))
            else:
                css['background'] = v
    else:
        # Reverse so bottom fill is first (CSS background layers: first = topmost)
        parts = [p for p in (_paint_to_css(f, sass_vars, var_id_to_bds, style_id_to_bds, node_fill_style_id, n_w, n_h) for f in reversed(fills)) if p]
        if parts:
            if node_type == 'TEXT':
                is_gradient = any(f.get('type', '').startswith('GRADIENT_') for f in fills)
                if is_gradient:
                    # Pick the gradient value from the reversed list
                    reversed_fills = list(reversed(fills))
                    g_value = next(
                        (parts[i] for i, f in enumerate(reversed_fills) if f.get('type', '').startswith('GRADIENT_')),
                        parts[0],
                    )
                    css['background-image'] = g_value
                    css['background-color'] = 'transparent'
                    css['-webkit-background-clip'] = 'text'
                    css['background-clip'] = 'text'
                    css['-webkit-text-fill-color'] = 'transparent'
                    css['color'] = 'transparent'
                else:
                    css['color'] = parts[0]
            else:
                css['background'] = ', '.join(parts)


def _paint_to_css(paint: dict, sass_vars: dict, var_id_to_bds: dict = None, style_id_to_bds: dict = None, node_fill_style_id: str = '', node_w: float = 1, node_h: float = 1):
    if paint.get('visible') is False:
        return None
    opacity = paint.get('opacity') if paint.get('opacity') is not None else 1
    paint_type = paint.get('type')

    if paint_type == 'SOLID':
        color = paint.get('color')
        if not color:
            return None
        bv = paint.get('boundVariables') or {}
        color_var = bv.get('color') or {}
        var_id = color_var.get('id')

        # P0a: direct BDS token via Figma Variable binding (fill.boundVariables.color.id)
        # Tries full ID first, then VARKEY:{40hexkey} prefix match for cross-file library vars.
        # Cross-file format: "VariableID:{varKey}/{localNodeId}" — varKey is globally stable.
        if var_id and var_id_to_bds:
            bds_token = var_id_to_bds.get(var_id)
            if not bds_token and '/' in var_id:
                var_key = var_id.replace('VariableID:', '').rsplit('/', 1)[0]
                bds_token = var_id_to_bds.get(f'VARKEY:{var_key}')
            if bds_token:
                # U-319: BDS -bg tokens are designed to stand at full opacity.
                # Low fill opacity on these tokens is typically a Figma export artifact
                # (component instance fills), not the designer's intent.
                if opacity >= 1 or bds_token.endswith('-bg'):
                    return f'var({bds_token})'
                return f'color-mix(in srgb, var({bds_token}) {round(opacity * 100)}%, transparent)'

        # P0b: BDS token via Figma Color Style (node.styles.fill = styleId)
        # Theme-sensitive text tokens (t2-t5) are blocked here — they come from
        # shared color styles, not explicit variable bindings, and flip drastically
        # between light/dark themes making text invisible.
        if node_fill_style_id and style_id_to_bds and node_fill_style_id in style_id_to_bds:
            bds_token = style_id_to_bds[node_fill_style_id]
            # U-319: -bg tokens always full opacity (same reasoning as variable binding path above)
            token_var = f'var({bds_token})' if (opacity >= 1 or bds_token.endswith('-bg')) else f'color-mix(in srgb, var({bds_token}) {round(opacity * 100)}%, transparent)'
            if _is_theme_sensitive_text_token(token_var):
                pass  # fall through to hex path
            else:
                return token_var

        # P1: Figma variable exists but not in BDS mapping — record miss, fall through
        if var_id and var_id_to_bds is not None:
            _record_var_miss(var_id)

        # P2: existing hex path (token_resolver will handle BDS substitution later)
        sass_var = sass_vars.get(var_id) if var_id else None
        return figma_color_to_css(color, opacity, sass_var)

    elif paint_type == 'GRADIENT_LINEAR':
        handles = paint.get('gradientHandlePositions') or []
        if len(handles) < 2:
            return None
        h0, h1 = handles[0], handles[1]
        W = node_w or 1
        H = node_h or 1
        dx = (h1['x'] - h0['x']) * W
        dy = (h1['y'] - h0['y']) * H

        angle_rad = math.atan2(dx, -dy)
        deg = round((rad_to_deg(angle_rad) + 360) % 360, 1)

        # CSS gradient line length
        L = abs(W * math.sin(angle_rad)) + abs(H * math.cos(angle_rad))
        # CSS gradient line start (magic corner)
        start_x = W / 2 - L / 2 * math.sin(angle_rad)
        start_y = H / 2 + L / 2 * math.cos(angle_rad)

        stop_parts = []
        for s in (paint.get('gradientStops') or []):
            t = s['position']
            px_ = (h0['x'] + t * (h1['x'] - h0['x'])) * W
            py_ = (h0['y'] + t * (h1['y'] - h0['y'])) * H
            proj = (px_ - start_x) * math.sin(angle_rad) + (py_ - start_y) * (-math.cos(angle_rad))
            pct = round(proj / L * 100, 2) if L != 0 else 0
            color_str = figma_color_to_css(s['color'], opacity)
            stop_parts.append(f'{color_str} {pct:g}%')

        return f'linear-gradient({deg:g}deg, {", ".join(stop_parts)})'

    elif paint_type == 'GRADIENT_RADIAL':
        handles = paint.get('gradientHandlePositions') or []
        if len(handles) < 3:
            return None
        c, rx, ry = handles[0], handles[1], handles[2]
        rw = round(math.hypot(rx['x'] - c['x'], rx['y'] - c['y']) * 100, 1)
        rh = round(math.hypot(ry['x'] - c['x'], ry['y'] - c['y']) * 100, 1)
        stops = ', '.join(
            f'{figma_color_to_css(s["color"], opacity)} {round(s["position"] * 100, 1):g}%'
            for s in (paint.get('gradientStops') or [])
        )
        cx = round(c['x'] * 100, 1)
        cy = round(c['y'] * 100, 1)
        return f'radial-gradient(ellipse {rw:g}% {rh:g}% at {cx:g}% {cy:g}%, {stops})'

    elif paint_type == 'GRADIENT_ANGULAR':
        handles = paint.get('gradientHandlePositions') or []
        if len(handles) < 2:
            return None
        c, h = handles[0], handles[1]
        deg = round(rad_to_deg(math.atan2(h['y'] - c['y'], h['x'] - c['x'])), 1)
        stops = ', '.join(
            f'{figma_color_to_css(s["color"], opacity)} {round(s["position"] * 100, 1):g}%'
            for s in (paint.get('gradientStops') or [])
        )
        cx = round(c['x'] * 100, 1)
        cy = round(c['y'] * 100, 1)
        return f'conic-gradient(from {deg:g}deg at {cx:g}% {cy:g}%, {stops})'

    elif paint_type == 'IMAGE':
        size_map = {'FILL': 'cover', 'FIT': 'contain', 'TILE': 'auto', 'STRETCH': '100% 100%'}
        scale_mode = paint.get('scaleMode') or 'FILL'
        size = size_map.get(scale_mode, 'cover')
        repeat = 'repeat' if scale_mode == 'TILE' else 'no-repeat'
        pos = 'center'
        # STRETCH with imageTransform: compute exact background-size/position from
        # the 2×3 affine matrix [[a,b,tx],[c,d,ty]] (no rotation: b=c=0).
        # Formula: size = (100/a)% (100/d)%  position = (-tx/a*100)% (-ty/d*100)%
        transform = paint.get('imageTransform')
        if scale_mode == 'STRETCH' and transform and len(transform) == 2:
            try:
                a  = transform[0][0] or 1.0
                tx = transform[0][2] or 0.0
                d  = transform[1][1] or 1.0
                ty = transform[1][2] or 0.0
                sw = round(100 / a, 2)
                sh = round(100 / d, 2)
                # Correct formula: pos% = -t / (scale - 1) * 100
                # (not -t/scale*100 which gives the wrong offset when scale ≠ 1)
                px_pos = round(-tx / (a - 1.0) * 100, 2) if abs(a - 1.0) > 1e-6 else 0.0
                py_pos = round(-ty / (d - 1.0) * 100, 2) if abs(d - 1.0) > 1e-6 else 0.0
                size = f'{sw}% {sh}%'
                pos  = f'{px_pos}% {py_pos}%'
            except (IndexError, ZeroDivisionError, TypeError):
                pass
        return f"{pos} / {size} {repeat} url('')"

    return None


# ─── Strokes ───────────────────────────────────────────────────────────────────

def _extract_strokes(css: dict, node: dict) -> None:
    node_type = node.get('type')
    abb = node.get('absoluteBoundingBox') or {}

    # Glassmorphism flag — needed for SVG early-return check and gradient border logic.
    has_backdrop_blur = any(
        e.get('visible') is not False and e.get('type') == 'BACKGROUND_BLUR'
        for e in (node.get('effects') or [])
    )

    # VECTOR/etc rendered as SVG img: strokes are embedded, skip CSS border.
    # Line-type vectors (w≤1 || h≤1) fall through for CSS border.
    # Exception: BOOLEAN_OPERATION with BACKGROUND_BLUR is a glassmorphism <div>
    # (not an SVG img), so its stroke must be extracted as CSS border/box-shadow.
    if node_type in ('VECTOR', 'BOOLEAN_OPERATION', 'REGULAR_POLYGON', 'STAR'):
        if not (node_type == 'BOOLEAN_OPERATION' and has_backdrop_blur):
            vw = node.get('width') if node.get('width') is not None else abb.get('width', 0)
            vh = node.get('height') if node.get('height') is not None else abb.get('height', 0)
            if (vw or 0) > 1 and (vh or 0) > 1:
                return  # SVG img — stroke already in SVG
            # Line-type vector with percentage positioning (inside icon SVG group) —
            # stroke is embedded in parent SVG export, CSS border would create artifacts.
            if isinstance(css.get('left'), str) and css['left'].endswith('%'):
                return
            if isinstance(css.get('top'), str) and css['top'].endswith('%'):
                return
            if isinstance(css.get('width'), str) and css['width'].endswith('%'):
                return

    strokes = [f for f in (node.get('strokes') or []) if f.get('visible') is not False]
    if not strokes:
        return

    stroke = strokes[0]
    stroke_type = stroke.get('type')

    if stroke_type != 'SOLID' or not stroke.get('color'):
        # Gradient stroke: approximate with box-shadow using mid-stop color.
        # Glassmorphism BOOLEAN_OPERATION: use subtle inset glow to avoid overpowering.
        if stroke_type and stroke_type.startswith('GRADIENT_'):
            stops = stroke.get('gradientStops') or []
            if stops:
                gw = max(1, round(node.get('strokeWeight') or 1))
                existing_shadow = css.get('box-shadow', '')
                if has_backdrop_blur and node_type == 'BOOLEAN_OPERATION':
                    brightest = max(stops, key=lambda s: s['color'].get('r', 0) + s['color'].get('g', 0) + s['color'].get('b', 0))
                    r = round(brightest['color'].get('r', 0) * 255)
                    g = round(brightest['color'].get('g', 0) * 255)
                    b = round(brightest['color'].get('b', 0) * 255)
                    shadow_rule = f'inset 0 0 0 {gw}px rgba({r}, {g}, {b}, 0.25)'
                else:
                    mid = stops[len(stops) // 2]
                    grad_color = figma_color_to_css(mid['color'], mid.get('opacity') or 1)
                    shadow_rule = f'0 0 0 {gw}px {grad_color}'
                css['box-shadow'] = f'{existing_shadow}, {shadow_rule}' if existing_shadow else shadow_rule
        return

    color = figma_color_to_css(stroke['color'], stroke.get('opacity') if stroke.get('opacity') is not None else 1)
    align = node.get('strokeAlign') or 'INSIDE'

    isw = node.get('individualStrokeWeights') or {}
    has_individual = any(
        node.get(k) is not None
        for k in ('strokeTopWeight', 'strokeRightWeight', 'strokeBottomWeight', 'strokeLeftWeight')
    ) or any(isw.get(k) is not None for k in ('top', 'right', 'bottom', 'left'))

    def_w = 0 if has_individual else (node.get('strokeWeight') or 0)
    top = node.get('strokeTopWeight') if node.get('strokeTopWeight') is not None else (isw.get('top') if isw.get('top') is not None else def_w)
    right = node.get('strokeRightWeight') if node.get('strokeRightWeight') is not None else (isw.get('right') if isw.get('right') is not None else def_w)
    bottom = node.get('strokeBottomWeight') if node.get('strokeBottomWeight') is not None else (isw.get('bottom') if isw.get('bottom') is not None else def_w)
    left = node.get('strokeLeftWeight') if node.get('strokeLeftWeight') is not None else (isw.get('left') if isw.get('left') is not None else def_w)

    is_uniform = top == right == bottom == left
    w = top if is_uniform else 0
    style = 'dashed' if (node.get('strokeDashes') or []) else 'solid'
    has_radius = bool(node.get('cornerRadius') or node.get('topLeftRadius'))

    def spx(v):
        return f'{max(1, round(v))}px' if v > 0 else '0px'

    if align == 'OUTSIDE':
        shadow = f'0 0 0 {spx(w)} {color}'
        existing = css.get('box-shadow', '')
        css['box-shadow'] = f'{existing}, {shadow}' if existing else shadow
        if not has_radius:
            css['outline'] = f'{spx(w)} {style} {color}'
            css['outline-offset'] = '0'
            del css['box-shadow']
    else:
        if is_uniform:
            css['border'] = f'{spx(w)} {style} {color}'
        else:
            if top:
                css['border-top'] = f'{spx(top)} {style} {color}'
            if right:
                css['border-right'] = f'{spx(right)} {style} {color}'
            if bottom:
                css['border-bottom'] = f'{spx(bottom)} {style} {color}'
            if left:
                css['border-left'] = f'{spx(left)} {style} {color}'


# ─── Effects ───────────────────────────────────────────────────────────────────

def _extract_effects(css: dict, node: dict) -> None:
    effects = [e for e in (node.get('effects') or []) if e.get('visible') is not False]
    if not effects:
        return

    is_text = node.get('type') == 'TEXT'
    shadows = [css['box-shadow']] if css.get('box-shadow') else []
    text_shadows = []
    filters = []
    backdrops = []

    for e in effects:
        e_type = e.get('type')
        if not e.get('color') and e_type in ('DROP_SHADOW', 'INNER_SHADOW'):
            continue
        color = figma_color_to_css(e['color'], 1) if e.get('color') else ''
        offset = e.get('offset') or {}
        x = offset.get('x', 0)
        y = offset.get('y', 0)
        radius = e.get('radius') or 0

        if e_type == 'DROP_SHADOW':
            if is_text:
                text_shadows.append(f'{px(x)} {px(y)} {px(radius)} {color}')
            elif not e.get('showShadowBehindNode', True):
                # showShadowBehindNode=false: Figma renders shadow only on the node's
                # pixel outline. CSS equivalent is filter:drop-shadow (follows actual
                # rendered pixels), NOT box-shadow (always rectangular).
                filters.append(f'drop-shadow({px(x)} {px(y)} {px(radius)} {color})')
            else:
                spread = e.get('spread') or 0
                shadows.append(f'{px(x)} {px(y)} {px(radius)} {px(spread)} {color}')
        elif e_type == 'INNER_SHADOW':
            if not is_text:
                _has_visible_fill = any(
                    f.get('visible', True) and (
                        f.get('type') != 'SOLID' or (f.get('color') or {}).get('a', 1) > 0.01
                    )
                    for f in (node.get('fills') or [])
                )
                if _has_visible_fill:
                    shadows.append(f'inset {px(x)} {px(y)} {px(radius)} {color}')
        elif e_type == 'LAYER_BLUR':
            filters.append(f'blur({px(radius)})')
        elif e_type == 'BACKGROUND_BLUR':
            # PROGRESSIVE blur fades from startRadius→radius and has no CSS equivalent.
            # Emitting blur(radius) produces a uniform full-strength blur that looks wrong
            # at the start edge (where Figma shows 0 blur). Strip it entirely.
            if e.get('blurType') != 'PROGRESSIVE':
                backdrops.append(f'blur({px(radius)})')

    if text_shadows:
        css['text-shadow'] = ', '.join(text_shadows)
    if filters:
        css['filter'] = ' '.join(filters)
    if backdrops:
        css['backdrop-filter'] = ' '.join(backdrops)
        css['-webkit-backdrop-filter'] = ' '.join(backdrops)
        # Glassmorphism: white gradient bg on white page makes top edge invisible.
        # Add a 1px ring shadow to make the boundary visible without affecting
        # box-model or border-radius clipping. Check node fields directly since
        # extractCornerRadius hasn't run yet.
        node_has_radius = (
            (node.get('cornerRadius') or 0) > 0
            or (node.get('topLeftRadius') or 0) > 0
            or (node.get('topRightRadius') or 0) > 0
            or (node.get('bottomLeftRadius') or 0) > 0
            or (node.get('bottomRightRadius') or 0) > 0
        )
        has_fill = bool(css.get('background') or css.get('background-image') or css.get('background-color'))
        if node_has_radius and has_fill and not css.get('border') and not css.get('outline'):
            shadows.append('0 0 0 1px rgba(0, 0, 0, 0.12)')

    if shadows:
        css['box-shadow'] = ', '.join(shadows)


# ─── Corner radius ─────────────────────────────────────────────────────────────

def _has_gradient_fill_descendant(node: dict, max_depth: int = 2) -> bool:
    """Return True if any visible descendant (up to max_depth) has a gradient fill.

    When a container uses gradient-fill children as visual overlays (soft fade/shadow),
    adding overflow:hidden would hard-clip those overlays, producing a harsh visual edge.
    Detecting gradient descendants lets us skip overflow:hidden and let the gradient do
    the masking instead.
    """
    if max_depth <= 0:
        return False
    for c in (node.get('children') or []):
        if c.get('visible') is False:
            continue
        fills = [f for f in (c.get('fills') or []) if f.get('visible') is not False]
        if any('GRADIENT' in (f.get('type') or '') for f in fills):
            return True
        if _has_gradient_fill_descendant(c, max_depth - 1):
            return True
    return False


def _extract_corner_radius(css: dict, node: dict) -> None:
    node_type = node.get('type')
    if node_type == 'ELLIPSE':
        css['border-radius'] = '50%'
        return

    # VECTOR nodes rendered as SVG/img: skip CSS border-radius because
    # the rounded shape is encoded in the SVG path data.
    # Exception: VECTORs with BACKGROUND_BLUR are rendered as <div> (not SVG),
    # so CSS border-radius IS needed to match Figma's rendered appearance.
    if node_type == 'VECTOR':
        _has_bdblur = any(
            e.get('visible') is not False and e.get('type') == 'BACKGROUND_BLUR'
            for e in (node.get('effects') or [])
        )
        if not _has_bdblur:
            return
        # Fall through: backdrop-blur VECTOR <div> needs CSS border-radius

    tl = node.get('topLeftRadius')
    tr = node.get('topRightRadius')
    br = node.get('bottomRightRadius')
    bl = node.get('bottomLeftRadius')

    # Figma may return 'rectangleCornerRadii' array [TL, TR, BR, BL] instead of individual fields
    # (common for INSTANCE nodes and nodes with non-uniform radii)
    if all(v is None for v in (tl, tr, br, bl)):
        rcr = node.get('rectangleCornerRadii')
        if rcr and len(rcr) == 4:
            tl, tr, br, bl = rcr

    if any(v is not None for v in (tl, tr, br, bl)):
        # Skip all-zero radius (e.g. GROUP with rectangleCornerRadii=[0,0,0,0]):
        # explicit "border-radius: 0px" is default CSS and adds noise.
        if not all((v or 0) == 0 for v in (tl, tr, br, bl)):
            css['border-radius'] = ' '.join(px(v or 0) for v in (tl, tr, br, bl))
    elif node.get('cornerRadius') is not None and (node.get('cornerRadius') or 0) > 0:
        css['border-radius'] = px(node['cornerRadius'])

    container_types = {'FRAME', 'COMPONENT', 'INSTANCE', 'COMPONENT_SET', 'GROUP'}
    is_container = node_type in container_types

    # Container with border-radius: add overflow:hidden for corner clipping (Figma default).
    # Rounded container with clipsContent=True/unspecified → clip children to rounded shape.
    # When clipsContent=False (e.g. speech-bubble pill with outflowing arrow pointer),
    # the designer explicitly wants children to overflow. DO NOT add overflow:hidden.
    # Exception: GROUP nodes with border-radius always need overflow:hidden because
    # their clipsContent=False is a default (GROUP has no clip toggle in Figma UI),
    # and border-radius without overflow:hidden causes corner leaks.
    if css.get('border-radius') and is_container:
        if node_type == 'GROUP' or node.get('clipsContent') is not False:
            css['overflow'] = 'hidden'

    # No radius but clipsContent explicitly true: designer enabled Clip Content.
    # clipsContent=True is an explicit designer choice — always add overflow:hidden.
    # When gradient-fill descendants exist (e.g. shadow overlay), the gradient direction
    # is correctly baked via _adjust_gradient_rotation so the clip boundary is visually
    # invisible (gradient reaches solid black exactly at the clip edge). No exception needed.
    if (not css.get('border-radius') and node.get('clipsContent') is True
            and is_container):
        css['overflow'] = 'hidden'

    # Inherit border-radius from full-coverage rounded child when parent has
    # backdrop-filter but no own radius. Without this, the backdrop blur/filter
    # renders as a rectangle while the visual pill shape is only on the child.
    if not css.get('border-radius') and css.get('backdrop-filter') and is_container:
        for child in (node.get('children') or []):
            child_radius = child.get('cornerRadius') or 0
            if child_radius > 0:
                child_abb = child.get('absoluteBoundingBox') or {}
                node_abb = node.get('absoluteBoundingBox') or {}
                cw = child_abb.get('width', 0)
                ch = child_abb.get('height', 0)
                nw = node_abb.get('width', 0)
                nh = node_abb.get('height', 0)
                if nw > 0 and nh > 0 and cw >= nw * 0.95 and ch >= nh * 0.95:
                    css['border-radius'] = px(child_radius)
                    css['overflow'] = 'hidden'
                    break


# ─── Typography ────────────────────────────────────────────────────────────────

def _extract_typography(css: dict, node: dict, ctx: dict = None) -> None:
    s = node.get('style')
    if not s:
        return

    _clamp = (ctx or {}).get('_adaptive_clamp', False)
    _unit = clamp_value if _clamp else px

    if s.get('fontSize'):
        css['font-size'] = _unit(s['fontSize'])
    if s.get('fontWeight'):
        css['font-weight'] = str(s['fontWeight'])
    if s.get('fontFamily'):
        css['font-family'] = f"'{s['fontFamily']}', sans-serif"
    if s.get('fontStyle') and 'italic' in s['fontStyle'].lower():
        css['font-style'] = 'italic'

    lh_unit = s.get('lineHeightUnit')
    if lh_unit == 'PIXELS' and s.get('lineHeightPx'):
        css['line-height'] = _unit(s['lineHeightPx'])
    elif lh_unit in ('PERCENT', 'FONT_SIZE_%') and s.get('lineHeightPercentFontSize'):
        css['line-height'] = str(round(s['lineHeightPercentFontSize'] / 100, 3))
    elif lh_unit == 'AUTO':
        css['line-height'] = 'normal'

    ls = s.get('letterSpacing')
    if ls is not None:
        if s.get('letterSpacingUnit') == 'PERCENT':
            ls_px = round((ls / 100) * (s.get("fontSize") or 16), 2)
            css['letter-spacing'] = _unit(ls_px)
        else:
            css['letter-spacing'] = _unit(ls)

    text_align_map = {'LEFT': 'left', 'CENTER': 'center', 'RIGHT': 'right', 'JUSTIFIED': 'justify'}
    if s.get('textAlignHorizontal'):
        css['text-align'] = text_align_map.get(s['textAlignHorizontal'], 'left')

    text_case_map = {'UPPER': 'uppercase', 'LOWER': 'lowercase', 'TITLE': 'capitalize'}
    text_case = s.get('textCase')
    if text_case and text_case != 'ORIGINAL':
        if text_case == 'SMALL_CAPS':
            css['font-variant'] = 'small-caps'
        elif text_case in text_case_map:
            css['text-transform'] = text_case_map[text_case]

    if s.get('textDecoration') == 'UNDERLINE':
        css['text-decoration'] = 'underline'
    elif s.get('textDecoration') == 'STRIKETHROUGH':
        css['text-decoration'] = 'line-through'

    if s.get('paragraphIndent'):
        css['text-indent'] = _unit(s['paragraphIndent'])

    tar = s.get('textAutoResize') or 'NONE'
    chars = node.get('characters') or ''

    # Check for explicit newlines (U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR, \n)
    has_newlines = '\n' in chars or any(
        ord(c) in (0x2028, 0x2029) for c in chars
    )

    # List nodes (UNORDERED/ORDERED) are rendered as <ul>/<ol>+<li> in TSX;
    # the <li> element handles line separation so white-space: pre-line is not needed.
    _line_types = node.get('lineTypes') or []
    _is_list_node = any(t in ('UNORDERED', 'ORDERED') for t in _line_types)

    if _is_list_node:
        _unique = set(_line_types)
        css['list-style-type'] = 'decimal' if _unique == {'ORDERED'} else 'disc'
        css['padding-left'] = '1.5em'
    elif has_newlines:
        if tar == 'WIDTH_AND_HEIGHT':
            # Figma sized the box to fit content exactly; browser font-metrics differ slightly.
            # Use 'pre' to preserve \n without allowing word-wrap to create extra lines.
            css['white-space'] = 'pre'
        else:
            # Fixed/HEIGHT box: preserve newlines but allow word-wrap within the fixed width.
            css['white-space'] = 'pre-line'

    # WIDTH_AND_HEIGHT without newlines: text box expands with content = always single line.
    # Force nowrap to prevent browser font-metric differences causing line breaks.
    if tar == 'WIDTH_AND_HEIGHT' and not has_newlines:
        css['white-space'] = 'nowrap'

    # HEIGHT without whitespace characters: single-word text like "3M+", "Users".
    # In a flex-column HUG container, without nowrap the minimum content width degrades
    # to a single character (~15px), collapsing the container.
    # Exclude texts with hyphens (natural CSS break points) that Figma wraps.
    if tar == 'HEIGHT' and not has_newlines and chars and ' ' not in chars.strip() and '-' not in chars:
        css['white-space'] = 'nowrap'

    # HEIGHT / FIXED single-line text WITH spaces: if node height ≈ lineHeight (single line in
    # Figma), add nowrap to prevent browser font-metric differences from wrapping the text.
    # Also covers textAutoResize=None (fixed-size box) — Figma may omit the field for fixed nodes,
    # but height≤lineHeight*1.2 reliably identifies single-line content. (Fix: node 6777:33242)
    _lh_nowrap = s.get('lineHeightPx') or 0
    _node_h = node.get('height') or (node.get('absoluteBoundingBox') or {}).get('height', 0) or 0
    if (tar in ('HEIGHT', None, 'NONE') and not has_newlines and chars and ' ' in chars
            and _lh_nowrap > 0 and _node_h > 0 and _node_h <= _lh_nowrap * 1.2
            and css.get('white-space') != 'nowrap'):
        css['white-space'] = 'nowrap'

    if tar == 'TRUNCATE' and s.get('textTruncation') == 'ENDING':
        lh_px = s.get('lineHeightPx') or 0
        if not lh_px:
            lh_str = css.get('line-height', '0')
            lh_px = float(lh_str) if lh_str.replace('.', '', 1).isdigit() else 0
        nod_h = node.get('height') if node.get('height') is not None else (node.get('absoluteBoundingBox') or {}).get('height', 0) or 0
        inferred_max_lines = int(nod_h / lh_px) if (lh_px > 0 and nod_h > 0) else 1
        max_ln = s.get('maxLines') if s.get('maxLines') is not None else inferred_max_lines
        if max_ln > 1:
            css['display'] = '-webkit-box'
            css['-webkit-line-clamp'] = str(max_ln)
            css['-webkit-box-orient'] = 'vertical'
            css['overflow'] = 'hidden'
        else:
            css['white-space'] = 'nowrap'
            css['text-overflow'] = 'ellipsis'
            css['overflow'] = 'hidden'


# ── BDS text-token revert for background-color ─────────────────────────────

_T1_TITLE_DARK = '#ffffff'
_T1_TITLE_LIGHT = '#121214'


def _revert_text_token_for_bg(value: str, raw_hex: str = '') -> str:
    """bds-gray-t1-title 用作 background-color 时追加 -revert。"""
    if 'bds-gray-t1-title' not in value:
        return value
    if raw_hex:
        normalized = raw_hex.lower().strip()
        if normalized in (_T1_TITLE_DARK, _T1_TITLE_LIGHT):
            return value
    return value.replace('bds-gray-t1-title', 'bds-gray-t1-title-revert')
