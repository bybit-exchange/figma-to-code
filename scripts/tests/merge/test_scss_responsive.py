import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.scss_generator import generate_scss


def _ir(cls: str, css: dict, children: list = None, **extra) -> dict:
    return {
        'figmaId': '1:1', 'figmaName': cls,
        'css': css, 'children': children or [],
        'semantic': {'className': cls, 'htmlTag': 'div', 'componentName': cls, 'props': []},
        **extra,
    }


def test_responsive_field_emits_media_block():
    ir = _ir('hero', {'display': 'flex', 'flexDirection': 'row'},
             responsive=[{'breakpoint': 768, 'css': {'flexDirection': 'column'}, 'confidence': 0.92}])
    css = generate_scss(ir)
    assert '@media (max-width: 768px)' in css
    assert 'flex-direction: column' in css


def test_no_responsive_field_no_media_block():
    ir = _ir('hero', {'display': 'flex'})
    css = generate_scss(ir)
    assert '@media' not in css


def test_pc_only_emits_hide_at_mobile():
    ir = _ir('decoration', {'position': 'absolute'}, pcOnly=True)
    css = generate_scss(ir)
    assert '@media (max-width: 768px)' in css
    assert 'display: none' in css
    assert '@media (min-width:' not in css


def test_h5_only_emits_hide_at_desktop():
    ir = _ir('mobile-cta', {'display': 'flex'}, h5Only=True)
    css = generate_scss(ir)
    assert '@media (min-width: 769px)' in css
    assert 'display: none' in css


def test_responsive_breakpoint_from_field():
    ir = _ir('hero', {'width': '100%'},
             responsive=[{'breakpoint': 960, 'css': {'padding': '0 16px'}, 'confidence': 0.8}])
    css = generate_scss(ir)
    assert '@media (max-width: 960px)' in css


def test_responsive_empty_css_no_empty_media_block():
    ir = _ir('hero', {'display': 'flex'},
             responsive=[{'breakpoint': 768, 'css': {}, 'confidence': 0.9}])
    css = generate_scss(ir)
    # Empty @media block should not be emitted
    assert '@media' not in css


def test_nested_responsive_also_emitted():
    child = _ir('inner', {'fontSize': '24px'},
                responsive=[{'breakpoint': 768, 'css': {'fontSize': '16px'}, 'confidence': 0.85}])
    ir = _ir('section', {'padding': '40px'}, children=[child])
    css = generate_scss(ir)
    assert 'font-size: 16px' in css
    assert '@media (max-width: 768px)' in css


def test_h5_root_height_injects_mobile_min_height():
    # Real data: merged root from Page_首页 (merged-315-29892).
    # H5 root (Page_by Meta 首页) had height 4438px.
    # When match rate is low, merged root needs mobile min-height so H5
    # absolutely-positioned content is not clipped on mobile viewport.
    ir = _ir('page_', {
        'width': '1440px',
        'display': 'flex',
        'flex-direction': 'row',
        'background-color': '#000000',
    }, h5RootHeight=4438.0)
    css = generate_scss(ir)
    assert '@media (max-width: 768px)' in css, 'mobile media block missing'
    assert 'min-height: 4438px' in css, 'mobile min-height missing'
    assert 'flex-direction: column' in css, 'mobile flex-direction:column missing'


def test_h5_root_height_absent_no_extra_media():
    # No h5RootHeight → no injected mobile media block
    ir = _ir('page_', {'width': '1440px', 'display': 'flex'})
    css = generate_scss(ir)
    assert '@media' not in css
