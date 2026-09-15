"""测试图片合成检测 + 整帧导出逻辑。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib import tsx_generator
from lib.tsx_generator import (
    _is_image_composition,
    render_jsx_body_with_leaf_refs,
    FLATTEN_IMAGE_COMPOSITIONS,
)


def _img_node(fid, name='img', w=200, h=150):
    return {
        'figmaId': fid, 'figmaName': name, 'figmaType': 'RECTANGLE',
        'isImageNode': True, 'isTextNode': False, 'isVectorNode': False,
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': w, 'height': h},
        'css': {'position': 'absolute', 'width': f'{w}px', 'height': f'{h}px'},
        'children': [],
        'localAssetPath': f'/assets/test/{fid}.png',
    }


def _vec_node(fid, name='vector', w=100, h=80):
    return {
        'figmaId': fid, 'figmaName': name, 'figmaType': 'VECTOR',
        'isImageNode': False, 'isTextNode': False, 'isVectorNode': True,
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': w, 'height': h},
        'css': {'position': 'absolute', 'width': f'{w}px', 'height': f'{h}px'},
        'children': [],
    }


def _text_node(fid, text='Hello'):
    return {
        'figmaId': fid, 'figmaName': 'label', 'figmaType': 'TEXT',
        'isImageNode': False, 'isTextNode': True, 'isVectorNode': False,
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': 100, 'height': 20},
        'css': {}, 'children': [],
        'textContent': text,
    }


def _frame_node(fid, children, w=548, h=346):
    return {
        'figmaId': fid, 'figmaName': 'Container', 'figmaType': 'FRAME',
        'isImageNode': False, 'isTextNode': False, 'isVectorNode': False,
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': w, 'height': h},
        'css': {'position': 'relative', 'width': f'{w}px', 'height': f'{h}px'},
        'children': children,
    }


def _instance_node(fid, comp_id='comp:1', w=100, h=80):
    """交互性组件实例（含文本子节点，非装饰性）。"""
    return {
        'figmaId': fid, 'figmaName': 'Button', 'figmaType': 'INSTANCE',
        'isImageNode': False, 'isTextNode': False, 'isVectorNode': False,
        'isComponentInstance': True, 'componentId': comp_id,
        'bb': {'width': w, 'height': h},
        'css': {'position': 'absolute'},
        'children': [
            _text_node(f'{fid}:label', 'Click me'),
        ],
    }


def _deco_instance_node(fid, comp_id='comp:deco', w=200, h=150):
    """纯装饰性组件实例（内部只有图片，无文本）。"""
    return {
        'figmaId': fid, 'figmaName': 'BGLayer', 'figmaType': 'INSTANCE',
        'isImageNode': False, 'isTextNode': False, 'isVectorNode': False,
        'isComponentInstance': True, 'componentId': comp_id,
        'bb': {'width': w, 'height': h},
        'css': {'position': 'absolute'},
        'children': [
            _img_node(f'{fid}:bg', 'bg', w, h),
        ],
    }


# ── 检测逻辑测试 ────────────────────────────────────────────────────────────────

def test_detect_pure_image_composition():
    """IMG-COMP-01: 2+ absolute 图片子节点 = 层叠合成。"""
    node = _frame_node('card:1', [
        _img_node('bg:1', 'BG-GREEN', 548, 346),
        _frame_node('butterfly:1', [
            _img_node('butterfly:img', 'Butterfly_1', 220, 175),
        ], 220, 175),
    ])
    node['children'][1]['css']['position'] = 'absolute'
    assert _is_image_composition(node) is True


def test_detect_rejects_with_text():
    """IMG-COMP-02: 子树中有文本节点 → 不是图片合成。"""
    node = _frame_node('card:2', [
        _img_node('bg:1', 'BG', 548, 346),
        _text_node('title:1', 'Hello World'),
        _img_node('deco:1', 'deco', 100, 100),
    ])
    assert _is_image_composition(node) is False


def test_detect_rejects_with_interactive():
    """IMG-COMP-03: 子树含交互性组件实例（内部有文本）→ 不是图片合成。"""
    node = _frame_node('card:3', [
        _img_node('bg:1', 'BG', 548, 346),
        _instance_node('btn:1', 'comp:button'),
    ])
    assert _is_image_composition(node) is False


def test_detect_rejects_single_child():
    """IMG-COMP-04: 只有 1 个子节点 → 没有合成，不触发。"""
    node = _frame_node('card:4', [
        _img_node('bg:1', 'BG', 548, 346),
    ])
    assert _is_image_composition(node) is False


def test_detect_with_deco_instances():
    """IMG-COMP-05: 子树含纯装饰性 instance（内部只有图片）→ 仍检测为合成。"""
    node = _frame_node('card:5', [
        _deco_instance_node('inst:bg1', 'comp:bg', 548, 346),
        _deco_instance_node('inst:bg2', 'comp:butterfly', 220, 175),
    ])
    assert _is_image_composition(node) is True


def test_detect_rejects_non_absolute_layout():
    """IMG-COMP-06: 子节点非 absolute → 非层叠，不是合成。"""
    node = _frame_node('card:6', [
        _img_node('img:1', 'Photo1', 200, 150),
        _img_node('img:2', 'Photo2', 200, 150),
        _img_node('img:3', 'Photo3', 200, 150),
    ])
    for c in node['children']:
        c['css']['position'] = 'relative'
    assert _is_image_composition(node) is False


def test_detect_rejects_flex_gap_gallery():
    """IMG-COMP-07: flex + gap 的图片列表（gallery）→ 不是合成。"""
    node = _frame_node('gallery:1', [
        _img_node('img:1', 'Photo1', 200, 150),
        _img_node('img:2', 'Photo2', 200, 150),
        _img_node('img:3', 'Photo3', 200, 150),
    ])
    node['css']['display'] = 'flex'
    node['css']['gap'] = '16px'
    assert _is_image_composition(node) is False


def test_detect_mixed_absolute_only_counts_absolute():
    """IMG-COMP-08: 只有 1 个 absolute 子节点 + 1 个 relative → 不够 2 层。"""
    node = _frame_node('card:8', [
        _img_node('bg:1', 'BG', 548, 346),  # absolute
        _img_node('icon:1', 'icon', 24, 24),  # will be relative
    ])
    node['children'][1]['css']['position'] = 'relative'
    assert _is_image_composition(node) is False


# ── 渲染拦截测试 ─────────────────────────────────────────────────────────────────

def test_render_flatten_outputs_single_img():
    """IMG-COMP-06: 开关开启时，图片合成节点渲染为单个 <img>。"""
    old_val = tsx_generator.FLATTEN_IMAGE_COMPOSITIONS
    tsx_generator.FLATTEN_IMAGE_COMPOSITIONS = True
    try:
        node = _frame_node('8456:11060', [
            _img_node('bg:1', 'BG-GREEN', 548, 346),
            _img_node('bg:2', 'BG-overlay', 548, 346),
            _frame_node('butterfly:1', [
                _img_node('butterfly:img', 'Butterfly_1', 220, 175),
            ], 220, 175),
            _vec_node('deco:1', 'sparkle', 50, 50),
        ])
        node['children'][2]['css']['position'] = 'absolute'
        node['localAssetPath'] = '/assets/LandingPage/8456-11060.png'
        # Add semantic so _render_node processes it
        node['semantic'] = {'htmlTag': 'div', 'className': 'cardbaselayer', 'props': []}
        for c in node['children']:
            c['semantic'] = {'htmlTag': 'div', 'className': 'child', 'props': []}
            for gc in c.get('children', []):
                gc['semantic'] = {'htmlTag': 'img', 'className': 'inner', 'props': []}

        result = render_jsx_body_with_leaf_refs(node, leaf_map={})
        assert '<img' in result
        assert '/assets/LandingPage/8456-11060.png' in result
        assert 'cardbaselayer' in result
        # 子节点不应出现
        assert 'BG-GREEN' not in result
        assert 'butterfly' not in result.lower() or 'butterfly' not in result
    finally:
        tsx_generator.FLATTEN_IMAGE_COMPOSITIONS = old_val


def test_render_flatten_disabled_preserves_children():
    """IMG-COMP-07: 开关关闭时，保持原有递归渲染。"""
    old_val = tsx_generator.FLATTEN_IMAGE_COMPOSITIONS
    tsx_generator.FLATTEN_IMAGE_COMPOSITIONS = False
    try:
        node = _frame_node('8456:11060', [
            _img_node('bg:1', 'BG-GREEN', 548, 346),
            _img_node('bg:2', 'BG-overlay', 548, 346),
            _frame_node('butterfly:1', [
                _img_node('butterfly:img', 'Butterfly_1', 220, 175),
            ], 220, 175),
            _vec_node('deco:1', 'sparkle', 50, 50),
        ])
        node['children'][2]['css']['position'] = 'absolute'
        node['localAssetPath'] = '/assets/LandingPage/8456-11060.png'
        node['semantic'] = {'htmlTag': 'div', 'className': 'cardbaselayer', 'props': []}
        for c in node['children']:
            c['semantic'] = {'htmlTag': 'div', 'className': 'child', 'props': []}
            for gc in c.get('children', []):
                gc['semantic'] = {'htmlTag': 'img', 'className': 'inner', 'props': []}

        result = render_jsx_body_with_leaf_refs(node, leaf_map={})
        # 开关关闭：应该递归渲染子节点
        assert 'child' in result
        # 不应该是单个 img
        lines_with_img = [l for l in result.splitlines() if '<img' in l and 'cardbaselayer' in l]
        assert len(lines_with_img) == 0
    finally:
        tsx_generator.FLATTEN_IMAGE_COMPOSITIONS = old_val


def test_render_flatten_generates_placeholder_path():
    """IMG-COMP-08: 无 localAssetPath 时，生成 __flatten__ 占位路径。"""
    old_val = tsx_generator.FLATTEN_IMAGE_COMPOSITIONS
    tsx_generator.FLATTEN_IMAGE_COMPOSITIONS = True
    try:
        node = _frame_node('8456:11060', [
            _img_node('bg:1', 'BG-GREEN', 548, 346),
            _img_node('bg:2', 'BG-overlay', 548, 346),
            _frame_node('butterfly:1', [
                _img_node('butterfly:img', 'Butterfly_1', 220, 175),
            ], 220, 175),
            _vec_node('deco:1', 'sparkle', 50, 50),
        ])
        node['children'][2]['css']['position'] = 'absolute'
        # 不设置 localAssetPath
        node['semantic'] = {'htmlTag': 'div', 'className': 'cardbaselayer', 'props': []}
        for c in node['children']:
            c['semantic'] = {'htmlTag': 'div', 'className': 'child', 'props': []}
            for gc in c.get('children', []):
                gc['semantic'] = {'htmlTag': 'img', 'className': 'inner', 'props': []}

        result = render_jsx_body_with_leaf_refs(node, leaf_map={})
        assert '__flatten__' in result
        assert '8456-11060' in result
    finally:
        tsx_generator.FLATTEN_IMAGE_COMPOSITIONS = old_val


if __name__ == '__main__':
    tests = [
        test_detect_pure_image_composition,
        test_detect_rejects_with_text,
        test_detect_rejects_with_interactive,
        test_detect_rejects_single_child,
        test_detect_with_deco_instances,
        test_detect_rejects_non_absolute_layout,
        test_detect_rejects_flex_gap_gallery,
        test_detect_mixed_absolute_only_counts_absolute,
        test_render_flatten_outputs_single_img,
        test_render_flatten_disabled_preserves_children,
        test_render_flatten_generates_placeholder_path,
    ]
    passed = 0
    for t in tests:
        try:
            t(); print(f'  ✓ {t.__name__}'); passed += 1
        except Exception as e:
            print(f'  ✗ {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')
