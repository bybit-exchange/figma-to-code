#!/usr/bin/env python3
"""
Inline node 根节点 CSS 剥离画布定位属性。

根因：page_flow 未触发时（children 有重叠），depth-1 inline nodes 保留 position:absolute。
split_codegen 的 is_inline_root 参数在拆分阶段将 inline nodes 转为正常文档流。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()

from lib.split_codegen import _css_from_orig, _is_section_overlay, _page_can_flow


def _make_ir(cls_name, css, children=None):
    return {
        'figmaId': '1:1',
        'semantic': {'htmlTag': 'div', 'className': cls_name, 'componentName': 'Test', 'props': []},
        'css': css,
        'children': children or [],
    }


def test_inline_root_strips_absolute_positioning():
    """INL-01: Inline root position:absolute + top/left 被剥离，top 转 margin-top。"""
    ir = _make_ir('navbar', {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '0px',
        'display': 'flex', 'flex-direction': 'column',
    })
    orig = {'navbar': {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '0px',
        'display': 'flex', 'flex-direction': 'column', 'align-items': 'center',
    }}
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True, section_orig_top=0)

    check('INL-01a', 'no position:absolute', 'position: absolute' not in result)
    check('INL-01b', 'has position:relative', 'position: relative' in result)
    check('INL-01c', 'no left', 'left: 0px' not in result)
    check('INL-01d', 'no top (was 0)', 'top: 0px' not in result)
    check('INL-01e', 'no margin-top (top was 0)', 'margin-top' not in result)
    check('INL-01f', 'width preserved', 'width: 375px' in result)
    check('INL-01g', 'display preserved', 'display: flex' in result)


def test_inline_root_top_to_margin_top_with_offset():
    """INL-02: top=91px 减去 section_orig_top=88 → margin-top: 3px。"""
    ir = _make_ir('frame-text', {
        'width': '270px', 'position': 'absolute',
        'left': '52.5px', 'top': '91px',
        'display': 'flex', 'flex-direction': 'column',
    })
    orig = {'frame-text': {
        'width': '270px', 'position': 'absolute',
        'left': '52.5px', 'top': '91px',
        'display': 'flex', 'flex-direction': 'column', 'align-items': 'center',
    }}
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True, section_orig_top=88)

    check('INL-02a', 'no position:absolute', 'position: absolute' not in result)
    check('INL-02b', 'has position:relative', 'position: relative' in result)
    check('INL-02c', 'margin-top: 3px', 'margin-top: 3px' in result)
    check('INL-02d', 'margin-left: 52.5px', 'margin-left: 52.5px' in result)
    lines = [l.strip() for l in result.split('\n')]
    check('INL-02e', 'no standalone left prop', 'left: 52.5px;' not in lines)
    check('INL-02f', 'width preserved', 'width: 270px' in result)


def test_inline_root_top_less_than_section_top_clamps_to_zero():
    """INL-03: top=50px, section_orig_top=88 → margin-top 省略（clamped to 0）。"""
    ir = _make_ir('header', {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '50px',
    })
    orig = {'header': {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '50px',
    }}
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True, section_orig_top=88)

    check('INL-03a', 'no margin-top', 'margin-top' not in result)
    check('INL-03b', 'position:relative', 'position: relative' in result)


def test_inline_root_bottom_overlay_becomes_sticky():
    """INL-04: bottom overlay（无 top 或 top=0）转为 sticky 以避免覆盖 flow 内容（U-461）。"""
    ir = _make_ir('btn-group', {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'bottom': '0px',
        'display': 'flex', 'flex-direction': 'column',
    })
    orig = {'btn-group': {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'bottom': '0px',
        'display': 'flex', 'flex-direction': 'column',
    }}
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True, section_orig_top=88)

    check('INL-04a', 'position:sticky generated', 'position: sticky' in result)
    check('INL-04b', 'bottom preserved', 'bottom: 0px' in result)
    check('INL-04c', 'left preserved', 'left: 0px' in result)


def test_inline_root_bottom_with_nonzero_top_converts():
    """INL-05: 有 bottom 但同时有非零 top → 仍然转换为 flow。"""
    ir = _make_ir('mixed', {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '100px', 'bottom': '20px',
    })
    orig = {'mixed': {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '100px', 'bottom': '20px',
    }}
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True, section_orig_top=88)

    check('INL-05a', 'no position:absolute', 'position: absolute' not in result)
    check('INL-05b', 'position:relative', 'position: relative' in result)
    check('INL-05c', 'margin-top: 12px (100-88)', 'margin-top: 12px' in result)


def test_inline_root_height_preserved():
    """INL-06: 与 Section 不同，inline root 保留 height。"""
    ir = _make_ir('navbar', {
        'width': '375px', 'height': '88px', 'position': 'absolute',
        'left': '0px', 'top': '0px',
    })
    orig = {'navbar': {
        'width': '375px', 'height': '88px', 'position': 'absolute',
        'left': '0px', 'top': '0px',
    }}
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True, section_orig_top=0)

    check('INL-06a', 'height preserved', 'height: 88px' in result)


def test_inline_root_zindex_injected():
    """INL-07: 转为 relative 后 z-index 仍然注入。"""
    ir = _make_ir('navbar', {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '0px',
    })
    orig = {'navbar': {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '0px',
    }}
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True,
                            section_orig_top=0, root_z_index=2)

    check('INL-07a', 'z-index injected', 'z-index: 2' in result)
    check('INL-07b', 'position:relative', 'position: relative' in result)


def test_inline_root_children_css_untouched():
    """INL-08: 子节点的 position/top 不被转换（只转根 class）。"""
    child_ir = {
        'figmaId': '1:2',
        'semantic': {'htmlTag': 'div', 'className': 'child-node', 'componentName': '', 'props': []},
        'css': {'height': '44px', 'position': 'absolute', 'top': '17px', 'left': '21px'},
        'children': [],
    }
    ir = _make_ir('navbar', {
        'width': '375px', 'position': 'absolute',
        'left': '0px', 'top': '0px',
    }, children=[child_ir])
    orig = {
        'navbar': {
            'width': '375px', 'position': 'absolute',
            'left': '0px', 'top': '0px',
        },
        'child-node': {
            'height': '44px', 'position': 'absolute', 'top': '17px', 'left': '21px',
        },
    }
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True, section_orig_top=88)

    lines = result.split('\n')
    in_child = False
    child_has_abs = False
    child_has_top17 = False
    for line in lines:
        if '.child-node' in line:
            in_child = True
        if in_child:
            if 'position: absolute' in line:
                child_has_abs = True
            if 'top: 17px' in line:
                child_has_top17 = True
            if line.strip() == '}':
                in_child = False

    check('INL-08a', 'child keeps position:absolute', child_has_abs)
    check('INL-08b', 'child keeps top:17px unchanged', child_has_top17)


def test_inline_root_non_absolute_unchanged():
    """INL-09: 非 absolute 的 inline root 不做任何转换。"""
    ir = _make_ir('flow-item', {
        'width': '375px', 'position': 'relative',
        'display': 'flex',
    })
    orig = {'flow-item': {
        'width': '375px', 'position': 'relative',
        'display': 'flex',
    }}
    result = _css_from_orig(ir, orig, 'less', is_inline_root=True, section_orig_top=88)

    check('INL-09a', 'position:relative unchanged', 'position: relative' in result)
    check('INL-09b', 'no margin-top added', 'margin-top' not in result)


# ─── Section Overlay Detection Tests ──────────────────────────────────────────

def test_section_overlay_fully_inside():
    """INL-10: Inline node 完全在 Section Y 范围内 → overlay=True。"""
    # Section y=-3115, h=706 → range (-3115, -2409)
    # Inline node y=-3112, h=77 → (-3112, -3035), fully inside
    ranges = [(-3115.0, -3115.0 + 706.0)]
    check('INL-10a', 'fully inside → overlay',
          _is_section_overlay(-3112.0, 77.0, ranges))


def test_section_overlay_not_overlapping():
    """INL-11: Inline node 不在 Section 范围内 → overlay=False。"""
    ranges = [(-3115.0, -3115.0 + 706.0)]  # (-3115, -2409)
    # NavBar at y=-3203, h=88 → (-3203, -3115), NOT inside section
    check('INL-11a', 'above section → not overlay',
          not _is_section_overlay(-3203.0, 88.0, ranges))
    # ButtonGroup at y=4210, h=144 → way below section
    check('INL-11b', 'below section → not overlay',
          not _is_section_overlay(4210.0, 144.0, ranges))


def test_section_overlay_partial_overlap():
    """INL-12: Inline node 部分超出 Section → overlay=False（只有完全在内才算）。"""
    ranges = [(100.0, 800.0)]  # Section y=100, h=700
    # Inline node y=90, h=50 → starts before section
    check('INL-12a', 'starts before → not overlay',
          not _is_section_overlay(90.0, 50.0, ranges))
    # Inline node y=750, h=100 → extends past section end
    check('INL-12b', 'extends past → not overlay',
          not _is_section_overlay(750.0, 100.0, ranges))


def test_section_overlay_empty_ranges():
    """INL-13: 无 Section 时永远返回 False。"""
    check('INL-13a', 'no sections → not overlay',
          not _is_section_overlay(100.0, 50.0, []))


def test_section_overlay_invalid_values():
    """INL-14: 无效 y/h 值返回 False。"""
    ranges = [(0.0, 1000.0)]
    check('INL-14a', 'inf y → not overlay',
          not _is_section_overlay(float('inf'), 50.0, ranges))
    check('INL-14b', 'zero height → not overlay',
          not _is_section_overlay(100.0, 0, ranges))
    check('INL-14c', 'negative height → not overlay',
          not _is_section_overlay(100.0, -10.0, ranges))


def test_section_overlay_multiple_sections():
    """INL-15: 多个 Section，只要落在任一内即为 overlay。"""
    ranges = [(0.0, 500.0), (600.0, 1200.0)]
    check('INL-15a', 'inside first section',
          _is_section_overlay(100.0, 50.0, ranges))
    check('INL-15b', 'inside second section',
          _is_section_overlay(700.0, 100.0, ranges))
    check('INL-15c', 'between sections → not overlay',
          not _is_section_overlay(510.0, 80.0, ranges))


def test_section_overlay_exact_boundary():
    """INL-16: 边界值：node 恰好从 section 起点开始/到 section 终点结束。"""
    ranges = [(100.0, 800.0)]
    check('INL-16a', 'starts at section start',
          _is_section_overlay(100.0, 50.0, ranges))
    check('INL-16b', 'ends at section end',
          _is_section_overlay(750.0, 50.0, ranges))
    check('INL-16c', 'fills entire section',
          _is_section_overlay(100.0, 700.0, ranges))


# ─── Page Can Flow Tests ──────────────────────────────────────────────────────

def _make_inline_node(y, h):
    return {'y': y, 'ir': {'bb': {'height': h}}}


def test_page_can_flow_no_overlap():
    """INL-17: Inline nodes + sections 无 Y 重叠 → 可以 flow。"""
    # H5 Dubai Gala pattern: NavBar(0-88), Section(88-794), ButtonGroup(7413-7557)
    sections = [(88.0, 794.0)]
    inlines = [_make_inline_node(0.0, 88.0), _make_inline_node(7413.0, 144.0)]
    check('INL-17a', 'no overlap → can flow', _page_can_flow(inlines, sections))


def test_page_can_flow_with_overlap():
    """INL-18: 多个 inline nodes 与 sections Y 重叠 → 不能 flow。"""
    # AI Hub pattern: sections + inlines overlap extensively
    sections = [(1553.0, 2769.0), (2868.0, 3690.0)]
    inlines = [
        _make_inline_node(736.0, 48.0),   # Navigation
        _make_inline_node(924.0, 472.0),  # universal_upscale: end=1396, overlaps with section start=1553? no
        _make_inline_node(852.0, 116.0),  # Frame: end=968
        _make_inline_node(1424.0, 48.0),  # Frame: end=1472, still before section
    ]
    # Actually let's use a clear overlap case
    sections_overlap = [(100.0, 800.0)]
    inlines_overlap = [_make_inline_node(50.0, 200.0)]  # end=250, overlaps section start=100
    check('INL-18a', 'inline overlaps section → cannot flow',
          not _page_can_flow(inlines_overlap, sections_overlap))


def test_page_can_flow_overlay_excluded():
    """INL-19: Overlay 节点（在 Section 内）不参与 overlap 判断。"""
    # Section covers y=100..800, inline at y=200..300 is overlay (inside section)
    # Another inline at y=50..90 is outside → no overlap with section
    sections = [(100.0, 800.0)]
    inlines = [
        _make_inline_node(200.0, 100.0),  # overlay (inside section)
        _make_inline_node(50.0, 40.0),    # before section, no overlap
    ]
    check('INL-19a', 'overlay excluded → can flow', _page_can_flow(inlines, sections))


def test_page_can_flow_empty():
    """INL-20: 无 inline nodes → 可以 flow。"""
    check('INL-20a', 'no inlines → can flow', _page_can_flow([], [(0.0, 500.0)]))
    check('INL-20b', 'no sections no inlines → can flow', _page_can_flow([], []))


def test_page_can_flow_inlines_overlap_each_other():
    """INL-21: Inline nodes 互相重叠 → 不能 flow。"""
    sections = [(1000.0, 2000.0)]
    inlines = [
        _make_inline_node(50.0, 100.0),   # end=150
        _make_inline_node(100.0, 80.0),   # start=100 < end=150 → overlap!
    ]
    check('INL-21a', 'inlines overlap each other → cannot flow',
          not _page_can_flow(inlines, sections))


# ─── Summary ───────────────────────────────────────────────────────────────────

test_inline_root_strips_absolute_positioning()
test_inline_root_top_to_margin_top_with_offset()
test_inline_root_top_less_than_section_top_clamps_to_zero()
test_inline_root_bottom_overlay_becomes_sticky()
test_inline_root_bottom_with_nonzero_top_converts()
test_inline_root_height_preserved()
test_inline_root_zindex_injected()
test_inline_root_children_css_untouched()
test_inline_root_non_absolute_unchanged()
test_section_overlay_fully_inside()
test_section_overlay_not_overlapping()
test_section_overlay_partial_overlap()
test_section_overlay_empty_ranges()
test_section_overlay_invalid_values()
test_section_overlay_multiple_sections()
test_section_overlay_exact_boundary()
test_page_can_flow_no_overlap()
test_page_can_flow_with_overlap()
test_page_can_flow_overlay_excluded()
test_page_can_flow_empty()
test_page_can_flow_inlines_overlap_each_other()

print_summary()
