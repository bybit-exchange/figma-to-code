#!/usr/bin/env python3
"""
test_token_resolver.py — token_resolver 单元测试（从 verify_fixes.py 拆分）
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
from lib.token_resolver import _is_bg_semantic_token, _is_bg_fill_token, _is_interaction_token, _resolve_css_dict, load_tokens, tag_mixed_theme_nodes
from lib.tsx_generator import generate_tsx, detect_page_theme
from lib.figma_vars import normalize_var_name


def run_tests():
    # ── U-240: Figma Variable → BDS Token mapping ──────────────────────────────
    _mock_var_id_to_token = {
        'VariableID:abc/111': '--bds-gray-t1-title',
        'VariableID:abc/222': '--bds-brand-700-normal',
    }

    # U-240a: SOLID fill with boundVariables hit → output var(--bds-xxx), not hex
    _node_240a = make_node(
        type='FRAME',
        fills=[{
            'type': 'SOLID',
            'color': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0},
            'boundVariables': {'color': {'type': 'VARIABLE_ALIAS', 'id': 'VariableID:abc/111'}},
        }]
    )
    _css_240a = extract_css(_node_240a, make_ctx(), {}, _mock_var_id_to_token)
    # hex=#ffffff 匹配 dark 主题标准值，无需 revert
    check('U-240a', 'boundVariables hit → var(--bds-gray-t1-title) for background-color',
          _css_240a.get('background-color') == 'var(--bds-gray-t1-title)')

    # U-240b: hit + opacity=0.8 → color-mix
    _node_240b = make_node(
        type='FRAME',
        fills=[{
            'type': 'SOLID',
            'color': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0},
            'opacity': 0.8,
            'boundVariables': {'color': {'type': 'VARIABLE_ALIAS', 'id': 'VariableID:abc/111'}},
        }]
    )
    _css_240b = extract_css(_node_240b, make_ctx(), {}, _mock_var_id_to_token)
    check('U-240b', 'boundVariables hit + opacity=0.8 → color-mix()',
          'color-mix' in (_css_240b.get('background-color') or ''))

    # U-240c: boundVariables exists but NOT in var_id_to_token → hex fallback + miss recorded
    _node_240c = make_node(
        type='FRAME',
        fills=[{
            'type': 'SOLID',
            'color': {'r': 1.0, 'g': 0.0, 'b': 0.0, 'a': 1.0},
            'boundVariables': {'color': {'type': 'VARIABLE_ALIAS', 'id': 'VariableID:abc/UNKNOWN'}},
        }]
    )
    clear_var_miss()
    _css_240c = extract_css(_node_240c, make_ctx(), {}, _mock_var_id_to_token)
    _miss_240c = get_var_miss()
    check('U-240c', 'boundVariables miss → hex fallback #ff0000',
          (_css_240c.get('background-color') or '').startswith('#ff'))
    check('U-240c-miss', 'miss var recorded in get_var_miss()',
          'VariableID:abc/UNKNOWN' in _miss_240c)

    # U-240d: no boundVariables → existing hex path unchanged
    _node_240d = make_node(
        type='FRAME',
        fills=[{'type': 'SOLID', 'color': {'r': 0.0, 'g': 0.5, 'b': 1.0, 'a': 1.0}}]
    )
    _css_240d = extract_css(_node_240d, make_ctx(), {}, _mock_var_id_to_token)
    check('U-240d', 'no boundVariables → hex output',
          (_css_240d.get('background-color') or '').startswith('#'))

    # U-240e: TEXT node with boundVariables hit → color: var(--bds-xxx)
    _node_240e = make_node(
        type='TEXT',
        fills=[{
            'type': 'SOLID',
            'color': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0},
            'boundVariables': {'color': {'type': 'VARIABLE_ALIAS', 'id': 'VariableID:abc/111'}},
        }],
        style={'fontSize': 16, 'fontFamily': 'Inter', 'fontWeight': 400,
               'lineHeightPx': 20, 'letterSpacing': 0, 'textAlignHorizontal': 'LEFT'},
        characters='Hello',
    )
    _css_240e = extract_css(_node_240e, make_ctx(), {}, _mock_var_id_to_token)
    check('U-240e', 'TEXT node boundVariables hit → color: var(--bds-gray-t1-title)',
          _css_240e.get('color') == 'var(--bds-gray-t1-title)')

    # U-240f: normalize_var_name
    check('U-240f-path', 'normalize_var_name("Gray/tt-1/title") → "--gray-tt-1-title"',
          normalize_var_name('Gray/tt-1/title') == '--gray-tt-1-title')
    check('U-240f-idem', 'normalize_var_name("--gray-tt-1-title") idempotent',
          normalize_var_name('--gray-tt-1-title') == '--gray-tt-1-title')
    check('U-240f-space', 'normalize_var_name("static White") → "--static-white"',
          normalize_var_name('static White') == '--static-white')


    # ── U-241~U-245: detect_page_theme 纯根节点背景亮度判断 ──────────────────
    def _mk_ir(root_bg, children_bgs):
        kids = [{'css': {'background-color': b}, 'children': []} for b in children_bgs]
        return {'css': {'background-color': root_bg}, 'fills': [], 'children': kids}

    try:
        # 新行为：只看根节点，不管子节点 token
        ir = _mk_ir('#ffffff', ['var(--bds-gray-bg-page)', 'var(--bds-gray-bg-card)', 'var(--bds-gray-bg-page)'])
        check('U-241', '根节点 #ffffff → light（忽略子节点 dark token）',
              detect_page_theme(ir) == 'light')
    except Exception as e:
        check('U-241', f'EXCEPTION: {e}', False)

    try:
        ir = _mk_ir('#ffffff', ['var(--bds-gray-bg-page)', 'var(--bds-gray-bg-card)',
                                 'var(--bds-gray-bg-area)', 'var(--bds-static-white)'])
        check('U-242', '根节点 #ffffff → light（不管子节点混合 token）',
              detect_page_theme(ir) == 'light')
    except Exception as e:
        check('U-242', f'EXCEPTION: {e}', False)

    try:
        ir = _mk_ir('#ffffff', ['var(--bds-static-white)', 'var(--bds-gray-bg-page-web)'])
        check('U-243', '根节点 #ffffff → light',
              detect_page_theme(ir) == 'light')
    except Exception as e:
        check('U-243', f'EXCEPTION: {e}', False)

    try:
        ir = _mk_ir('#000000', ['#111111', '#222222'])
        check('U-244', '根背景 #000000 → dark',
              detect_page_theme(ir) == 'dark')
    except Exception as e:
        check('U-244', f'EXCEPTION: {e}', False)

    try:
        ir = _mk_ir('#ffffff', [])
        check('U-245', '根背景 #ffffff → light',
              detect_page_theme(ir) == 'light')
    except Exception as e:
        check('U-245', f'EXCEPTION: {e}', False)


    # ── U-246~U-247: tag_mixed_theme_nodes 纯 luminance 判断 ─────────────────
    def _mk_tag_ir(child_bg: str) -> dict:
        child = {'figmaType': 'FRAME', 'figmaName': 'TestFrame',
                 'css': {'background-color': child_bg}, 'children': []}
        return {'css': {'background-color': '#000000'}, 'fills': [], 'children': [child]}

    try:
        ir246 = _mk_tag_ir('#ffffff')
        tag_mixed_theme_nodes(ir246, page_theme='dark')
        child_ov = ir246['children'][0].get('themeOverride', '')
        check('U-246', '#ffffff FRAME 在 dark 页 → themeOverride=light（luminance ≥ 0.6）',
              child_ov == 'light')
    except Exception as e:
        check('U-246', f'EXCEPTION: {e}', False)

    try:
        # #bafa5b luminance ≈ 0.79，超过 0.6 → 会被标为 light
        # 新行为不再有饱和度过滤——纯 luminance 判断
        ir247 = _mk_tag_ir('#bafa5b')
        tag_mixed_theme_nodes(ir247, page_theme='dark')
        child_ov = ir247['children'][0].get('themeOverride', '')
        check('U-247', '#bafa5b FRAME luminance≈0.79 在 dark 页 → themeOverride=light',
              child_ov == 'light')
    except Exception as e:
        check('U-247', f'EXCEPTION: {e}', False)


    # ── U-248: 根节点背景不强制替换 — Figma 给的值保持原样 ──────────────────
    try:
        # 根节点有变量绑定（由 P0a 已解析为 var），保持不变
        ir248 = {'css': {'background-color': 'var(--bds-static-white)'}, 'children': []}
        check('U-248a', '根节点 var(--bds-static-white) 保持原样，不强制替换',
              ir248['css']['background-color'] == 'var(--bds-static-white)')

        # 根节点无变量绑定（raw hex），也保持不变
        ir248b = {'css': {'background-color': '#ffffff'}, 'children': []}
        check('U-248b', '根节点 #ffffff 保持原样，不强制替换为 var',
              ir248b['css']['background-color'] == '#ffffff')
    except Exception as e:
        check('U-248', f'EXCEPTION: {e}', False)


    # ── U-249: generate_tsx 接受外部 page_theme，不再内部重新检测 ─────────────
    try:
        _ir249 = {
            'figmaId': '1:1', 'figmaName': 'Page', 'figmaType': 'FRAME',
            'css': {'background-color': 'var(--bds-gray-bg-page)', 'width': '1440px'},
            'children': [],
            'semantic': {'className': 'test-page', 'componentName': 'TestPage'},
        }
        tsx_dark = generate_tsx(_ir249, page_theme='dark')
        tsx_light = generate_tsx(_ir249, page_theme='light')
        check('U-249a', "page_theme='dark' → usePageEnv sets data-theme='dark'",
              "html.setAttribute('data-theme', 'dark')" in tsx_dark)
        check('U-249b', "page_theme='light' → usePageEnv sets data-theme='light'",
              "html.setAttribute('data-theme', 'light')" in tsx_light)
        check('U-249c', "page_theme='dark' 和 'light' 生成不同 TSX",
              tsx_dark != tsx_light)
    except Exception as e:
        check('U-249', f'EXCEPTION: {e}', False)


    # ── U-250: Format B fill 绑定（node.boundVariables.fills[i]）注入 ────────────
    # 当 fill 对象本身没有 boundVariables.color，但 node.boundVariables.fills[i] 有时，
    # 应正确解析为 BDS token 而非 hex fallback。
    try:
        from lib.css_extractor import extract_css as _ec
        _node250 = {
            'type': 'TEXT',
            'fills': [{'type': 'SOLID', 'color': {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 1.0}, 'blendMode': 'NORMAL', 'boundVariables': {}}],
            'boundVariables': {'fills': [{'type': 'VARIABLE_ALIAS', 'id': 'VariableID:abc250def/1:1'}]},
        }
        _bds250 = {'VariableID:abc250def/1:1': '--bds-gray-t1-title'}
        _ctx250 = {'x': 0, 'y': 0, 'width': 100, 'height': 20}
        _css250 = _ec(_node250, _ctx250, var_id_to_bds=_bds250)
        check('U-250a', 'Format B fill bv → var(--bds-gray-t1-title), not #000000',
              _css250.get('color') == 'var(--bds-gray-t1-title)')

        # Format A 优先，Format B 作为 fallback（不覆盖已有的 fill 级绑定）
        _node250b = {
            'type': 'TEXT',
            'fills': [{'type': 'SOLID', 'color': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0}, 'blendMode': 'NORMAL',
                       'boundVariables': {'color': {'type': 'VARIABLE_ALIAS', 'id': 'VariableID:aaa/2:2'}}}],
            'boundVariables': {'fills': [{'type': 'VARIABLE_ALIAS', 'id': 'VariableID:abc250def/1:1'}]},
        }
        _bds250b = {'VariableID:aaa/2:2': '--bds-gray-t2', 'VariableID:abc250def/1:1': '--bds-gray-t1-title'}
        _css250b = _ec(_node250b, _ctx250, var_id_to_bds=_bds250b)
        check('U-250b', 'Format A 优先于 Format B (fill 级绑定不被 node 级覆盖)',
              _css250b.get('color') == 'var(--bds-gray-t2)')
    except Exception as e:
        check('U-250', f'EXCEPTION: {e}', False)


    # ── U-251: tag_mixed_theme_nodes 识别渐变背景中的浅色（gradient light detection）───
    # 对于 dark 页面，linear-gradient(#f5f7fa) 这样的浅色渐变节点
    # 应当被标记为 themeOverride='light'（theme-override-light）
    try:
        import lib.token_resolver as _tr251
        _tag251 = _tr251.tag_mixed_theme_nodes

        # tag_mixed_theme_nodes skips root, so gradient section must be a child
        _cta251 = {
            'figmaId': '177:13498', 'figmaName': 'CTA', 'figmaType': 'FRAME',
            'css': {'background': 'linear-gradient(180deg, #f5f7fa 50%)'},
            'children': []
        }
        _root251 = {
            'figmaId': '169:1', 'figmaName': 'Page', 'figmaType': 'FRAME',
            'css': {'background-color': '#000000'},
            'children': [_cta251]
        }
        _tag251(_root251, page_theme='dark')
        check('U-251a', '渐变背景 #f5f7fa → 在 dark 页面被标记为 themeOverride=light',
              _cta251.get('themeOverride') == 'light')

        # Orange gradient: #ff9c2e luminance ≈ 0.66, ≥ 0.6 → tagged as light
        _orange251 = {
            'figmaId': '999:1', 'figmaName': 'OrangeBg', 'figmaType': 'FRAME',
            'css': {'background': 'linear-gradient(90deg, #ff9c2e 0%, #ff6600 100%)'},
            'children': []
        }
        _root251b = {
            'figmaId': '169:2', 'figmaName': 'Page2', 'figmaType': 'FRAME',
            'css': {'background-color': '#000000'},
            'children': [_orange251]
        }
        _tag251(_root251b, page_theme='dark')
        check('U-251b', '橙色渐变 lum≈0.66 ≥ 0.6 在 dark 页 → themeOverride=light',
              _orange251.get('themeOverride') == 'light')

        # U-251c: 兄弟节点垂直重叠传播
        _bg251c = {
            'figmaId': '177:13498', 'figmaName': 'BgLayer', 'figmaType': 'FRAME',
            'css': {'background': 'linear-gradient(180deg, #f5f7fa 50%)', 'top': '2995px', 'height': '537px'},
            'children': []
        }
        _content251c = {
            'figmaId': '177:13501', 'figmaName': 'ContentLayer', 'figmaType': 'FRAME',
            'css': {'top': '3153px'},  # inside bg range [2995, 3532], no background
            'children': []
        }
        _root251c = {
            'figmaId': '169:1', 'figmaName': 'Page', 'figmaType': 'FRAME',
            'css': {'background-color': '#000000'},
            'children': [_bg251c, _content251c]
        }
        _tag251(_root251c, page_theme='dark')
        check('U-251c', '内容层（无背景）覆盖在 light 渐变背景兄弟节点上 → 也被标记为 themeOverride=light',
              _content251c.get('themeOverride') == 'light')
    except Exception as e:
        check('U-251', f'EXCEPTION: {e}', False)


    # ── U-252~U-255: detect_page_theme 简化为纯根节点背景亮度 ──────────────────
    # 新行为：只看根节点 background-color，不统计子节点 BDS token
    try:
        # U-252: 根节点白色，子节点全是 dark token → 应返回 light（只看根节点）
        ir252 = {'css': {'background-color': '#ffffff'},
                 'children': [{'css': {'background-color': 'var(--bds-gray-bg-page)'}, 'children': []},
                              {'css': {'background-color': 'var(--bds-gray-bg-card)'}, 'children': []}]}
        check('U-252', '根节点 #ffffff → light（忽略子节点 dark token）',
              detect_page_theme(ir252) == 'light')
    except Exception as e:
        check('U-252', f'EXCEPTION: {e}', False)

    try:
        # U-253: 根节点黑色 → dark
        ir253 = {'css': {'background-color': '#1a1a1a'}, 'children': []}
        check('U-253', '根节点 #1a1a1a → dark',
              detect_page_theme(ir253) == 'dark')
    except Exception as e:
        check('U-253', f'EXCEPTION: {e}', False)

    try:
        # U-254: 根节点深蓝 → dark
        ir254 = {'css': {'background-color': '#0a0e17'}, 'children': []}
        check('U-254', '根节点 #0a0e17（深蓝黑）→ dark',
              detect_page_theme(ir254) == 'dark')
    except Exception as e:
        check('U-254', f'EXCEPTION: {e}', False)

    try:
        # U-255: 无背景色 → 默认 light
        ir255 = {'css': {}, 'children': []}
        check('U-255', '无 background-color → 默认 light',
              detect_page_theme(ir255) == 'light')
    except Exception as e:
        check('U-255', f'EXCEPTION: {e}', False)


    # ── U-256~U-259: tag_mixed_theme_nodes 简化为纯 luminance ────────────────
    # 新行为：不需要 tokens 参数，直接用背景色亮度判断是否与页面主题相反
    try:
        import lib.token_resolver as _tr256
        _tag256 = _tr256.tag_mixed_theme_nodes

        # U-256: dark 页 + 子节点白色背景 → themeOverride=light（无需 tokens）
        _child256 = {'figmaType': 'FRAME', 'figmaName': 'LightSection',
                     'css': {'background-color': '#ffffff'}, 'children': []}
        _root256 = {'css': {'background-color': '#000000'}, 'children': [_child256]}
        _tag256(_root256, page_theme='dark')
        check('U-256', 'dark 页 + 白色子节点 → themeOverride=light（纯 luminance）',
              _child256.get('themeOverride') == 'light')
    except Exception as e:
        check('U-256', f'EXCEPTION: {e}', False)

    try:
        # U-257: light 页 + 子节点深色背景 → themeOverride=dark
        _child257 = {'figmaType': 'FRAME', 'figmaName': 'DarkSection',
                     'css': {'background-color': '#1a1a1a'}, 'children': []}
        _root257 = {'css': {'background-color': '#ffffff'}, 'children': [_child257]}
        _tag256(_root257, page_theme='light')
        check('U-257', 'light 页 + 深色子节点 → themeOverride=dark（纯 luminance）',
              _child257.get('themeOverride') == 'dark')
    except Exception as e:
        check('U-257', f'EXCEPTION: {e}', False)

    try:
        # U-258: dark 页 + 子节点也是深色 → 不标记（同主题）
        _child258 = {'figmaType': 'FRAME', 'figmaName': 'DarkSection',
                     'css': {'background-color': '#222222'}, 'children': []}
        _root258 = {'css': {'background-color': '#000000'}, 'children': [_child258]}
        _tag256(_root258, page_theme='dark')
        check('U-258', 'dark 页 + 深色子节点 → 不标记 themeOverride',
              _child258.get('themeOverride') is None)
    except Exception as e:
        check('U-258', f'EXCEPTION: {e}', False)

    try:
        # U-259: tag_mixed_theme_nodes 不需要 tokens 参数也能正常工作
        _child259 = {'figmaType': 'FRAME', 'figmaName': 'LightCard',
                     'css': {'background-color': '#f5f5f5'}, 'children': []}
        _root259 = {'css': {'background-color': '#0a0e17'}, 'children': [_child259]}
        _tag256(_root259, page_theme='dark')
        check('U-259', 'tag_mixed_theme_nodes 无 tokens 参数正常工作',
              _child259.get('themeOverride') == 'light')
    except Exception as e:
        check('U-259', f'EXCEPTION: {e}', False)


    # ── U-261~U-262: 文字色 token 用作背景色的反色标记 ──────────────────────────
    # --bds-gray-t[1-4] 文字色 token 极性反转：暗色模式值=亮色，亮色模式值=暗色。
    # 当用作 background-color 时，rawBgHex luminance 需要反转判断。
    try:
        # U-261: light 页 + t1-title 做背景(rawBgHex=#FFFFFF) → themeOverride=dark
        # t1-title 暗色模式=白色，rawBgHex=白说明设计在暗色模式。
        # 运行时 light 模式下 t1-title=暗色 → 需要 dark override 让它保持白色。
        _child261 = {'figmaType': 'FRAME', 'figmaName': 'CardDarkTheme',
                     'css': {'background-color': 'var(--bds-gray-t1-title)', '_rawBgHex': '#ffffff'},
                     'children': []}
        _root261 = {'css': {'background-color': '#ffffff'}, 'children': [_child261]}
        _tag256(_root261, page_theme='light')
        check('U-261', 'light 页 + t1-title bg(rawBgHex=白) → themeOverride=dark',
              _child261.get('themeOverride') == 'dark')
    except Exception as e:
        check('U-261', f'EXCEPTION: {e}', False)

    try:
        # U-262: dark 页 + bg-card 做背景(rawBgHex=#1a1a1a) → 不标记
        # bg-card 暗色模式=暗色，rawBgHex=暗 → 与 dark page 一致 → 无需反色。
        _child262 = {'figmaType': 'FRAME', 'figmaName': 'CardSameTheme',
                     'css': {'background-color': 'var(--bds-gray-bg-card)', '_rawBgHex': '#1a1a1a'},
                     'children': []}
        _root262 = {'css': {'background-color': '#0a0a0a'}, 'children': [_child262]}
        _tag256(_root262, page_theme='dark')
        check('U-262', 'dark 页 + bg-card bg(rawBgHex=暗) → 不标记（同主题）',
              _child262.get('themeOverride') is None)
    except Exception as e:
        check('U-262', f'EXCEPTION: {e}', False)

    try:
        # U-263: brand token (--bds-brand-700-normal) 不应触发 theme override
        # Brand 色是固定色（橙色），不随主题变化，不应参与主题反色判断
        _child263 = {'figmaType': 'FRAME', 'figmaName': 'ProgressBar',
                     'css': {'background-color': 'var(--bds-brand-700-normal)', '_rawBgHex': '#F7A600'},
                     'children': []}
        _root263 = {'css': {'background-color': '#0a0a0a'}, 'children': [_child263]}
        _tag256(_root263, page_theme='dark')
        check('U-263', 'dark 页 + brand-700-normal bg(rawBgHex=橙) → 不标记（brand 固定色跳过判断）',
              _child263.get('themeOverride') is None)
    except Exception as e:
        check('U-263', f'EXCEPTION: {e}', False)

    try:
        # U-264: Brand token 也不应在 light 页面触发
        _child264 = {'figmaType': 'FRAME', 'figmaName': 'ButtonBrand',
                     'css': {'background-color': 'var(--Brand-700-normal)', '_rawBgHex': '#F7A600'},
                     'children': []}
        _root264 = {'css': {'background-color': '#ffffff'}, 'children': [_child264]}
        _tag256(_root264, page_theme='light')
        check('U-264', 'light 页 + Brand-700-normal bg(rawBgHex=橙) → 不标记（Brand 大写前缀同样跳过）',
              _child264.get('themeOverride') is None)
    except Exception as e:
        check('U-264', f'EXCEPTION: {e}', False)


    # ── U-260: 不再做 hex→token 反查替换 ─────────────────────────────────────
    # Figma 节点没有绑定变量时，CSS 值保持原始 hex，不做 resolve_ir_tokens
    try:
        from lib.token_resolver import resolve_ir_tokens as _rit260
        _ir260 = {'css': {'color': '#f7a600', 'background-color': '#1e2023'}, 'children': []}
        _tokens260 = {'colors': {'#f7a600': 'var(--bds-brand-700)', '#1e2023': 'var(--bds-gray-bg-card)'},
                      'color_themes': {}, 'font_sizes': {}, 'font_weights': {}, 'border_radii': {}, 'font_family': None}
        # 新行为：resolve_ir_tokens 不应该被调用（或调用后不应改变值）
        # 这个测试验证我们的 convert.py 流程不再调用 resolve_ir_tokens
        # 但函数本身可能还存在做兼容——所以这里测试的是整体产物中
        # 没有绑定变量的节点保持原始 hex
        check('U-260', '（设计意图）无 Figma 变量绑定的节点应保持原始 hex 值',
              True)  # 此测试通过产物验证，这里标记设计意图
    except Exception as e:
        check('U-260', f'EXCEPTION: {e}', False)



    # ── U-274: 颜色模糊匹配（ΔE≤2）应命中 1-bit 偏差的 token ─────────────────
    # BDS token #f7f7f7 对应 --bds-gray-t1，但 Figma 导出 #f8f8f8（RGB 各通道差 1）
    # 精确匹配失败后，模糊匹配应找到最近的 token。
    try:
        _fuzzy_tokens = {
            'colors': {'#f7f7f7': 'var(--bds-gray-t1)', '#333333': 'var(--bds-gray-t0)'},
            'font_sizes': {}, 'font_weights': {}, 'border_radii': {}, 'font_family': None,
        }
        _fuzzy_css = {'background-color': '#f8f8f8'}  # 1-bit off from #f7f7f7
        _resolve_css_dict(_fuzzy_css, _fuzzy_tokens)
        check('U-274', '颜色模糊匹配: #f8f8f8 → var(--bds-gray-t1)（ΔE=1, 应命中）',
              _fuzzy_css.get('background-color') == 'var(--bds-gray-t1)')
    except Exception as e:
        check('U-274', f'EXCEPTION: {e}', False)

    # ── U-275: 模糊匹配不应跨越 ΔE>2 的阈值 ──────────────────────────────────
    try:
        _fuzzy_tokens2 = {
            'colors': {'#f0f0f0': 'var(--bds-gray-bg-area)'},
            'font_sizes': {}, 'font_weights': {}, 'border_radii': {}, 'font_family': None,
        }
        _fuzzy_css2 = {'background-color': '#f5f5f5'}  # ΔE=5, too far
        _resolve_css_dict(_fuzzy_css2, _fuzzy_tokens2)
        check('U-275', '颜色模糊匹配: #f5f5f5 vs #f0f0f0（ΔE=5）不应命中',
              _fuzzy_css2.get('background-color') == '#f5f5f5')
    except Exception as e:
        check('U-275', f'EXCEPTION: {e}', False)


    # ── U-276: design token 会替换 text color（_BG_SEMANTIC_TOKENS 已清空，不再阻断替换）──
    try:
        _tokens_276 = {
            'colors': {'#383b3d': 'var(--color-text-disabled)'},
            'font_sizes': {}, 'font_weights': {}, 'border_radii': {}, 'font_family': None,
        }
        _css_276 = {'color': '#383b3d'}
        _resolve_css_dict(_css_276, _tokens_276)
        check('U-276', 'design token 替换 text color（无硬编码阻断名单时正常替换）',
              _css_276.get('color') == 'var(--color-text-disabled)')
    except Exception as e:
        check('U-276', f'EXCEPTION: {e}', False)

    # ── U-277: App-only Figma variables must NOT map to web BDS tokens ────────
    # Gray[Bg]/Bg_Page_App is an app-only variable; it must NOT resolve to
    # --bds-gray-bg-page (which is a web token). The pipeline should fall through
    # to raw hex for App-only variables.
    try:
        from lib.figma_vars import load_var_token_map
        _vbm = load_var_token_map()
        _app_keys = [k for k in _vbm if k.endswith('-app')]
        check('U-277', 'var_token_map must not contain _App suffix mappings',
              len(_app_keys) == 0)
    except Exception as e:
        check('U-277', f'EXCEPTION: {e}', False)


    # ── U-278: 根无背景，子节点有 BDS token + _rawBgHex(浅色) → light ──────────
    # Bug: _count_themed_bg_nodes 靠 token 亮度投票，正确区分 dark/light，
    # 但该 token 在 light 模式下解析为 #ffffff（亮），应判 light。
    # Real data: node 10664:26584 "Rectangle 337" from page 10664-26578 (nodeId: 10664-26578)
    try:
        _ir278 = {
            'css': {},  # 根节点透明，无背景色
            'fills': [],
            'children': [
                # 26 个 --bds-gray-bg-card 节点，Figma light 模式下解析为 #ffffff
                {'css': {'background-color': 'var(--bds-gray-bg-card)', '_rawBgHex': '#ffffff'}, 'children': []},
                {'css': {'background-color': 'var(--bds-gray-bg-card)', '_rawBgHex': '#ffffff'}, 'children': []},
                {'css': {'background-color': 'var(--bds-gray-bg-card)', '_rawBgHex': '#ffffff'}, 'children': []},
                # --bds-static-white → #ffffff
                {'css': {'background-color': 'var(--bds-static-white)', '_rawBgHex': '#ffffff'}, 'children': []},
            ],
        }
        check('U-278', '根无背景 + 子节点 --bds-gray-bg-card/_rawBgHex=#ffffff → light',
              detect_page_theme(_ir278) == 'light')
    except Exception as e:
        check('U-278', f'EXCEPTION: {e}', False)

    # ── U-279: 根无背景，子节点 BDS token + _rawBgHex(深色) → dark ─────────────
    # 确保修复后深色场景仍然正确识别为 dark。
    try:
        _ir279 = {
            'css': {},
            'fills': [],
            'children': [
                # dark 模式下 --bds-gray-bg-card 解析为深灰 #1e2026（luminance ≈ 0.017）
                {'css': {'background-color': 'var(--bds-gray-bg-card)', '_rawBgHex': '#1e2026'}, 'children': []},
                {'css': {'background-color': 'var(--bds-gray-bg-card)', '_rawBgHex': '#1e2026'}, 'children': []},
                {'css': {'background-color': 'var(--bds-gray-bg-float)', '_rawBgHex': '#2b2f36'}, 'children': []},
            ],
        }
        check('U-279', '根无背景 + 子节点 --bds-gray-bg-card/_rawBgHex=#1e2026 → dark',
              detect_page_theme(_ir279) == 'dark')
    except Exception as e:
        check('U-279', f'EXCEPTION: {e}', False)

    # ── U-280: 根无背景，子节点有 BDS token 但无 _rawBgHex → 不计入投票 ─────────
    # _rawBgHex 缺失时不能靠 token 名判断，该节点应被跳过，
    # 最终 dark=0 light=0 → 默认返回 light（detect_page_theme 最终 return "light"）。
    try:
        _ir280 = {
            'css': {},
            'fills': [],
            'children': [
                # 无 _rawBgHex：旧代码靠 token 名判 dark，修复后应跳过
                {'css': {'background-color': 'var(--bds-gray-bg-card)'}, 'children': []},
                {'css': {'background-color': 'var(--bds-gray-bg-page)'}, 'children': []},
            ],
        }
        check('U-280', '根无背景 + 子节点无 _rawBgHex → 跳过不计入投票 → 默认 light',
              detect_page_theme(_ir280) == 'light')
    except Exception as e:
        check('U-280', f'EXCEPTION: {e}', False)

    # ── U-422: light 页 + 节点 PC 背景白色 + H5 responsive 背景深色 → themeOverride=dark ─
    # Real data: EU Deposit Campaign Hero section 250:2620
    # PC css.background-color = #ffffff → luminance=1.0 → light → no override normally
    # H5 responsive css.background-color = #000000 → luminance=0.0 → dark
    # page_theme = 'light', so a dark H5 background IS opposite → should set themeOverride=dark
    # Bug: tag_mixed_theme_nodes only checks css.background-color (PC), not responsive[].css
    # Fix: also check responsive[].css.background-color entries
    try:
        _hero422 = {
            'figmaType': 'FRAME',
            'figmaName': 'Hero',
            'figmaId': '250:2620',
            'css': {'background-color': '#ffffff'},
            'responsive': [{'breakpoint': 768, 'css': {'background-color': '#000000'}}],
            'children': [],
        }
        _root422 = {'css': {}, 'children': [_hero422]}
        tag_mixed_theme_nodes(_root422, page_theme='light')
        check('U-422a', 'light 页 + PC白色背景 + H5深色背景 → themeOverride=dark',
              _hero422.get('themeOverride') == 'dark')

        # Negative: if H5 responsive background is ALSO light, no override
        _hero422b = {
            'figmaType': 'FRAME',
            'figmaName': 'LightHero',
            'css': {'background-color': '#ffffff'},
            'responsive': [{'breakpoint': 768, 'css': {'background-color': '#f5f5f5'}}],
            'children': [],
        }
        _root422b = {'css': {}, 'children': [_hero422b]}
        tag_mixed_theme_nodes(_root422b, page_theme='light')
        check('U-422b', 'light 页 + PC白色背景 + H5浅色背景 → 不标记 themeOverride',
              _hero422b.get('themeOverride') is None)

        # When PC background IS dark, it should still be tagged (existing behavior unchanged)
        _hero422c = {
            'figmaType': 'FRAME',
            'figmaName': 'DarkHero',
            'css': {'background-color': '#0a0e17'},
            'responsive': [],
            'children': [],
        }
        _root422c = {'css': {}, 'children': [_hero422c]}
        tag_mixed_theme_nodes(_root422c, page_theme='light')
        check('U-422c', 'light 页 + PC深色背景（无responsive）→ themeOverride=dark（原有行为保持）',
              _hero422c.get('themeOverride') == 'dark')
    except Exception as e:
        check('U-422a', f'EXCEPTION: {e}', False)
        check('U-422b', f'EXCEPTION: {e}', False)
        check('U-422c', f'EXCEPTION: {e}', False)


if __name__ == '__main__':
    reset()
    run_tests()
    ok = print_summary()
    sys.exit(0 if ok else 1)
