#!/usr/bin/env python3
"""
Section 根节点 CSS 剥离画布定位属性。

根因：Figma Canvas 中 Section 子节点有 position:absolute + top/left + 固定 height，
这些是设计画布坐标，拆分为独立组件后由 Page 布局控制，Section 根 class 不应保留。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()

from lib.split_codegen import _css_from_orig, _collect_responsive_overrides_from_ir


def _make_section_ir(cls_name, css):
    return {
        'figmaId': '1:1',
        'semantic': {'htmlTag': 'div', 'className': cls_name, 'componentName': 'Test', 'props': []},
        'css': css,
        'children': [],
    }


def test_section_root_strips_absolute_positioning():
    """SEC-01: Section 根节点 position:absolute + top/left/height 被剥离。"""
    ir = _make_section_ir('frame-root', {
        'width': '375px', 'height': '706px', 'position': 'absolute',
        'left': '0px', 'top': '88px',
        'display': 'flex', 'flex-direction': 'column',
    })
    orig = {'frame-root': {
        'width': '375px', 'height': '706px', 'position': 'absolute',
        'left': '0px', 'top': '88px',
        'display': 'flex', 'flex-direction': 'column', 'align-items': 'flex-start',
        'z-index': '1',
    }}
    result = _css_from_orig(ir, orig, 'less', is_section_root=True)

    check('SEC-01a', 'no position:absolute', 'position: absolute' not in result)
    # "top: 88px" as substring exists in "margin-top: 88px", so check line-level
    lines = [l.strip() for l in result.split('\n')]
    check('SEC-01b', 'no standalone top prop', 'top: 88px;' not in lines and 'margin-top: 88px;' in lines)
    check('SEC-01c', 'no left', 'left:' not in result and 'left :' not in result)
    check('SEC-01d', 'no fixed height', 'height: 706px' not in result)
    check('SEC-01e', 'has position:relative', 'position: relative' in result)
    check('SEC-01f', 'width preserved', 'width: 375px' in result)
    check('SEC-01g', 'display preserved', 'display: flex' in result)


def test_section_root_no_strip_when_relative():
    """SEC-02: Section 根节点 position:relative 不被剥离。"""
    ir = _make_section_ir('frame-root', {
        'width': '375px', 'height': '400px', 'position': 'relative',
        'display': 'flex', 'flex-direction': 'column',
    })
    orig = {'frame-root': {
        'width': '375px', 'height': '400px', 'position': 'relative',
        'display': 'flex', 'flex-direction': 'column',
    }}
    result = _css_from_orig(ir, orig, 'less', is_section_root=True)

    check('SEC-02a', 'position:relative preserved', 'position: relative' in result)
    check('SEC-02b', 'height preserved (not absolute)', 'height: 400px' in result)


def test_section_root_no_strip_when_flag_false():
    """SEC-03: is_section_root=False 时不剥离（默认行为不变）。"""
    ir = _make_section_ir('frame-root', {
        'width': '375px', 'height': '706px', 'position': 'absolute',
        'left': '0px', 'top': '88px',
        'display': 'flex',
    })
    orig = {'frame-root': {
        'width': '375px', 'height': '706px', 'position': 'absolute',
        'left': '0px', 'top': '88px', 'display': 'flex',
    }}
    result = _css_from_orig(ir, orig, 'less', is_section_root=False)

    check('SEC-03a', 'position:absolute preserved', 'position: absolute' in result)
    check('SEC-03b', 'top preserved', 'top: 88px' in result)
    check('SEC-03c', 'height preserved', 'height: 706px' in result)


def test_section_root_preserves_child_css():
    """SEC-04: 只影响根 class，子 class 的 absolute 不被剥离。"""
    ir = {
        'figmaId': '1:1',
        'semantic': {'htmlTag': 'div', 'className': 'root-cls', 'componentName': 'T', 'props': []},
        'css': {'position': 'absolute', 'top': '88px', 'display': 'flex'},
        'children': [{
            'figmaId': '1:2',
            'semantic': {'htmlTag': 'div', 'className': 'child-cls', 'props': []},
            'css': {'position': 'absolute', 'top': '20px', 'width': '100px'},
            'children': [],
        }],
    }
    orig = {
        'root-cls': {'position': 'absolute', 'top': '88px', 'display': 'flex'},
        'child-cls': {'position': 'absolute', 'top': '20px', 'width': '100px'},
    }
    result = _css_from_orig(ir, orig, 'less', is_section_root=True)

    # root 的 top:88px 变为 margin-top:88px，原始 "top: 88px" 不再存在
    root_block = result.split('child-cls')[0]
    check('SEC-04a', 'root top→margin-top', 'margin-top: 88px' in root_block and 'position: absolute' not in root_block)
    check('SEC-04b', 'child absolute preserved', 'position: absolute' in result.split('child-cls')[1])
    check('SEC-04c', 'child top preserved', 'top: 20px' in result.split('child-cls')[1])


def test_section_root_top_becomes_margin_top():
    """SEC-05: top 值转为 margin-top 保留垂直偏移。"""
    ir = _make_section_ir('frame-root', {
        'width': '375px', 'height': '706px', 'position': 'absolute',
        'left': '0px', 'top': '88px', 'display': 'flex',
    })
    orig = {'frame-root': {
        'width': '375px', 'height': '706px', 'position': 'absolute',
        'left': '0px', 'top': '88px', 'display': 'flex',
    }}
    result = _css_from_orig(ir, orig, 'less', is_section_root=True)

    check('SEC-05a', 'margin-top: 88px injected', 'margin-top: 88px' in result)
    # "top: 88px" as standalone prop is gone; only "margin-top: 88px" remains
    lines = [l.strip() for l in result.split('\n')]
    check('SEC-05b', 'top as standalone removed', 'top: 88px;' not in lines)


def test_section_root_top_zero_no_margin():
    """SEC-06: top=0px 时不生成 margin-top。"""
    ir = _make_section_ir('frame-root', {
        'width': '375px', 'position': 'absolute', 'top': '0px', 'display': 'flex',
    })
    orig = {'frame-root': {
        'width': '375px', 'position': 'absolute', 'top': '0px', 'display': 'flex',
    }}
    result = _css_from_orig(ir, orig, 'less', is_section_root=True)

    check('SEC-06a', 'no margin-top for top:0px', 'margin-top' not in result)


def test_moly_text_override_collapse():
    """SEC-07: _generate_cc_text_overrides 为 Collapse 生成字号+布局+间距覆写。"""
    from lib.split_codegen import _generate_cc_text_overrides
    overrides = [('faq-expandable', 'Collapse', '10.56px', '15.25px', '9.38px', '14.07px', '14.07px', '7.04px')]
    result = _generate_cc_text_overrides(overrides)

    check('SEC-07a', 'contains class selector', '.faq-expandable {' in result)
    check('SEC-07b', 'title font-size override', 'font-size: 10.56px' in result)
    check('SEC-07c', 'content font-size override', 'font-size: 9.38px' in result)
    check('SEC-07d', ':global(.moly-collapse h3 button)', ':global(.moly-collapse h3 button)' in result)
    check('SEC-07e', ':global(.moly-collapse [role="region"])', ':global(.moly-collapse [role="region"] > div)' in result)
    check('SEC-07f', 'icon right-align: justify-content', 'justify-content: space-between' in result)
    check('SEC-07g', 'button full width', 'width: 100%' in result)
    check('SEC-07h', 'icon flex-shrink:0', 'flex-shrink: 0' in result)
    check('SEC-07i', 'item gap from design', 'gap: 14.07px' in result)
    check('SEC-07j', 'h3 padding from content_gap', '7.04px' in result)


def test_moly_text_override_tabs():
    """SEC-08: _generate_cc_text_overrides 为 Tabs 生成字号覆写。"""
    from lib.split_codegen import _generate_cc_text_overrides
    overrides = [('tab-cls', 'Tabs', '12px', '16px', None, None)]
    result = _generate_cc_text_overrides(overrides)

    check('SEC-08a', 'contains class selector', '.tab-cls {' in result)
    check('SEC-08b', 'tab button font-size', 'font-size: 12px' in result)
    check('SEC-08c', ':global(button[role="tab"])', ':global(button[role="tab"])' in result)


def test_extract_moly_text_sizes():
    """SEC-09: _extract_moly_text_sizes 从 IR 提取 Collapse 子节点字号+间距。"""
    from lib.split_codegen import _extract_moly_text_sizes
    ir_node = {
        'figmaId': '1:1',
        'css': {'gap': '14px', 'display': 'flex'},
        'children': [{
            'figmaId': '1:2',
            'css': {'gap': '7px'},
            'children': [{
                'figmaId': '1:3',
                'isTextNode': True,
                'css': {'font-size': '11px', 'line-height': '16px'},
                'children': [],
            }, {
                'figmaId': '1:4',
                'isTextNode': True,
                'css': {'font-size': '9px', 'line-height': '13px'},
                'children': [],
            }],
        }],
    }
    t_fs, t_lh, c_fs, c_lh, i_gap, c_gap = _extract_moly_text_sizes(ir_node)
    check('SEC-09a', 'title font-size', t_fs == '11px')
    check('SEC-09b', 'title line-height', t_lh == '16px')
    check('SEC-09c', 'content font-size', c_fs == '9px')
    check('SEC-09d', 'content line-height', c_lh == '13px')
    check('SEC-09e', 'item gap', i_gap == '14px')
    check('SEC-09f', 'content gap', c_gap == '7px')


def test_text_override_wrapper_keeps_width():
    """SEC-10: Collapse/Tabs 的 text_override wrapper 应保留 width 约束。
    数据来自 202:33820 FAQ Collapse（宽度 1200px），父容器 align-items:center 时
    子元素不会自动撑满，需要 wrapper 保留 width。
    """
    from lib.split_codegen import _MOLY_TEXT_OVERRIDE_POSITIONING_PROPS
    check('SEC-10a', 'width in TEXT_OVERRIDE positioning props',
          'width' in _MOLY_TEXT_OVERRIDE_POSITIONING_PROPS)
    check('SEC-10b', 'max-width in TEXT_OVERRIDE positioning props',
          'max-width' in _MOLY_TEXT_OVERRIDE_POSITIONING_PROPS)
    check('SEC-10c', 'min-width in TEXT_OVERRIDE positioning props',
          'min-width' in _MOLY_TEXT_OVERRIDE_POSITIONING_PROPS)


def test_u442_section_root_responsive_absolute_stripped():
    """U-442: Section root 的 H5 responsive[] 覆盖中 position:absolute 被剥离，
    不产生脱离文档流的 @media 样式。

    Root cause: DemoTrading AdvantagesSection (202:33619) 的 H5 supplement
    Frame 1410119543 在 Figma 中 position:absolute;top:88px;height:706px，
    被写入 responsive[] 传到 @media 块，导致 H5 视图下整个 section 绝对定位
    脱离文档流、高度截断至 706px，H5 内容全部不可见。

    Real data: node 202:33619 from DemoTrading (merged-202-33464)
    """
    from lib.split_codegen import _strip_section_root_responsive_props

    # Real data from merged IR node 202:33619 responsive[0].css
    resp_css = {
        'height': '706px',
        'left': '0px',
        'padding': '0',
        'width': '375px',
        'position': 'absolute',
        'top': '88px',
        'align-items': 'flex-start',
    }
    result = _strip_section_root_responsive_props(resp_css)

    check('U-442a', 'position:absolute removed', result.get('position') != 'absolute')
    check('U-442b', 'position:relative injected', result.get('position') == 'relative')
    check('U-442c', 'height stripped', 'height' not in result)
    check('U-442d', 'left stripped', 'left' not in result)
    check('U-442e', 'top stripped', 'top' not in result)
    check('U-442f', 'fixed px width stripped', 'width' not in result)
    check('U-442g', 'padding preserved', result.get('padding') == '0')
    check('U-442h', 'align-items preserved', result.get('align-items') == 'flex-start')


def test_u442_section_root_responsive_relative_unchanged():
    """U-442 no-op: position:relative 的 responsive 覆盖保持不变。"""
    from lib.split_codegen import _strip_section_root_responsive_props

    resp_css = {
        'background-color': '#000000',
        'position': 'relative',
        'width': '100%',
    }
    result = _strip_section_root_responsive_props(resp_css)
    check('U-442i', 'position:relative unchanged', result.get('position') == 'relative')
    check('U-442j', 'width 100% unchanged', result.get('width') == '100%')
    check('U-442k', 'background-color preserved', result.get('background-color') == '#000000')


def test_u442_section_root_responsive_no_position_unchanged():
    """U-442 no-op: 无 position 的 responsive 覆盖保持不变（如 margin 调整）。"""
    from lib.split_codegen import _strip_section_root_responsive_props

    resp_css = {'margin-left': '0px', 'margin-top': '0px'}
    result = _strip_section_root_responsive_props(resp_css)
    check('U-442l', 'margin-left preserved', result.get('margin-left') == '0px')
    check('U-442m', 'margin-top preserved', result.get('margin-top') == '0px')
    check('U-442n', 'no position injected', 'position' not in result)


def test_u446_abs_positioned_node_responsive_height_stripped():
    """U-446: _collect_responsive_overrides_from_ir must NOT emit 'height' in the
    @media block for a node whose base CSS has position:absolute AND whose
    immediate children include pcOnly/h5Only variants.

    Root cause: Tomorrowland GatewayToTheSection festival-benefits-col
    (I39641:9277;39641:6279) has base position:absolute, H5 responsive
    override height:753px, AND pcOnly/h5Only immediate children.
    After the merge the PC photo gallery is hidden (pcOnly), so the
    753px height creates a large blank orange area.

    Fix: in _collect_responsive_overrides_from_ir, when node base CSS has
    position:absolute AND has immediate pcOnly/h5Only children AND responsive
    override includes fixed px height, strip that height.

    Real data: node I39641:9277;39641:6279 from Tomorrowland (merged-39641-6863).
    """
    # Real data: base CSS has position:absolute, has pcOnly + h5Only children,
    # responsive has height:753px
    node = {
        'figmaId': 'I39641:9277;39641:6279',
        'figmaName': 'Frame 2147224634',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'css': {
            'width': '100%',
            'flex-direction': 'column',
            'position': 'absolute',
            'align-items': 'center',
            'top': '80px',
            'gap': '40px',
            'left': '120px',
            'display': 'flex',
            'max-width': '1200px',
        },
        'responsive': [{
            'breakpoint': 768,
            'css': {
                'height': '753px',
                'top': '127.09px',
                'gap': '32px',
                'left': '20px',
            },
            'confidence': 1.0,
        }],
        # Real children: pcOnly text + h5Only replacement (from merged-39641-6863)
        'children': [
            {'figmaId': 'I39641:9277;39641:6280', 'figmaName': 'Gateway...', 'pcOnly': True,
             'css': {}, 'children': []},
            {'figmaId': 'I39648:4932;39603:19889', 'figmaName': 'Frame 1410117602', 'h5Only': True,
             'css': {}, 'children': []},
            {'figmaId': 'I39641:9277;39641:6281', 'figmaName': 'Frame 2147224633',
             'css': {}, 'children': []},
        ],
    }
    fid_cls_map = {'I39641:9277;39641:6279': 'festival-benefits-col'}

    overrides = _collect_responsive_overrides_from_ir(node, fid_cls_map, set())

    assert len(overrides) == 1, f'U-446: expected 1 override, got {len(overrides)}'
    cls, bp, css = overrides[0]
    assert cls == 'festival-benefits-col'
    assert bp == 768

    check('U-446a', 'height stripped from abs-pos node responsive override',
          'height' not in css)
    check('U-446b', 'top preserved (positioning update is valid)',
          css.get('top') == '127.09px')
    check('U-446c', 'gap preserved (layout spacing is valid)',
          css.get('gap') == '32px')


def test_u446_h5_makes_absolute_with_height_stripped():
    """U-446 extended case B: when base CSS has position:relative but the H5
    responsive override changes it to position:absolute AND adds height:px,
    strip the height. This covers I39641:7672;39641:5102 (vip-content-row) whose
    base is relative but H5 makes it absolute with height:682px, creating blank
    space when pcOnly children (PC-only visual) are hidden on H5.

    Real data: node I39641:7672;39641:5102 from Tomorrowland (merged-39641-6863).
    """
    # Real data: base is relative (no height), H5 responsive adds position:absolute + height
    node = {
        'figmaId': 'I39641:7672;39641:5102',
        'figmaName': 'Frame 2147223767',
        'css': {
            'position': 'relative',    # ← base is relative, NOT absolute
            'width': '100%',
            'flex-direction': 'row',
        },
        'responsive': [{
            'breakpoint': 768,
            'css': {
                'position': 'absolute',   # ← H5 switches to absolute
                'height': '682px',        # ← H5 adds fixed height
                'overflow': 'hidden',
                'flex-direction': 'column',
            },
            'confidence': 1.0,
        }],
        'children': [
            {'figmaId': 'child-pc', 'pcOnly': True, 'css': {}, 'children': []},   # PC-only card
            {'figmaId': 'child-h5', 'h5Only': True, 'css': {}, 'children': []},   # H5 text (h5Only)
        ],
    }
    fid_cls_map = {'I39641:7672;39641:5102': 'vip-content-row'}
    overrides = _collect_responsive_overrides_from_ir(node, fid_cls_map, set())

    assert len(overrides) == 1
    _, _, css = overrides[0]

    check('U-446h', 'H5-makes-absolute height stripped', 'height' not in css)
    check('U-446i', 'position:absolute kept (layout needs it)', css.get('position') == 'absolute')
    check('U-446j', 'overflow:hidden kept', css.get('overflow') == 'hidden')
    check('U-446k', 'flex-direction kept', css.get('flex-direction') == 'column')


def test_u446_base_height_exceeded_by_h5_stripped():
    """U-446 case C: base CSS has explicit height but H5 override EXCEEDS it.
    When H5 height > PC height and node has pcOnly/h5Only children, strip H5 height.

    Real data: I39641:7672;39641:5102 (vip-content-row) from Tomorrowland.
    After _expand_ir_max_css: base height=494px. H5 responsive height=682px (>494).
    The extra 188px creates blank space when the PC-only card is hidden on H5.
    """
    node = {
        'figmaId': 'I39641:7672;39641:5102',
        'figmaName': 'Frame 2147223767',
        'css': {
            'position': 'relative',
            'height': '494px',          # ← base HAS explicit height (from _expand_ir_max_css)
            'width': '100%',
        },
        'responsive': [{
            'breakpoint': 768,
            'css': {
                'position': 'absolute',
                'height': '682px',      # ← H5/PC = 682/494 = 1.38x >= 1.35 threshold → strip
                'overflow': 'hidden',
                'flex-direction': 'column',
            },
            'confidence': 1.0,
        }],
        'children': [
            {'figmaId': 'vip-card', 'pcOnly': True, 'css': {}, 'children': []},
            {'figmaId': 'vip-h5', 'h5Only': True, 'css': {}, 'children': []},
        ],
    }
    fid_cls_map = {'I39641:7672;39641:5102': 'vip-content-row'}
    overrides = _collect_responsive_overrides_from_ir(node, fid_cls_map, set())

    assert len(overrides) == 1
    _, _, css = overrides[0]
    # U-448 correction: base has explicit height (494px) + overflow:hidden → preserve H5
    # height (682px) so coming-soon-row at y≈516px is not clipped by inherited 494px.
    check('U-446l', 'H5 height preserved: overflow:hidden + base has explicit height', css.get('height') == '682px')
    check('U-446m', 'position:absolute kept', css.get('position') == 'absolute')
    check('U-446n', 'overflow:hidden kept', css.get('overflow') == 'hidden')


def test_u446_small_h5_upscale_below_ratio_threshold_kept():
    """U-446 case C no-op: H5 height > PC height but ratio < 1.35x — H5 content
    genuinely needs the extra space. Do NOT strip.

    Real data: 39641:9382 (ExampleLandingSection) from Tomorrowland.
    base=546px, H5=654px → 654/546=1.20x < 1.35 threshold → keep.
    The H5 section is larger because the H5 festival photo + text layout needs more
    space, not because pcOnly content is hidden.
    """
    node = {
        'figmaId': '39641:9382',
        'figmaName': 'ExampleLandingPage',
        'css': {'position': 'absolute', 'height': '546px', 'width': '100%'},
        'responsive': [{
            'breakpoint': 768,
            'css': {'height': '654px', 'top': '3021px'},  # 654/546=1.20x < 1.35 → keep
            'confidence': 1.0,
        }],
        'children': [
            {'figmaId': 'example-pc', 'pcOnly': True, 'css': {}, 'children': []},
            {'figmaId': 'example-h5', 'h5Only': True, 'css': {}, 'children': []},
        ],
    }
    fid_cls_map = {'39641:9382': 'example-section'}
    overrides = _collect_responsive_overrides_from_ir(node, fid_cls_map, set())
    assert len(overrides) == 1
    _, _, css = overrides[0]
    check('U-446o', 'small ratio H5 upscale kept (1.20x < 1.35 threshold)',
          css.get('height') == '654px')


def test_u446_abs_positioned_no_variant_children_height_kept():
    """U-446 no-op: abs-pos node WITHOUT pcOnly/h5Only children keeps height."""
    node = {
        'figmaId': 'fake:0',
        'css': {'position': 'absolute', 'top': '0px', 'width': '300px'},
        'responsive': [{'breakpoint': 768, 'css': {'height': '200px', 'top': '50px'}, 'confidence': 1.0}],
        # No pcOnly/h5Only children — content is the same on both platforms
        'children': [
            {'figmaId': 'fake:0:1', 'css': {}, 'children': []},
        ],
    }
    fid_cls_map = {'fake:0': 'step-container'}
    overrides = _collect_responsive_overrides_from_ir(node, fid_cls_map, set())
    assert len(overrides) == 1
    _, _, css = overrides[0]
    check('U-446f', 'abs-pos without pcOnly/h5Only children keeps height', 'height' in css)


def test_u446_abs_with_base_height_downscale_kept():
    """U-446 no-op: abs-pos node WITH base CSS height whose H5 override reduces height
    (legitimate downscale like PC 837px → H5 566px) must NOT have height stripped.

    Real data: node 39641:9277 (GatewayToTheSection) from Tomorrowland.
    base CSS has height:837px; H5 override has height:566px (smaller) → keep.
    """
    # Real data: 39641:9277 from Tomorrowland (merged-39641-6863)
    node = {
        'figmaId': '39641:9277',
        'figmaName': 'Frame 2147224635',
        'css': {
            'width': '100%',
            'height': '837px',           # ← base HAS explicit height
            'position': 'absolute',
            'overflow': 'hidden',
            'top': '1595px',
        },
        'responsive': [{
            'breakpoint': 768,
            'css': {
                'height': '566px',       # ← smaller than 837px: legitimate downscale
                'top': '1346.98px',
                'background-color': 'transparent',
            },
            'confidence': 1.0,
        }],
        # These children exist but since base CSS HAS height, downscale is kept
        'children': [
            {'figmaId': '39641:9277-child1', 'pcOnly': True, 'css': {}, 'children': []},
            {'figmaId': '39641:9277-child2', 'h5Only': True, 'css': {}, 'children': []},
        ],
    }
    fid_cls_map = {'39641:9277': 'gateway-section'}
    overrides = _collect_responsive_overrides_from_ir(node, fid_cls_map, set())
    assert len(overrides) == 1
    _, _, css = overrides[0]
    check('U-446g', 'abs-pos with base height: H5 downscale height is kept',
          css.get('height') == '566px')


def test_u446_relative_positioned_node_height_kept():
    """U-446 no-op: position:relative node keeps height in responsive override."""
    node = {
        'figmaId': 'fake:1',
        'css': {'position': 'relative', 'width': '100%'},
        'responsive': [{'breakpoint': 768, 'css': {'height': '400px'}, 'confidence': 1.0}],
        'children': [],
    }
    fid_cls_map = {'fake:1': 'my-container'}
    overrides = _collect_responsive_overrides_from_ir(node, fid_cls_map, set())
    assert len(overrides) == 1
    _, _, css = overrides[0]
    check('U-446d', 'position:relative node keeps height override', 'height' in css)


def test_u446_no_position_node_height_kept():
    """U-446 no-op: node without position in base CSS keeps height override."""
    node = {
        'figmaId': 'fake:2',
        'css': {'width': '100%', 'display': 'flex'},
        'responsive': [{'breakpoint': 768, 'css': {'height': '500px', 'gap': '16px'}, 'confidence': 1.0}],
        'children': [],
    }
    fid_cls_map = {'fake:2': 'flow-container'}
    overrides = _collect_responsive_overrides_from_ir(node, fid_cls_map, set())
    assert len(overrides) == 1
    _, _, css = overrides[0]
    check('U-446e', 'node without position keeps height override', 'height' in css)


def test_u447_h5only_wrapper_height_rule_abs_with_height():
    """U-447: h5-only wrapper must get position:relative + height when subsection root is absolute.
    Real case: TomorrowlandLandingPage4 HeroSection root has position:absolute + height:630px,
    causing h5-only-hero-section wrapper to collapse to height:0.
    """
    from lib.split_codegen import _h5only_wrapper_height_rule

    css = {
        'width': '393px',
        'height': '630px',
        'position': 'absolute',
        'left': '0px',
        'top': '0px',
        'background-color': '#101014',
        'overflow': 'hidden',
    }
    result = _h5only_wrapper_height_rule(css)
    check('U-447a', 'position:relative in wrapper rule', 'position: relative' in result)
    check('U-447b', 'height:630px in wrapper rule', 'height: 630px' in result)
    check('U-447c', 'overflow:hidden propagated', 'overflow: hidden' in result)


def test_u447_h5only_wrapper_height_rule_relative_noop():
    """U-447: no-op when subsection root is position:relative."""
    from lib.split_codegen import _h5only_wrapper_height_rule

    css = {'width': '100%', 'position': 'relative', 'height': '630px'}
    result = _h5only_wrapper_height_rule(css)
    check('U-447d', 'relative root returns empty string', result == '')


def test_u447_h5only_wrapper_height_rule_no_height_noop():
    """U-447: no-op when position:absolute but no fixed px height."""
    from lib.split_codegen import _h5only_wrapper_height_rule

    css = {'width': '100%', 'position': 'absolute', 'top': '0px'}
    result = _h5only_wrapper_height_rule(css)
    check('U-447e', 'abs without height returns empty string', result == '')


def test_u453_flex_row_to_column_child_width_overflow():
    """U-453: when parent responsive CSS changes flex-direction to column,
    a direct child with fixed px width > 390px and no responsive CSS should get
    width:100% emitted.

    Real case: ExampleLandingSection benefitIconLabel (I39641:9382;39641:6697)
    changes from flex-row to column on H5. Its child header-text-cta has width:540px
    with no responsive override → overflows 390px viewport.
    """
    parent = {
        'figmaId': 'I39641:9382;39641:6697',
        'figmaName': 'benefitIconLabel',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'css': {
            'width': '100%',
            'display': 'flex',
            'flex-direction': 'row',
            'position': 'relative',
            'margin-top': '80px',
            'gap': '80px',
        },
        'responsive': [{
            'breakpoint': 768,
            'css': {
                'position': 'absolute',
                'margin-top': '0px',
                'left': '20px',
                'flex-direction': 'column',
                'top': '31.21px',
                'gap': '24px',
            },
            'confidence': 1.0,
        }],
        'children': [{
            'figmaId': 'I39641:9382;39641:6690',
            'figmaName': 'Header Text CTA',
            'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'css': {'width': '540px', 'height': '265px', 'position': 'relative', 'display': 'flex'},
            'responsive': None,
            'children': [],
        }],
    }
    fid_cls_map = {
        'I39641:9382;39641:6697': 'benefitIconLabel-I39641-9382-39641-6697',
        'I39641:9382;39641:6690': 'header-text-cta',
    }
    results = _collect_responsive_overrides_from_ir(parent, fid_cls_map, set())
    cls_to_css = {cls: css for cls, bp, css in results}
    check('U-453a', 'parent responsive override emitted', 'benefitIconLabel-I39641-9382-39641-6697' in cls_to_css)
    check('U-453b', 'header-text-cta gets width:100% override', cls_to_css.get('header-text-cta', {}).get('width') == '100%')


def test_u453_child_already_has_responsive_no_duplicate():
    """U-453: when child already has responsive CSS, no width:100% should be added."""
    parent = {
        'figmaId': 'fid-parent',
        'css': {'display': 'flex', 'flex-direction': 'row', 'position': 'relative'},
        'responsive': [{'breakpoint': 768, 'css': {'flex-direction': 'column'}, 'confidence': 1.0}],
        'children': [{
            'figmaId': 'fid-child',
            'css': {'width': '600px', 'position': 'relative'},
            'responsive': [{'breakpoint': 768, 'css': {'width': '350px'}, 'confidence': 1.0}],
            'children': [],
        }],
    }
    fid_cls_map = {'fid-parent': 'parent-cls', 'fid-child': 'child-cls'}
    results = _collect_responsive_overrides_from_ir(parent, fid_cls_map, set())
    child_overrides = [(cls, bp, css) for cls, bp, css in results if cls == 'child-cls']
    # Child's own responsive CSS should be emitted, but NOT an extra width:100%
    child_widths = [css.get('width') for _, _, css in child_overrides]
    check('U-453c', 'child existing width override kept', '350px' in child_widths)
    check('U-453d', 'no extra width:100% added when child has responsive', '100%' not in child_widths)


def test_u453_child_absolute_no_width_override():
    """U-453: child with position:absolute should NOT get width:100% (it's canvas-positioned)."""
    parent = {
        'figmaId': 'fid-parent2',
        'css': {'display': 'flex', 'flex-direction': 'row', 'position': 'relative'},
        'responsive': [{'breakpoint': 768, 'css': {'flex-direction': 'column'}, 'confidence': 1.0}],
        'children': [{
            'figmaId': 'fid-child2',
            'css': {'width': '600px', 'position': 'absolute'},
            'responsive': None,
            'children': [],
        }],
    }
    fid_cls_map = {'fid-parent2': 'parent-cls2', 'fid-child2': 'child-cls2'}
    results = _collect_responsive_overrides_from_ir(parent, fid_cls_map, set())
    child_overrides = [(cls, bp, css) for cls, bp, css in results if cls == 'child-cls2']
    check('U-453e', 'absolute child does not get width:100%', not child_overrides)


def test_u453_parent_stays_row_no_width_override():
    """U-453: when parent responsive CSS does NOT change flex-direction to column, no fix."""
    parent = {
        'figmaId': 'fid-parent3',
        'css': {'display': 'flex', 'flex-direction': 'row', 'position': 'relative'},
        'responsive': [{'breakpoint': 768, 'css': {'gap': '16px'}, 'confidence': 1.0}],
        'children': [{
            'figmaId': 'fid-child3',
            'css': {'width': '600px', 'position': 'relative'},
            'responsive': None,
            'children': [],
        }],
    }
    fid_cls_map = {'fid-parent3': 'parent-cls3', 'fid-child3': 'child-cls3'}
    results = _collect_responsive_overrides_from_ir(parent, fid_cls_map, set())
    child_overrides = [(cls, bp, css) for cls, bp, css in results if cls == 'child-cls3']
    check('U-453f', 'child width not touched when parent stays row', not child_overrides)


def test_u456_grandchild_overflow_gets_max_width():
    """U-456: when a direct child gets width:100% from U-453, descendants with fixed
    px widths > 390px should also get max-width:100% to prevent overflow.

    Real case: ExampleLandingSection benefitIconLabel changes flex-row→column
    on H5; header-text-cta (540px) gets width:100%, but its grandchild
    a-new-rhythm-for-real-world-finance (534.18px) still overflows the 390px viewport.
    """
    parent = {
        'figmaId': 'I39641:9382;39641:6697',
        'figmaName': 'benefitIconLabel',
        'css': {
            'width': '100%',
            'display': 'flex',
            'flex-direction': 'row',
            'position': 'relative',
        },
        'responsive': [{
            'breakpoint': 768,
            'css': {'flex-direction': 'column'},
            'confidence': 1.0,
        }],
        'children': [{
            'figmaId': 'header-text-cta-fid',
            'figmaName': 'Header Text CTA',
            'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'css': {'width': '540px', 'position': 'relative', 'display': 'flex'},
            'responsive': None,
            'children': [{
                'figmaId': 'col-group-fid',
                'figmaName': 'colGroup',
                'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
                'responsive': None,
                'children': [{
                    'figmaId': 'a-new-rhythm-fid',
                    'figmaName': 'a new rhythm for real-world finance',
                    'figmaType': 'TEXT',
                    'isTextNode': True,
                    'css': {'width': '534.18px', 'color': '#000000', 'font-size': '40px'},
                    'responsive': None,
                    'children': [],
                }],
            }],
        }],
    }
    fid_cls_map = {
        'I39641:9382;39641:6697': 'benefitIconLabel-I39641-9382-39641-6697',
        'header-text-cta-fid': 'header-text-cta',
        'col-group-fid': 'col-group',
        'a-new-rhythm-fid': 'a-new-rhythm-for-real-world-finance',
    }
    results = _collect_responsive_overrides_from_ir(parent, fid_cls_map, set())
    cls_to_css = {cls: css for cls, bp, css in results}
    check('U-456a', 'header-text-cta gets width:100%',
          cls_to_css.get('header-text-cta', {}).get('width') == '100%')
    check('U-456b', 'grandchild a-new-rhythm gets max-width:100%',
          cls_to_css.get('a-new-rhythm-for-real-world-finance', {}).get('max-width') == '100%')


def test_u456_descendant_with_responsive_not_overridden():
    """U-456: descendants that already have their own responsive CSS should NOT get
    an extra max-width:100% from U-456 (same guard as U-453 for direct children)."""
    parent = {
        'figmaId': 'fid-p4',
        'css': {'display': 'flex', 'flex-direction': 'row', 'position': 'relative'},
        'responsive': [{'breakpoint': 768, 'css': {'flex-direction': 'column'}, 'confidence': 1.0}],
        'children': [{
            'figmaId': 'fid-c4',
            'css': {'width': '500px', 'position': 'relative'},
            'responsive': None,
            'children': [{
                'figmaId': 'fid-gc4',
                'css': {'width': '450px', 'position': 'relative'},
                'responsive': [{'breakpoint': 768, 'css': {'width': '300px'}, 'confidence': 1.0}],
                'children': [],
            }],
        }],
    }
    fid_cls_map = {'fid-p4': 'p4', 'fid-c4': 'c4', 'fid-gc4': 'gc4'}
    results = _collect_responsive_overrides_from_ir(parent, fid_cls_map, set())
    cls_to_css_list = {}
    for cls, bp, css in results:
        cls_to_css_list.setdefault(cls, []).append(css)
    gc_widths = [c.get('max-width') for c in cls_to_css_list.get('gc4', [])]
    check('U-456c', 'grandchild with own responsive not given max-width:100%',
          '100%' not in gc_widths)


def test_u457_base_absolute_resp_no_position_gets_relative():
    """U-457: When base CSS has position:absolute but H5 responsive override has NO
    explicit 'position', the responsive block must inject position:relative and strip
    top/left/right/bottom offsets (they're meaningless for normal-flow).

    Height is intentionally preserved: sections with all-absolute children need a
    fixed height to be visible in normal flow.

    Root cause: Tomorrowland VipStatusFollowsSection (39641:7672)
      base CSS:  position:absolute; top:861px; height:734px
      H5 resp:   {background-color:transparent, top:630px, height:692px, left:0px}
    With no position override, the H5 inherits position:absolute and falls out of
    document flow, causing all subsequent sections to stack underneath it.

    Real data: node 39641:7672 from TomorrowlandLandingPage4 (merged-39641-6863)
    """
    from lib.split_codegen import _strip_section_root_responsive_props

    # Real data: VipStatusFollowsSection H5 responsive[0].css
    resp_css = {
        'background-color': 'transparent',
        'top': '630px',
        'height': '692px',
        'left': '0px',
    }
    result = _strip_section_root_responsive_props(resp_css, base_position='absolute')

    check('U-457a', 'position:relative injected', result.get('position') == 'relative')
    check('U-457b', 'top stripped (offset meaningless for relative)', 'top' not in result)
    check('U-457c', 'left stripped', 'left' not in result)
    check('U-457d', 'height preserved (section needs fixed height for absolute children)',
          result.get('height') == '692px')
    check('U-457e', 'background-color preserved', result.get('background-color') == 'transparent')


def test_u457_base_absolute_resp_no_offsets_noop_except_position():
    """U-457 variant: base:absolute, resp has no offsets at all — only position injected."""
    from lib.split_codegen import _strip_section_root_responsive_props

    resp_css = {'background-color': '#ffffff'}
    result = _strip_section_root_responsive_props(resp_css, base_position='absolute')

    check('U-457f', 'position:relative injected', result.get('position') == 'relative')
    check('U-457g', 'background-color preserved', result.get('background-color') == '#ffffff')
    check('U-457h', 'no spurious keys added', set(result.keys()) == {'background-color', 'position'})


def test_u457_base_relative_no_change():
    """U-457 no-op: base:relative — no position injected even when resp has no position."""
    from lib.split_codegen import _strip_section_root_responsive_props

    resp_css = {'margin-top': '20px'}
    result = _strip_section_root_responsive_props(resp_css, base_position='relative')

    check('U-457i', 'no position injected for base:relative', 'position' not in result)
    check('U-457j', 'margin-top preserved', result.get('margin-top') == '20px')


def test_u457_orig_u442_still_works_with_new_param():
    """U-457 compat: original U-442 behavior (resp itself has position:absolute) still works
    when base_position param is provided."""
    from lib.split_codegen import _strip_section_root_responsive_props

    # Same data as U-442 original test
    resp_css = {
        'height': '706px',
        'left': '0px',
        'padding': '0',
        'width': '375px',
        'position': 'absolute',
        'top': '88px',
        'align-items': 'flex-start',
    }
    result = _strip_section_root_responsive_props(resp_css, base_position='relative')

    check('U-457k', 'position:absolute still removed when resp has it', result.get('position') == 'relative')
    check('U-457l', 'height stripped (resp sets position:absolute case)', 'height' not in result)
    check('U-457m', 'fixed px width stripped', 'width' not in result)
    check('U-457n', 'padding preserved', result.get('padding') == '0')
    check('U-457o', 'align-items preserved', result.get('align-items') == 'flex-start')


# ── Run ──
print('\n── Section Root CSS Strip Tests ──')
test_section_root_strips_absolute_positioning()
test_section_root_no_strip_when_relative()
test_section_root_no_strip_when_flag_false()
test_section_root_preserves_child_css()
test_section_root_top_becomes_margin_top()
test_section_root_top_zero_no_margin()
test_moly_text_override_collapse()
test_moly_text_override_tabs()
test_extract_moly_text_sizes()
test_text_override_wrapper_keeps_width()
test_u442_section_root_responsive_absolute_stripped()
test_u442_section_root_responsive_relative_unchanged()
test_u442_section_root_responsive_no_position_unchanged()
test_u446_abs_positioned_node_responsive_height_stripped()
test_u446_h5_makes_absolute_with_height_stripped()
test_u446_base_height_exceeded_by_h5_stripped()
test_u446_small_h5_upscale_below_ratio_threshold_kept()
test_u446_abs_positioned_no_variant_children_height_kept()
test_u446_abs_with_base_height_downscale_kept()
test_u446_relative_positioned_node_height_kept()
test_u446_no_position_node_height_kept()
test_u447_h5only_wrapper_height_rule_abs_with_height()
test_u447_h5only_wrapper_height_rule_relative_noop()
test_u447_h5only_wrapper_height_rule_no_height_noop()
test_u453_flex_row_to_column_child_width_overflow()
test_u453_child_already_has_responsive_no_duplicate()
test_u453_child_absolute_no_width_override()
test_u453_parent_stays_row_no_width_override()
test_u456_grandchild_overflow_gets_max_width()
test_u456_descendant_with_responsive_not_overridden()
test_u457_base_absolute_resp_no_position_gets_relative()
test_u457_base_absolute_resp_no_offsets_noop_except_position()
test_u457_base_relative_no_change()
test_u457_orig_u442_still_works_with_new_param()
print_summary('split/test_section_root_strip')
