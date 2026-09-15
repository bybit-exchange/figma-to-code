from __future__ import annotations
"""
Script Layer: build Intermediate Representation (IR) tree from raw Figma node data.
"""

import re
import copy
import math as _math

from .css_extractor import extract_css
from .layout_inference import infer_flex_layout, infer_page_flow, inferred_flex_to_css
from .node_classifier import classify_children, convert_to_flow, should_skip_classifier
from .color import figma_color_to_css, px as _px


def _is_empty_render_node(node: dict) -> bool:
    """Return True if this node has no visible pixel output and should be excluded from DOM.

    Figma sets absoluteRenderBounds=None (key present, value null) for nodes that produce
    no visible pixels — this is Figma's FINAL verdict after considering masks, clips, and
    actual pixel output. A leaf node (no children, not TEXT) with this value never renders
    anything, regardless of what its fills/strokes properties say.

    Important: only triggers when 'absoluteRenderBounds' key is EXPLICITLY present with
    value None. If the key is absent (e.g., in mock data or older API responses), we
    cannot determine emptiness and return False (safe default).
    """
    if 'absoluteRenderBounds' not in node:
        return False
    if node['absoluteRenderBounds'] is not None:
        return False
    if node.get('type') == 'TEXT':
        return False
    if node.get('children'):
        return False
    # absoluteRenderBounds=null on a leaf node = Figma says zero visible pixels.
    return True


_VECTOR_LEAF_TYPES = frozenset({
    'VECTOR', 'BOOLEAN_OPERATION', 'LINE', 'STAR', 'REGULAR_POLYGON',
})
_VECTOR_CONTAINER_TYPES = frozenset({
    'GROUP', 'FRAME', 'INSTANCE', 'COMPONENT', 'COMPONENT_SET',
})


def _is_vector_only_subtree(node: dict, max_depth: int = 10) -> bool:
    """判断子树是否只由向量图形构成（适合整体导出为 SVG/PNG）。

    True 条件：所有可见叶节点都是向量类型（VECTOR/BOOLEAN_OPERATION/LINE/STAR/POLYGON）。
    容器类型（GROUP/FRAME/INSTANCE）本身不计入，递归检查其 children。
    RECTANGLE/ELLIPSE 不视为向量叶（常携带 IMAGE fill）。
    不可见节点被忽略。
    """
    if max_depth <= 0:
        return False
    if not node.get('visible', True):
        return True
    ntype = node.get('type', '')
    if ntype in _VECTOR_LEAF_TYPES:
        return True
    if ntype == 'TEXT':
        return False
    if ntype in ('RECTANGLE', 'ELLIPSE'):
        return False
    children = node.get('children') or []
    if not children and ntype not in _VECTOR_CONTAINER_TYPES:
        return False
    for child in children:
        if not child.get('visible', True):
            continue
        if not _is_vector_only_subtree(child, max_depth - 1):
            return False
    return True


def _extract_icon_fill_color(node: dict) -> str | None:
    """Extract dominant fill color from icon instance's child VECTOR nodes.
    Returns hex color string (e.g. '#ffffff') if a non-black solid fill is found,
    None otherwise (black is the default icon color, no need to specify)."""
    def _get_vectors(n):
        if n.get('type') in _VECTOR_LEAF_TYPES:
            yield n
        for c in (n.get('children') or []):
            if c.get('visible', True):
                yield from _get_vectors(c)

    for vec in _get_vectors(node):
        for fill in (vec.get('fills') or []):
            if fill.get('type') == 'SOLID' and fill.get('visible', True):
                c = fill.get('color', {})
                r, g, b = int(c.get('r', 0) * 255), int(c.get('g', 0) * 255), int(c.get('b', 0) * 255)
                if (r, g, b) != (0, 0, 0):
                    return f'#{r:02x}{g:02x}{b:02x}'
    return None


def _parse_vertical_padding(padding_str: str) -> float:
    """Return vertical (top + bottom) padding total in px from a CSS padding shorthand string.

    Figma frame heights are border-box values; CSS min-height/height are content-box (this
    project has no global box-sizing:border-box reset). Subtracting vertical padding from the
    Figma height yields the correct CSS content-box value.

    Examples:
      '280px 16px 40px 16px' → 320.0   (top=280, bottom=40)
      '40px'                 → 80.0    (top=bottom=40)
      '40px 16px'            → 80.0    (top=bottom=40)
      '40px 16px 20px'       → 60.0    (top=40, bottom=20)
    """
    if not padding_str:
        return 0.0
    parts = padding_str.split()
    vals: list[float] = []
    for p in parts:
        try:
            vals.append(float(p.rstrip('px')))
        except (ValueError, AttributeError):
            vals.append(0.0)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0] * 2          # all sides equal
    if len(vals) == 2:
        return vals[0] * 2          # top/bottom = first value
    return vals[0] + vals[2]        # top + bottom (3- or 4-value shorthand)


def _fix_text_gap_overlays(flat: list) -> list:
    """
    Figma 'text gap + overlay' pattern:

    When a designer places a large overlay node O (e.g. '10%') over a text node T
    that has intentional blank-line gaps ( {2,}), the two elements appear to
    visually interleave: 'Earn up to / [10%] / cashback with Brand Card'.

    Geometrically, O's bbox is contained within a VERTICAL-layout FRAME sibling F,
    which in turn contains T. This causes _has_overlap to abort flex inference and
    make all children position:absolute — producing overlapping elements in CSS.

    Fix: adopt O into F by splitting T at the gap and inserting O between the parts.

    Detection criteria:
    - O is a TEXT node in flat whose absoluteBoundingBox is FULLY contained within
      a VERTICAL-layout FRAME sibling F's absoluteBoundingBox.
    - F has a TEXT child T with 2+ consecutive   characters.
    - O's Y range overlaps with T's Y range.
    """
    result = list(flat)

    vertical_frames = [
        n for n in result
        if n.get('type') in ('FRAME', 'COMPONENT', 'INSTANCE', 'GROUP')
        and n.get('layoutMode') == 'VERTICAL'
    ]
    text_orphans = [n for n in result if n.get('type') == 'TEXT']

    for o_node in text_orphans:
        o_abb = o_node.get('absoluteBoundingBox') or {}
        if not o_abb:
            continue
        ox1 = o_abb.get('x', 0)
        oy1 = o_abb.get('y', 0)
        ox2 = ox1 + o_abb.get('width', 0)
        oy2 = oy1 + o_abb.get('height', 0)

        for f_node in vertical_frames:
            f_abb = f_node.get('absoluteBoundingBox') or {}
            if not f_abb:
                continue
            fx1 = f_abb.get('x', 0)
            fy1 = f_abb.get('y', 0)
            fx2 = fx1 + f_abb.get('width', 0)
            fy2 = fy1 + f_abb.get('height', 0)

            # O must be fully contained within F
            if not (fx1 <= ox1 and ox2 <= fx2 and fy1 <= oy1 and oy2 <= fy2):
                continue

            # Find a TEXT child T in F with a multi-  gap whose Y overlaps O
            f_children = list(f_node.get('children') or [])
            for k, t_child in enumerate(f_children):
                if t_child.get('type') != 'TEXT':
                    continue
                chars = t_child.get('characters') or ''
                gap_match = re.search(r' {2,}', chars)
                if not gap_match:
                    continue
                t_abb = t_child.get('absoluteBoundingBox') or {}
                if not t_abb:
                    continue
                tty1 = t_abb.get('y', 0)
                tty2 = tty1 + t_abb.get('height', 0)
                # O's Y range must fall within T's Y range
                if not (tty1 <= oy1 and oy2 <= tty2):
                    continue

                # Split T at the gap
                gap_start = gap_match.start()
                gap_end = gap_match.end()
                text_above = chars[:gap_start]
                text_below = chars[gap_end:]

                t_above = copy.deepcopy(t_child)
                t_above['characters'] = text_above
                t_above['absoluteBoundingBox'] = {
                    'x': t_abb.get('x', 0),
                    'y': tty1,
                    'width': t_abb.get('width', 0),
                    'height': max(1.0, oy1 - tty1),
                }

                t_below = copy.deepcopy(t_child)
                t_below['characters'] = text_below
                t_below['absoluteBoundingBox'] = {
                    'x': t_abb.get('x', 0),
                    'y': oy2,
                    'width': t_abb.get('width', 0),
                    'height': max(1.0, tty2 - oy2),
                }

                # Replace T with [T_above, O, T_below] in F's children
                new_children = f_children[:k]
                if text_above:
                    new_children.append(t_above)
                new_children.append(o_node)
                if text_below:
                    new_children.append(t_below)
                new_children.extend(f_children[k + 1:])
                f_node['children'] = new_children

                # Remove O from the parent flat list
                if o_node in result:
                    result.remove(o_node)
                break  # T found and processed
            break  # F found and O adopted

    return result


def build_ir(nodes: list, sass_vars: dict = None, var_id_to_bds: dict = None, style_id_to_bds: dict = None, adaptive: bool = False, adaptive_clamp: bool = False) -> list:
    """Build an IR tree from a list of top-level Figma nodes."""
    if sass_vars is None:
        sass_vars = {}
    if var_id_to_bds is None:
        var_id_to_bds = {}
    if style_id_to_bds is None:
        style_id_to_bds = {}
    root_ctx = {
        'layoutMode': 'NONE',
        'width': 0,
        'height': 0,
        'absX': 0,
        'absY': 0,
        'isRoot': True,
        '_adaptive': adaptive,
        '_adaptive_clamp': adaptive_clamp,
    }
    return [
        ir
        for n in nodes
        if n.get('visible') is not False
        for ir in [_build_node(n, root_ctx, sass_vars, var_id_to_bds, style_id_to_bds)]
        if ir is not None
    ]


# ─── Node builder ──────────────────────────────────────────────────────────────

def _build_node(node: dict, ctx: dict, sass_vars: dict, var_id_to_bds: dict = None, style_id_to_bds: dict = None):
    if node.get('visible') is False or node.get('type') == 'SLICE':
        return None

    abb = node.get('absoluteBoundingBox') or {}
    w = node.get('width') if node.get('width') is not None else abb.get('width', 0)
    h = node.get('height') if node.get('height') is not None else abb.get('height', 0)
    w = w or 0
    h = h or 0

    # VECTOR/BOOLEAN_OPERATION nodes are exported as SVG assets with fills baked in.
    # Only force CSS fills when backdrop-blur is present (which forces <div> rendering).
    # For normal SVG exports: setting CSS background on <img> is wrong because:
    #   1. The fill is already encoded in the SVG file.
    #   2. CSS background shows through transparent areas of the SVG, corrupting the visual
    #      (e.g. a gradient-filled vector SVG with soft edges gets a CSS gradient overlay
    #      that bleeds beyond the intended visual shape).
    _nt = node.get('type', '')
    _has_bdblur = any(
        e.get('visible') is not False and e.get('type') == 'BACKGROUND_BLUR'
        for e in (node.get('effects') or [])
    )
    _fills = node.get('fills') or []
    _force_css_fills = (
        _nt in ('VECTOR', 'BOOLEAN_OPERATION')
        and _has_bdblur  # only backdrop-blur VECTORs render as <div> and need CSS fills
    )
    css = extract_css(node, ctx, sass_vars, var_id_to_bds, style_id_to_bds, force_css_fills=_force_css_fills)

    # Stroke info needed by children for position adjustment:
    # CSS `bottom`/`top` is measured from the PADDING EDGE, not the BORDER EDGE.
    # When the parent has an INSIDE stroke (border drawn inside the bbox), the
    # padding edge is offset inward by the border width. We pass this so that
    # _extract_position can compensate the BOTTOM/TOP offset by the border thickness.
    _visible_strokes = [s for s in (node.get('strokes') or []) if s.get('visible') is not False]
    _stroke_weight = node.get('strokeWeight', 0) or 0
    _stroke_align = node.get('strokeAlign', 'INSIDE') or 'INSIDE'

    child_ctx = {
        'layoutMode': node.get('layoutMode') or 'NONE',
        'width': w,
        'height': h,
        'absX': abb.get('x') if abb.get('x') is not None else ctx.get('absX', 0),
        'absY': abb.get('y') if abb.get('y') is not None else ctx.get('absY', 0),
        # Stroke metadata for position calculation (INSIDE border shifts the padding edge)
        'strokeWeight': _stroke_weight if _visible_strokes else 0,
        'strokeAlign': _stroke_align if _visible_strokes else 'CENTER',
        '_adaptive': ctx.get('_adaptive', False),
        '_adaptive_clamp': ctx.get('_adaptive_clamp', False),
        # Parent sizing mode — needed to prevent width:100% in HUG parents
        '_parentSizingH': node.get('layoutSizingHorizontal'),
        '_parentSizingV': node.get('layoutSizingVertical'),
    }

    visible_children = [
        c for c in (node.get('children') or [])
        if c.get('visible') is not False and not c.get('isMask') and not _is_empty_render_node(c)
    ]

    # Composite icon detection:
    # INSTANCE/COMPONENT/FRAME with all children being VECTOR types and no layoutMode
    # → export as a single SVG, don't recurse into children.
    VECTOR_CHILD_TYPES = {'VECTOR', 'BOOLEAN_OPERATION', 'REGULAR_POLYGON', 'STAR', 'LINE'}
    node_type = node.get('type')
    is_composite_icon = (
        node_type in ('INSTANCE', 'COMPONENT', 'FRAME')
        and not node.get('layoutMode')
        and len(visible_children) > 0
        and all(c.get('type') in VECTOR_CHILD_TYPES for c in visible_children)
    )

    parent_has_layout = bool(node.get('layoutMode') and node.get('layoutMode') != 'NONE')
    flat = [] if is_composite_icon else flatten_groups(
        visible_children, parent_has_layout, child_ctx['absX'], child_ctx['absY']
    )

    # Fix: adopt orphan text nodes that fall inside a VERTICAL-layout sibling frame
    # (Figma 'text gap + overlay' pattern — e.g. large '10%' over blank-line text)
    if not parent_has_layout and len(flat) >= 2:
        flat = _fix_text_gap_overlays(flat)

    # Children become absolute in two cases:
    # 1. Child has layoutPositioning=ABSOLUTE
    # 2. Parent has no layoutMode (NONE) — all children go through extractPosition's absolute branch
    parent_has_no_layout = not node.get('layoutMode') or node.get('layoutMode') == 'NONE'
    has_absolute_children = (
        any(c.get('layoutPositioning') == 'ABSOLUTE' for c in flat)
        or (parent_has_no_layout and len(flat) > 0)
    )
    if has_absolute_children and not css.get('position'):
        css['position'] = 'relative'

    # ── isVectorNode logic ──
    if is_composite_icon:
        is_vector_node = True
    elif node_type not in ('VECTOR', 'BOOLEAN_OPERATION', 'REGULAR_POLYGON', 'STAR'):
        is_vector_node = False
    else:
        has_backdrop_blur = any(
            e.get('visible') is not False and e.get('type') == 'BACKGROUND_BLUR'
            for e in (node.get('effects') or [])
        )
        if has_backdrop_blur:
            is_vector_node = False
        else:
            vw = node.get('width') if node.get('width') is not None else abb.get('width', 0)
            vh = node.get('height') if node.get('height') is not None else abb.get('height', 0)
            is_vector_node = (vw or 0) > 1 and (vh or 0) > 1

    # ── exportSettings: PNG → treat complex shape as raster image ──
    # Figma marks BOOLEAN_OPERATION nodes with exportSettings when the shape cannot be
    # accurately reconstructed in CSS (backdrop-filter + gradient stroke + non-rect path).
    _has_export_png = node_type == 'BOOLEAN_OPERATION' and any(
        s.get('format') == 'PNG' for s in (node.get('exportSettings') or [])
    )
    if _has_export_png:
        # Keep only layout/positioning CSS; all visual properties are baked into the PNG.
        _LAYOUT_KEYS = {
            'width', 'height', 'flex-shrink', 'flex', 'flex-grow', 'min-width',
            'position', 'left', 'top', 'right', 'bottom',
            'display', 'flex-direction', 'justify-content', 'align-items', 'gap',
            'overflow', 'max-width', 'max-height',
        }
        for k in list(css.keys()):
            if k not in _LAYOUT_KEYS:
                del css[k]
        is_vector_node = False

    # ── imageRef / fillImageRef ──
    fills = node.get('fills') or []
    image_fill = next(
        (f for f in fills if f.get('type') == 'IMAGE' and f.get('visible') is not False),
        None,
    )
    image_ref = image_fill.get('imageRef') if (image_fill and node_type == 'RECTANGLE') else None
    fill_image_ref = image_fill.get('imageRef') if (image_fill and node_type != 'RECTANGLE') else None

    ir = {
        'figmaId': node.get('id'),
        'figmaName': node.get('name'),
        'figmaType': node_type,
        'componentId': node.get('componentId'),
        'bb': {'width': abb.get('width', 0), 'height': abb.get('height', 0)} if abb else None,
        'isImageNode': (
            (node_type == 'RECTANGLE'
             and any(f.get('type') == 'IMAGE' and f.get('visible') is not False for f in fills))
            or _has_export_png
        ),
        'isVectorNode': is_vector_node,
        'isTextNode': node_type == 'TEXT',
        'isComponentInstance': node_type == 'INSTANCE',
        'figmaCounterAxisAlign': node.get('counterAxisAlignItems'),
        'textContent': (
            node['characters'].replace(' ', '\n').replace(' ', '\n')
            if node.get('characters') is not None
            else None
        ),
        'textSegments': _build_text_segments(node) if node_type == 'TEXT' else None,
        'textAutoResize': (
            (node.get('style') or {}).get('textAutoResize') if node_type == 'TEXT' else None
        ),
        'lineTypes': node.get('lineTypes') if node_type == 'TEXT' else None,
        'lineIndentations': node.get('lineIndentations') if node_type == 'TEXT' else None,
        'variants': _extract_variants(node),
        'imageRef': image_ref,
        'fillImageRef': fill_image_ref,
        'css': css,
        'children': [],
    }

    # Composite icons: no child recursion — Figma Export API handles the SVG.
    if ir['isVectorNode'] and is_composite_icon:
        # Extract icon fill color from child VECTOR nodes
        _icon_fill = _extract_icon_fill_color(node)
        if _icon_fill:
            ir['iconColor'] = _icon_fill
        return ir

    # exportSettings PNG nodes: children are baked into the exported image; skip recursion.
    if _has_export_png:
        return ir

    # Layout inference for no-layout parents with ≥1 children
    inferred = None  # declared before block so post-processing below can access it
    _flow_result = None  # constraint-driven flow conversion result
    classification = None  # node classification result (background/content/etc.)
    if not node.get('layoutMode') and len(flat) >= 1:
        # ── Constraint-driven classification (depth ≥ 2, or page_flow not active) ──
        # Skip if: small container (≤100px both dimensions) or root level (page_flow handles it)
        _skip_classifier = should_skip_classifier(w, h, clips_content=bool(node.get('clipsContent')))
        if not _skip_classifier and not ctx.get('isRoot') and len(flat) >= 1:
            classification = classify_children(node, flat)
            content_nodes = classification.get('content', [])
            if content_nodes:
                # Guard: if content and preserve_absolute nodes are side-by-side
                # (Y-overlap, no X-overlap) they are row-level siblings (logo parts,
                # label+slider, etc.). Flow conversion (always column) would break them.
                # Let infer_flex_layout handle the row detection instead.
                _abs_nodes = classification.get('preserve_absolute', [])
                _side_by_side = False
                if _abs_nodes and content_nodes:
                    for _cn in content_nodes:
                        _cn_abb = _cn.get('absoluteBoundingBox') or {}
                        _cn_t = _cn_abb.get('y', 0)
                        _cn_b = _cn_t + (_cn_abb.get('height', 0) or 0)
                        for _an in _abs_nodes:
                            _an_abb = _an.get('absoluteBoundingBox') or {}
                            _an_t = _an_abb.get('y', 0)
                            _an_b = _an_t + (_an_abb.get('height', 0) or 0)
                            _y_overlap = not (_cn_b <= _an_t or _an_b <= _cn_t)
                            _cn_l = _cn_abb.get('x', 0)
                            _cn_r = _cn_l + (_cn_abb.get('width', 0) or 0)
                            _an_l = _an_abb.get('x', 0)
                            _an_r = _an_l + (_an_abb.get('width', 0) or 0)
                            _x_overlap = not (_cn_r <= _an_l or _an_r <= _cn_l)
                            if _y_overlap and not _x_overlap:
                                _side_by_side = True
                                break
                        if _side_by_side:
                            break
                if not _side_by_side:
                    _flow_result = convert_to_flow(node, content_nodes, adaptive_clamp=ctx.get('_adaptive_clamp', False))

        # Fall back to existing inference when classifier doesn't apply
        if not _flow_result and len(flat) >= 2:
            inferred = infer_flex_layout(flat, node, child_ctx['absX'], child_ctx['absY'])
            if not inferred and ctx.get('isRoot'):
                # Root page frame: try variable-gap column detection.
                # Guarded by isRoot so this never fires inside component internals.
                inferred = infer_page_flow(flat, node, child_ctx['absX'], child_ctx['absY'])
                if inferred:
                    # Switch child context: children become flex flow items, not absolute.
                    child_ctx = {
                        **child_ctx,
                        'layoutMode': 'VERTICAL',
                        'pageDesignWidth': node.get('width', 0),
                    }
        if inferred:
            css.update(inferred_flex_to_css(inferred))
            if inferred.get('mode') == 'page_flow':
                # Remove fixed canvas height — let content drive page height.
                css.pop('min-height', None)
                # Defensive: root nodes write min-height not height (see css_extractor isRoot
                # branch), but pop both in case that changes.
                css.pop('height', None)
                # Remove position:relative set for absolute children — in page_flow mode
                # all children are flex flow items, not absolutely positioned.
                css.pop('position', None)
                # Pop align-items written by css_extractor (counterAxisAlignItems absent
                # defaults to MIN → flex-start). page_flow frames have no Auto Layout so
                # counterAxisAlignItems is not part of the design spec; don't hardcode it.
                css.pop('align-items', None)
            ir['inferredFlex'] = {
                'direction': inferred['direction'],
                'confidence': inferred['confidence'],
                'mode': inferred.get('mode', 'uniform'),
            }
        elif not css.get('position'):
            css['position'] = 'relative'

        # Apply constraint-driven flow conversion
        if _flow_result:
            # Preserve the node's own position (e.g. absolute from layoutPositioning)
            # since _flow_result['parent_css'] always sets position:relative which
            # would incorrectly override it.
            _orig_position = css.get('position')
            css.update(_flow_result['parent_css'])
            if _orig_position == 'absolute':
                css['position'] = 'absolute'

    if _has_overlap(flat, child_ctx['absX'], child_ctx['absY']):
        if not css.get('position'):
            css['position'] = 'relative'

    # When flow conversion is active, build content vs non-content children with
    # different contexts: content nodes use VERTICAL (flex items), non-content nodes
    # use the original NONE context (so extract_css computes their absolute left/top).
    if _flow_result:
        flow_css_map = _flow_result['children_css']
        content_ctx = {**child_ctx, 'layoutMode': 'VERTICAL'}
        abs_ctx = child_ctx  # original context with layoutMode=NONE
        # Collect background node IDs for potential skip
        _bg_ids = {n.get('id') for n in classification.get('background', [])} if classification else set()

        flow_children = []
        abs_children = []
        for c in flat:
            cid = c.get('id', '')
            # Skip background nodes that only contain image leaf children —
            # they are decorative underlays fully covered by content in Figma
            # but may leak through overflow:hidden clipping differences in CSS.
            if cid in _bg_ids:
                _bg_children = c.get('children') or []
                _leaf_types = {'RECTANGLE', 'ELLIPSE', 'VECTOR', 'LINE'}
                # Skip background leaf-image containers ONLY when they are
                # spatially covered by a content sibling (decorative underlay).
                # Preserve background nodes with cornerRadius — they carry visible
                # border/rounded-corner effects that must render as CSS.
                _has_visual_radius = bool(c.get('cornerRadius'))
                if not _has_visual_radius and _bg_children and all(
                    ch.get('type') in _leaf_types and not ch.get('children')
                    for ch in _bg_children
                ):
                    _c_abb = c.get('absoluteBoundingBox') or {}
                    _c_area = (_c_abb.get('width', 0) or 0) * (_c_abb.get('height', 0) or 0)
                    _covered = False
                    if _c_area > 0:
                        for _content_node in content_nodes:
                            _cn_abb = _content_node.get('absoluteBoundingBox') or {}
                            _cn_area = (_cn_abb.get('width', 0) or 0) * (_cn_abb.get('height', 0) or 0)
                            if _cn_area >= _c_area * 0.5:
                                _covered = True
                                break
                    if _covered:
                        continue
            if cid in flow_css_map:
                child_ir = _build_node(c, content_ctx, sass_vars, var_id_to_bds, style_id_to_bds)
                if not child_ir:
                    continue
                c_css = child_ir.setdefault('css', {})
                for prop, val in flow_css_map[cid].items():
                    c_css[prop] = val
                # Remove absolute positioning that was set by extract_css
                c_css.pop('left', None)
                c_css.pop('top', None)
                c_css.pop('right', None)
                c_css.pop('bottom', None)
                c_css.pop('transform', None)
                c_css['flex-shrink'] = '0'
                # Set position:relative so flow children stack above absolute
                # background/decorative siblings (CSS paints positioned elements
                # above non-positioned ones regardless of DOM order).
                c_css['position'] = 'relative'
                flow_children.append(child_ir)
            else:
                child_ir = _build_node(c, abs_ctx, sass_vars, var_id_to_bds, style_id_to_bds)
                if not child_ir:
                    continue
                abs_children.append(child_ir)
        # Preserve Figma z-order: absolute nodes before first content stay behind,
        # absolute nodes after last content stay in front (foreground overlays).
        content_id_order = list(flow_css_map.keys())
        flow_children.sort(key=lambda c: content_id_order.index(c.get('figmaId')) if c.get('figmaId') in content_id_order else 999)
        # Split abs_children by their original Figma order relative to content
        _flat_ids = [c.get('id', '') for c in flat]
        _first_content_idx = min(
            (_flat_ids.index(cid) for cid in content_id_order if cid in _flat_ids),
            default=len(_flat_ids)
        )
        abs_before = []
        abs_after = []
        for ac in abs_children:
            ac_fid = ac.get('figmaId', '')
            ac_idx = _flat_ids.index(ac_fid) if ac_fid in _flat_ids else 0
            if ac_idx < _first_content_idx:
                abs_before.append(ac)
            else:
                abs_after.append(ac)
        built_children = abs_before + flow_children + abs_after
    else:
        built_children = [_build_node(c, child_ctx, sass_vars, var_id_to_bds, style_id_to_bds) for c in flat]


    # page_flow: inject per-child margin-top / margin-left / max-width.
    # Children were built with layoutMode='VERTICAL' so they have no position:absolute.
    # Here we apply spacing that replaces the old top/left pixel coordinates.
    if inferred and inferred.get('mode') == 'page_flow':
        margin_map = {m['id']: m for m in inferred.get('per_child_margins', [])}
        for child_ir in built_children:
            if not child_ir:
                continue
            m = margin_map.get(child_ir.get('figmaId'), {})
            if not m:
                continue
            c_css = child_ir.setdefault('css', {})
            if m.get('mt', 0) > 0:
                c_css['margin-top'] = _px(m['mt'])
            if m.get('centered'):
                c_css['margin-left'] = 'auto'
                c_css['margin-right'] = 'auto'
                c_css['max-width'] = _px(m['max_width'])
            elif m.get('ml', 0) > 0:
                c_css['margin-left'] = _px(m['ml'])
            c_css['flex-shrink'] = '0'

    # Negative itemSpacing → CSS gap cannot be negative; apply negative margin to
    # non-first flow children to preserve the intended overlap effect.
    item_spacing = node.get('itemSpacing') or 0
    node_layout = node.get('layoutMode') or ''
    if item_spacing < 0 and node_layout in ('VERTICAL', 'HORIZONTAL'):
        flow_idx = 0
        for child_ir in built_children:
            if child_ir is None:
                continue
            if (child_ir.get('css') or {}).get('position') == 'absolute':
                continue
            if flow_idx > 0:
                child_ir.setdefault('css', {})
                if node_layout == 'VERTICAL':
                    child_ir['css']['margin-top'] = f'{item_spacing}px'
                else:
                    child_ir['css']['margin-left'] = f'{item_spacing}px'
            flow_idx += 1

    # Z-order: Figma Auto-Layout children array is already bottom-to-top
    # (first child = lowest z-layer), which matches CSS DOM paint order
    # (later elements paint on top). No reversal needed.

    ir['children'] = [c for c in built_children if c is not None]

    # Vector overflow expansion: when a container has clipsContent=false, only one
    # visible vector child (SVG img at 100%x100%), and invisible siblings that extend
    # beyond the container, expand the container CSS size to the actual content bounding
    # box so the SVG renders at the correct size.
    # Skip for INSTANCE nodes: invisible children are variant-disabled parts of the
    # component master, NOT overflow content of the visible SVG.
    if (not node.get('clipsContent')
            and node_type != 'INSTANCE'
            and len(ir['children']) == 1
            and ir['children'][0].get('isVectorNode')
            and w > 0 and h > 0):
        all_children = node.get('children') or []
        _invis = [c for c in all_children if c.get('visible') is False]
        if _invis:
            _p_x, _p_y = abb.get('x', 0), abb.get('y', 0)
            _min_x, _min_y = _p_x, _p_y
            _max_x, _max_y = _p_x + w, _p_y + h
            for _ic in all_children:
                _ic_abb = _ic.get('absoluteBoundingBox') or {}
                _ic_x = _ic_abb.get('x', _p_x)
                _ic_y = _ic_abb.get('y', _p_y)
                _ic_w = _ic_abb.get('width', 0) or 0
                _ic_h = _ic_abb.get('height', 0) or 0
                _min_x = min(_min_x, _ic_x)
                _min_y = min(_min_y, _ic_y)
                _max_x = max(_max_x, _ic_x + _ic_w)
                _max_y = max(_max_y, _ic_y + _ic_h)
            _content_w = _max_x - _min_x
            _content_h = _max_y - _min_y
            if _content_w > w * 1.3 or _content_h > h * 1.3:
                css['width'] = _px(_content_w)
                css['height'] = _px(_content_h)
                css['overflow'] = 'visible'
                _offset_x = _p_x - _min_x
                _offset_y = _p_y - _min_y
                if _offset_x > 0:
                    _cur_left = css.get('left', '')
                    if _cur_left.endswith('px'):
                        css['left'] = _px(float(_cur_left[:-2]) - _offset_x)
                if _offset_y > 0:
                    _cur_top = css.get('top', '')
                    if _cur_top.endswith('px'):
                        css['top'] = _px(float(_cur_top[:-2]) - _offset_y)

    # Post-processing: decorative element marking + frame height adaptation
    _post_process_frame(ir)

    # postProcessFrame may mark decorative children as position:absolute after the
    # hasAbsoluteChildren check above — ensure parent is a containing block.
    if not ir['css'].get('position'):
        if any(c.get('css', {}).get('position') == 'absolute' for c in ir['children']):
            ir['css']['position'] = 'relative'

    # Overflow child promotion:
    # When a node has clipsContent=False + visible fills + an ABSOLUTE child that extends
    # outside the node's bounds, Figma visually renders the node's fill ON TOP of the
    # overflowing child (covering the inner portion). CSS cannot do this because
    # background is always behind children. Fix: wrap the node + promoted overflow
    # children in a transparent container; the node renders AFTER the overflow children
    # in DOM order, so its fill naturally covers their inner portion.
    ir = _wrap_overflow_children(ir, node)

    return ir


# ─── Overflow child promotion ──────────────────────────────────────────────────

def _child_extends_outside(child_css: dict) -> bool:
    """Return True if an absolute child has a negative CSS position value (> 1px outside parent)."""
    for prop in ('top', 'left', 'right', 'bottom'):
        val = child_css.get(prop, '')
        if isinstance(val, str) and val.endswith('px'):
            try:
                if float(val[:-2]) < -1.0:
                    return True
            except ValueError:
                pass
    return False


def _parent_needs_overflow_wrap(node: dict, ir: dict) -> bool:
    """
    True when this node should have its overflowing absolute children promoted to a wrapper:
    - clipsContent is explicitly False (not None/True — those mean clip or default-clip)
    - Node has at least one visible fill (the fill is what covers the inner portion of the child)
    - At least one absolute child extends outside the parent's CSS bounds
    """
    if node.get('clipsContent') is not False:
        return False
    fills = node.get('fills') or []
    if not any(f.get('visible') is not False for f in fills):
        return False
    for child_ir in (ir.get('children') or []):
        child_css = child_ir.get('css') or {}
        if child_css.get('position') == 'absolute' and _child_extends_outside(child_css):
            return True
    return False


def _wrap_overflow_children(ir: dict, node: dict) -> dict:
    """
    Promote ABSOLUTE children that extend outside a fill-bearing, no-clip parent into a
    transparent wrapper.  DOM order: [overflow_children..., parent] — so the parent's
    background is painted AFTER the overflow children, covering their inner overlap.

    Wrapper CSS:
      - position: relative (containing block for absolute children)
      - height / width / min-height from parent (so bottom/top/left/right refs are correct)
      - Positioning CSS from parent if parent was absolutely positioned
      - Flex-child CSS (flex-grow, align-self, margin-*) from parent

    Parent CSS after promotion:
      - Drops absolute positioning coords transferred to wrapper (left/top/right/bottom)
      - Retains position: relative (for any remaining absolute children)
      - Retains all visual CSS (background, border, padding, display, etc.)
    """
    if not _parent_needs_overflow_wrap(node, ir):
        return ir

    parent_css = ir.get('css') or {}

    # Separate overflowing absolute children from the rest
    promoted = []
    remaining_children = []
    for child_ir in (ir.get('children') or []):
        child_css = child_ir.get('css') or {}
        if child_css.get('position') == 'absolute' and _child_extends_outside(child_css):
            # Promoted children appear before the parent in DOM (so parent background can cover
            # the overlap area), but must have z-index: 1 to stay above the parent's background
            # in that overlap region — otherwise the parent (DOM-later, position: relative) paints
            # on top and hides the overflow decoration.
            # _isOWChild marker prevents patch_flex_shrink Patch 12 from stripping this z-index.
            child_css['z-index'] = '1'
            child_ir['_isOWChild'] = True
            promoted.append(child_ir)
        else:
            remaining_children.append(child_ir)

    if not promoted:
        return ir  # nothing to promote (safety check)

    # Build wrapper CSS
    wrapper_css = {'position': 'relative'}

    # Size: copy from parent so absolute children have the correct bottom/top/left/right reference.
    # IMPORTANT: CSS `bottom` is relative to the containing block's USED HEIGHT, which requires an
    # explicit `height` — not `min-height`. If _post_process_frame converted height→min-height,
    # we restore it as `height` on the wrapper (the wrapper is transparent and won't clip the pill).
    for prop in ('max-width', 'max-height', 'min-width'):
        if parent_css.get(prop):
            wrapper_css[prop] = parent_css[prop]
    if parent_css.get('width'):
        wrapper_css['width'] = parent_css['width']
    # Height: prefer explicit height; fall back to min-height as height for wrapper reference
    _parent_h = parent_css.get('height') or parent_css.get('min-height')
    if _parent_h:
        wrapper_css['height'] = _parent_h

    # Transfer absolute positioning coords from parent to wrapper
    _ABS_POSITION_PROPS = ('left', 'top', 'right', 'bottom')
    if parent_css.get('position') == 'absolute':
        wrapper_css['position'] = 'absolute'
        for prop in _ABS_POSITION_PROPS:
            if parent_css.get(prop):
                wrapper_css[prop] = parent_css.pop(prop)
        if parent_css.get('transform'):
            wrapper_css['transform'] = parent_css.pop('transform')

    # Transfer flex-child layout properties so wrapper takes the same place in parent layout
    for prop in ('flex', 'flex-grow', 'flex-shrink', 'align-self',
                 'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
                 'flex-basis'):
        if parent_css.get(prop):
            wrapper_css[prop] = parent_css.get(prop)

    # Parent always remains position:relative (containing block for any non-promoted absolute children)
    parent_css['position'] = 'relative'

    # Build modified parent with overflow children removed
    modified_parent = dict(ir)
    modified_parent['children'] = remaining_children
    modified_parent['css'] = parent_css

    return {
        'figmaId': f"{ir.get('figmaId', '')}__ow",
        'figmaName': f"{ir.get('figmaName', '')}-ow-wrapper",
        'figmaType': 'FRAME',
        'isGeneratedWrapper': True,
        'bb': ir.get('bb'),
        'isImageNode': False,
        'isVectorNode': False,
        'isTextNode': False,
        'isComponentInstance': False,
        'figmaCounterAxisAlign': None,
        'textContent': None,
        'textSegments': None,
        'textAutoResize': None,
        'lineTypes': None,
        'lineIndentations': None,
        'variants': None,
        'imageRef': None,
        'fillImageRef': None,
        'componentId': None,
        'css': wrapper_css,
        'children': promoted + [modified_parent],
    }


# ─── Post-processing ───────────────────────────────────────────────────────────

def _post_process_frame(ir: dict) -> None:
    """
    1. Decorative elements (out-of-bounds / VECTOR / UUID names) → pointer-events: none
    2. Children exceeding frame height → height becomes min-height
    3. Overflowing image/decorative child → overflow: hidden on parent
    """
    # Parse height robustly — handle px, % and other units
    raw_h = (ir.get('css') or {}).get('height', '')
    try:
        frame_h = float(raw_h.rstrip('px')) if isinstance(raw_h, str) and raw_h.endswith('px') else 0
    except (ValueError, AttributeError):
        frame_h = 0

    parent_is_flex = (ir.get('css') or {}).get('display') == 'flex'
    children_exceed_height = False

    for child in ir.get('children') or []:
        child_css = child.get('css') or {}

        top_str = child_css.get('top', '0')
        height_str = child_css.get('height', '0')
        try:
            top = float(top_str.rstrip('px')) if isinstance(top_str, str) and top_str.endswith('px') else 0
        except (ValueError, AttributeError):
            top = 0
        try:
            height = float(height_str.rstrip('px')) if isinstance(height_str, str) and height_str.endswith('px') else 0
        except (ValueError, AttributeError):
            height = 0

        left_str = child_css.get('left', '')
        left_pct = None
        if isinstance(left_str, str) and left_str.endswith('%'):
            try:
                left_pct = float(left_str.rstrip('%'))
            except ValueError:
                pass

        # Decorative element detection:
        # - left% beyond ±200% (SCALE constraint + out-of-bounds)
        # - top is deeply negative (above frame top)
        # - UUID-style node name (tool-generated)
        # - is a vector/boolean node
        is_out_of_bounds_h = left_pct is not None and abs(left_pct) > 200
        is_out_of_bounds_v = top < -(frame_h * 0.5) and frame_h > 0
        # Image nodes often have Figma asset-hash names (UUID format) but are valid content.
        is_uuid = (
            not child.get('isImageNode')
            and bool(re.match(r'^[0-9a-f\-]{20,}', child.get('figmaName') or ''))
        )
        is_decorative = (
            is_out_of_bounds_h
            or is_out_of_bounds_v
            or is_uuid
            or child.get('isVectorNode')
        )

        if is_decorative:
            child_css['pointer-events'] = 'none'
            child['isDecorativeElement'] = True
            # Inline icons in flex containers must stay in flow — if we make them
            # absolute the parent collapses and same-row text disappears.
            if not child_css.get('position') and not (parent_is_flex and child.get('isVectorNode')):
                child_css['position'] = 'absolute'
            # Zero-size decorative nodes: hide entirely to avoid extreme coordinates
            # (e.g. SCALE % divided by near-zero parent width) disrupting layout.
            try:
                cw = float((child_css.get('width', '1') or '1').rstrip('px'))
            except (ValueError, AttributeError):
                cw = 1
            try:
                ch = float((child_css.get('height', '1') or '1').rstrip('px'))
            except (ValueError, AttributeError):
                ch = 1
            if cw == 0 and ch == 0:
                child_css['display'] = 'none'

        # Check if absolute child exceeds frame height (5% tolerance)
        if frame_h > 0 and not is_decorative and child_css.get('position') == 'absolute':
            if top > frame_h or (height > 0 and top + height > frame_h * 1.05):
                children_exceed_height = True

        # Bottom-sheet pattern: abs-positioned flex-column with negative bottom + fixed height
        # + border-radius + overflow:hidden (the overflow is for radius clipping, not size clipping).
        # The fixed height creates blank space when content is shorter than the Figma frame height.
        # Fix: convert bottom+height → top+auto so the panel sizes to its actual content.
        # top = parent_h - child_h - bottom_px  (negative bottom means panel overhangs downward)
        # Real data: node 314:11956 from PageByMeta (bottom=-1034px, height=4764px, parent_h=4438px)
        # Note: parent may have min-height (not height) if already post-processed; read both.
        _parent_ir_css = ir.get('css') or {}
        _parent_h_str = _parent_ir_css.get('height') or _parent_ir_css.get('min-height') or ''
        try:
            _parent_h = float(_parent_h_str.rstrip('px')) if isinstance(_parent_h_str, str) and _parent_h_str.endswith('px') else 0
        except (ValueError, AttributeError):
            _parent_h = 0
        if (
            _parent_h > 0
            and not child.get('isDecorativeElement')
            and child_css.get('position') == 'absolute'
            and child_css.get('display') == 'flex'
            and child_css.get('flex-direction') == 'column'
            and child_css.get('border-radius')
        ):
            _bot_str = child_css.get('bottom', '')
            if isinstance(_bot_str, str) and _bot_str.endswith('px'):
                try:
                    _bot_px = float(_bot_str[:-2])
                    if _bot_px < 0 and height > 0:
                        _top_px = _parent_h - height - _bot_px
                        child_css['top'] = _px(_top_px)
                        del child_css['bottom']
                        del child_css['height']
                        # Also remove fixed height from direct flex-column flow children.
                        # When the panel loses its fixed height, child heights that were
                        # sized relative to that fixed height create blank space.
                        # Real data: 314:11957 (.content-section height:2814px) clips content
                        # via parent overflow:hidden after parent loses height:4764px.
                        for _gc in child.get('children') or []:
                            _gc_css = _gc.get('css') or {}
                            if (
                                _gc_css.get('display') == 'flex'
                                and _gc_css.get('flex-direction') == 'column'
                                and _gc_css.get('height')
                                and _gc_css.get('position') != 'absolute'
                            ):
                                del _gc_css['height']
                except (ValueError, AttributeError):
                    pass

    # Flex-column overflow detection:
    # Absolute children are handled above via top values.
    # Flow children in a flex-column have no top; estimate total height by summing them.
    ir_css = ir.get('css') or {}
    flow_kids = []  # initialised here; populated below if this is a flex-column
    if (
        frame_h > 0
        and ir_css.get('display') == 'flex'
        and ir_css.get('flex-direction', 'row') == 'column'
    ):
        gap_str = ir_css.get('gap', '0')
        try:
            gap = float(gap_str.rstrip('px')) if isinstance(gap_str, str) else 0
        except (ValueError, AttributeError):
            gap = 0

        padding_str = ir_css.get('padding', '')
        padding_parts = []
        if padding_str:
            for part in padding_str.split():
                try:
                    padding_parts.append(float(part.rstrip('px')))
                except ValueError:
                    padding_parts.append(0)
        pt = padding_parts[0] if len(padding_parts) > 0 else 0
        pb = padding_parts[2] if len(padding_parts) > 2 else (padding_parts[0] if padding_parts else 0)

        flow_kids = [
            c for c in (ir.get('children') or [])
            if not c.get('isDecorativeElement') and (c.get('css') or {}).get('position') != 'absolute'
        ]

        if flow_kids:
            total_kid_h = 0
            for c in flow_kids:
                c_css = c.get('css') or {}
                explicit_str = c_css.get('height') or c_css.get('min-height') or '0'
                try:
                    explicit = float(explicit_str.rstrip('px')) if isinstance(explicit_str, str) else 0
                except (ValueError, AttributeError):
                    explicit = 0

                if explicit > 0:
                    total_kid_h += explicit
                elif c.get('isTextNode'):
                    lh = c_css.get('line-height')
                    if lh and isinstance(lh, str) and lh.endswith('px'):
                        try:
                            total_kid_h += float(lh.rstrip('px'))
                        except ValueError:
                            total_kid_h += 16 * 1.4
                    else:
                        fs_str = c_css.get('font-size', '16')
                        try:
                            fs = float(fs_str.rstrip('px')) if isinstance(fs_str, str) else 16
                        except (ValueError, AttributeError):
                            fs = 16
                        lh_num = 0
                        if lh:
                            try:
                                lh_num = float(lh)
                            except (ValueError, TypeError):
                                lh_num = 0
                        total_kid_h += (lh_num * fs) if lh_num > 0 else (fs * 1.4)
                else:
                    fs_str = c_css.get('font-size', '0')
                    try:
                        fs = float(fs_str.rstrip('px')) if isinstance(fs_str, str) else 0
                    except (ValueError, AttributeError):
                        fs = 0
                    total_kid_h += (fs * 1.4) if fs > 0 else 24

            total_h = total_kid_h + gap * (len(flow_kids) - 1) + pt + pb
            if total_h > frame_h * 1.05:
                children_exceed_height = True

    # Overflow image/decorative child detection — must run BEFORE height→min-height
    # so that a frame whose image child triggers overflow:hidden keeps its fixed height
    # (clipping at the frame boundary) instead of being converted to min-height.
    if not ir_css.get('overflow'):
        frame_w_str = ir_css.get('width', '0')
        f_h_str = ir_css.get('height') or ir_css.get('min-height') or '0'
        try:
            frame_w = float(frame_w_str.rstrip('px')) if isinstance(frame_w_str, str) else 0
        except (ValueError, AttributeError):
            frame_w = 0
        try:
            f_h = float(f_h_str.rstrip('px')) if isinstance(f_h_str, str) else 0
        except (ValueError, AttributeError):
            f_h = 0

        for child in ir.get('children') or []:
            if not child.get('isDecorativeElement') and not child.get('isImageNode'):
                continue
            c_css = child.get('css') or {}
            cw_str = c_css.get('width', '0')
            ch_str = c_css.get('height', '0')
            try:
                cw = float(cw_str.rstrip('px')) if isinstance(cw_str, str) else 0
            except (ValueError, AttributeError):
                cw = 0
            try:
                ch = float(ch_str.rstrip('px')) if isinstance(ch_str, str) else 0
            except (ValueError, AttributeError):
                ch = 0
            overflows_h = f_h > 0 and ch > f_h * 1.05
            overflows_w = frame_w > 0 and cw > frame_w * 1.05
            if overflows_h or overflows_w:
                ir_css['overflow'] = 'hidden'
                break

    # Frame height adaptation: replace fixed height with min-height.
    # Exception 1: when overflow:hidden is already set (clipsContent=True or image overflow
    # detected above), the designer explicitly wants the frame to clip at the fixed height.
    # Exception 2: pure-text containers — CSS line-height adds half-leading (~2-4px per
    # container) beyond Figma's glyph-based bbox. Keep the fixed height so cumulative
    # drift doesn't shift downstream content; the half-leading is visually negligible.
    # Exception 3: containers with absolute children that use percentage-based centering
    # (top:50%+translateY(-50%)) — they need a fixed parent height as a positioning
    # reference; converting to min-height breaks the centering.
    _all_pure_text_ch = flow_kids and all(c.get('isTextNode') for c in flow_kids)
    _has_pct_positioned_abs = any(
        (c.get('css') or {}).get('position') == 'absolute'
        and ('50%' in str((c.get('css') or {}).get('top', ''))
             or 'translateY(-50%)' in str((c.get('css') or {}).get('transform', '')))
        for c in (ir.get('children') or [])
    )
    if children_exceed_height and ir_css.get('height') and not ir_css.get('overflow') \
            and not _all_pure_text_ch and not _has_pct_positioned_abs:
        # Figma height is a border-box value; CSS min-height is content-box (no global
        # box-sizing:border-box reset in this project). Subtract vertical padding so the
        # rendered element height matches the Figma frame dimensions.
        # Example: Figma h=478, padding-top=280, padding-bottom=40 → content min-height=158,
        # total CSS height = 158 + 280 + 40 = 478px ✓ (instead of 478 + 320 = 798px ✗).
        _raw_h_str = ir_css['height']
        _pad_v = _parse_vertical_padding(ir_css.get('padding', ''))
        try:
            _h_val = float(_raw_h_str.rstrip('px'))
        except (ValueError, AttributeError):
            _h_val = 0.0
        _content_h = max(0.0, _h_val - _pad_v)
        ir_css['min-height'] = _px(_content_h) if _content_h > 0 else '0'
        del ir_css['height']

    # Stacking fix: when a frame has absolute children (backgrounds/decorations),
    # flow siblings without `position` are rendered below the absolute layer in CSS
    # (static < positioned in stacking order). Give them position:relative so they
    # appear above the absolute background.
    _abs_children = [
        c for c in (ir.get('children') or [])
        if (c.get('css') or {}).get('position') == 'absolute'
    ]
    if _abs_children:
        for c in (ir.get('children') or []):
            c_css = c.get('css') or {}
            if c_css.get('position') not in ('absolute', 'relative', 'fixed', 'sticky'):
                c_css['position'] = 'relative'


# ─── Group flattening ──────────────────────────────────────────────────────────

def flatten_groups(
    children: list,
    parent_has_layout: bool = False,
    parent_abs_x: float = 0,
    parent_abs_y: float = 0,
) -> list:
    """
    Recursively flatten PASS_THROUGH GROUP nodes that add no layout value.
    Groups with blend modes, masks, or offsets are preserved.
    Groups inside auto-layout containers are always preserved (they act as a single flex child).
    """
    result = []
    for n in children:
        if n.get('type') != 'GROUP':
            result.append(n)
            continue
        blend = n.get('blendMode')
        if blend and blend != 'PASS_THROUGH':
            result.append(n)
            continue
        if any(c.get('isMask') for c in (n.get('children') or [])):
            result.append(n)
            continue
        if parent_has_layout:
            # Preserve group integrity inside auto-layout containers
            result.append(n)
            continue
        if n.get('cornerRadius'):
            # GROUP with cornerRadius provides visual rounding — preserve as a div with border-radius.
            result.append(n)
            continue
        abb = n.get('absoluteBoundingBox') or {}
        group_abs_x = abb.get('x') if abb.get('x') is not None else parent_abs_x
        group_abs_y = abb.get('y') if abb.get('y') is not None else parent_abs_y
        rel_x = group_abs_x - parent_abs_x
        rel_y = group_abs_y - parent_abs_y
        if abs(rel_x) > 0.5 or abs(rel_y) > 0.5:
            result.append(n)
            continue
        result.extend(flatten_groups(
            [c for c in (n.get('children') or []) if c.get('visible') is not False],
            parent_has_layout,
            group_abs_x,
            group_abs_y,
        ))
    return result


# ─── Overlap detection ─────────────────────────────────────────────────────────

def _has_overlap(children: list, parent_abs_x: float = 0, parent_abs_y: float = 0) -> bool:
    """AABB overlap test using relative coordinates."""
    def bounds(n):
        abb = n.get('absoluteBoundingBox') or {}
        x = n.get('x') if n.get('x') is not None else ((abb.get('x') or 0) - parent_abs_x)
        y = n.get('y') if n.get('y') is not None else ((abb.get('y') or 0) - parent_abs_y)
        w = n.get('width') if n.get('width') is not None else abb.get('width', 0)
        h = n.get('height') if n.get('height') is not None else abb.get('height', 0)
        return x or 0, y or 0, w or 0, h or 0

    for i in range(len(children)):
        for j in range(i + 1, len(children)):
            ax, ay, aw, ah = bounds(children[i])
            cx, cy, cw, ch = bounds(children[j])
            if ax < cx + cw and ax + aw > cx and ay < cy + ch and ay + ah > cy:
                return True
    return False


# ─── Variant extraction ────────────────────────────────────────────────────────

def _extract_variants(node: dict):
    """Extract variant property map from COMPONENT_SET nodes."""
    if node.get('type') != 'COMPONENT_SET':
        return None
    result = {}
    for child in (node.get('children') or []):
        for k, v in (child.get('variantProperties') or {}).items():
            if k not in result:
                result[k] = []
            if v not in result[k]:
                result[k].append(v)
    return result if result else None


# ─── Text segment extraction ───────────────────────────────────────────────────

def _build_text_segments(node: dict):
    """
    Extract character-level style segments from Figma mixed-style text.
    Returns [{text, color, fontSize, fontWeight}] list when any segment differs
    from the node-level style (color, fontSize, or fontWeight).
    Returns None for single-style text.
    """
    overrides = node.get('characterStyleOverrides')
    table = node.get('styleOverrideTable')
    chars = node.get('characters') or ''

    if not overrides or not table or all(v == 0 for v in overrides):
        return None

    # Node-level font properties for comparison
    node_style = node.get('style') or {}
    node_font_size = node_style.get('fontSize')
    node_font_weight = node_style.get('fontWeight')

    segments = []
    start = 0
    prev_id = overrides[0] if overrides else 0

    for i in range(1, len(chars) + 1):
        cur_id = overrides[i] if i < len(overrides) else -1
        if cur_id != prev_id:
            text = chars[start:i]
            override = table.get(prev_id) or table.get(str(prev_id)) or {}
            segment_fills = [f for f in (override.get('fills') or []) if f.get('visible') is not False]
            color = None
            if segment_fills:
                fill = segment_fills[0]
                fill_type = fill.get('type')
                if fill_type == 'SOLID' and fill.get('color'):
                    color = figma_color_to_css(fill['color'], fill.get('opacity') if fill.get('opacity') is not None else 1)
                elif fill_type == 'GRADIENT_LINEAR':
                    # Build CSS linear-gradient for text; caller will use
                    # background-clip:text + webkit-text-fill-color:transparent
                    bbox = node.get('absoluteBoundingBox') or {}
                    W = bbox.get('width') or 1
                    H = bbox.get('height') or 1
                    handles = fill.get('gradientHandlePositions') or []
                    grad_stops = fill.get('gradientStops') or []
                    gradient = None
                    if len(handles) >= 2 and grad_stops:
                        h0, h1 = handles[0], handles[1]
                        dx = (h1['x'] - h0['x']) * W
                        dy = (h1['y'] - h0['y']) * H
                        angle_rad = _math.atan2(dx, -dy)
                        deg = round((_math.degrees(angle_rad) + 360) % 360, 1)
                        L = abs(W * _math.sin(angle_rad)) + abs(H * _math.cos(angle_rad))
                        sx = W / 2 - L / 2 * _math.sin(angle_rad)
                        sy = H / 2 + L / 2 * _math.cos(angle_rad)
                        parts = []
                        for s in grad_stops:
                            t = s['position']
                            px_ = (h0['x'] + t * (h1['x'] - h0['x'])) * W
                            py_ = (h0['y'] + t * (h1['y'] - h0['y'])) * H
                            proj = (px_ - sx) * _math.sin(angle_rad) + (py_ - sy) * (-_math.cos(angle_rad))
                            pct = round(proj / L * 100, 2) if L else 0
                            parts.append(f'{figma_color_to_css(s["color"])} {pct:g}%')
                        gradient = f'linear-gradient({deg:g}deg, {", ".join(parts)})'
                    if gradient:
                        segments.append({'text': text, 'color': None, 'gradient': gradient})
                        start = i
                        prev_id = cur_id
                        continue

            # Extract fontSize/fontWeight overrides (only when different from node-level)
            seg_font_size = override.get('fontSize')
            seg_font_weight = override.get('fontWeight')
            font_size = seg_font_size if (seg_font_size is not None and seg_font_size != node_font_size) else None
            font_weight = seg_font_weight if (seg_font_weight is not None and seg_font_weight != node_font_weight) else None

            seg = {'text': text, 'color': color}
            if font_size is not None:
                seg['fontSize'] = f'{font_size}px'
            if font_weight is not None:
                seg['fontWeight'] = str(int(font_weight))
            segments.append(seg)
            start = i
            prev_id = cur_id

    # Single segment with no style override → let global CSS handle it
    has_any_override = any(
        seg.get('color') or seg.get('gradient') or seg.get('fontSize') or seg.get('fontWeight')
        for seg in segments
    )
    if len(segments) <= 1 and not has_any_override:
        return None
    return segments
