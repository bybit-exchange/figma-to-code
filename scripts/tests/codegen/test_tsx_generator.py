import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.tsx_generator import generate_tsx, render_jsx_body_with_leaf_refs


def _base_ir(name='Hero', tag='section', cls='hero'):
    return {
        'figmaId': '1:1', 'figmaName': name,
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex'}, 'children': [],
        'semantic': {
            'htmlTag': tag, 'className': cls,
            'componentName': name, 'props': [],
        },
    }


def test_uses_export_default():
    ir = _base_ir()
    tsx = generate_tsx(ir)
    assert 'export default function' in tsx
    assert 'export function' not in tsx.replace('export default function', '')


def test_prop_name_replaces_text_content():
    ir = _base_ir()
    ir['children'] = [{
        'figmaId': '1:2', 'figmaName': 'title',
        'isTextNode': True, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': 'Static Title', 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {}, 'children': [],
        '_prop_name': 'title', '_prop_type': 'string',
        'semantic': {'htmlTag': 'span', 'className': 'title', 'props': []},
    }]
    tsx = generate_tsx(ir)
    assert '{title}' in tsx
    assert 'Static Title' not in tsx


def test_props_interface_generated():
    ir = _base_ir()
    ir['children'] = [{
        'figmaId': '1:2', 'figmaName': 'title',
        'isTextNode': True, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': 'X', 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {}, 'children': [],
        '_prop_name': 'title', '_prop_type': 'string',
        'semantic': {'htmlTag': 'span', 'className': 'title', 'props': []},
    }]
    tsx = generate_tsx(ir)
    assert 'interface HeroProps' in tsx
    assert 'title: string' in tsx


def test_no_props_interface_without_markers():
    ir = _base_ir()
    tsx = generate_tsx(ir)
    assert 'interface' not in tsx


def test_page_text_token_uses_type_import():
    """PageTextToken は export type なので import 側も type キーワードが必要。
    Vite/esbuild の ESM ランタイムは type-only export を value として解決できず
    'does not provide an export named PageTextToken' エラーになる。"""
    ir = _base_ir()
    texts_map = {'title': 'Hello', 'body': 'World'}
    tsx = generate_tsx(ir, texts_map=texts_map)
    # Must use type-only import so esbuild strips it at transpile time
    # Code generates 'import type { PageTextToken }' (whole-line type import)
    assert 'import type { PageTextToken }' in tsx, (
        "PageTextToken は export type なので 'import type { PageTextToken }' "
        "が必要。value import では Vite ESM ランタイムエラーになる"
    )
    # Ensure the value imports (PAGE_TEXTS, I18N_NS) are still present
    assert 'PAGE_TEXTS' in tsx
    assert 'I18N_NS' in tsx


def test_leaf_call_renders_segments_as_jsx():
    """U-354: 调用处带 segments 的 varying prop 应生成多色 JSX 而非纯字符串。
    数据特征：第一个实例 "Top-up your card here" 中 "here" 为橙色 #ff9c2e。
    """
    section_ir = {
        'figmaId': 'sec:1', 'figmaName': 'Section',
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {}, 'children': [{
            'figmaId': 'I39641:9303;39641:6388', 'figmaName': 'Container',
            'isTextNode': False, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {}, 'children': [],
            'semantic': {'htmlTag': 'div', 'className': 'container', 'props': []},
        }],
        'semantic': {'htmlTag': 'section', 'className': 'section', 'props': []},
    }
    leaf_map = {
        'I39641:9303;39641:6388': {
            'componentName': 'TopUp',
            'varyingProps': [{'propName': 'topUpYourCardHere', 'type': 'text'}],
            'instanceData': {
                'topUpYourCardHere': 'Top-up your card here',
                '__segments__topUpYourCardHere': [
                    {'text': 'Top-up your card ', 'color': None},
                    {'text': 'here', 'color': '#ff9c2e'},
                ],
            },
            'rootClassName': 'container',
            'isMoly': False,
            'ccComponent': '',
            'inlineSnippet': None,
            'iconColor': '',
        },
    }
    jsx = render_jsx_body_with_leaf_refs(section_ir, leaf_map)
    assert "color:'#ff9c2e'" in jsx, \
        f"应生成带 color 的 span 而非纯字符串，实际: {jsx}"
    assert '<>' in jsx or 'span' in jsx, \
        "多色文案应生成 JSX fragment 或 span"


def test_reactnode_prop_type_for_text_segments():
    """U-353: 带 textSegments 的 varying prop 应生成 React.ReactNode 类型。
    数据特征来自 I39641:9303;39641:6392 — "Top-up your card here" 双色文案。
    """
    ir = _base_ir(name='TopUp')
    ir['children'] = [{
        'figmaId': 'I39641:9303;39641:6392', 'figmaName': 'Top-up your card here',
        'isTextNode': True, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': 'Top-up your card here',
        'textSegments': [
            {'text': 'Top-up your card ', 'color': None},
            {'text': 'here', 'color': '#ff9c2e'},
        ],
        'localAssetPath': None, 'lineTypes': [],
        'css': {}, 'children': [],
        '_prop_name': 'topUpYourCardHere', '_prop_type': 'React.ReactNode',
        'semantic': {'htmlTag': 'span', 'className': 'top-up-your-card-here', 'props': []},
    }]
    tsx = generate_tsx(ir)
    assert 'topUpYourCardHere: React.ReactNode' in tsx, \
        "textSegments varying prop 的类型应为 React.ReactNode"
    assert '{topUpYourCardHere}' in tsx, \
        "组件内部应渲染 {propName} 而非硬编码文案"


def test_segments_trailing_text_preserved():
    """U-355: textSegments 未覆盖完整文本时，trailing 部分必须保留。
    数据来自 I39648:4883;39603:19866：
      textContent = "Buy a premium product  here\\nSign up and apply for your Card."
      textSegments = [{"text":"Buy a premium product  ","fontWeight":"700"},{"text":"here\\n","color":"#ff9c2e","fontWeight":"700"}]
    segments 只覆盖第一行，第二行副标题是 trailing 必须保留。
    """
    section_ir = {
        'figmaId': 'sec:1', 'figmaName': 'Section',
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {}, 'children': [{
            'figmaId': 'I39648:4883;39603:19865', 'figmaName': 'Container',
            'isTextNode': False, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {}, 'children': [],
            'semantic': {'htmlTag': 'div', 'className': 'container', 'props': []},
        }],
        'semantic': {'htmlTag': 'section', 'className': 'section', 'props': []},
    }
    leaf_map = {
        'I39648:4883;39603:19865': {
            'componentName': 'Frame1410117791',
            'varyingProps': [{'propName': 'title', 'type': 'text'}],
            'instanceData': {
                'title': 'Buy a premium product  here\nSign up and apply for your Card.',
                '__segments__title': [
                    {'text': 'Buy a premium product  ', 'color': None, 'fontWeight': '700'},
                    {'text': 'here\n', 'color': '#ff9c2e', 'fontWeight': '700'},
                ],
            },
            'rootClassName': 'container',
            'isMoly': False,
            'ccComponent': '',
            'inlineSnippet': None,
            'iconColor': '',
        },
    }
    jsx = render_jsx_body_with_leaf_refs(section_ir, leaf_map)
    assert 'Sign up and apply' in jsx, \
        f"trailing 副标题文案必须保留，实际输出: {jsx}"
    assert "color:'#ff9c2e'" in jsx, \
        "标题部分 'here' 的橙色样式必须存在"


def test_single_segment_prop_renders_multicolor_jsx():
    """U-356: 仅 1 个 segment 的 varying prop（有 trailing 文本）应生成多色 JSX，而非纯字符串。
    Real data: node I11372:39208;30281:76072 (Quote data) from Modal-11372-39177,
    Frame2147224717 组件。textContent="SOL  -0.15%",
    textSegments=[{text:"SOL", color:"#ffffff", fontWeight:"400"}]
    "SOL" 白色，"  -0.15%" 继承 CSS 颜色（红色）——双色文案。
    当前 bug：len(segments) > 1 条件拦截了 1-segment 的情况，输出纯字符串。
    """
    section_ir = {
        'figmaId': 'sec:1', 'figmaName': 'Section',
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {}, 'children': [{
            'figmaId': 'I11372:39208;32326:26653', 'figmaName': 'Container',
            'isTextNode': False, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {}, 'children': [],
            'semantic': {'htmlTag': 'div', 'className': 'tag-trading', 'props': []},
        }],
        'semantic': {'htmlTag': 'section', 'className': 'section', 'props': []},
    }
    leaf_map = {
        'I11372:39208;32326:26653': {
            'componentName': 'Frame2147224717',
            'varyingProps': [{'propName': 'quoteData', 'type': 'text'}],
            'instanceData': {
                # Real data: node I11372:39208;30281:76072, textContent="SOL  -0.15%"
                'quoteData': 'SOL  -0.15%',
                '__segments__quoteData': [
                    {'text': 'SOL', 'color': '#ffffff', 'fontWeight': '400'},
                ],
            },
            'rootClassName': 'tag-trading',
            'isMoly': False,
            'ccComponent': '',
            'inlineSnippet': None,
            'iconColor': '',
        },
    }
    jsx = render_jsx_body_with_leaf_refs(section_ir, leaf_map)
    assert "color:'#ffffff'" in jsx, (
        f"1-segment quoteData 应生成带 color:#ffffff 的 span，实际: {jsx}"
    )
    assert '-0.15%' in jsx, (
        f"trailing 文本 '  -0.15%' 必须保留，实际: {jsx}"
    )


def test_leaf_call_includes_data_figma_id_on_no_wrapper_path():
    """U-357: 无 wrapper 路径的叶子组件调用应在组件 tag 上带 data-figma-id，
    便于在浏览器 DOM 中定位各实例对应的 Figma 节点。

    Real data: TaskCard2Row instances from MyPage (nodeId: 169-33787)
    figmaIds: 169:35571, 169:35579, 169:35587, 169:35595
    当前行为：<TaskCard2Row text0="行情洞察" />（无 data-figma-id）
    预期行为：<TaskCard2Row data-figma-id="169:35571" text0="行情洞察" />
    """
    # 构造最小 section IR：包含一个叶子实例节点
    section_ir = {
        'figmaId': 'section:root', 'figmaName': 'Section3',
        'figmaType': 'FRAME', 'isComponentInstance': False,
        'bb': {'width': 1200, 'height': 400},
        'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
        'css': {'display': 'flex', 'flex-direction': 'column'},
        'semantic': {'htmlTag': 'section', 'className': 'section3', 'componentName': 'Section3', 'props': []},
        'children': [{
            'figmaId': '169:35571', 'figmaName': 'TaskCard',
            'figmaType': 'FRAME', 'isComponentInstance': False,
            'bb': {'width': 200, 'height': 80},
            'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
            'css': {'display': 'flex'},
            'semantic': {'htmlTag': 'div', 'className': 'task-card', 'componentName': 'TaskCard', 'props': []},
            'children': [],
        }],
    }
    leaf_map = {
        '169:35571': {
            'componentName': 'TaskCard2Row',
            'varyingProps': [{'propName': 'text0', 'type': 'text', 'path': []}],
            'instanceData': {'text0': '行情洞察'},
            'rootClassName': 'task-card',   # same as inst_cls → no wrapper
            'isMoly': False,
            'ccComponent': '',
            'inlineSnippet': None,
            'iconColor': '',
        },
    }
    jsx = render_jsx_body_with_leaf_refs(section_ir, leaf_map)
    assert 'data-figma-id="169:35571"' in jsx, (
        f'U-357 FAIL: leaf call should include data-figma-id="169:35571".\n'
        f'Actual JSX:\n{jsx}'
    )
    # data-figma-id 必须紧接在组件关闭标签 /> 之前，不能破坏 prop 值中的 JSX fragment </>
    # Real bug: .replace('/>', ..., 1) 会误替换 airpodsPro2={<>...</>} 中的第一个 />
    assert 'data-figma-id="169:35571" />' in jsx, (
        f'U-357 FAIL: data-figma-id 必须在最后的 /> 之前（rfind），而非 prop 值中的 />。\n'
        f'Actual: {jsx}'
    )


def test_decorative_vector_with_prop_name_uses_dynamic_src():
    """U-366: isDecorativeElement + isVectorNode 的 img，若已标记 _prop_name，
    应使用 src={propName} 而非硬编码的 localAssetPath。

    Root cause: tsx_generator._render_node 对装饰性 vector 节点（isDecorativeElement=True）
    在 line 528 直接用 localAssetPath 渲染 img，没有检查 _prop_name。
    结果：icon1 prop 写入了 interface/destructure，但 img src 仍硬编码第一个实例的路径。

    Real data: TaskCard2Row from MyPage (169-33787)
      isDecorativeElement=True, isVectorNode=True
      _prop_name='icon1', localAssetPath='/assets/.../I169-35578-2715-4498.svg'
    Expected: src={icon1}  NOT  src="/assets/.../I169-35578-2715-4498.svg"
    """
    icon_node = {
        'figmaId': 'I169:35578;2715:4498', 'figmaName': 'icon1',
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': True, 'isDecorativeElement': True,
        'textContent': None, 'textSegments': None,
        'localAssetPath': '/assets/MyPage/I169-35578-2715-4498.svg',
        'lineTypes': [],
        'css': {'width': '16px', 'height': '16px'},
        'children': [],
        '_prop_name': 'icon1',
        '_prop_type': 'string',
        'semantic': {
            'htmlTag': 'img', 'className': 'icon1-I169-35578-2715-4498',
            'componentName': None, 'props': [],
        },
    }
    root_ir = {**_base_ir('TaskCard2Row', 'div', 'task-card'), 'children': [icon_node]}
    root_ir['semantic']['props'] = [{'name': 'icon1', 'type': 'string'}]

    tsx = generate_tsx(root_ir, css_ext='less', inject_page_env=False)

    assert 'src={icon1}' in tsx, (
        f'U-366 FAIL: 装饰性 vector + _prop_name 应生成 src={{icon1}}，而非硬编码路径。\n'
        f'Actual TSX (icon lines):\n'
        + '\n'.join(l for l in tsx.splitlines() if 'src=' in l or 'icon' in l.lower())
    )
    # 硬编码的 localAssetPath 不应出现在 src= 属性值中（只应在 data-figma-id 中出现 figmaId）
    assert 'src="/assets/MyPage/I169-35578-2715-4498.svg"' not in tsx, (
        f'U-366 FAIL: 硬编码路径不应出现在 src 属性值中。\n'
        f'Actual TSX:\n{tsx}'
    )


def test_icon_tree_leaf_renders_jsx_expression():
    """U-370: _prop_kind='icon_tree' 的节点在 leaf 组件中应渲染为 {coinIcon}（不是 img）。

    多层图标容器节点被 mark_varying_nodes 标记为 _prop_kind='icon_tree' 后，
    tsx_generator 应在叶子组件的 JSX 中渲染 {coinIcon}（ReactNode 占位符），
    而不是将其当作普通 div 递归展开或当作 image 渲染 <img>。
    """
    # Icon container: marked as icon_tree prop by mark_varying_nodes
    icon_node = {
        'figmaId': 'icon:1', 'figmaName': 'CoinIcon', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '40px', 'height': '40px'},
        'children': [],
        '_prop_name': 'coinIcon',
        '_prop_type': 'React.ReactNode',
        '_prop_kind': 'icon_tree',
        'semantic': {
            'htmlTag': 'div', 'className': 'coin-icon',
            'componentName': None, 'props': [],
        },
    }
    root_ir = {**_base_ir('CoinCard', 'div', 'coin-card'), 'children': [icon_node]}

    tsx = generate_tsx(root_ir, css_ext='less', inject_page_env=False)

    assert 'coinIcon: React.ReactNode' in tsx, (
        f'U-370 FAIL: interface 应有 coinIcon: React.ReactNode\n{tsx}'
    )
    assert '{coinIcon}' in tsx, (
        f'U-370 FAIL: icon_tree 节点应渲染为 {{coinIcon}}（ReactNode 占位符）\n{tsx}'
    )
    assert '<img' not in tsx.replace('data-figma-id', ''), (
        f'U-370 FAIL: leaf 组件不应直接渲染 img（应渲染 {{coinIcon}}）\n{tsx}'
    )


def test_icon_tree_section_renders_fragment_as_prop():
    """U-371: render_jsx_body_with_leaf_refs 对 icon_tree vp 应将 IR 节点渲染为 JSX fragment prop。

    Section 使用叶子组件时，icon_tree 类型的 varyingProp 的值是一个 IR 节点 dict，
    应被渲染为 coinIcon={<div ...><img src="..."/><img src="..."/></div>}，
    而不是硬编码字符串路径。
    """
    root_ir = {
        'figmaId': 'section:1', 'figmaName': 'Section', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex'}, 'children': [
            {'figmaId': 'inst:mnt', 'figmaName': 'CoinCard',
             'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
             'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
             'localAssetPath': None, 'lineTypes': [],
             'css': {}, 'children': [],
             'semantic': {'htmlTag': 'div', 'className': 'coin-card-inst', 'componentName': None, 'props': []}},
        ],
        'semantic': {'htmlTag': 'div', 'className': 'section', 'componentName': 'Section', 'props': []},
    }
    # MNT icon with 2 SVG layers
    mnt_icon_node = {
        'figmaId': 'icon:mnt', 'figmaName': 'MNT', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '40px', 'height': '40px'},
        'bb': {'x': 0, 'y': 0, 'width': 40, 'height': 40},
        'children': [
            {'figmaId': 'layer:1', 'figmaName': 'layer1', 'figmaType': 'VECTOR',
             'isTextNode': False, 'isImageNode': False, 'isVectorNode': True,
             'isDecorativeElement': True, 'textContent': None, 'textSegments': None,
             'localAssetPath': '/assets/mnt-layer1.svg', 'lineTypes': [],
             'css': {'position': 'absolute', 'top': '0px', 'left': '0px', 'width': '40px', 'height': '40px'},
             'bb': {'x': 0, 'y': 0, 'width': 40, 'height': 40}, 'children': []},
            {'figmaId': 'layer:2', 'figmaName': 'layer2', 'figmaType': 'VECTOR',
             'isTextNode': False, 'isImageNode': False, 'isVectorNode': True,
             'isDecorativeElement': True, 'textContent': None, 'textSegments': None,
             'localAssetPath': '/assets/mnt-layer2.svg', 'lineTypes': [],
             'css': {'position': 'absolute', 'top': '5px', 'left': '5px', 'width': '30px', 'height': '30px'},
             'bb': {'x': 5, 'y': 5, 'width': 30, 'height': 30}, 'children': []},
        ],
    }
    leaf_map = {
        'inst:mnt': {
            'componentName': 'CoinCard',
            'varyingProps': [{'propName': 'coinIcon', 'type': 'icon_tree'}],
            'instanceData': {'coinIcon': mnt_icon_node},
            'rootClassName': 'coin-card',
            'isMoly': False,
            'inlineSnippet': None,
        }
    }
    jsx = render_jsx_body_with_leaf_refs(root_ir, leaf_map)

    assert 'coinIcon=' in jsx, (
        f'U-371 FAIL: 应有 coinIcon= prop\n{jsx}'
    )
    assert 'mnt-layer1.svg' in jsx, (
        f'U-371 FAIL: 应包含 MNT layer1 路径\n{jsx}'
    )
    assert 'mnt-layer2.svg' in jsx, (
        f'U-371 FAIL: 应包含 MNT layer2 路径\n{jsx}'
    )
    # 值应是 JSX 表达式（含 {}），不是字符串（不含引号）
    assert 'coinIcon="' not in jsx, (
        f'U-371 FAIL: icon_tree prop 不应为字符串值\n{jsx}'
    )


def test_abs_positioned_tag_badge_skips_cc_renders_as_div():
    """U-372: position:absolute + 非对称 border-radius 的 Tag 实例（角标场景）
    应跳过 CC 映射，回退普通 div 渲染，保证 border-radius 样式生效。
    Real data: node 6777:32826 from PortalRevised (nodeId: 6777-32751)，
    "1_Tag (Default)" 实例，CSS: position:absolute, border-radius:"100px 0px 0px 100px"。
    当前 bug：Moly Tag 内部 border-radius 规则优先级高于 className，自定义形状丢失。
    """
    section_ir = {
        'figmaId': 'section:root', 'figmaName': 'ChooseSection',
        'figmaType': 'FRAME', 'isComponentInstance': False,
        'bb': {'width': 588, 'height': 398},
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
        'semantic': {'htmlTag': 'div', 'className': 'frame-n6777-32813',
                     'componentName': 'ChooseSection', 'props': []},
        'children': [{
            # Real data: node 6777:32826 from PortalRevised (nodeId: 6777-32751)
            # figmaName "1_Tag (Default)", INSTANCE, position:absolute, border-radius asymmetric
            'figmaId': '6777:32826', 'figmaName': '1_Tag (Default)',
            'figmaType': 'INSTANCE', 'isComponentInstance': True,
            'bb': {'width': 151.0, 'height': 32.0},
            'isTextNode': False, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {
                'width': '151px', 'height': '32px',
                'position': 'absolute', 'right': '0px', 'top': '-1px',
                'background-color': 'color-mix(in srgb, var(--bds-green-100-bg) 12%, transparent)',
                'border-radius': '100px 0px 0px 100px',
                'overflow': 'hidden',
            },
            'children': [{
                'figmaId': 'I6777:32826;text', 'figmaName': 'Recommended',
                'isTextNode': True, 'isImageNode': False,
                'isVectorNode': False, 'isDecorativeElement': False,
                'textContent': 'Recommended', 'textSegments': None,
                'localAssetPath': None, 'lineTypes': [],
                'css': {}, 'children': [],
                'semantic': {'htmlTag': 'span', 'className': 'tag-text', 'props': []},
            }],
            'semantic': {'htmlTag': 'div', 'className': 'n1-tag-default', 'props': []},
        }],
    }
    leaf_map = {
        '6777:32826': {
            'componentName': 'Tag',
            'varyingProps': [],
            'instanceData': {},
            'rootClassName': 'n1-tag-default',
            'variantCls': '',
            'isMoly': True,
            'ccComponent': 'Tag',
            'inlineSnippet': '<Tag variant="default" color="green" size="xlarge">\n  Recommended\n</Tag>',
            'iconColor': '',
        },
    }
    jsx = render_jsx_body_with_leaf_refs(section_ir, leaf_map)
    assert '<Tag' not in jsx, (
        f'U-372 FAIL: position:absolute Tag badge 不应渲染为 Moly Tag，'
        f'其内部 border-radius 会覆盖 className 中的自定义值。\n实际输出:\n{jsx}'
    )
    assert "styles['n1-tag-default']" in jsx, (
        f'U-372 FAIL: 应保留 CSS class "n1-tag-default" 渲染为普通 div。\n实际输出:\n{jsx}'
    )
    assert 'Recommended' in jsx, (
        f'U-372 FAIL: 子文本 "Recommended" 应从 IR children 渲染。\n实际输出:\n{jsx}'
    )


def test_decorative_icon_with_iconcolor_renders_as_moly_component():
    """U-373: 装饰性 icon 节点（isDecorativeElement=True, isVectorNode=True, iconColor 已设置）
    应渲染为对应 icon 组件（如 <IconThumbsup color="...">），而非黑色 SVG <img>。
    需要配置 ICON_PACKAGE 且图标名在合法集合中，才会渲染为组件。
    """
    import lib.tsx_generator as _tsx_mod
    _orig_valid = _tsx_mod._VALID_ICON_COMPS
    _orig_pkg = _tsx_mod._ICON_PACKAGE
    _tsx_mod._VALID_ICON_COMPS = {'IconThumbsup'}
    _tsx_mod._ICON_PACKAGE = 'test-icon-lib'
    try:
        section_ir = {
            'figmaId': 'badge:root', 'figmaName': 'BadgeSection',
            'figmaType': 'FRAME', 'isComponentInstance': False,
            'bb': {'width': 200, 'height': 100},
            'isTextNode': False, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'display': 'flex', 'position': 'relative'},
            'semantic': {'htmlTag': 'div', 'className': 'badge-section', 'componentName': 'BadgeSection', 'props': []},
            'children': [{
                'figmaId': 'I6777:32826;13351:402772', 'figmaName': 'icon_thumbsup',
                'figmaType': 'INSTANCE', 'isComponentInstance': True,
                'bb': {'width': 18.0, 'height': 18.0},
                'isTextNode': False, 'isImageNode': False,
                'isVectorNode': True, 'isDecorativeElement': True,
                'textContent': None, 'textSegments': None,
                'localAssetPath': '/assets/portalrevised/I6777-32826-13351-402772.svg',
                'lineTypes': [],
                'css': {'width': '18px', 'height': '18px', 'flex-shrink': '0', 'overflow': 'hidden'},
                'children': [],
                'iconColor': '#06c167',
                'semantic': {'htmlTag': 'img', 'className': 'icon-thumbsup', 'props': []},
            }],
        }
        jsx = render_jsx_body_with_leaf_refs(section_ir, {})
        assert '<IconThumbsup' in jsx, (
            f'U-373 FAIL: iconColor=#06c167 的 icon_thumbsup 应渲染为 <IconThumbsup>，'
            f'而非黑色 <img>。\n实际输出:\n{jsx}'
        )
        assert 'color="#06c167"' in jsx, (
            f'U-373 FAIL: 应注入 color="#06c167" 到 IconThumbsup 组件。\n实际输出:\n{jsx}'
        )
        assert '<img' not in jsx or '/I6777-32826' not in jsx, (
            f'U-373 FAIL: 不应渲染为 <img> SVG 黑色图标。\n实际输出:\n{jsx}'
        )
    finally:
        _tsx_mod._VALID_ICON_COMPS = _orig_valid
        _tsx_mod._ICON_PACKAGE = _orig_pkg


def test_icon_not_in_package_falls_back_to_img():
    """U-374: iconColor 节点的 icon 名不在图标包中时，
    应回退到 SVG <img> 渲染，不生成不存在的组件引用（避免 ReferenceError）。
    当前修复：_get_valid_icon_comps 校验后跳过，回退到 <img>。
    """
    import lib.tsx_generator as _tsx_mod
    # 临时将合法 icon 集设为仅含 IconThumbsup，排除 IconPlay
    _orig = _tsx_mod._VALID_ICON_COMPS
    _tsx_mod._VALID_ICON_COMPS = {'IconThumbsup'}  # IconPlay 不在集合中
    try:
        section_ir = {
            'figmaId': 'sec:root', 'figmaName': 'Section',
            'figmaType': 'FRAME', 'isComponentInstance': False,
            'bb': {'width': 200, 'height': 100},
            'isTextNode': False, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'display': 'flex', 'position': 'relative'},
            'semantic': {'htmlTag': 'div', 'className': 'section', 'componentName': 'Section', 'props': []},
            'children': [{
                # Real data: node 6777:32765 from PortalRevised — icon_play, iconColor=#121214
                # IconPlay is not in the valid icon set (only IconThumbsup is)
                'figmaId': '6777:32765', 'figmaName': 'icon_play',
                'figmaType': 'INSTANCE', 'isComponentInstance': True,
                'bb': {'width': 24, 'height': 24},
                'isTextNode': False, 'isImageNode': False,
                'isVectorNode': True, 'isDecorativeElement': True,
                'textContent': None, 'textSegments': None,
                'localAssetPath': '/assets/portalrevised/6777-32765.svg',
                'lineTypes': [],
                'css': {'width': '24px', 'height': '24px'},
                'children': [],
                'iconColor': '#121214',
                'semantic': {'htmlTag': 'img', 'className': 'icon-play', 'props': []},
            }],
        }
        jsx = render_jsx_body_with_leaf_refs(section_ir, {})
        assert '<IconPlay' not in jsx, (
            f'U-374 FAIL: IconPlay 不在合法 icon 集中，不应生成 <IconPlay>。\n实际输出:\n{jsx}'
        )
        assert '<img' in jsx, (
            f'U-374 FAIL: 应回退到 <img> SVG 渲染。\n实际输出:\n{jsx}'
        )
    finally:
        _tsx_mod._VALID_ICON_COMPS = _orig


def test_generate_tsx_adds_icon_import():
    """U-375: generate_tsx 应自动检测 JSX body 中由 U-373 生成的 <Icon*> 组件，
    并追加对应的 ICON_PACKAGE import，避免 ReferenceError。
    需要配置 ICON_PACKAGE 且图标名在合法集合中，才会追加 import。
    """
    import lib.tsx_generator as _tsx_mod
    # 临时将合法 icon 集设为包含 IconGlobe，并设置图标包名
    _orig_valid = _tsx_mod._VALID_ICON_COMPS
    _orig_pkg = _tsx_mod._ICON_PACKAGE
    _tsx_mod._VALID_ICON_COMPS = {'IconGlobe', 'IconThumbsup'}
    _tsx_mod._ICON_PACKAGE = 'test-icon-lib'
    try:
        ir = {
            'figmaId': 'page:root', 'figmaName': 'PageRoot',
            'figmaType': 'FRAME', 'isComponentInstance': False,
            'bb': {'width': 1440, 'height': 800},
            'isTextNode': False, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [{
                # Decorative icon node with iconColor → U-373 renders as <IconGlobe>
                'figmaId': 'icon:globe', 'figmaName': 'icon_globe',
                'figmaType': 'INSTANCE', 'isComponentInstance': True,
                'bb': {'width': 24, 'height': 24},
                'isTextNode': False, 'isImageNode': False,
                'isVectorNode': True, 'isDecorativeElement': True,
                'textContent': None, 'textSegments': None,
                'localAssetPath': '/assets/globe.svg',
                'lineTypes': [],
                'css': {'width': '24px', 'height': '24px'},
                'children': [],
                'iconColor': '#ffffff',
                'semantic': {'htmlTag': 'img', 'className': 'icon-globe', 'props': []},
            }],
            'semantic': {'htmlTag': 'div', 'className': 'page-root', 'componentName': 'PageRoot', 'props': []},
        }
        tsx = generate_tsx(ir)
        assert 'IconGlobe' in tsx, (
            f'U-375 FAIL: TSX 应包含 IconGlobe（U-373 渲染结果）。\n实际输出:\n{tsx[:500]}'
        )
        assert "from 'test-icon-lib'" in tsx, (
            f'U-375 FAIL: 应自动追加 ICON_PACKAGE import。\n实际输出:\n{tsx[:500]}'
        )
    finally:
        _tsx_mod._VALID_ICON_COMPS = _orig_valid
        _tsx_mod._ICON_PACKAGE = _orig_pkg


def test_h5only_subsection_gets_responsive_wrapper():
    """U-396: h5Only subSection 应被包裹在 h5-<kebab-name> div 中，
    并且父 section CSS 应有 @media (min-width: 769px) { .h5-<name> { display: none } } 规则。
    Real data: node 250:4576 (Rewards tiers, h5Only=True) in TieredRewardsSection
    from EU deposit campaign (fileKey=VxzOGHRHeBHKhJtDLz1PP5, nodeId=250-2618).
    Problem: merge_responsive correctly marks 250:4576 as h5Only, but _render_node
    emits plain <RewardsTiersSection /> without the hide-on-desktop wrapper.
    """
    section_ir = {
        'figmaId': '250:3404', 'figmaName': 'Tiered Rewards',
        'figmaType': 'FRAME', 'isComponentInstance': False,
        'bb': {'width': 1440, 'height': 780},
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'semantic': {'htmlTag': 'div', 'className': 'tiered-rewards',
                     'componentName': 'TieredRewardsSection', 'props': []},
        'children': [
            {
                # Real data: 250:4576 Rewards tiers (H5 accordion), h5Only=True
                'figmaId': '250:4576', 'figmaName': 'Rewards tiers',
                'figmaType': 'FRAME', 'isComponentInstance': False,
                'bb': {'width': 393, 'height': 60},
                'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                'localAssetPath': None, 'lineTypes': [],
                'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
                'semantic': {'htmlTag': 'div', 'className': 'rewards-tiers',
                             'componentName': 'RewardsTiersSection', 'props': []},
                'children': [],
                'h5Only': True,
            }
        ],
    }
    subsection_map = {'250:4576': 'RewardsTiersSection'}
    jsx = render_jsx_body_with_leaf_refs(section_ir, {}, subsection_map=subsection_map)
    assert "h5-rewards-tiers-section" in jsx, (
        f'U-396 FAIL: h5Only subSection 应有 h5-rewards-tiers-section wrapper class，'
        f'用于生成 @media (min-width: 769px) {{ display: none }} 规则。\n实际输出:\n{jsx}'
    )
    assert '<RewardsTiersSection' in jsx, (
        f'U-396 FAIL: 包装后应仍渲染 <RewardsTiersSection>。\n实际输出:\n{jsx}'
    )


def test_pconly_subsection_gets_responsive_wrapper():
    """U-397: pcOnly subSection 应被包裹在 pc-<kebab-name> div 中，
    并且父 section CSS 应有 @media (max-width: 768px) { .pc-<name> { display: none } } 规则。
    Real data: node 250:3405 (Tiered Table / 1, should be pcOnly=True) in TieredRewardsSection
    from EU deposit campaign (fileKey=VxzOGHRHeBHKhJtDLz1PP5, nodeId=250-2618).
    Problem: merge_responsive pairs 250:3405 with H5 inner content (score 0.948 due to
    shared text data), preventing pcOnly from being set. PC table renders on mobile
    even though the h5Only accordion (250:4576) provides the mobile view.
    """
    section_ir = {
        'figmaId': '250:3404', 'figmaName': 'Tiered Rewards',
        'figmaType': 'FRAME', 'isComponentInstance': False,
        'bb': {'width': 1440, 'height': 780},
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'semantic': {'htmlTag': 'div', 'className': 'tiered-rewards',
                     'componentName': 'TieredRewardsSection', 'props': []},
        'children': [
            {
                # Real data: 250:3405 Tiered Table / 1 (PC table), pcOnly=True after fix
                'figmaId': '250:3405', 'figmaName': 'Tiered Table / 1',
                'figmaType': 'FRAME', 'isComponentInstance': False,
                'bb': {'width': 1200, 'height': 686},
                'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                'localAssetPath': None, 'lineTypes': [],
                'css': {'display': 'flex', 'flex-direction': 'column', 'max-width': '1200px'},
                'semantic': {'htmlTag': 'div', 'className': 'tiered-table-1',
                             'componentName': 'TieredTable1Section', 'props': []},
                'children': [],
                'pcOnly': True,
            }
        ],
    }
    subsection_map = {'250:3405': 'TieredTable1Section'}
    jsx = render_jsx_body_with_leaf_refs(section_ir, {}, subsection_map=subsection_map)
    assert "pc-tiered-table1-section" in jsx, (
        f'U-397 FAIL: pcOnly subSection 应有 pc-tiered-table1-section wrapper class，'
        f'用于生成 @media (max-width: 768px) {{ display: none }} 规则。\n实际输出:\n{jsx}'
    )
    assert '<TieredTable1Section' in jsx, (
        f'U-397 FAIL: 包装后应仍渲染 <TieredTable1Section>。\n实际输出:\n{jsx}'
    )


def test_inline_moly_wrapper_carries_data_figma_id():
    """U-413: When an inline Moly component (e.g. Countdown) has a wrapper div,
    the wrapper div must carry data-figma-id so that CSS attribute selectors
    [data-figma-id="..."] generated for pcOnly/h5Only nodes can match and hide/show
    the element on mobile.

    Real case: HeroSection 250:2629 (pcOnly Countdown). CSS rule:
    @media (max-width: 768px) { [data-figma-id="250:2629"] { display: none !important; } }
    Without data-figma-id on the wrapper, the rule matches nothing (Moly <Countdown>
    doesn't forward data-figma-id to the DOM root), so the PC countdown remains visible
    on mobile alongside the h5Only countdown → duplicate countdown bug.
    """
    section_ir = {
        'figmaId': '250:2620', 'figmaName': 'HeroSection',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column'},
        'semantic': {'htmlTag': 'section', 'className': 'hero-section', 'componentName': 'HeroSection', 'props': []},
        'children': [{
            'figmaId': '250:2629', 'figmaName': 'Countdown',
            'figmaType': 'INSTANCE', 'isComponentInstance': True,
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'display': 'flex'},
            'semantic': {'htmlTag': 'div', 'className': 'n1-countdown-simple', 'componentName': 'Countdown', 'props': []},
            'children': [],
        }],
    }
    leaf_map = {
        '250:2629': {
            'componentName': 'N1CountdownSimple',
            'varyingProps': [],
            'instanceData': {},
            'rootClassName': 'n1-countdown-simple',
            'isMoly': True,
            'ccComponent': 'Countdown',
            'inlineSnippet': '<Countdown mode="easy" size="middle" display={{ day: true, hour: true, minute: true, second: true }} targetTimestamp={Date.now() + 3600000} />',
            'iconColor': '',
        },
    }
    jsx = render_jsx_body_with_leaf_refs(section_ir, leaf_map)
    # The wrapper div must carry data-figma-id for CSS attr selectors to work
    assert 'data-figma-id="250:2629"' in jsx, (
        'U-413 FAIL: inline Moly wrapper div must have data-figma-id="250:2629" '
        'so CSS [data-figma-id="250:2629"] can match and hide/show on mobile.\n'
        f'Actual JSX:\n{jsx}'
    )
    # data-figma-id should be on the WRAPPER div (not just inside the Moly component)
    # The wrapper div line should contain both className and data-figma-id
    wrapper_line = next((l for l in jsx.splitlines() if 'n1-countdown-simple' in l and '<div' in l), None)
    assert wrapper_line is not None, (
        'U-413 FAIL: wrapper div with n1-countdown-simple class not found.\n'
        f'Actual JSX:\n{jsx}'
    )
    assert 'data-figma-id="250:2629"' in wrapper_line, (
        'U-413 FAIL: data-figma-id must be on the wrapper div, not just the Moly component.\n'
        f'Wrapper line: {wrapper_line}\n'
        f'Full JSX:\n{jsx}'
    )


def test_u414_leaf_component_in_wrapper_receives_data_figma_id_prop():
    """U-414: When a leaf component is placed inside a wrapper div (needs_wrapper=True),
    the leaf component call must ALSO receive data-figma-id as a prop so its root
    element has the correct figmaId for CSS visibility rules.

    Real case: EarnFromSection Trade leaf (allInstanceFigmaIds: ['250:2637', '250:3851', '250:2644']).
    When Trade is rendered inside the h5Only wrapper div (data-figma-id="250:3851"):
      <div className={styles['trade-n250-3851']} data-figma-id="250:3851">
        <Trade ... />   ← Trade uses default figmaId "250:2637" (pcOnly, hidden on mobile!)
      </div>
    Without passing data-figma-id to Trade, Trade's root div defaults to "250:2637" which
    has [data-figma-id="250:2637"] { display: none !important } on mobile → icon disappears.

    Fix: also inject data-figma-id into the component call so Trade renders:
      <div data-figma-id="250:3851"> ← shows on mobile via h5Only CSS rule
    instead of the default pcOnly "250:2637".
    """
    section_ir = {
        'figmaId': '250:2633', 'figmaName': 'EarnFromSection',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column'},
        'semantic': {'htmlTag': 'section', 'className': 'earn-from', 'componentName': 'EarnFromSection', 'props': []},
        'children': [{
            'figmaId': '250:3851', 'figmaName': 'TradeWrapper',
            'figmaType': 'FRAME', 'isComponentInstance': False,
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'display': 'flex'},
            'semantic': {
                'htmlTag': 'div',
                'className': 'trade-n250-3851',   # DIFFERENT from Trade rootClassName
                'componentName': 'Trade',
                'props': []
            },
            'children': [],
        }],
    }
    leaf_map = {
        '250:3851': {
            'componentName': 'Trade',
            'varyingProps': [],
            'instanceData': {},
            'rootClassName': 'trade',          # Trade component's OWN root class (different)
            'isMoly': False,
            'ccComponent': '',
            'inlineSnippet': None,
            'iconColor': '',
        },
    }
    jsx = render_jsx_body_with_leaf_refs(section_ir, leaf_map)

    # The wrapper div must have data-figma-id
    assert 'data-figma-id="250:3851"' in jsx, (
        'U-414 FAIL: wrapper div must have data-figma-id="250:3851".\n'
        f'Actual JSX:\n{jsx}'
    )

    # The Trade component CALL must ALSO receive data-figma-id prop
    # so Trade's root div (via dataFigmaId ?? "250:2637") uses "250:3851" not "250:2637"
    lines = jsx.splitlines()
    trade_call_line = next((l for l in lines if '<Trade' in l), None)
    assert trade_call_line is not None, (
        'U-414 FAIL: <Trade ... /> call not found in JSX.\n'
        f'Actual JSX:\n{jsx}'
    )
    assert 'data-figma-id="250:3851"' in trade_call_line, (
        'U-414 FAIL: <Trade> call must receive data-figma-id="250:3851" prop '
        'so its root div has the h5Only figmaId, not the pcOnly default.\n'
        f'Trade call line: {trade_call_line}\n'
        f'Full JSX:\n{jsx}'
    )


def test_detect_page_theme_uses_heading_text_color_when_no_bg():
    """U-418: detect_page_theme 在根节点无背景色时，用首屏大标题的 _rawColorHex 投票。

    浅色文字（亮度 > 0.5）→ dark 主题；深色文字（亮度 ≤ 0.5）→ light 主题。
    Real case: EU deposit LP (250-2618) 根节点透明，section 背景为 hardcoded hex，
    原逻辑 BDS token 投票为 0:0 → 误判 light。
    大标题（64px, #ffffff）正确反映深色页面主题。
    """
    from lib.tsx_generator import detect_page_theme

    def _heading(raw_color_hex):
        return {
            'figmaId': 'h:1', 'figmaName': 'title',
            'figmaType': 'TEXT', 'isTextNode': True,
            'children': [],
            'css': {'font-size': '64px', 'color': 'var(--bds-gray-t1-title)',
                    '_rawColorHex': raw_color_hex},
        }

    def _section(text_node):
        return {
            'figmaId': 's:1', 'figmaName': 'HeroSection',
            'figmaType': 'FRAME', 'isTextNode': False,
            'children': [text_node],
            'css': {'background-color': '#1a1a1a'},
        }

    # Case A: 白色大标题 → dark 主题
    ir_dark = {
        'figmaId': 'root', 'figmaType': 'FRAME',
        'css': {},  # 无背景色
        'children': [_section(_heading('#ffffff'))],
    }
    assert detect_page_theme(ir_dark) == 'dark', (
        'U-418a FAIL: 白色大标题（#ffffff）→ 应判断为 dark 主题'
    )

    # Case B: 深色大标题 → light 主题
    ir_light = {
        'figmaId': 'root', 'figmaType': 'FRAME',
        'css': {},  # 无背景色
        'children': [_section(_heading('#1a1a1a'))],
    }
    assert detect_page_theme(ir_light) == 'light', (
        'U-418b FAIL: 深色大标题（#1a1a1a）→ 应判断为 light 主题'
    )

    # Case C: 小字（16px）低于 20px 阈值不参与投票，无大标题时 fallback light
    small_text = {
        'figmaId': 'h:2', 'figmaName': 'small',
        'figmaType': 'TEXT', 'isTextNode': True,
        'children': [],
        'css': {'font-size': '16px', 'color': '#ffffff', '_rawColorHex': '#ffffff'},
    }
    ir_small_only = {
        'figmaId': 'root', 'figmaType': 'FRAME',
        'css': {},
        'children': [{'figmaId': 's:2', 'figmaType': 'FRAME', 'isTextNode': False,
                      'children': [small_text], 'css': {}}],
    }
    assert detect_page_theme(ir_small_only) == 'light', (
        'U-418c FAIL: 只有小字节点（16px）不参与投票 → fallback light'
    )


def test_u419_detect_page_theme_nav_then_hero_returns_dark():
    """U-419: detect_page_theme 在第一个 section 无大文字（如 Navigation）时
    应继续扫下一个 section，而非直接 fallback light。

    Real case: EU deposit LP merged IR —
      section[0] = Navigation (navbar, no ≥20px text)
      section[1] = Hero (has white 40px title → dark theme)

    NOTE: 实际的 EU deposit LP 整页是 light 主题（大多数 section 为白底），
    仅 Hero section 的背景为深色。整页强制 dark 主题会破坏其他 section 的布局。
    正确做法：保持页面 light 主题，Hero 文字颜色通过 U-420 var() fallback 修复。
    本测试验证有 white 大标题且 BDS token 投票 dark > light 时能正确判断为 dark。
    """
    from lib.tsx_generator import detect_page_theme

    nav_section = {
        'figmaId': 'nav:1', 'figmaName': 'Navigation',
        'figmaType': 'FRAME', 'isTextNode': False,
        'children': [],          # no text nodes
        'css': {},
    }

    hero_title = {
        'figmaId': 'h:1', 'figmaName': 'Move Your Funds',
        'figmaType': 'TEXT', 'isTextNode': True,
        'children': [],
        'css': {'font-size': '40px', 'color': 'var(--bds-gray-t1-title)',
                '_rawColorHex': '#ffffff'},
    }
    hero_section = {
        'figmaId': 'hero:1', 'figmaName': 'HeroSection',
        'figmaType': 'FRAME', 'isTextNode': False,
        'children': [hero_title],
        'css': {},
    }

    ir = {
        'figmaId': 'root', 'figmaType': 'FRAME',
        'css': {},
        'children': [nav_section, hero_section],
    }

    result = detect_page_theme(ir)
    assert result == 'dark', (
        f'U-419 FAIL: Navigation(empty) + Hero(white 40px text) → '
        f'expected dark, got {result!r}. '
        f'Fix: scan sections until large text found (not just [:1])'
    )


def test_u432_image_fill_node_renders_as_container_with_object_cover():
    """U-432: isImageNode RECTANGLE fills must render as <div container> + <img object-cover>
    instead of a direct <img> with the Figma bounding-box offset as inline style.

    Real data: EU deposit campaign EarnPointsOnCardPaySection, nodes 250:3299 and 250:3300.
    Figma design (from MCP): each image fill node is rendered as
      <div style={{position:absolute, top:'-16px', left:'-37px', width:'314px', height:'272px'}}>
        <img style={{position:'absolute', inset:'0', width:'100%', height:'100%', objectFit:'cover'}} />
      </div>

    Bug: _render_icon_subtree returned a direct <img> with all CSS as inline style.
    This causes the image to render at 1:1 pixel size within the clip container, showing
    a different crop than Figma's object-cover behaviour. For node 250:3300 (1075×432px),
    the visible 240×240 area was the right-edge strip (x=771–1011) instead of the center
    crop, completely obscuring the intended QR-code-and-phone scene with a blurry background.

    Fix: _render_icon_subtree wraps isImageNode=True nodes in a container div (with the
    positioning CSS from Figma's bounding box) and renders the image inside using
    object-cover to correctly fill the container.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from lib.tsx_generator import _render_icon_subtree

    image_node = {
        'figmaId': '250:3299', 'figmaName': 'image 5055', 'figmaType': 'RECTANGLE',
        'isImageNode': True, 'isVectorNode': False, 'isTextNode': False,
        'isComponentInstance': False,
        'css': {'position': 'absolute', 'left': '-37px', 'top': '-16px',
                'width': '314px', 'height': '272px'},
        'children': [], 'visible': True,
        'localAssetPath': '/assets/test/250-3299.png',
    }
    result = _render_icon_subtree(image_node, 0)

    assert '<div' in result, (
        f'U-432 FAIL: isImageNode fill must render as container <div>, not bare <img>. '
        f'Got: {result!r}'
    )
    assert "objectFit: 'cover'" in result, (
        f"U-432 FAIL: inner <img> must have objectFit:'cover' for correct fill behavior. "
        f'Got: {result!r}'
    )
    assert "inset: '0'" in result or "inset:" in result, (
        f"U-432 FAIL: inner <img> must have inset:'0' to fill the container. "
        f'Got: {result!r}'
    )
    assert "width: '100%'" in result, (
        f"U-432 FAIL: inner <img> must have width:'100%'. Got: {result!r}"
    )
    assert "height: '100%'" in result, (
        f"U-432 FAIL: inner <img> must have height:'100%'. Got: {result!r}"
    )
    assert "data-figma-id=\"250:3299\"" in result, (
        f'U-432 FAIL: data-figma-id must be on the container div. Got: {result!r}'
    )
    assert "top: '-16px'" in result, (
        f"U-432 FAIL: container div must have Figma offset top:'-16px'. Got: {result!r}"
    )
    assert "left: '-37px'" in result, (
        f"U-432 FAIL: container div must have Figma offset left:'-37px'. Got: {result!r}"
    )


def test_u433_collect_prop_markers_deduplicates_same_prop_name():
    """U-433: _collect_prop_markers must rename duplicate _prop_name nodes in-place.

    Real scenario: Brand6PreKyc/EarnUseCase — two sibling text nodes figmaId 4030:41609
    and 4030:41610 both have figmaName="Earnings", both get _prop_name='earnings'.
    Without dedup the generated interface contains:
        earnings: string;
        earnings: string;   ← Babel: Argument name clash
    Fix: second occurrence renamed to 'earnings2', IR node updated in-place so
    _render_node emits {earnings2}.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from lib.tsx_generator import _collect_prop_markers

    ir = {
        'figmaId': '4030:41601', 'figmaName': 'Earn use case',
        'isTextNode': False,
        'children': [
            {
                'figmaId': '4030:41609', 'figmaName': 'Earnings',
                'isTextNode': True, '_prop_name': 'earnings', '_prop_type': 'string',
                'children': [],
            },
            {
                'figmaId': '4030:41610', 'figmaName': 'Earnings',
                'isTextNode': True, '_prop_name': 'earnings', '_prop_type': 'string',
                'children': [],
            },
        ],
    }

    result = _collect_prop_markers(ir)
    names = [p['name'] for p in result]

    assert len(names) == 2, f'U-433 FAIL: expected 2 props, got {names}'
    assert names[0] == 'earnings', (
        f'U-433 FAIL: first prop must stay "earnings", got {names[0]!r}')
    assert names[1] == 'earnings2', (
        f'U-433 FAIL: second prop must be renamed "earnings2", got {names[1]!r}')
    assert ir['children'][1]['_prop_name'] == 'earnings2', (
        f'U-433 FAIL: IR node must be updated in-place for _render_node. '
        f'Got: {ir["children"][1]["_prop_name"]!r}')


def test_u434_inline_snippet_arrow_function_onChange_data_figma_id_not_garbled():
    """U-434: data-figma-id injection must not match '>' inside arrow function props.

    Real data: node 227:13142 from ByAiHub20 (nodeId: 169-33787).
    Pagination with onChange={() => {}} — the '>' in '=>' was matched by the
    naive r'(/?>)' regex, inserting data-figma-id inside the arrow function body:
        onChange={() = data-figma-id="227:13142"> {}}
    which is invalid JSX and crashes the page.
    """
    section_ir = {
        'figmaId': '227:13000', 'figmaName': 'SkillsSection',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column'},
        'semantic': {'htmlTag': 'section', 'className': 'skills-section',
                     'componentName': 'SkillsSection', 'props': []},
        'children': [{
            'figmaId': '227:13142', 'figmaName': 'Pagination',
            'figmaType': 'INSTANCE', 'isComponentInstance': True,
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'display': 'flex'},
            'semantic': {'htmlTag': 'div', 'className': 'paginationdefault',
                         'componentName': 'Pagination', 'props': []},
            'children': [],
        }],
    }
    leaf_map = {
        '227:13142': {
            'componentName': 'PaginationDefault',
            'varyingProps': [],
            'instanceData': {},
            'rootClassName': 'paginationdefault',
            'isMoly': True,
            'ccComponent': 'Pagination',
            # snippet already has onChange injected by code_connect._normalize_snippet fix
            'inlineSnippet': '<Pagination total={50} current={1} pageSize={10} onChange={() => {}} />',
            'iconColor': '',
        },
    }
    jsx = render_jsx_body_with_leaf_refs(section_ir, leaf_map)
    assert 'onChange={() =>' in jsx or 'onChange={() => {}}' in jsx, (
        f'U-434 FAIL: arrow function onChange must not be split by data-figma-id injection.\n'
        f'Actual JSX:\n{jsx}'
    )
    assert f'data-figma-id="227:13142"' in jsx, (
        f'U-434 FAIL: data-figma-id must still be present.\n'
        f'Actual JSX:\n{jsx}'
    )
    # The garbled form must not appear
    assert 'onChange={() =' not in jsx.replace('onChange={() => {}}', ''), (
        f'U-434 FAIL: garbled onChange detected (> was split by injection).\n'
        f'Actual JSX:\n{jsx}'
    )


if __name__ == '__main__':
    tests = [test_uses_export_default, test_prop_name_replaces_text_content,
             test_props_interface_generated, test_no_props_interface_without_markers,
             test_page_text_token_uses_type_import,
             test_leaf_call_renders_segments_as_jsx,
             test_reactnode_prop_type_for_text_segments,
             test_segments_trailing_text_preserved,
             test_single_segment_prop_renders_multicolor_jsx,
             test_leaf_call_includes_data_figma_id_on_no_wrapper_path,
             test_decorative_vector_with_prop_name_uses_dynamic_src,
             test_icon_tree_leaf_renders_jsx_expression,
             test_icon_tree_section_renders_fragment_as_prop,
             test_abs_positioned_tag_badge_skips_cc_renders_as_div,
             test_decorative_icon_with_iconcolor_renders_as_moly_component,
             test_icon_not_in_package_falls_back_to_img,
             test_generate_tsx_adds_icon_import,
             test_h5only_subsection_gets_responsive_wrapper,
             test_pconly_subsection_gets_responsive_wrapper,
             test_inline_moly_wrapper_carries_data_figma_id,
             test_u414_leaf_component_in_wrapper_receives_data_figma_id_prop,
             test_detect_page_theme_uses_heading_text_color_when_no_bg,
             test_u419_detect_page_theme_nav_then_hero_returns_dark,
             test_u432_image_fill_node_renders_as_container_with_object_cover,
             test_u433_collect_prop_markers_deduplicates_same_prop_name,
             test_u434_inline_snippet_arrow_function_onChange_data_figma_id_not_garbled]
    passed = 0
    for t in tests:
        try:
            t(); print(f'  ✓ {t.__name__}'); passed += 1
        except Exception as e:
            print(f'  ✗ {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')
