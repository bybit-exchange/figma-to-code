#!/usr/bin/env python3
"""
test_css_extractor.py — css_extractor 单元测试（从 verify_fixes.py 拆分）
"""
import sys
import re
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tests.helpers import check, make_node, make_ctx, reset, print_summary, _SOLID_BLACK, _SOLID_DARK
from lib.css_extractor import extract_css, clear_var_miss, get_var_miss
from lib.ir_builder import build_ir
from lib.color import px
from convert import patch_flex_shrink, patch_top_gradient_reversal, patch_filter_blend_isolation
from lib.ir_builder import _build_text_segments, _child_extends_outside, _parent_needs_overflow_wrap, _wrap_overflow_children
from lib.tsx_generator import _render_text_segment
from lib.tsx_generator import generate_tsx, _render_text_segment
from lib.token_resolver import _is_bg_semantic_token, _is_bg_fill_token, _is_interaction_token, _resolve_css_dict, load_tokens


def run_tests():
    print('\n  ─── CSS 提取器单元测试 ───────────────────')


    # ── U-1 through U-5: rotation radians→degrees ─────────────────────────────

    try:
        css = extract_css(make_node(rotation=-math.pi / 2), make_ctx())
        check('U-1', '-π/2 rad → rotate(-90deg)（修复弧度当度数用导致 rotate(1.57deg)）',
              css.get('transform') == 'rotate(-90deg)')
    except Exception as e:
        check('U-1', '-π/2 rad → rotate(-90deg)', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(rotation=-0.49), make_ctx())
        t = css.get('transform', '')
        check('U-2', '-0.49 rad ≈ -28.07° → rotate(-28.07deg)',
              'rotate(-28.' in t)
    except Exception as e:
        check('U-2', '-0.49 rad ≈ -28.07°', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(rotation=-0.005), make_ctx())
        check('U-3', '-0.005 rad（< 0.01 阈值）→ 无 transform（浮点噪声过滤）',
              not css.get('transform'))
    except Exception as e:
        check('U-3', '-0.005 rad → 无 transform', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(rotation=0), make_ctx())
        check('U-4', '0 rad → 无 transform',
              not css.get('transform'))
    except Exception as e:
        check('U-4', '0 rad → 无 transform', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(rotation=-5), make_ctx())
        t = css.get('transform', '')
        check('U-5', '-5 rad → rotate(-286.48deg)',
              'rotate(-286.' in t)
    except Exception as e:
        check('U-5', '-5 rad → rotate(-286.48deg)', False)
        print(f'       Error: {e}')


    # ── U-6 through U-10: CENTER constraints ─────────────────────────────────

    try:
        css = extract_css(
            make_node(width=50, height=50, x=175, y=0,
                      constraints={'horizontal': 'CENTER', 'vertical': 'TOP'}),
            make_ctx(width=400))
        check('U-6', '水平 CENTER → left: 50%（精确居中）',
              css.get('left') == '50%' and 'translateX(-50%)' in css.get('transform', ''))
    except Exception as e:
        check('U-6', '水平 CENTER → left: 50%', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(
            make_node(width=50, height=50, x=215, y=0,
                      constraints={'horizontal': 'CENTER', 'vertical': 'TOP'}),
            make_ctx(width=400))
        # offset=(215+25)-200=40px > 3px → calc(50% + 40px), exact position preserved
        check('U-7', '水平 CENTER 偏移 40px → calc(50% + 40px)（精确位置，非近似居中）',
              'calc(50% + 40px)' in (css.get('left') or '') and 'translateX(-50%)' in css.get('transform', ''))
    except Exception as e:
        check('U-7', '水平 CENTER → left: 50% (offset)', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(
            make_node(width=514, height=212, x=48, y=116,
                      constraints={'horizontal': 'LEFT', 'vertical': 'CENTER'}),
            make_ctx(width=610, height=364))
        # v_offset=(116+106)-182=40px > 3px → calc(50% + 40px), not top:116px
        check('U-8', '垂直 CENTER 偏移 40px → calc(50% + 40px)（精确位置，非 top:116px）',
              'calc(50% + 40px)' in (css.get('top') or '')
              and 'translateY(-50%)' in css.get('transform', '')
              and css.get('top') != '116px')
    except Exception as e:
        check('U-8', '垂直 CENTER → top: 50%', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(
            make_node(width=50, height=5, x=0, y=67,
                      constraints={'horizontal': 'CENTER', 'vertical': 'CENTER'}),
            make_ctx(width=400, height=134))
        check('U-9', '垂直 CENTER → top: 50%（ellipse-39 bug：偏差 2.5px）',
              css.get('top') == '50%' and css.get('top') != '67px')
    except Exception as e:
        check('U-9', '垂直 CENTER → top: 50%', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(
            make_node(width=50, height=50, x=10, y=20,
                      constraints={'horizontal': 'LEFT', 'vertical': 'TOP'}),
            make_ctx(width=400, height=400))
        check('U-10', 'LEFT/TOP constraint 不受影响，仍输出 px',
              css.get('left') == '10px' and css.get('top') == '20px')
    except Exception as e:
        check('U-10', 'LEFT/TOP → px', False)
        print(f'       Error: {e}')


    # ── U-11 through U-13: sub-pixel stroke rounding ──────────────────────────

    try:
        css = extract_css(make_node(
            type='RECTANGLE',
            strokes=[dict(_SOLID_DARK)],
            strokeWeight=0.40583, strokeAlign='INSIDE', fills=[],
        ), make_ctx())
        check('U-11', '亚像素描边 0.406px → border: 1px（修复 Math.round 归零）',
              css.get('border') == '1px solid #2b2b2b')
    except Exception as e:
        check('U-11', '亚像素描边 0.406px → 1px', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(
            type='RECTANGLE',
            strokes=[dict(_SOLID_DARK)],
            strokeWeight=2, strokeAlign='INSIDE', fills=[],
        ), make_ctx())
        check('U-12', '正常描边 2px 保持不变',
              css.get('border') == '2px solid #2b2b2b')
    except Exception as e:
        check('U-12', '正常描边 2px', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(
            type='RECTANGLE',
            strokes=[dict(_SOLID_DARK)],
            strokeWeight=0, strokeAlign='INSIDE', fills=[],
        ), make_ctx())
        check('U-13', '描边权重 0 → 0px solid',
              css.get('border') == '0px solid #2b2b2b')
    except Exception as e:
        check('U-13', '描边权重 0 → 0px', False)
        print(f'       Error: {e}')


    # ── U-14 through U-17: blend mode mapping ────────────────────────────────

    try:
        css = extract_css(make_node(blendMode='LINEAR_DODGE'), make_ctx())
        check('U-14', 'LINEAR_DODGE → mix-blend-mode: plus-lighter（CSS Add，非 screen）',
              css.get('mix-blend-mode') == 'plus-lighter')
    except Exception as e:
        check('U-14', 'LINEAR_DODGE → plus-lighter', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(blendMode='LINEAR_BURN'), make_ctx())
        check('U-15', 'LINEAR_BURN → mix-blend-mode: color-burn',
              css.get('mix-blend-mode') == 'color-burn')
    except Exception as e:
        check('U-15', 'LINEAR_BURN → color-burn', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(blendMode='MULTIPLY'), make_ctx())
        check('U-16', 'MULTIPLY → mix-blend-mode: multiply',
              css.get('mix-blend-mode') == 'multiply')
    except Exception as e:
        check('U-16', 'MULTIPLY → multiply', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(blendMode='NORMAL'), make_ctx())
        check('U-17', 'NORMAL blend mode → 不输出 mix-blend-mode',
              not css.get('mix-blend-mode'))
    except Exception as e:
        check('U-17', 'NORMAL → no mix-blend-mode', False)
        print(f'       Error: {e}')


    # ── U-18: gradient angle ──────────────────────────────────────────────────

    try:
        css = extract_css(make_node(
            fills=[{
                'type': 'GRADIENT_LINEAR', 'visible': True, 'opacity': 1,
                'gradientHandlePositions': [{'x': 0.5, 'y': 0}, {'x': 0.5, 'y': 1}],
                'gradientStops': [
                    {'position': 0, 'color': {'r': 1, 'g': 0, 'b': 0, 'a': 1}},
                    {'position': 1, 'color': {'r': 0, 'g': 0, 'b': 1, 'a': 1}},
                ],
            }],
            width=100, height=100,
        ), make_ctx())
        check('U-18', '正方形元素垂直渐变 → 180deg（to bottom）',
              css.get('background') == 'linear-gradient(180deg, #ff0000 0%, #0000ff 100%)')
    except Exception as e:
        check('U-18', '正方形垂直渐变 → 180deg', False)
        print(f'       Error: {e}')


    # ── U-19: wide rectangle gradient angle ──────────────────────────────────
    # Verifies that the angle formula accounts for node aspect ratio.
    # For handles (1,0)→(0,1) on a 1441×837 node:
    #   Old square-assumption formula gives ≈225deg
    #   Correct formula (using actual w×h) gives ≈239.8deg
    # Also verify old-formula artefacts (-36deg / 323.2deg) are absent.

    try:
        css = extract_css(make_node(
            fills=[{
                'type': 'GRADIENT_LINEAR', 'visible': True, 'opacity': 1,
                'gradientHandlePositions': [
                    {'x': 1.0, 'y': 0.0},
                    {'x': 0.0, 'y': 1.0},
                ],
                'gradientStops': [
                    {'position': 0,   'color': {'r': 1, 'g': 0.42, 'b': 0, 'a': 1}},
                    {'position': 0.5, 'color': {'r': 1, 'g': 0.61, 'b': 0.18, 'a': 1}},
                    {'position': 1,   'color': {'r': 1, 'g': 0.69, 'b': 0.31, 'a': 1}},
                ],
            }],
            width=1441, height=837,
        ), make_ctx())
        bg = css.get('background', '')
        # Correct aspect-ratio-aware formula → NOT the old 225deg square assumption
        old_square_bad = '-36' in bg or '323.2' in bg or '225deg' in bg
        check('U-19', '宽矩形（1441×837）渐变 → 角度非正方形假设值（修复非正方形元素角度偏差）',
              'linear-gradient' in bg and not old_square_bad)
    except Exception as e:
        check('U-19', '宽矩形渐变角度非正方形假设值', False)
        print(f'       Error: {e}')


    # ── U-20 through U-22: layoutMode ─────────────────────────────────────────

    try:
        css = extract_css(make_node(layoutMode='GRID', width=1200, height=400), make_ctx())
        check('U-20', 'layoutMode GRID → flex-direction: row（修复 GRID 被错误处理为 column）',
              css.get('flex-direction') == 'row' and css.get('display') == 'flex')
    except Exception as e:
        check('U-20', 'GRID → flex-direction: row', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(layoutMode='HORIZONTAL', width=400, height=100), make_ctx())
        check('U-21', 'layoutMode HORIZONTAL → 仍为 row（回归测试）',
              css.get('flex-direction') == 'row')
    except Exception as e:
        check('U-21', 'HORIZONTAL → row', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(layoutMode='VERTICAL', width=400, height=400), make_ctx())
        check('U-22', 'layoutMode VERTICAL → 仍为 column（回归测试）',
              css.get('flex-direction') == 'column')
    except Exception as e:
        check('U-22', 'VERTICAL → column', False)
        print(f'       Error: {e}')


    # ── U-23 through U-25: VECTOR/BOOLEAN strokes ────────────────────────────

    try:
        css = extract_css(make_node(
            type='VECTOR',
            strokes=[dict(_SOLID_BLACK)],
            strokeWeight=2, strokeAlign='INSIDE', fills=[],
            width=24, height=24,
        ), make_ctx())
        check('U-23', 'VECTOR 图标节点（24×24）有 stroke → 不生成 CSS border（SVG <img> 内嵌描边）',
              not css.get('border') and not css.get('outline'))
    except Exception as e:
        check('U-23', 'VECTOR 24×24 → no border', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(
            type='VECTOR',
            strokes=[dict(_SOLID_BLACK)],
            strokeWeight=2, strokeAlign='INSIDE', fills=[],
            width=0, height=58,
        ), make_ctx())
        check('U-23b', 'VECTOR 线条型节点（0×58）有 stroke → 保留 CSS border（渲染为分隔线）',
              bool(css.get('border')))
    except Exception as e:
        check('U-23b', 'VECTOR 0×58 → has border', False)
        print(f'       Error: {e}')

    try:
        node = make_node(
            type='VECTOR',
            strokes=[dict(_SOLID_BLACK)],
            strokeWeight=2, strokeAlign='INSIDE', fills=[],
            width=22, height=0,
            constraints={'horizontal': 'SCALE', 'vertical': 'SCALE'},
            x=4.8, y=12,
        )
        ctx = make_ctx(layoutMode='NONE', width=32, height=32)
        css = extract_css(node, ctx)
        check('U-23c', 'VECTOR 线条型但有 % 定位（图标组件内部子元素）→ 不生成 CSS border',
              not css.get('border')
              and isinstance(css.get('left'), str)
              and css['left'].endswith('%'))
    except Exception as e:
        check('U-23c', 'VECTOR line % positioned → no border', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(
            type='BOOLEAN_OPERATION',
            strokes=[dict(_SOLID_BLACK)],
            strokeWeight=1, strokeAlign='INSIDE', fills=[],
            width=20, height=20,
        ), make_ctx())
        check('U-24', 'BOOLEAN_OPERATION 节点（20×20）有 stroke → 无 CSS border',
              not css.get('border'))
    except Exception as e:
        check('U-24', 'BOOLEAN_OPERATION → no border', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(
            type='RECTANGLE',
            strokes=[dict(_SOLID_BLACK)],
            strokeWeight=2, strokeAlign='INSIDE', fills=[],
            width=100, height=100,
        ), make_ctx())
        check('U-25', 'RECTANGLE 节点有 stroke → 正常生成 CSS border',
              bool(css.get('border')))
    except Exception as e:
        check('U-25', 'RECTANGLE → has border', False)
        print(f'       Error: {e}')


    # ── U-26 through U-27: TEXT empty fills ──────────────────────────────────

    try:
        css = extract_css(make_node(type='TEXT', fills=[], characters='BTC/USDT'), make_ctx())
        check('U-26', 'TEXT 节点 fills=[] → color:transparent（修复不可见占位文字被渲染为黑色）',
              css.get('color') == 'transparent')
    except Exception as e:
        check('U-26', 'TEXT fills=[] → transparent', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(
            type='TEXT',
            fills=[{'type': 'SOLID', 'color': {'r': 0.07, 'g': 0.07, 'b': 0.08, 'a': 1}, 'visible': True}],
        ), make_ctx())
        check('U-27', 'TEXT 节点有 SOLID fill → 不输出 color:transparent',
              css.get('color') != 'transparent' and bool(css.get('color')))
    except Exception as e:
        check('U-27', 'TEXT SOLID fill → not transparent', False)
        print(f'       Error: {e}')


    # ── U-28 through U-29: clipsContent ──────────────────────────────────────

    try:
        css = extract_css(make_node(type='FRAME', clipsContent=True), make_ctx())
        check('U-28', 'FRAME clipsContent=true 无圆角 → overflow:hidden（修复无圆角容器漏设 overflow）',
              css.get('overflow') == 'hidden')
    except Exception as e:
        check('U-28', 'FRAME clipsContent=True → overflow:hidden', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(type='FRAME', cornerRadius=8, clipsContent=None), make_ctx())
        check('U-29', 'FRAME clipsContent=None + 有圆角 → 仍有 overflow:hidden（回归测试）',
              css.get('overflow') == 'hidden')
    except Exception as e:
        check('U-29', 'FRAME cornerRadius + no clipsContent → overflow:hidden', False)
        print(f'       Error: {e}')


    # ── U-30: TEXT DROP_SHADOW → text-shadow ─────────────────────────────────

    try:
        css = extract_css(make_node(
            type='TEXT',
            fills=[{'type': 'SOLID', 'color': {'r': 1, 'g': 0.6, 'b': 0, 'a': 1}, 'visible': True}],
            effects=[{
                'type': 'DROP_SHADOW', 'visible': True,
                'color': {'r': 1, 'g': 0.6, 'b': 0, 'a': 1},
                'offset': {'x': 0, 'y': 0}, 'radius': 30, 'spread': 0,
            }],
        ), make_ctx())
        check('U-30', 'TEXT 节点 DROP_SHADOW → text-shadow（无 spread），不生成 box-shadow',
              bool(css.get('text-shadow')) and not css.get('box-shadow'))
    except Exception as e:
        check('U-30', 'TEXT DROP_SHADOW → text-shadow', False)
        print(f'       Error: {e}')


    # ── U-31: TEXT GRADIENT_LINEAR fill → background-clip:text ───────────────

    try:
        css = extract_css(make_node(
            type='TEXT',
            fills=[{
                'type': 'GRADIENT_LINEAR', 'visible': True, 'opacity': 1,
                'gradientHandlePositions': [{'x': 0.5, 'y': 0}, {'x': 0.5, 'y': 1}],
                'gradientStops': [
                    {'position': 0,    'color': {'r': 0.737, 'g': 0.737, 'b': 0.737, 'a': 1}},
                    {'position': 0.33, 'color': {'r': 0.957, 'g': 0.957, 'b': 0.957, 'a': 1}},
                    {'position': 0.67, 'color': {'r': 0.420, 'g': 0.420, 'b': 0.420, 'a': 1}},
                    {'position': 1,    'color': {'r': 0.737, 'g': 0.737, 'b': 0.737, 'a': 1}},
                ],
            }],
        ), make_ctx())
        check('U-31', 'TEXT 节点渐变 fill → background-clip:text + gradient（修复退化为单色近似）',
              css.get('-webkit-background-clip') == 'text'
              and css.get('color') == 'transparent'
              and isinstance(css.get('background-image'), str)
              and 'linear-gradient' in css.get('background-image', ''))
    except Exception as e:
        check('U-31', 'TEXT GRADIENT → background-clip:text', False)
        print(f'       Error: {e}')


    # ── U-32, U-32b: padding overflow detection ───────────────────────────────

    try:
        css = extract_css(make_node(
            type='FRAME', width=40, height=40, layoutMode='HORIZONTAL',
            primaryAxisAlignItems='CENTER', counterAxisAlignItems='CENTER',
            paddingTop=10, paddingRight=23, paddingBottom=10, paddingLeft=23,
            cornerRadius=20,
        ), make_ctx())
        # 23+23=46 >= 40 → padding should be omitted
        check('U-32', 'flex 圆形按钮（40px + padding:23px 双侧）→ 跳过 padding（23+23=46 >= 40）',
              not css.get('padding') and not css.get('box-sizing'))
    except Exception as e:
        check('U-32', 'padding overflow → skip padding', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(
            type='FRAME', width=400, height=200, layoutMode='HORIZONTAL',
            primaryAxisAlignItems='CENTER', counterAxisAlignItems='CENTER',
            paddingTop=12, paddingRight=12, paddingBottom=12, paddingLeft=12,
            cornerRadius=8,
        ), make_ctx())
        # 12+12=24 < 400 → padding should exist, no box-sizing
        check('U-32b', 'flex 普通卡片（400px + padding:12px）→ padding 存在，无 box-sizing',
              bool(css.get('padding')) and not css.get('box-sizing'))
    except Exception as e:
        check('U-32b', 'normal padding → exists', False)
        print(f'       Error: {e}')


    # ── U-33, U-33b: FILL horizontal/vertical + min-width ────────────────────

    try:
        css = extract_css(make_node(
            type='FRAME', width=200, layoutGrow=1,
            layoutSizingHorizontal='FILL',
            layoutPositioning=None,
        ), make_ctx(layoutMode='HORIZONTAL'))
        check('U-33', 'FILL 水平 flex 子节点 → min-width:0（修复长文本撑开 flex 行超出容器）',
              css.get('min-width') == '0' and css.get('flex-grow') == '1')
    except Exception as e:
        check('U-33', 'FILL HORIZONTAL → min-width:0 + flex-grow:1', False)
        print(f'       Error: {e}')

    try:
        css = extract_css(make_node(
            type='FRAME', height=200, layoutGrow=1,
            layoutSizingVertical='FILL',
            layoutPositioning=None,
        ), make_ctx(layoutMode='VERTICAL'))
        check('U-33b', 'FILL 垂直 flex 子节点 → 不加 min-width:0（避免影响列方向布局）',
              not css.get('min-width') and css.get('flex-grow') == '1')
    except Exception as e:
        check('U-33b', 'FILL VERTICAL → no min-width', False)
        print(f'       Error: {e}')


    # ── U-34: TRUNCATE + implicit maxLines ────────────────────────────────────

    try:
        css = extract_css(make_node(
            type='TEXT', height=55,
            style={'textAutoResize': 'TRUNCATE', 'textTruncation': 'ENDING', 'lineHeightPx': 22},
            fills=[{'type': 'SOLID', 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1}, 'visible': True}],
        ), make_ctx())
        check('U-34', 'TRUNCATE + height:55px + lineHeight:22px → -webkit-line-clamp:2（修复 maxLines 未设时退化为单行）',
              css.get('-webkit-line-clamp') == '2' and css.get('display') == '-webkit-box')
    except Exception as e:
        check('U-34', 'TRUNCATE → -webkit-line-clamp:2', False)
        print(f'       Error: {e}')


    # ── U-35 through U-37: TEXT GRADIENT fills (regression) ──────────────────

    _grad_fill_h = {
        'type': 'GRADIENT_LINEAR', 'visible': True, 'opacity': 1,
        'gradientHandlePositions': [{'x': 0, 'y': 0.5}, {'x': 1, 'y': 0.5}, {'x': 0, 'y': 0}],
        'gradientStops': [
            {'position': 0,    'color': {'r': 0.737, 'g': 0.737, 'b': 0.737, 'a': 1}},
            {'position': 0.333, 'color': {'r': 0.957, 'g': 0.957, 'b': 0.957, 'a': 1}},
            {'position': 0.666, 'color': {'r': 0.42,  'g': 0.42,  'b': 0.42,  'a': 1}},
            {'position': 1,    'color': {'r': 0.737, 'g': 0.737, 'b': 0.737, 'a': 1}},
        ],
    }

    try:
        css = extract_css(make_node(type='TEXT', fills=[_grad_fill_h]), make_ctx())
        check('U-35', 'TEXT + GRADIENT_LINEAR fill → background-image 包含 linear-gradient（用 background-image 避免 postcss-rtlcss 重置 background-clip）',
              isinstance(css.get('background-image'), str)
              and 'linear-gradient' in css.get('background-image', '')
              and not css.get('background'))
    except Exception as e:
        check('U-35', 'TEXT GRADIENT → background-image (not background)', False)
        print(f'       Error: {e}')

    _grad_fill_2stop = {
        'type': 'GRADIENT_LINEAR', 'visible': True, 'opacity': 1,
        'gradientHandlePositions': [{'x': 0, 'y': 0.5}, {'x': 1, 'y': 0.5}, {'x': 0, 'y': 0}],
        'gradientStops': [
            {'position': 0, 'color': {'r': 0.737, 'g': 0.737, 'b': 0.737, 'a': 1}},
            {'position': 1, 'color': {'r': 0.957, 'g': 0.957, 'b': 0.957, 'a': 1}},
        ],
    }

    try:
        css = extract_css(make_node(type='TEXT', fills=[_grad_fill_2stop]), make_ctx())
        check('U-36', 'TEXT + GRADIENT_LINEAR fill → -webkit-background-clip:text + color:transparent',
              css.get('-webkit-background-clip') == 'text'
              and css.get('background-clip') == 'text'
              and css.get('-webkit-text-fill-color') == 'transparent'
              and css.get('color') == 'transparent')
    except Exception as e:
        check('U-36', 'TEXT GRADIENT → -webkit-background-clip:text', False)
        print(f'       Error: {e}')

    _grad_fill_2stop_b = {
        'type': 'GRADIENT_LINEAR', 'visible': True, 'opacity': 1,
        'gradientHandlePositions': [{'x': 0, 'y': 0.5}, {'x': 1, 'y': 0.5}, {'x': 0, 'y': 0}],
        'gradientStops': [
            {'position': 0,    'color': {'r': 0.737, 'g': 0.737, 'b': 0.737, 'a': 1}},
            {'position': 0.33, 'color': {'r': 0.957, 'g': 0.957, 'b': 0.957, 'a': 1}},
        ],
    }

    try:
        css = extract_css(make_node(type='TEXT', fills=[_grad_fill_2stop_b]), make_ctx())
        check('U-37', 'TEXT + GRADIENT_LINEAR fill → 不再把最亮色阶当 color（旧行为 color:#f4f4f4 防御）',
              css.get('color') == 'transparent' and css.get('color') != '#f4f4f4')
    except Exception as e:
        check('U-37', 'TEXT GRADIENT → color != #f4f4f4', False)
        print(f'       Error: {e}')


    # ── U-38: real rotation (-π/2) ────────────────────────────────────────────

    try:
        css = extract_css(make_node(width=523, height=550, rotation=-1.5707963705062884), make_ctx())
        check('U-38', '202:33617 shadow 节点（有 width/height）rotation(-π/2) → rotate(-90deg)',
              'rotate(-90' in css.get('transform', ''))
    except Exception as e:
        check('U-38', 'rotation(-π/2) → rotate(-90deg)', False)
        print(f'       Error: {e}')


    # ── U-39: LINE node with rotation → NO CSS rotate ─────────────────────────

    try:
        node = {
            'type': 'LINE', 'width': None, 'height': None,
            'rotation': 1.5707963705062848,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 0, 'height': 358},
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'fills': [],
        }
        css = extract_css(node, make_ctx())
        t = css.get('transform', '')
        check('U-39', 'LINE 节点（width:None, height:None）rotation(π/2) → 无 CSS rotate（absoluteBoundingBox 已含旋转，避免二重旋转）',
              not t or 'rotate' not in t)
    except Exception as e:
        check('U-39', 'LINE rotation → no CSS rotate', False)
        print(f'       Error: {e}')


    # ── U-40: FRAME with null width/height + rotation → NO CSS rotate ─────────

    try:
        node = {
            'type': 'FRAME', 'width': None, 'height': None,
            'rotation': -1.5707966532669442,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 11.25, 'height': 20},
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'fills': [], 'children': [],
        }
        css = extract_css(node, make_ctx())
        check('U-40', 'FRAME instance（width:None, height:None）rotation(-π/2) → 无 CSS rotate（G-13 outline FRAME 回归测试）',
              not css.get('transform'))
    except Exception as e:
        check('U-40', 'FRAME null dims rotation → no transform', False)
        print(f'       Error: {e}')


    # ── U-41: text gap + overlay adoption (_fix_text_gap_overlays) ──────────────
    # Reproduces the '10% over Earn up to/cashback with Example Card' pattern (4030:41365)
    try:
        from lib.ir_builder import _fix_text_gap_overlays

        bg_image = {
            'type': 'RECTANGLE',
            'absoluteBoundingBox': {'x': -330, 'y': 66, 'width': 720, 'height': 555},
        }
        inner_text = {
            'type': 'TEXT',
            'characters': 'Earn up to   cashback with Example Card',
            'absoluteBoundingBox': {'x': 31, 'y': 82, 'width': 320, 'height': 128},
        }
        text_card = {
            'type': 'FRAME',
            'layoutMode': 'VERTICAL',
            'absoluteBoundingBox': {'x': 31, 'y': 46, 'width': 320, 'height': 208},
            'children': [inner_text],
        }
        overlay_text = {
            'type': 'TEXT',
            'characters': '10%',
            'absoluteBoundingBox': {'x': 142, 'y': 117, 'width': 96, 'height': 56},
        }
        flat = [bg_image, text_card, overlay_text]
        result = _fix_text_gap_overlays(flat)

        overlay_removed = overlay_text not in result
        fc = text_card.get('children', [])
        card_has_overlay = overlay_text in fc
        card_has_split = len(fc) == 3
        above_ok = fc[0].get('characters') == 'Earn up to' if len(fc) >= 1 else False
        below_ok = fc[2].get('characters') == 'cashback with Example Card' if len(fc) >= 3 else False

        check('U-41', 'text gap overlay: orphan TEXT 从 flat 移入 VERTICAL frame 子节点',
              overlay_removed and card_has_overlay and card_has_split and above_ok and below_ok)
    except Exception as e:
        check('U-41', 'text gap overlay adoption', False)
        print(f'       Error: {e}')

    # ── patch_flex_shrink: Bug-1 大背景图居中修复 ──────────────────────────────
    print('\n  ─── patch_flex_shrink 回归测试 ───────────')

    def _pf_frame(css, children=None, figma_type='FRAME', figma_name='Container',
                  figma_id='0:1', is_image=False):
        return {'figmaId': figma_id, 'figmaName': figma_name, 'figmaType': figma_type,
                'isImageNode': is_image, 'isVectorNode': False, 'isDecorativeElement': False,
                'css': dict(css), 'children': children or []}

    def _pf_img(css, figma_id='0:2'):
        return _pf_frame(css, figma_id=figma_id, is_image=True)

    def _make_bg_tree(extra_transform=''):
        t = 'translateX(-50%) translateY(-50%)'
        if extra_transform:
            t = extra_transform + ' ' + t
        img = _pf_img({'width': '1376px', 'height': '786px', 'position': 'absolute',
                        'left': '50%', 'top': '50%', 'transform': t})
        parent = _pf_frame({'display': 'flex', 'position': 'relative'}, children=[img])
        return parent, img

    try:
        parent, img = _make_bg_tree()
        patch_flex_shrink(parent)
        check('U-42', 'bg-img: width 转为 100%（abs-pos <img> 需显式 width:100% 才能拉伸至含块全宽）',
              img['css'].get('width') == '100%')
        check('U-42b', 'bg-img: 无 right 属性（使用 width:100%+left:0，不再需要 right:0）',
              img['css'].get('right') is None)
        check('U-43', 'bg-img: left 重置为 0（不再用 left:50%）',
              img['css'].get('left') == '0')
        check('U-44', 'bg-img: translateX(-50%) 已移除',
              'translateX(-50%)' not in img['css'].get('transform', ''))
        check('U-45', 'bg-img: translateY(-50%) 保留用于垂直居中',
              'translateY(-50%)' in img['css'].get('transform', ''))
        check('U-46', 'bg-img: object-fit: cover 已设置',
              img['css'].get('object-fit') == 'cover')
    except Exception as e:
        for tid in ('U-42', 'U-42b', 'U-43', 'U-44', 'U-45', 'U-46'):
            check(tid, 'bg-img patch', False)
        print(f'       Error: {e}')

    try:
        img2 = _pf_img({'width': '1376px', 'height': '786px', 'position': 'absolute',
                         'left': '50%', 'top': '50%', 'transform': 'translateX(-50%)'})
        parent2 = _pf_frame({'display': 'flex', 'position': 'relative'}, children=[img2])
        patch_flex_shrink(parent2)
        check('U-47', 'bg-img: 仅 translateX 时 transform key 完全移除',
              'transform' not in img2['css'])
    except Exception as e:
        check('U-47', 'bg-img transform cleared', False)
        print(f'       Error: {e}')

    try:
        parent3, img3 = _make_bg_tree(extra_transform='scale(1.1)')
        patch_flex_shrink(parent3)
        check('U-48', 'bg-img: scale(1.1) 等其他 transform 保留',
              'scale(1.1)' in img3['css'].get('transform', '') and
              'translateX(-50%)' not in img3['css'].get('transform', ''))
    except Exception as e:
        check('U-48', 'bg-img extra transform preserved', False)
        print(f'       Error: {e}')

    try:
        small = _pf_img({'width': '400px', 'height': '300px', 'position': 'absolute',
                          'left': '50%', 'transform': 'translateX(-50%)'})
        p_small = _pf_frame({'display': 'flex'}, children=[small])
        patch_flex_shrink(p_small)
        check('U-49', 'bg-img: ≤800px 图片不触发 patch',
              small['css']['width'] == '400px' and small['css']['left'] == '50%')
    except Exception as e:
        check('U-49', 'small image not patched', False)
        print(f'       Error: {e}')

    try:
        tall = _pf_img({'width': '900px', 'height': '900px', 'position': 'absolute',
                         'left': '50%', 'transform': 'translateX(-50%)'})
        p_tall = _pf_frame({'display': 'flex'}, children=[tall])
        patch_flex_shrink(p_tall)
        check('U-50', 'bg-img: 宽高比 1.0（正方形）> 0.5 → 触发 patch，生成 width:100%',
              tall['css'].get('width') == '100%' and tall['css'].get('left') == '0')
    except Exception as e:
        check('U-50', 'narrow aspect ratio not patched', False)
        print(f'       Error: {e}')

    # ── patch_flex_shrink: calc(50%+N) 偏移阈值修复 ─────────────────────────────
    # 修复前用相对阈值 < 15% (即1376px图片容忍206px偏移)，导致99.75px偏移被误判为背景图

    def _pf_calc_img(offset_px, img_w=1376):
        return _pf_img({'width': f'{img_w}px', 'height': '786px', 'position': 'absolute',
                         'left': f'calc(50% + {offset_px}px)', 'top': '50%',
                         'transform': 'translateX(-50%) translateY(-50%)'})

    try:
        img_large = _pf_calc_img(99.75)
        p = _pf_frame({'display': 'flex', 'position': 'relative'}, children=[img_large])
        patch_flex_shrink(p)
        check('U-51', 'calc(50%+99.75px) 大偏移 → 保留原始 left/width/transform（不触发全宽改写）',
              img_large['css'].get('left') == 'calc(50% + 99.75px)' and
              img_large['css'].get('width') == '1376px' and
              'translateX(-50%)' in img_large['css'].get('transform', ''))
    except Exception as e:
        check('U-51', 'large calc offset not patched', False)
        print(f'       Error: {e}')

    try:
        img_small_offset = _pf_calc_img(5)
        p = _pf_frame({'display': 'flex', 'position': 'relative'}, children=[img_small_offset])
        patch_flex_shrink(p)
        check('U-52', 'calc(50%+5px) 小偏移（≤10px）→ 触发全宽改写',
              img_small_offset['css'].get('left') == '0' and
              img_small_offset['css'].get('width') == '100%' and
              'translateX(-50%)' not in img_small_offset['css'].get('transform', ''))
    except Exception as e:
        check('U-52', 'small calc offset patched', False)
        print(f'       Error: {e}')

    try:
        img_boundary = _pf_calc_img(10)
        p = _pf_frame({'display': 'flex', 'position': 'relative'}, children=[img_boundary])
        patch_flex_shrink(p)
        check('U-53', 'calc(50%+10px) 边界值（=10px）→ 触发全宽改写',
              img_boundary['css'].get('left') == '0' and
              img_boundary['css'].get('width') == '100%')
    except Exception as e:
        check('U-53', 'boundary 10px patched', False)
        print(f'       Error: {e}')

    try:
        img_over = _pf_calc_img(10.01)
        p = _pf_frame({'display': 'flex', 'position': 'relative'}, children=[img_over])
        patch_flex_shrink(p)
        check('U-54', 'calc(50%+10.01px) 超出边界（>10px）→ 保留原始值',
              img_over['css'].get('left') == 'calc(50% + 10.01px)' and
              img_over['css'].get('width') == '1376px')
    except Exception as e:
        check('U-54', 'over-boundary not patched', False)
        print(f'       Error: {e}')

    try:
        img_neg = _pf_calc_img(-99.75)
        p = _pf_frame({'display': 'flex', 'position': 'relative'}, children=[img_neg])
        patch_flex_shrink(p)
        check('U-55', 'calc(50%+-99.75px) 负大偏移 → 保留原始值（abs 判断）',
              img_neg['css'].get('left') == 'calc(50% + -99.75px)' and
              img_neg['css'].get('width') == '1376px')
    except Exception as e:
        check('U-55', 'negative large offset not patched', False)
        print(f'       Error: {e}')

    # ── patch_flex_shrink: Bug-2 Patch18 误触发修复 ────────────────────────────

    def _make_card_row(first_width_val=None):
        def card(cid, width_val=None):
            css = {'display': 'flex', 'flex-direction': 'column', 'align-items': 'center'}
            if width_val:
                css['width'] = width_val
            else:
                css['flex'] = '1'; css['flex-grow'] = '1'
            return _pf_frame(css, figma_id=cid, figma_name='Container')
        cards = [card('4030:41875', first_width_val), card('4030:41880'), card('4030:41890')]
        parent = _pf_frame({'display': 'flex', 'flex-direction': 'row'}, children=cards)
        return parent, cards

    try:
        parent_row, cards = _make_card_row(first_width_val=None)
        patch_flex_shrink(parent_row)
        check('U-56', 'Patch18: flex:1 同名卡片不被误设 align-items:flex-end（PreKYC 4030:41890 回归）',
              cards[-1]['css'].get('align-items') == 'center')
    except Exception as e:
        check('U-56', 'Patch18 flex:1 not corrupted', False)
        print(f'       Error: {e}')

    try:
        parent_row2, cards2 = _make_card_row(first_width_val='32px')
        cards2[-1]['css']['align-items'] = 'center'
        patch_flex_shrink(parent_row2)
        check('U-57', 'Patch18: 有明确 32px 宽度时仍触发 → 末位设 flex-end',
              cards2[-1]['css'].get('align-items') == 'flex-end')
    except Exception as e:
        check('U-57', 'Patch18 explicit px triggers', False)
        print(f'       Error: {e}')

    try:
        parent_row3, cards3 = _make_card_row(first_width_val='200px')
        cards3[-1]['css']['align-items'] = 'center'
        patch_flex_shrink(parent_row3)
        check('U-58', 'Patch18: 宽度 >64px 不触发',
              cards3[-1]['css'].get('align-items') == 'center')
    except Exception as e:
        check('U-58', 'Patch18 large px not triggered', False)
        print(f'       Error: {e}')

    try:
        def diff_name_card(cid, name):
            return _pf_frame({'display': 'flex', 'flex-direction': 'column',
                               'width': '32px', 'align-items': 'center'},
                              figma_id=cid, figma_name=name)
        p_diff = _pf_frame({'display': 'flex', 'flex-direction': 'row'},
                            children=[diff_name_card('1:1', 'Left'), diff_name_card('1:2', 'Right')])
        patch_flex_shrink(p_diff)
        check('U-59', 'Patch18: 不同 figmaName 不触发',
              p_diff['children'][-1]['css'].get('align-items') == 'center')
    except Exception as e:
        check('U-59', 'Patch18 different names not triggered', False)
        print(f'       Error: {e}')

    # ── Figma list 节点（lineTypes）支持 ─────────────────────────────────────────
    print('\n  ─── Figma list 节点（lineTypes）测试 ───────')

    def _make_text_node(characters, line_types=None, line_indentations=None, **extra):
        """Minimal Figma TEXT node for testing lineTypes extraction."""
        node = {
            'type': 'TEXT', 'width': 800, 'height': 100,
            'layoutMode': None, 'layoutPositioning': 'AUTO',
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'textAutoResize': 'HEIGHT',
            'characters': characters,
            'style': {'textAutoResize': 'HEIGHT', 'fontSize': 16.0, 'lineHeightPx': 24.0,
                      'fontFamily': 'Inter', 'fontWeight': 400,
                      'textAlignHorizontal': 'LEFT', 'letterSpacing': 0.0,
                      'lineHeightUnit': 'PIXELS'},
            'fills': [{'type': 'SOLID', 'color': {'r': 0.4, 'g': 0.4, 'b': 0.4, 'a': 1.0},
                       'visible': True, 'opacity': 1.0}],
        }
        if line_types is not None:
            node['lineTypes'] = line_types
        if line_indentations is not None:
            node['lineIndentations'] = line_indentations
        node.update(extra)
        return node

    # css_extractor: UNORDERED list
    try:
        css = extract_css(_make_text_node(
            'Line one\nLine two\nLine three',
            line_types=['UNORDERED', 'UNORDERED', 'UNORDERED'],
            line_indentations=[1, 1, 1],
        ), {'isRoot': True})
        check('U-60', 'list UNORDERED → list-style-type: disc',
              css.get('list-style-type') == 'disc')
        check('U-61', 'list UNORDERED → padding-left: 1.5em',
              css.get('padding-left') == '1.5em')
        check('U-62', 'list UNORDERED → 不生成 white-space: pre-line',
              css.get('white-space') != 'pre-line')
    except Exception as e:
        for tid in ('U-60', 'U-61', 'U-62'):
            check(tid, 'list unordered css', False)
        print(f'       Error: {e}')

    # css_extractor: ORDERED list
    try:
        css_ol = extract_css(_make_text_node(
            'Step one\nStep two',
            line_types=['ORDERED', 'ORDERED'],
        ), {'isRoot': True})
        check('U-63', 'list ORDERED → list-style-type: decimal',
              css_ol.get('list-style-type') == 'decimal')
    except Exception as e:
        check('U-63', 'list ordered css', False)
        print(f'       Error: {e}')

    # css_extractor: 普通换行（无 lineTypes）→ 仍保留 white-space: pre-line（回归）
    try:
        css_plain = extract_css(_make_text_node('Line one\nLine two'), {'isRoot': True})
        check('U-64', '无 lineTypes 多行文本 → 仍有 white-space: pre-line（回归）',
              css_plain.get('white-space') == 'pre-line')
    except Exception as e:
        check('U-64', 'plain multiline still pre-line', False)
        print(f'       Error: {e}')

    # tsx_generator: UNORDERED → <ul>/<li>
    def _list_ir(text, line_types, list_style='disc'):
        return {
            'figmaId': '311:15504', 'figmaName': 'expanded', 'figmaType': 'TEXT',
            'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'isComponentInstance': False,
            'textContent': text, 'textSegments': None, 'textAutoResize': 'HEIGHT',
            'lineTypes': line_types, 'lineIndentations': [1] * len(line_types),
            'imageRef': None, 'fillImageRef': None, 'localAssetPath': None,
            'variants': None, 'children': [],
            'css': {'color': '#6a6e73', 'font-size': '16px', 'list-style-type': list_style,
                    'padding-left': '1.5em'},
            'semantic': {'className': 'expanded', 'htmlTag': 'span', 'props': [],
                         'componentName': 'MyList'},
        }

    try:
        ir_ul = _list_ir('Item A\nItem B\nItem C', ['UNORDERED', 'UNORDERED', 'UNORDERED'])
        tsx = generate_tsx(ir_ul, css_ext='scss')
        check('U-65', 'UNORDERED list → TSX 包含 <ul>',
              '<ul ' in tsx or '<ul\n' in tsx)
        check('U-66', 'UNORDERED list → TSX 包含 <li>Item A</li>',
              '<li>Item A</li>' in tsx)
        check('U-67', 'UNORDERED list → TSX 不使用模板字符串换行',
              '`Item A' not in tsx)
    except Exception as e:
        for tid in ('U-65', 'U-66', 'U-67'):
            check(tid, 'list ul tsx', False)
        print(f'       Error: {e}')

    # tsx_generator: ORDERED → <ol>/<li>
    try:
        ir_ol = _list_ir('Step one\nStep two', ['ORDERED', 'ORDERED'], list_style='decimal')
        tsx_ol = generate_tsx(ir_ol, css_ext='scss')
        check('U-68', 'ORDERED list → TSX 包含 <ol>',
              '<ol ' in tsx_ol or '<ol\n' in tsx_ol)
        check('U-69', 'ORDERED list → TSX 包含 <li>Step one</li>',
              '<li>Step one</li>' in tsx_ol)
    except Exception as e:
        for tid in ('U-68', 'U-69'):
            check(tid, 'list ol tsx', False)
        print(f'       Error: {e}')

    # tsx_generator: 花括号字符应被 HTML 实体转义
    try:
        ir_brace = _list_ir('{foo}\n{bar}', ['UNORDERED', 'UNORDERED'])
        tsx_brace = generate_tsx(ir_brace, css_ext='scss')
        check('U-70', 'list 文本含 { } → 转义为 HTML 实体，无裸 JSX 语法错误',
              '<li>&#123;foo&#125;</li>' in tsx_brace)
    except Exception as e:
        check('U-70', 'list brace escaping', False)
        print(f'       Error: {e}')



    # ── U-71 ~ U-74: rectangleCornerRadii + px() 亚像素保留 ─────────────────────

    # U-71: INSTANCE 节点使用 rectangleCornerRadii 数组格式
    try:
        css = extract_css(make_node(
            type='INSTANCE',
            rectangleCornerRadii=[0.0, 4.0, 0.0, 4.0],
        ), make_ctx())
        check('U-71', 'INSTANCE rectangleCornerRadii [0,4,0,4] → border-radius: 0px 4px 0px 4px',
              css.get('border-radius') == '0px 4px 0px 4px')
    except Exception as e:
        check('U-71', 'rectangleCornerRadii array fallback', False)
        print(f'       Error: {e}')

    # U-72: 个别字段优先于 rectangleCornerRadii（不应被覆盖）
    try:
        css = extract_css(make_node(
            type='FRAME',
            topLeftRadius=8, topRightRadius=8,
            rectangleCornerRadii=[0.0, 0.0, 0.0, 0.0],
        ), make_ctx())
        check('U-72', '个别字段 (topLeftRadius=8) 优先于 rectangleCornerRadii',
              css.get('border-radius') == '8px 8px 0px 0px')
    except Exception as e:
        check('U-72', 'individual fields override rectangleCornerRadii', False)
        print(f'       Error: {e}')

    # U-73: px() 亚像素保留 —— 非整数值保留 2 位小数
    try:
        check('U-73', 'px(147.67138671875) → 147.67px（不再整数化为 148px）',
              px(147.67138671875) == '147.67px')
    except Exception as e:
        check('U-73', 'px() fractional precision', False)
        print(f'       Error: {e}')

    # U-74: px() 整数值不带小数点
    try:
        check('U-74', 'px(148.0) → 148px（整数不加 .0）',
              px(148.0) == '148px')
    except Exception as e:
        check('U-74', 'px() integer unchanged', False)
        print(f'       Error: {e}')

    # U-75: showShadowBehindNode=false + 透明填充 → filter:drop-shadow（非 box-shadow）
    try:
        css = extract_css(make_node(
            type='FRAME',
            fills=[],
            effects=[{
                'type': 'DROP_SHADOW',
                'visible': True,
                'showShadowBehindNode': False,
                'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0.05},
                'offset': {'x': 0, 'y': 4},
                'radius': 20,
                'spread': 0,
            }],
        ), make_ctx())
        check('U-75', 'showShadowBehindNode=false + 透明容器 → filter:drop-shadow（不是 box-shadow）',
              'drop-shadow' in (css.get('filter') or '') and not css.get('box-shadow'))
    except Exception as e:
        check('U-75', 'showShadowBehindNode=false transparent container', False)
        print(f'       Error: {e}')

    # U-76: showShadowBehindNode=true → box-shadow（标准行为不变）
    try:
        css = extract_css(make_node(
            type='FRAME',
            fills=[],
            effects=[{
                'type': 'DROP_SHADOW',
                'visible': True,
                'showShadowBehindNode': True,
                'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0.2},
                'offset': {'x': 0, 'y': 4},
                'radius': 8,
                'spread': 0,
            }],
        ), make_ctx())
        check('U-76', 'showShadowBehindNode=true → box-shadow（标准路径不变）',
              css.get('box-shadow') is not None and not css.get('filter'))
    except Exception as e:
        check('U-76', 'showShadowBehindNode=true → box-shadow', False)
        print(f'       Error: {e}')



    # ─── U-77 ~ U-83: textSegments 1-segment + trailing text; image fill filters; glassmorphism stroke ──

    # U-77: 1 个 segment + trailing text → trailing text 追加在 span 后
    try:
        ir_1seg = {
            'figmaId': '202:33658', 'figmaName': 'Text', 'figmaType': 'TEXT',
            'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'isComponentInstance': False,
            'textContent': 'All Product Lines Can Be Simulated For Demo Trading',
            'textSegments': [{'text': 'All Product Lines', 'color': '#f7a600'}],
            'textAutoResize': 'HEIGHT', 'lineTypes': ['NONE'], 'lineIndentations': [0],
            'imageRef': None, 'fillImageRef': None, 'localAssetPath': None,
            'variants': None, 'children': [],
            'css': {'width': '488px', 'color': '#ffffff', 'font-size': '24px',
                    'font-weight': '600', 'font-family': "'IBM Plex Sans', sans-serif",
                    'line-height': '32px', 'letter-spacing': '0px', 'text-align': 'left'},
            'semantic': {'className': 'text', 'htmlTag': 'span', 'props': [],
                         'componentName': 'Demo'},
        }
        tsx = generate_tsx(ir_1seg, css_ext='scss')
        check('U-77', '1-segment + trailing → <span style> 包含 orange',
              "color:'#f7a600'" in tsx or 'color: "#f7a600"' in tsx or "color:'#f7a600'" in tsx)
        check('U-78', '1-segment + trailing → trailing text 出现在产物中',
              'Can Be Simulated For Demo Trading' in tsx)
        check('U-79', '1-segment + trailing → trailing text 不在 span style 内',
              tsx.index('Can Be Simulated') > tsx.index('</span>') if '</span>' in tsx and 'Can Be Simulated' in tsx else False)
    except Exception as e:
        for tid in ('U-77', 'U-78', 'U-79'):
            check(tid, '1-segment trailing text', False)
        print(f'       Error: {e}')

    # U-80: IMAGE fill with saturation filter → CSS filter: saturate(0%)
    try:
        from lib.css_extractor import _image_fill_filters_to_css
        f_str = _image_fill_filters_to_css({'saturation': -1.0, 'highlights': -0.34})
        check('U-80', 'saturation:-1.0 → saturate(0%)', 'saturate(0.0%)' in f_str or 'saturate(0%)' in f_str)
        check('U-81', 'highlights:-0.34 → contrast >= 130% (更激进近似)', 'contrast(130' in f_str or 'contrast(13' in f_str)
    except Exception as e:
        for tid in ('U-80', 'U-81'):
            check(tid, 'image fill filters to css', False)
        print(f'       Error: {e}')

    # U-82/U-83: RECTANGLE with only IMAGE fills → NO CSS filter
    # Figma exports such nodes via /images (node ID), which bakes in IMAGE fill color adjustments.
    # Applying CSS filter would double-process the adjustments (bug: 4030:41623 over-saturated).
    try:
        node_img = make_node(
            type='RECTANGLE',
            fills=[{'type': 'IMAGE', 'blendMode': 'NORMAL',
                    'imageRef': 'abc', 'scaleMode': 'STRETCH',
                    'filters': {'saturation': -1.0, 'highlights': -0.34}}],
        )
        css = extract_css(node_img, make_ctx())
        check('U-82', 'RECTANGLE IMAGE（node export bakes filters）→ CSS filter 不应存在（双重叠加 bug）',
              not css.get('filter'))
        check('U-83', 'RECTANGLE IMAGE → 无 saturate() 也无 brightness()（调整已烘焙进导出 PNG）',
              not css.get('filter'))
    except Exception as e:
        for tid in ('U-82', 'U-83'):
            check(tid, 'extract_css image fill filters', False)
        print(f'       Error: {e}')

    # U-84 ~ U-85: glassmorphism BOOLEAN_OPERATION 渐变描边 → CSS gradient border
    try:
        grad_stroke = {
            'type': 'GRADIENT_LINEAR',
            'gradientStops': [
                {'color': {'r': 0.737, 'g': 0.737, 'b': 0.737, 'a': 1.0}, 'position': 0.0},
                {'color': {'r': 0.955, 'g': 0.955, 'b': 0.955, 'a': 1.0}, 'position': 0.333},
                {'color': {'r': 0.420, 'g': 0.420, 'b': 0.420, 'a': 1.0}, 'position': 0.667},
                {'color': {'r': 0.737, 'g': 0.737, 'b': 0.737, 'a': 1.0}, 'position': 1.0},
            ],
            'gradientHandlePositions': [
                {'x': 0.0, 'y': 0.35}, {'x': 1.0, 'y': 0.35}, {'x': 0.0, 'y': 1.63},
            ],
        }
        node_glass = make_node(
            type='BOOLEAN_OPERATION',
            width=312, height=80,
            fills=[{'type': 'SOLID', 'color': {'r': 0.678, 'g': 0.694, 'b': 0.722, 'a': 1.0},
                    'opacity': 0.2, 'blendMode': 'NORMAL'}],
            strokes=[grad_stroke],
            strokeWeight=1.0,
            strokeAlign='INSIDE',
            effects=[{'type': 'BACKGROUND_BLUR', 'visible': True, 'radius': 15.0}],
        )
        css = extract_css(node_glass, make_ctx())
        bg = css.get('background', '')
        check('U-84', 'glassmorphism BOOLEAN_OPERATION 渐变描边 → inset box-shadow（无亮银边框）',
              'inset' in (css.get('box-shadow') or '') and 'rgba' in (css.get('box-shadow') or ''))
        check('U-85', 'glassmorphism BOOLEAN_OPERATION 渐变描边 → 无 gradient border（不改 background-color）',
              css.get('background-color') is not None and 'padding-box' not in (css.get('background', '')))
    except Exception as e:
        for tid in ('U-84', 'U-85'):
            check(tid, 'glassmorphism gradient border', False)
        print(f'       Error: {e}')

    # U-86 ~ U-87: BOOLEAN_OPERATION + exportSettings PNG → isImageNode, no visual CSS, no children
    try:
        bool_op_node = {
            'id': '202:33514', 'name': 'Union3', 'type': 'BOOLEAN_OPERATION',
            'exportSettings': [{'format': 'PNG', 'constraint': {'type': 'SCALE', 'value': 2.0}}],
            'fills': [{'opacity': 0.2, 'blendMode': 'NORMAL', 'type': 'SOLID',
                       'color': {'r': 0.678, 'g': 0.694, 'b': 0.722, 'a': 1.0}}],
            'effects': [{'type': 'BACKGROUND_BLUR', 'visible': True, 'radius': 15.16}],
            'strokes': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 312.86, 'height': 80.0},
            'layoutAlign': 'INHERIT', 'layoutGrow': 0,
            'children': [],
        }
        ir_list = build_ir([bool_op_node], {})
        ir = ir_list[0] if ir_list else {}
        check('U-86', 'exportSettings PNG BOOLEAN_OPERATION → isImageNode: True',
              ir.get('isImageNode') is True)
        check('U-87', 'exportSettings PNG BOOLEAN_OPERATION → CSS 无 background-color/backdrop-filter',
              'background-color' not in ir.get('css', {}) and 'backdrop-filter' not in ir.get('css', {}))
    except Exception as e:
        for tid in ('U-86', 'U-87'):
            check(tid, 'exportSettings PNG image node', False)
        print(f'       Error: {e}')

    # U-88 ~ U-90: WIDTH_AND_HEIGHT + newlines → white-space: pre（防止 word-wrap 产生多余换行）
    try:
        text_wah_newline = {
            'id': '202:33517', 'name': 'Where can I practice trading\nwithout any risks?',
            'type': 'TEXT',
            'characters': 'Where can I practice trading\nwithout any risks?',
            'style': {'textAutoResize': 'WIDTH_AND_HEIGHT', 'fontSize': 16.17,
                      'fontFamily': 'IBM Plex Sans', 'fontWeight': 450,
                      'textAlignHorizontal': 'CENTER', 'textAlignVertical': 'TOP',
                      'lineHeightPx': 21.0, 'letterSpacing': 0},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 208.0, 'height': 38.0},
            'fills': [{'type': 'SOLID', 'color': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
                       'opacity': 1, 'blendMode': 'NORMAL'}],
            'effects': [], 'strokes': [],
        }
        css_wah = extract_css(text_wah_newline, {})
        check('U-88', 'WIDTH_AND_HEIGHT + newlines → white-space: pre（非 pre-line）',
              css_wah.get('white-space') == 'pre')
        check('U-89', 'WIDTH_AND_HEIGHT + newlines → 不是 nowrap（明确有换行）',
              css_wah.get('white-space') != 'nowrap')

        # HEIGHT（固定高度）+ newlines 仍应为 pre-line
        text_fixed_newline = {
            'id': 'test:fixed', 'name': 'fixed height text\nwith newline',
            'type': 'TEXT',
            'characters': 'fixed height text\nwith newline',
            'style': {'textAutoResize': 'HEIGHT', 'fontSize': 14.0,
                      'fontFamily': 'Inter', 'fontWeight': 400,
                      'textAlignHorizontal': 'LEFT', 'textAlignVertical': 'TOP',
                      'lineHeightPx': 18.0, 'letterSpacing': 0},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200.0, 'height': 40.0},
            'fills': [{'type': 'SOLID', 'color': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
                       'opacity': 1, 'blendMode': 'NORMAL'}],
            'effects': [], 'strokes': [],
        }
        css_fixed = extract_css(text_fixed_newline, {})
        check('U-90', 'HEIGHT + newlines → white-space: pre-line（固定宽度允许 word-wrap）',
              css_fixed.get('white-space') == 'pre-line')
    except Exception as e:
        for tid in ('U-88', 'U-89', 'U-90'):
            check(tid, 'WIDTH_AND_HEIGHT+newlines white-space', False)
        print(f'       Error: {e}')

    # U-91: figmaCounterAxisAlign=CENTER 保护 Patch 18a 不覆盖 align-items:center
    try:
        child_a = {'figmaId': 'a', 'figmaName': 'icon', 'figmaType': 'RECTANGLE',
                   'css': {'width': '16px', 'height': '16px', 'flex-shrink': '0'},
                   'children': []}
        child_b = {'figmaId': 'b', 'figmaName': 'text', 'figmaType': 'TEXT',
                   'isTextNode': True,
                   'css': {'flex-grow': '1'},
                   'children': []}
        # WITH figmaCounterAxisAlign=CENTER → Patch 18a must NOT fire
        node_center = {
            'figmaId': 'parent', 'figmaType': 'FRAME',
            'figmaCounterAxisAlign': 'CENTER',
            'css': {'display': 'flex', 'flex-direction': 'row', 'align-items': 'center'},
            'children': [child_a, child_b],
        }
        patch_flex_shrink(node_center)
        check('U-91',
              'figmaCounterAxisAlign=CENTER → Patch 18a 不覆盖 align-items:center',
              node_center['css'].get('align-items') == 'center')
    except Exception as e:
        check('U-91', 'Patch 18a CENTER guard', False)
        print(f'       Error: {e}')

    # U-92 ~ U-93: patch_top_gradient_reversal ─────────────────────────────────
    print('\n  ─── patch_top_gradient_reversal 测试 ───────────')
    try:
        # U-92: top:0 + transparent→opaque gradient → should reverse to opaque→transparent
        node_top = {
            'css': {
                'top': '0px',
                'background': 'linear-gradient(178.8deg, rgba(255, 255, 255, 0.0) 12.25%, #ffffff 85.06%)',
            },
            'children': []
        }
        patch_top_gradient_reversal(node_top)
        bg = node_top['css']['background']
        check('U-92', 'top:0 transparent→opaque gradient → reversed to opaque→transparent',
              bg == 'linear-gradient(178.8deg, #ffffff 14.94%, rgba(255, 255, 255, 0.0) 87.75%)')
    except Exception as e:
        check('U-92', 'patch_top_gradient_reversal top:0 reversal', False)
        print(f'       Error: {e}')

    try:
        # U-93: bottom overlay (top!=0) → gradient must NOT be reversed
        node_bot = {
            'css': {
                'top': '640.37px',
                'background': 'linear-gradient(178.2deg, rgba(255, 255, 255, 0.0) 18.48%, #ffffff 59.4%)',
            },
            'children': []
        }
        original_bg = node_bot['css']['background']
        patch_top_gradient_reversal(node_bot)
        check('U-93', 'bottom overlay (top!=0px) → gradient 不变',
              node_bot['css']['background'] == original_bg)
    except Exception as e:
        check('U-93', 'patch_top_gradient_reversal non-zero top unchanged', False)
        print(f'       Error: {e}')

    try:
        # U-96: top:0 + rotate(-90deg) = side overlay → gradient 不应被反转
        node_side = {
            'css': {
                'top': '0px',
                'background': 'linear-gradient(180deg, rgba(255, 255, 255, 0.0) 6.03%, #ffffff 93.1%)',
                'transform': 'rotate(-90deg)',
            },
            'children': []
        }
        original_bg = node_side['css']['background']
        patch_top_gradient_reversal(node_side)
        check('U-96', 'top:0 + rotate(-90deg) 侧边 overlay → gradient 不变（不应被反转）',
              node_side['css']['background'] == original_bg)
    except Exception as e:
        check('U-96', 'patch_top_gradient_reversal rotated side overlay unchanged', False)
        print(f'       Error: {e}')

    # U-94 ~ U-95: patch_filter_blend_isolation ────────────────────────────────
    print('\n  ─── patch_filter_blend_isolation 测试 ───────────')
    try:
        # U-94: group with filter + mixed blend/non-blend children →
        #   filter moves from group to non-blend child only
        img_child = {'css': {'width': '297px'}, 'children': []}
        blend_child = {'css': {'mix-blend-mode': 'plus-lighter', 'background': 'linear-gradient(#111, #050505)'}, 'children': []}
        group_node = {
            'css': {'filter': 'drop-shadow(0px 4px 4px rgba(183, 183, 183, 0.25))', 'border-radius': '16px'},
            'children': [blend_child, img_child]
        }
        patch_filter_blend_isolation(group_node)
        check('U-94',
              'filter group → filter 从 group 移到 img_child，blend_child 保留 mix-blend-mode',
              'filter' not in group_node['css']
              and img_child['css'].get('filter') == 'drop-shadow(0px 4px 4px rgba(183, 183, 183, 0.25))'
              and blend_child['css'].get('mix-blend-mode') == 'plus-lighter'
              and 'filter' not in blend_child['css'])
    except Exception as e:
        check('U-94', 'patch_filter_blend_isolation move filter', False)
        print(f'       Error: {e}')

    try:
        # U-95: group with filter + all children have blend mode → filter stays (can't move)
        blend_a = {'css': {'mix-blend-mode': 'plus-lighter'}, 'children': []}
        blend_b = {'css': {'mix-blend-mode': 'screen'}, 'children': []}
        group_all_blend = {
            'css': {'filter': 'drop-shadow(0px 2px 4px rgba(0,0,0,0.3))'},
            'children': [blend_a, blend_b]
        }
        patch_filter_blend_isolation(group_all_blend)
        check('U-95',
              '所有子节点都有 blend mode → filter 保留在 group（无法移动）',
              group_all_blend['css'].get('filter') == 'drop-shadow(0px 2px 4px rgba(0,0,0,0.3))')
    except Exception as e:
        check('U-95', 'patch_filter_blend_isolation all-blend children', False)
        print(f'       Error: {e}')

    # U-97 ~ U-99: gradient text + token guard ────────────────────────────────────
    print('\n  ─── gradient 文字 + token guard 测试 ───────────')
    try:
        # U-97: GRADIENT_LINEAR fill on text character → segment has 'gradient' CSS string
        grad_node = {
            'characters': 'A5000B',
            'characterStyleOverrides': [0, 0, 2, 2, 2, 2, 0],  # 2 = override "5000"
            'styleOverrideTable': {
                '2': {
                    'fills': [{
                        'type': 'GRADIENT_LINEAR',
                        'gradientHandlePositions': [
                            {'x': 0.0, 'y': 0.251},
                            {'x': 0.791, 'y': 0.251},
                            {'x': 0.0, 'y': 5.714},
                        ],
                        'gradientStops': [
                            {'color': {'r': 0.996, 'g': 0.820, 'b': 0.675, 'a': 1.0}, 'position': 0.026},
                            {'color': {'r': 1.0, 'g': 0.612, 'b': 0.180, 'a': 1.0}, 'position': 0.211},
                        ]
                    }]
                }
            },
            'absoluteBoundingBox': {'width': 400, 'height': 36}
        }
        segs = _build_text_segments(grad_node)
        grad_seg = next((s for s in (segs or []) if s.get('gradient')), None)
        check('U-97',
              'GRADIENT_LINEAR 文字段 → segment 包含 gradient CSS string（不是单色 fallback）',
              grad_seg is not None and 'linear-gradient' in (grad_seg.get('gradient') or ''))
    except Exception as e:
        check('U-97', '_build_text_segments gradient fill', False)
        print(f'       Error: {e}')

    try:
        # U-98: _render_text_segment with gradient → outputs background-clip:text inline style
        seg_with_grad = {
            'text': '5000',
            'gradient': 'linear-gradient(90deg, #fed1ac 2.6%, #ff9c2e 21.1%)',
            'color': None
        }
        rendered = _render_text_segment(seg_with_grad)
        check('U-98',
              'gradient 文字段 → JSX 包含 WebkitBackgroundClip 和 WebkitTextFillColor',
              'WebkitBackgroundClip' in rendered and 'WebkitTextFillColor' in rendered and '5000' in rendered)
    except Exception as e:
        check('U-98', '_render_text_segment gradient output', False)
        print(f'       Error: {e}')

    try:
        # U-99: bds-gray-t3 does NOT contain -bg- pattern → not a background semantic token
        check('U-99',
              'bds-gray-t3 不含 -bg- 命名模式 → _is_bg_semantic_token 返回 False（文字灰色 token 正常替换）',
              _is_bg_semantic_token('var(--bds-gray-t3)') is False)
    except Exception as e:
        check('U-99', '_is_bg_semantic_token bds-gray-t3', False)
        print(f'       Error: {e}')


    # ── U-140~U-143: negative itemSpacing + flex position:relative ──────────────

    # U-140: negative itemSpacing must not produce gap:-Xpx (invalid CSS flexbox)
    try:
        node140 = make_node(layoutMode='VERTICAL', itemSpacing=-14,
                            layoutPositioning=None, width=200, height=200)
        ctx140 = make_ctx(layoutMode='VERTICAL')
        css140 = extract_css(node140, ctx140)
        check('U-140', 'F-1: 负 itemSpacing=-14 → 不生成 gap:-14px（CSS flexbox 不支持负 gap）',
              'gap' not in css140)
    except Exception as e:
        check('U-140', 'F-1: 负 itemSpacing 不生成 gap', False)
        print(f'       Error: {e}')

    # U-141: positive itemSpacing must still produce gap
    try:
        node141 = make_node(layoutMode='VERTICAL', itemSpacing=16,
                            layoutPositioning=None, width=200, height=200)
        ctx141 = make_ctx(layoutMode='VERTICAL')
        css141 = extract_css(node141, ctx141)
        check('U-141', 'F-1: 正 itemSpacing=16 → gap:16px（回归验证正值不受影响）',
              css141.get('gap') == '16px')
    except Exception as e:
        check('U-141', 'F-1: 正 itemSpacing 仍生成 gap', False)
        print(f'       Error: {e}')

    # U-142: non-root flex container must get position:relative
    try:
        node142 = make_node(layoutMode='VERTICAL', itemSpacing=8,
                            layoutPositioning=None, width=353, height=1651)
        ctx142 = make_ctx(layoutMode='VERTICAL')
        css142 = extract_css(node142, ctx142)
        check('U-142', 'F-2: 非根 flex 容器 → position:relative（绝对定位子节点的正确锚点）',
              css142.get('position') == 'relative')
    except Exception as e:
        check('U-142', 'F-2: 非根 flex 容器获得 position:relative', False)
        print(f'       Error: {e}')

    # U-143: position:absolute flex container must not be overridden to relative
    try:
        node143 = make_node(layoutMode='HORIZONTAL', itemSpacing=8,
                            layoutPositioning='ABSOLUTE', width=100, height=50)
        ctx143 = make_ctx(layoutMode='NONE')
        css143 = extract_css(node143, ctx143)
        check('U-143', 'F-2: 绝对定位 flex 容器不被覆盖为 relative（position:absolute 保持）',
              css143.get('position') == 'absolute')
    except Exception as e:
        check('U-143', 'F-2: 绝对 flex 容器 position 不被覆盖', False)
        print(f'       Error: {e}')


    # ── U-144 ~ U-145: bds-empty-* token exclusion (F-3) ─────────────────────
    try:
        # U-144: #f7a600 must not be mapped to var(--bds-empty-svg-line)
        token_cache = Path('.figma-to-code/1-design-token')
        tokens_test = load_tokens(token_cache)
        if not tokens_test.get('colors'):
            check('U-144', '#f7a600 不被 bds-empty-svg-line 替换（token 缓存不可用，跳过）', True)
        else:
            css_test = {'background-color': '#f7a600', 'border-radius': '8px'}
            _resolve_css_dict(css_test, tokens_test)
            val = css_test.get('background-color', '')
            check('U-144', '#f7a600 不被 bds-empty-svg-line 替换（brand orange 保持原值）',
                  'bds-empty' not in val)
    except Exception as e:
        check('U-144', '#f7a600 not mapped to bds-empty token', False)
        print(f'       Error: {e}')

    try:
        # U-145: _is_interaction_token uses generic naming patterns (trans-/hover/active/state)
        # bds-empty-* is NOT detected (BDS-specific concept; no equivalent in generic design systems)
        cases = [
            ('var(--bds-empty-svg-line)', False),  # empty-state is BDS-specific, not generic
            ('var(--bds-empty-icon)', False),       # same
            ('var(--bds-trans-hover)', True),       # -trans- pattern matches
            ('var(--bds-state-active)', True),      # -state- pattern matches
            ('var(--bds-brand-yellow)', False),
            ('var(--bds-gray-t1)', False),
        ]
        all_ok = all(_is_interaction_token(v) == expected for v, expected in cases)
        check('U-145', '_is_interaction_token 通用 trans-/state-/hover-/active- 模式识别（bds-empty-* 不再特殊处理）',
              all_ok)
    except Exception as e:
        check('U-145', '_is_interaction_token pattern guard', False)
        print(f'       Error: {e}')




    # ── U-268: flex-row 子级不应使用 elastic centering (margin:auto) ────────────
    # 原因: "Adaptive elastic centering" 对 FIXED 宽度且比父级窄的元素应用
    #       max-width + width:100% + margin:auto。但在 flex-row 父容器中 margin:auto
    #       会吸收剩余空间，把兄弟元素挤开。典型：倒计时块挤开标签，badge 挤开文字。
    try:
        _node_268 = {
            'type': 'INSTANCE',
            'layoutSizingHorizontal': 'FIXED',
            'layoutSizingVertical': 'FIXED',
            'absoluteBoundingBox': {'x': 100, 'y': 100, 'width': 48, 'height': 32},
            'fills': [{'type': 'SOLID', 'color': {'r': 0.75, 'g': 0.82, 'b': 0.9, 'a': 0.12}}],
            'effects': [],
        }
        _ctx_268 = {
            'layoutMode': 'HORIZONTAL',
            'isRoot': False,
            'width': 200,
            'height': 32,
            '_adaptive': True,
        }
        _css_268 = extract_css(_node_268, _ctx_268)
        _no_margin = _css_268.get('margin-left') != 'auto' and _css_268.get('margin-right') != 'auto'
        check('U-268', 'flex-row 子级不应 margin:auto（margin-left=' + str(_css_268.get('margin-left', 'None')) + '）',
              _no_margin)
    except Exception as e:
        check('U-268', f'EXCEPTION: {e}', False)



    # ── U-280: FILL-vertical + padding in row parent → explicit height (not stretch) ──
    # align-self:stretch can be overridden by min-height:auto when padding + content
    # exceeds parent cross-size. Use explicit height to enforce Figma's bb.height.
    try:
        _node_280 = make_node(width=305, height=40, layoutMode='HORIZONTAL',
                              layoutSizingHorizontal='FILL', layoutSizingVertical='FILL',
                              layoutAlign='STRETCH', layoutGrow=1,
                              paddingTop=11.43, paddingBottom=11.43, paddingLeft=4, paddingRight=4,
                              counterAxisAlignItems='CENTER', primaryAxisAlignItems='CENTER')
        _ctx_280 = make_ctx(layoutMode='HORIZONTAL', width=305, height=40)
        _css_280 = extract_css(_node_280, _ctx_280)
        check('U-280', 'FILL-v + padding in row → height:40px (not just align-self:stretch)',
              _css_280.get('height') == '40px')
    except Exception as e:
        check('U-280', f'EXCEPTION: {e}', False)


    # ── U-183/184: imageTransform STRETCH → background-size/position ──
    from lib.css_extractor import _paint_to_css

    paint_183 = {
        'type': 'IMAGE',
        'scaleMode': 'STRETCH',
        'imageTransform': [[0.932, 0.0, 0.0407], [0.0, 0.629, 0.0]],
    }
    result_183 = _paint_to_css(paint_183, {}, 375, 104)
    check('U-183', 'imageTransform STRETCH → background-size ~107%/~159%',
          result_183 is not None and '107.' in result_183)

    paint_184 = {'type': 'IMAGE', 'scaleMode': 'STRETCH'}
    result_184 = _paint_to_css(paint_184, {}, 375, 104)
    check('U-184', 'STRETCH without imageTransform → 100% 100% center',
          result_184 is not None and '100% 100%' in result_184)

    # ── U-185/186: bottom constraint + INSIDE border adjustment ──
    _node_185 = {
        'type': 'VECTOR',
        'layoutPositioning': 'ABSOLUTE',
        'constraints': {'vertical': 'BOTTOM', 'horizontal': 'CENTER'},
        'absoluteBoundingBox': {'x': 1260.51, 'y': 7134.00, 'width': 16.97, 'height': 16.97},
        'fills': [], 'effects': [],
    }
    _ctx_185 = {
        'layoutMode': 'NONE', 'isRoot': False, 'width': 53, 'height': 28,
        'absX': 1242.0, 'absY': 7115.0, 'strokeWeight': 2.0, 'strokeAlign': 'INSIDE',
    }
    _css_185 = extract_css(_node_185, _ctx_185)
    check('U-185', 'INSIDE border shifts bottom by border_w',
          '-9.' in _css_185.get('bottom', '') or '-10' in _css_185.get('bottom', ''))

    _ctx_186 = {
        'layoutMode': 'NONE', 'isRoot': False, 'width': 53, 'height': 28,
        'absX': 1242.0, 'absY': 7115.0, 'strokeWeight': 0, 'strokeAlign': 'INSIDE',
    }
    _css_186 = extract_css(_node_185, _ctx_186)
    check('U-186', 'no INSIDE border → bottom unchanged',
          '-7.' in _css_186.get('bottom', '') or '-8' in _css_186.get('bottom', ''))

    # ── U-315: BDS t1-title Variable 绑定用作 background-color 时应反色为 -revert ──
    from lib.css_extractor import _revert_text_token_for_bg
    check('U-315a', 'var(--bds-gray-t1-title) → var(--bds-gray-t1-title-revert)',
          _revert_text_token_for_bg('var(--bds-gray-t1-title)') == 'var(--bds-gray-t1-title-revert)')
    check('U-315b', 'var(--bds-static-white) unchanged',
          _revert_text_token_for_bg('var(--bds-static-white)') == 'var(--bds-static-white)')
    check('U-315c', 'color-mix with t1-title also reverted',
          'title-revert' in _revert_text_token_for_bg('color-mix(in srgb, var(--bds-gray-t1-title) 80%, transparent)'))

    # U-315d: _extract_fills integration — SOLID fill with t1-title variable binding
    _node_315 = {
        'type': 'FRAME',
        'fills': [{'type': 'SOLID', 'color': {'r': 1, 'g': 1, 'b': 1, 'a': 1}, 'visible': True,
                   'boundVariables': {'color': {'id': 'VariableID:abc123/2772:355'}}}],
        'effects': [], 'strokes': [],
    }
    _ctx_315 = {'layoutMode': 'NONE', 'isRoot': False, 'width': 200, 'height': 40, 'absX': 0, 'absY': 0}
    _var_id_to_bds_315 = {'VariableID:abc123/2772:355': '--bds-gray-t1-title'}
    from lib.css_extractor import _extract_fills
    _css_315 = {}
    _extract_fills(_css_315, _node_315, {}, _var_id_to_bds_315)
    # hex=#ffffff 匹配 dark 主题标准值，无需 revert
    check('U-315d', 'background-color keeps token (hex matches theme value)',
          _css_315.get('background-color') == 'var(--bds-gray-t1-title)')


    # U-316: RECTANGLE+IMAGE nodes do NOT get object-fit from css_extractor.
    # Object-fit is handled at the split layer (split_codegen) where varying-prop
    # context is available, to avoid breaking non-shared-component image nodes.
    _node_316 = {
        'type': 'RECTANGLE',
        'fills': [{'type': 'IMAGE', 'scaleMode': 'FILL', 'visible': True,
                   'imageRef': 'f0dae2dc593baf21757e226078730a41964cf4a7'}],
        'effects': [], 'strokes': [],
        'width': 712, 'height': 811,
    }
    _ctx_316 = {'layoutMode': 'NONE', 'isRoot': False, 'width': 500, 'height': 400,
                'absX': 0, 'absY': 0, 'constraints': {'horizontal': 'CENTER', 'vertical': 'CENTER'}}
    _css_316 = extract_css(_node_316, _ctx_316)
    check('U-316a', 'RECTANGLE IMAGE → css_extractor does NOT add object-fit (handled at split layer)',
          'object-fit' not in _css_316)

    # U-316c: FRAME with image fill also no object-fit
    _ctx_316c = {'layoutMode': 'NONE', 'isRoot': False, 'width': 200, 'height': 200,
                 'absX': 0, 'absY': 0}
    _node_316c = {
        'type': 'FRAME',
        'fills': [{'type': 'IMAGE', 'scaleMode': 'FILL', 'visible': True, 'imageRef': 'xyz'}],
        'effects': [], 'strokes': [],
        'width': 200, 'height': 200,
    }
    _css_316c = extract_css(_node_316c, _ctx_316c)
    check('U-316c', 'FRAME IMAGE fill → no object-fit from css_extractor',
          'object-fit' not in _css_316c)


    # U-317: Fixed-size text node (textAutoResize=None) that is single-line in Figma must get
    # white-space:nowrap to prevent browser font-metric differences from wrapping the text.
    # Real data: node 6777:33242 from Arena page (nodeId: 6777-33238)
    #   characters: 'Round 1: Nov 17, 2025, 10AM UTC -Nov 28, 2025, 11:59PM UTC'
    #   textAutoResize: None (fixed-size box), lineHeightPx=24, height=15 → single line
    _node_317 = {
        'type': 'TEXT',
        'characters': 'Round 1: Nov 17, 2025, 10AM UTC -Nov 28, 2025, 11:59PM UTC',
        'style': {
            'textAutoResize': None,  # fixed-size text box — the key difference
            'lineHeightPx': 24.0,
            'fontSize': 12,
            'fontFamily': 'IBM Plex Sans',
            'fontWeight': 400,
            'fills': [{'type': 'SOLID', 'color': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0}, 'visible': True}],
            'textAlignHorizontal': 'LEFT',
        },
        'fills': [],
        'effects': [], 'strokes': [],
        'width': 359.0, 'height': 15.0,  # height=15 ≤ lineHeightPx*1.2=28.8 → single line
    }
    _ctx_317 = {'layoutMode': 'NONE', 'isRoot': False, 'width': 400, 'height': 100,
                'absX': 0, 'absY': 0}
    _css_317 = extract_css(_node_317, _ctx_317)
    check('U-317', 'fixed-size single-line TEXT (textAutoResize=None, h≤lh*1.2) → white-space:nowrap',
          _css_317.get('white-space') == 'nowrap')

    # U-318: Auto-layout frame where a child overflows the padding zone — actual child offset
    # < API-reported padding. Padding should be corrected to min actual child offset.
    # Real data: Portal Revised node 6777:33071 (text-box)
    #   paddingLeft/Right=40, but text child at x=20 from parent → use 20px not 40px
    _node_318 = {
        'type': 'FRAME',
        'layoutMode': 'VERTICAL',
        'primaryAxisAlignItems': 'CENTER',
        'counterAxisAlignItems': 'CENTER',
        'paddingTop': 32.0, 'paddingBottom': 32.0,
        'paddingLeft': 40.0, 'paddingRight': 40.0,
        'absoluteBoundingBox': {'x': 8314.0, 'y': 2700.0, 'width': 282.0, 'height': 196.0},
        'fills': [], 'effects': [], 'strokes': [],
        'width': 282.0, 'height': 196.0,
        'children': [
            {'type': 'FRAME', 'id': '6777:33072',
             'absoluteBoundingBox': {'x': 8354.0, 'y': 2732.0, 'width': 202.0, 'height': 70.0}},
            {'type': 'TEXT', 'id': '6777:33075',
             'absoluteBoundingBox': {'x': 8334.0, 'y': 2810.0, 'width': 242.0, 'height': 54.0}},
        ],
    }
    _ctx_318 = {'layoutMode': 'NONE', 'isRoot': False, 'width': 400, 'height': 400,
                'absX': 0, 'absY': 0}
    _css_318 = extract_css(_node_318, _ctx_318)
    check('U-318',
          'auto-layout frame with overflowing child → padding corrected to min actual child offset',
          _css_318.get('padding') == '32px 20px 32px 20px')


    # ── U-319: BDS -bg token 在低透明度填充时应直接使用 token（不加 color-mix） ──────────────────
    # Real data: node 6777:32826 "1_Tag (Default)" from PortalRevised (nodeId: 6777-32751)
    # fill opacity=0.12, bds_token='--bds-green-100-bg'
    # 当前 bug：css_extractor 生成 color-mix(in srgb, var(--bds-green-100-bg) 12%, transparent)
    # 导致角标背景几乎透明；-bg 后缀的 token 本身已是背景颜色，不应再乘以透明度。
    _node_319 = {
        'type': 'FRAME',
        'fills': [{'type': 'SOLID', 'color': {'r': 0.8, 'g': 0.96, 'b': 0.85, 'a': 1},
                   'opacity': 0.12, 'visible': True,
                   'boundVariables': {'color': {'id': 'VariableID:test319/1234:567'}}}],
        'effects': [], 'strokes': [],
    }
    _var_id_to_bds_319 = {'VariableID:test319/1234:567': '--bds-green-100-bg'}
    _css_319 = {}
    _extract_fills(_css_319, _node_319, {}, _var_id_to_bds_319)
    check('U-319', 'BDS -bg token with fill opacity=0.12 → var(TOKEN)，不应加 color-mix',
          _css_319.get('background-color') == 'var(--bds-green-100-bg)')


    # ── U-320: layoutMode=GRID 节点应从 gridColumnGap/gridRowGap 提取 gap CSS ────────────
    # Real data: node 6777:32842 "Container" from PortalRevised (nodeId: 6777-32751)
    # layoutMode=GRID, gridColumnGap=24, gridColumnCount=2
    # 当前 bug：css_extractor 只读 itemSpacing（GRID 节点无此字段），导致 gap 为 0，
    # 两张卡片直接相邻，中间缺少 24px 间距。
    _node_320 = {
        'type': 'FRAME',
        'layoutMode': 'GRID',
        'primaryAxisSizingMode': 'FIXED',
        'counterAxisSizingMode': 'FIXED',
        'gridColumnCount': 2,
        'gridRowCount': 1,
        'gridColumnGap': 24.0,
        'gridRowGap': 0.0,
        'gridColumnsSizing': 'repeat(2,minmax(0,1fr))',
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1200, 'height': 98},
        'fills': [], 'effects': [], 'strokes': [],
        # itemSpacing 不设置，模拟 GRID 节点的真实数据
    }
    _ctx_320 = {'layoutMode': 'NONE', 'isRoot': False, 'width': 1200, 'height': 160,
                'absX': 0, 'absY': 0}
    _css_320 = extract_css(_node_320, _ctx_320)
    check('U-320', 'layoutMode=GRID + gridColumnGap=24 → gap: 24px',
          _css_320.get('gap') == '24px' or _css_320.get('column-gap') == '24px')


    # ── U-321: PROGRESSIVE BACKGROUND_BLUR → no backdrop-filter ─────────────────
    # Real data: node 4030:41712 "Frame 2147224635" from Brand6PreKyc (nodeId: 4030-41365)
    # blurType=PROGRESSIVE, startRadius=0, radius=30 — fades from 0px to 30px top→bottom.
    # CSS has no progressive blur primitive; uniform blur(30px) is visually wrong at top.
    _node_321 = {
        'type': 'FRAME',
        'layoutMode': 'VERTICAL',
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 388, 'height': 136},
        'fills': [{'type': 'GRADIENT_LINEAR', 'visible': False, 'opacity': 0.4,
                   'blendMode': 'NORMAL', 'gradientHandlePositions': [],
                   'gradientStops': []}],
        'strokes': [],
        'effects': [{
            'type': 'BACKGROUND_BLUR',
            'visible': True,
            'blurType': 'PROGRESSIVE',
            'startRadius': 0.0,
            'startOffset': {'x': 0.5, 'y': 0.0},
            'endOffset':   {'x': 0.5, 'y': 1.0},
            'radius': 30.0,
        }],
    }
    _ctx_321 = make_ctx(width=388, height=136)
    _css_321 = extract_css(_node_321, _ctx_321)
    check('U-321', 'PROGRESSIVE BACKGROUND_BLUR → no backdrop-filter (CSS 不支持渐进模糊)',
          'backdrop-filter' not in _css_321 and '-webkit-backdrop-filter' not in _css_321)

    # ── U-322: 普通 BACKGROUND_BLUR (无 blurType) → backdrop-filter 保留（回归）────
    _node_322 = {
        'type': 'FRAME',
        'layoutMode': 'NONE',
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200, 'height': 200},
        'fills': [{'type': 'SOLID', 'visible': True, 'opacity': 0.3,
                   'blendMode': 'NORMAL',
                   'color': {'r': 1, 'g': 1, 'b': 1, 'a': 1}}],
        'strokes': [],
        'effects': [{
            'type': 'BACKGROUND_BLUR',
            'visible': True,
            'radius': 20.0,
            # blurType absent → regular uniform blur
        }],
    }
    _ctx_322 = make_ctx(width=200, height=200)
    _css_322 = extract_css(_node_322, _ctx_322)
    check('U-322', '普通 BACKGROUND_BLUR (无 blurType) → backdrop-filter: blur(20px) 保留',
          _css_322.get('backdrop-filter') == 'blur(20px)')

    # ── U-324: 根帧 clipsContent=True → overflow-x: hidden（横向裁剪），纵向不限 ────
    # 真实来源: TomorrowlandLandingPage4 根节点 39641:6863，蝴蝶装饰图 40017:4440
    # 右边缘 1552px 导致截图宽于 1440px，pixel-diff 直接偏移。
    _node_324a = {
        'type': 'FRAME',
        'layoutMode': 'NONE',
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 5020},
        'fills': [{'type': 'SOLID', 'visible': True, 'color': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
                   'blendMode': 'NORMAL', 'opacity': 1.0}],
        'strokes': [],
        'effects': [],
        'clipsContent': True,
    }
    _ctx_324a = make_ctx(isRoot=True, width=1440, height=5020)
    _css_324a = extract_css(_node_324a, _ctx_324a)
    check('U-324a', 'root frame clipsContent=True → overflow-x: hidden（横向装饰图不溢出）',
          _css_324a.get('overflow-x') == 'hidden')
    check('U-324a-no-overflow-y', 'root frame clipsContent=True → 不设 overflow-y（允许页面竖向滚动）',
          'overflow-y' not in _css_324a and _css_324a.get('overflow') != 'hidden')

    _node_324b = {
        'type': 'FRAME',
        'layoutMode': 'NONE',
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 3000},
        'fills': [{'type': 'SOLID', 'visible': True, 'color': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
                   'blendMode': 'NORMAL', 'opacity': 1.0}],
        'strokes': [],
        'effects': [],
        'clipsContent': False,
    }
    _ctx_324b = make_ctx(isRoot=True, width=1440, height=3000)
    _css_324b = extract_css(_node_324b, _ctx_324b)
    check('U-324b', 'root frame clipsContent=False → 不加 overflow-x（回归：其他页面无此属性）',
          'overflow-x' not in _css_324b and 'overflow' not in _css_324b)

    # ── U-325: ABSOLUTE 子节点不参与 padding 缩减计算 ─────────────────────────────
    # 真实来源: Hot Events section 4030:41521（Brand6PreKyc）
    # Cursor 指示器（4030:41562，layoutPositioning=ABSOLUTE）右边界距框右 50px，
    # 错误触发 paddingRight 从 120→72→50，最终输出 padding: 0px 50px 0px 72px（非对称）。
    # 正确行为：ABSOLUTE 子节点跳过 padding 缩减，保持对称 72px（由 AUTO 子节点决定）。
    _parent_x = 0
    _parent_w = 1440
    _node_325 = {
        'type': 'FRAME',
        'layoutMode': 'HORIZONTAL',
        'absoluteBoundingBox': {'x': _parent_x, 'y': 0, 'width': _parent_w, 'height': 124},
        'paddingLeft': 120.0, 'paddingRight': 120.0, 'paddingTop': 0.0, 'paddingBottom': 0.0,
        'fills': [], 'strokes': [], 'effects': [],
        'children': [
            # LEFT icon (AUTO) — at x=72, effectively paddingLeft=72
            {'id': 'icon-left', 'layoutPositioning': 'AUTO',
             'absoluteBoundingBox': {'x': 72, 'y': 50, 'width': 24, 'height': 24}},
            # List (AUTO) — 1200px wide, at x=120
            {'id': 'list', 'layoutPositioning': 'AUTO',
             'absoluteBoundingBox': {'x': 120, 'y': 0, 'width': 1200, 'height': 124}},
            # RIGHT icon (AUTO) — right edge at 1440-72=1368, paddingRight=72
            {'id': 'icon-right', 'layoutPositioning': 'AUTO',
             'absoluteBoundingBox': {'x': 1344, 'y': 50, 'width': 24, 'height': 24}},
            # Cursor scroll indicator (ABSOLUTE) — right edge at 1440-50=1390, should be IGNORED
            {'id': 'cursor', 'layoutPositioning': 'ABSOLUTE',
             'absoluteBoundingBox': {'x': 1358, 'y': 10, 'width': 32, 'height': 32}},
        ],
    }
    _ctx_325 = make_ctx(width=_parent_w, height=124)
    _css_325 = extract_css(_node_325, _ctx_325)
    _pad_325 = _css_325.get('padding', '')
    check('U-325', 'ABSOLUTE 子节点跳过 padding 缩减 → padding: 0px 72px 0px 72px（非 50px）',
          _pad_325 == '0px 72px 0px 72px')

    # ── U-417: TEXT 节点 SOLID fill 是 BDS variable 时写入 _rawColorHex ──────────
    # Real case: EU deposit LP 大标题用 var(--bds-gray-t1-title)，在深色模式下
    # 该 token 解析为白色（#ffffff）。没有 _rawColorHex 则无法从文字颜色推断页面主题。
    from lib.css_extractor import _extract_fills
    _node_417 = {
        'type': 'TEXT',
        'fills': [{'type': 'SOLID', 'visible': True,
                   'color': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0},
                   'boundVariables': {'color': {'id': 'VariableID:eu417/1234:56'}}}],
        'effects': [], 'strokes': [],
    }
    _vmap_417 = {'VariableID:eu417/1234:56': '--bds-gray-t1-title'}
    _css_417 = {}
    _extract_fills(_css_417, _node_417, {}, _vmap_417)
    check('U-417a', 'TEXT BDS variable → color 为 var()',
          _css_417.get('color', '').startswith('var('))
    check('U-417b', 'TEXT BDS variable → _rawColorHex 写入 Figma 解析色值 #ffffff',
          _css_417.get('_rawColorHex') == '#ffffff')

    # ── U-418: 不可见子节点不应参与 padding 校正 ─────────────────────────────────
    # Real data: node 177:13510 (Example_Primary Buttons-Dark) from ExamplePage.
    # icon1 (x=11984.5, w=18) and icon2 (x=12075.5, w=18) are both visible=False.
    # icon2 is only 2px from the right edge: (11956.5+139)-(12075.5+18)=2.
    # Without the fix, the correction loop reduces paddingRight 28→2, then the
    # CENTER-layout symmetry rule also reduces paddingLeft 28→2, producing "12px 2px".
    from lib.css_extractor import _extract_flex_container
    _node_418 = {
        'id': '177:13510',
        'type': 'INSTANCE',
        'paddingTop': 12.0,
        'paddingRight': 28.0,
        'paddingBottom': 12.0,
        'paddingLeft': 28.0,
        'layoutMode': 'HORIZONTAL',
        'primaryAxisAlignItems': 'CENTER',
        'absoluteBoundingBox': {'x': 11956.5, 'y': 4011.0, 'width': 139.0, 'height': 48.0},
        'children': [
            {
                'id': 'I177:13510;2:8052',
                'name': 'icon1',
                'visible': False,
                'absoluteBoundingBox': {'x': 11984.5, 'y': 4026.0, 'width': 18.0, 'height': 18.0},
                'layoutPositioning': None,
            },
            {
                'id': 'I177:13510;2:8053',
                'name': 'Button Text',
                'visible': None,
                'absoluteBoundingBox': {'x': 11984.5, 'y': 4023.0, 'width': 83.0, 'height': 24.0},
                'layoutPositioning': None,
            },
            {
                'id': 'I177:13510;2:8054',
                'name': 'icon2',
                'visible': False,
                'absoluteBoundingBox': {'x': 12075.5, 'y': 4026.0, 'width': 18.0, 'height': 18.0},
                'layoutPositioning': None,
            },
        ],
    }
    _css_418 = {}
    _extract_flex_container(_css_418, _node_418)
    check('U-418', '不可见子节点不参与 padding 校正：padding 应保持 12px 28px 12px 28px',
          _css_418.get('padding') == '12px 28px 12px 28px')

    # ── U-467: 根节点 FRAME opacity < 1 → CSS 不输出 opacity ──────────────────────
    # Real data: node 6777:32751 from PortalRevised (PortalRevised page, nodeId: 6777-32751)
    # 设计师在 Figma 给页面根 FRAME 设了 opacity=0.2（可能是设计草稿标记），
    # 生产代码中根节点不应输出 opacity，否则整页呈现半透明。
    _node_467 = {
        'type': 'FRAME',
        'opacity': 0.20000000298023224,
        'layoutMode': 'VERTICAL',
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 5787},
        'fills': [{'type': 'SOLID', 'visible': True,
                   'color': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
                   'blendMode': 'NORMAL', 'opacity': 1.0}],
        'strokes': [], 'effects': [], 'clipsContent': True,
    }
    _ctx_467 = make_ctx(isRoot=True, width=1440, height=5787)
    _css_467 = extract_css(_node_467, _ctx_467)
    check('U-467', 'root FRAME opacity=0.2 → CSS 不输出 opacity（整页不能半透明）',
          'opacity' not in _css_467)

    # ── U-468: HORIZONTAL + 无 primaryAxisAlignItems + 对称 padding + 单子节点 → justify-content: center ──
    # Real data: node 6777:32824 from PortalRevised (button wrapper frame, nodeId: 6777-32751)
    # HORIZONTAL Auto Layout，primaryAxisAlignItems 未设（即 MIN/flex-start），
    # paddingLeft=paddingRight=36，单子按钮宽 516px。
    # 设计稿宽 588px 时按钮填满内容区，但父卡片有 flex-grow=1，
    # 卡片变宽后按钮会贴左边缘。对称 padding + 单子节点是居中意图的信号，应输出 justify-content: center。
    _node_468 = {
        'type': 'FRAME',
        'layoutMode': 'HORIZONTAL',
        'absoluteBoundingBox': {'x': 27655.5, 'y': 0, 'width': 588, 'height': 88},
        'paddingLeft': 36.0, 'paddingRight': 36.0, 'paddingTop': 0.0, 'paddingBottom': 40.0,
        'fills': [], 'strokes': [], 'effects': [],
        'children': [
            {
                'id': '6777:32825',
                'name': 'By_Primary Buttons-Dark',
                'type': 'INSTANCE',
                'visible': None,
                'absoluteBoundingBox': {'x': 27691.5, 'y': 0, 'width': 516, 'height': 48},
                'layoutPositioning': None,
            }
        ],
    }
    _ctx_468 = make_ctx(width=588, height=88, layoutMode='VERTICAL')
    _css_468 = extract_css(_node_468, _ctx_468)
    check('U-468', 'HORIZONTAL + 无 primaryAxisAlignItems + 对称 padding + 单子节点 → justify-content: center',
          _css_468.get('justify-content') == 'center')


if __name__ == '__main__':
    reset()
    run_tests()
    ok = print_summary()
    sys.exit(0 if ok else 1)
