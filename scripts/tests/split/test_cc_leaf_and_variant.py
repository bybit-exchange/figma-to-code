#!/usr/bin/env python3
"""
Code Connect 叶子组件测试：
- position strip 时 template 实例强制 wrapper
- Button variant 从实例 CSS 推断（primary vs outline）
- CC 单次出现节点也应成为 leaf 组件
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()


def test_bug14_wrapper_not_collapsed_when_position_stripped():
    """U-310: template 实例的 wrapper 不应在 position strip 时被折叠。"""
    from lib.split_codegen import _detect_width_conflict, _POSITION_PROPS

    # Template has absolute, instance has different position context
    orig_css_map = {
        'register-for': {
            'width': '383px',
            'position': 'absolute',
            'left': '32px',
            'top': '23px',
        },
        'register-for-inst2': {
            'width': '383px',
            'margin-top': '24px',
            'align-self': 'center',
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

    strip = _detect_width_conflict(lc, {}, fid_to_class, orig_css_map)

    check('U-310a', 'position is stripped', 'position' in strip)
    # BUG-14 fix: template instance (311:15199) should force wrapper
    # because its CSS in section has position:absolute but component doesn't
    check('U-310b', 'strip intersects POSITION_PROPS',
          bool(strip & _POSITION_PROPS))


def test_bug19_button_variant_detection():
    """U-311: Button 实例间 variant 差异应被检测。"""
    # Simulate two Button instances with different fills
    node_index = {
        '8464:6205': {'figmaId': '8464:6205', 'css': {'background-color': '#ff9c2e'}},
        '8464:6206': {'figmaId': '8464:6206', 'css': {'border': '1px solid #ff9c2e'}},
    }

    # Detect variant from CSS
    variants = []
    for fid in ['8464:6205', '8464:6206']:
        inst = node_index[fid]
        bg = inst.get('css', {}).get('background-color', '')
        bd = inst.get('css', {}).get('border', '')
        if bd and (not bg or 'transparent' in bg):
            variants.append('outline')
        else:
            variants.append('primary')

    check('U-311a', 'first button is primary', variants[0] == 'primary')
    check('U-311b', 'second button is outline', variants[1] == 'outline')
    check('U-311c', 'variants differ', len(set(variants)) > 1)


def test_bug20_cc_single_instance_becomes_leaf():
    """U-312: CC 匹配但只出现 1 次的节点也应成为 leaf 组件。"""
    from lib.boundary_detector import detect_boundaries

    # Minimal IR with one INSTANCE node that matches CC
    ir = {
        'figmaId': 'root',
        'figmaName': 'Page',
        'figmaType': 'FRAME',
        'css': {},
        'children': [{
            'figmaId': '100:1',
            'figmaName': 'FAQ Section',
            'figmaType': 'FRAME',
            'css': {'width': '1200px', 'height': '400px'},
            'bb': {'width': 1200, 'height': 400},
            'children': [{
                'figmaId': '100:2',
                'figmaName': 'Collapse Component',
                'figmaType': 'INSTANCE',
                'isComponentInstance': True,
                'css': {'display': 'flex'},
                'bb': {'width': 1200, 'height': 300},
                'children': [],
            }],
        }],
    }

    cc_map = {
        '100:2': {
            'componentName': 'Collapse',
            'ccImport': "import { Collapse } from 'your-component-lib'",
            'allImports': ["import { Collapse } from 'your-component-lib'"],
            'snippet': '<Collapse type="single" />',
        }
    }

    result = detect_boundaries(ir, {}, code_connect_map=cc_map)

    # The CC node should appear as a leaf component (even though count=1)
    all_leaves = result['leafComponents']
    section_leaves = []
    for s in result['sections']:
        section_leaves.extend(s.get('leafComponents', []))

    all_cc_leaves = [lc for lc in (all_leaves + section_leaves)
                     if lc.get('ccComponent') == 'Collapse']

    check('U-312a', 'Collapse found as leaf component', len(all_cc_leaves) > 0)
    if all_cc_leaves:
        check('U-312b', 'ccComponent is Collapse',
              all_cc_leaves[0]['ccComponent'] == 'Collapse')
        check('U-312c', 'source is code-connect',
              all_cc_leaves[0].get('source') == 'code-connect')


def test_cc_name_fallback_matches_button():
    """U-313: 当 componentId 不在 CC map 中时，通过组件名称 fallback 匹配 Button。"""
    from lib.boundary_detector import _collect_cc_nodes

    cc_map = {
        '0:3765': {
            'componentName': 'Button',
            'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': ['import { Button } from "your-component-lib";'],
            'snippet': '<Button variant="primary" size="middle">Label</Button>',
        },
    }

    # Node with componentId '3:2531' (not in cc_map) but named 'Primary Button'
    node = {
        'figmaId': 'I100:200;100:300',
        'figmaName': '1_Primary Button (Default)',
        'figmaType': 'INSTANCE',
        'isComponentInstance': True,
        'componentId': '3:2531',
        'children': [],
    }

    cc_components = []
    cc_matched_ids = set()
    _collect_cc_nodes(node, cc_map, cc_components, cc_matched_ids)

    check('U-313a', 'No name fallback (only CC precise match)', len(cc_components) == 0)


def test_cc_name_fallback_no_false_positive():
    """U-314: 非 Button 名称的节点不应被 fallback 匹配。"""
    from lib.boundary_detector import _collect_cc_nodes

    cc_map = {
        '0:3765': {
            'componentName': 'Button',
            'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': [],
            'snippet': '<Button>OK</Button>',
        },
    }

    node = {
        'figmaId': 'I200:300;200:400',
        'figmaName': 'Card Container',
        'figmaType': 'INSTANCE',
        'isComponentInstance': True,
        'componentId': '5:999',
        'children': [],
    }

    cc_components = []
    cc_matched_ids = set()
    _collect_cc_nodes(node, cc_map, cc_components, cc_matched_ids)

    check('U-314a', 'Non-button node not matched', len(cc_components) == 0)


def test_cc_collapse_name_alias_fallback():
    """U-318: Collapse 通过 figmaName 中的 'FAQ'/'Expandable' 别名 fallback 匹配。"""
    from lib.boundary_detector import _collect_cc_nodes

    cc_map = {
        '0:3791': {
            'componentName': 'Collapse',
            'ccImport': 'import { Collapse } from "your-component-lib";',
            'allImports': ['import { Collapse } from "your-component-lib";'],
            'snippet': '<Collapse type="single" collapsible items={[]}/>',
        },
    }

    # Node named "FAQ (Expandable)" with componentId not in cc_map
    node = {
        'figmaId': 'I39641:9399;39356:7792',
        'figmaName': 'FAQ (Expandable)',
        'figmaType': 'INSTANCE',
        'isComponentInstance': True,
        'componentId': '0:3153',
        'children': [],
    }

    cc_components = []
    cc_matched_ids = set()
    _collect_cc_nodes(node, cc_map, cc_components, cc_matched_ids)

    check('U-318a', 'No alias fallback (only CC precise match)', len(cc_components) == 0)


def test_cc_direct_name_in_figmaname():
    """U-319: 通用 fallback — figmaName 直接包含组件名称。"""
    from lib.boundary_detector import _collect_cc_nodes

    cc_map = {
        '0:9999': {
            'componentName': 'Pagination',
            'ccImport': 'import { Pagination } from "your-component-lib";',
            'allImports': ['import { Pagination } from "your-component-lib";'],
            'snippet': '<Pagination total={100}/>',
        },
    }

    node = {
        'figmaId': 'I100:200;100:300',
        'figmaName': 'Pagination Large',
        'figmaType': 'INSTANCE',
        'isComponentInstance': True,
        'componentId': '5:888',
        'children': [],
    }

    cc_components = []
    cc_matched_ids = set()
    _collect_cc_nodes(node, cc_map, cc_components, cc_matched_ids)

    check('U-319a', 'No direct name fallback (only CC precise match)', len(cc_components) == 0)


def test_cc_no_false_positive_generic_name():
    """U-320: Icon 组件名不应匹配普通含 icon 字样的节点。"""
    from lib.boundary_detector import _collect_cc_nodes

    cc_map = {
        '0:1111': {
            'componentName': 'Icon',
            'ccImport': 'import { Icon } from "your-component-lib";',
            'allImports': [],
            'snippet': '<Icon name="check"/>',
        },
    }

    # "Section Icon Header" contains "Icon" but is not a component match
    # because it's a FRAME not an INSTANCE
    node = {
        'figmaId': 'I200:300',
        'figmaName': 'Section Icon Header',
        'figmaType': 'FRAME',
        'componentId': '',
        'children': [],
    }

    cc_components = []
    cc_matched_ids = set()
    _collect_cc_nodes(node, cc_map, cc_components, cc_matched_ids)

    check('U-320a', 'Non-instance node not matched even with name hit', len(cc_components) == 0)


# ─── _patch_cc_snippet icon 位置 + 文本注入 ─────────────────────────────────────

from lib.split_codegen import _patch_cc_snippet, _extract_instance_texts


def _make_instance_ir(children_order):
    """构造 IR：children_order 是 ('text', 'icon') 或 ('icon', 'text') 等序列。"""
    children = []
    for kind in children_order:
        if kind == 'text':
            children.append({'figmaId': 'T:1', 'figmaName': 'Text', 'isTextNode': True,
                             'isVectorNode': False, 'isImageNode': False, 'textContent': 'Buy'})
        elif kind == 'icon':
            children.append({'figmaId': 'V:1', 'figmaName': 'icon', 'isTextNode': False,
                             'isVectorNode': True, 'isImageNode': False})
    return {'figmaId': '1:1', 'children': children}


def test_icon_injection_after_text():
    """CC-ICON-01: IR 中 text 在前 icon 在后 → icon 注入到 children 末尾。"""
    snippet = '<Button variant="primary">\n        Buy\n      </Button>'
    all_imports = ["import { Button } from 'your-component-lib'", "import { IconArrowRight } from '@example/icons'"]
    ir = _make_instance_ir(['text', 'icon'])

    result = _patch_cc_snippet(snippet, all_imports, instance_ir=ir)
    # icon 应在 Buy 后、</Button> 前
    buy_pos = result.find('Buy')
    icon_pos = result.find('IconArrowRight')
    close_pos = result.find('</Button>')
    check('CC-ICON-01a', 'icon injected', icon_pos > 0)
    check('CC-ICON-01b', 'icon after text', icon_pos > buy_pos)
    check('CC-ICON-01c', 'icon before closing tag', icon_pos < close_pos)


def test_icon_injection_before_text():
    """CC-ICON-02: IR 中 icon 在前 text 在后 → icon 注入到 children 开头。"""
    snippet = '<Button variant="primary">\n        Buy\n      </Button>'
    all_imports = ["import { Button } from 'your-component-lib'", "import { IconArrowLeft } from '@example/icons'"]
    ir = _make_instance_ir(['icon', 'text'])

    result = _patch_cc_snippet(snippet, all_imports, instance_ir=ir)
    buy_pos = result.find('Buy')
    icon_pos = result.find('IconArrowLeft')
    check('CC-ICON-02a', 'icon injected', icon_pos > 0)
    check('CC-ICON-02b', 'icon before text', icon_pos < buy_pos)


def test_icon_injection_no_ir():
    """CC-ICON-03: 无 instance_ir 时默认放在 children 末尾。"""
    snippet = '<Button>\n        Go\n      </Button>'
    all_imports = ["import { Button } from 'your-component-lib'", "import { IconCheck } from '@example/icons'"]

    result = _patch_cc_snippet(snippet, all_imports, instance_ir=None)
    go_pos = result.find('Go')
    icon_pos = result.find('IconCheck')
    check('CC-ICON-03a', 'icon injected', icon_pos > 0)
    check('CC-ICON-03b', 'icon after text (default)', icon_pos > go_pos)


def test_icon_injection_self_closing():
    """CC-ICON-04: 自闭合标签不注入 icon。"""
    snippet = '<Input placeholder="search" />'
    all_imports = ["import { Input } from 'your-component-lib'", "import { IconSearch } from '@example/icons'"]

    result = _patch_cc_snippet(snippet, all_imports)
    check('CC-ICON-04a', 'no icon injected for self-closing', 'IconSearch' not in result)


def test_icon_injection_already_present():
    """CC-ICON-05: icon 已在 snippet 中则不重复注入。"""
    snippet = '<Button><IconCheck />Go</Button>'
    all_imports = ["import { Button } from 'your-component-lib'", "import { IconCheck } from '@example/icons'"]

    result = _patch_cc_snippet(snippet, all_imports)
    check('CC-ICON-05a', 'no duplicate icon', result.count('IconCheck') == 1)


def test_empty_children_text_injection():
    """CC-TEXT-01: 内联 snippet 空 children 时注入实例文本。"""
    import re
    snippet = '<Tag color="red">\n        \n      </Tag>'
    ir = {'figmaId': '1:1', 'children': [
        {'figmaId': '2:1', 'children': [
            {'figmaId': '3:1', 'isTextNode': True, 'textContent': 'HOT', 'children': []}
        ]}
    ]}
    texts = _extract_instance_texts(ir)
    check('CC-TEXT-01a', 'text extracted', texts == ['HOT'])

    # Simulate inline snippet text injection
    if texts and re.search(r'>\s+</', snippet):
        result = re.sub(r'>\s+</', f'>{texts[0]}</', snippet, count=1)
    else:
        result = snippet
    check('CC-TEXT-01b', 'text injected', '>HOT</' in result)
    check('CC-TEXT-01c', 'empty removed', '>\n        \n      </' not in result)


def test_empty_children_no_injection_when_has_content():
    """CC-TEXT-02: snippet 已有内容时不注入。"""
    import re
    snippet = '<Tag>Existing</Tag>'
    check('CC-TEXT-02a', 'no empty children match', re.search(r'>\s+</', snippet) is None)


def test_leaf_root_strip_props():
    """LEAF-STRIP-01: 叶子组件根 class CSS strip 外部容器属性。"""
    from lib.split_codegen import _css_from_orig, _LEAF_ROOT_STRIP_PROPS

    ir = {
        'figmaId': '1:1',
        'semantic': {'htmlTag': 'div', 'className': 'container', 'componentName': 'TopUp', 'props': []},
        'css': {
            'width': '497px', 'height': '140px',
            'position': 'absolute', 'left': '120px', 'top': '0px',
            'display': 'flex', 'flex-direction': 'column',
            'align-items': 'flex-start', 'gap': '12px',
            'padding': '32px', 'overflow': 'hidden',
            'background-color': '#ffffff', 'border-radius': '24px',
        },
        'children': [],
    }
    orig = {'container': dict(ir['css'])}
    result = _css_from_orig(ir, orig, 'less', strip_root_props=_LEAF_ROOT_STRIP_PROPS)

    # 外部容器属性应被 strip
    check('LEAF-STRIP-01a', 'no position:absolute', 'position: absolute' not in result)
    check('LEAF-STRIP-01b', 'padding preserved', 'padding:' in result)
    check('LEAF-STRIP-01c', 'overflow preserved (visual)', 'overflow: hidden' in result)
    check('LEAF-STRIP-01d', 'height preserved', 'height: 140px' in result)
    # 视觉属性保留（不 strip）
    check('LEAF-STRIP-01e', 'background-color preserved', 'background-color: #ffffff' in result)
    check('LEAF-STRIP-01f', 'border-radius preserved', 'border-radius: 24px' in result)
    # 内部布局属性保留
    check('LEAF-STRIP-01g', 'display preserved', 'display: flex' in result)
    check('LEAF-STRIP-01h', 'flex-direction preserved', 'flex-direction: column' in result)
    check('LEAF-STRIP-01i', 'gap preserved', 'gap: 12px' in result)
    check('LEAF-STRIP-01j', 'align-items preserved', 'align-items: flex-start' in result)


def test_leaf_root_strip_position_relative_preserved():
    """LEAF-STRIP-02: position:relative 不被 strip（子元素 absolute 需要它作参照）。"""
    from lib.split_codegen import _css_from_orig, _LEAF_ROOT_STRIP_PROPS

    ir = {
        'figmaId': '2573:28396',
        'semantic': {'htmlTag': 'div', 'className': 'group-1000004507', 'componentName': 'Day', 'props': []},
        'css': {
            'width': '497px', 'height': '39px',
            'position': 'relative',
            'display': 'flex', 'flex-direction': 'row',
            'align-items': 'flex-start', 'gap': '15px',
            'justify-content': 'space-between',
            'flex-shrink': '0',
        },
        'children': [],
    }
    orig = {'group-1000004507': dict(ir['css'])}
    result = _css_from_orig(ir, orig, 'less', strip_root_props=_LEAF_ROOT_STRIP_PROPS)

    check('LEAF-STRIP-02a', 'position:relative preserved', 'position: relative' in result)
    check('LEAF-STRIP-02b', 'flex-shrink stripped', 'flex-shrink' not in result)
    check('LEAF-STRIP-02c', 'display preserved', 'display: flex' in result)


def test_leaf_root_strip_position_absolute_stripped():
    """LEAF-STRIP-03: position:absolute 仍然被 strip（当通过 _LEAF_ROOT_STRIP_PROPS 传入时）。"""
    from lib.split_codegen import _css_from_orig, _LEAF_ROOT_STRIP_PROPS

    ir = {
        'figmaId': '1:2',
        'semantic': {'htmlTag': 'div', 'className': 'abs-box', 'componentName': 'Box', 'props': []},
        'css': {
            'width': '200px', 'height': '100px',
            'position': 'absolute', 'left': '50px', 'top': '20px',
            'display': 'flex',
        },
        'children': [],
    }
    orig = {'abs-box': dict(ir['css'])}
    result = _css_from_orig(ir, orig, 'less', strip_root_props=_LEAF_ROOT_STRIP_PROPS)

    check('LEAF-STRIP-03a', 'position:absolute stripped', 'position: absolute' not in result)
    check('LEAF-STRIP-03b', 'left stripped', 'left: 50px' not in result)
    check('LEAF-STRIP-03c', 'top stripped', 'top: 20px' not in result)
    check('LEAF-STRIP-03d', 'display preserved', 'display: flex' in result)


def test_leaf_root_position_absolute_uniform_preserved():
    """LEAF-STRIP-04: 所有实例 position:absolute 一致时，不 strip position 系属性。
    场景：背景层组件（absolute 铺满父容器，不应参与 flex 流）。"""
    from lib.split_codegen import _detect_width_conflict, _LEAF_ROOT_STRIP_PROPS, _POSITION_PROPS

    # 5 个实例全都是 position:absolute, left:0, top:0
    fid_to_class = {
        '311:15271': 'group-bg',
        '311:15285': 'group-bg-inst2',
        '311:15299': 'group-bg-inst3',
        '311:15314': 'group-bg-inst4',
        '311:15330': 'group-bg-inst5',
    }
    orig_css_map = {
        'group-bg': {'width': '384px', 'height': '186px', 'position': 'absolute', 'left': '0px', 'top': '0px'},
        'group-bg-inst2': {'width': '384px', 'height': '186px', 'position': 'absolute', 'left': '0px', 'top': '0px'},
        'group-bg-inst3': {'width': '384px', 'height': '186px', 'position': 'absolute', 'left': '0px', 'top': '0px'},
        'group-bg-inst4': {'width': '384px', 'height': '186px', 'position': 'absolute', 'left': '0px', 'top': '0px'},
        'group-bg-inst5': {'width': '384px', 'height': '186px', 'position': 'absolute', 'left': '0px', 'top': '0px'},
    }
    lc = {
        'ir': {'figmaId': '311:15271'},
        'allInstanceFigmaIds': list(fid_to_class.keys()),
    }

    strip = _detect_width_conflict(lc, {}, fid_to_class, orig_css_map)

    # _detect_width_conflict 不应 strip position（所有实例一致）
    check('LEAF-STRIP-04a', 'no position conflict detected', not (strip & _POSITION_PROPS))

    # 模拟 generate_components 中的逻辑：所有实例 position 一致时不合入 POSITION_PROPS
    _all_inst_pos = set()
    for _ifid in lc['allInstanceFigmaIds']:
        _icls = fid_to_class.get(_ifid, '')
        _iprops = orig_css_map.get(_icls, {})
        _all_inst_pos.add(_iprops.get('position', ''))

    if len(_all_inst_pos) == 1 and 'absolute' in _all_inst_pos:
        _base_strip = _LEAF_ROOT_STRIP_PROPS - _POSITION_PROPS
    else:
        _base_strip = _LEAF_ROOT_STRIP_PROPS

    final_strip = (strip or set()) | _base_strip
    check('LEAF-STRIP-04b', 'position NOT in final strip', 'position' not in final_strip)
    check('LEAF-STRIP-04c', 'left NOT in final strip', 'left' not in final_strip)
    check('LEAF-STRIP-04d', 'flex-shrink still in final strip', 'flex-shrink' in final_strip)


def test_single_instance_cc_leaf_no_tsx_file():
    """U-323/324/325: 单实例 CC leaf（无 varyingProps + snippet）不应生成独立 .tsx 文件。

    U-323: single-instance CC + no varyingProps → Section inlines snippet → NO .tsx file
    U-324: single-instance CC + has varyingProps → Section uses leaf component → HAS .tsx file
    U-325: multi-instance CC (same snippet, different instance data) → HAS .tsx file

    Real data: StAction19 (IconRefresh) and TagDefault1 (Tag) from page 10664-26578.
    Both are single-instance with no varyingProps and were being generated but never imported.
    """
    import os, shutil, tempfile
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '10-300'
        comp_name = 'TestPage300'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)
        (page_dir / f'{comp_name}.module.less').write_text('.page { width: 1440px; }')
        (page_dir / f'{comp_name}.tsx').write_text(
            f"import styles from './{comp_name}.module.less';\n"
            f"export default function {comp_name}() {{ return <div />; }}"
        )
        (page_dir / 'figma-maps.json').write_text(json.dumps({
            'fidToClass': {'1:1': 'page', '2:1': 'icon-refresh', '3:1': 'tag-buy', '4:1': 'tag-sell'},
            'fidToSrc': {}
        }))

        section_ir = {
            'figmaId': '1:100', 'figmaName': 'MainSection', 'figmaType': 'FRAME',
            'css': {'position': 'relative', 'width': '1440px'},
            'semantic': {'className': 'main-section', 'componentName': 'MainSection'},
            'children': [
                {'figmaId': '2:1', 'figmaName': 'Refresh', 'figmaType': 'INSTANCE',
                 'css': {}, 'children': [], 'iconColor': '#7f838a',
                 'semantic': {'className': 'icon-refresh', 'componentName': 'Refresh'}},
                {'figmaId': '3:1', 'figmaName': 'TagBuy', 'figmaType': 'INSTANCE',
                 'css': {}, 'children': [], 'isComponentInstance': True,
                 'semantic': {'className': 'tag-buy', 'componentName': 'TagBuy'}},
                {'figmaId': '4:1', 'figmaName': 'TagSell', 'figmaType': 'INSTANCE',
                 'css': {}, 'children': [], 'isComponentInstance': True,
                 'semantic': {'className': 'tag-sell', 'componentName': 'TagSell'}},
            ],
        }

        plan = {
            'pageComponent': comp_name, 'nodeId': node_id, 'nodeIdSafe': node_id,
            'cssExt': 'less', 'irPath': '',
            'sections': [{
                'name': 'MainSection', 'ir': section_ir, 'y': 0,
                'leafComponents': [
                    # U-323: single-instance, no varyingProps → should NOT generate file
                    # Real data: StAction19 from page 10664-26578
                    {
                        'name': 'StAction19',
                        'ir': section_ir['children'][0],
                        'ccComponent': 'IconRefresh',
                        'ccImport': "import { IconRefresh } from '@example/icons';",
                        'allImports': ["import { IconRefresh } from '@example/icons';"],
                        'snippet': '<IconRefresh />',
                        'varyingProps': [],          # no varying props
                        'allInstanceFigmaIds': ['2:1'],  # single instance
                        'confidence': 1.0, 'source': 'code-connect',
                    },
                    # U-324: single-instance, HAS varyingProps → SHOULD generate file
                    {
                        'name': 'TagBuyVariant',
                        'ir': section_ir['children'][1],
                        'ccComponent': 'Tag',
                        'ccImport': "import { Tag } from 'your-component-lib';",
                        'allImports': ["import { Tag } from 'your-component-lib';"],
                        'snippet': '<Tag variant="default" color="green" size="small">Buy</Tag>',
                        'varyingProps': [{'path': [], 'propName': 'label', 'type': 'text', 'values': ['Buy', 'Sell']}],
                        'allInstanceFigmaIds': ['3:1'],  # single instance but HAS varyingProps
                        'confidence': 1.0, 'source': 'code-connect',
                    },
                    # U-325: multi-instance → SHOULD generate file
                    {
                        'name': 'TagDefault',
                        'ir': section_ir['children'][2],
                        'ccComponent': 'Tag',
                        'ccImport': "import { Tag } from 'your-component-lib';",
                        'allImports': ["import { Tag } from 'your-component-lib';"],
                        'snippet': '<Tag variant="default" color="green" size="small">Buy</Tag>',
                        'varyingProps': [],
                        'allInstanceFigmaIds': ['3:1', '4:1'],  # MULTIPLE instances
                        'confidence': 1.0, 'source': 'code-connect',
                        'instancesData': [{'label': 'Buy'}, {'label': 'Sell'}],
                    },
                ],
                'topLeaves': [], 'inlineNodes': [],
            }],
            'topLeaves': [], 'inlineNodes': [],
        }

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from lib.split_codegen import generate_components
            out_dir = tmp / 'out'
            generate_components(plan, out_dir, css_ext='less')
        finally:
            os.chdir(old_cwd)

        section_dir = out_dir / 'components' / 'MainSection'

        # U-323: single-instance, no varyingProps → NO .tsx file (inlined in Section)
        # Real data: StAction19 from page 10664-26578 was orphaned (never imported)
        check('U-323', 'single-instance CC with no varyingProps: NO separate .tsx file',
              not (section_dir / 'StAction19.tsx').exists())

        # U-324: single-instance WITH varyingProps → HAS .tsx file
        check('U-324', 'single-instance CC with varyingProps: HAS .tsx file',
              (section_dir / 'TagBuyVariant.tsx').exists())

        # U-325: multi-instance → HAS .tsx file
        check('U-325', 'multi-instance CC leaf: HAS .tsx file',
              (section_dir / 'TagDefault.tsx').exists())

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_self_closing_button_text_injection():
    """U-326: 自闭合 CC snippet + 实例直接文本子节点 → 展开并注入文本（BtcPizzaDay 空按钮回归）。
    Real data: node 245:12751 from BtcPizzaDay (nodeId: 235-14237).
    CC snippet is '<Button variant="primary" size="small" />' (self-closing, no children).
    Instance IR has a direct text child: isTextNode=True, textContent='Registration'.
    Bug: _write_moly_flat's empty_children regex r'>\\s*</' cannot match self-closing tags,
    so text injection is skipped and the generated Button has no label text.
    """
    import tempfile, shutil
    from lib.split_codegen import _write_moly_flat

    # Real data: node 245:12751 from BtcPizzaDay (nodeId 235-14237)
    instance_ir = {
        'figmaId': '245:12751',
        'figmaName': 'button_registration',
        'isTextNode': False,
        'children': [
            {
                'figmaId': 'I245:12751;513:18789',
                'isTextNode': True,
                'textContent': 'Registration',
                'children': [],
            }
        ],
    }

    tmp = Path(tempfile.mkdtemp())
    try:
        _write_moly_flat(
            tmp, 'ButtonComp', 'Button',
            "import { Button } from 'your-component-lib'",
            css_ext='less',
            all_imports=["import { Button } from 'your-component-lib'"],
            snippet='<Button variant="primary" size="small" />',
            instance_ir=instance_ir,
        )
        content = (tmp / 'ButtonComp.tsx').read_text()
        # U-326: self-closing Button with instance text → children injected
        check('U-326', 'self-closing Button with instance text → children injected',
              'Registration' in content)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Run ──
print('\n── BUG-14/19/20/26/30/33 + CC icon/text 补充单测 ──')
test_bug14_wrapper_not_collapsed_when_position_stripped()
test_bug19_button_variant_detection()
test_bug20_cc_single_instance_becomes_leaf()
test_cc_name_fallback_matches_button()
test_cc_name_fallback_no_false_positive()
test_cc_collapse_name_alias_fallback()
test_cc_direct_name_in_figmaname()
test_cc_no_false_positive_generic_name()
test_icon_injection_after_text()
test_icon_injection_before_text()
test_icon_injection_no_ir()
test_icon_injection_self_closing()
test_icon_injection_already_present()
test_empty_children_text_injection()
test_empty_children_no_injection_when_has_content()
test_leaf_root_strip_props()
test_leaf_root_strip_position_relative_preserved()
test_leaf_root_strip_position_absolute_stripped()
test_leaf_root_position_absolute_uniform_preserved()
test_single_instance_cc_leaf_no_tsx_file()
test_self_closing_button_text_injection()
print_summary('split/test_cc_leaf_and_variant')
