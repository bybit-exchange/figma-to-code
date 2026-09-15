"""Unit tests for node_classifier module."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.node_classifier import classify_child, convert_to_flow, should_skip_classifier


# ─── Skip ──────────────────────────────────────────────────────────────

def test_hidden_node_returns_skip():
    child = {'visible': False, 'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'}}
    assert classify_child(child, 1440, 900, []) == 'skip'


# ─── Preserve Absolute (SCALE+SCALE) ──────────────────────────────────

def test_scale_scale_returns_preserve_absolute():
    child = {
        'visible': True,
        'type': 'VECTOR',
        'constraints': {'horizontal': 'SCALE', 'vertical': 'SCALE'},
        'absoluteBoundingBox': {'x': 10, 'y': 10, 'width': 24, 'height': 24},
    }
    assert classify_child(child, 1440, 900, []) == 'preserve_absolute'


# ─── Content: interactions ─────────────────────────────────────────────

def test_interactive_node_returns_content():
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'interactions': [{'type': 'ON_CLICK'}],
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200, 'height': 50},
    }
    assert classify_child(child, 1440, 900, []) == 'content'


# ─── Content: has TEXT descendant ──────────────────────────────────────

def test_frame_with_text_child_returns_content():
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 0, 'y': 100, 'width': 400, 'height': 200},
        'children': [
            {'type': 'TEXT', 'characters': 'Hello'}
        ],
    }
    assert classify_child(child, 1440, 900, []) == 'content'


# ─── Content: INSTANCE ─────────────────────────────────────────────────

def test_instance_returns_content():
    child = {
        'visible': True,
        'type': 'INSTANCE',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 300, 'height': 100},
    }
    assert classify_child(child, 1440, 900, []) == 'content'


# ─── Background ────────────────────────────────────────────────────────

def test_left_right_top_bottom_is_background():
    child = {
        'visible': True,
        'type': 'RECTANGLE',
        'constraints': {'horizontal': 'LEFT_RIGHT', 'vertical': 'TOP_BOTTOM'},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
    }
    assert classify_child(child, 1440, 900, []) == 'background'


def test_frame_with_children_near_parent_size_is_not_background():
    """FRAME with children that covers ~100% of parent should NOT be background."""
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'CENTER', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 114, 'height': 115},
        'children': [
            {'type': 'FRAME', 'visible': True},
            {'type': 'RECTANGLE', 'visible': True},
        ],
    }
    assert classify_child(child, 112, 113, []) == 'content'


def test_large_frame_with_image_children_is_not_background():
    """FRAME with leaf RECTANGLE children that have IMAGE fills should NOT be background.
    Real case: 285:43815 (concept node with flag images) was misclassified as background
    because all children are leaf RECTANGLEs — but they carry IMAGE fills = content images.
    Also: clipsContent=True prevents decorative classification despite width > 150%.
    """
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 792, 'height': 1130},
        'clipsContent': True,
        'children': [
            {'type': 'RECTANGLE', 'fills': [{'type': 'IMAGE', 'visible': True, 'imageRef': 'abc'}]},
            {'type': 'RECTANGLE', 'fills': [{'type': 'IMAGE', 'visible': True, 'imageRef': 'def'}]},
            {'type': 'RECTANGLE', 'fills': [{'type': 'IMAGE', 'visible': True, 'imageRef': 'ghi'}]},
        ],
    }
    # Parent is 393x1208 — child is geo-match but has IMAGE content; clipsContent prevents decorative
    result = classify_child(child, 393, 1208, [])
    assert result != 'background', f'should not be background, got {result}'
    assert result != 'decorative', f'should not be decorative, got {result}'


def test_large_low_opacity_is_background():
    child = {
        'visible': True,
        'type': 'RECTANGLE',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'opacity': 0.3,
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1400, 'height': 880},
    }
    assert classify_child(child, 1440, 900, []) == 'background'


# ─── Decorative ────────────────────────────────────────────────────────

def test_large_blur_is_decorative():
    child = {
        'visible': True,
        'type': 'ELLIPSE',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'effects': [{'type': 'LAYER_BLUR', 'radius': 100}],
        'absoluteBoundingBox': {'x': -100, 'y': -100, 'width': 400, 'height': 400},
    }
    assert classify_child(child, 1440, 900, []) == 'decorative'


def test_overwide_element_is_decorative():
    child = {
        'visible': True,
        'type': 'LINE',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': -500, 'y': 450, 'width': 2724, 'height': 1},
    }
    assert classify_child(child, 1440, 900, []) == 'decorative'


def test_group_all_vectors_is_decorative():
    child = {
        'visible': True,
        'type': 'GROUP',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 50, 'y': 50, 'width': 100, 'height': 100},
        'children': [
            {'type': 'VECTOR'},
            {'type': 'VECTOR'},
            {'type': 'VECTOR'},
        ],
    }
    assert classify_child(child, 1440, 900, []) == 'decorative'


# ─── Content: CENTER constraint ────────────────────────────────────────

def test_center_constraint_returns_content():
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'CENTER', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 300, 'y': 100, 'width': 840, 'height': 200},
    }
    assert classify_child(child, 1440, 900, []) == 'content'


# ─── Content: boundVariables ───────────────────────────────────────────

def test_bound_variables_returns_content():
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'boundVariables': {'fills': [{'id': 'var123'}]},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200, 'height': 100},
    }
    assert classify_child(child, 1440, 900, []) == 'content'


# ─── Overlap ───────────────────────────────────────────────────────────

def test_overlapping_sibling_returns_overlap():
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 100, 'y': 100, 'width': 200, 'height': 200},
    }
    sibling = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 150, 'y': 150, 'width': 200, 'height': 200},
    }
    assert classify_child(child, 1440, 900, [child, sibling]) == 'overlap'


# ─── Default → content ─────────────────────────────────────────────────

def test_default_returns_content():
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 400, 'height': 200},
    }
    assert classify_child(child, 1440, 900, []) == 'content'


# ─── Leaf RECTANGLE/ELLIPSE → preserve_absolute ──────────────────────

def test_leaf_rectangle_no_text_returns_preserve_absolute():
    """Leaf RECTANGLE without text/children should NOT be content (e.g. image icons)."""
    child = {
        'visible': True,
        'type': 'RECTANGLE',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 100, 'y': 50, 'width': 75, 'height': 71},
    }
    assert classify_child(child, 375, 104, []) == 'preserve_absolute'


def test_leaf_ellipse_no_text_returns_preserve_absolute():
    """Leaf ELLIPSE without text/children should NOT be content."""
    child = {
        'visible': True,
        'type': 'ELLIPSE',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 50, 'y': 50, 'width': 40, 'height': 40},
    }
    assert classify_child(child, 300, 200, []) == 'preserve_absolute'


# ─── Rotated node → preserve_absolute ─────────────────────────────────

def test_rotated_node_returns_preserve_absolute():
    child = {
        'visible': True,
        'type': 'FRAME',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'rotation': 45,
        'absoluteBoundingBox': {'x': 100, 'y': 100, 'width': 200, 'height': 200},
    }
    assert classify_child(child, 1440, 900, []) == 'preserve_absolute'


# ─── Flow Conversion ───────────────────────────────────────────────────

def test_flow_basic_column():
    """Two content nodes -> flex-column with correct margin-top."""
    parent = {
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
        'width': 1440, 'height': 900,
    }
    content_nodes = [
        {
            'id': 'a',
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 0, 'y': 100, 'width': 400, 'height': 200},
        },
        {
            'id': 'b',
            'constraints': {'horizontal': 'CENTER', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 300, 'y': 350, 'width': 840, 'height': 200},
        },
    ]
    result = convert_to_flow(parent, content_nodes)
    assert result is not None
    assert result['parent_css']['display'] == 'flex'
    assert result['parent_css']['flex-direction'] == 'column'
    assert result['parent_css']['position'] == 'relative'

    children_css = result['children_css']
    assert children_css['a']['margin-top'] == '100px'
    assert children_css['b']['margin-top'] == '50px'
    assert children_css['b']['align-self'] == 'center'
    assert children_css['b']['width'] == '840px'


def test_flow_right_aligned():
    """RIGHT constraint -> align-self: flex-end."""
    parent = {
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 600},
        'width': 1440, 'height': 600,
    }
    content_nodes = [
        {
            'id': 'a',
            'constraints': {'horizontal': 'RIGHT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 1000, 'y': 50, 'width': 400, 'height': 100},
        },
    ]
    result = convert_to_flow(parent, content_nodes)
    assert result['children_css']['a']['align-self'] == 'flex-end'


def test_flow_left_right_stretch():
    """LEFT_RIGHT constraint -> width: 100%."""
    parent = {
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 600},
        'width': 1440, 'height': 600,
    }
    content_nodes = [
        {
            'id': 'a',
            'constraints': {'horizontal': 'LEFT_RIGHT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 0, 'y': 50, 'width': 1440, 'height': 100},
        },
    ]
    result = convert_to_flow(parent, content_nodes)
    assert result['children_css']['a']['width'] == '100%'


def test_flow_overlap_aborts():
    """Content nodes with Y-overlap -> returns None (abort to absolute)."""
    parent = {
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
        'width': 1440, 'height': 900,
    }
    content_nodes = [
        {
            'id': 'a',
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 0, 'y': 100, 'width': 400, 'height': 300},
        },
        {
            'id': 'b',
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 500, 'y': 200, 'width': 400, 'height': 300},
        },
    ]
    result = convert_to_flow(parent, content_nodes)
    assert result is None


def test_flow_single_center_center():
    """Single content node with CENTER+CENTER -> justify-content:center + align-items:center."""
    parent = {
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
        'width': 1440, 'height': 900,
    }
    content_nodes = [
        {
            'id': 'a',
            'constraints': {'horizontal': 'CENTER', 'vertical': 'CENTER'},
            'absoluteBoundingBox': {'x': 400, 'y': 350, 'width': 640, 'height': 200},
        },
    ]
    result = convert_to_flow(parent, content_nodes)
    assert result['parent_css']['display'] == 'flex'
    assert result['parent_css']['justify-content'] == 'center'
    assert result['parent_css']['align-items'] == 'center'


def test_flow_single_center_top():
    """Single content node with CENTER+TOP -> flex-column + align-items:center + margin-top."""
    parent = {
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
        'width': 1440, 'height': 900,
    }
    content_nodes = [
        {
            'id': 'a',
            'constraints': {'horizontal': 'CENTER', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 300, 'y': 80, 'width': 840, 'height': 200},
        },
    ]
    result = convert_to_flow(parent, content_nodes)
    assert result['parent_css']['display'] == 'flex'
    assert result['parent_css']['flex-direction'] == 'column'
    assert result['parent_css']['align-items'] == 'center'
    assert result['children_css']['a']['margin-top'] == '80px'


# ─── Adaptive Clamp ────────────────────────────────────────────────────

def test_flow_adaptive_clamp_margin():
    """When adaptive_clamp is True, margin-top should use clamp()."""
    parent = {
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
        'width': 1440, 'height': 900,
    }
    content_nodes = [
        {
            'id': 'a',
            'constraints': {'horizontal': 'CENTER', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 300, 'y': 100, 'width': 840, 'height': 200},
        },
        {
            'id': 'b',
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 0, 'y': 350, 'width': 1440, 'height': 400},
        },
    ]
    result = convert_to_flow(parent, content_nodes, adaptive_clamp=True)
    assert result is not None
    mt_a = result['children_css']['a']['margin-top']
    assert 'clamp(' in mt_a
    assert '100px' in mt_a


def test_flow_non_adaptive_plain_px():
    """Without adaptive_clamp, margin-top is plain px."""
    parent = {
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
        'width': 1440, 'height': 900,
    }
    content_nodes = [
        {
            'id': 'a',
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 0, 'y': 100, 'width': 400, 'height': 200},
        },
    ]
    result = convert_to_flow(parent, content_nodes, adaptive_clamp=False)
    assert result['children_css']['a']['margin-top'] == '100px'


# ─── Small Container Guard ─────────────────────────────────────────────

def test_small_container_skips():
    assert should_skip_classifier(80, 80) is True
    assert should_skip_classifier(100, 100) is True


def test_large_container_does_not_skip():
    assert should_skip_classifier(200, 300) is False
    assert should_skip_classifier(101, 50) is False


def test_clip_container_skips():
    """Containers with clipsContent=True and small size should skip."""
    assert should_skip_classifier(112, 113, clips_content=True) is True
    assert should_skip_classifier(150, 150, clips_content=True) is True


def test_large_clip_container_does_not_skip():
    """Large clip containers (>200px both) should not skip."""
    assert should_skip_classifier(300, 400, clips_content=True) is False


def test_one_dimension_small_does_not_skip():
    """Only skip if BOTH dimensions ≤ 100."""
    assert should_skip_classifier(50, 200) is False


# ─── E2E: Real Data Validation ─────────────────────────────────────────

import json
import os


def test_real_data_classification_stats():
    """Validate against actual Figma page data — SCALE+SCALE should be majority."""
    raw_path = os.environ.get('FIGMA_RAW_DATA_PATH')
    if not raw_path:
        candidates = [
            Path(__file__).parent.parent.parent / '.figma-to-code' / '1-raw-data' / '8456-11015-raw-data.json',
        ]
        for p in candidates:
            if p.exists():
                raw_path = str(p)
                break

    if not raw_path or not Path(raw_path).exists():
        print('  ⚠  Skipping e2e test — raw data not found')
        return

    with open(raw_path) as f:
        data = json.load(f)

    # Try both structures: data.nodes[id].document and data.data.nodes[id].document
    nodes = data.get('nodes') or data.get('data', {}).get('nodes', {})
    root_node = None
    for nid, node_data in nodes.items():
        doc = node_data.get('document')
        if doc and doc.get('type') == 'FRAME':
            root_node = doc
            break

    assert root_node is not None, "No root FRAME found in raw data"

    scale_count = 0
    content_count = 0

    def _nw(n):
        return n.get('width') or (n.get('absoluteBoundingBox') or {}).get('width', 0) or 0

    def _nh(n):
        return n.get('height') or (n.get('absoluteBoundingBox') or {}).get('height', 0) or 0

    def walk(node):
        nonlocal scale_count, content_count
        if not node.get('children'):
            return
        parent_lm = node.get('layoutMode')
        if parent_lm and parent_lm != 'NONE':
            for c in node.get('children', []):
                walk(c)
            return
        pw = _nw(node)
        ph = _nh(node)
        if pw <= 100 and ph <= 100:
            for c in node.get('children', []):
                walk(c)
            return
        for c in (node.get('children') or []):
            if c.get('visible') is False:
                continue
            cat = classify_child(c, pw, ph, [])
            if cat == 'preserve_absolute':
                scale_count += 1
            elif cat == 'content':
                content_count += 1
            walk(c)

    walk(root_node)

    total = scale_count + content_count
    if total > 0:
        scale_ratio = scale_count / total
        print(f'  ℹ  SCALE+SCALE: {scale_count}/{total} ({scale_ratio:.0%})')
        print(f'  ℹ  Content: {content_count}/{total} ({1-scale_ratio:.0%})')
        assert scale_ratio > 0.5, f"Expected majority SCALE+SCALE, got {scale_ratio:.0%}"
    else:
        print('  ⚠  No classified nodes found')


# ─── Runner ────────────────────────────────────────────────────────────

def run_all():
    tests = [v for k, v in globals().items() if k.startswith('test_')]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f'  ✓  {t.__name__}')
            passed += 1
        except AssertionError as e:
            print(f'  ✗  {t.__name__}: {e}')
            failed += 1
    print(f'\n  {"✅" if not failed else "❌"}  {passed}/{passed+failed} passed')
    return failed == 0


if __name__ == '__main__':
    success = run_all()
    sys.exit(0 if success else 1)
