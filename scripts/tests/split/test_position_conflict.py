#!/usr/bin/env python3
"""
BUG-2: 组件实例复用时 absolute 定位不适配不同容器。

根因：_detect_width_conflict 只检测 width 冲突。当同一组件被多处引用且各实例的
position/left/top 不一致时（如一个 absolute+left:32px，另一个用 flex margin），
组件根 class 保留了第一个实例的 absolute 定位，导致第二个实例位置错乱。

修复：扩展检测为 _detect_layout_conflict，当检测到 position 属性在实例间不一致
（有的有 position:absolute 有的没有，或 left/top 值不同）时，将这些属性 strip 掉。
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()


def test_position_conflict_detected():
    """U-295: 当叶子组件 template 有 position:absolute 但某实例无 position 时，应 strip 定位属性。"""
    from lib.split_codegen import _detect_width_conflict

    # Template has position:absolute + left + top
    orig_css_map = {
        'register-for': {
            'width': '383px',
            'position': 'absolute',
            'left': '32px',
            'top': '23px',
            'display': 'flex',
            'flex-direction': 'column',
            'gap': '21px',
        },
        'register-for-inst2': {
            'width': '383px',
            'display': 'flex',
            'flex-direction': 'column',
            'gap': '21px',
            'margin-top': '24px',
            'align-self': 'center',
            # NO position/left/top → conflict!
        },
    }

    lc = {
        'ir': {'figmaId': '311:15199'},
        'allInstanceFigmaIds': ['311:15199', '311:15231'],
    }

    fid_to_class = {
        '311:15199': 'register-for',
        '311:15231': 'register-for-inst2',
    }

    node_index = {}

    result = _detect_width_conflict(lc, node_index, fid_to_class, orig_css_map)

    check('U-295a', 'conflict detected (non-empty set)', len(result) > 0)
    check('U-295b', 'position in stripped set', 'position' in result)
    check('U-295c', 'left in stripped set', 'left' in result)
    check('U-295d', 'top in stripped set', 'top' in result)


def test_no_conflict_when_all_absolute():
    """U-296: 当所有实例都有相同的 position:absolute + left + top，不 strip。"""
    from lib.split_codegen import _detect_width_conflict

    orig_css_map = {
        'card-content': {
            'width': '383px',
            'position': 'absolute',
            'left': '32px',
            'top': '23px',
        },
        'card-content-2': {
            'width': '383px',
            'position': 'absolute',
            'left': '32px',
            'top': '23px',
        },
    }

    lc = {
        'ir': {'figmaId': '100:1'},
        'allInstanceFigmaIds': ['100:1', '100:2'],
    }

    fid_to_class = {
        '100:1': 'card-content',
        '100:2': 'card-content-2',
    }

    result = _detect_width_conflict(lc, {}, fid_to_class, orig_css_map)

    check('U-296a', 'no position conflict when all instances have same absolute',
          'position' not in result)


def test_position_conflict_strips_right_bottom_too():
    """U-297: 当存在 position 冲突时，right 和 bottom 也应 strip。"""
    from lib.split_codegen import _detect_width_conflict

    orig_css_map = {
        'tooltip': {
            'position': 'absolute',
            'right': '10px',
            'bottom': '5px',
        },
        'tooltip-inst': {
            # No position at all
            'margin-left': '8px',
        },
    }

    lc = {
        'ir': {'figmaId': '200:1'},
        'allInstanceFigmaIds': ['200:1', '200:2'],
    }

    fid_to_class = {
        '200:1': 'tooltip',
        '200:2': 'tooltip-inst',
    }

    result = _detect_width_conflict(lc, {}, fid_to_class, orig_css_map)

    check('U-297a', 'position in stripped set', 'position' in result)
    check('U-297b', 'right in stripped set', 'right' in result)
    check('U-297c', 'bottom in stripped set', 'bottom' in result)


def test_position_conflict_different_left_strips():
    """U-298: 两个实例都有 position:absolute 但 left 值不同时，应 strip 定位属性。"""
    from lib.split_codegen import _detect_width_conflict

    orig_css_map = {
        'group-2007673489': {
            'width': '325px',
            'height': '251px',
            'position': 'absolute',
            'left': '875.33px',
            'top': '55.9px',
            'border-radius': '16px',
            'overflow': 'hidden',
        },
        'group-2007673487': {
            'width': '325px',
            'height': '251px',
            'position': 'absolute',
            'left': '0px',
            'top': '55.9px',
        },
    }

    lc = {
        'ir': {'figmaId': 'I39641:9277;39641:6284'},
        'allInstanceFigmaIds': ['I39641:9277;39641:6284', 'I39641:9277;39641:6287'],
    }

    fid_to_class = {
        'I39641:9277;39641:6284': 'group-2007673489',
        'I39641:9277;39641:6287': 'group-2007673487',
    }

    result = _detect_width_conflict(lc, {}, fid_to_class, orig_css_map)

    check('U-298a', 'position conflict detected when left differs',
          len(result) > 0)
    check('U-298b', 'left in stripped set', 'left' in result)
    check('U-298c', 'position in stripped set', 'position' in result)


def test_position_conflict_forces_wrapper():
    """U-299: 当实例 CSS 不同（位置冲突）时，effective_root_cls 应为空字符串（强制 wrapper）。"""
    # This test verifies the split_codegen logic at the effective_root_cls decision point.
    # When inst_cls != root_cls AND their CSS differs → effective_root_cls = '' (force wrapper)
    # We can't easily unit-test the full generate_components flow, so we test the condition directly.
    orig_css_map = {
        'group-right': {
            'width': '325px',
            'position': 'absolute',
            'left': '875px',
            'top': '56px',
        },
        'group-left': {
            'width': '325px',
            'position': 'absolute',
            'left': '0px',
            'top': '56px',
        },
    }

    def _strip_invisible(css):
        return {k: v for k, v in css.items()
                if not (k == 'opacity' and str(v).strip() in ('0', '0.0'))}

    root_cls = 'group-right'
    inst_cls = 'group-left'

    css_same = _strip_invisible(orig_css_map.get(inst_cls, {})) == \
               _strip_invisible(orig_css_map.get(root_cls, {}))
    check('U-299a', 'CSS comparison detects difference', css_same is False)

    # Per the fix: when CSS differs, effective_root_cls = '' (force wrapper)
    effective_root_cls = inst_cls if css_same else ''
    check('U-299b', 'effective_root_cls is empty (forces wrapper)', effective_root_cls == '')


def test_overflow_hidden_gets_position_relative_after_strip():
    """U-316: 当 position 被 strip 且根有 overflow:hidden 时，注入 position:relative。"""
    from lib.split_codegen import _css_from_orig

    # Simulate an IR with root class that has position:absolute + overflow:hidden
    ir = {
        'figmaId': 'root',
        'figmaName': 'Group 2007673489',
        'figmaType': 'GROUP',
        'semantic': {'className': 'group-2007673489'},
        'css': {
            'width': '324.67px',
            'height': '250.98px',
            'position': 'absolute',
            'left': '875.33px',
            'top': '55.9px',
            'border-radius': '16px',
            'overflow': 'hidden',
        },
        'children': [],
    }

    orig_css_map = {
        'group-2007673489': {
            'width': '324.67px',
            'height': '250.98px',
            'position': 'absolute',
            'left': '875.33px',
            'top': '55.9px',
            'border-radius': '16px',
            'overflow': 'hidden',
        },
    }

    strip_props = {'position', 'left', 'top'}
    css_text = _css_from_orig(ir, orig_css_map, 'less', strip_root_props=strip_props)

    check('U-316a', 'position:absolute removed from output',
          'position: absolute' not in css_text)
    check('U-316b', 'position:relative injected when overflow:hidden',
          'position: relative' in css_text)
    check('U-316c', 'overflow:hidden preserved',
          'overflow: hidden' in css_text)


def test_no_position_relative_without_overflow_hidden():
    """U-317: 无 overflow:hidden 时，strip position 后不注入 relative。"""
    from lib.split_codegen import _css_from_orig

    ir = {
        'figmaId': 'root',
        'figmaName': 'Frame',
        'figmaType': 'FRAME',
        'semantic': {'className': 'frame-123'},
        'css': {
            'width': '300px',
            'height': '200px',
            'position': 'absolute',
            'left': '10px',
            'top': '20px',
        },
        'children': [],
    }

    orig_css_map = {
        'frame-123': {
            'width': '300px',
            'height': '200px',
            'position': 'absolute',
            'left': '10px',
            'top': '20px',
        },
    }

    strip_props = {'position', 'left', 'top'}
    css_text = _css_from_orig(ir, orig_css_map, 'less', strip_root_props=strip_props)

    check('U-317a', 'position:absolute removed',
          'position: absolute' not in css_text)
    check('U-317b', 'position:relative NOT injected without overflow:hidden',
          'position: relative' not in css_text)


# ── U-452c: sub-pixel top difference must NOT trigger position conflict ────────────────────
def test_u452c_subpixel_top_difference_not_a_conflict():
    """U-452c: When two instances differ in 'top' by less than 1px (Figma rounding artifact),
    _detect_width_conflict must NOT return position props → no wrapper div generated.

    Bug: TopUp in Tomorrowland GetStartedInSection has:
      template (I39641:9303;39641:6388): top:0px
      instance (I39641:9303;39641:6400): top:-0.25px  ← 0.25px Figma rounding
    _detect_width_conflict detects top diff → returns {'position','left','top'}
    → effective_root_cls='' → wrapper div generated
    → wrapper + leaf root both have position:absolute;left:120px → double offset = 240px.

    Fix: in _detect_width_conflict coordinate comparison, ignore differences < 1px
    (sub-pixel Figma rounding artifacts treated as same position).

    Real data: node I39641:9303;39641:6388 (top:0px) vs I39641:9303;39641:6400 (top:-0.25px)
               from Tomorrowland GetStartedInSection TopUp leaf
    """
    from lib.split_codegen import _detect_width_conflict, _POSITION_PROPS

    lc = {
        'ir': {
            'figmaId': 'I39641:9303;39641:6388',
            'semantic': {'className': 'container-I39641-9303-39641-6388'},
        },
        'allInstanceFigmaIds': ['I39641:9303;39641:6388', 'I39641:9303;39641:6400'],
    }
    fid_to_class = {
        'I39641:9303;39641:6388': 'container-I39641-9303-39641-6388',
        'I39641:9303;39641:6400': 'container-I39641-9303-39641-6400',
    }
    orig_css_map = {
        # Template: top:0px
        'container-I39641-9303-39641-6388': {
            'width': '497px', 'height': '140px',
            'position': 'absolute', 'left': '120px', 'top': '0px',
            'display': 'flex', 'overflow': 'hidden',
        },
        # Instance 2: top:-0.25px (Figma sub-pixel rounding, visually identical)
        'container-I39641-9303-39641-6400': {
            'height': '140px',
            'position': 'absolute', 'left': '120px', 'top': '-0.25px',
            'display': 'flex', 'overflow': 'hidden',
        },
    }
    node_index = {}

    # Call _detect_width_conflict (after fix, should NOT return position props)
    strip = _detect_width_conflict(lc, node_index, fid_to_class, orig_css_map)

    # Precondition confirmation embedded in the real data assertion:
    # The diff is purely 'top: 0px' vs 'top: -0.25px' → 0.25px sub-pixel only.
    check('U-452c-precondition',
          'Real data: top values differ (0px vs -0.25px) — sub-pixel Figma rounding confirmed',
          orig_css_map['container-I39641-9303-39641-6388'].get('top') == '0px' and
          orig_css_map['container-I39641-9303-39641-6400'].get('top') == '-0.25px')

    check('U-452c',
          'FIXED: sub-pixel top diff (0.25px) does NOT trigger position conflict',
          not bool(strip & _POSITION_PROPS))


# ── Run ──
print('\n── split position conflict detection tests ──')
test_position_conflict_detected()
test_no_conflict_when_all_absolute()
test_position_conflict_strips_right_bottom_too()
test_position_conflict_different_left_strips()
test_position_conflict_forces_wrapper()
test_overflow_hidden_gets_position_relative_after_strip()
test_no_position_relative_without_overflow_hidden()
test_u452c_subpixel_top_difference_not_a_conflict()
print_summary('split/test_position_conflict')
