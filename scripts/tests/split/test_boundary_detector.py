import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.boundary_detector import detect_boundaries


def _ir_root(children):
    return {'figmaId': 'root', 'figmaName': 'Page', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 1440, 'height': 3000}, 'css': {}, 'children': children}


def _inst_ir(node_id, comp_id, name, w=100, h=80):
    return {'figmaId': node_id, 'figmaName': name, 'figmaType': 'INSTANCE',
            'isComponentInstance': True, 'componentId': comp_id,
            'bb': {'width': w, 'height': h}, 'isDecorativeElement': False,
            'css': {}, 'children': [
                {'figmaId': node_id+':t', 'figmaName': 'lbl', 'figmaType': 'TEXT',
                 'isTextNode': True, 'isComponentInstance': False,
                 'componentId': None, 'bb': {'width': 80, 'height': 20},
                 'textContent': name, 'css': {}, 'children': []}
            ]}


def _section_ir(node_id, name, height, children=None):
    return {'figmaId': node_id, 'figmaName': name, 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 1440, 'height': height}, 'isDecorativeElement': False,
            'css': {}, 'children': children or []}


EMPTY_SEM = {'componentInstances': {}}


def test_leaf_deduplication():
    """3 个相同实例 → leafComponents 只有 1 条，count=3。"""
    ir = _ir_root([_inst_ir('1','c99','Card'), _inst_ir('2','c99','Card'), _inst_ir('3','c99','Card')])
    result = detect_boundaries(ir, EMPTY_SEM)
    assert len(result['leafComponents']) == 1
    assert result['leafComponents'][0]['count'] == 3


def test_icon_filtered_out():
    """宽高 ≤ 32px → 图标，不提取为叶子组件。"""
    ir = _ir_root([_inst_ir('1','icon','SearchIcon',w=24,h=24),
                   _inst_ir('2','icon','SearchIcon',w=24,h=24)])
    result = detect_boundaries(ir, EMPTY_SEM)
    assert result['leafComponents'] == []


def test_section_detected():
    """depth=1 + height > 200px → 识别为 Section。"""
    ir = _ir_root([_section_ir('s1','HeroSection', 800,
                               children=[_section_ir('c1','A',100),_section_ir('c2','B',100)])])
    result = detect_boundaries(ir, EMPTY_SEM)
    assert len(result['sections']) == 1
    assert result['sections'][0]['figmaName'] == 'HeroSection'


def test_small_section_skipped():
    """高度 ≤ 200px 不识别为 Section。"""
    ir = _ir_root([_section_ir('s1','Divider', 4)])
    result = detect_boundaries(ir, EMPTY_SEM)
    assert result['sections'] == []


def test_leaf_inside_section_owned_by_section():
    """Section 子树内的叶子 → 归属该 Section，顶层 leafComponents 为空。"""
    cards = [_inst_ir(f'{i}','c99','Card') for i in range(3)]
    ir = _ir_root([_section_ir('s1','CardSection', 600, children=cards)])
    result = detect_boundaries(ir, EMPTY_SEM)
    assert result['leafComponents'] == []
    assert len(result['sections']) == 1
    assert len(result['sections'][0]['leafComponents']) == 1


def test_depth_penetration():
    """单一 FRAME 包裹层 → 穿透找到内部 Section。"""
    inner = [_section_ir('s1','Hero', 800, children=[_section_ir('c1','A',100)])]
    ir = _ir_root([_section_ir('wrapper','PageWrapper', 3000, children=inner)])
    result = detect_boundaries(ir, EMPTY_SEM)
    assert len(result['sections']) >= 1


def test_decorative_type_skipped():
    """LINE/ELLIPSE 等装饰类型不识别为 Section。"""
    ir = _ir_root([{'figmaId': 'e1', 'figmaName': 'Bg', 'figmaType': 'ELLIPSE',
                    'isComponentInstance': False, 'componentId': None,
                    'bb': {'width': 1440, 'height': 500},
                    'isDecorativeElement': False, 'css': {}, 'children': []}])
    result = detect_boundaries(ir, EMPTY_SEM)
    assert result['sections'] == []


def test_button_icon_injection_from_cc_map():
    """Button 实例含 icon 子节点，icon componentId 在 CC map 中有映射 → allImports 自动补充 icon import。"""
    icon_child = {'figmaId': 'I1;icon', 'figmaName': 'icon1', 'figmaType': 'INSTANCE',
                  'isComponentInstance': True, 'componentId': 'icon-comp-1',
                  'bb': {'width': 18, 'height': 18}, 'css': {}, 'children': []}
    text_child = {'figmaId': 'I1;text', 'figmaName': 'Button Text', 'figmaType': 'TEXT',
                  'isTextNode': True, 'isComponentInstance': False, 'componentId': None,
                  'bb': {'width': 60, 'height': 16}, 'textContent': 'Click',
                  'css': {}, 'children': []}
    button = {'figmaId': 'btn1', 'figmaName': 'Primary Button', 'figmaType': 'INSTANCE',
              'isComponentInstance': True, 'componentId': 'btn-comp',
              'bb': {'width': 200, 'height': 48}, 'css': {}, 'children': [icon_child, text_child]}
    # 独立 icon 实例（用于建立 componentId→name 映射）
    standalone_icon = {'figmaId': 'icon-standalone', 'figmaName': 'FireIcon', 'figmaType': 'INSTANCE',
                       'isComponentInstance': True, 'componentId': 'icon-comp-1',
                       'bb': {'width': 24, 'height': 24}, 'css': {}, 'children': []}
    ir = _ir_root([_section_ir('s1', 'HeroSection', 800, children=[button, standalone_icon])])
    cc_map = {
        'btn1': {
            'componentName': 'Button', 'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': ['import { Button } from "your-component-lib";'],
            'snippet': '<Button variant="primary">\n        \n      </Button>',
        },
        'icon-standalone': {
            'componentName': 'IconFireSolid', 'ccImport': 'import { IconFireSolid } from "@example/icons";',
            'allImports': ['import { IconFireSolid } from "@example/icons";'],
            'snippet': '<IconFireSolid />', 'isIcon': True,
        },
    }
    result = detect_boundaries(ir, EMPTY_SEM, code_connect_map=cc_map)
    # Button 的 CC 结果应包含 IconFireSolid import
    btn_cc = [c for c in result['codeConnectComponents'] if c['ccComponent'] == 'Button']
    assert len(btn_cc) == 1, f"Expected 1 Button CC, got {len(btn_cc)}"
    assert any('IconFireSolid' in imp for imp in btn_cc[0]['allImports']), \
        f"IconFireSolid not in allImports: {btn_cc[0]['allImports']}"


def test_button_icon_comment_when_unmapped():
    """Button 实例含 icon 子节点，但 icon componentId 不在 CC map 中且无 components_meta → snippet 注入 TODO 注释。"""
    icon_child = {'figmaId': 'I2;icon', 'figmaName': 'icon2', 'figmaType': 'INSTANCE',
                  'isComponentInstance': True, 'componentId': 'unknown-icon-comp',
                  'bb': {'width': 18, 'height': 18}, 'css': {}, 'children': []}
    text_child = {'figmaId': 'I2;text', 'figmaName': 'Button Text', 'figmaType': 'TEXT',
                  'isTextNode': True, 'isComponentInstance': False, 'componentId': None,
                  'bb': {'width': 60, 'height': 16}, 'textContent': 'Submit',
                  'css': {}, 'children': []}
    button = {'figmaId': 'btn2', 'figmaName': 'Secondary Button', 'figmaType': 'INSTANCE',
              'isComponentInstance': True, 'componentId': 'btn-comp-2',
              'bb': {'width': 200, 'height': 48}, 'css': {}, 'children': [icon_child, text_child]}
    ir = _ir_root([_section_ir('s1', 'FormSection', 600, children=[button])])
    cc_map = {
        'btn2': {
            'componentName': 'Button', 'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': ['import { Button } from "your-component-lib";'],
            'snippet': '<Button variant="secondary">\n        Submit\n      </Button>',
        },
    }
    result = detect_boundaries(ir, EMPTY_SEM, code_connect_map=cc_map)
    btn_cc = [c for c in result['codeConnectComponents'] if c['ccComponent'] == 'Button']
    assert len(btn_cc) == 1
    assert '{/* icon:' in btn_cc[0]['snippet'], \
        f"Expected TODO comment in snippet: {btn_cc[0]['snippet']}"


def test_button_icon_from_components_meta():
    """Button icon componentId 不在 CC map 中，但 components_meta 有 icon_xxx 命名 → 自动推导注入 import。"""
    icon_child = {'figmaId': 'I3;icon', 'figmaName': 'icon1', 'figmaType': 'INSTANCE',
                  'isComponentInstance': True, 'componentId': 'icon-share-comp',
                  'bb': {'width': 18, 'height': 18}, 'css': {}, 'children': []}
    text_child = {'figmaId': 'I3;text', 'figmaName': 'Button Text', 'figmaType': 'TEXT',
                  'isTextNode': True, 'isComponentInstance': False, 'componentId': None,
                  'bb': {'width': 60, 'height': 16}, 'textContent': 'Share',
                  'css': {}, 'children': []}
    button = {'figmaId': 'btn3', 'figmaName': 'Primary Button', 'figmaType': 'INSTANCE',
              'isComponentInstance': True, 'componentId': 'btn-comp-3',
              'bb': {'width': 200, 'height': 48}, 'css': {}, 'children': [icon_child, text_child]}
    ir = _ir_root([_section_ir('s1', 'ShareSection', 600, children=[button])])
    cc_map = {
        'btn3': {
            'componentName': 'Button', 'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': ['import { Button } from "your-component-lib";'],
            'snippet': '<Button variant="primary">\n        Share\n      </Button>',
        },
    }
    components_meta = {
        'icon-share-comp': {'name': 'icon_share', 'key': 'abc123'},
    }
    result = detect_boundaries(ir, EMPTY_SEM, code_connect_map=cc_map,
                               components_meta=components_meta)
    btn_cc = [c for c in result['codeConnectComponents'] if c['ccComponent'] == 'Button']
    assert len(btn_cc) == 1
    assert any('IconShare' in imp for imp in btn_cc[0]['allImports']), \
        f"IconShare not in allImports: {btn_cc[0]['allImports']}"
    assert '{/* icon:' not in btn_cc[0]['snippet'], \
        "Should NOT have TODO comment when icon is resolved"


def test_wrapper_frame_unwrapped_to_sections():
    """单包装层展开：多余的 FRAME 包裹层应被识别并展开，其子节点成为独立 Section。

    Real data: Frame 2147224865 (10664:26578, h=2756) wraps
      Frame 1000006371 (h=152, nav)    → 展开后应成为 Section（h>50，低阈值）
      Frame 2147224878 (h=40)          → 展开后仍 inline（h<50）
      Frame 2147224879 (h=28)          → 展开后仍 inline（h<50）
      Frame 2147224833 (h=2463, data)  → 展开后应成为 Section

    Without the fix: Frame 2147224865 整个作为一个 Section，nav 和 vol data 无法独立。
    With the fix: 展开后得到 NavSection + VolDataSection + Footer，与设计意图一致。
    """
    def _mk_frame(fid, name, h, children=None):
        return {'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
                'bb': {'height': h, 'width': 1440.0},
                'isComponentInstance': False, 'children': children or []}

    # 构造与真实设计稿相同的层级（来自 page 10664-26577 / 10664-26578）
    nav_frame = _mk_frame('10664:26579', 'Frame 1000006371', 152.0)
    small1 = _mk_frame('10664:26594', 'Frame 2147224878', 40.0)
    small2 = _mk_frame('10664:26605', 'Frame 2147224879', 28.0)
    vol_data = _mk_frame('10664:26609', 'Frame 2147224833', 2463.0)
    wrapper = _mk_frame('10664:26578', 'Frame 2147224865', 2756.0,
                        children=[nav_frame, small1, small2, vol_data])
    footer = _mk_frame('10664:27655', '.Footer_lite', 300.0)

    ir = {'figmaId': '10664:26577', 'figmaName': 'Frame 2147224870',
          'figmaType': 'FRAME', 'bb': {'height': 3056.0, 'width': 1440.0},
          'isComponentInstance': False, 'children': [wrapper, footer]}

    result = detect_boundaries(ir, EMPTY_SEM)

    section_names = [s['figmaName'] for s in result['sections']]
    inline_names = [n['figmaName'] for n in result['inlineNodes']]

    # expandedWrapperFigmaIds 应包含被展开的包装层 figmaId（用于 validate_split 跳过检查）
    assert '10664:26578' in result.get('expandedWrapperFigmaIds', []), \
        f"Expected wrapper figmaId '10664:26578' in expandedWrapperFigmaIds: {result.get('expandedWrapperFigmaIds')}"

    # wrapper 被展开，nav 和 vol data 成为独立 Section（不应看到 Frame 2147224865 整体）
    assert 'Frame 2147224865' not in section_names, \
        f"Wrapper frame should NOT be a section: {section_names}"

    # nav frame (h=152 > 50) 应成为 Section
    assert 'Frame 1000006371' in section_names, \
        f"Nav frame (h=152) should be a Section after unwrap: {section_names}"

    # vol data (h=2463) 应成为 Section
    assert 'Frame 2147224833' in section_names, \
        f"VolData frame (h=2463) should be a Section: {section_names}"

    # footer 应成为 Section
    assert '.Footer_lite' in section_names, \
        f".Footer_lite should be a Section: {section_names}"

    # 小节点应是 inline
    assert 'Frame 2147224878' in inline_names, \
        f"Frame 2147224878 (h=40) should be inline: {inline_names}"
    assert 'Frame 2147224879' in inline_names, \
        f"Frame 2147224879 (h=28) should be inline: {inline_names}"


def test_padded_section_not_unwrapped_as_wrapper():
    """U-372: 有实质性 padding（≥50px）的 FRAME 不应被视为包装层展开。

    Real data: 4030:41366 Hot assets - V3
      padding: 100px 120px 20px 120px  ← 100px 顶部 padding
      children:
        4030:41367 Frame 2147229741 h=600  ← large
        4030:41521 Hot Events h=124         ← medium
        4030:41564 Navigation h=48          ← small（触发 has_small 检查）
    _is_wrapper_frame 因为 has_large (600) + has_small (48) 会误判为包装层，
    从而展开成 3 个独立 Section，导致 100px 顶部 padding 丢失。
    修复后：有 padding-top ≥ 50px 的 FRAME 不触发展开。
    """
    def _mk_section(fid, name, h, css=None):
        return {'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
                'bb': {'height': h, 'width': 1440.0},
                'isComponentInstance': False,
                'css': css or {}, 'children': []}

    hot_assets = {
        'figmaId': '4030:41366', 'figmaName': 'Hot assets - V3',
        'figmaType': 'FRAME',
        'bb': {'height': 912.0, 'width': 1440.0},
        'isComponentInstance': False,
        'css': {
            'width': '100%', 'height': '912px',
            'display': 'flex', 'flex-direction': 'column',
            'padding': '100px 120px 20px 120px',  # 100px top padding!
        },
        'children': [
            _mk_section('4030:41367', 'Frame 2147229741', 600.0),
            _mk_section('4030:41521', 'Hot Events', 124.0),
            _mk_section('4030:41564', 'Navigation', 48.0),
        ],
    }
    ir = _ir_root([hot_assets])
    result = detect_boundaries(ir, EMPTY_SEM)
    section_names = [s['figmaName'] for s in result['sections']]

    assert 'Hot assets - V3' in section_names, (
        f'U-372 FAIL: 有 100px padding 的 Hot assets - V3 应作为独立 Section 而非被展开，'
        f'实际 sections: {section_names}'
    )
    # 不应把内部子节点直接提升为顶级 Section
    assert 'Frame 2147229741' not in section_names, (
        f'U-372 FAIL: Frame 2147229741 应嵌套在 Hot assets - V3 内，而非顶级 Section，'
        f'实际 sections: {section_names}'
    )
    assert 'Navigation' not in section_names, (
        f'U-372 FAIL: Navigation (h=48) 应嵌套在 Hot assets - V3 内，'
        f'实际 sections: {section_names}'
    )


if __name__ == '__main__':
    tests = [test_leaf_deduplication, test_icon_filtered_out, test_section_detected,
             test_small_section_skipped, test_leaf_inside_section_owned_by_section,
             test_depth_penetration, test_decorative_type_skipped,
             test_button_icon_injection_from_cc_map, test_button_icon_comment_when_unmapped,
             test_button_icon_from_components_meta, test_wrapper_frame_unwrapped_to_sections,
             test_padded_section_not_unwrapped_as_wrapper]
    passed = 0
    for t in tests:
        try:
            t(); print(f'  ✓ {t.__name__}'); passed += 1
        except Exception as e:
            print(f'  ✗ {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')
