#!/usr/bin/env python3
"""Unit tests for ir_builder module — visibility filtering logic."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.ir_builder import _is_empty_render_node


# ─── Test runner ──────────────────────────────────────────────────────────────

_pass = _fail = 0
_failures = []


def check(test_id, desc, condition):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f'  ✓  [{test_id}] {desc}')
    else:
        _fail += 1
        _failures.append(f'[{test_id}] {desc}')
        print(f'  ✗  [{test_id}] {desc}')


# ─── Empty render node detection ─────────────────────────────────────

def test_empty_render_nodes():
    print('\n── _is_empty_render_node tests ──')

    # 1. Empty fills + null render bounds (key present) → empty (the shoe-toe ghost case)
    node = {
        'type': 'RECTANGLE',
        'fills': [],
        'absoluteRenderBounds': None,
        'effects': [{'type': 'DROP_SHADOW', 'visible': True}],
    }
    check('IR-01', 'empty fills + null renderBounds + effects → empty', _is_empty_render_node(node) is True)

    # 2. Visible IMAGE fill + real render bounds → NOT empty
    node = {
        'type': 'RECTANGLE',
        'fills': [{'type': 'IMAGE', 'visible': True}],
        'absoluteRenderBounds': {'x': 10, 'y': 10, 'width': 100, 'height': 50},
    }
    check('IR-02', 'visible fill + renderBounds → not empty', _is_empty_render_node(node) is False)

    # 3. All fills invisible + null render bounds → empty
    node = {
        'type': 'RECTANGLE',
        'fills': [
            {'type': 'IMAGE', 'visible': False},
            {'type': 'SOLID', 'visible': False},
        ],
        'absoluteRenderBounds': None,
    }
    check('IR-03', 'all fills invisible + null renderBounds → empty', _is_empty_render_node(node) is True)

    # 4. No fills key + null render bounds → empty
    node = {
        'type': 'RECTANGLE',
        'absoluteRenderBounds': None,
    }
    check('IR-04', 'no fills key + null renderBounds → empty', _is_empty_render_node(node) is True)

    # 5. Node with children → never empty (children may render)
    node = {
        'type': 'FRAME',
        'fills': [],
        'absoluteRenderBounds': None,
        'children': [{'type': 'TEXT', 'visible': True}],
    }
    check('IR-05', 'has children → not empty', _is_empty_render_node(node) is False)

    # 6. TEXT node → never empty (has character content)
    node = {
        'type': 'TEXT',
        'fills': [],
        'absoluteRenderBounds': None,
        'characters': 'Hello',
    }
    check('IR-06', 'TEXT node → not empty', _is_empty_render_node(node) is False)

    # 7. Node with visible strokes + null renderBounds → empty
    # If Figma says renderBounds=null, the node produces zero pixels regardless of strokes.
    # In practice, truly visible strokes would give non-null renderBounds.
    node = {
        'type': 'RECTANGLE',
        'fills': [],
        'absoluteRenderBounds': None,
        'strokes': [{'type': 'SOLID', 'visible': True}],
    }
    check('IR-07', 'visible strokes + null renderBounds → empty (Figma verdict)', _is_empty_render_node(node) is True)

    # 8. Has absoluteRenderBounds (non-null) → never empty
    node = {
        'type': 'RECTANGLE',
        'fills': [],
        'absoluteRenderBounds': {'x': 0, 'y': 0, 'width': 10, 'height': 10},
    }
    check('IR-08', 'renderBounds present → not empty', _is_empty_render_node(node) is False)

    # 9. Real case: 1280:21072 (empty shell with drop-shadow + rotation)
    node = {
        'id': '1280:21072',
        'name': 'jimeng-2026-04-27-9635-去掉背景 1',
        'type': 'RECTANGLE',
        'rotation': 3.141592653589793,
        'fills': [],
        'strokes': [],
        'absoluteRenderBounds': None,
        'effects': [{'type': 'DROP_SHADOW', 'visible': True, 'radius': 1.89}],
    }
    check('IR-09', 'real case 1280:21072 (empty shell with shadow) → empty', _is_empty_render_node(node) is True)

    # 10. Key absent (mock data / old API) → safe default: not empty
    node = {
        'type': 'FRAME',
        'fills': [],
        'strokes': [],
        'children': [],
    }
    check('IR-10', 'absoluteRenderBounds key absent → not empty (safe default)', _is_empty_render_node(node) is False)

    # 11. Leaf node with visible IMAGE fill but renderBounds=null → empty
    # Real case: 1280:21064 — image of 3 shoes, masked to show 1, but mask clips all pixels.
    # Figma's verdict: absoluteRenderBounds=null means NO pixels rendered.
    node = {
        'id': '1280:21064',
        'type': 'RECTANGLE',
        'fills': [{'type': 'IMAGE', 'visible': True, 'imageRef': '0b5efca228b5fcb532a6'}],
        'strokes': [],
        'absoluteRenderBounds': None,
    }
    check('IR-14', 'visible IMAGE fill + null renderBounds (leaf) → empty', _is_empty_render_node(node) is True)

    # 12. Leaf node with visible SOLID fill but renderBounds=null → empty
    node = {
        'id': '285:43795',
        'type': 'RECTANGLE',
        'fills': [{'type': 'SOLID', 'visible': True, 'color': {'r': 0.5, 'g': 0.5, 'b': 0.5, 'a': 1}}],
        'strokes': [],
        'absoluteRenderBounds': None,
    }
    check('IR-15', 'visible SOLID fill + null renderBounds (leaf) → empty', _is_empty_render_node(node) is True)


# ─── INNER_SHADOW on transparent container ───────────────────────────────────

def test_inner_shadow_on_transparent_container():
    """INNER_SHADOW on a container with no visible fills should NOT generate box-shadow."""
    from lib.css_extractor import extract_css

    print('\n── INNER_SHADOW on transparent container ──')

    # Soccer ball case: FRAME with fills=[{visible:False}], INNER_SHADOW + DROP_SHADOW
    node = {
        'type': 'FRAME',
        'id': '286:66351',
        'width': 37, 'height': 37,
        'layoutMode': None,
        'fills': [{'type': 'SOLID', 'color': {'r': 1, 'g': 1, 'b': 1, 'a': 1}, 'visible': False}],
        'strokes': [],
        'strokeWeight': 0.015,
        'effects': [
            {'type': 'DROP_SHADOW', 'visible': True, 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0.25},
             'offset': {'x': 0, 'y': 1.38}, 'radius': 1.38, 'showShadowBehindNode': False},
            {'type': 'INNER_SHADOW', 'visible': True, 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0.25},
             'offset': {'x': 0, 'y': -0.78}, 'radius': 0.34},
        ],
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 37, 'height': 37},
    }
    ctx = {'layoutMode': 'NONE', 'width': 400, 'height': 400, 'absX': 0, 'absY': 0, 'isRoot': False}
    css = extract_css(node, ctx)

    has_box_shadow = 'box-shadow' in css
    has_filter = 'filter' in css

    check('IR-11', 'transparent container: no box-shadow (inset shadow suppressed)',
          not has_box_shadow)
    check('IR-12', 'transparent container: DROP_SHADOW → filter: drop-shadow',
          has_filter and 'drop-shadow' in css.get('filter', ''))




# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    test_empty_render_nodes()
    test_inner_shadow_on_transparent_container()

    print(f'\n{"═" * 44}')
    print(f'  ir_builder tests: {_pass} passed, {_fail} failed')
    print('═' * 44)
    if _failures:
        print('\n  FAILURES:')
        for f in _failures:
            print(f'    {f}')
        sys.exit(1)
    sys.exit(0)
