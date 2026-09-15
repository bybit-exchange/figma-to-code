from __future__ import annotations
"""
node_classifier.py — Constraint-driven node classification for non-Auto-Layout containers.

Classifies children of non-Auto-Layout FRAME parents into categories that determine
their CSS positioning strategy:
  - skip: invisible, not rendered
  - preserve_absolute: keep position:absolute (SCALE+SCALE, rotated, small containers)
  - content: participate in flex-column flow layout
  - background: full-size backdrop layer, keep absolute or ::before
  - decorative: visual ornament (blur, out-of-bounds, oversized), keep absolute
  - overlap: overlaps with content siblings, keep absolute
"""


from .color import px as _px, clamp_value as _clamp_value


def classify_children(parent: dict, children: list[dict]) -> dict:
    """Classify all children of a non-Auto-Layout parent.

    Args:
        parent: The parent Figma node (must have layoutMode=None or absent).
        children: List of visible child nodes.

    Returns:
        Dict with keys: skip, preserve_absolute, content, background, decorative, overlap.
        Each value is a list of child nodes in that category.
    """
    abb = parent.get('absoluteBoundingBox') or {}
    parent_w = parent.get('width') or abb.get('width', 0) or 0
    parent_h = parent.get('height') or abb.get('height', 0) or 0

    result = {
        'skip': [],
        'preserve_absolute': [],
        'content': [],
        'background': [],
        'decorative': [],
        'overlap': [],
    }

    # Two-pass: first classify non-overlap categories, then check overlap among candidates
    candidates = []
    for child in children:
        cat = _classify_single(child, parent_w, parent_h)
        if cat == '_candidate':
            candidates.append(child)
        else:
            result[cat].append(child)

    # Overlap check: among candidates, detect mutual overlap
    for child in candidates:
        if _has_layout_overlap(child, candidates):
            result['overlap'].append(child)
        else:
            result['content'].append(child)

    return result


def classify_child(child: dict, parent_w: float, parent_h: float, siblings: list[dict]) -> str:
    """Classify a single child node. Public API for testing.

    Args:
        child: The child Figma node.
        parent_w: Parent container width.
        parent_h: Parent container height.
        siblings: All candidate siblings (already filtered of skip/preserve_absolute).

    Returns:
        One of: 'skip', 'preserve_absolute', 'content', 'background', 'decorative', 'overlap'.
    """
    cat = _classify_single(child, parent_w, parent_h)
    if cat == '_candidate':
        if _has_layout_overlap(child, siblings):
            return 'overlap'
        return 'content'
    return cat


def _classify_single(child: dict, parent_w: float, parent_h: float) -> str:
    """Internal: classify without overlap check. Returns '_candidate' for overlap-pending nodes."""
    c = child.get('constraints') or {}
    h_c = c.get('horizontal', 'LEFT')
    v_c = c.get('vertical', 'TOP')

    # 0. Hidden
    if child.get('visible') is False:
        return 'skip'

    # 0b. Rotated node
    rotation = child.get('rotation', 0) or 0
    if abs(rotation) > 0.5:
        return 'preserve_absolute'

    # 1. SCALE+SCALE — icon/vector internals
    if h_c == 'SCALE' and v_c == 'SCALE':
        return 'preserve_absolute'

    # 2. Interactive
    if child.get('interactions') and len(child['interactions']) > 0:
        return 'content'

    # 3. Has TEXT descendant
    if _has_text_descendant(child):
        return 'content'

    # 4. INSTANCE (non-preserveRatio)
    if child.get('type') == 'INSTANCE' and not child.get('preserveRatio'):
        return 'content'

    # 5. Background layer
    if _is_background_layer(child, h_c, v_c, parent_w, parent_h):
        return 'background'

    # 6. Decorative
    if _is_decorative(child, parent_w, parent_h):
        return 'decorative'

    # 7. Leaf shape nodes (RECTANGLE/ELLIPSE/LINE without children) — decorative images,
    # icons, dividers. These should not participate in flow layout.
    _leaf_shape_types = {'RECTANGLE', 'ELLIPSE', 'LINE', 'REGULAR_POLYGON', 'STAR'}
    if child.get('type') in _leaf_shape_types and not child.get('children'):
        return 'preserve_absolute'

    # 8. CENTER constraint → content
    if h_c == 'CENTER':
        return 'content'

    # 9. Bound variables → content
    if child.get('boundVariables'):
        return 'content'

    # 10. Overlap check deferred → return candidate marker
    return '_candidate'


# ─── Helper functions ──────────────────────────────────────────────────────────


def _has_text_descendant(node: dict) -> bool:
    """Recursively check if node or any descendant is type TEXT."""
    if node.get('type') == 'TEXT':
        return True
    for child in node.get('children') or []:
        if _has_text_descendant(child):
            return True
    return False


def _is_background_layer(child: dict, h_c: str, v_c: str, parent_w: float, parent_h: float) -> bool:
    """Detect background layers: full-stretch constraints OR large+low-opacity."""
    if h_c == 'LEFT_RIGHT' and v_c == 'TOP_BOTTOM':
        return True

    abb = child.get('absoluteBoundingBox') or {}
    cw = abb.get('width', 0) or 0
    ch = abb.get('height', 0) or 0

    geo_match = (
        parent_w > 0 and parent_h > 0
        and cw >= parent_w * 0.9
        and ch >= parent_h * 0.9
    )

    if geo_match and not _has_text_descendant(child):
        children = child.get('children') or []
        if not children:
            return True
        if all(
            c.get('type') in ('RECTANGLE', 'ELLIPSE', 'VECTOR', 'LINE')
            and not c.get('children')
            for c in children
        ) and not any(
            any(f.get('type') == 'IMAGE' for f in (c.get('fills') or []))
            for c in children
        ):
            return True

    if child.get('opacity', 1.0) < 0.5 and geo_match:
        return True

    return False


def _is_decorative(child: dict, parent_w: float, parent_h: float) -> bool:
    """Detect decorative elements: blur, out-of-bounds, oversized, all-vector GROUP."""
    abb = child.get('absoluteBoundingBox') or {}
    cw = abb.get('width', 0) or 0

    # Large LAYER_BLUR (radius > 50)
    for e in child.get('effects') or []:
        if e.get('type') == 'LAYER_BLUR' and (e.get('radius', 0) or 0) > 50:
            return True

    # Width exceeds parent 150% — but not if node clips its own content
    # (clipsContent=True means it uses overflow:hidden, so actual visible area is bounded)
    if parent_w > 0 and cw > parent_w * 1.5 and not child.get('clipsContent'):
        return True

    # GROUP with all VECTOR children
    if child.get('type') == 'GROUP':
        children = child.get('children') or []
        if children and all(c.get('type') == 'VECTOR' for c in children):
            return True

    return False


def _has_layout_overlap(child: dict, siblings: list[dict]) -> bool:
    """Check if child overlaps with any sibling by > 30% of the smaller area."""
    child_box = child.get('absoluteBoundingBox') or {}
    cx = child_box.get('x', 0) or 0
    cy = child_box.get('y', 0) or 0
    cw = child_box.get('width', 0) or 0
    ch = child_box.get('height', 0) or 0
    child_area = cw * ch

    for sib in siblings:
        if sib is child:
            continue
        sib_box = sib.get('absoluteBoundingBox') or {}
        sx = sib_box.get('x', 0) or 0
        sy = sib_box.get('y', 0) or 0
        sw = sib_box.get('width', 0) or 0
        sh = sib_box.get('height', 0) or 0
        sib_area = sw * sh

        ix = max(cx, sx)
        iy = max(cy, sy)
        ix2 = min(cx + cw, sx + sw)
        iy2 = min(cy + ch, sy + sh)
        if ix2 <= ix or iy2 <= iy:
            continue
        intersection = (ix2 - ix) * (iy2 - iy)

        min_area = min(child_area, sib_area)
        if min_area > 0 and intersection / min_area > 0.3:
            return True

    return False


# ─── Guards ────────────────────────────────────────────────────────────────────


def should_skip_classifier(parent_w: float, parent_h: float, clips_content: bool = False) -> bool:
    """Return True if the container is too small to benefit from flow conversion.
    Small containers (≤100px in both dimensions) are atomic units — icons, avatars, etc.
    Clip containers (≤200px both dimensions) with clipsContent=True are precise crop
    frames — flow conversion would break their positioning.
    """
    if parent_w <= 100 and parent_h <= 100:
        return True
    if clips_content and parent_w <= 200 and parent_h <= 200:
        return True
    return False


# ─── Flow Conversion ──────────────────────────────────────────────────────────


def convert_to_flow(parent: dict, content_nodes: list[dict], adaptive_clamp: bool = False) -> dict | None:
    """Convert classified content nodes into a flex-column flow layout.

    Args:
        parent: Parent Figma node.
        content_nodes: List of nodes classified as 'content'.
        adaptive_clamp: If True, use clamp() for margin-top values.

    Returns:
        Dict with 'parent_css' and 'children_css' (keyed by node id), or None if
        conversion is unsafe (Y-overlap detected among content nodes).
    """
    if not content_nodes:
        return None

    abb = parent.get('absoluteBoundingBox') or {}
    parent_y = abb.get('y', 0) or 0

    # Single-element special path
    if len(content_nodes) == 1:
        return _single_element_flow(parent, content_nodes[0], parent_y, adaptive_clamp)

    # Sort by Y coordinate
    sorted_nodes = sorted(content_nodes, key=lambda n: _node_y(n))

    # Check Y-overlap among content nodes
    for i in range(1, len(sorted_nodes)):
        prev = sorted_nodes[i - 1]
        curr = sorted_nodes[i]
        prev_bottom = _node_y(prev) + _node_h(prev)
        curr_top = _node_y(curr)
        if curr_top < prev_bottom - 0.5:
            return None

    # Safety: if content nodes span nearly the entire parent height, they are
    # tightly packed absolute elements (not a simple vertical flow). Converting
    # them would overflow the container or break the intended overlap layout.
    parent_h = abb.get('height', 0) or 0
    if parent_h > 0 and len(sorted_nodes) >= 2:
        total_span = (_node_y(sorted_nodes[-1]) + _node_h(sorted_nodes[-1])) - _node_y(sorted_nodes[0])
        if total_span > parent_h * 0.9:
            return None

    # Build flex-column — preserve container height when content doesn't fill it
    parent_css = {
        'display': 'flex',
        'flex-direction': 'column',
        'position': 'relative',
    }
    if parent_h > 0:
        last_node = sorted_nodes[-1]
        content_bottom = (_node_y(last_node) + _node_h(last_node)) - parent_y
        if content_bottom < parent_h - 1:
            parent_css['height'] = _px(parent_h)

    children_css = {}
    for i, node in enumerate(sorted_nodes):
        node_id = node.get('id', str(i))
        c = node.get('constraints') or {}
        h_c = c.get('horizontal', 'LEFT')

        # margin-top
        if i == 0:
            mt = _node_y(node) - parent_y
        else:
            prev = sorted_nodes[i - 1]
            mt = _node_y(node) - (_node_y(prev) + _node_h(prev))
        mt = max(0, round(mt))

        child_css = {}
        if mt > 0:
            child_css['margin-top'] = _clamp_value(mt) if adaptive_clamp else _px(mt)

        # align-self from constraint (or detected centering)
        node_w = _node_w(node)
        parent_abb = parent.get('absoluteBoundingBox') or {}
        parent_x = parent_abb.get('x', 0) or 0
        parent_width = parent_abb.get('width', 0) or 0
        node_left = _node_x(node) - parent_x
        node_right = parent_width - node_left - node_w
        _is_geo_centered = (
            parent_width > 0 and node_w < parent_width * 0.98
            and abs(node_left - node_right) <= max(4, parent_width * 0.05)
        )

        # 检测靠右对齐：右边距 < 左边距的一半且右边距 < 30px
        _is_right_aligned = (
            parent_width > 0 and node_w < parent_width * 0.98
            and node_right < node_left * 0.5 and node_right < 30
        )

        if h_c == 'CENTER' or _is_geo_centered:
            child_css['align-self'] = 'center'
            child_css['width'] = _px(node_w)
        elif h_c == 'RIGHT' or _is_right_aligned:
            child_css['align-self'] = 'flex-end'
            child_css['width'] = _px(node_w)
            if round(node_right) > 0:
                child_css['margin-right'] = _clamp_value(round(node_right)) if adaptive_clamp else _px(round(node_right))
        elif h_c == 'LEFT_RIGHT':
            child_css['width'] = '100%'
        else:
            child_css['width'] = _px(node_w)
            if node_left > 0:
                child_css['margin-left'] = _clamp_value(round(node_left)) if adaptive_clamp else _px(round(node_left))

        children_css[node_id] = child_css

    return {'parent_css': parent_css, 'children_css': children_css}


def _single_element_flow(parent: dict, node: dict, parent_y: float, adaptive_clamp: bool = False) -> dict:
    """Handle the single-content-node case using constraints for centering."""
    c = node.get('constraints') or {}
    h_c = c.get('horizontal', 'LEFT')
    v_c = c.get('vertical', 'TOP')
    node_id = node.get('id', '0')

    # GROUP/FRAME without layoutMode: all children become absolute-positioned,
    # so the node needs explicit height to prevent collapse.
    _needs_height = (
        not node.get('layoutMode') or node.get('layoutMode') == 'NONE'
    ) and node.get('type') in ('GROUP', 'FRAME', 'COMPONENT', 'INSTANCE')

    if h_c == 'CENTER' and v_c == 'CENTER':
        parent_abb = parent.get('absoluteBoundingBox') or {}
        ph = parent_abb.get('height', 0) or 0
        nh = _node_h(node)
        actual_top = _node_y(node) - parent_y
        expected_center_top = (ph - nh) / 2
        if ph > 0 and abs(actual_top - expected_center_top) <= 8:
            child_css = {'width': _px(_node_w(node))}
            if _needs_height and nh > 0:
                child_css['height'] = _px(nh)
            return {
                'parent_css': {
                    'display': 'flex',
                    'justify-content': 'center',
                    'align-items': 'center',
                    'position': 'relative',
                },
                'children_css': {
                    node_id: child_css,
                },
            }

    if h_c == 'CENTER' and v_c == 'TOP':
        mt = max(0, round(_node_y(node) - parent_y))
        child_css = {'width': _px(_node_w(node))}
        if _needs_height and _node_h(node) > 0:
            child_css['height'] = _px(_node_h(node))
        if mt > 0:
            child_css['margin-top'] = _clamp_value(mt) if adaptive_clamp else _px(mt)
        return {
            'parent_css': {
                'display': 'flex',
                'flex-direction': 'column',
                'align-items': 'center',
                'position': 'relative',
            },
            'children_css': {node_id: child_css},
        }

    mt = max(0, round(_node_y(node) - parent_y))
    child_css = {'width': _px(_node_w(node))}
    if _needs_height and _node_h(node) > 0:
        child_css['height'] = _px(_node_h(node))
    if mt > 0:
        child_css['margin-top'] = _clamp_value(mt) if adaptive_clamp else _px(mt)

    # 几何居中检测：constraint=LEFT 但实际位置水平居中（左右偏移对称 ≤4px）
    parent_abb = parent.get('absoluteBoundingBox') or {}
    pw = parent_abb.get('width', 0) or 0
    nw = _node_w(node)
    nx = _node_x(node) - parent_abb.get('x', 0) if parent_abb.get('x') is not None else 0
    ml = nx
    mr = pw - nx - nw
    _geo_centered = pw > 0 and nw < pw and abs(ml - mr) <= 4 and ml > 0

    if h_c == 'RIGHT':
        child_css['align-self'] = 'flex-end'
    elif h_c == 'LEFT_RIGHT':
        child_css['width'] = '100%'
    elif _geo_centered:
        child_css['align-self'] = 'center'
    elif ml > 0:
        child_css['margin-left'] = _clamp_value(round(ml)) if adaptive_clamp else _px(round(ml))

    return {
        'parent_css': {
            'display': 'flex',
            'flex-direction': 'column',
            'position': 'relative',
        },
        'children_css': {node_id: child_css},
    }


# ─── Geometry helpers ──────────────────────────────────────────────────────────


def _node_y(node: dict) -> float:
    abb = node.get('absoluteBoundingBox') or {}
    return abb.get('y', 0) or 0


def _node_x(node: dict) -> float:
    abb = node.get('absoluteBoundingBox') or {}
    return abb.get('x', 0) or 0


def _node_w(node: dict) -> float:
    abb = node.get('absoluteBoundingBox') or {}
    return node.get('width') or abb.get('width', 0) or 0


def _node_h(node: dict) -> float:
    abb = node.get('absoluteBoundingBox') or {}
    return node.get('height') or abb.get('height', 0) or 0
