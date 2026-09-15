import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.instance_merger import (
    group_instances, extract_varying_props,
    build_instances_data, mark_varying_nodes,
    _struct_key,
)


def _ir_tree(children):
    """构造最小 IR 树，包含给定子节点列表。"""
    return {'figmaId': 'root', 'figmaName': 'Page', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 1440, 'height': 3000}, 'css': {}, 'children': children}


def _inst_ir(figma_id, comp_id, name, text, w=100, h=80):
    """构造 IR 格式的 INSTANCE 节点（含 componentId 和 bb）。"""
    return {
        'figmaId': figma_id, 'figmaName': name, 'figmaType': 'INSTANCE',
        'isComponentInstance': True, 'componentId': comp_id,
        'bb': {'width': w, 'height': h},
        'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
        'css': {'width': f'{w}px', 'height': f'{h}px'}, 'children': [
            {'figmaId': figma_id + ':t', 'figmaName': 'label',
             'figmaType': 'TEXT', 'isTextNode': True, 'isComponentInstance': False,
             'componentId': None, 'bb': {'width': 80, 'height': 20},
             'textContent': text, 'isImageNode': False, 'isDecorativeElement': False,
             'css': {}, 'children': []}
        ],
    }


EMPTY_SEM = {'componentInstances': {}, 'interactiveNodes': {}, 'readyFrames': []}


def test_groups_by_component_id():
    ir = _ir_tree([_inst_ir('1', 'c99', 'Card', 'Alpha'),
                   _inst_ir('2', 'c99', 'Card', 'Beta'),
                   _inst_ir('3', 'c99', 'Card', 'Gamma')])
    groups = group_instances(ir, EMPTY_SEM)
    assert len(groups) == 1
    assert groups[0]['count'] == 3
    assert groups[0]['componentId'] == 'c99'


def test_skips_single_instance():
    ir = _ir_tree([_inst_ir('1', 'c99', 'Card', 'Only')])
    assert group_instances(ir, EMPTY_SEM) == []


def test_skips_icons():
    """宽高 ≤ 32px 的 INSTANCE → 图标，跳过。"""
    ir = _ir_tree([_inst_ir('1', 'icon1', 'SearchIcon', 'x', w=24, h=24),
                   _inst_ir('2', 'icon1', 'SearchIcon', 'x', w=24, h=24)])
    assert group_instances(ir, EMPTY_SEM) == []


def test_extract_varying_props_text():
    instances = [
        {'figmaId': '1', 'figmaName': 'title', 'isTextNode': True,
         'textContent': 'Alpha', 'children': []},
        {'figmaId': '2', 'figmaName': 'title', 'isTextNode': True,
         'textContent': 'Beta',  'children': []},
    ]
    props = extract_varying_props(instances)
    assert len(props) == 1
    assert props[0]['propName'] == 'title'
    assert props[0]['type'] == 'text'
    assert props[0]['values'] == ['Alpha', 'Beta']


def test_extract_no_props_when_same():
    instances = [
        {'figmaId': '1', 'figmaName': 'label', 'isTextNode': True,
         'textContent': 'Same', 'children': []},
        {'figmaId': '2', 'figmaName': 'label', 'isTextNode': True,
         'textContent': 'Same', 'children': []},
    ]
    assert extract_varying_props(instances) == []


def test_build_instances_data():
    instances = [_inst_ir('1','c','C','A'), _inst_ir('2','c','C','B'), _inst_ir('3','c','C','C')]
    varying = [{'path': [0], 'propName': 'label', 'type': 'text', 'values': ['A','B','C']}]
    data = build_instances_data(instances, varying)
    assert data == [{'label': 'A'}, {'label': 'B'}, {'label': 'C'}]


def test_mark_varying_nodes():
    ir = {'figmaId': '1', 'isTextNode': False, 'children': [
        {'figmaId': '1:t', 'isTextNode': True, 'textContent': 'Alpha', 'children': []}
    ]}
    varying = [{'path': [0], 'propName': 'label', 'type': 'text', 'values': ['Alpha', 'Beta']}]
    mark_varying_nodes(ir, varying)
    assert ir['children'][0]['_prop_name'] == 'label'
    assert ir['children'][0]['_prop_type'] == 'string'


def _inst_ir_with_struct(figma_id, comp_id, name, children, w=548, h=346):
    """构造带自定义子树结构的 INSTANCE 节点。"""
    return {
        'figmaId': figma_id, 'figmaName': name, 'figmaType': 'INSTANCE',
        'isComponentInstance': True, 'componentId': comp_id,
        'bb': {'width': w, 'height': h},
        'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
        'css': {'width': f'{w}px', 'height': f'{h}px'}, 'children': children,
    }


def test_same_component_id_different_struct_split():
    """INST-SPLIT-01: 相同 componentId 但子树结构不同的实例不应合并为同一组。
    模拟 Example Product Demo Core 的 BaseLayer vs VIPLayer 场景。
    """
    # BaseLayer: 有 2 个子节点（bg + butterfly），各自是 FRAME 且较大
    base_children = [
        {'figmaId': 'base:bg', 'figmaName': 'BG-GREEN', 'figmaType': 'FRAME',
         'isComponentInstance': False, 'componentId': None,
         'bb': {'width': 548, 'height': 346}, 'isTextNode': False,
         'isImageNode': True, 'css': {}, 'children': []},
        {'figmaId': 'base:butterfly', 'figmaName': 'Butterfly_1', 'figmaType': 'FRAME',
         'isComponentInstance': False, 'componentId': None,
         'bb': {'width': 220, 'height': 175}, 'isTextNode': False,
         'isImageNode': False, 'css': {}, 'children': [
             {'figmaId': 'base:butterfly:img', 'figmaName': '2024_Butterflies', 'figmaType': 'RECTANGLE',
              'isComponentInstance': False, 'componentId': None,
              'bb': {'width': 215, 'height': 170}, 'isTextNode': False,
              'isImageNode': True, 'css': {}, 'children': []}
         ]},
    ]
    # VIPLayer: 有 3 个子节点（bg + butterfly2 + butterfly1），结构不同
    vip_children = [
        {'figmaId': 'vip:bg', 'figmaName': 'BG-BLACK', 'figmaType': 'FRAME',
         'isComponentInstance': False, 'componentId': None,
         'bb': {'width': 548, 'height': 346}, 'isTextNode': False,
         'isImageNode': True, 'css': {}, 'children': []},
        {'figmaId': 'vip:butterfly2', 'figmaName': 'Butterfly_2', 'figmaType': 'FRAME',
         'isComponentInstance': False, 'componentId': None,
         'bb': {'width': 149, 'height': 92}, 'isTextNode': False,
         'isImageNode': False, 'css': {}, 'children': [
             {'figmaId': 'vip:butterfly2:img', 'figmaName': '2024_Butterflies_2', 'figmaType': 'RECTANGLE',
              'isComponentInstance': False, 'componentId': None,
              'bb': {'width': 149, 'height': 92}, 'isTextNode': False,
              'isImageNode': True, 'css': {}, 'children': []}
         ]},
        {'figmaId': 'vip:butterfly1', 'figmaName': 'Butterfly_1', 'figmaType': 'FRAME',
         'isComponentInstance': False, 'componentId': None,
         'bb': {'width': 221, 'height': 177}, 'isTextNode': False,
         'isImageNode': False, 'css': {}, 'children': [
             {'figmaId': 'vip:butterfly1:img', 'figmaName': '2024_Butterflies_1', 'figmaType': 'RECTANGLE',
              'isComponentInstance': False, 'componentId': None,
              'bb': {'width': 213, 'height': 174}, 'isTextNode': False,
              'isImageNode': True, 'css': {}, 'children': []}
         ]},
    ]

    inst_base = _inst_ir_with_struct('8456:11061', '8442:10003', 'Example Product Demo Core', base_children)
    inst_vip = _inst_ir_with_struct('8456:11063', '8442:10003', 'Example Product Demo Core', vip_children)

    ir = _ir_tree([inst_base, inst_vip])
    groups = group_instances(ir, EMPTY_SEM)

    # 因为结构不同，这两个实例不应被合并为同一组
    # 它们或者不出现在结果中（count < 2 per sub-group），或者各自成组
    for g in groups:
        # 不应有一个组同时包含两个结构不同的实例
        fids = {n['figmaId'] for n in g['instances']}
        assert not ({'8456:11061', '8456:11063'} <= fids), \
            "结构不同的实例不应被合并为同一叶子组件组"


def test_same_component_id_same_struct_still_grouped():
    """INST-SPLIT-02: 相同 componentId 且结构相同的实例仍应合并。"""
    children_template = [
        {'figmaId': 'child:bg', 'figmaName': 'BG-GREEN', 'figmaType': 'FRAME',
         'isComponentInstance': False, 'componentId': None,
         'bb': {'width': 548, 'height': 346}, 'isTextNode': False,
         'isImageNode': True, 'css': {}, 'children': []},
        {'figmaId': 'child:butterfly', 'figmaName': 'Butterfly_1', 'figmaType': 'FRAME',
         'isComponentInstance': False, 'componentId': None,
         'bb': {'width': 220, 'height': 175}, 'isTextNode': False,
         'isImageNode': False, 'css': {}, 'children': [
             {'figmaId': 'child:img', 'figmaName': '2024_Butterflies', 'figmaType': 'RECTANGLE',
              'isComponentInstance': False, 'componentId': None,
              'bb': {'width': 215, 'height': 170}, 'isTextNode': False,
              'isImageNode': True, 'css': {}, 'children': []}
         ]},
    ]

    import copy
    inst_a = _inst_ir_with_struct('inst:1', 'comp:card', 'Card', copy.deepcopy(children_template))
    inst_b = _inst_ir_with_struct('inst:2', 'comp:card', 'Card', copy.deepcopy(children_template))

    ir = _ir_tree([inst_a, inst_b])
    groups = group_instances(ir, EMPTY_SEM)

    assert len(groups) == 1
    assert groups[0]['count'] == 2
    assert groups[0]['componentId'] == 'comp:card'


def test_text_segments_in_varying_props():
    """U-350: 真实场景 — 两个实例文本不同且第一个有 textSegments(双色)。
    数据来自 I39641:9303;39641:6388 和 I39641:9303;39641:6400 的 TopUp 组件。
    extract_varying_props 应在 entry 中记录 segments 信息。
    """
    inst_a = {
        'figmaId': 'I39641:9303;39641:6388', 'figmaName': 'Container',
        'figmaType': 'FRAME', 'isTextNode': False, 'isComponentInstance': False,
        'componentId': None, 'bb': {'width': 497, 'height': 140},
        'css': {}, 'children': [{
            'figmaId': 'I39641:9303;39641:6392', 'figmaName': 'Top-up your card here',
            'figmaType': 'TEXT', 'isTextNode': True, 'isComponentInstance': False,
            'componentId': None, 'bb': {'width': 259, 'height': 32},
            'textContent': 'Top-up your card here',
            'textSegments': [
                {'text': 'Top-up your card ', 'color': None},
                {'text': 'here', 'color': '#ff9c2e'},
            ],
            'css': {}, 'children': [],
        }],
    }
    inst_b = {
        'figmaId': 'I39641:9303;39641:6400', 'figmaName': 'Container',
        'figmaType': 'FRAME', 'isTextNode': False, 'isComponentInstance': False,
        'componentId': None, 'bb': {'width': 497, 'height': 140},
        'css': {}, 'children': [{
            'figmaId': 'I39641:9303;39641:6402', 'figmaName': 'Use it throughout the journey',
            'figmaType': 'TEXT', 'isTextNode': True, 'isComponentInstance': False,
            'componentId': None, 'bb': {'width': 259, 'height': 32},
            'textContent': 'Use it throughout the journey',
            'textSegments': None,
            'css': {}, 'children': [],
        }],
    }
    props = extract_varying_props([inst_a, inst_b])
    assert len(props) == 1
    assert props[0]['type'] == 'text'
    assert props[0]['values'] == ['Top-up your card here', 'Use it throughout the journey']
    # 关键断言：segments 被保留
    assert 'segments' in props[0], "varying prop with textSegments should have segments key"
    assert props[0]['segments'][0] == [
        {'text': 'Top-up your card ', 'color': None},
        {'text': 'here', 'color': '#ff9c2e'},
    ]
    assert props[0]['segments'][1] is None


def test_build_instances_data_with_segments():
    """U-351: build_instances_data 应将 segments 信息注入 instanceData。"""
    varying = [{
        'path': [0], 'propName': 'title', 'type': 'text',
        'values': ['Top-up your card here', 'Use it throughout the journey'],
        'segments': [
            [{'text': 'Top-up your card ', 'color': None}, {'text': 'here', 'color': '#ff9c2e'}],
            None,
        ],
    }]
    instances = [
        {'figmaId': 'a', 'children': []},
        {'figmaId': 'b', 'children': []},
    ]
    data = build_instances_data(instances, varying)
    assert data[0]['title'] == 'Top-up your card here'
    assert data[0]['__segments__title'] == [
        {'text': 'Top-up your card ', 'color': None},
        {'text': 'here', 'color': '#ff9c2e'},
    ]
    assert data[1]['title'] == 'Use it throughout the journey'
    assert '__segments__title' not in data[1]


def test_mark_varying_nodes_reactnode_type():
    """U-352: 有 segments 的 varying prop 应标记为 React.ReactNode 类型。"""
    ir = {'figmaId': '1', 'isTextNode': False, 'children': [
        {'figmaId': '1:t', 'isTextNode': True, 'textContent': 'Top-up your card here',
         'textSegments': [{'text': 'Top-up your card ', 'color': None},
                          {'text': 'here', 'color': '#ff9c2e'}],
         'children': []}
    ]}
    varying = [{
        'path': [0], 'propName': 'title', 'type': 'text',
        'values': ['Top-up your card here', 'Use it'],
        'segments': [
            [{'text': 'Top-up your card ', 'color': None}, {'text': 'here', 'color': '#ff9c2e'}],
            None,
        ],
    }]
    mark_varying_nodes(ir, varying)
    assert ir['children'][0]['_prop_name'] == 'title'
    assert ir['children'][0]['_prop_type'] == 'React.ReactNode'


def test_misgroup_returns_none():
    """extract_varying_props 超过 _MAX_VARYING_PROPS 时应返回 None（而非 []）。
    Real data: 12244:11875 (Frame 2147240334) vs 12244:11923 (Frame 2147240335)
    from Homegames page (12244-11664). 两节点 CSS 相同但文本完全不同
    （11875 含 'Top contracts'/'ETH Up/Down' 等，11923 含 'Recent highlights'/'美国初请失业金人数' 等）。
    _MAX_VARYING_PROPS=8，超过阈值 → 返回 None 表示误分组。
    历史 bug：返回 [] 导致生成相同内容并渲染两遍，12244:11923 全部内容丢失。"""
    def _txt(fid, text):
        return {'figmaId': fid, 'figmaType': 'TEXT', 'figmaName': 'text',
                'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
                'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 100, 'height': 20},
                'textContent': text, 'textSegments': None,
                'css': {'font-size': '14px'}, 'children': []}

    def _frame(fid, texts):
        return {'figmaId': fid, 'figmaType': 'FRAME', 'figmaName': 'Frame',
                'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 357, 'height': 400},
                'css': {'display': 'flex', 'flex-direction': 'column'},
                'textContent': None, 'textSegments': None,
                'children': [_txt(f'{fid}-t{i}', t) for i, t in enumerate(texts)]}

    # 9 个文本节点全部不同，超过 _MAX_VARYING_PROPS=8 → 应返回 None
    inst_a = _frame('12244:11875', [
        'Top contracts', 'ETH Up/Down in 15 min', 'Up or Down', '1.28x', '57%',
        'ETH Up/Down in 1h', '1.18x', '57%', 'ETH Up/Down in 15 min 2',
    ])
    inst_b = _frame('12244:11923', [
        'Recent highlights', 'More', '美国初请失业金人数', 'High Impact',
        '06/13/2026, 09:30 PM', 'in 8H 32M', '美联储官员讲话', 'Add', '美国非农就业报告',
    ])

    result = extract_varying_props([inst_a, inst_b])
    assert result is None, (
        f'超过 _MAX_VARYING_PROPS 时必须返回 None 表示误分组，'
        f'实际返回: {result!r}（返回 [] 会导致两个实例渲染相同内容）'
    )


def test_css_color_variant_generates_segments():
    """U-357: 三实例 text1 文本相同结构但 CSS color 不同时（红/绿/绿），
    非模板实例应在 varying prop 的 segments 里记录显式颜色 segment。
    Real data: nodes 11372:39212 (template, red), 11372:39231 (green), 11372:39250 (green)
    from Modal-11372-39177, Frame2147224717 三行历史数据行。
    """
    def _txt(fid, text, css_color):
        return {
            'figmaId': fid, 'figmaType': 'TEXT', 'figmaName': '9,200.78',
            'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 133.3, 'height': 20},
            'textContent': text, 'textSegments': None,
            'css': {'color': css_color, 'font-size': '14px'}, 'children': [],
        }

    def _frame(fid, text_node):
        return {
            'figmaId': fid, 'figmaType': 'FRAME', 'figmaName': 'Frame2147224717',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 400, 'height': 80},
            'textContent': None, 'textSegments': None,
            'css': {'display': 'flex'}, 'children': [text_node],
        }

    # Real data: node 11372:39212 (template), 11372:39231, 11372:39250
    inst_1 = _frame('11372:39208', _txt('11372:39212', '46K', 'var(--bds-red-700-normal)'))
    inst_2 = _frame('11372:39227', _txt('11372:39231', '130K', 'var(--bds-green-700-normal)'))
    inst_3 = _frame('11372:39246', _txt('11372:39250', '50K', 'var(--bds-green-700-normal)'))

    props = extract_varying_props([inst_1, inst_2, inst_3])
    assert props is not None and len(props) == 1
    entry = props[0]
    assert entry['values'] == ['46K', '130K', '50K']
    assert 'segments' in entry, (
        "CSS color 变体的 varying prop 应携带 segments 信息"
    )
    # 模板实例（red）无需显式 segment
    assert entry['segments'][0] is None, "模板实例与自身颜色一致，无需显式 segment"
    # 非模板实例（green）应有显式颜色 segment
    assert entry['segments'][1] == [{'text': '130K', 'color': 'var(--bds-green-700-normal)'}], (
        f"instance2 应生成带绿色的 segment，实际: {entry['segments'][1]}"
    )
    assert entry['segments'][2] == [{'text': '50K', 'color': 'var(--bds-green-700-normal)'}], (
        f"instance3 应生成带绿色的 segment，实际: {entry['segments'][2]}"
    )


def test_same_component_different_root_bg_splits_into_two_groups():
    """U-358: 相同 componentId + 相同结构 + 不同根节点 background-color 应拆成两个独立组件组。
    Real data: 12244:12113/12118 (green bg) 和 12244:12114/12119 (red bg)
    from Homegames page (12244-11664)。当前 bug：两者被归为同一组，红色实例通过
    needs_wrapper 包裹绿色组件，导致内部 green CSS 覆盖外层 red，figma-id 也错误。
    """
    def _trading_btn(fid, bg_color, text):
        return {
            'figmaId': fid, 'figmaName': '5_Trading Button', 'figmaType': 'INSTANCE',
            'isComponentInstance': True, 'componentId': '11372:3555',
            'bb': {'width': 100.0, 'height': 40.0},
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False,
            'css': {'background-color': bg_color, 'width': '100px', 'height': '40px'},
            'children': [{
                'figmaId': f'I{fid};14251:389077', 'figmaName': 'Button',
                'figmaType': 'TEXT', 'isTextNode': True, 'isComponentInstance': False,
                'componentId': None, 'bb': {'width': 60, 'height': 22},
                'textContent': text, 'textSegments': None, 'css': {}, 'children': [],
            }],
        }

    # Real data: 12244:12113, 12244:12118 are green; 12244:12114, 12244:12119 are red
    green_bg = 'color-mix(in srgb, var(--bds-green-100-bg) 16%, transparent)'
    red_bg   = 'color-mix(in srgb, var(--bds-red-100-bg) 16%, transparent)'
    inst_g1 = _trading_btn('12244:12113', green_bg, 'Above 1.02x')
    inst_g2 = _trading_btn('12244:12118', green_bg, 'Above 1.05x')
    inst_r1 = _trading_btn('12244:12114', red_bg,   'Below 1.02x')
    inst_r2 = _trading_btn('12244:12119', red_bg,   'Below 1.05x')

    ir = _ir_tree([inst_g1, inst_r1, inst_g2, inst_r2])
    groups = group_instances(ir, EMPTY_SEM)

    assert len(groups) == 2, (
        f'不同 background-color 的同 componentId 实例应拆成 2 组，实际 {len(groups)} 组。'
        f'当前 bug：红绿实例合并后红色实例通过 needs_wrapper 包裹绿色组件，内层绿色 CSS 覆盖外层红色。'
    )
    bg_colors = set()
    for g in groups:
        bg_colors.add(g['instances'][0].get('css', {}).get('background-color', ''))
    assert green_bg in bg_colors, '应有一组使用绿色背景'
    assert red_bg in bg_colors,   '应有一组使用红色背景'
    for g in groups:
        assert g['count'] == 2, f'每组应有 2 个实例，实际 {g["count"]}'


def test_pass_b_groups_frames_with_varying_text_widths():
    """U-359: Pass B 应将结构相同但文本内容宽度不同的非实例 FRAME 分为一组。
    Real data: nodes 10664:26612 / 10664:26620 from OptionExperience2026 (10664-26578).
    Bug：TEXT 节点宽度（内容驱动）和其包裹 FRAME 宽度均进入 _struct_key，导致
    6 张结构完全相同的 stat 卡片产生 6 个不同哈希，Pass B count=1 全部被过滤。
    修复：depth>=1 的节点宽度不参与哈希（内层宽度为内容驱动，非结构信号）。
    """
    def _stat_card(fid, label_text_w, value_text_w, desc_text_w,
                   label_wrapper_w, fid_suffix=''):
        """构造 vol stat 卡片 IR（真实结构：3 行 × label/value/desc）。
        Real data: Frame 2147224779 pattern from OptionExperience2026 VolTabBar.
        外框: width=180, height≈97.59（取整 bucket: 100x180）
        label 行: label_wrapper（宽度内容驱动） → TEXT（宽度内容驱动）
        value 行: value TEXT
        desc 行: desc TEXT（含彩色 span）
        """
        base_fid = fid + fid_suffix
        return {
            'figmaId': fid, 'figmaName': 'Frame 2147224779',
            'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 180.0, 'height': 97.59375},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {'display': 'flex', 'flex-direction': 'column', 'gap': '8px'},
            'children': [
                # 行 1：label 包裹 FRAME → TEXT（label_wrapper_w 因文字长短不同而异）
                {
                    'figmaId': base_fid + ':r1', 'figmaName': 'Frame 2147224780',
                    'figmaType': 'FRAME',
                    'isComponentInstance': False, 'componentId': None,
                    'bb': {'width': 148.0, 'height': 16.0},
                    'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                    'css': {},
                    'children': [{
                        'figmaId': base_fid + ':r1:wrap', 'figmaName': 'container',
                        'figmaType': 'FRAME',
                        'isComponentInstance': False, 'componentId': None,
                        'bb': {'width': label_wrapper_w, 'height': 16.0},  # 内容驱动
                        'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                        'css': {},
                        'children': [{
                            'figmaId': base_fid + ':r1:txt',
                            'figmaName': 'label', 'figmaType': 'TEXT',
                            'isTextNode': True, 'isComponentInstance': False,
                            'componentId': None,
                            'bb': {'width': label_text_w, 'height': 18.0},  # 内容驱动
                            'textContent': 'LABEL', 'textSegments': None,
                            'isImageNode': False, 'isDecorativeElement': False,
                            'css': {}, 'children': [],
                        }],
                    }],
                },
                # 行 2：value TEXT（宽度内容驱动）
                {
                    'figmaId': base_fid + ':r2', 'figmaName': 'container',
                    'figmaType': 'FRAME',
                    'isComponentInstance': False, 'componentId': None,
                    'bb': {'width': 148.0, 'height': 22.39},
                    'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                    'css': {},
                    'children': [{
                        'figmaId': base_fid + ':r2:txt',
                        'figmaName': 'value', 'figmaType': 'TEXT',
                        'isTextNode': True, 'isComponentInstance': False,
                        'componentId': None,
                        'bb': {'width': value_text_w, 'height': 23.0},  # 内容驱动
                        'textContent': 'VALUE', 'textSegments': None,
                        'isImageNode': False, 'isDecorativeElement': False,
                        'css': {}, 'children': [],
                    }],
                },
                # 行 3：desc TEXT（宽度内容驱动）
                {
                    'figmaId': base_fid + ':r3', 'figmaName': 'container',
                    'figmaType': 'FRAME',
                    'isComponentInstance': False, 'componentId': None,
                    'bb': {'width': 148.0, 'height': 19.19},
                    'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                    'css': {},
                    'children': [{
                        'figmaId': base_fid + ':r3:txt',
                        'figmaName': 'desc', 'figmaType': 'TEXT',
                        'isTextNode': True, 'isComponentInstance': False,
                        'componentId': None,
                        'bb': {'width': desc_text_w, 'height': 20.0},  # 内容驱动
                        'textContent': 'DESC', 'textSegments': None,
                        'isImageNode': False, 'isDecorativeElement': False,
                        'css': {}, 'children': [],
                    }],
                },
            ],
        }

    # Real data: 6 nodes from VolTabBar (10664:26611 container) in OptionExperience2026
    # 各卡片 label/value/desc 文字长度不同 → bb.width 各异
    card1 = _stat_card('10664:26612', label_text_w=68,  label_wrapper_w=84,
                       value_text_w=38,  desc_text_w=99)   # BVOL (30D)
    card2 = _stat_card('10664:26620', label_text_w=104, label_wrapper_w=63,
                       value_text_w=68,  desc_text_w=122)  # IV Structure (30D)
    card3 = _stat_card('10664:26628', label_text_w=114, label_wrapper_w=100,
                       value_text_w=63,  desc_text_w=92)   # Max Pain (26JUN 26)
    card4 = _stat_card('10664:26636', label_text_w=128, label_wrapper_w=100,
                       value_text_w=38,  desc_text_w=90)   # P/C OI Ratio
    card5 = _stat_card('10664:26644', label_text_w=110, label_wrapper_w=100,
                       value_text_w=40,  desc_text_w=110)  # NETGEX
    card6 = _stat_card('10664:26652', label_text_w=118, label_wrapper_w=100,
                       value_text_w=36,  desc_text_w=145)  # 25 Delta Skew (30D)

    ir = _ir_tree([card1, card2, card3, card4, card5, card6])
    groups = group_instances(ir, EMPTY_SEM)

    struct_groups = [g for g in groups if g['componentId'].startswith('__struct__')]
    assert len(struct_groups) == 1, (
        f'Pass B 应将 6 张结构相同的 stat 卡片归为 1 组，实际得到 {len(struct_groups)} 个结构组。'
        f'Bug：内层 FRAME/TEXT 的内容驱动宽度进入 _struct_key，导致哈希各不相同。'
    )
    assert struct_groups[0]['count'] == 6, (
        f'结构组应含 6 个实例，实际 {struct_groups[0]["count"]} 个。'
    )


def test_pass_b_groups_frames_with_different_instance_children():
    """U-360: Pass B 应识别含不同 componentId INSTANCE 子节点的相同结构 FRAME 们。

    场景：3 个 Market Card_Light 样式的非实例 FRAME（类似 SpotX_light 的循环块），
    每个卡片内有一个不同 componentId 的 INSTANCE（代表不同币种图表：BTC、ETH、SOL），
    但卡片整体结构（布局、尺寸、TEXT 层）完全相同 → Pass B 应将 3 张卡片归为 1 组。

    Bug：_struct_key 对 depth>=1 的 INSTANCE 子节点仍包含具体 componentId，
    导致含不同 coin chart 实例的卡片生成不同 struct key，无法被 Pass B 分组。
    Fix：depth>=1 时将 INSTANCE 的 componentId 替换为泛型占位符 '_'。
    """
    def _market_card(figma_id, chart_comp_id, coin_name):
        """非实例 FRAME + 1 个 TEXT + 1 个不同 componentId 的图表 INSTANCE。"""
        return {
            'figmaId': figma_id,
            'figmaName': 'Market Card_Light',
            'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 180, 'height': 124},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {},
            'children': [
                {
                    'figmaId': figma_id + ':title',
                    'figmaName': 'title', 'figmaType': 'TEXT',
                    'isComponentInstance': False, 'componentId': None,
                    'bb': {'width': 60, 'height': 20},
                    'isTextNode': True, 'isImageNode': False, 'isDecorativeElement': False,
                    'textContent': coin_name, 'css': {}, 'children': [],
                },
                {
                    'figmaId': figma_id + ':chart',
                    'figmaName': coin_name + ' chart', 'figmaType': 'INSTANCE',
                    'isComponentInstance': True, 'componentId': chart_comp_id,
                    'bb': {'width': 180, 'height': 60},
                    'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                    'css': {}, 'children': [],
                },
            ],
        }

    cards = [
        _market_card('c1', '4030:41054', 'BTC'),
        _market_card('c2', '4030:41056', 'ETH'),
        _market_card('c3', '4030:41059', 'SOL'),
    ]
    ir = _ir_tree(cards)
    groups = group_instances(ir, EMPTY_SEM)

    struct_groups = [g for g in groups if g['componentId'].startswith('__struct__')]
    assert len(struct_groups) == 1, (
        f'Pass B 应将 3 张结构相同（但图表 componentId 不同）的 Market Card 归为 1 组，'
        f'实际得到 {len(struct_groups)} 个结构组。'
        f'Bug：depth>=1 的 INSTANCE 子节点 componentId 不同导致 struct key 各异。'
    )
    assert struct_groups[0]['count'] == 3, (
        f'结构组应含 3 个实例，实际 {struct_groups[0]["count"]} 个。'
    )


def test_pass_b_nested_inner_cards_extracted_before_outer_rows():
    """U-360: Pass B 应优先提取出现次数多的内层卡片（count=6），而非面积大的外层 Row（count=2）。
    Real data pattern: 两层嵌套 node 176:13423 (3×2 grid) and 循环块 node 4030:41732.
    Bug：Pass B 按面积从大到小排序，Row（1140×220=250800）面积大于 Card（360×200=72000），
    Row 先提取 → Card 进 seen_fids → Card×6 被祖先过滤跳过。
    修复：按 (-count, -area) 排序，Card（count=6）先于 Row（count=2）提取。
    """
    def _make_card(fid, title_w, desc_w):
        """Real data: Card pattern from 两层嵌套 176:13423, width=360, height=200."""
        return {
            'figmaId': fid, 'figmaName': 'Card', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 360, 'height': 200},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': [
                {'figmaId': fid + ':t', 'figmaType': 'TEXT', 'isTextNode': True,
                 'isComponentInstance': False, 'componentId': None,
                 'bb': {'width': title_w, 'height': 24}, 'css': {}, 'children': []},
                {'figmaId': fid + ':d', 'figmaType': 'TEXT', 'isTextNode': True,
                 'isComponentInstance': False, 'componentId': None,
                 'bb': {'width': desc_w, 'height': 16}, 'css': {}, 'children': []},
            ],
        }

    def _make_row(fid, cards):
        """Real data: Row wrapper, width=1140, height=220 (area=250800 > Card area=72000)."""
        return {
            'figmaId': fid, 'figmaName': 'Row', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 1140, 'height': 220},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': cards,
        }

    row1 = _make_row('r1', [_make_card('c1', 120, 80), _make_card('c2', 140, 90),
                             _make_card('c3', 100, 70)])
    row2 = _make_row('r2', [_make_card('c4', 130, 85), _make_card('c5', 110, 75),
                             _make_card('c6', 150, 95)])
    ir = _ir_tree([row1, row2])

    groups = group_instances(ir, EMPTY_SEM)

    card_groups = [g for g in groups if g['figmaName'] == 'Card']
    assert len(card_groups) == 1, (
        f'Card 应被识别为 1 个组，实际 {len(card_groups)} 个。'
        f'Bug：Row（面积更大）先提取后，内层 Card 被祖先过滤跳过。'
    )
    assert card_groups[0]['count'] == 6, (
        f'Card 组应含 6 个实例（来自两行），实际 {card_groups[0]["count"]} 个。'
    )
    card_fids = {n['figmaId'] for n in card_groups[0]['instances']}
    assert card_fids == {'c1', 'c2', 'c3', 'c4', 'c5', 'c6'}, (
        f'Card 组应包含全部 6 张卡片，实际 {card_fids}'
    )

    row_groups = [g for g in groups if g['figmaName'] == 'Row']
    assert len(row_groups) == 1, f'Row 仍应被识别为 1 个组（count=2），实际 {len(row_groups)} 个'
    assert row_groups[0]['count'] == 2


def test_pass_b_absorbs_orphan_with_optional_badge():
    """U-361: Pass B 孤节点吸收——card1 比 card2/3 多一个 height≤30px 无子节点的 FRAME badge，
    三者应被归为同一组（count=3），badge 节点标记为可选。
    Real data pattern: 纵向块 node 4030:41687, USDT card has 'New User Exclusive' badge
    (FRAME height=20px, no children), BTC/ETH cards do not.
    Bug：badge 改变子节点数 (4 vs 3) → _struct_key 不同 → 只有 card2+card3 被归为一组。
    修复：孤节点吸收步骤检测额外的小型空 FRAME 子节点，将孤节点并入最近的结构组。
    """
    def _make_earn_card(fid, has_badge, icon_cid):
        """Real data: EarnCard from 纵向块 4030:41687, width=380, height=350."""
        children = [
            {'figmaId': fid + ':icon', 'figmaType': 'INSTANCE',
             'isComponentInstance': True, 'componentId': icon_cid,
             'bb': {'width': 80, 'height': 80},
             'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
             'css': {}, 'children': []},
            {'figmaId': fid + ':apr', 'figmaType': 'TEXT', 'isTextNode': True,
             'isComponentInstance': False, 'componentId': None,
             'bb': {'width': 120, 'height': 40}, 'css': {}, 'children': []},
            {'figmaId': fid + ':desc', 'figmaType': 'TEXT', 'isTextNode': True,
             'isComponentInstance': False, 'componentId': None,
             'bb': {'width': 200, 'height': 32}, 'css': {}, 'children': []},
        ]
        if has_badge:
            # Real data: 'New User Exclusive' badge, FRAME height=20px, no children
            children.insert(2, {
                'figmaId': fid + ':badge', 'figmaType': 'FRAME',
                'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 100, 'height': 20},
                'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                'css': {}, 'children': [],
            })
        return {
            'figmaId': fid, 'figmaName': 'EarnCard', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 380, 'height': 350},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': children,
        }

    card_usdt = _make_earn_card('e1', has_badge=True,  icon_cid='coin_usdt')
    card_btc  = _make_earn_card('e2', has_badge=False, icon_cid='coin_btc')
    card_eth  = _make_earn_card('e3', has_badge=False, icon_cid='coin_eth')

    ir = _ir_tree([card_usdt, card_btc, card_eth])
    groups = group_instances(ir, EMPTY_SEM)

    earn_groups = [g for g in groups if g['figmaName'] == 'EarnCard']
    assert len(earn_groups) == 1, (
        f'EarnCard 应被归为 1 组（badge 视为可选），实际 {len(earn_groups)} 组。'
        f'Bug：badge 使 card1 struct_key 与 card2/3 不同，card1 成为孤节点被过滤。'
    )
    assert earn_groups[0]['count'] == 3, (
        f'EarnCard 组应含 3 个实例，实际 {earn_groups[0]["count"]} 个。'
    )
    earn_fids = {n['figmaId'] for n in earn_groups[0]['instances']}
    assert earn_fids == {'e1', 'e2', 'e3'}, (
        f'EarnCard 组应包含全部 3 张卡片（含 badge 的 e1），实际 {earn_fids}'
    )


def test_struct_key_image_height_ignored_at_depth1():
    """U-362: 图片节点（isImageNode=True）在 depth >= 1 时，高度不参与结构哈希。
    不同高度的图片节点在同一结构槽位应产生相同 struct_key，
    使包含不同尺寸图片的父容器能被识别为同一可复用组件。

    Real data: Promo Card containers from Brand6PreKyc (nodeId: 4030-41365)
    4030:41524 → image 4030:41528 height=120px
    4030:41535 → image 4030:41539 height=160px
    4030:41546 → image 4030:41550 height=140px
    不同图片高度导致 struct_key 不同 → 3 张 Promo Card 无法被归为同一组件。
    """
    def _make_promo_card(fid, img_h):
        """Real data: Promo Card, FRAME 380x120, child frame contains image + countdown frame."""
        return {
            'figmaId': fid, 'figmaName': 'Promo Card', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 380, 'height': 120},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': [{
                'figmaId': fid + ':outer', 'figmaName': 'frame-2147224373',
                'figmaType': 'FRAME', 'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 360, 'height': 100},
                'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                'css': {}, 'children': [
                    {
                        'figmaId': fid + ':img', 'figmaName': 'image 11',
                        'figmaType': 'RECTANGLE', 'isComponentInstance': False,
                        'componentId': None,
                        'bb': {'width': 360, 'height': img_h},  # ← 不同高度
                        'isTextNode': False, 'isImageNode': True,
                        'isDecorativeElement': False, 'css': {}, 'children': [],
                        'localAssetPath': f'/assets/card/{fid}-img.png',
                    },
                    {
                        'figmaId': fid + ':info', 'figmaName': 'Frame 1312320533',
                        'figmaType': 'FRAME', 'isComponentInstance': False,
                        'componentId': None,
                        'bb': {'width': 360, 'height': 60},
                        'isTextNode': False, 'isImageNode': False,
                        'isDecorativeElement': False, 'css': {}, 'children': [],
                    },
                ],
            }],
        }

    card1 = _make_promo_card('p1', 120)
    card2 = _make_promo_card('p2', 160)
    card3 = _make_promo_card('p3', 140)

    key1 = _struct_key(card1)
    key2 = _struct_key(card2)
    key3 = _struct_key(card3)

    assert key1 == key2 == key3, (
        f'U-362 FAIL: 不同图片高度产生了不同 struct_key，导致父容器无法被识别为同一组件。\n'
        f'  card1(h=120): {key1}\n'
        f'  card2(h=160): {key2}\n'
        f'  card3(h=140): {key3}'
    )


def test_varying_image_height_containers_grouped_with_image_prop():
    """U-363: 内部图片高度不同的同构父容器应被 group_instances 归为一组，
    且 extract_varying_props 应检测到 image 差异并返回 image 类型 varyingProp。

    Real data: Promo Card containers from Brand6PreKyc (nodeId: 4030-41365)
    修复前：3 张 Promo Card struct_key 各不相同 → 不分组 → 渲染为内联 HTML。
    修复后：图片高度忽略 → 同一 struct_key → 分组 → PromoCard 组件 + image prop 循环渲染。
    """
    def _make_promo_card(fid, img_h, asset_path):
        return {
            'figmaId': fid, 'figmaName': 'Promo Card', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 380, 'height': 120},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': [{
                'figmaId': fid + ':outer', 'figmaName': 'frame-2147224373',
                'figmaType': 'FRAME', 'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 360, 'height': 100},
                'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                'css': {}, 'children': [
                    {
                        'figmaId': fid + ':img', 'figmaName': 'image 11',
                        'figmaType': 'RECTANGLE', 'isComponentInstance': False,
                        'componentId': None,
                        'bb': {'width': 360, 'height': img_h},
                        'isTextNode': False, 'isImageNode': True,
                        'isDecorativeElement': False, 'css': {}, 'children': [],
                        'localAssetPath': asset_path,
                    },
                    {
                        'figmaId': fid + ':info', 'figmaName': 'Frame 1312320533',
                        'figmaType': 'FRAME', 'isComponentInstance': False,
                        'componentId': None,
                        'bb': {'width': 360, 'height': 60},
                        'isTextNode': False, 'isImageNode': False,
                        'isDecorativeElement': False, 'css': {}, 'children': [],
                    },
                ],
            }],
        }

    card1 = _make_promo_card('p1', 120, '/assets/promo/4030-41528.png')
    card2 = _make_promo_card('p2', 160, '/assets/promo/4030-41539.png')
    card3 = _make_promo_card('p3', 140, '/assets/promo/4030-41550.png')

    ir = _ir_tree([card1, card2, card3])
    groups = group_instances(ir, EMPTY_SEM)

    promo_groups = [g for g in groups if g['figmaName'] == 'Promo Card']
    assert len(promo_groups) == 1, (
        f'U-363 FAIL: Promo Card 应被归为 1 组，实际 {len(promo_groups)} 组。'
        f'Bug：图片高度不同导致 struct_key 各异，外层容器未分组。'
    )
    assert promo_groups[0]['count'] == 3

    vp = extract_varying_props(promo_groups[0]['instances'])
    assert vp is not None and len(vp) == 1, (
        f'U-363 FAIL: 应检测到 1 个 image varyingProp，实际 {vp}'
    )
    assert vp[0]['type'] == 'image', f'U-363 FAIL: varyingProp 类型应为 image，实际 {vp[0]["type"]}'
    assert vp[0]['values'] == [
        '/assets/promo/4030-41528.png',
        '/assets/promo/4030-41539.png',
        '/assets/promo/4030-41550.png',
    ], f'U-363 FAIL: image values 不符，实际 {vp[0]["values"]}'


def test_vector_svg_icon_detected_as_varying_image_prop():
    """U-364: isVectorNode SVG 图标节点（localAssetPath 各不同）应被检测为 image 类型 varyingProp。

    Root cause: _walk_diff 只检查 isImageNode，忽略了 isVectorNode=True 的 SVG 图标节点，
    导致每个支付方式的图标（Apple Pay / Google Pay / Bank Card）全部用第一个实例的 SVG。

    Real data: MarketCard from BtcPizzaDay (nodeId: 235-14237)
      Template:  I311:14983;24:40473  isVectorNode=True  → /assets/.../I311-14983-24-40473.svg
      Instance2: I311:14989;24:40115  isVectorNode=True  → /assets/.../I311-14989-24-40115.svg
    Expected: extract_varying_props detects 1 image-type varyingProp with 2 different svg paths.
    """
    def _make_market_card(outer_fid, icon_fid, icon_asset):
        return {
            'figmaId': outer_fid, 'figmaName': 'Market Card', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 200, 'height': 80},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': [
                {
                    'figmaId': icon_fid, 'figmaName': 'Vector', 'figmaType': 'VECTOR',
                    'isComponentInstance': False, 'componentId': None,
                    'bb': {'width': 24, 'height': 24},
                    'isTextNode': False, 'isImageNode': False, 'isVectorNode': True,
                    'isDecorativeElement': False, 'css': {}, 'children': [],
                    'localAssetPath': icon_asset,
                },
            ],
        }

    # Real data: Apple Pay (311:14982/14983) and Google Pay (311:14988/14989)
    card1 = _make_market_card('311:14982', 'I311:14983;24:40473',
                               '/assets/BtcPizzaDay/I311-14983-24-40473.svg')
    card2 = _make_market_card('311:14988', 'I311:14989;24:40115',
                               '/assets/BtcPizzaDay/I311-14989-24-40115.svg')

    vp = extract_varying_props([card1, card2])
    assert vp is not None and len(vp) == 1, (
        f'U-364 FAIL: 应检测到 1 个 image varyingProp（SVG icon），实际 {vp}'
    )
    assert vp[0]['type'] == 'image', (
        f'U-364 FAIL: varyingProp 类型应为 image，实际 {vp[0]["type"]}'
    )
    assert '/assets/BtcPizzaDay/I311-14983-24-40473.svg' in vp[0]['values'], (
        f'U-364 FAIL: Apple Pay SVG path not in values: {vp[0]["values"]}'
    )
    assert '/assets/BtcPizzaDay/I311-14989-24-40115.svg' in vp[0]['values'], (
        f'U-364 FAIL: Google Pay SVG path not in values: {vp[0]["values"]}'
    )


def test_inner_frame_height_ignored_for_struct_key():
    """U-365: depth>=2 の FRAME/GROUP height 差異は struct_key に含めてはならない。

    Root cause: _struct_key で depth>=1 は w=0 だが h は保持される。
    inner text/layout frames は Auto Layout のため高さがコンテンツで決まり、
    構造的には同一のノード間で h が異なると struct_key が変わってしまう。

    Real data: 311:15268 の 4 直接子フレーム
      Row1 (311:15269): inner FRAME height=140px（コンテンツが短い）
      Row2 (311:15312): inner FRAME height=180px（コンテンツが長い）
    → struct_key が違うため Row1 が Frame1410117561 グループに入らない。

    修正後: depth>=2 の FRAME/GROUP は h=0 扱い → 同じ struct_key → 4行全部グループ化。
    """
    def _make_row(fid, inner_frame_h):
        """Real-world pattern: Group > Group[Vector] + Frame[Frame + Text + Frame]"""
        return {
            'figmaId': fid, 'figmaName': 'Frame 1410117560', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 1200, 'height': 180},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': [
                {   # depth-1: GROUP 1000005300
                    'figmaId': fid + ':g0', 'figmaName': 'Group 1000005300', 'figmaType': 'GROUP',
                    'isComponentInstance': False, 'componentId': None,
                    'bb': {'width': 180, 'height': inner_frame_h},
                    'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                    'css': {}, 'children': [
                        {   # depth-2: inner GROUP (icon slot)
                            'figmaId': fid + ':g0:icon', 'figmaName': 'Group icon',
                            'figmaType': 'GROUP', 'isComponentInstance': False, 'componentId': None,
                            'bb': {'width': 80, 'height': 80},
                            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                            'css': {}, 'children': [],
                        },
                        {   # depth-2: FRAME with text — height varies by content
                            'figmaId': fid + ':g0:txt', 'figmaName': 'Frame text',
                            'figmaType': 'FRAME', 'isComponentInstance': False, 'componentId': None,
                            'bb': {'width': 180, 'height': inner_frame_h},  # ← DIFFERS between rows
                            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
                            'css': {}, 'children': [],
                        },
                    ],
                },
            ],
        }

    row1 = _make_row('311:15269', inner_frame_h=140)  # Real: Apple Vision Pro row
    row2 = _make_row('311:15312', inner_frame_h=180)  # Real: PS5 row

    k1 = _struct_key(row1, depth=0)
    k2 = _struct_key(row2, depth=0)
    assert k1 == k2, (
        f'U-365 FAIL: depth>=2 の FRAME height 差（140 vs 180）は struct_key に影響してはならない。\n'
        f'  row1 key: {k1}\n'
        f'  row2 key: {k2}'
    )

    ir = _ir_tree([row1, row2])
    groups = group_instances(ir, EMPTY_SEM)
    grouped = [g for g in groups if len(g.get('instances', [])) >= 2]
    assert len(grouped) >= 1, (
        f'U-365 FAIL: depth>=2 の height 差のある 2 行は同一グループになるべき。'
        f'groups={[(g["figmaName"], g["count"]) for g in groups]}'
    )


def test_asymmetric_icon_instances_detected_as_varying():
    """U-367: 图标区域（Figma 组件实例）在不同 parent 实例间结构不同时，仍应检测为 varying prop。

    Root cause: _walk_diff 从模板视角递归，若模板处是 component instance（非 image/vector）
    但其他实例在同路径是 vector（有 localAssetPath），现有逻辑无法收集到 varying 信息。

    Real data: MarketCardLight (Brand6PreKyc 4030-41365)
      Template (MCDX 4030:41743) at path [0]: MCD-dark isComponentInstance=True
        → child[0] = Ticker=MCD isImageNode=True lap=I4030-41749-22793-13549.png
      Instance 2 (SOMI 4030:41753) at path [0]: SOMI-dark isComponentInstance=True
        → child[0] = Ticker=SOMI isImageNode=True lap=I4030-41759-24434-13706.png
      Instance 3 (XO 4030:41763) at path [0]: XO-light isVectorNode=True
        lap=4030-41769.svg (NO children; cannot reach [0][0])
    Expected: extract_varying_props detects 1 image varyingProp for the coin icon.
    """
    def _make_card(fid, icon_fid, icon_lap, icon_is_image, icon_is_vector, has_icon_child=True):
        """Make a MarketCard with inner icon structure."""
        icon_node = {
            'figmaId': icon_fid, 'figmaName': 'coin icon',
            'figmaType': 'INSTANCE', 'isComponentInstance': True,
            'bb': {'width': 32, 'height': 32},
            'isTextNode': False, 'isImageNode': icon_is_image, 'isVectorNode': icon_is_vector,
            'isDecorativeElement': False, 'css': {}, 'children': [],
            'localAssetPath': icon_lap,
        }
        if has_icon_child and not icon_is_vector:
            # PNG coin icons have a child image node
            icon_node['isImageNode'] = False  # outer comp is not image
            icon_node['isVectorNode'] = False  # outer comp is not vector
            icon_node['localAssetPath'] = ''   # outer comp has no direct asset
            icon_node['children'] = [{
                'figmaId': 'I' + icon_fid + ';inner',
                'figmaName': 'Ticker image',
                'figmaType': 'RECTANGLE',
                'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 32, 'height': 32},
                'isTextNode': False, 'isImageNode': True, 'isVectorNode': False,
                'isDecorativeElement': False, 'css': {}, 'children': [],
                'localAssetPath': icon_lap,
            }]
        return {
            'figmaId': fid, 'figmaName': 'MarketCard', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 300, 'height': 80},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': [icon_node],
        }

    # MCDX: comp inst → child image (PNG)
    card1 = _make_card('4030:41743', '4030:41749', '/assets/I4030-41749-22793-13549.png',
                       icon_is_image=False, icon_is_vector=False, has_icon_child=True)
    # SOMI: comp inst → child image (different PNG)
    card2 = _make_card('4030:41753', '4030:41759', '/assets/I4030-41759-24434-13706.png',
                       icon_is_image=False, icon_is_vector=False, has_icon_child=True)
    # XO: comp inst IS a vector/SVG itself (no children)
    card3 = _make_card('4030:41763', '4030:41769', '/assets/4030-41769.svg',
                       icon_is_image=False, icon_is_vector=True, has_icon_child=False)

    vp = extract_varying_props([card1, card2, card3])
    assert vp is not None and len(vp) >= 1, (
        f'U-367 FAIL: 应检测到 1 个图标 varying prop，实际 {vp}'
    )
    icon_prop = next((v for v in vp if v.get('type') == 'image'), None)
    assert icon_prop is not None, (
        f'U-367 FAIL: 图标应为 image 类型 varying prop，实际 {[v["type"] for v in vp]}'
    )
    values = icon_prop.get('values', [])
    assert len(set(values)) == 3, (
        f'U-367 FAIL: 3个实例应有3个不同图标路径，实际 {values}'
    )


def test_multilayer_icon_detected_as_icon_tree():
    """U-368: 多层 SVG 图标（模板有多个 vector 子层）应整体检测为 icon_tree 类型 varyingProp。

    场景：
      Template (MNT coin): FRAME → [vector-layer1.svg, vector-layer2.svg, vector-layer3.svg]
      Other (MCDX coin):   FRAME → [image-mcdx.png]  (单层 PNG)
    预期：extract_varying_props 返回 1 个 type='icon_tree' prop，values 是 IR 节点。
    不应将各 vector 子层单独检测为独立 image props（那样会漏掉 layer2/3）。
    """
    def _make_multilayer_coin(fid, layers):
        """layers: [(lap, is_image, is_vector), ...]"""
        children = []
        for i, (lap, is_img, is_vec) in enumerate(layers):
            children.append({
                'figmaId': f'{fid}:l{i}', 'figmaName': f'layer{i}',
                'figmaType': 'VECTOR', 'isComponentInstance': False,
                'bb': {'x': 0, 'y': 0, 'width': 40, 'height': 40},
                'isTextNode': False, 'isImageNode': is_img, 'isVectorNode': is_vec,
                'isDecorativeElement': True, 'css': {}, 'children': [],
                'localAssetPath': lap,
            })
        return {
            'figmaId': fid, 'figmaName': 'CoinIcon', 'figmaType': 'FRAME',
            'isComponentInstance': True, 'componentId': f'comp:{fid}',
            'bb': {'x': 0, 'y': 0, 'width': 40, 'height': 40},
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'css': {}, 'children': children,
        }

    # MNT: 3 vector layers (multi-layer)
    mnt_icon = _make_multilayer_coin('mnt:1', [
        ('/assets/mnt-layer1.svg', False, True),
        ('/assets/mnt-layer2.svg', False, True),
        ('/assets/mnt-layer3.svg', False, True),
    ])
    # MCDX: 1 image child (single-layer)
    mcdx_icon = _make_multilayer_coin('mcdx:1', [
        ('/assets/mcdx.png', True, False),
    ])

    def _card(fid, icon):
        return {
            'figmaId': fid, 'figmaName': 'CoinCard', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 300, 'height': 80},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': [icon],
        }

    card_mnt  = _card('card:mnt', mnt_icon)
    card_mcdx = _card('card:mcdx', mcdx_icon)

    vp = extract_varying_props([card_mnt, card_mcdx])
    assert vp is not None, 'U-368 FAIL: extract_varying_props 不应返回 None'
    icon_props = [v for v in vp if v.get('type') == 'icon_tree']
    assert len(icon_props) == 1, (
        f'U-368 FAIL: 应有 1 个 icon_tree prop，实际 type 列表: {[v["type"] for v in (vp or [])]}'
    )
    # values 应是 IR 节点（dict），不是字符串路径
    assert isinstance(icon_props[0]['values'][0], dict), (
        f'U-368 FAIL: icon_tree values 应为 IR 节点 dict，实际: {type(icon_props[0]["values"][0])}'
    )
    # 不应有单独的 image props（不应把 layer1 单独提取出来）
    image_props = [v for v in vp if v.get('type') == 'image']
    assert len(image_props) == 0, (
        f'U-368 FAIL: 不应有单独的 image prop（多层图标应整体作为 icon_tree），实际: {image_props}'
    )


def test_icon_tree_mark_varying_nodes_sets_reactnode_type():
    """U-369: mark_varying_nodes 对 icon_tree 类型应设置 _prop_type=React.ReactNode, _prop_kind=icon_tree。"""
    icon_node = {
        'figmaId': 'icon:1', 'figmaName': 'CoinIcon', 'figmaType': 'FRAME',
        'isComponentInstance': True, 'componentId': 'comp:icon',
        'bb': {'width': 40, 'height': 40},
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'css': {}, 'children': [],
    }
    ir = {
        'figmaId': 'card:1', 'figmaName': 'CoinCard', 'figmaType': 'FRAME',
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': 300, 'height': 80},
        'isTextNode': False, 'isImageNode': False, 'css': {}, 'children': [icon_node],
    }
    varying = [{'path': [0], 'type': 'icon_tree', 'propName': 'coinIcon',
                'values': [icon_node, icon_node]}]
    mark_varying_nodes(ir, varying)
    marked = ir['children'][0]
    assert marked.get('_prop_name') == 'coinIcon', (
        f'U-369 FAIL: _prop_name 应为 coinIcon，实际: {marked.get("_prop_name")}'
    )
    assert marked.get('_prop_type') == 'React.ReactNode', (
        f'U-369 FAIL: icon_tree 应标记为 React.ReactNode，实际: {marked.get("_prop_type")}'
    )
    assert marked.get('_prop_kind') == 'icon_tree', (
        f'U-369 FAIL: icon_tree 应标记为 icon_tree，实际: {marked.get("_prop_kind")}'
    )


def test_large_multilayer_icon_detected_without_size_guard():
    """U-374: 超过 64px 的多层图标（如 BTC-DARK 108×108）也应检测为 icon_tree。

    Real data: 4030:41718 BTC-DARK (108×108) has 2 vector children (the Bitcoin logo layers).
    4030:41730 eth-LIGHT is a direct isVectorNode=True (single-layer).
    Previously: size guard (≤64px) excluded BTC-DARK → only first layer used → icon broken.
    After fix: rely on _has_text_descendants instead of size → BTC-DARK is icon_tree.
    """
    # BTC-DARK: 108×108 INSTANCE with 2 vector children
    btc_layers = [
        {'figmaId': 'I-btc;v1', 'figmaName': 'Vector', 'figmaType': 'VECTOR',
         'isVectorNode': True, 'isImageNode': False, 'isTextNode': False,
         'localAssetPath': '/assets/btc-layer1.svg',
         'isDecorativeElement': True, 'css': {}, 'children': []},
        {'figmaId': 'I-btc;v2', 'figmaName': 'Vector2', 'figmaType': 'VECTOR',
         'isVectorNode': True, 'isImageNode': False, 'isTextNode': False,
         'localAssetPath': '/assets/btc-layer2.svg',
         'isDecorativeElement': True, 'css': {}, 'children': []},
    ]
    btc_dark = {
        'figmaId': 'btc:1', 'figmaName': 'BTC-DARK', 'figmaType': 'INSTANCE',
        'isComponentInstance': True, 'componentId': 'comp:btc',
        'bb': {'width': 108, 'height': 108},
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'css': {}, 'children': btc_layers,
    }
    # ETH: direct vector (single-layer)
    eth_light = {
        'figmaId': 'eth:1', 'figmaName': 'eth-LIGHT', 'figmaType': 'INSTANCE',
        'isComponentInstance': True, 'componentId': 'comp:eth',
        'bb': {'width': 108, 'height': 108},
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': True,
        'isDecorativeElement': False, 'localAssetPath': '/assets/eth.svg',
        'css': {}, 'children': [],
    }

    def _card(fid, icon):
        return {
            'figmaId': fid, 'figmaName': 'EarnCard', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 400, 'height': 600},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {}, 'children': [icon],
        }

    card_btc = _card('card:btc', btc_dark)
    card_eth = _card('card:eth', eth_light)

    vp = extract_varying_props([card_btc, card_eth])
    assert vp is not None, 'U-374 FAIL: extract_varying_props 返回 None'
    icon_tree_props = [v for v in vp if v.get('type') == 'icon_tree']
    assert len(icon_tree_props) == 1, (
        f'U-374 FAIL: 108×108 的多层图标应检测为 icon_tree，'
        f'实际: {[v["type"] for v in (vp or [])]}'
    )
    # values 应是 IR 节点，保留两层
    assert isinstance(icon_tree_props[0]['values'][0], dict), \
        'U-374 FAIL: icon_tree values[0] 应是 IR 节点 dict'


def test_u424_expand_ir_max_css_skips_different_image_assets():
    """U-424: _expand_ir_max_css 不应扩展 isImageNode 子节点的 CSS 尺寸，
    当 template 与 other 的 localAssetPath 不同时（不同图片资源，尺寸不应互相覆盖）。

    Real data: EarnPointsOnCardPaySection Pic 叶子组件，
    step1 250:3295 (484px) 被错误扩展成 step2 250:3300 (1075.81px)。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from split_components import _expand_ir_max_css

    # Real data: node 250:3295 from EarnPointsOnCardPaySection (merged-250-2618)
    template_img = {
        'figmaId': '250:3295',
        'figmaName': 'image 5058',
        'isImageNode': True,
        'localAssetPath': '/assets/EuDepositCampaign/250-3295.png',
        'css': {'width': '484px', 'height': '484px', 'position': 'absolute',
                'left': '-115px', 'top': '-100px'},
        'children': []
    }
    template_pic = {
        'figmaId': '250:3291',
        'figmaName': 'Pic',
        'css': {'width': '240px', 'height': '240px', 'overflow': 'hidden'},
        'children': [template_img]
    }

    # Real data: node 250:3300 from step2 (different image, wider)
    other_img = {
        'figmaId': '250:3300',
        'figmaName': 'image 5042',
        'isImageNode': True,
        'localAssetPath': '/assets/EuDepositCampaign/250-3300.png',
        'css': {'width': '1075.81px', 'height': '432.34px', 'position': 'absolute',
                'right': '-64.81px', 'top': '-150px'},
        'children': []
    }
    other_pic = {
        'figmaId': '250:3297',
        'figmaName': 'Pic',
        'css': {'width': '240px', 'height': '240px', 'overflow': 'hidden'},
        'children': [other_img]
    }

    _expand_ir_max_css(template_pic, [other_pic])

    actual_width = template_img['css']['width']
    assert actual_width == '484px', (
        f'U-424 FAIL: _expand_ir_max_css 不应将不同图片资源的 width 覆盖为 other 的值。'
        f' 期望 484px，实际 {actual_width}'
    )


if __name__ == '__main__':
    tests = [test_groups_by_component_id, test_skips_single_instance,
             test_skips_icons, test_extract_varying_props_text,
             test_extract_no_props_when_same, test_build_instances_data,
             test_mark_varying_nodes,
             test_same_component_id_different_struct_split,
             test_same_component_id_same_struct_still_grouped,
             test_text_segments_in_varying_props,
             test_build_instances_data_with_segments,
             test_mark_varying_nodes_reactnode_type,
             test_misgroup_returns_none,
             test_css_color_variant_generates_segments,
             test_same_component_different_root_bg_splits_into_two_groups,
             test_pass_b_groups_frames_with_varying_text_widths,
             test_pass_b_groups_frames_with_different_instance_children,
             test_pass_b_nested_inner_cards_extracted_before_outer_rows,
             test_pass_b_absorbs_orphan_with_optional_badge,
             test_struct_key_image_height_ignored_at_depth1,
             test_varying_image_height_containers_grouped_with_image_prop,
             test_vector_svg_icon_detected_as_varying_image_prop,
             test_inner_frame_height_ignored_for_struct_key,
             test_asymmetric_icon_instances_detected_as_varying,
             test_multilayer_icon_detected_as_icon_tree,
             test_icon_tree_mark_varying_nodes_sets_reactnode_type,
             test_large_multilayer_icon_detected_without_size_guard,
             test_u424_expand_ir_max_css_skips_different_image_assets]
    passed = 0
    for t in tests:
        try:
            t(); print(f'  ✓ {t.__name__}'); passed += 1
        except Exception as e:
            print(f'  ✗ {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')
