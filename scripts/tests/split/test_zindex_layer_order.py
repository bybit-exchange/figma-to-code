#!/usr/bin/env python3
"""
BUG-3: Section 间 absolute 定位导致节点遮挡。

根因：脚本按 y 坐标排序 Section 的 DOM 顺序，导致 Figma 中底层的背景/装饰节点
（child index 小、y 值大）被渲染在后面的 DOM 位置 → 层叠在内容之上。

修复：对 position:absolute 的 section root class 注入 z-index，值为 Figma
IR children 数组中的 index，确保低层级节点始终在视觉堆叠的下方。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()


def test_zindex_injected_for_absolute_section():
    """U-300: position:absolute 的 section root class 应注入 z-index。"""
    from lib.split_codegen import _css_from_orig
    from lib.scss_generator import _collect_classes

    ir = {
        'figmaId': '235:14240',
        'figmaType': 'FRAME',
        'figmaName': 'background-image',
        'css': {'width': '1440px', 'height': '537px'},
        'children': [],
        'semantic': {'htmlTag': 'div', 'className': 'node-235-14240',
                     'componentName': None, 'isExtractedComponent': False, 'props': []},
    }

    orig_css_map = {
        'node-235-14240': {
            'width': '1440px',
            'height': '537px',
            'position': 'absolute',
            'left': '0px',
            'top': '5675px',
        }
    }

    result = _css_from_orig(ir, orig_css_map, 'less', root_z_index=1)

    check('U-300a', 'z-index:1 in output', 'z-index: 1' in result)


def test_zindex_injected_for_relative_positioned_section():
    """U-301: position:relative の section も root_z_index が指定された場合 z-index を注入する。

    [Updated behavior] Previously z-index was only injected for position:absolute. This was
    changed because CSS stacking puts absolute+z-index:N above relative+z-index:auto regardless
    of DOM order — causing background rectangles (z-index:1) to cover relative PC sections.

    Fix (U-424/U-436): Rule 5 now injects z-index for both absolute and relative positioned
    roots when root_z_index is provided, so merged page's HomepageWebSection (position:relative)
    gets z-index:7 and appears correctly above H5 background rectangles (z-index:0).
    """
    from lib.split_codegen import _css_from_orig

    ir = {
        'figmaId': '311:15190',
        'figmaType': 'FRAME',
        'figmaName': 'content-section',
        'css': {'display': 'flex'},
        'children': [],
        'semantic': {'htmlTag': 'div', 'className': 'content-section',
                     'componentName': None, 'isExtractedComponent': False, 'props': []},
    }

    orig_css_map = {
        'content-section': {
            'display': 'flex',
            'flex-direction': 'column',
            'position': 'relative',
        }
    }

    result = _css_from_orig(ir, orig_css_map, 'less', root_z_index=5)

    check('U-301a', 'z-index:5 IS injected for position:relative when root_z_index=5',
          'z-index: 5' in result)
    check('U-301b', 'position:relative preserved (not stripped)', 'position: relative' in result)


def test_zindex_not_injected_when_already_present():
    """U-302: 原始 CSS 已有 z-index 时不覆盖。"""
    from lib.split_codegen import _css_from_orig

    ir = {
        'figmaId': '100:1',
        'figmaType': 'FRAME',
        'figmaName': 'modal',
        'css': {'position': 'absolute', 'z-index': '999', 'top': '0px'},
        'children': [],
        'semantic': {'htmlTag': 'div', 'className': 'modal',
                     'componentName': None, 'isExtractedComponent': False, 'props': []},
    }

    orig_css_map = {
        'modal': {
            'position': 'absolute',
            'z-index': '999',
            'top': '0px',
        }
    }

    result = _css_from_orig(ir, orig_css_map, 'less', root_z_index=2)

    check('U-302a', 'original z-index:999 preserved', 'z-index: 999' in result)
    check('U-302b', 'z-index:2 NOT injected', 'z-index: 2' not in result)


def test_zindex_none_means_no_injection():
    """U-303: root_z_index=None 时不注入（无 IR 文件的 fallback）。"""
    from lib.split_codegen import _css_from_orig

    ir = {
        'figmaId': '200:1',
        'figmaType': 'FRAME',
        'figmaName': 'section',
        'css': {},
        'children': [],
        'semantic': {'htmlTag': 'div', 'className': 'section',
                     'componentName': None, 'isExtractedComponent': False, 'props': []},
    }

    orig_css_map = {
        'section': {
            'position': 'absolute',
            'top': '100px',
        }
    }

    result = _css_from_orig(ir, orig_css_map, 'less', root_z_index=None)

    check('U-303a', 'no z-index when root_z_index is None', 'z-index' not in result)


# ── Run ──
print('\n── split z-index layer order tests ──')
test_zindex_injected_for_absolute_section()
test_zindex_injected_for_relative_positioned_section()
test_zindex_not_injected_when_already_present()
test_zindex_none_means_no_injection()
print_summary('split/test_zindex_layer_order')
