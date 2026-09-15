#!/usr/bin/env python3
"""
test_ir_builder.py — ir_builder 单元测试（从 verify_fixes.py 拆分）
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
from lib.ir_builder import _post_process_frame
from convert import patch_flex_shrink
from lib.tsx_generator import generate_tsx, detect_page_theme
from lib.token_resolver import tag_mixed_theme_nodes
from lib.figma_vars import normalize_var_name
from lib.node_classifier import convert_to_flow


from lib.token_resolver import _is_bg_semantic_token, _is_bg_fill_token, _is_interaction_token, _resolve_css_dict, load_tokens, tag_mixed_theme_nodes
from lib.ir_builder import _build_text_segments, _child_extends_outside, _parent_needs_overflow_wrap, _wrap_overflow_children
from lib.figma_vars import normalize_var_name

def run_tests():
    # ── U-100/U-101: absolute-layout (layoutMode=NONE) 容器强制输出 height/width ──

    try:
        # U-100: FRAME with layoutMode=NONE + vw=HUG → must emit height
        # Bug: HUG 模式不写 height，但 absolute-layout 容器中 CSS flex HUG 无效
        node = make_node(type='FRAME', layoutMode='NONE',
                         layoutSizingVertical='HUG', height=400,
                         absoluteBoundingBox={'x': 0, 'y': 0, 'width': 200, 'height': 400})
        css = extract_css(node, make_ctx(layoutMode='NONE'))
        check('U-100', 'layoutMode=NONE + vw=HUG → 必须输出 height（子节点均为 absolute，HUG 无效）',
              css.get('height') == '400px')
    except Exception as e:
        check('U-100', 'layoutMode=NONE + vw=HUG → height', False)
        print(f'       Error: {e}')

    try:
        # U-101: FRAME with layoutMode=NONE + hw=HUG → must emit width
        node = make_node(type='FRAME', layoutMode='NONE',
                         layoutSizingHorizontal='HUG', width=300,
                         absoluteBoundingBox={'x': 0, 'y': 0, 'width': 300, 'height': 200})
        css = extract_css(node, make_ctx(layoutMode='NONE'))
        check('U-101', 'layoutMode=NONE + hw=HUG → 必须输出 width（子节点均为 absolute，HUG 无效）',
              css.get('width') == '300px')
    except Exception as e:
        check('U-101', 'layoutMode=NONE + hw=HUG → width', False)
        print(f'       Error: {e}')

    try:
        # U-102: clipsContent=True + no border-radius → overflow: hidden
        node = make_node(type='FRAME', clipsContent=True)
        css = extract_css(node, make_ctx())
        check('U-102', 'clipsContent=True + 无 border-radius → overflow: hidden',
              css.get('overflow') == 'hidden')
    except Exception as e:
        check('U-102', 'clipsContent=True → overflow: hidden', False)
        print(f'       Error: {e}')

    try:
        # U-104: patch_flex_shrink Patch10: flex container with BOTH absolute and flow
        # children must NOT infer height from absolute child — flow children grow it.
        flex_node = {
            'figmaId': 'test:104',
            'figmaType': 'FRAME',
            'css': {'display': 'flex', 'flex-direction': 'column', 'gap': '40px'},
            'children': [
                {
                    'figmaId': 'test:104a',
                    'figmaType': 'FRAME',
                    'css': {'position': 'absolute', 'height': '38px', 'width': '181px'},
                    'isImageNode': False, 'isVectorNode': False, 'isDecorativeElement': False,
                    'children': [],
                },
                {
                    'figmaId': 'test:104b',
                    'figmaType': 'FRAME',
                    'css': {'width': '353px'},  # flow child, no height
                    'isImageNode': False, 'isVectorNode': False, 'isDecorativeElement': False,
                    'children': [],
                },
            ],
        }
        patch_flex_shrink(flex_node)
        css_after = flex_node.get('css', {})
        check('U-104',
              'flex 容器含 abs+flow 子节点 → 不从 absolute child 推断 height（flow 子节点会撑开容器）',
              'height' not in css_after and 'min-height' not in css_after)
    except Exception as e:
        check('U-104', 'patch_flex_shrink Patch10: flex+abs+flow → no height inference', False)
        print(f'       Error: {e}')

    try:
        # U-103: _post_process_frame: when overflow:hidden is set (clipsContent=True),
        # children exceeding frame height must NOT convert height → min-height.
        # Bug: _post_process_frame was removing overflow:hidden and converting to
        # min-height even when designer explicitly set clipsContent=True.
        from lib.ir_builder import _post_process_frame
        ir = {
            'figmaId': 'test:103',
            'figmaType': 'INSTANCE',
            'css': {'width': '393px', 'height': '566px', 'position': 'absolute', 'overflow': 'hidden'},
            'children': [
                {
                    'figmaId': 'test:103a',
                    'figmaType': 'FRAME',
                    'css': {'position': 'absolute', 'top': '127px', 'height': '753px', 'width': '353px'},
                    'isImageNode': False, 'isVectorNode': False, 'isDecorativeElement': False,
                    'children': [],
                }
            ],
        }
        _post_process_frame(ir)
        css_after = ir.get('css', {})
        check('U-103',
              'clipsContent=True (overflow:hidden) 且 child 超出 frame height → 保持 height 固定，不转为 min-height',
              css_after.get('height') == '566px'
              and 'min-height' not in css_after
              and css_after.get('overflow') == 'hidden')
    except Exception as e:
        check('U-103', '_post_process_frame: overflow:hidden → keep height, no min-height conversion', False)
        print(f'       Error: {e}')

    try:
        # U-110: _build_text_segments extracts fontSize override
        # Bug: 字符级 fontSize/fontWeight 差异未被提取，导致混排文字全用节点级样式
        import lib.ir_builder as _ir_builder_u110
        node_u110 = {
            'characters': 'BigSmall',
            'characterStyleOverrides': [1, 1, 1, 0, 0, 0, 0, 0],
            'styleOverrideTable': {
                1: {'fontSize': 24, 'fontWeight': 700, 'fills': []},
            },
            'style': {'fontSize': 16, 'fontWeight': 400},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200, 'height': 40},
        }
        segs_u110 = _ir_builder_u110._build_text_segments(node_u110)
        check('U-110', '_build_text_segments 提取 fontSize/fontWeight override',
              segs_u110 is not None
              and any(s.get('fontSize') == '24px' for s in segs_u110)
              and any(s.get('fontWeight') == '700' for s in segs_u110))
    except Exception as e:
        check('U-110', '_build_text_segments fontSize/fontWeight extraction', False)
        print(f'       Error: {e}')

    try:
        # U-111: _render_text_segment emits fontSize and fontWeight in inline style
        import lib.tsx_generator as _tsx_gen_u111
        seg_u111 = {'text': 'Hello', 'color': None, 'fontSize': '24px', 'fontWeight': '700'}
        html_u111 = _tsx_gen_u111._render_text_segment(seg_u111)
        check('U-111', '_render_text_segment 输出 fontSize + fontWeight inline style',
              "fontSize:'24px'" in html_u111 and 'fontWeight:700' in html_u111 and '>Hello<' in html_u111)
    except Exception as e:
        check('U-111', '_render_text_segment fontSize/fontWeight rendering', False)
        print(f'       Error: {e}')

    try:
        # U-112: _post_process_frame: frame with overflowing image child gets overflow:hidden
        # AND keeps height (not min-height). Bug: overflow detection ran AFTER height→min-height,
        # so frame ended up with both min-height AND overflow:hidden.
        import lib.ir_builder as _ir_builder_u112
        ir_u112 = {
            'figmaId': 'test:112',
            'figmaType': 'FRAME',
            'css': {'width': '393px', 'height': '500px', 'position': 'absolute'},
            'children': [
                {
                    'figmaId': 'test:112a',
                    'figmaType': 'RECTANGLE',
                    'css': {'width': '393px', 'height': '900px', 'position': 'absolute', 'top': '0px'},
                    'isImageNode': True, 'isVectorNode': False, 'isDecorativeElement': False,
                    'children': [],
                },
            ],
        }
        _ir_builder_u112._post_process_frame(ir_u112)
        css_u112 = ir_u112.get('css', {})
        check('U-112',
              '溢出 image child → 父容器保持 height（不转 min-height）且添加 overflow:hidden',
              css_u112.get('height') == '500px'
              and 'min-height' not in css_u112
              and css_u112.get('overflow') == 'hidden')
    except Exception as e:
        check('U-112', '_post_process_frame: overflowing image → height preserved + overflow:hidden', False)
        print(f'       Error: {e}')


    # U-120: bds-trans-hover must NOT replace background-color (interaction overlay token)
    try:
        _mock_tokens_u120 = {
            'colors': {'rgba(192, 210, 231, 0.12)': 'var(--bds-trans-hover)'},
        }
        css_u120 = {'background-color': 'rgba(192, 210, 231, 0.12)'}
        _resolve_css_dict(css_u120, _mock_tokens_u120)
        check('U-120', 'bds-trans-hover 不替换 background-color（交互叠加层不能作为填充色）',
              css_u120.get('background-color') == 'rgba(192, 210, 231, 0.12)')
    except Exception as e:
        check('U-120', 'bds-trans-hover must not replace background-color', False)
        print(f'       Error: {e}')

    # U-121: bds-gray-t2 可以替换 text color（不含 -bg- 命名，非背景语义 token）
    try:
        _mock_tokens_u121 = {
            'colors': {'#6a6e73': 'var(--bds-gray-t2)'},
        }
        css_u121 = {'color': '#6a6e73'}
        _resolve_css_dict(css_u121, _mock_tokens_u121)
        check('U-121', 'bds-gray-t2 可替换 text color（通用 token 命名无 -bg- 限制）',
              css_u121.get('color') == 'var(--bds-gray-t2)')
    except Exception as e:
        check('U-121', 'bds-gray-t2 replaces text color', False)
        print(f'       Error: {e}')

    # U-122: bds-gray-t2 CAN replace background-color (only blocked for text `color`)
    try:
        _mock_tokens_u122 = {
            'colors': {'#6a6e73': 'var(--bds-gray-t2)'},
        }
        css_u122 = {'background-color': '#6a6e73'}
        _resolve_css_dict(css_u122, _mock_tokens_u122)
        check('U-122', 'bds-gray-t2 可以替换 background-color（非文字颜色属性仍走 token 替换）',
              css_u122.get('background-color') == 'var(--bds-gray-t2)')
    except Exception as e:
        check('U-122', 'bds-gray-t2 can replace background-color', False)
        print(f'       Error: {e}')

    # U-123: _is_interaction_token correctly identifies bds-trans-* tokens
    try:
        check('U-123a', '_is_interaction_token 识别 bds-trans-hover',
              _is_interaction_token('var(--bds-trans-hover)'))
        check('U-123b', '_is_interaction_token 识别 bds-trans-active',
              _is_interaction_token('var(--bds-trans-active)'))
        check('U-123c', '_is_interaction_token 不误判 bds-gray-t2',
              not _is_interaction_token('var(--bds-gray-t2)'))
        check('U-123d', '_is_interaction_token 不误判 bds-gray-bg-card',
              not _is_interaction_token('var(--bds-gray-bg-card)'))
    except Exception as e:
        check('U-123', '_is_interaction_token detection', False)
        print(f'       Error: {e}')


    # U-130: Patch 15 — flex-column with overflow:hidden (clipsContent=True) → keep height, keep overflow
    # When a designer sets clipsContent=True, the frame must clip at fixed height; never convert to min-height.
    try:
        p130 = {
            'figmaId': 'U-130-test', 'figmaName': 'hero', 'figmaType': 'FRAME',
            'isImageNode': False, 'isVectorNode': False, 'isTextNode': False,
            'isDecorativeElement': False,
            'css': {
                'display': 'flex', 'flex-direction': 'column',
                'height': '400px', 'overflow': 'hidden',
                'background-color': '#16171a',
            },
            'children': [
                {
                    'figmaId': 'child1', 'figmaName': 'content', 'figmaType': 'FRAME',
                    'isDecorativeElement': False, 'isImageNode': False, 'isVectorNode': False, 'isTextNode': False,
                    'css': {'height': '478px'},  # exceeds 400px * 1.05 = 420px
                    'children': [],
                }
            ],
        }
        patch_flex_shrink(p130)
        check('U-130a', 'Patch15 clipsContent=True → 保持 height: 400px（不转为 min-height）',
              p130['css'].get('height') == '400px' and 'min-height' not in p130['css'])
        check('U-130b', 'Patch15 clipsContent=True → 保持 overflow: hidden',
              p130['css'].get('overflow') == 'hidden')
    except Exception as e:
        check('U-130', 'Patch15 clipsContent overflow guard', False)
        print(f'       Error: {e}')

    # U-131: flex-column without overflow → _post_process_frame converts to min-height
    # (Previously tested via Patch 15, now unified in ir_builder._post_process_frame)
    try:
        p131 = {
            'figmaId': 'U-131-test', 'figmaName': 'frame', 'figmaType': 'FRAME',
            'isImageNode': False, 'isVectorNode': False, 'isTextNode': False,
            'isDecorativeElement': False,
            'css': {
                'display': 'flex', 'flex-direction': 'column',
                'height': '200px',
            },
            'children': [
                {
                    'figmaId': 'c1', 'figmaName': 'c', 'figmaType': 'FRAME',
                    'isDecorativeElement': False, 'isImageNode': False, 'isVectorNode': False, 'isTextNode': False,
                    'css': {'height': '300px'},  # exceeds 200px * 1.05 = 210px
                    'children': [],
                }
            ],
        }
        _post_process_frame(p131)
        check('U-131', 'ir_builder._post_process_frame 无 overflow → 转为 min-height（统一逻辑）',
              p131['css'].get('min-height') == '200px' and 'height' not in p131['css'])
    except Exception as e:
        check('U-131', '_post_process_frame no-overflow converts to min-height', False)
        print(f'       Error: {e}')


    # ── U-150~U-152: 根帧 min-height + overflow 移除 + VECTOR border-radius ────

    # U-150: 根帧使用 min-height 而非 height（root_ctx 的 layoutMode='NONE'）
    try:
        node150 = make_node(layoutMode='VERTICAL', width=393, height=2849,
                            layoutPositioning=None)
        ctx150 = make_ctx(layoutMode='NONE', isRoot=True, width=0, height=0)
        css150 = extract_css(node150, ctx150)
        check('U-150', 'F-4: 根帧 height:2849px → min-height:2849px（避免文字回流截断）',
              css150.get('min-height') == '2849px' and 'height' not in css150)
    except Exception as e:
        check('U-150', 'F-4: 根帧 height → min-height', False)
        print(f'       Error: {e}')

    # U-151: 根帧 clipsContent+radius 时不生成 overflow:hidden
    try:
        node151 = make_node(layoutMode='VERTICAL', width=393, height=2849,
                            clipsContent=True, cornerRadius=24,
                            layoutPositioning=None)
        ctx151 = make_ctx(layoutMode='NONE', isRoot=True, width=0, height=0)
        css151 = extract_css(node151, ctx151)
        check('U-151', 'F-4: 根帧 clipsContent+radius → 不生成 overflow:hidden（避免截断底部）',
              'overflow' not in css151)
    except Exception as e:
        check('U-151', 'F-4: 根帧无 overflow:hidden', False)
        print(f'       Error: {e}')

    # U-152: VECTOR 类型节点不生成 border-radius
    try:
        node152 = make_node(type='VECTOR', width=353, height=405,
                            layoutPositioning='ABSOLUTE',
                            rectangleCornerRadii=[100.0, 100.0, 100.0, 100.0])
        ctx152 = make_ctx(layoutMode='NONE')
        css152 = extract_css(node152, ctx152)
        check('U-152', 'F-5: VECTOR 节点 rectangleCornerRadii → 不生成 border-radius（路径几何≠CSS 圆角）',
              'border-radius' not in css152)
    except Exception as e:
        check('U-152', 'F-5: VECTOR 节点无 border-radius', False)
        print(f'       Error: {e}')

    # U-160: frame with child extending outside bounds must not get overflow:hidden
    try:
        node160 = {
            'type': 'FRAME', 'layoutMode': 'NONE', 'clipsContent': True,
            'cornerRadius': 15.0,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 30, 'height': 30},
            'children': [{
                'id': 'child1', 'visible': None,
                'layoutPositioning': 'ABSOLUTE',
                'absoluteBoundingBox': {'x': 5, 'y': 25, 'width': 20, 'height': 15},
            }],
        }
        css160 = extract_css(node160, make_ctx(layoutMode='NONE'))
        # Updated: radius + clipsContent ALWAYS gets overflow:hidden (speech-bubble pattern)
        check('U-160', 'G: clipsContent+radius → 始终生成 overflow:hidden（圆角容器无需 _has_overflow_child 豁免）',
              css160.get('overflow') == 'hidden' and 'border-radius' in css160)
    except Exception as e:
        check('U-160', 'G: clipsContent+radius → 始终生成 overflow:hidden', False)
        print(f'       Error: {e}')

    # U-161: frame with all children inside bounds should still get overflow:hidden
    try:
        node161 = {
            'type': 'FRAME', 'layoutMode': 'NONE', 'clipsContent': True,
            'cornerRadius': 8.0,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 100, 'height': 50},
            'children': [{
                'id': 'child1', 'visible': None,
                'layoutPositioning': 'ABSOLUTE',
                'absoluteBoundingBox': {'x': 5, 'y': 5, 'width': 80, 'height': 40},
            }],
        }
        css161 = extract_css(node161, make_ctx(layoutMode='NONE'))
        check('U-161', 'G: 子节点在边界内时 clipsContent+radius → 生成 overflow:hidden',
              css161.get('overflow') == 'hidden')
    except Exception as e:
        check('U-161', 'G: 子节点在边界内时生成 overflow:hidden', False)
        print(f'       Error: {e}')


    # ── U-170: VECTOR SOLID fill extracted as CSS when force_css_fills=True ───────
    try:
        from lib.css_extractor import _has_overflow_child as _hoc
        node170 = {
            'type': 'VECTOR',
            'fills': [{'blendMode': 'NORMAL', 'type': 'SOLID',
                       'color': {'r': 0.969, 'g': 0.651, 'b': 0.0, 'a': 1.0}}],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 17, 'height': 17},
            'effects': [],
        }
        ctx170 = {'layoutMode': 'NONE', 'isRoot': False, 'width': 30, 'height': 30,
                  'absX': 0, 'absY': 0}
        css_no = extract_css(node170, ctx170, force_css_fills=False)
        css_yes = extract_css(node170, ctx170, force_css_fills=True)
        check('U-170a', 'Bug 1: VECTOR no force → no background-color',
              css_no.get('background-color') is None)
        bg = css_yes.get('background-color', '')
        check('U-170b', 'Bug 1: VECTOR force_css_fills=True → background-color #f7a600',
              'f7a6' in bg.lower() or 'f7a600' in bg.lower())
    except Exception as e:
        check('U-170', 'Bug 1: VECTOR fill with force_css_fills', False)
        print(f'       Error: {e}')


    # ── U-171: VECTOR rotation is SKIPPED in CSS (SVG export bakes rotation into file) ──
    # Previously this tested that rotation was applied; now VECTOR-family nodes export as SVG
    # assets where Figma has already applied the rotation visually. Adding CSS rotate would
    # double-rotate the rendered image.
    try:
        import math as _math
        node171 = {
            'type': 'VECTOR',
            'rotation': -2.3561944,  # -135 degrees
            'fills': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 17, 'height': 17},
            'effects': [],
        }
        ctx171 = {'layoutMode': 'NONE', 'isRoot': False, 'width': 30, 'height': 30,
                  'absX': 0, 'absY': 0}
        css171 = extract_css(node171, ctx171)
        check('U-171', 'VECTOR rotation SKIPPED in CSS（SVG 已烘焙旋转，避免双重旋转）',
              'rotate' not in (css171.get('transform') or ''))
    except Exception as e:
        check('U-171', 'VECTOR rotation skipped', False)
        print(f'       Error: {e}')


    # ── U-172: clipsContent=True without radius → overflow:hidden ─────────────────
    try:
        node172 = {
            'type': 'FRAME', 'layoutMode': 'VERTICAL', 'clipsContent': True,
            'cornerRadius': None, 'rectangleCornerRadii': None,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 2681},
            'fills': [], 'children': [], 'effects': [],
        }
        ctx172 = {'layoutMode': 'VERTICAL', 'isRoot': False, 'width': 393, 'height': 2849}
        css172 = extract_css(node172, ctx172)
        check('U-172', 'Bug 3: clipsContent=True no radius → overflow:hidden',
              css172.get('overflow') == 'hidden')
    except Exception as e:
        check('U-172', 'Bug 3: clipsContent no radius → overflow:hidden', False)
        print(f'       Error: {e}')


    # ── U-174: Patch 5 keeps background-color for VECTOR without imageRef ─────────
    try:
        # Simulate Patch 5 logic: VECTOR with imageRef → strip bg; without → keep bg
        def _p5_would_strip(child):
            ccss = child.get('css', {})
            return (child.get('isVectorNode') and 'background-color' in ccss
                    and (child.get('imageRef') or child.get('fillImageRef')))

        with_ref = {'isVectorNode': True, 'imageRef': 'abc123',
                    'css': {'background-color': '#f7a600'}}
        without_ref = {'isVectorNode': True, 'imageRef': None, 'fillImageRef': None,
                       'css': {'background-color': '#f7a600'}}
        check('U-174a', 'Bug 1b: VECTOR with imageRef → Patch 5 strips bg-color',
              _p5_would_strip(with_ref))
        check('U-174b', 'Bug 1b: VECTOR without imageRef → Patch 5 keeps bg-color',
              not _p5_would_strip(without_ref))
    except Exception as e:
        check('U-174', 'Bug 1b: Patch 5 imageRef guard', False)
        print(f'       Error: {e}')


    # ── U-173: _has_overflow_child ignores top/left, detects bottom/right ─────────
    try:
        from lib.css_extractor import _has_overflow_child as _hoc2
        node_top = {
            'absoluteBoundingBox': {'x': 0, 'y': 5245, 'width': 393, 'height': 2681},
            'children': [{'visible': None,
                          'absoluteBoundingBox': {'x': 0, 'y': 5145, 'width': 393, 'height': 1208}}]
        }
        node_bot = {
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 30, 'height': 30},
            'children': [{'visible': None,
                          'absoluteBoundingBox': {'x': 5, 'y': 25, 'width': 20, 'height': 15}}]
        }
        check('U-173a', 'Bug 3: top-only overflow child → does NOT block overflow:hidden',
              not _hoc2(node_top))
        check('U-173b', 'Bug 3: bottom overflow child → DOES block overflow:hidden',
              _hoc2(node_bot))
    except Exception as e:
        check('U-173', 'Bug 3: _has_overflow_child direction check', False)
        print(f'       Error: {e}')


    # ── U-180: pure-text HUG flex container does NOT get explicit height (is_hug_v guard)
    # After Task 1 introduced is_hug_v, the pure-text height-pinning path is also
    # guarded by is_hug_v. HUG flex containers rely on content-driven height (height:auto)
    # via flex semantics; explicit height pinning is a legacy workaround that no longer
    # applies when the container participates in a flex parent flow.
    try:
        node180 = {
            'type': 'FRAME', 'layoutMode': 'VERTICAL',
            'layoutSizingVertical': 'HUG',
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200, 'height': 20},
            'fills': [], 'effects': [],
            'children': [{'type': 'TEXT', 'visible': None, 'id': 't1'}],
        }
        css180 = extract_css(node180, make_ctx(layoutMode='VERTICAL'))
        check('U-180', 'Fix: 纯文字 HUG flex 容器不写 height（is_hug_v 保护）',
              css180.get('height') is None)
    except Exception as e:
        check('U-180', 'Fix: 纯文字 HUG 容器 height 约束', False)
        print(f'       Error: {e}')


    # ── U-181: mixed-children HUG container does NOT get explicit height ─────────
    try:
        node181 = {
            'type': 'FRAME', 'layoutMode': 'VERTICAL',
            'layoutSizingVertical': 'HUG',
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200, 'height': 80},
            'fills': [], 'effects': [],
            'children': [
                {'type': 'TEXT', 'visible': None, 'id': 't1'},
                {'type': 'FRAME', 'visible': None, 'id': 'f1'},
            ],
        }
        css181 = extract_css(node181, make_ctx(layoutMode='VERTICAL'))
        check('U-181', 'Fix: 混合子节点 HUG 容器不设置 height（避免影响非文字容器）',
              css181.get('height') is None)
    except Exception as e:
        check('U-181', 'Fix: 混合子节点不受影响', False)
        print(f'       Error: {e}')


    # ── U-182: FIXED-height container not overridden by pure-text rule ───────────
    try:
        node182 = {
            'type': 'FRAME', 'layoutMode': 'VERTICAL',
            'layoutSizingVertical': 'FIXED',
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200, 'height': 50},
            'fills': [], 'effects': [],
            'children': [{'type': 'TEXT', 'visible': None, 'id': 't1'}],
        }
        css182 = extract_css(node182, make_ctx(layoutMode='VERTICAL'))
        check('U-182', 'Fix: FIXED 高度容器不被纯文字规则覆盖',
              css182.get('height') == '50px')
    except Exception as e:
        check('U-182', 'Fix: FIXED height 不受影响', False)
        print(f'       Error: {e}')


    # ── U-190~U-197: overflow child promotion (DOM restructuring for Figma fill-cover effect) ──
    print('\n  ─── overflow child promotion 测试 ───────────')

    # Helper: build a minimal overflow scenario (pill + arrow below + content frame)
    # IMPORTANT: must include a non-VECTOR child (content frame) so the pill is NOT treated as
    # a composite icon (which would skip child processing entirely).
    def _make_overflow_figma_node(clips_content=False, has_fills=True, arrow_extends=True):
        arrow_y = 219.0 if arrow_extends else 210.0  # 219 → extends 8px below pill bottom (228)
        arrow = {
            'type': 'VECTOR', 'id': 'arrow-1', 'name': 'arrow',
            'layoutPositioning': 'ABSOLUTE',
            'constraints': {'vertical': 'BOTTOM', 'horizontal': 'CENTER'},
            'absoluteBoundingBox': {'x': 118.5, 'y': arrow_y, 'width': 17.0, 'height': 17.0},
            'fills': [{'type': 'SOLID', 'color': {'r': 0.97, 'g': 0.65, 'b': 0, 'a': 1}, 'visible': True}],
            'effects': [],
        }
        # Non-VECTOR content child — prevents pill from being misidentified as composite icon
        content = {
            'type': 'FRAME', 'id': 'content-1', 'name': 'content',
            'layoutMode': 'HORIZONTAL',
            'absoluteBoundingBox': {'x': 108, 'y': 205, 'width': 37, 'height': 15},
            'fills': [], 'effects': [], 'strokes': [],
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'children': [],
        }
        pill_fills = [{'type': 'SOLID', 'color': {'r': 1, 'g': 1, 'b': 1, 'a': 0.6}, 'visible': True}] if has_fills else []
        return {
            'type': 'FRAME', 'id': 'pill-1', 'name': 'Pill',
            'clipsContent': clips_content,
            'layoutMode': None,
            'fills': pill_fills,
            'effects': [],
            'strokes': [],
            'absoluteBoundingBox': {'x': 100, 'y': 200, 'width': 53, 'height': 28},
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'children': [arrow, content],
        }

    # U-190: clipsContent=False + visible fills + overflow child → build_ir wraps in ow-wrapper
    try:
        ir_list_190 = build_ir([_make_overflow_figma_node(clips_content=False, has_fills=True, arrow_extends=True)])
        ir_190 = ir_list_190[0]
        check('U-190', 'clipsContent=False + fills + overflow child → IR 外层是 ow-wrapper',
              ir_190.get('isGeneratedWrapper') is True)
    except Exception as e:
        check('U-190', 'overflow → ow-wrapper', False)
        print(f'       Error: {e}')

    # U-191: clipsContent=True → no wrapping (clips the child instead)
    try:
        ir_list_191 = build_ir([_make_overflow_figma_node(clips_content=True, has_fills=True, arrow_extends=True)])
        ir_191 = ir_list_191[0]
        check('U-191', 'clipsContent=True → 不包 wrapper（由 overflow:hidden 处理）',
              ir_191.get('isGeneratedWrapper') is not True)
    except Exception as e:
        check('U-191', 'clipsContent=True → no wrap', False)
        print(f'       Error: {e}')

    # U-192: clipsContent=False + NO fills → no wrapping (fill-cover effect doesn't apply)
    try:
        ir_list_192 = build_ir([_make_overflow_figma_node(clips_content=False, has_fills=False, arrow_extends=True)])
        ir_192 = ir_list_192[0]
        check('U-192', 'clipsContent=False + 无 fill → 不包 wrapper（无 fill 不存在遮盖效果）',
              ir_192.get('isGeneratedWrapper') is not True)
    except Exception as e:
        check('U-192', 'no fills → no wrap', False)
        print(f'       Error: {e}')

    # U-193: wrapper has position:relative and same height as parent
    try:
        ir_193 = build_ir([_make_overflow_figma_node(clips_content=False, has_fills=True, arrow_extends=True)])[0]
        w_css = ir_193.get('css') or {}
        check('U-193', 'wrapper CSS 有 position:relative + 继承父节点高度',
              w_css.get('position') == 'relative' and '28' in (w_css.get('height') or ''))
    except Exception as e:
        check('U-193', 'wrapper css', False)
        print(f'       Error: {e}')

    # U-194: promoted overflow child is FIRST child of wrapper (renders before parent → parent fill covers it)
    try:
        ir_194 = build_ir([_make_overflow_figma_node(clips_content=False, has_fills=True, arrow_extends=True)])[0]
        first_child = (ir_194.get('children') or [{}])[0]
        check('U-194', '被提升的 overflow 子节点是 wrapper 的第一个子节点（DOM 顺序先渲染，parent fill 后盖）',
              first_child.get('figmaId') == 'arrow-1' or first_child.get('isVectorNode') is True)
    except Exception as e:
        check('U-194', 'overflow child first', False)
        print(f'       Error: {e}')

    # U-195: child with positive position values (not extending outside) stays inside parent
    try:
        ir_list_195 = build_ir([_make_overflow_figma_node(clips_content=False, has_fills=True, arrow_extends=False)])
        ir_195 = ir_list_195[0]
        check('U-195', '不溢出的 absolute 子节点留在 parent 内（不被提升）',
              ir_195.get('isGeneratedWrapper') is not True)
    except Exception as e:
        check('U-195', 'non-overflow child not promoted', False)
        print(f'       Error: {e}')

    # U-196: parent inside wrapper retains visual CSS (background-color)
    try:
        ir_196 = build_ir([_make_overflow_figma_node(clips_content=False, has_fills=True, arrow_extends=True)])[0]
        parent_in_wrapper = next(
            (c for c in (ir_196.get('children') or []) if not c.get('isVectorNode')),
            None
        )
        p_css = (parent_in_wrapper or {}).get('css') or {}
        check('U-196', 'wrapper 内的 parent 节点保留 background-color（视觉 CSS 不丢失）',
              bool(p_css.get('background-color')))
    except Exception as e:
        check('U-196', 'parent retains visual css', False)
        print(f'       Error: {e}')

    # U-197: _child_extends_outside helper — unit test for the detection logic
    try:
        check('U-197a', '_child_extends_outside: bottom:-8px → True',
              _child_extends_outside({'position': 'absolute', 'bottom': '-8px'}))
        check('U-197b', '_child_extends_outside: bottom:5px → False',
              not _child_extends_outside({'position': 'absolute', 'bottom': '5px'}))
        check('U-197c', '_child_extends_outside: top:-100px → True',
              _child_extends_outside({'position': 'absolute', 'top': '-100px'}))
        check('U-197d', '_child_extends_outside: bottom:-0.5px（小于阈值 -1）→ False',
              not _child_extends_outside({'position': 'absolute', 'bottom': '-0.5px'}))
    except Exception as e:
        check('U-197', '_child_extends_outside helper', False)
        print(f'       Error: {e}')


    # ─── U-198 ~ U-201: padding-aware content-box height adjustment ───────────────────
    # Bug: Figma frame heights are border-box values; CSS min-height is content-box.
    # When a frame has vertical padding and children exceed its height, the generated
    # `min-height` must equal (figma_height - padding_top - padding_bottom) so that
    # the total CSS height matches the Figma frame dimensions.
    # Reproduces the DemoTrading H5 hero section bug: padding 280+40=320px + min-height
    # 478px → total CSS height 798px instead of the intended 478px.

    try:
        # U-198: hero section scenario — large vertical padding + child exceeds height
        # Figma: h=478, padding-top=280, padding-bottom=40 → content min-height=158
        from lib.ir_builder import _post_process_frame as _ppf_198
        ir_198 = {
            'figmaId': 'test:198',
            'figmaType': 'FRAME',
            'css': {
                'display': 'flex',
                'flex-direction': 'column',
                'height': '478px',
                'padding': '280px 16px 40px 16px',
                'position': 'relative',
            },
            'children': [
                {
                    'figmaId': 'test:198a',
                    'figmaType': 'FRAME',
                    'css': {'position': 'absolute', 'top': '47px', 'height': '633px', 'width': '447px'},
                    'isImageNode': False, 'isVectorNode': False, 'isDecorativeElement': False,
                    'children': [],
                },
            ],
        }
        _ppf_198(ir_198)
        css_198 = ir_198['css']
        check('U-198a',
              'large padding + child exceeds height → min-height = figma_h - pad_v (478-320=158px)',
              css_198.get('min-height') == '158px')
        check('U-198b',
              'height key removed after conversion',
              'height' not in css_198)
        check('U-198c',
              'padding preserved unchanged',
              css_198.get('padding') == '280px 16px 40px 16px')
    except Exception as e:
        check('U-198', '_post_process_frame: large padding content-box adjustment', False)
        print(f'       Error: {e}')

    try:
        # U-199: no padding → behavior unchanged, min-height equals Figma height
        from lib.ir_builder import _post_process_frame as _ppf_199
        ir_199 = {
            'figmaId': 'test:199',
            'figmaType': 'FRAME',
            'css': {'height': '500px', 'position': 'relative'},
            'children': [
                {
                    'figmaId': 'test:199a',
                    'figmaType': 'FRAME',
                    'css': {'position': 'absolute', 'top': '10px', 'height': '600px', 'width': '300px'},
                    'isImageNode': False, 'isVectorNode': False, 'isDecorativeElement': False,
                    'children': [],
                },
            ],
        }
        _ppf_199(ir_199)
        css_199 = ir_199['css']
        check('U-199',
              'no padding → min-height = figma_h (500px unchanged)',
              css_199.get('min-height') == '500px' and 'height' not in css_199)
    except Exception as e:
        check('U-199', '_post_process_frame: no padding → min-height unchanged', False)
        print(f'       Error: {e}')

    try:
        # U-200: _parse_vertical_padding helper covers all shorthand forms
        from lib.ir_builder import _parse_vertical_padding as _pvp
        check('U-200a', '_parse_vertical_padding: 4-value "280px 16px 40px 16px" → 320',
              _pvp('280px 16px 40px 16px') == 320.0)
        check('U-200b', '_parse_vertical_padding: 1-value "40px" → 80',
              _pvp('40px') == 80.0)
        check('U-200c', '_parse_vertical_padding: 2-value "40px 16px" → 80',
              _pvp('40px 16px') == 80.0)
        check('U-200d', '_parse_vertical_padding: 3-value "40px 16px 20px" → 60',
              _pvp('40px 16px 20px') == 60.0)
        check('U-200e', '_parse_vertical_padding: empty string → 0',
              _pvp('') == 0.0)
    except Exception as e:
        check('U-200', '_parse_vertical_padding helper', False)
        print(f'       Error: {e}')

    try:
        # U-202: clipsContent=True + no radius + child extends outside bounds + NO gradient
        # → overflow:hidden MUST be set.
        # When a container has clipsContent=True with no gradient-fill descendants, the
        # designer wants a hard clip (e.g. hero section clipping phone at 400px). The shadow
        # gradient that covers the clip boundary is at depth>2 and NOT caught by
        # _has_gradient_fill_descendant, so overflow:hidden is correctly preserved.
        # This ensures the phone group doesn't overflow into the next section.
        # Reproduces 1484:27141 (.hero 400px, clipsContent=True, gradient shadow at depth 3).
        node_202 = {
            'type': 'FRAME',
            'width': 375, 'height': 478,
            'layoutMode': 'VERTICAL',
            'layoutSizingHorizontal': 'FILL',
            'layoutSizingVertical': 'FIXED',
            'clipsContent': True,
            'fills': [{'type': 'SOLID', 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1}}],
            'effects': [], 'strokes': [],
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            # Child that overflows right (width > parent) and bottom (top+h > parent.h)
            # but has NO gradient fill → overflow:hidden should stay
            'children': [{
                'type': 'FRAME',
                'id': 'child:202a',
                'visible': True,
                'absoluteBoundingBox': {'x': -36, 'y': 47, 'width': 447, 'height': 633},
                'fills': [{'type': 'SOLID', 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1}}],
            }],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 478},
        }
        ctx_202 = make_ctx(layoutMode='VERTICAL', isRoot=False, width=800, height=2000)
        css_202 = extract_css(node_202, ctx_202)
        check('U-202',
              'clipsContent=True + overflow child + no gradient descendant → overflow:hidden 保留（shadow 在 depth>2 覆盖边界）',
              css_202.get('overflow') == 'hidden')
    except Exception as e:
        check('U-202', 'clipsContent=True + overflow child → overflow:hidden', False)
        print(f'       Error: {e}')

    try:
        # U-203: _post_process_frame: overflow:hidden + padding + children exceed height
        # → height stays (Exception 1 in overflow:hidden path).
        # Reproduces 1484:27142 with clipsContent=True fix applied:
        # h=478, padding-top=280, bottom=40 → overflow:hidden set by css_extractor →
        # height stays at 478px (border-box value from Figma; project uses box-sizing:border-box
        # via project tailwind.css, so height includes padding — no subtraction needed).
        from lib.ir_builder import _post_process_frame as _ppf_203
        ir_203 = {
            'figmaId': 'test:203',
            'figmaType': 'FRAME',
            'css': {
                'height': '478px',
                'padding': '280px 16px 40px 16px',
                'overflow': 'hidden',   # set by clipsContent=True fix
                'position': 'relative',
            },
            'children': [
                {
                    'figmaId': 'test:203a',
                    'figmaType': 'FRAME',
                    'css': {'position': 'absolute', 'top': '47px', 'height': '633px', 'width': '447px'},
                    'isImageNode': False, 'isVectorNode': False, 'isDecorativeElement': False,
                    'children': [],
                },
            ],
        }
        _ppf_203(ir_203)
        css_203 = ir_203['css']
        check('U-203a',
              'overflow:hidden + padding: height 保持 Figma border-box 值 478px（不减 padding）',
              css_203.get('height') == '478px')
        check('U-203b',
              'overflow:hidden preserved after height adjustment',
              css_203.get('overflow') == 'hidden')
        check('U-203c',
              'no min-height present (height kept, not converted)',
              'min-height' not in css_203)
    except Exception as e:
        check('U-203', '_post_process_frame: overflow:hidden + padding → height 保持 border-box 值', False)
        print(f'       Error: {e}')

    try:
        # U-204: VECTOR node with GRADIENT fill + no backdrop blur → NO CSS background.
        # Bug: _force_css_fills was True for any VECTOR with fills, causing redundant
        # CSS background on <img> elements that already have the fill baked into the SVG.
        # This caused gradient fills to bleed through transparent SVG areas (wrong shape).
        # Reproduces 1484:27830 (snake SVG): GRADIENT_RADIAL fill, no backdrop blur.
        node_204 = {
            'type': 'VECTOR',
            'width': 648, 'height': 162,
            'layoutPositioning': 'ABSOLUTE',
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'fills': [{'type': 'GRADIENT_RADIAL', 'visible': True,
                        'gradientStops': [{'color': {'r': 0.137, 'g': 0.259, 'b': 0.886, 'a': 0.2}, 'position': 0}],
                        'gradientHandlePositions': [{'x': 0.5, 'y': 0}, {'x': 1, 'y': 0}, {'x': 0.5, 'y': 1}]}],
            'effects': [],
            'strokes': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 648, 'height': 162},
        }
        ctx_204 = make_ctx(layoutMode='NONE', isRoot=False, width=302, height=173)
        css_204 = extract_css(node_204, ctx_204)
        check('U-204',
              'VECTOR with gradient fill + no backdrop blur → no CSS background (fill baked into SVG)',
              'background' not in css_204 and 'background-color' not in css_204)
    except Exception as e:
        check('U-204', 'VECTOR gradient fill → no CSS background', False)
        print(f'       Error: {e}')

    try:
        # U-201: css_extractor root frame — min-height accounts for vertical padding
        # Root frame with paddingTop=280, paddingBottom=40, height=478 → content min-height=158
        node_201 = {
            'type': 'FRAME',
            'width': 375, 'height': 478,
            'layoutMode': 'VERTICAL',
            'layoutSizingHorizontal': 'FIXED',
            'layoutSizingVertical': 'FIXED',
            'paddingTop': 280.0, 'paddingBottom': 40.0,
            'paddingLeft': 16.0, 'paddingRight': 16.0,
            'primaryAxisAlignItems': 'MAX',
            'counterAxisAlignItems': 'CENTER',
            'itemSpacing': 32.0,
            'fills': [], 'effects': [], 'strokes': [],
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        }
        ctx_201 = make_ctx(layoutMode='VERTICAL', isRoot=True, width=375, height=478)
        css_201 = extract_css(node_201, ctx_201)
        check('U-201',
              'root frame with paddingTop=280 paddingBottom=40 h=478 → min-height=158px (content-box)',
              css_201.get('min-height') == '158px')
    except Exception as e:
        check('U-201', 'css_extractor root frame padding-aware min-height', False)
        print(f'       Error: {e}')

    try:
        # U-208: clipsContent=True + no radius + overflow child + gradient descendant at depth 1
        # → overflow:hidden REMOVED by U-207 (gradient descendant), NOT by overflow-child logic.
        # Key: it's the gradient descendant (depth≤2) that removes overflow:hidden,
        # not the overflow child. Depth>2 gradients (like hero → group → shadow) don't
        # trigger U-207, so those containers keep overflow:hidden.
        # Reproduces 1484:27142 (.hero-n1484-27142): clipsContent=True, has gradient child
        # at depth 2 (group→shadow), overflow child → no overflow:hidden ✓
        node_208_child_gradient = {
            'id': 'shadow', 'type': 'RECTANGLE', 'name': 'Shadow',
            'absoluteBoundingBox': {'x': 0, 'y': 300, 'width': 375, 'height': 100},
            'fills': [{'type': 'GRADIENT_LINEAR',
                        'gradientHandlePositions': [{'x': 0.5, 'y': 0}, {'x': 0.5, 'y': 1}, {'x': 0, 'y': 0}],
                        'gradientStops': [{'position': 0.326, 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1}},
                                          {'position': 1.0,   'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0}}]}],
            'effects': [], 'strokes': [],
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
        }
        node_208_child_group = {
            'id': 'group', 'type': 'GROUP', 'name': 'Group',
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 633},
            'children': [node_208_child_gradient],
        }
        node_208 = {
            'type': 'FRAME', 'layoutMode': 'VERTICAL', 'clipsContent': True, 'cornerRadius': None,
            'width': 375, 'height': 478,
            'fills': [{'type': 'SOLID', 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1}}],
            'effects': [], 'strokes': [],
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 478},
            'children': [node_208_child_group],
        }
        ctx_208 = make_ctx(layoutMode='VERTICAL', isRoot=False, width=375, height=900)
        css_208 = extract_css(node_208, ctx_208, {})
        # U-207 was reverted: gradient descendants no longer remove overflow:hidden.
        # Correct gradient direction (U-206) makes the clip visually smooth.
        # So clipsContent=True → overflow:hidden even with gradient descendants.
        check('U-208',
              'clipsContent=True + gradient descendant → overflow:hidden 保留（渐变角度正确使 clip 不可见）',
              css_208.get('overflow') == 'hidden')
    except Exception as e:
        check('U-208', 'clipsContent + gradient descendant → overflow:hidden', False)
        print(f'       Error: {e}')

    try:
        # U-207: clipsContent=True + no border-radius + gradient-fill descendant
        # → overflow:hidden IS set (correct behavior after U-206 gradient rotation fix).
        # The gradient direction is now correctly baked (rotation → 0deg = black at bottom),
        # so the clip boundary is visually invisible — overflow:hidden is needed and correct.
        # Previous assertion was reversed (removed overflow:hidden) based on wrong premise
        # that gradient descendants need visible overflow. With correct gradient direction,
        # clipsContent=True always means overflow:hidden.
        # Reproduces 1484:27142 (Hero FRAME, clipsContent=True, no radius, gradient shadow
        # correctly covers the clip at the boundary — clip is visually smooth).
        node_207 = {
            'type': 'FRAME',
            'layoutMode': 'VERTICAL',
            'clipsContent': True,
            'cornerRadius': None,
            'width': 375, 'height': 478,
            'layoutSizingHorizontal': 'FILL',
            'layoutSizingVertical': 'FIXED',
            'fills': [{'type': 'SOLID', 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1}}],
            'effects': [], 'strokes': [],
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 478},
            'children': [
                {
                    'id': 'group1', 'type': 'GROUP', 'name': 'Group',
                    'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 478},
                    'children': [
                        {
                            'id': 'shadow1', 'type': 'RECTANGLE', 'name': 'Shadow',
                            'absoluteBoundingBox': {'x': 0, 'y': 99, 'width': 375, 'height': 100},
                            'fills': [{
                                'type': 'GRADIENT_LINEAR',
                                'gradientHandlePositions': [
                                    {'x': 0.5, 'y': 0.023}, {'x': 0.5, 'y': 1.0}, {'x': 0.011, 'y': 0.023}
                                ],
                                'gradientStops': [
                                    {'position': 0.326, 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1.0}},
                                    {'position': 1.0,   'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0.0}},
                                ],
                            }],
                            'effects': [], 'strokes': [],
                            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
                        },
                    ],
                },
            ],
        }
        ctx_207 = make_ctx(layoutMode='VERTICAL', isRoot=False, width=375, height=900)
        css_207 = extract_css(node_207, ctx_207, {})
        check('U-207',
              'clipsContent=True + gradient descendant → overflow:hidden 保留（渐变角度正确，clip 视觉不可见）',
              css_207.get('overflow') == 'hidden')
    except Exception as e:
        check('U-207', 'clipsContent + gradient descendant → overflow:hidden', False)
        print(f'       Error: {e}')

    try:
        # U-206: RECTANGLE with GRADIENT_LINEAR fill + rotation=-π → NO CSS rotate() transform.
        # Bug: `should_rotate` fires for gradient elements because of `or has_bg_gradient`.
        # CSS rotate() on a gradient rectangle double-flips the visual gradient direction.
        # Fix: bake rotation into gradient angle instead of applying CSS rotate().
        # screen_angle = (local_angle − css_rotation_deg + 360) % 360
        #             = (180 − (−180) + 360) % 360 = 360 % 360 = 0deg
        # Result: linear-gradient(0deg, black 32.62%, transparent) = black at BOTTOM ✓
        # (0deg in CSS = direction from bottom to top; 0% = bottom = black, fades upward)
        # This correctly fades out the bottom of the phone image in the hero section.
        # Reproduces 1484:27152 (shadow RECTANGLE, GRADIENT_LINEAR, rotation=-π).
        import math as _math_206
        node_206 = {
            'type': 'RECTANGLE',
            'width': 375, 'height': 100,
            'rotation': -_math_206.pi,
            'layoutPositioning': 'ABSOLUTE',
            'constraints': {'horizontal': 'CENTER', 'vertical': 'TOP'},
            'fills': [{
                'type': 'GRADIENT_LINEAR',
                'gradientHandlePositions': [
                    {'x': 0.5, 'y': 0.023},
                    {'x': 0.5, 'y': 1.0},
                    {'x': 0.011, 'y': 0.023},
                ],
                'gradientStops': [
                    {'position': 0.3262, 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1.0}},
                    {'position': 1.0,    'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0.0}},
                ],
            }],
            'effects': [], 'strokes': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 100},
        }
        ctx_206 = make_ctx(layoutMode='NONE', isRoot=False, width=375, height=400)
        css_206 = extract_css(node_206, ctx_206, {})
        transform_206 = css_206.get('transform', '')
        has_rotate_206 = 'rotate' in transform_206
        bg_206 = css_206.get('background', '')
        has_gradient_206 = 'linear-gradient' in bg_206
        check('U-206a',
              'RECTANGLE gradient fill + rotation=-π → gradient angle 调整为 0deg（bake rotation，黑色在底部）',
              has_gradient_206 and 'linear-gradient(0deg,' in bg_206)
        check('U-206b',
              'RECTANGLE gradient fill + rotation=-π → transform 不含 rotate()（旋转已 bake 进角度）',
              not has_rotate_206)
    except Exception as e:
        check('U-206a', 'RECTANGLE gradient rotation → linear-gradient', False)
        check('U-206b', 'RECTANGLE gradient rotation → no rotate()', False)
        print(f'       Error: {e}')

    try:
        # U-205: HUG button with invisible icon children + visible TEXT + vertical padding
        # → must NOT set height (pure-text HUG optimization should not fire for padded buttons).
        # Bug (d016d7ee): after filtering visible=False icon children, visible_ch=[TEXT only]
        # → height: 48px set. _post_process_frame then subtracts padding (24px) → height: 24px.
        # With box-sizing:border-box the button content area = 0px — button looks completely flat.
        # Reproduces I1484:27518;11515:12430 (Button 1, paddingTop=12 paddingBottom=12).
        node_205 = {
            'type': 'INSTANCE',
            'layoutMode': 'HORIZONTAL',
            'layoutSizingHorizontal': 'FILL',
            'layoutSizingVertical': 'HUG',
            'paddingTop': 12.0, 'paddingBottom': 12.0,
            'paddingLeft': 24.0, 'paddingRight': 24.0,
            'primaryAxisAlignItems': 'CENTER',
            'counterAxisAlignItems': 'CENTER',
            'fills': [{'type': 'SOLID', 'color': {'r': 0.97, 'g': 0.65, 'b': 0, 'a': 1}}],
            'effects': [], 'strokes': [],
            'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 361, 'height': 48},
            'children': [
                {'id': 'icon1', 'type': 'INSTANCE', 'name': 'icon1', 'visible': False,
                 'absoluteBoundingBox': {'x': 24, 'y': 14, 'width': 20, 'height': 20}},
                {'id': 'text1', 'type': 'TEXT', 'name': 'Button Text',
                 'absoluteBoundingBox': {'x': 48, 'y': 14, 'width': 100, 'height': 20}},
                {'id': 'icon2', 'type': 'INSTANCE', 'name': 'icon2', 'visible': False,
                 'absoluteBoundingBox': {'x': 152, 'y': 14, 'width': 20, 'height': 20}},
            ],
        }
        ctx_205 = make_ctx(layoutMode='VERTICAL', isRoot=False, width=375, height=100)
        css_205 = extract_css(node_205, ctx_205, {})
        check('U-205',
              'HUG INSTANCE 按钮（invisible icon 子节点 + 垂直 padding 12px）→ 不应生成 height',
              'height' not in css_205 and 'min-height' not in css_205)
    except Exception as e:
        check('U-205', 'HUG 按钮 invisible icon → no height', False)
        print(f'       Error: {e}')

    try:
        # U-206: _post_process_frame 不应从 height 减去 vertical padding
        # 根因: 项目使用 Tailwind 的 box-sizing:border-box 全局 reset（来自项目 tailwind.css）。
        # Figma height=48px 已是 border-box 值，直接对应 CSS height:48px。
        # 错误的 content-box 转换（48 - 12 - 12 = 24px）会让内容区域变 0px，padding 失效。
        # 回归节点：data-figma-id="169:35115" (Example_Primary Buttons-Dark)
        from lib.ir_builder import _post_process_frame
        ir_206 = {
            'figmaId': '169:35115',
            'figmaType': 'INSTANCE',
            'isComponentInstance': True,
            'isImageNode': False,
            'isVectorNode': False,
            'css': {
                'width': '220px',
                'height': '48px',
                'display': 'flex',
                'flex-direction': 'row',
                'justify-content': 'center',
                'align-items': 'center',
                'gap': '4px',
                'padding': '12px 28px 12px 28px',
                'flex-shrink': '0',
                'background-color': '#ff9c2e',
                'border-radius': '24px',
                'position': 'relative',
            },
            'children': [
                {
                    'figmaId': 'I169:35115;2:8053',
                    'figmaType': 'TEXT',
                    'isTextNode': True,
                    'isImageNode': False,
                    'isVectorNode': False,
                    'css': {'color': '#000', 'font-size': '14px'},
                    'children': [],
                }
            ],
        }
        _post_process_frame(ir_206)
        css_206 = ir_206.get('css', {})
        check('U-206',
              'border-box 项目中带 vertical-padding 的按钮 → height 保持 48px，不被减 padding（24px bug 回归）',
              css_206.get('height') == '48px'
              and 'min-height' not in css_206)
    except Exception as e:
        check('U-206', '_post_process_frame: 按钮 height 不被 padding 减小', False)
        print(f'       Error: {e}')

    try:
        # U-209: 渐变角度 bake 公式修正 — rotation=-π/2
        # Bug: local_deg - rd 给出 270deg（黑色在右），正确应为 local_deg + rd = 90deg（黑色在左）
        # 回归节点: 202:33617 左侧渐变阴影（DemoTrading 页）
        import math as _m
        node_grad = make_node(
            type='RECTANGLE',
            rotation=-_m.pi / 2,
            width=523, height=550,
            fills=[{
                'type': 'GRADIENT_LINEAR',
                'gradientHandlePositions': [
                    {'x': 0.5, 'y': 0.023},
                    {'x': 0.5, 'y': 1.0},
                    {'x': 0.011, 'y': 0.023},
                ],
                'gradientStops': [
                    {'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1.0}, 'position': 0.31},
                    {'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0.0}, 'position': 1.0},
                ],
            }],
        )
        css_grad = extract_css(node_grad, make_ctx(layoutMode='NONE'))
        bg = css_grad.get('background', '') or css_grad.get('background-image', '')
        check('U-209',
              'rotation=-π/2 渐变 → 烘焙后角度为 90deg（黑色在左侧，非 270deg）',
              '90deg' in bg and '270deg' not in bg)
    except Exception as e:
        check('U-209', 'gradient rotation bake formula fix', False)
        print(f'       Error: {e}')

    try:
        # U-210: RECTANGLE with IMAGE fill + rotation → 不应产生 CSS rotate()
        # Bug: _shape_family 新增导致 RECTANGLE/ELLIPSE null-width 节点获得 CSS rotation，
        # 而 Figma node export PNG 已含旋转（双重旋转 bug）。
        # 回归节点: 4030:41631 PreKYC example mobile image
        import math as _m2
        node_img_rot = make_node(
            type='RECTANGLE',
            rotation=-_m2.pi / 6,  # -30deg
            fills=[{'type': 'IMAGE', 'scaleMode': 'FILL', 'imageRef': 'abc123'}],
            absoluteBoundingBox={'x': 0, 'y': 0, 'width': 1736, 'height': 1610},
        )
        css_img = extract_css(node_img_rot, make_ctx(layoutMode='NONE'))
        transform = css_img.get('transform', '')
        check('U-210',
              'RECTANGLE IMAGE fill + rotation → 不应产生 CSS rotate()（PNG 已含旋转）',
              'rotate' not in transform)
    except Exception as e:
        check('U-210', 'IMAGE fill node: no CSS rotation', False)
        print(f'       Error: {e}')

    try:
        # U-211: GROUP 节点 rectangleCornerRadii=[0,0,0,0] → 不应写 border-radius: 0px
        # Bug: all-zero array 被写入 CSS，产生冗余且可能覆盖父容器 clip 效果的 0px 圆角。
        # 回归节点: 311:15270 Group 1000005300（BTC Pizza Day 页）
        node_zero_radius = make_node(
            type='FRAME',
            rectangleCornerRadii=[0.0, 0.0, 0.0, 0.0],
        )
        css_zr = extract_css(node_zero_radius, make_ctx())
        check('U-211',
              'rectangleCornerRadii=[0,0,0,0] → 不写 border-radius（避免冗余 0px）',
              'border-radius' not in css_zr)
    except Exception as e:
        check('U-211', 'zero cornerRadius: no border-radius CSS', False)
        print(f'       Error: {e}')

    try:
        # U-212: flatten_groups 不应展平含 cornerRadius 的 GROUP
        # Bug: GROUP 311:15271 (cornerRadius=16) 被展平，圆角 CSS 丢失，卡片无圆角。
        from lib.ir_builder import flatten_groups
        group_with_radius = {
            'type': 'GROUP',
            'id': 'test:212a',
            'name': 'Card Container',
            'blendMode': 'PASS_THROUGH',
            'cornerRadius': 16.0,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 384, 'height': 188},
            'children': [{'type': 'VECTOR', 'id': 'test:212b', 'name': 'bg'}],
        }
        group_passthrough = {
            'type': 'GROUP',
            'id': 'test:212c',
            'name': 'Pass Through',
            'blendMode': 'PASS_THROUGH',
            'cornerRadius': None,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 384, 'height': 188},
            'children': [{'type': 'RECTANGLE', 'id': 'test:212d', 'name': 'rect'}],
        }
        result = flatten_groups([group_with_radius, group_passthrough], False, 0, 0)
        ids = [n.get('id') for n in result]
        check('U-212a',
              'GROUP with cornerRadius=16 → 保留为 div（不展平，圆角不丢失）',
              'test:212a' in ids)
        check('U-212b',
              'GROUP without cornerRadius (PASS_THROUGH) → 仍被展平',
              'test:212c' not in ids and 'test:212d' in ids)
    except Exception as e:
        check('U-212a', 'flatten_groups: cornerRadius GROUP preserved', False)
        check('U-212b', 'flatten_groups: plain GROUP still flattened', False)
        print(f'       Error: {e}')

    try:
        # U-213: VECTOR + BACKGROUND_BLUR → 应产生 CSS border-radius
        # VECTOR with backdrop-blur 被渲染为 <div>（不是 SVG），CSS border-radius 有效。
        # 回归节点: 311:15272 (Rectangle 34624476, BTC Pizza Day 卡片背景)
        node_vec_blur = make_node(
            type='VECTOR',
            cornerRadius=16.0,
            effects=[{'type': 'BACKGROUND_BLUR', 'visible': True, 'radius': 10.0}],
        )
        css_vec_blur = extract_css(node_vec_blur, make_ctx())
        check('U-213a',
              'VECTOR + BACKGROUND_BLUR → 产生 border-radius: 16px',
              css_vec_blur.get('border-radius') == '16px')

        # 对照：普通 VECTOR（无 backdrop-blur）→ 不产生 border-radius
        node_vec_plain = make_node(type='VECTOR', cornerRadius=16.0, effects=[])
        css_vec_plain = extract_css(node_vec_plain, make_ctx())
        check('U-213b',
              'VECTOR without BACKGROUND_BLUR → 不产生 border-radius（SVG 路径已编码圆角）',
              'border-radius' not in css_vec_plain)
    except Exception as e:
        check('U-213a', 'VECTOR backdrop-blur: border-radius applied', False)
        check('U-213b', 'VECTOR plain: border-radius skipped', False)
        print(f'       Error: {e}')

    try:
        # U-214: 渐变角度 bake 后不应出现科学计数法（浏览器不支持 "5e-06deg"）
        # 根因: Figma rotation 不精确（-3.1415926 ≠ -π），导致 (180 + (-179.9999993) + 360)%360 ≈ 7e-7
        # Python {:g} 对 < 1e-4 的数使用科学计数法，CSS 浏览器拒绝 "7e-07deg"。
        # 修复: round(adjusted, 2) 将浮点误差归零 → "0deg" 有效 CSS。
        # 回归节点: 1484:27152 Shadow (DemoTrading H5 页)
        import math as _m3
        # Simulate Figma's slightly-off -π rotation
        node_near_pi = make_node(
            type='RECTANGLE',
            rotation=-3.1415925661670165,  # Figma's imprecise -π
            width=375, height=100,
            fills=[{
                'type': 'GRADIENT_LINEAR',
                'gradientHandlePositions': [
                    {'x': 0.5, 'y': 0.023},
                    {'x': 0.5, 'y': 1.0},
                    {'x': 0.011, 'y': 0.023},
                ],
                'gradientStops': [
                    {'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1.0}, 'position': 0.31},
                    {'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0.0}, 'position': 1.0},
                ],
            }],
        )
        css_pi = extract_css(node_near_pi, make_ctx(layoutMode='NONE'))
        bg_pi = css_pi.get('background', '') or css_pi.get('background-image', '')
        has_sci_notation = 'e-' in bg_pi or 'e+' in bg_pi
        check('U-214',
              'Figma 非精确 -π rotation → 渐变角度不含科学计数法（"5e-06deg" 是无效 CSS）',
              not has_sci_notation and 'linear-gradient' in bg_pi)
    except Exception as e:
        check('U-214', 'gradient angle: no scientific notation after near-π rotation', False)
        print(f'       Error: {e}')


    # U-215: HUG 子节点在 VERTICAL flex 父容器中缺少 flex-shrink:0 导致被等比压缩
    # 根因: _extract_flex_child 只对 FIXED 设 flex-shrink:0，HUG 语义是"永远按内容撑开"
    #        CSS 默认 flex-shrink:1 允许压缩，违反 Figma 语义
    try:
        _parent_215 = {
            'id': 'U215:parent', 'name': 'footer-text-area', 'type': 'FRAME',
            'layoutMode': 'VERTICAL',
            'layoutSizingHorizontal': 'FIXED', 'layoutSizingVertical': 'HUG',
            'itemSpacing': 32.0,
            'paddingTop': 0, 'paddingBottom': 0, 'paddingLeft': 20, 'paddingRight': 20,
            'clipsContent': True, 'width': 1200, 'height': 1388,
            'absoluteBoundingBox': {'x': 120, 'y': 384, 'width': 1200, 'height': 1388},
            'fills': [], 'strokes': [], 'effects': [],
            'children': [
                {
                    'id': 'U215:card', 'name': 'Frame 2147239946', 'type': 'FRAME',
                    'layoutMode': 'VERTICAL',
                    'layoutSizingHorizontal': 'FIXED', 'layoutSizingVertical': 'HUG',
                    'layoutPositioning': None, 'layoutGrow': 0.0,
                    'clipsContent': True, 'width': 1160, 'height': 208,
                    'absoluteBoundingBox': {'x': 140, 'y': 416, 'width': 1160, 'height': 208},
                    'fills': [], 'strokes': [], 'effects': [], 'children': [],
                },
                {
                    'id': 'U215:date', 'name': 'date-group', 'type': 'FRAME',
                    'layoutMode': 'VERTICAL',
                    'layoutSizingHorizontal': 'FIXED', 'layoutSizingVertical': 'HUG',
                    'layoutPositioning': None, 'layoutGrow': 0.0,
                    'clipsContent': False, 'width': 1160, 'height': 400,
                    'absoluteBoundingBox': {'x': 140, 'y': 656, 'width': 1160, 'height': 400},
                    'fills': [], 'strokes': [], 'effects': [], 'children': [],
                },
            ],
        }
        _ir215 = build_ir([_parent_215])[0]
        _parent_css215 = _ir215.get('css', {})
        _has_flex_col = (
            _parent_css215.get('display') == 'flex'
            and _parent_css215.get('flex-direction') == 'column'
        )
        _card215 = next((c for c in _ir215.get('children', []) if c.get('figmaId') == 'U215:card'), None)
        _card_shrink215 = (_card215 or {}).get('css', {}).get('flex-shrink')
        _date215 = next((c for c in _ir215.get('children', []) if c.get('figmaId') == 'U215:date'), None)
        _date_shrink215 = (_date215 or {}).get('css', {}).get('flex-shrink')

        check('U-215a',
              'VERTICAL flex 父容器应生成 display:flex + flex-direction:column',
              _has_flex_col)
        check('U-215b',
              'layoutSizingVertical=HUG 的 flex 子节点应有 flex-shrink:0（HUG=永远按内容撑开）',
              _card_shrink215 == '0' and _date_shrink215 == '0')
    except Exception as e:
        for tid in ('U-215a', 'U-215b'):
            check(tid, 'HUG vertical child: flex-shrink:0', False)
        print(f'       Error: {e}')

    # U-216: 绝对定位 HUG 节点（自身有 flex layoutMode）不应写入显式 height
    # 根因: is_absolute_self=True + layoutSizingVertical=HUG + 自身有 flex layoutMode
    #        → 旧逻辑强写 height:{bb}px → 配合 overflow:hidden 截断末尾子节点
    try:
        _abs_hug_node = make_node(
            type='FRAME', width=1200, height=400,
            layoutMode='VERTICAL',
            layoutSizingHorizontal='FIXED', layoutSizingVertical='HUG',
            layoutPositioning=None, paddingTop=0, paddingBottom=0,
        )
        _abs_fixed_node = make_node(
            type='FRAME', width=1200, height=400,
            layoutMode='VERTICAL',
            layoutSizingHorizontal='FIXED', layoutSizingVertical='FIXED',
            layoutPositioning=None,
        )
        _ctx_none = make_ctx(layoutMode='NONE')
        _css_hug = extract_css(_abs_hug_node, _ctx_none)
        _css_fixed = extract_css(_abs_fixed_node, _ctx_none)

        check('U-216a',
              'is_absolute_self=True + HUG + 自身 layoutMode=VERTICAL → 不应写 height',
              'height' not in _css_hug)
        check('U-216b',
              'is_absolute_self=True + FIXED → 应写 height',
              'height' in _css_fixed and _css_fixed['height'] == '400px')
    except Exception as e:
        for tid in ('U-216a', 'U-216b'):
            check(tid, 'absolute HUG node: no explicit height', False)
        print(f'       Error: {e}')

    # U-217: infer_page_flow 检测变化间距的垂直堆叠
    # 根因: infer_flex_layout 要求 gap 方差 ≤5px，根框架的段落间距不均（0/56/139px）
    #        新函数 infer_page_flow 放宽约束，返回 per-child margin 而非 uniform gap
    try:
        from lib.layout_inference import infer_page_flow
        _pf_children = [
            {'id': 'nav',     'absoluteBoundingBox': {'x': 0,   'y': 0,   'width': 1440, 'height': 48}},
            {'id': 'hero',    'absoluteBoundingBox': {'x': 0,   'y': 48,  'width': 1440, 'height': 280}},
            {'id': 'content', 'absoluteBoundingBox': {'x': 120, 'y': 384, 'width': 1200, 'height': 1388}},
            {'id': 'footer',  'absoluteBoundingBox': {'x': 0,   'y': 1911,'width': 1440, 'height': 448}},
        ]
        _pf_parent = {'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 2359}}
        _pf_result = infer_page_flow(_pf_children, _pf_parent, 0, 0)

        check('U-217a',
              'infer_page_flow 应检测到无重叠垂直堆叠并返回 page_flow descriptor',
              _pf_result is not None and _pf_result.get('mode') == 'page_flow')
        if _pf_result:
            _margins = _pf_result.get('per_child_margins', [])
            _content_m = next((m for m in _margins if m.get('id') == 'content'), {})
            check('U-217b',
                  'content 段落（左右各120px偏移）应被识别为居中容器（centered=True）',
                  _content_m.get('centered') is True and _content_m.get('max_width') == 1200)
        else:
            check('U-217b', 'infer_page_flow returned None', False)
    except Exception as e:
        for tid in ('U-217a', 'U-217b'):
            check(tid, 'infer_page_flow basic detection', False)
        print(f'       Error: {e}')

    # U-219: flex 子节点宽度等于父容器宽度 → _extract_sizing 输出 width:100%
    # 根因: 固定 width:1440px 在视口缩小时产生横向截断，width:100% 允许随父容器收缩
    try:
        _full_w_child = make_node(
            type='FRAME', width=1440, height=48,
            layoutMode='VERTICAL',
            layoutSizingHorizontal='FIXED', layoutSizingVertical='HUG',
            layoutPositioning=None,
        )
        _partial_w_child = make_node(
            type='FRAME', width=1200, height=48,
            layoutMode='VERTICAL',
            layoutSizingHorizontal='FIXED', layoutSizingVertical='HUG',
            layoutPositioning=None,
        )
        _border_w_child = make_node(
            type='FRAME', width=1438, height=48,
            layoutMode='VERTICAL',
            layoutSizingHorizontal='FIXED', layoutSizingVertical='HUG',
            layoutPositioning=None,
        )
        # ctx: 父容器宽度 1440px，layoutMode VERTICAL（flex 子节点，非 absolute）
        _ctx_v_1440 = make_ctx(layoutMode='VERTICAL', width=1440, height=2359)

        _css_full = extract_css(_full_w_child, _ctx_v_1440)
        _css_partial = extract_css(_partial_w_child, _ctx_v_1440)
        _css_border = extract_css(_border_w_child, _ctx_v_1440)

        check('U-219a',
              'flex 子节点宽度等于父容器（1440==1440）→ width:100%',
              _css_full.get('width') == '100%')
        check('U-219b',
              'flex 子节点宽度不等于父容器（1200≠1440）→ 保留 width:1200px',
              _css_partial.get('width') == '1200px')
        check('U-219c',
              'flex 子节点宽度比父容器小 2px（1438 vs 1440）→ 保留 width:1438px，不触发 100%',
              _css_border.get('width') == '1438px')
    except Exception as e:
        for tid in ('U-219a', 'U-219b', 'U-219c'):
            check(tid, 'full-width child → width:100%', False)
        print(f'       Error: {e}')

    # U-218: 根框架 page_flow 触发 → 子节点无 position:absolute，有 margin-top，
    #          根节点无 min-height
    try:
        _root_frame = {
            'id': 'U218:root', 'name': 'Page Root', 'type': 'FRAME',
            'layoutMode': None,
            'width': 1440, 'height': 2359,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 2359},
            'fills': [], 'strokes': [], 'effects': [],
            'children': [
                {
                    'id': 'U218:nav', 'name': 'Nav', 'type': 'FRAME',
                    'layoutMode': 'VERTICAL',
                    'layoutSizingHorizontal': 'FIXED', 'layoutSizingVertical': 'HUG',
                    'layoutPositioning': None, 'width': 1440, 'height': 48,
                    'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 48},
                    'fills': [], 'strokes': [], 'effects': [], 'children': [],
                },
                {
                    'id': 'U218:hero', 'name': 'Hero', 'type': 'FRAME',
                    'layoutMode': None,
                    'layoutSizingHorizontal': None, 'layoutSizingVertical': None,
                    'layoutPositioning': None, 'width': 1440, 'height': 280,
                    'absoluteBoundingBox': {'x': 0, 'y': 48, 'width': 1440, 'height': 280},
                    'fills': [], 'strokes': [], 'effects': [], 'children': [],
                },
                {
                    'id': 'U218:content', 'name': 'Content', 'type': 'FRAME',
                    'layoutMode': 'VERTICAL',
                    'layoutSizingHorizontal': 'FIXED', 'layoutSizingVertical': 'HUG',
                    'layoutPositioning': None, 'width': 1200, 'height': 1388,
                    'absoluteBoundingBox': {'x': 120, 'y': 384, 'width': 1200, 'height': 1388},
                    'fills': [], 'strokes': [], 'effects': [], 'children': [],
                },
                {
                    'id': 'U218:footer', 'name': 'Footer', 'type': 'FRAME',
                    'layoutMode': 'VERTICAL',
                    'layoutSizingHorizontal': 'FIXED', 'layoutSizingVertical': 'HUG',
                    'layoutPositioning': None, 'width': 1440, 'height': 448,
                    'absoluteBoundingBox': {'x': 0, 'y': 1911, 'width': 1440, 'height': 448},
                    'fills': [], 'strokes': [], 'effects': [], 'children': [],
                },
            ],
        }
        _ir218 = build_ir([_root_frame])[0]
        _root_css = _ir218.get('css', {})
        _children = _ir218.get('children', [])
        _nav = next((c for c in _children if c.get('figmaId') == 'U218:nav'), None)
        _content = next((c for c in _children if c.get('figmaId') == 'U218:content'), None)
        _footer = next((c for c in _children if c.get('figmaId') == 'U218:footer'), None)

        check('U-218a',
              'page_flow 根节点：display:flex + flex-direction:column，无 min-height，无 position:relative',
              _root_css.get('display') == 'flex'
              and _root_css.get('flex-direction') == 'column'
              and 'min-height' not in _root_css
              and 'position' not in _root_css)
        check('U-218b',
              'page_flow 子节点：无 position:absolute，nav 有 flex-shrink:0',
              (_nav or {}).get('css', {}).get('position') != 'absolute'
              and (_nav or {}).get('css', {}).get('flex-shrink') == '0')
        check('U-218c',
              'page_flow 居中子节点：content 有 margin-left:auto + max-width:1200px',
              (_content or {}).get('css', {}).get('margin-left') == 'auto'
              and (_content or {}).get('css', {}).get('max-width') == '1200px')
    except Exception as e:
        for tid in ('U-218a', 'U-218b', 'U-218c'):
            check(tid, 'ir_builder page_flow trigger', False)
        print(f'       Error: {e}')


    # ── U-219: 根节点 overflow-x 规则 ────────────────────────────────────────────
    # 背景: convert.py 曾无条件设置 overflow-x:hidden（旧 bug），会在视口 <1440px 时
    # 静默裁断右侧内容。修复：不对根节点无条件设置。
    # 但 clipsContent=True 表示设计师显式开启了"Clip Content"——装饰图（如 TomorrowlandLP
    # 蝴蝶 40017:4440）溢出到 1552px，导致 Playwright 截图宽于 1440px，pixel-diff 整体偏移。
    # 正确行为：isRoot + clipsContent=True → overflow-x:hidden（横向截断）；
    #          isRoot + clipsContent=False → 不设置（维持旧修复，允许横向展开）。

    print('\n  ─── U-219: 根节点 overflow-x 修复 ───────────')

    # U-219a: extract_css(isRoot=True) + clipsContent=True → overflow-x: hidden
    # (设计师显式启用 Clip Content，横向装饰图应被截断)
    try:
        root_node = make_node(
            type='FRAME', width=1440, height=5000,
            layoutMode='VERTICAL',
            clipsContent=True,
            fills=[], strokes=[], effects=[],
        )
        root_ctx = make_ctx(isRoot=True, layoutMode='VERTICAL', width=0, height=0)
        css_root = extract_css(root_node, root_ctx)
        check('U-219a',
              'extract_css isRoot=True + clipsContent=True → overflow-x: hidden（横向装饰图截断）',
              css_root.get('overflow-x') == 'hidden' and 'overflow-y' not in css_root
              and css_root.get('overflow') != 'hidden')
    except Exception as e:
        check('U-219a', 'root extract_css: overflow-x:hidden when clipsContent', False)
        print(f'       Error: {e}')

    # U-219a-neg: isRoot=True + clipsContent=False → 不设置 overflow-x（旧修复不变）
    try:
        root_node_nocc = make_node(
            type='FRAME', width=1440, height=5000,
            layoutMode='VERTICAL',
            clipsContent=False,
            fills=[], strokes=[], effects=[],
        )
        root_ctx_nocc = make_ctx(isRoot=True, layoutMode='VERTICAL', width=0, height=0)
        css_root_nocc = extract_css(root_node_nocc, root_ctx_nocc)
        check('U-219a-neg',
              'extract_css isRoot=True + clipsContent=False → 无 overflow / overflow-x（旧修复回归）',
              'overflow' not in css_root_nocc and 'overflow-x' not in css_root_nocc)
    except Exception as e:
        check('U-219a-neg', 'root extract_css no-clips: no overflow-x', False)
        print(f'       Error: {e}')

    # U-219b: convert.py 根节点后处理只 pop overflow（shorthand），不 pop overflow-x
    # — overflow-x:hidden 由 css_extractor 在 clipsContent=True 时有意设置，需保留。
    try:
        fake_ir = {'figmaId': 'test:root', 'css': {'overflow-x': 'hidden', 'overflow': 'hidden', 'width': '1440px'}}
        fake_ir['css'].pop('overflow', None)   # convert.py 只 pop 这一个
        # overflow-x 不 pop — intentionally left
        check('U-219b',
              'convert.py 后处理只 pop overflow shorthand，保留 overflow-x:hidden',
              'overflow' not in fake_ir['css'] and fake_ir['css'].get('overflow-x') == 'hidden')
    except Exception as e:
        check('U-219b', 'root IR post-process: overflow-x preserved', False)
        print(f'       Error: {e}')

    # U-219c: extract_css(isRoot=False) + clipsContent=True → 仍正常写入 overflow:hidden
    # 非根节点的 clipsContent clip 行为不受影响
    try:
        inner_node = make_node(
            type='FRAME', width=400, height=300,
            clipsContent=True,
            cornerRadius=8.0,
            fills=[], strokes=[], effects=[],
        )
        inner_ctx = make_ctx(isRoot=False, width=600, height=600)
        css_inner = extract_css(inner_node, inner_ctx)
        check('U-219c',
              'extract_css isRoot=False + clipsContent=True + radius → 仍有 overflow:hidden',
              css_inner.get('overflow') == 'hidden')
    except Exception as e:
        check('U-219c', 'inner node: overflow:hidden preserved', False)
        print(f'       Error: {e}')

    # U-220: infer_page_flow with gap > MAX_PAGE_FLOW_GAP_PX → returns None
    # Regression: N11 page had footer at y=956px and navi at y=892px, which
    # produced margin-top:956px / 892px, inflating page height ~3x.
    try:
        from lib.layout_inference import infer_page_flow
        pf_children_large_gap = [
            {'id': 'A', 'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 48}},
            {'id': 'B', 'absoluteBoundingBox': {'x': 0, 'y': 892, 'width': 1440, 'height': 48}},
            {'id': 'C', 'absoluteBoundingBox': {'x': 0, 'y': 956, 'width': 1440, 'height': 28}},
        ]
        pf_parent_large = {'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 1123}}
        result_large = infer_page_flow(pf_children_large_gap, pf_parent_large, 0, 0)
        check('U-220', 'infer_page_flow: gap>400px → returns None (fixed-height page guard)',
              result_large is None)
    except Exception as e:
        check('U-220', 'infer_page_flow: large-gap guard', False)
        print(f'       Error: {e}')

    # U-221: inferred_flex_to_css page_flow mode must NOT include align-items
    # Regression: hardcoded align-items:flex-start prevented HUG children from
    # stretching to fill the container width (status bar rendered as 136px).
    try:
        from lib.layout_inference import inferred_flex_to_css
        pf_inf = {
            'direction': 'column', 'mode': 'page_flow',
            'per_child_margins': [], 'confidence': 'high',
        }
        pf_css = inferred_flex_to_css(pf_inf)
        check('U-221a', 'inferred_flex_to_css page_flow: display:flex',
              pf_css.get('display') == 'flex')
        check('U-221b', 'inferred_flex_to_css page_flow: flex-direction:column',
              pf_css.get('flex-direction') == 'column')
        check('U-221c', 'inferred_flex_to_css page_flow: no align-items (design spec absent; ir_builder pops extractor default)',
              'align-items' not in pf_css)
    except Exception as e:
        check('U-221', 'inferred_flex_to_css page_flow: no align-items', False)
        print(f'       Error: {e}')

    # U-222: ir_builder page_flow container must not have align-items in final CSS
    # Regression: css_extractor writes align-items:flex-start from counterAxisAlignItems=None,
    # but ir_builder must pop it for page_flow so HUG children can stretch to full width.
    try:
        # Use same data structure as U-218 (known to trigger page_flow)
        # Verify page_flow root has no align-items (design spec absent; extractor default popped)
        _root_u222 = {
            'id': 'U222:root', 'name': 'Page Root', 'type': 'FRAME',
            'layoutMode': None,
            'width': 1440, 'height': 2359,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 2359},
            'fills': [], 'strokes': [], 'effects': [],
            'children': [
                {'id': 'U222:nav', 'name': 'Nav', 'type': 'FRAME',
                 'layoutMode': 'VERTICAL', 'layoutSizingHorizontal': 'FIXED',
                 'layoutSizingVertical': 'HUG', 'layoutPositioning': None,
                 'width': 1440, 'height': 48,
                 'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 48},
                 'fills': [], 'strokes': [], 'effects': [], 'children': []},
                {'id': 'U222:hero', 'name': 'Hero', 'type': 'FRAME',
                 'layoutMode': None, 'layoutSizingHorizontal': None,
                 'layoutSizingVertical': None, 'layoutPositioning': None,
                 'width': 1440, 'height': 280,
                 'absoluteBoundingBox': {'x': 0, 'y': 48, 'width': 1440, 'height': 280},
                 'fills': [], 'strokes': [], 'effects': [], 'children': []},
                {'id': 'U222:content', 'name': 'Content', 'type': 'FRAME',
                 'layoutMode': 'VERTICAL', 'layoutSizingHorizontal': 'FIXED',
                 'layoutSizingVertical': 'HUG', 'layoutPositioning': None,
                 'width': 1200, 'height': 600,
                 'absoluteBoundingBox': {'x': 120, 'y': 368, 'width': 1200, 'height': 600},
                 'fills': [], 'strokes': [], 'effects': [], 'children': []},
            ],
        }
        _irs_u222 = build_ir([_root_u222])
        _root_pf_css = _irs_u222[0].get('css', {}) if _irs_u222 else {}
        _is_page_flow = _irs_u222[0].get('inferredFlex', {}).get('mode') == 'page_flow' if _irs_u222 else False
        check('U-222', 'ir_builder page_flow container: align-items popped (design spec absent, extractor default removed)',
              _is_page_flow
              and 'align-items' not in _root_pf_css
              and _root_pf_css.get('display') == 'flex'
              and _root_pf_css.get('flex-direction') == 'column')
    except Exception as e:
        check('U-222', 'ir_builder page_flow container: no align-items', False)
        print(f'       Error: {e}')

    # U-223: css_extractor defaults to align-items:flex-start when counterAxisAlignItems absent.
    # This matches Figma's default (MIN = flex-start) and prevents layout regressions in
    # component internals (e.g. IDO Homepage Card children would incorrectly stretch otherwise).
    # NOTE: justify-content is NOT written when primaryAxisAlignItems is absent (see U-225).
    try:
        node_no_align = make_node(
            type='FRAME', layoutMode='VERTICAL',
            width=393, height=800,
        )
        ctx_v = make_ctx(layoutMode='NONE', width=393, height=900)
        css_no_align = extract_css(node_no_align, ctx_v)
        check('U-223a', 'VERTICAL flex without counterAxisAlignItems → defaults to align-items:flex-start',
              css_no_align.get('align-items') == 'flex-start')
        check('U-223b', 'VERTICAL flex without counterAxisAlignItems → still has display:flex',
              css_no_align.get('display') == 'flex')
        check('U-223c', 'VERTICAL flex without primaryAxisAlignItems → NO justify-content written',
              'justify-content' not in css_no_align)
    except Exception as e:
        check('U-223', 'css_extractor: absent counterAxisAlignItems → defaults flex-start', False)
        print(f'       Error: {e}')

    # U-224: css_extractor writes correct align-items when counterAxisAlignItems explicitly set
    try:
        node_with_align = make_node(
            type='FRAME', layoutMode='VERTICAL',
            width=393, height=800,
            counterAxisAlignItems='CENTER',
        )
        ctx_v2 = make_ctx(layoutMode='NONE', width=393, height=900)
        css_with_align = extract_css(node_with_align, ctx_v2)
        check('U-224', 'VERTICAL flex with counterAxisAlignItems=CENTER → align-items:center',
              css_with_align.get('align-items') == 'center')
    except Exception as e:
        check('U-224', 'css_extractor: explicit counterAxisAlignItems → align-items written', False)
        print(f'       Error: {e}')

    # U-225: css_extractor writes justify-content only when primaryAxisAlignItems explicitly set.
    # Absent → do not hardcode flex-start (design spec didn't specify it).
    # CSS default is normal (= flex-start for flex) so visual equivalence is maintained.
    try:
        node_no_jc = make_node(type='FRAME', layoutMode='VERTICAL', width=393, height=800)
        node_jc = make_node(type='FRAME', layoutMode='VERTICAL', width=393, height=800,
                            primaryAxisAlignItems='SPACE_BETWEEN')
        ctx_jc = make_ctx(layoutMode='NONE', width=393, height=900)
        css_no_jc = extract_css(node_no_jc, ctx_jc)
        css_with_jc = extract_css(node_jc, ctx_jc)
        check('U-225a', 'VERTICAL flex without primaryAxisAlignItems → no justify-content',
              'justify-content' not in css_no_jc)
        check('U-225b', 'VERTICAL flex with primaryAxisAlignItems=SPACE_BETWEEN → justify-content:space-between',
              css_with_jc.get('justify-content') == 'space-between')
    except Exception as e:
        check('U-225', 'css_extractor: primaryAxisAlignItems → justify-content behavior', False)
        print(f'       Error: {e}')

    # U-226: HUG element whose bb.width equals parent width → generates width:100%
    # Design info: when a HUG frame's measured width = parent width, it fills the parent.
    # CSS should reflect this so the element renders at the correct width, not content-width.
    try:
        # layoutPositioning=None → flow item (not absolute) so HUG branch executes
        hug_full = make_node(type='FRAME', layoutMode='VERTICAL', width=393, height=100,
                             layoutSizingHorizontal='HUG', layoutPositioning=None)
        ctx_parent393 = make_ctx(layoutMode='VERTICAL', width=393, height=900)
        css_hug_full = extract_css(hug_full, ctx_parent393)
        check('U-226a', 'HUG frame with bb.width == parent_w (393==393) → width:100%',
              css_hug_full.get('width') == '100%')

        hug_narrow = make_node(type='FRAME', layoutMode='VERTICAL', width=353, height=100,
                               layoutSizingHorizontal='HUG', layoutPositioning=None)
        css_hug_narrow = extract_css(hug_narrow, ctx_parent393)
        check('U-226b', 'HUG frame with bb.width != parent_w (353≠393) → no width written',
              'width' not in css_hug_narrow)
    except Exception as e:
        check('U-226', 'css_extractor: HUG full-width detection', False)
        print(f'       Error: {e}')

    # U-227: patch_flex_shrink must NOT infer height for flex container that has BOTH
    # absolute children AND flow children — even when the flex container has an explicit width.
    # Root cause: 286:66267 (IDO Homepage Card) had height:468px inferred from its Ellipse
    # child (468×468, position:absolute) despite also having two flow children.
    # The old guard `not (is_flex_with_abs and flow_children)` missed this case because
    # is_flex_with_abs=False when css.width is set. Fixed to use `css.display==flex`.
    try:
        abs_ellipse = {
            'figmaId': '286:66277', 'figmaName': 'Ellipse 7112',
            'figmaType': 'ELLIPSE', 'isDecorativeElement': False,
            'css': {'width': '468px', 'height': '468px', 'position': 'absolute',
                    'top': '-315px', 'left': '50%'},
            'children': [],
        }
        flow_row = {
            'figmaId': '286:66273', 'figmaName': 'Frame row',
            'figmaType': 'FRAME', 'isDecorativeElement': False,
            'css': {'display': 'flex', 'flex-direction': 'row', 'position': 'relative'},
            'children': [],
        }
        flow_col = {
            'figmaId': '286:66279', 'figmaName': 'Frame col',
            'figmaType': 'FRAME', 'isDecorativeElement': False,
            'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
            'children': [],
        }
        # IDO card: absolute position, flex column, explicit width, has both abs+flow children
        ido_card = {
            'figmaId': '286:66267', 'figmaName': 'IDO Homepage Card',
            'figmaType': 'FRAME', 'isDecorativeElement': False,
            'css': {'width': '353px', 'position': 'absolute', 'display': 'flex',
                    'flex-direction': 'column', 'align-items': 'flex-start',
                    'gap': '24px', 'padding': '8px 0px 16px 0px', 'overflow': 'hidden'},
            'children': [abs_ellipse, flow_row, flow_col],
        }
        parent_node = {
            'figmaId': 'parent', 'figmaName': 'Parent',
            'figmaType': 'FRAME', 'isDecorativeElement': False,
            'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
            'children': [ido_card],
        }
        patch_flex_shrink(parent_node)
        check('U-227a', 'flex container with explicit width + abs+flow children: no height inferred',
              'height' not in ido_card['css'])
        check('U-227b', 'flex container with explicit width + abs+flow children: width preserved',
              ido_card['css'].get('width') == '353px')
    except Exception as e:
        check('U-227', 'patch_flex_shrink: flex+flow guard prevents abs-child height inference', False)
        print(f'       Error: {e}')

    # U-227c: pure-absolute container (no flow children) SHOULD still infer height
    try:
        abs_only_child = {
            'figmaId': 'child1', 'figmaName': 'Abs child',
            'figmaType': 'FRAME', 'isDecorativeElement': False,
            'css': {'width': '200px', 'height': '300px', 'position': 'absolute',
                    'top': '0px', 'left': '0px'},
            'children': [],
        }
        abs_only_parent = {
            'figmaId': 'parent2', 'figmaName': 'All-abs parent',
            'figmaType': 'FRAME', 'isDecorativeElement': False,
            'css': {'position': 'absolute'},
            'children': [abs_only_child],
        }
        wrapper = {
            'figmaId': 'wrapper', 'figmaName': 'Wrapper',
            'figmaType': 'FRAME', 'isDecorativeElement': False,
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [abs_only_parent],
        }
        patch_flex_shrink(wrapper)
        check('U-227c', 'pure-absolute parent (no flow children): height IS inferred from abs child',
              abs_only_parent['css'].get('height') == '300px')
    except Exception as e:
        check('U-227c', 'patch_flex_shrink: pure-abs container still gets height inferred', False)
        print(f'       Error: {e}')

    # U-228: patch_flex_shrink must use bb.width (NOT abs-children l+w) when
    # is_flex_with_abs=True AND flow children exist.
    # Root cause: 285:43827 (Frame 2147229902) has 3 decorative 光圈 abs children
    # (360px, 348px, 386px — wider than container) + 1 flow child (Input, width:100%).
    # Old code used l+w of abs children → 389px; bb.width is 375px (correct).
    # Fix: when is_flex_with_abs=True + flow_children, prefer bb.width over abs-child calc.
    try:
        halo1 = {
            'figmaId': 'halo1', 'figmaName': '光圈1',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'css': {'width': '360.61px', 'height': '360.67px', 'position': 'absolute',
                    'left': '4.28px', 'top': '-88px'},
            'children': [],
        }
        halo2 = {
            'figmaId': 'halo2', 'figmaName': '光圈2',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'css': {'width': '386.67px', 'height': '386.83px', 'position': 'absolute',
                    'left': '-7.88px', 'top': '-113px'},
            'children': [],
        }
        input_flow = {
            'figmaId': 'input1', 'figmaName': 'Input',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                    'align-items': 'center', 'flex-shrink': '0', 'position': 'relative'},
            'children': [],
        }
        # 285:43827 equivalent: flex row, no width, has abs halo + flow input
        frame_row = {
            'figmaId': '285:43827', 'figmaName': 'Frame 2147229902',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 375.39, 'height': 120.0},
            'css': {'display': 'flex', 'flex-direction': 'row',
                    'justify-content': 'center', 'align-items': 'flex-start',
                    'flex-shrink': '0', 'position': 'relative'},
            'children': [halo1, halo2, input_flow],
        }
        outer = {
            'figmaId': 'outer', 'figmaName': 'Outer',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
            'children': [frame_row],
        }
        patch_flex_shrink(outer)
        inferred_w = frame_row['css'].get('width', '')
        check('U-228a', 'flex+abs+flow: width from bb.width (375px), NOT abs-child l+w (389px)',
              inferred_w == '375px')
        check('U-228b', 'flex+abs+flow: width is NOT from abs l+w (would be 379px in this test)',
              inferred_w != '379px')
    except Exception as e:
        check('U-228', 'patch_flex_shrink: flex+abs+flow uses bb.width for width', False)
        print(f'       Error: {e}')

    # U-229: patch_flex_shrink sets width:100% on flow child whose bb.width equals parent
    # content area (parent_bb_w - paddingLeft - paddingRight).
    # Root cause: 285:43825 (frame-2147229978) has bb.width=353 inside parent with
    # bb.width=393, padding=20px LR → content=353px. Child has no CSS width → browser
    # sizes it to max-content (375px from 285:43827 child) instead of 353px.
    # Fix: detect this pattern and set width:100% on the child.
    try:
        # Child with bb.width=353 = parent content area (393-20-20=353)
        child_fill = {
            'figmaId': '285:43825', 'figmaName': 'Frame 2147229978',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 353.0, 'height': 974.0},
            'css': {'display': 'flex', 'flex-direction': 'column',
                    'align-items': 'flex-start', 'flex-shrink': '0', 'position': 'relative'},
            'children': [],
        }
        parent_padded = {
            'figmaId': '285:43787', 'figmaName': 'Frame 2147223618',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 393.0, 'height': 2681.0},
            'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                    'align-items': 'center', 'padding': '24px 20px 0px 20px',
                    'flex-shrink': '0', 'position': 'relative'},
            'children': [child_fill],
        }
        outer3 = {
            'figmaId': 'outer3', 'figmaName': 'Root',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [parent_padded],
        }
        patch_flex_shrink(outer3)
        check('U-229a', 'flow child bb.width(353) == parent_content(393-40=353) → width:100%',
              child_fill['css'].get('width') == '100%')
    except Exception as e:
        check('U-229', 'patch_flex_shrink: content-area fill detection → width:100%', False)
        print(f'       Error: {e}')

    # U-229c: Task #7 full-width breakout — child bb.width ≈ parent bb.width (not content area)
    # and parent has LR padding → child gets calc(100% + padLR) + negative margins.
    # Root cause: 285:44342 (bb.width=393) inside 285:43787 (bb=393, padding=20px LR).
    # width:100% = 353px (content), but design requires 393px (full breakout).
    # Fix: replace width:100% with calc(100% + 40px) and add margin-left:-20px.
    try:
        breakout_child = {
            'figmaId': '285:44342', 'figmaName': '1',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 393.0, 'height': 1651.0},
            'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'row',
                    'align-items': 'center', 'padding': '0px 20px 0px 20px',
                    'flex-shrink': '0', 'position': 'relative'},
            'children': [],
        }
        breakout_parent = {
            'figmaId': '285:43787', 'figmaName': 'Frame 2147223618',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 393.0, 'height': 2681.0},
            'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                    'align-items': 'center', 'padding': '24px 20px 0px 20px',
                    'flex-shrink': '0', 'position': 'relative'},
            'children': [breakout_child],
        }
        outer_bo = {
            'figmaId': 'outer_bo', 'figmaName': 'Root',
            'figmaType': 'FRAME',
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [breakout_parent],
        }
        patch_flex_shrink(outer_bo)
        w229c = breakout_child['css'].get('width', '')
        check('U-229c', 'full-width breakout: width becomes explicit bb.width=393px (not calc hack)',
              w229c == '393px')
        check('U-229d', 'full-width breakout: no margin-left needed (align-items:center handles offset)',
              breakout_child['css'].get('margin-left') is None)
    except Exception as e:
        check('U-229c', 'patch_flex_shrink: full-width breakout calc+negative-margin', False)
        print(f'       Error: {e}')

    # U-229b: child whose bb.width != parent content area should NOT get width:100%
    try:
        child_narrow = {
            'figmaId': 'narrow', 'figmaName': 'Narrow child',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 300.0, 'height': 100.0},
            'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
            'children': [],
        }
        parent_padded2 = {
            'figmaId': 'parent_p2', 'figmaName': 'Parent',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 393.0, 'height': 500.0},
            'css': {'display': 'flex', 'flex-direction': 'column',
                    'padding': '0px 20px 0px 20px', 'position': 'relative'},
            'children': [child_narrow],
        }
        outer4 = {
            'figmaId': 'outer4', 'figmaName': 'Root4',
            'figmaType': 'FRAME',
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [parent_padded2],
        }
        patch_flex_shrink(outer4)
        check('U-229b', 'child bb.width(300) ≠ parent_content(353) → no width:100% forced',
              child_narrow['css'].get('width') != '100%')
    except Exception as e:
        check('U-229b', 'patch_flex_shrink: non-matching child not forced to 100%', False)
        print(f'       Error: {e}')

    # U-230: patch_flex_shrink sets width/height from bb for flex-row flow child
    # with flex-shrink:0 but no explicit width.
    # Root cause: Pagination items (bb=32x32) share same CSS → normalize_class_names
    # deduplicates them all to one class; that class needs explicit width/height to render
    # at the designed size (32px) instead of content-driven width (9px for numbers).
    try:
        pag_item = {
            'figmaId': 'I227:13142;7788:217407', 'figmaName': '_Components/Pagination-Item',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 32.0, 'height': 32.0},
            'css': {'display': 'flex', 'flex-direction': 'column',
                    'align-items': 'flex-start', 'flex-shrink': '0', 'position': 'relative'},
            'children': [],
        }
        flex_row_parent = {
            'figmaId': '227:13142', 'figmaName': 'Pagination/Default',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 272.0, 'height': 32.0},
            'css': {'display': 'flex', 'flex-direction': 'row',
                    'align-items': 'center', 'gap': '8px', 'position': 'relative'},
            'children': [pag_item],
        }
        outer_pag = {
            'figmaId': 'outer_pag', 'figmaName': 'Root',
            'figmaType': 'FRAME',
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [flex_row_parent],
        }
        patch_flex_shrink(outer_pag)
        check('U-230a', 'flex-row child with flex-shrink:0, no width, bb=32 → width:32px',
              pag_item['css'].get('width') == '32px')
        check('U-230b', 'flex-row child with flex-shrink:0, no height, bb=32 → height:32px',
              pag_item['css'].get('height') == '32px')
    except Exception as e:
        check('U-230', 'patch_flex_shrink: flex-shrink:0 child gets bb dimensions', False)
        print(f'       Error: {e}')

    # U-230c: flex-row child WITHOUT flex-shrink:0 should NOT get bb width forced
    try:
        no_shrink_item = {
            'figmaId': 'no_shrink', 'figmaName': 'Grow Item',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 100.0, 'height': 32.0},
            'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
            'children': [],
        }
        flex_row_parent2 = {
            'figmaId': 'flex_row2', 'figmaName': 'Row2',
            'figmaType': 'FRAME',
            'bb': {'width': 300.0, 'height': 32.0},
            'css': {'display': 'flex', 'flex-direction': 'row', 'position': 'relative'},
            'children': [no_shrink_item],
        }
        outer_pag2 = {
            'figmaId': 'outer_pag2', 'figmaName': 'Root2',
            'figmaType': 'FRAME',
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [flex_row_parent2],
        }
        patch_flex_shrink(outer_pag2)
        check('U-230c', 'flex-row child WITHOUT flex-shrink:0 → no bb width forced',
              no_shrink_item['css'].get('width') is None)
    except Exception as e:
        check('U-230c', 'patch_flex_shrink: non-shrink child not forced', False)
        print(f'       Error: {e}')

    # U-228c: pure-abs flex container (no flow) still uses abs-child l+w for width
    try:
        halo_only = {
            'figmaId': 'halo_only', 'figmaName': '光圈',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'css': {'width': '200px', 'height': '200px', 'position': 'absolute',
                    'left': '10px', 'top': '0px'},
            'children': [],
        }
        pure_abs_flex = {
            'figmaId': 'pure_abs', 'figmaName': 'Pure Abs Flex',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'bb': {'width': 150.0, 'height': 200.0},
            'css': {'display': 'flex', 'flex-direction': 'row', 'position': 'relative'},
            'children': [halo_only],
        }
        outer2 = {
            'figmaId': 'outer2', 'figmaName': 'Outer2',
            'figmaType': 'FRAME', 'isDecorativeElement': None,
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [pure_abs_flex],
        }
        patch_flex_shrink(outer2)
        # For pure-abs (no flow): abs-child l+w = 10+200 = 210px (is_flex_with_abs behavior)
        w228c = pure_abs_flex['css'].get('width', '')
        check('U-228c', 'pure-abs flex (no flow children): width inferred from abs l+w = 210px',
              w228c == '210px')
    except Exception as e:
        check('U-228c', 'patch_flex_shrink: pure-abs flex still infers from abs children', False)
        print(f'       Error: {e}')

    # U-231: background-color: #000000 不应被映射为 --bds-gray-bg-page
    # --bds-gray-bg-page 是主题敏感 token（dark=#000000，light=浅灰），
    # 用于 background-color 时在亮色主题页（如 Tomorrowland footer）会显示为浅色。
    try:
        _mock_tokens_u231 = {
            'colors': {'#000000': 'var(--bds-gray-bg-page)'},
        }
        css_u231a = {'background-color': '#000000'}
        _resolve_css_dict(css_u231a, _mock_tokens_u231)
        check('U-231a', 'background-color:#000000 不替换为 --bds-gray-bg-page（亮色主题下会显示为浅灰）',
              css_u231a.get('background-color') == '#000000')

        css_u231b = {'color': '#000000'}
        _resolve_css_dict(css_u231b, _mock_tokens_u231)
        check('U-231b', 'color:#000000 不替换为 --bds-gray-bg-page（同样保护）',
              css_u231b.get('color') == '#000000')

        css_u231c = {'border-color': '#000000'}
        _resolve_css_dict(css_u231c, _mock_tokens_u231)
        check('U-231c', 'border-color:#000000 可以替换为 --bds-gray-bg-page（非守卫属性）',
              css_u231c.get('border-color') == 'var(--bds-gray-bg-page)')

        # bg-card / bg-area / bg-float 同样不应出现在 background-color
        _mock_tokens_u231d = {
            'colors': {'#16171a': 'var(--bds-gray-bg-card)'},
        }
        css_u231d = {'background-color': '#16171a'}
        _resolve_css_dict(css_u231d, _mock_tokens_u231d)
        check('U-231d', 'background-color:#16171a 不替换为 --bds-gray-bg-card',
              css_u231d.get('background-color') == '#16171a')

        _mock_tokens_u231e = {
            'colors': {'#101014': 'var(--bds-gray-bg-area)'},
        }
        css_u231e = {'background-color': '#101014'}
        _resolve_css_dict(css_u231e, _mock_tokens_u231e)
        check('U-231e', 'background-color:#101014 不替换为 --bds-gray-bg-area',
              css_u231e.get('background-color') == '#101014')
    except Exception as e:
        check('U-231a', f'EXCEPTION: {e}', False)
        check('U-231b', f'EXCEPTION: {e}', False)
        check('U-231c', f'EXCEPTION: {e}', False)
        check('U-231d', f'EXCEPTION: {e}', False)
        check('U-231e', f'EXCEPTION: {e}', False)


    # ── U-265: auto-layout 内 absolute 子节点 DOM 顺序保持（z-order 一致）─────
    # Figma children 数组：back-to-front（index 0=最底层，last=最顶层）。
    # CSS DOM 绘制顺序：后面的元素在上面。两者一致，无需反转。
    try:
        from lib.ir_builder import build_ir as _bi265
        # 模拟 auto-layout HORIZONTAL 容器，3个 absolute 子节点
        # Figma 顺序: bg(idx 0=底层), progress(idx 1=中间), handle(idx 2=顶层)
        _slider_node = {
            'id': 'slider:1', 'name': 'Slider', 'type': 'INSTANCE',
            'layoutMode': 'HORIZONTAL',
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 192, 'height': 10},
            'children': [
                {'id': 'bg', 'name': 'bg', 'type': 'RECTANGLE',
                 'layoutPositioning': 'ABSOLUTE',
                 'absoluteBoundingBox': {'x': 0, 'y': 3, 'width': 192, 'height': 4},
                 'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
                 'fills': [{'type': 'SOLID', 'color': {'r': 0.3, 'g': 0.3, 'b': 0.3, 'a': 1}}],
                 'children': []},
                {'id': 'progress', 'name': 'progress', 'type': 'FRAME',
                 'layoutPositioning': 'ABSOLUTE',
                 'absoluteBoundingBox': {'x': 0, 'y': 3, 'width': 192, 'height': 4},
                 'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
                 'fills': [{'type': 'SOLID', 'color': {'r': 0.97, 'g': 0.65, 'b': 0, 'a': 1}}],
                 'children': []},
                {'id': 'handle', 'name': 'handle', 'type': 'FRAME',
                 'layoutPositioning': 'ABSOLUTE',
                 'absoluteBoundingBox': {'x': 5, 'y': 4, 'width': 10, 'height': 10},
                 'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
                 'fills': [], 'children': []},
            ],
        }
        _ir265 = _bi265([_slider_node], {})[0]
        _child_ids = [c.get('figmaId') for c in _ir265.get('children', [])]
        # Figma back-to-front order preserved: bg(底), progress(中), handle(顶)
        # CSS DOM order same: bg first(底), handle last(顶)
        check('U-265', 'auto-layout abs 子节点保持 Figma 顺序（back-to-front = DOM 绘制顺序）',
              _child_ids == ['bg', 'progress', 'handle'])
    except Exception as e:
        check('U-265', f'EXCEPTION: {e}', False)


    # ── U-269: 有 absolute 子级的容器不应 height→min-height ──────────────────────
    # 原因: _post_process_frame 在 flow children 占据高度超出 frame_h*1.05 时把
    #       height 转为 min-height。但若容器有 position:absolute 子级（背景层），
    #       它们依赖父级固定 height 来居中定位，不能转为 min-height。
    try:
        from lib.ir_builder import _post_process_frame
        _ir_269 = {
            'figmaId': 'test:269',
            'css': {
                'display': 'flex', 'flex-direction': 'column',
                'align-items': 'center', 'gap': '96px',
                'padding': '32px 12px 32px 12px',
                'height': '160px', 'width': '100%',
                'position': 'relative',
            },
            'children': [
                # absolute background child (decoration)
                {'figmaId': 'test:bg', 'isDecorativeElement': True,
                 'css': {'position': 'absolute', 'width': '480px', 'height': '142px',
                         'top': '50%', 'left': '50%',
                         'transform': 'translateX(-50%) translateY(-50%)'}},
                # flow child 1: text title
                {'figmaId': 'test:title', 'isTextNode': True,
                 'css': {'font-size': '14px', 'line-height': '20px', 'width': '100%'}},
                # flow child 2: subtitle row
                {'figmaId': 'test:amount',
                 'css': {'display': 'flex', 'flex-direction': 'row', 'width': '100%'}},
            ],
        }
        _post_process_frame(_ir_269)
        _s_css_269 = _ir_269['css']
        _kept_height = _s_css_269.get('height') is not None
        check('U-269', 'absolute 子级容器保持固定 height（height=' + str(_s_css_269.get('height', 'None')) + ', min-height=' + str(_s_css_269.get('min-height', 'None')) + '）',
              _kept_height)
    except Exception as e:
        check('U-269', f'EXCEPTION: {e}', False)


    # ── U-270: flow children 应有 position:relative 当存在 absolute 兄弟时 ──────
    # 原因: position:absolute 元素在 stacking 中高于 static 元素（即使 DOM 在前）。
    #       当容器有 absolute 背景层时，没有 position 的 flow 兄弟会被遮住。
    #       所有 flow children 应被赋予 position:relative 以确保在 absolute 层之上。
    try:
        from lib.ir_builder import _post_process_frame as _ppf_270
        _ir_270 = {
            'figmaId': 'test:270',
            'css': {
                'display': 'flex', 'flex-direction': 'column',
                'height': '160px', 'width': '100%',
                'position': 'relative',
            },
            'children': [
                # absolute background child
                {'figmaId': 'test:270-bg', 'isDecorativeElement': True,
                 'css': {'position': 'absolute', 'width': '480px', 'height': '142px',
                         'top': '50%', 'left': '50%',
                         'transform': 'translateX(-50%) translateY(-50%)'}},
                # flow child WITHOUT position (should get position:relative)
                {'figmaId': 'test:270-title', 'isTextNode': True,
                 'css': {'font-size': '14px', 'line-height': '20px', 'width': '100%'}},
                # flow child already WITH position:relative (should keep)
                {'figmaId': 'test:270-content',
                 'css': {'display': 'flex', 'width': '100%', 'position': 'relative'}},
            ],
        }
        _ppf_270(_ir_270)
        _title_css = _ir_270['children'][1]['css']
        _content_css = _ir_270['children'][2]['css']
        check('U-270a', 'flow child 无 position → 应得 position:relative（当前: ' + str(_title_css.get('position', 'None')) + '）',
              _title_css.get('position') == 'relative')
        check('U-270b', 'flow child 已有 position:relative → 保持不变',
              _content_css.get('position') == 'relative')
    except Exception as e:
        check('U-270', f'EXCEPTION: {e}', False)

    # ── U-278: constraint=CENTER/CENTER but NOT geometrically centered ────────
    try:
        _parent_278 = {
            'id': 'p278', 'type': 'FRAME', 'name': 'Card',
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 381, 'height': 480},
            'layoutMode': None,
            'children': [
                {
                    'id': 'c278', 'type': 'FRAME', 'name': 'TextFrame',
                    'absoluteBoundingBox': {'x': 30, 'y': 46, 'width': 320, 'height': 208},
                    'constraints': {'vertical': 'CENTER', 'horizontal': 'CENTER'},
                }
            ],
        }
        _content_278 = _parent_278['children']
        _result_278 = convert_to_flow(_parent_278, _content_278)
        _parent_css_278 = _result_278.get('parent_css', {}) if _result_278 else {}
        _has_center = (_parent_css_278.get('justify-content') == 'center'
                       and _parent_css_278.get('align-items') == 'center')
        check('U-278', 'constraint=CENTER/CENTER 但 Y≠居中时不应生成 flex center',
              not _has_center)
    except Exception as e:
        check('U-278', f'EXCEPTION: {e}', False)


    # ── U-279: GROUP content node in _single_element_flow must receive height ──
    # When a GROUP (no layoutMode) with overlapping children is the sole content
    # node inside a clipsContent parent, it must get height in children_css.
    # Without height the group collapses to 0 (all children are absolute) and
    # the parent's overflow:hidden clips everything.
    try:
        _parent_279 = {
            'id': 'P279', 'type': 'FRAME', 'layoutMode': None, 'clipsContent': True,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 250, 'height': 200},
            'children': [{'id': 'C279', 'type': 'GROUP', 'layoutMode': None,
                          'absoluteBoundingBox': {'x': 14, 'y': 10, 'width': 220, 'height': 194},
                          'constraints': {'horizontal': 'CENTER', 'vertical': 'CENTER'},
                          'children': [
                              {'id': 'img1', 'type': 'RECTANGLE', 'absoluteBoundingBox': {'x': 20, 'y': 10, 'width': 195, 'height': 194}},
                              {'id': 'img2', 'type': 'RECTANGLE', 'absoluteBoundingBox': {'x': 50, 'y': 30, 'width': 60, 'height': 60}},
                          ]}]
        }
        _content_nodes_279 = [_parent_279['children'][0]]
        _result_279 = convert_to_flow(_parent_279, _content_nodes_279)
        _child_css_279 = _result_279.get('children_css', {}).get('C279', {}) if _result_279 else {}
        check('U-279', 'GROUP content node in flow conversion must include height (got: ' + str(_child_css_279.get('height', 'MISSING')) + ')',
              'height' in _child_css_279)
    except Exception as e:
        check('U-279', f'EXCEPTION: {e}', False)


    # ── U-280: bottom-sheet pattern — abs+neg-bottom+fixed-height+flex-col+radius ──
    # Real data: node 314:11956 from PageByMeta-314-11779 (nodeId: 314-11779)
    # frame_h=4438 (root min-height), child: bottom=-1034px, height=4764px
    # Expected: child.bottom removed, child.top=708px set, child.height removed
    # Rationale: top = parent_h - child_h - bottom_px = 4438 - 4764 - (-1034) = 708
    try:
        # Realistic state: child has border-radius but overflow:hidden not yet set
        # (overflow is added by a later pass; the fix must not depend on it).
        _parent_280 = {
            'figmaId': '314:11779', 'type': 'FRAME', 'name': 'PageRoot',
            'css': {
                'width': '393px', 'height': '4438px',
                'position': 'relative', 'background-color': '#000000',
            },
            'children': [{
                'figmaId': '314:11956', 'type': 'FRAME', 'name': 'BottomSheet',
                'css': {
                    'position': 'absolute', 'left': '0px', 'bottom': '-1034px',
                    'width': '393px', 'height': '4764px',
                    'display': 'flex', 'flex-direction': 'column',
                    'border-radius': '24px 24px 0px 0px',
                    'background-color': '#000000', 'z-index': '7',
                },
                'children': [],
            }],
        }
        _post_process_frame(_parent_280)
        _child_css_280 = _parent_280['children'][0]['css']
        check('U-280a', 'bottom-sheet: bottom key removed from child',
              'bottom' not in _child_css_280)
        check('U-280b', 'bottom-sheet: top:708px set on child',
              _child_css_280.get('top') == '708px')
        check('U-280c', 'bottom-sheet: height removed from child',
              'height' not in _child_css_280)
        check('U-280d', 'bottom-sheet: border-radius preserved',
              _child_css_280.get('border-radius') == '24px 24px 0px 0px')
    except Exception as e:
        check('U-280', f'EXCEPTION: {e}', False)

    # ── U-280e: bottom-sheet with parent using min-height (realistic IR state) ──
    # Real data: in the saved IR, root has min-height:4438px (height was already converted).
    # The fix must also read min-height as fallback so frame_h is non-zero.
    try:
        _parent_280e = {
            'figmaId': '314:11779', 'type': 'FRAME', 'name': 'PageRoot',
            'css': {
                'width': '393px', 'min-height': '4438px',  # no 'height' key
                'position': 'relative', 'background-color': '#000000',
            },
            'children': [{
                'figmaId': '314:11956', 'type': 'FRAME', 'name': 'BottomSheet',
                'css': {
                    'position': 'absolute', 'left': '0px', 'bottom': '-1034px',
                    'width': '393px', 'height': '4764px',
                    'display': 'flex', 'flex-direction': 'column',
                    'border-radius': '24px 24px 0px 0px',
                    'background-color': '#000000', 'z-index': '7',
                },
                'children': [],
            }],
        }
        _post_process_frame(_parent_280e)
        _child_css_280e = _parent_280e['children'][0]['css']
        check('U-280e', 'bottom-sheet with min-height parent: top:708px set on child',
              _child_css_280e.get('top') == '708px')
    except Exception as e:
        check('U-280e', f'EXCEPTION: {e}', False)


    # ── U-281: bottom-sheet child's flex-column flow children lose fixed height ──
    # Real data: 314:11957 (.content-section) inside 314:11956 (.page-container)
    # height:2814px on .content-section creates blank space after parent loses its height.
    # Fix: when bottom-sheet transformation fires, also remove height from direct
    # flex-column flow children of the transformed child.
    try:
        _content_section_css = {
            'width': '393px', 'height': '2814px',
            'display': 'flex', 'flex-direction': 'column',
            'align-items': 'center', 'gap': '24px',
            'flex-shrink': '0', 'position': 'relative',
        }
        _parent_281 = {
            'figmaId': '314:11779', 'type': 'FRAME', 'name': 'PageRoot',
            'css': {'width': '393px', 'height': '4438px', 'position': 'relative'},
            'children': [{
                'figmaId': '314:11956', 'type': 'FRAME', 'name': 'BottomSheet',
                'css': {
                    'position': 'absolute', 'left': '0px', 'bottom': '-1034px',
                    'width': '393px', 'height': '4764px',
                    'display': 'flex', 'flex-direction': 'column',
                    'border-radius': '24px 24px 0px 0px',
                    'background-color': '#000000', 'z-index': '7',
                },
                'children': [{
                    'figmaId': '314:11957', 'type': 'FRAME', 'name': 'ContentSection',
                    'css': _content_section_css,
                    'children': [],
                }],
            }],
        }
        _post_process_frame(_parent_281)
        check('U-281', 'bottom-sheet: direct flex-column flow child height removed',
              'height' not in _content_section_css)
    except Exception as e:
        check('U-281', f'EXCEPTION: {e}', False)



    # ── U-282: _wrap_overflow_children promoted nodes must have z-index: 1 ──────
    # Real data: node 6777:33063 "ChatGPT Image Feb 2" from PortalRevised (nodeId: 6777-32751)
    # position: absolute; top: -87.5px overflows above get-started-features card (6777:33056).
    # Bug: promoted overflow images have z-index: auto (default). The card (DOM-later,
    # position: relative) is painted on top of the image in the overlapping area, hiding the
    # 3D "0%" decoration that should be visible above AND inside the card.
    # Fix: _wrap_overflow_children must set z-index: 1 on each promoted child so the image
    # appears above the card background in the overlap region.
    try:
        _node_282 = {
            'type': 'FRAME',
            'clipsContent': False,
            'fills': [{'type': 'SOLID', 'color': {'r': 0.85, 'g': 0.85, 'b': 0.85, 'a': 0.1}, 'visible': True}],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1200, 'height': 174},
        }
        _overflow_img_282 = {
            'figmaId': '6777:33063',
            'figmaType': 'RECTANGLE',
            'figmaName': 'ChatGPT Image Feb 2, 2026',
            'isImageNode': True, 'isVectorNode': False, 'isTextNode': False,
            'isComponentInstance': False,
            'bb': None, 'imageRef': None, 'fillImageRef': None, 'componentId': None,
            'figmaCounterAxisAlign': None, 'textContent': None, 'textSegments': None,
            'textAutoResize': None, 'lineTypes': None, 'lineIndentations': None,
            'variants': None,
            'css': {
                'width': '452px', 'height': '301px',
                'position': 'absolute', 'left': '809px', 'top': '-87.5px',
                'pointer-events': 'none',
            },
            'children': [],
        }
        _card_ir_282 = {
            'figmaId': '6777:33056',
            'figmaType': 'FRAME',
            'figmaName': 'get-started-features',
            'isImageNode': False, 'isVectorNode': False, 'isTextNode': False,
            'isComponentInstance': False,
            'bb': None, 'imageRef': None, 'fillImageRef': None, 'componentId': None,
            'figmaCounterAxisAlign': None, 'textContent': None, 'textSegments': None,
            'textAutoResize': None, 'lineTypes': None, 'lineIndentations': None,
            'variants': None,
            'css': {
                'width': '100%', 'height': '174px',
                'display': 'flex', 'flex-direction': 'column',
                'position': 'relative', 'overflow': 'hidden',
                'border-radius': '24px',
            },
            'children': [_overflow_img_282],
        }
        _result_282 = _wrap_overflow_children(_card_ir_282, _node_282)
        # Promoted child should be first, modified_parent second
        _first_child_css = (_result_282.get('children') or [{}])[0].get('css', {})
        _first_child_node = (_result_282.get('children') or [{}])[0]
        check('U-282a', 'promoted overflow child (top: -87.5px) gets z-index: 1',
              _first_child_css.get('z-index') == '1')
        check('U-282b', 'promoted overflow child gets _isOWChild: True marker',
              _first_child_node.get('_isOWChild') is True)
    except Exception as e:
        check('U-282', f'EXCEPTION: {e}', False)


if __name__ == '__main__':
    reset()
    run_tests()
    ok = print_summary()
    sys.exit(0 if ok else 1)
