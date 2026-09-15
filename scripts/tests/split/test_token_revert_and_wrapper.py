#!/usr/bin/env python3
"""
Token 反转 + 实例 wrapper 渲染测试：
- text token 用作 background-color 时自动 -revert
- 实例路径 ID（含分号）在 class 不同时渲染 wrapper div
- class 相同时不渲染 wrapper（避免双层）
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()


def test_bug15_text_token_revert_for_background():
    """U-313: bds-gray-t1-title 用作 background-color 时应替换为 -revert。"""
    from lib.token_resolver import _revert_text_token_for_bg

    # t1-title as background → revert
    result = _revert_text_token_for_bg('var(--bds-gray-t1-title)')
    check('U-313a', 't1-title becomes t1-title-revert',
          'bds-gray-t1-title-revert' in result)

    # t2 as background → NO revert (faint gray is OK as bg)
    result2 = _revert_text_token_for_bg('var(--bds-gray-t2)')
    check('U-313b', 't2 stays unchanged',
          result2 == 'var(--bds-gray-t2)')

    # Non-text token → unchanged
    result3 = _revert_text_token_for_bg('var(--bds-brand-700-normal)')
    check('U-313c', 'brand token unchanged',
          result3 == 'var(--bds-brand-700-normal)')

    # Plain hex → unchanged
    result4 = _revert_text_token_for_bg('#ffffff')
    check('U-313d', 'hex unchanged', result4 == '#ffffff')


def test_bug18_semicolon_fid_gets_wrapper():
    """U-314: 实例路径 ID（含分号）的节点也应获得 wrapper div（当 class 不同时）。"""
    from lib.tsx_generator import render_jsx_body_with_leaf_refs

    # IR node with semicolon figmaId and different className from rootClassName
    ir = {
        'figmaId': 'I100:200;300:400',
        'figmaName': 'Button Instance',
        'figmaType': 'INSTANCE',
        'css': {'margin-top': '615px'},
        'children': [],
        'semantic': {
            'htmlTag': 'div',
            'className': 'btn-instance-class',
            'componentName': None,
            'isExtractedComponent': False,
            'props': [],
        },
    }

    leaf_map = {
        'I100:200;300:400': {
            'componentName': 'MyButton',
            'varyingProps': [{'propName': 'text', 'type': 'text', 'values': ['Click']}],
            'instanceData': {'text': 'Click'},
            'rootClassName': 'btn-template-class',  # DIFFERENT from node's className
            'isMoly': False,
        }
    }

    result = render_jsx_body_with_leaf_refs(ir, leaf_map)

    check('U-314a', 'wrapper div rendered (className present)',
          "styles['btn-instance-class']" in result)
    check('U-314b', 'component rendered inside wrapper',
          '<MyButton' in result)
    check('U-314c', 'prop passed to component',
          'text="Click"' in result)


def test_bug18_same_class_no_wrapper():
    """U-315: 当 className == rootClassName 时，不渲染 wrapper（避免双层）。"""
    from lib.tsx_generator import render_jsx_body_with_leaf_refs

    ir = {
        'figmaId': 'I100:200;300:400',
        'figmaName': 'Button',
        'figmaType': 'INSTANCE',
        'css': {},
        'children': [],
        'semantic': {
            'htmlTag': 'div',
            'className': 'same-class',
            'componentName': None,
            'isExtractedComponent': False,
            'props': [],
        },
    }

    leaf_map = {
        'I100:200;300:400': {
            'componentName': 'MyButton',
            'varyingProps': [],
            'instanceData': {},
            'rootClassName': 'same-class',  # SAME → no wrapper
            'isMoly': False,
        }
    }

    result = render_jsx_body_with_leaf_refs(ir, leaf_map)

    check('U-315a', 'no wrapper div when same class',
          "styles['same-class']" not in result)
    check('U-315b', 'component rendered directly',
          '<MyButton ' in result or '<MyButton/>' in result)


def test_revert_respects_dark_mode_value():
    """raw_hex 与 token dark 模式值一致时不 revert。
    bds-gray-t1-title dark 模式值=#ffffff，设计稿 rawBgHex=#ffffff → 不 revert"""
    from lib.token_resolver import _revert_text_token_for_bg
    from lib.css_extractor import _revert_text_token_for_bg as _css_revert

    # token_resolver version
    result = _revert_text_token_for_bg('var(--bds-gray-t1-title)', '#ffffff')
    check('U-316a', 'token_resolver: dark value #ffffff → no revert',
          result == 'var(--bds-gray-t1-title)')

    # css_extractor version
    result2 = _css_revert('var(--bds-gray-t1-title)', '#ffffff')
    check('U-316b', 'css_extractor: dark value #ffffff → no revert',
          result2 == 'var(--bds-gray-t1-title)')

    # light value match → also no revert
    result3 = _revert_text_token_for_bg('var(--bds-gray-t1-title)', '#121214')
    check('U-316c', 'light value #121214 → no revert',
          result3 == 'var(--bds-gray-t1-title)')

    # Neither light nor dark → revert
    result4 = _revert_text_token_for_bg('var(--bds-gray-t1-title)', '#888888')
    check('U-316d', 'unmatched hex → revert',
          'bds-gray-t1-title-revert' in result4)

    # No raw_hex → revert (fallback behavior)
    result5 = _revert_text_token_for_bg('var(--bds-gray-t1-title)', '')
    check('U-316e', 'empty raw_hex → revert',
          'bds-gray-t1-title-revert' in result5)


def test_u420_raw_hex_injected_as_var_fallback():
    """U-420: _render_rule 在 color/background-color 用 var(--bds-...) 且存在
    _rawColorHex/_rawBgHex 时，应在 var() 中注入 raw hex 作为 fallback。

    场景：Hero 标题 color: var(--bds-gray-t1-title)，_rawColorHex=#ffffff。
    在 BDS token 未加载的 dev 环境中，var() 无 fallback → 颜色不生效 → 文字不可见。
    修复后输出 color: var(--bds-gray-t1-title, #ffffff) → dev 环境也可见。
    """
    from lib.scss_generator import _render_rule

    css_color = {
        'font-size': '40px',
        'color': 'var(--bds-gray-t1-title)',
        '_rawColorHex': '#ffffff',
    }
    result = _render_rule('hero-title', css_color)
    check('U-420a', 'color var() gets raw hex fallback',
          'var(--bds-gray-t1-title, #ffffff)' in result)

    css_bg = {
        'background-color': 'var(--bds-brand-700-normal)',
        '_rawBgHex': '#ff9c2e',
    }
    result_bg = _render_rule('cta-btn', css_bg)
    check('U-420b', 'background-color var() gets raw hex fallback',
          'var(--bds-brand-700-normal, #ff9c2e)' in result_bg)

    # Already has fallback → should NOT double-inject
    css_already = {
        'color': 'var(--bds-gray-t1-title, #aabbcc)',
        '_rawColorHex': '#ffffff',
    }
    result_already = _render_rule('existing-fallback', css_already)
    check('U-420c', 'existing fallback not replaced',
          'var(--bds-gray-t1-title, #aabbcc)' in result_already)

    # color-mix(... var(...) ...) → should NOT be modified
    css_mix = {
        'background-color': 'color-mix(in srgb, var(--bds-trans-hover) 12%, transparent)',
        '_rawBgHex': '#121214',
    }
    result_mix = _render_rule('mix-node', css_mix)
    check('U-420d', 'color-mix background not modified',
          'color-mix(in srgb, var(--bds-trans-hover) 12%, transparent)' in result_mix)


def test_u456_ext_relative_inst_abs_template_needs_wrapper():
    """U-456-ext: 实例 position:relative、模板 position:absolute 时应生成 wrapper div。

    根因：BtcDark 组件根元素是 .background（position:absolute; width:100%; height:100%），
    实例 .btc-dark-n311-14974 是 position:relative; width:48px; height:48px。
    split_codegen 在 U-452 路径中误将 effective_root_cls 设为 inst_cls，
    导致 tsx_generator 看到 inst_cls == root_cls 而跳过 wrapper div。
    修复后 rootClassName 保持为真实的组件根类（btc--dark），wrapper 正常生成。
    """
    from lib.tsx_generator import render_jsx_body_with_leaf_refs

    # IR: 容器 frame（flex row），内含 BtcDark 实例 + text span
    ir = {
        'figmaId': '311:14973',
        'figmaName': 'Frame 2147229802',
        'figmaType': 'FRAME',
        'css': {'display': 'flex', 'flex-direction': 'row', 'align-items': 'center', 'gap': '20px', 'position': 'relative'},
        'children': [
            {
                'figmaId': '311:14974',
                'figmaName': 'BTC -DARK',
                'figmaType': 'INSTANCE',
                'css': {'width': '48px', 'height': '48px', 'flex-shrink': '0', 'position': 'relative'},
                'children': [],
                'semantic': {
                    'htmlTag': 'div',
                    'className': 'btc-dark-n311-14974',
                    'componentName': None,
                    'isExtractedComponent': False,
                    'props': [],
                },
            },
            {
                'figmaId': '311:14975',
                'figmaName': 'text',
                'figmaType': 'TEXT',
                'css': {'color': '#000', 'font-size': '24px'},
                'children': [],
                'semantic': {
                    'htmlTag': 'span',
                    'className': 'n10011',
                    'componentName': None,
                    'isExtractedComponent': False,
                    'props': [],
                },
                'textContent': '0.00152462',
            },
        ],
        'semantic': {
            'htmlTag': 'div',
            'className': 'frame-2147229801',
            'componentName': None,
            'isExtractedComponent': False,
            'props': [],
        },
    }

    # leaf_map: BtcDark 的 rootClassName 是真实的组件根类（fix 后 split_codegen 不再改成 inst_cls）
    leaf_map = {
        '311:14974': {
            'componentName': 'BtcDark',
            'varyingProps': [{'propName': 'btcDark', 'type': 'node', 'values': [None, None]}],
            'instanceData': {'btcDark': None},
            'rootClassName': 'btc--dark',   # 真实组件根类（≠ inst_cls）→ 应生成 wrapper
            'isMoly': False,
            'ccComponent': None,
        },
    }

    jsx = render_jsx_body_with_leaf_refs(ir, leaf_map)

    # wrapper div with btc-dark-n311-14974 must wrap BtcDark
    has_wrapper = (
        "styles['btc-dark-n311-14974']" in jsx
        and '<BtcDark' in jsx
    )
    check('U-456-ext-a', 'wrapper div btc-dark-n311-14974 is generated', has_wrapper)

    # BtcDark should be INSIDE the wrapper (not at top level of flex row)
    wrapper_idx = jsx.find("styles['btc-dark-n311-14974']")
    btc_dark_idx = jsx.find('<BtcDark')
    check('U-456-ext-b', 'BtcDark appears after wrapper open tag', btc_dark_idx > wrapper_idx)

    # text span must still be present
    check('U-456-ext-c', 'text span n10011 is rendered', "styles['n10011']" in jsx)


# ── Run ──
print('\n── BUG-15/18/46 补充单测 ──')
test_bug15_text_token_revert_for_background()
test_bug18_semicolon_fid_gets_wrapper()
test_bug18_same_class_no_wrapper()
test_revert_respects_dark_mode_value()
test_u420_raw_hex_injected_as_var_fallback()
test_u456_ext_relative_inst_abs_template_needs_wrapper()
print_summary('split/test_token_revert_and_wrapper')
