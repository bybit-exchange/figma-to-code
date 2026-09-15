#!/usr/bin/env python3
"""
test_convert_patches.py — convert_patches 单元测试（从 verify_fixes.py 拆分）
"""
import sys
import re
import math
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tests.helpers import check, make_node, make_ctx, reset, print_summary, _SOLID_BLACK, _SOLID_DARK
from lib.css_extractor import extract_css, clear_var_miss, get_var_miss
from lib.ir_builder import build_ir
from lib.color import px
from convert import patch_flex_shrink, resolve_asset_paths
from lib.paths import page_code_subdir


def run_tests():
    # ── U-266: Patch 25 不应给 TEXT 节点添加固定 width/height ────────────────
    # convert.py 的 patch_flex_shrink (Patch 25) 对 flex-row 中 flex-shrink:0 且
    # 无显式 width 的子节点添加 bb 尺寸。但 TEXT 节点的尺寸由文本内容决定（HUG），
    # 不应被 Patch 25 覆盖，否则导致文本裁剪/重叠。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        # 通过产物验证：重建后 TEXT 节点不含固定 width/height
        import importlib
        _convert_mod = importlib.import_module('convert')
        _pfx = getattr(_convert_mod, 'patch_flex_shrink')
        # 模拟：flex-row 父节点有一个 TEXT 子节点
        _parent_ir = {
            'figmaId': 'parent:1', 'figmaName': 'Block', 'figmaType': 'FRAME',
            'bb': {'width': 62, 'height': 32},
            'css': {'display': 'flex', 'flex-direction': 'row', 'align-items': 'center',
                    'gap': '6px', 'flex-shrink': '0', 'position': 'relative'},
            'children': [{
                'figmaId': 'text:1', 'figmaName': 'Time 1', 'figmaType': 'TEXT',
                'isTextNode': True,
                'bb': {'width': 13, 'height': 26},
                'css': {'flex-shrink': '0', 'color': '#adb1b8', 'font-size': '18px',
                        'font-weight': '500', 'white-space': 'nowrap'},
                'children': [],
            }],
        }
        _pfx(_parent_ir)
        _text_css = _parent_ir['children'][0]['css']
        check('U-266', 'Patch 25 不给 TEXT 节点加 width（当前: ' + str(_text_css.get('width', 'None')) + '）',
              'width' not in _text_css)
        check('U-267', 'Patch 25 不给 TEXT 节点加 height（当前: ' + str(_text_css.get('height', 'None')) + '）',
              'height' not in _text_css)
    except Exception as e:
        check('U-266', f'EXCEPTION: {e}', False)



    # ── U-271: Patch 8 不应在 top 未输出（top=0 被省略）时误恢复装饰物 ────────
    # 当 isDecorativeElement=True + isImageNode=True 的节点 top=0 被 css_extractor
    # 省略（即 'top' not in css），Patch 8 不应误判为"无定位"而恢复到 flow。
    # 只有当节点既无 top 也无 left 且确实没有定位信息时才应恢复。
    _p8_parent = {
        'figmaId': 'p8:1', 'figmaName': 'Hero', 'figmaType': 'FRAME',
        'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative',
                'width': '1440px', 'height': '600px'},
        'children': [{
            'figmaId': 'p8-bg:1', 'figmaName': 'bg-image', 'figmaType': 'RECTANGLE',
            'isDecorativeElement': True, 'isImageNode': True,
            'bb': {'width': 1440, 'height': 600},
            'css': {'position': 'absolute', 'width': '1440px', 'height': '600px',
                    'pointer-events': 'none'},
            'children': [],
        }],
    }
    _pfx(_p8_parent)
    _p8_child = _p8_parent['children'][0]
    check('U-271', 'Patch 8: 无 top/left 的 absolute 装饰图不应被误恢复（isDecorativeElement 保留）',
          _p8_child.get('isDecorativeElement') is True)

    # ── U-273: 删除 Patch 15 后，flex-column 容器的 height 不再被 patch_flex_shrink 改动 ──
    # height→min-height 统一由 ir_builder._post_process_frame() 处理（包含正确的 padding 减法）。
    # patch_flex_shrink 不应再对 height 做任何变更。
    _p15_parent = {
        'figmaId': 'p15:1', 'figmaName': 'Section', 'figmaType': 'FRAME',
        'css': {'display': 'flex', 'flex-direction': 'column', 'height': '400px',
                'padding': '100px 20px 50px 20px', 'gap': '10px'},
        'children': [
            {'figmaId': 'p15-c1:1', 'figmaName': 'A', 'figmaType': 'FRAME',
             'isTextNode': False, 'css': {'height': '200px'}, 'children': []},
            {'figmaId': 'p15-c2:1', 'figmaName': 'B', 'figmaType': 'FRAME',
             'isTextNode': False, 'css': {'height': '200px'}, 'children': []},
        ],
    }
    _pfx(_p15_parent)
    check('U-273', 'patch_flex_shrink 不再修改 flex-column 容器的 height（由 ir_builder 统一处理）',
          _p15_parent['css'].get('height') == '400px')

    # ── U-272: Patch 18a 不应在 figmaCounterAxisAlign 缺失时覆盖 align-items ────
    # 当 Figma 未显式设置 counterAxisAlignItems（值为 None）时，css_extractor 根据
    # 几何推断的 align-items:center 不应被 Patch 18a 覆盖为 flex-start。
    _p18a_parent = {
        'figmaId': 'p18a:1', 'figmaName': 'CardRow', 'figmaType': 'FRAME',
        'figmaCounterAxisAlign': None,  # 设计师未显式设置
        'css': {'display': 'flex', 'flex-direction': 'row', 'align-items': 'center',
                'gap': '12px'},
        'children': [
            {
                'figmaId': 'p18a-icon:1', 'figmaName': 'Icon', 'figmaType': 'FRAME',
                'css': {'flex-shrink': '0', 'width': '24px', 'height': '24px'},
                'children': [],
            },
            {
                'figmaId': 'p18a-text:1', 'figmaName': 'Content', 'figmaType': 'FRAME',
                'css': {'flex-grow': '1', 'display': 'flex', 'flex-direction': 'column'},
                'children': [],
            },
        ],
    }
    _pfx(_p18a_parent)
    check('U-272', 'Patch 18a: figmaCounterAxisAlign=None 时不覆盖 align-items',
          _p18a_parent['css'].get('align-items') == 'center')


    # ── U-274: _asset_comp_name 应将 nodeId_safe 追加到组件名后 ─────────────────
    # Real data: node 181:2980 "Web" (trump-pc) 和 250:2618 "Web" (eu-pc) 来自不同
    # Figma 文件，但根帧同名 "Web"。_asset_comp_name 需将 node_id_safe 追加，
    # 防止并行转换时资源目录冲突。
    check('U-274', '_asset_comp_name 含 node_id_safe: Web+181-2980 → Web-181-2980',
          page_code_subdir('Web', '181-2980').name == 'Web-181-2980')

    # ── U-275: 同名 Frame 不同 nodeId 的 comp_name 必须互不相同 ─────────────────
    # Real data: trump-pc node 181:2980 和 eu-pc node 250:2618 均名为 "Web"
    check('U-275', '同名 Frame 不同 nodeId 资源路径互不相同',
          page_code_subdir('Web', '181-2980').name != page_code_subdir('Web', '250-2618').name)

    # ── U-276: resolve_asset_paths 使用含 nodeId 的 comp_name 嵌入正确 URL ──────
    # Real data: node 181:3011（trump-pc GmtSection 中的 logo SVG）
    _vec_node_276 = {
        'figmaId': '181:3011', 'figmaName': 'logo', 'figmaType': 'RECTANGLE',
        'isVectorNode': True, 'children': [],
    }
    with tempfile.TemporaryDirectory() as _td_276:
        resolve_asset_paths(_vec_node_276, 'Web-181-2980', Path(_td_276))
    check('U-276', 'localAssetPath 含 nodeId 的 comp_name',
          _vec_node_276.get('localAssetPath') == '/assets/Web-181-2980/181-3011.svg')

    # ── U-378: Patch 3 不应给 __ow wrapper 加 overflow:hidden ─────────────────
    # __ow wrapper 由 ir_builder._wrap_overflow_children 创建，目的就是允许装饰图
    # 超出父框范围显示（clipsContent=False 的 Figma 设计）。
    # Patch 3 的"装饰子节点高度 > 父节点 1.5× → 加 overflow:hidden"触发后，
    # 会把 __ow 的超出效果全部裁掉，即还原了 _wrap_overflow_children 的设计意图。
    # Real case: get-started-features 6777:33056__ow，图片 301px > 174×1.5=261px。
    _ow_node = {
        'figmaId': '6777:33056__ow',
        'figmaType': None,
        'css': {'position': 'relative', 'height': '174px', 'width': '100%', 'flex-shrink': '0'},
        'bb': {'width': 1200.0, 'height': 174.0},
        'children': [
            {
                'figmaId': '6777:33063',
                'isDecorativeElement': True,
                'isImageNode': True,
                'css': {
                    'width': '452px', 'height': '301px',
                    'position': 'absolute', 'left': '809px', 'top': '-87.5px',
                    'pointer-events': 'none',
                },
                'children': [],
            },
            {
                'figmaId': '6777:33056',
                'figmaType': 'FRAME',
                'css': {
                    'height': '174px', 'width': '100%',
                    'display': 'flex', 'flex-direction': 'column',
                    'border-radius': '24px',
                },
                'bb': {'width': 1200.0, 'height': 174.0},
                'children': [],
            },
        ],
    }
    patch_flex_shrink(_ow_node)
    check('U-378a', '__ow wrapper: Patch3 不应加 overflow:hidden',
          'overflow' not in _ow_node.get('css', {}))

    test_brand_replace()


def test_brand_replace():
    """U-377: brand_replace_ir 替换代码标识符中的品牌词，保留文本内容。

    替换：figmaName、cssClass、localAssetPath（目录部分）、semantic.componentName/className。
    不改：characters、textContent、textSegments[].text（用户可见文字保持原始设计稿内容）。
    不改：figmaId、CSS 数值属性（width/color 等）。

    使用通用品牌词（OldCo → NewCo）验证替换逻辑，与具体品牌名无关。
    _PAIRS 默认为空，测试内部临时注入替换对。
    """
    import lib.brand_replace as _br_mod
    from lib.brand_replace import brand_replace_ir

    # 临时注入替换对（不影响全局配置）
    _orig_pairs = _br_mod._PAIRS
    _br_mod._PAIRS = [('OLDCO', 'NEWCO'), ('OldCo', 'NewCo'), ('oldco', 'newco')]
    try:
        ir = {
            'figmaId': '169:33787',                                              # 不应改
            'figmaName': 'OldCo AI Hub 2.0',                                     # → 'NewCo AI Hub 2.0'
            'figmaType': 'FRAME',
            'cssClass': 'oldco-ai-hub-20',                                       # → 'newco-ai-hub-20'
            'characters': None,
            'textContent': None,
            'localAssetPath': '/assets/OldCoAiHub20-169-33787/icon.svg',         # 目录部分替换
            'css': {'width': '1440px', 'background-color': '#fff'},              # 数值属性不替换
            'children': [
                {
                    'figmaId': '169:33788',
                    'figmaName': 'oldco-logo-primary',   # → 'newco-logo-primary'
                    'figmaType': 'TEXT',
                    'cssClass': 'oldco-logo-primary',    # → 'newco-logo-primary'
                    'characters': 'OLDCO AI HUB',        # 文本内容不替换，保持原值
                    'textContent': 'OldCo',              # 文本内容不替换，保持原值
                    'textSegments': [
                        {'text': 'OldCo', 'fontWeight': 600},   # 文本内容不替换，保持原值
                        {'text': ' Hub', 'fontWeight': 400},    # 不含品牌词，不变
                    ],
                    'localAssetPath': '/assets/OldCoAiHub20-169-33787/I169-33977-2-8395.svg',
                    'css': {},
                    'children': [],
                },
                {
                    'figmaId': '169:33789',
                    'figmaName': 'OLDCO_LOGO',   # → 'NEWCO_LOGO'
                    'figmaType': 'INSTANCE',
                    'cssClass': 'oldco-logo',    # → 'newco-logo'
                    'characters': None,
                    'textContent': None,
                    'textSegments': None,
                    'localAssetPath': None,
                    'css': {},
                    'children': [],
                },
            ],
        }

        result = brand_replace_ir(ir)

        # 根节点
        check('U-377a', 'figmaName OldCo → NewCo',
              result['figmaName'] == 'NewCo AI Hub 2.0')
        check('U-377b', 'cssClass oldco → newco',
              result['cssClass'] == 'newco-ai-hub-20')
        check('U-377c', 'figmaId 不变',
              result['figmaId'] == '169:33787')
        check('U-377d', 'localAssetPath 目录部分替换，文件名不变',
              result['localAssetPath'] == '/assets/NewCoAiHub20-169-33787/icon.svg')

        # 子节点 text
        child_text = result['children'][0]
        check('U-377e', 'figmaName oldco-logo-primary → newco-logo-primary',
              child_text['figmaName'] == 'newco-logo-primary')
        check('U-377f', 'characters 文本内容不替换，保持原值 OLDCO AI HUB',
              child_text['characters'] == 'OLDCO AI HUB')
        check('U-377g', 'textContent 文本内容不替换，保持原值 OldCo',
              child_text['textContent'] == 'OldCo')
        check('U-377h', 'textSegments[0].text 文本内容不替换，保持原值 OldCo',
              child_text['textSegments'][0]['text'] == 'OldCo')
        check('U-377i', 'textSegments[1].text 无品牌词，不变',
              child_text['textSegments'][1]['text'] == ' Hub')
        check('U-377j', 'child localAssetPath 目录部分替换',
              child_text['localAssetPath'] == '/assets/NewCoAiHub20-169-33787/I169-33977-2-8395.svg')

        # 子节点 UPPER
        child_upper = result['children'][1]
        check('U-377k', 'figmaName OLDCO_LOGO → NEWCO_LOGO',
              child_upper['figmaName'] == 'NEWCO_LOGO')
        check('U-377l', 'localAssetPath None 不变',
              child_upper['localAssetPath'] is None)

        # semantic 字段（驱动组件导出名和目录名）
        ir_with_sem = {
            'figmaId': '169:33787',
            'figmaName': 'OldCo AI Hub 2.0',
            'figmaType': 'FRAME',
            'cssClass': 'oldco-ai-hub-20',
            'characters': None, 'textContent': None,
            'localAssetPath': None,
            'css': {},
            'children': [],
            'semantic': {
                'htmlTag': 'div',
                'className': 'oldco-ai-hub-20',       # → 'newco-ai-hub-20'
                'componentName': 'OldCoAiHub20',       # → 'NewCoAiHub20'
                'props': [],
                'isExtractedComponent': True,
            },
        }
        r2 = brand_replace_ir(ir_with_sem)
        check('U-377m', 'semantic.componentName OldCoAiHub20 → NewCoAiHub20',
              r2['semantic']['componentName'] == 'NewCoAiHub20')
        check('U-377n', 'semantic.className oldco → newco',
              r2['semantic']['className'] == 'newco-ai-hub-20')
    finally:
        _br_mod._PAIRS = _orig_pairs



    # ── U-379: patch_center_flex_padding_normalize ───────────────────────────
    # Real data: By_Primary Buttons-Dark (177:13510) from ByAiHub20 (169-33787).
    # Figma button component has icon slot on left (padding:12px 2px 12px 28px),
    # but only text child is rendered. With justify-content:center, text appears
    # visually off-center because content area is shifted 13px right of button center.
    # Fix: for isComponentInstance + justify-content:center + only-text children +
    # asymmetric padding (|left-right| > 12px), normalize to symmetric padding.
    try:
        from convert import patch_center_flex_padding_normalize as _pfn

        # Minimal node: component instance with justify-content:center + asymmetric padding
        # + only text child — real structure from ByAiHub20 (169-33787) node 177:13510
        _btn = {
            'figmaId': '177:13510', 'figmaName': 'By_Primary Buttons-Dark',
            'figmaType': 'FRAME', 'isComponentInstance': True,
            'css': {
                'display': 'flex', 'flex-direction': 'row',
                'justify-content': 'center', 'align-items': 'center',
                'padding': '12px 2px 12px 28px',   # asymmetric: left=28, right=2
                'gap': '4px',
            },
            'children': [{
                'figmaId': 'I177:13510;2:8053', 'figmaName': 'Button Text',
                'figmaType': 'TEXT', 'isTextNode': True, 'isComponentInstance': False,
                'css': {'color': '#121214', 'font-size': '16px'},
                'children': [],
            }],
        }
        _pfn(_btn)
        _css = _btn['css']
        _pad = _css.get('padding', '')
        _parts = _pad.split()
        check('U-379a', 'padding has 4 parts after normalization',
              len(_parts) == 4)
        if len(_parts) == 4:
            check('U-379b', 'left padding == right padding (symmetric)',
                  _parts[1] == _parts[3])
            check('U-379c', 'padding no longer 12px 2px 12px 28px (was asymmetric)',
                  _pad != '12px 2px 12px 28px')
    except ImportError:
        check('U-379-IMPORT', 'patch_center_flex_padding_normalize exists in convert.py', False)


    # ── U-380: Patch 12 must preserve z-index on _isOWChild nodes ─────────────
    # Real scenario: 6777:33063 "ChatGPT Image" from PortalRevised (nodeId: 6777-32751)
    # _wrap_overflow_children sets z-index:1 on promoted overflow children so they appear
    # above the parent card. But patch_flex_shrink Patch 12 runs after build_ir and removes
    # ALL z-index values — stripping the z-index set by _wrap_overflow_children.
    # Fix: mark promoted children with _isOWChild:True; Patch 12 must skip these nodes.
    try:
        from convert import patch_flex_shrink as _pfs380

        _promoted_node_380 = {
            'figmaId': '6777:33063',
            'figmaName': 'ChatGPT Image Feb 2',
            'figmaType': 'RECTANGLE',
            '_isOWChild': True,
            'css': {
                'width': '452px', 'height': '301px',
                'position': 'absolute', 'left': '809px', 'top': '-87.5px',
                'pointer-events': 'none',
                'z-index': '1',
            },
            'children': [],
        }
        _pfs380(_promoted_node_380)
        check('U-380', 'Patch 12 preserves z-index:1 on _isOWChild promoted overflow node',
              _promoted_node_380['css'].get('z-index') == '1')
    except ImportError:
        check('U-380-IMPORT', 'patch_flex_shrink exists in convert.py', False)
    except Exception as e:
        check('U-380', f'EXCEPTION: {e}', False)


def test_fetch_retry():
    # ── U-381: fetch_export_urls 网络错误时应重试，最终返回正确 URL ──────────
    # Real scenario: Brand6PreKyc (4030-41365) - Figma images API 第 1 次超时，
    # 第 2 次成功。当前代码只打印 warning 不重试，导致整批 URL 丢失（21 个 PNG 缺失）。
    import importlib
    _conv = importlib.import_module('convert')
    _call_count = [0]

    def _mock_figma_get_retry(url, token):
        _call_count[0] += 1
        if _call_count[0] < 2:
            raise ConnectionError('mock timeout')
        return {'images': {'4030:41601': 'https://cdn.figma.com/img/test.png'}}

    _orig = _conv.figma_get
    try:
        _conv.figma_get = _mock_figma_get_retry
        result = _conv.fetch_export_urls('FAKE_FK', ['4030:41601'], 'png', 'FAKE_TOKEN')
        check('U-381', 'fetch_export_urls 遇网络错误后重试并返回 URL',
              result.get('4030:41601') == 'https://cdn.figma.com/img/test.png')
    finally:
        _conv.figma_get = _orig

    # ── U-382: fetch_image_fill_urls 网络错误时应重试，最终返回正确数据 ─────
    # Real scenario: image fill refs (imageRef → CDN URL) —— 失败返回 {} 导致
    # 所有 fill 图片缺失。需要和 fetch_export_urls 一样加重试。
    _call_count2 = [0]

    def _mock_figma_get_fill(url, token):
        _call_count2[0] += 1
        if _call_count2[0] < 2:
            raise ConnectionError('mock timeout')
        return {'meta': {'images': {'abc123': 'https://cdn.figma.com/fill.png'}}}

    try:
        _conv.figma_get = _mock_figma_get_fill
        result2 = _conv.fetch_image_fill_urls('FAKE_FK', 'FAKE_TOKEN')
        check('U-382', 'fetch_image_fill_urls 遇网络错误后重试并返回数据',
              result2.get('abc123') == 'https://cdn.figma.com/fill.png')
    finally:
        _conv.figma_get = _orig


if __name__ == '__main__':
    reset()
    run_tests()
    test_fetch_retry()
    ok = print_summary()
    sys.exit(0 if ok else 1)
