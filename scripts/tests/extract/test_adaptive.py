"""
Unit tests for adaptive output feature (signal detection + clamp + sizing).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.color import px, clamp_value, should_adaptive
from lib.css_extractor import extract_css


# ─── clamp_value tests ───────────────────────────────────────────────

def test_clamp_zero():
    assert clamp_value(0) == '0'

def test_clamp_below_threshold():
    assert clamp_value(1) == '1px'
    assert clamp_value(2) == '2px'
    assert clamp_value(-1) == '-1px'

def test_clamp_normal():
    result = clamp_value(48)
    assert result.startswith('clamp(')
    assert '48px' in result  # max is design value
    assert 'vw' in result

def test_clamp_large():
    result = clamp_value(1200)
    assert result.startswith('clamp(')
    assert '1200px' in result

def test_clamp_min_less_than_max():
    result = clamp_value(100)
    parts = result[6:-1].split(',')
    min_v = float(parts[0].strip().rstrip('px'))
    max_v = float(parts[2].strip().rstrip('px'))
    assert min_v < max_v


# ─── should_adaptive tests ───────────────────────────────────────────

def test_adaptive_vertical_with_fill():
    node = {
        'layoutMode': 'VERTICAL',
        'children': [
            {'layoutSizingHorizontal': 'FILL'},
            {'layoutSizingHorizontal': 'FILL'},
            {'layoutSizingHorizontal': 'FIXED'},
        ]
    }
    assert should_adaptive(node) is True

def test_adaptive_horizontal_with_fill():
    node = {
        'layoutMode': 'HORIZONTAL',
        'children': [
            {'layoutSizingHorizontal': 'FILL'},
            {'layoutSizingHorizontal': 'FILL'},
        ]
    }
    assert should_adaptive(node) is True

def test_no_adaptive_no_layout():
    node = {
        'layoutMode': None,
        'children': [{'layoutSizingHorizontal': 'FILL'}]
    }
    assert should_adaptive(node) is False

def test_no_adaptive_below_threshold():
    node = {
        'layoutMode': 'VERTICAL',
        'children': [
            {'layoutSizingHorizontal': 'FIXED'},
            {'layoutSizingHorizontal': 'FIXED'},
            {'layoutSizingHorizontal': 'FILL'},
        ]
    }
    assert should_adaptive(node) is False

def test_no_adaptive_no_children():
    node = {'layoutMode': 'VERTICAL', 'children': []}
    assert should_adaptive(node) is False


# ─── extract_css adaptive vs non-adaptive ────────────────────────────

def test_typography_adaptive():
    node = {
        'type': 'TEXT',
        'layoutSizingHorizontal': 'HUG',
        'style': {
            'fontSize': 48,
            'fontWeight': 700,
            'lineHeightUnit': 'PIXELS',
            'lineHeightPx': 56,
        },
        'characters': 'Test',
        'width': 200, 'height': 56,
    }
    ctx = {'layoutMode': 'VERTICAL', 'width': 1440, 'height': 900, '_adaptive': True, '_adaptive_clamp': True}
    css = extract_css(node, ctx)
    assert 'clamp(' in css['font-size']
    assert 'clamp(' in css['line-height']

def test_typography_non_adaptive():
    node = {
        'type': 'TEXT',
        'layoutSizingHorizontal': 'HUG',
        'style': {
            'fontSize': 48,
            'fontWeight': 700,
            'lineHeightUnit': 'PIXELS',
            'lineHeightPx': 56,
        },
        'characters': 'Test',
        'width': 200, 'height': 56,
    }
    ctx = {'layoutMode': 'VERTICAL', 'width': 1440, 'height': 900, '_adaptive': False}
    css = extract_css(node, ctx)
    assert css['font-size'] == '48px'
    assert css['line-height'] == '56px'

def test_fill_vertical_adaptive():
    node = {
        'type': 'FRAME',
        'layoutSizingHorizontal': 'FILL',
        'layoutSizingVertical': 'FIXED',
        'width': 1440, 'height': 800,
        'layoutMode': 'VERTICAL',
    }
    ctx = {'layoutMode': 'VERTICAL', 'width': 1440, 'height': 5000, '_adaptive': True}
    css = extract_css(node, ctx)
    assert css['width'] == '100%'

def test_fixed_content_adaptive():
    node = {
        'type': 'FRAME',
        'layoutSizingHorizontal': 'FIXED',
        'layoutSizingVertical': 'HUG',
        'width': 1200, 'height': 400,
        'layoutMode': 'VERTICAL',
    }
    ctx = {'layoutMode': 'VERTICAL', 'width': 1440, 'height': 5000, '_adaptive': True}
    css = extract_css(node, ctx)
    assert css.get('max-width') == '1200px'
    assert css['width'] == '100%'
    assert css['margin-left'] == 'auto'
    assert css['margin-right'] == 'auto'

def test_fixed_content_non_adaptive():
    node = {
        'type': 'FRAME',
        'layoutSizingHorizontal': 'FIXED',
        'layoutSizingVertical': 'HUG',
        'width': 1200, 'height': 400,
        'layoutMode': 'VERTICAL',
    }
    ctx = {'layoutMode': 'VERTICAL', 'width': 1440, 'height': 5000, '_adaptive': False}
    css = extract_css(node, ctx)
    assert css['width'] == '1200px'
    assert 'max-width' not in css

def test_gap_adaptive():
    node = {
        'type': 'FRAME',
        'layoutSizingHorizontal': 'FILL',
        'layoutSizingVertical': 'HUG',
        'width': 1440, 'height': 400,
        'layoutMode': 'VERTICAL',
        'itemSpacing': 24,
    }
    ctx = {'layoutMode': 'VERTICAL', 'width': 1440, 'height': 5000, '_adaptive': True, '_adaptive_clamp': True}
    css = extract_css(node, ctx)
    assert 'clamp(' in css.get('gap', '')

def test_padding_adaptive():
    node = {
        'type': 'FRAME',
        'layoutSizingHorizontal': 'FILL',
        'layoutSizingVertical': 'FIXED',
        'width': 1440, 'height': 400,
        'layoutMode': 'VERTICAL',
        'paddingTop': 40, 'paddingRight': 120,
        'paddingBottom': 40, 'paddingLeft': 120,
    }
    ctx = {'layoutMode': 'VERTICAL', 'width': 1440, 'height': 5000, '_adaptive': True, '_adaptive_clamp': True}
    css = extract_css(node, ctx)
    assert 'clamp(' in css.get('padding', '')


# ─── Runner ──────────────────────────────────────────────────────────

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
