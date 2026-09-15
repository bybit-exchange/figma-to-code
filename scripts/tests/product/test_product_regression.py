#!/usr/bin/env python3
"""
verify_fixes.py — 产物回归测试（P-*/G-*/M-*）

单元测试已全部拆分到子目录：
  - tests/extract/
  - tests/transform/
  - tests/codegen/
  - tests/split/
  - tests/semantic/

本文件仅保留需要产物目录（3-page-code）才能运行的回归测试。

用法：
  python3 scripts/tests/verify_fixes.py --out=.figma-to-code/3-page-code
"""

import sys
import re
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, reset, get_results, print_summary
from helpers import node_has_css, class_lacks_property, read_page
from lib.paths import texts_defaults_ts_filename


def run_product_tests(out_dir, css_ext='less'):
    print('\n  ─── 产物回归测试 ─────────────────────────')

    def _check_page(page_name):
        files = read_page(out_dir, page_name, css_ext)
        if not files:
            print(f'  ○  (页面 {page_name} 未找到，跳过产物测试)')
        return files

    brand_files = _check_page('Brand6PreKyc')

    # P-1: flex-direction row (not column)
    if brand_files:
        try:
            tsx, scss = brand_files['tsx'], brand_files['scss']
            line = next((l for l in tsx.split('\n') if 'data-figma-id="4030:41879"' in l), None)
            if line is None:
                print('  ○  [P-1] 节点未找到，跳过')
            else:
                m = re.search(r"styles\['([^']+)'\]", line)
                if m:
                    cls = re.escape(m.group(1))
                    bm = re.search(rf'\.{cls}\s*\{{([^}}]+)\}}', scss, re.DOTALL)
                    block = bm.group(1) if bm else ''
                    check('P-1', 'Container 4030:41879 → flex-direction: row（修复 GRID 被错误处理为 column）',
                          'flex-direction: row' in block and 'flex-direction: column' not in block)
                else:
                    print('  ○  [P-1] class 未找到，跳过')
        except Exception as e:
            check('P-1', 'flex-direction row', False)
            print(f'       Error: {e}')

    # P-2: align-items flex-end (not flex-start)
    if brand_files:
        try:
            tsx, scss = brand_files['tsx'], brand_files['scss']
            figma_id = 'I4030:41667;1:13752;15017:20143'
            line = next((l for l in tsx.split('\n') if f'data-figma-id="{figma_id}"' in l), None)
            if line is None:
                print('  ○  [P-2] 节点未找到，跳过')
            else:
                m = re.search(r"styles\['([^']+)'\]", line)
                if m:
                    cls = re.escape(m.group(1))
                    bm = re.search(rf'\.{cls}\s*\{{([^}}]+)\}}', scss, re.DOTALL)
                    block = bm.group(1) if bm else ''
                    check('P-2', 'Right outline I4030:41667;1:13752;15017:20143 → align-items: flex-end（修复 Eyebrow-Core 右侧 bar 间距不对）',
                          'align-items: flex-end' in block and 'align-items: flex-start' not in block)
                else:
                    print('  ○  [P-2] class 未找到，跳过')
        except Exception as e:
            check('P-2', 'align-items flex-end', False)
            print(f'       Error: {e}')

    # P-3: overflow hidden
    if brand_files:
        try:
            result = node_has_css(brand_files['scss'], brand_files['tsx'], '4030:41663', 'overflow', 'hidden')
            if result is None:
                print('  ○  [P-3] 节点未找到，跳过')
            else:
                check('P-3', 'Card 4030:41663 → overflow: hidden（修复 shadow 旋转层溢出父容器边界）', result)
        except Exception as e:
            check('P-3', 'overflow:hidden', False)
            print(f'       Error: {e}')

    # P-5: display none
    if brand_files:
        try:
            result = node_has_css(brand_files['scss'], brand_files['tsx'], '4030:41665', 'display', 'none')
            if result is None:
                print('  ○  [P-5] 节点未找到，跳过')
            else:
                check('P-5', 'Shadow 4030:41665 → display:none（修复白色渐变层遮住卡片内容）', result)
        except Exception as e:
            check('P-5', 'display:none', False)
            print(f'       Error: {e}')

    # P-6: <img> with svg path
    if brand_files:
        try:
            tsx = brand_files['tsx']
            line = next((l for l in tsx.split('\n') if 'data-figma-id="4030:41415"' in l), None)
            if line is None:
                print('  ○  [P-6] 节点未找到，跳过')
            else:
                check('P-6', 'Vector 1401 4030:41415 → 渲染为 <img>（修复 align-self:stretch 矢量被误判为线条型）',
                      '<img' in line and '4030-41415.svg' in line)
        except Exception as e:
            check('P-6', '<img> with svg', False)
            print(f'       Error: {e}')

    # P-12: justify-content:space-between AND no gap
    if brand_files:
        try:
            tsx, scss = brand_files['tsx'], brand_files['scss']
            line = next((l for l in tsx.split('\n') if 'data-figma-id="4030:41422"' in l), None)
            if line is None:
                print('  ○  [P-12] 节点未找到，跳过')
            else:
                m = re.search(r"styles\['([^']+)'\]", line)
                if m:
                    cls = re.escape(m.group(1))
                    bm = re.search(rf'\.{cls}\s*\{{([^}}]+)\}}', scss, re.DOTALL)
                    block = bm.group(1) if bm else ''
                    check('P-12', 'TradFi card 4030:41422 → justify-content:space-between 无 gap（修复布局整体靠下）',
                          'justify-content: space-between' in block and 'gap:' not in block)
                else:
                    print('  ○  [P-12] class 未找到，跳过')
        except Exception as e:
            check('P-12', 'space-between no gap', False)
            print(f'       Error: {e}')

    # ── helper for getting a node's CSS block ─────────────────────────────
    def _nb(scss, tsx, figma_id):
        line = next((l for l in tsx.split('\n') if f'data-figma-id="{figma_id}"' in l), None)
        if not line:
            return None
        m = re.search(r"styles\['([^']+)'\]", line)
        if not m:
            return None
        bm = re.search(rf'\.{re.escape(m.group(1))}\s*\{{([^}}]+)\}}', scss, re.DOTALL)
        return bm.group(1) if bm else ''

    # ── TomorrowlandLandingPage4 ─────────────────────────────────────────
    tl = read_page(out_dir, 'TomorrowlandLandingPage4', css_ext)
    if not tl:
        print('  ○  (页面 TomorrowlandLandingPage4 未找到，跳过 G-1…T-8)')
    else:
        tsx, scss = tl['tsx'], tl['scss']
        check('G-1', 'ELLIPSE 圆点 → border-radius: 50%',
              node_has_css(scss, tsx, 'I39641:9277;39641:6297', 'border-radius', '50%'))
        check('G-2', 'ELLIPSE active dot → border-radius: 50%',
              node_has_css(scss, tsx, 'I39641:9277;39641:6296', 'border-radius', '50%'))
        check('G-3', 'tokenizedvip card → overflow: hidden',
              node_has_css(scss, tsx, 'I39641:7672;39641:5118', 'overflow', 'hidden'))
        check('G-4', 'heading-3 → 不得有 overflow:hidden',
              class_lacks_property(scss, 'heading-3', 'overflow') is not False)
        check('G-5', 'header-cta → width: 398px',
              node_has_css(scss, tsx, 'I39641:7473;39641:5643', 'width', '398px'))
        check('G-6', 'REGULAR_POLYGON → 渲染为 <img>',
              any('I39641:9303;39760:4679' in l and '<img' in l for l in tsx.split('\n')))
        _tl_fn = texts_defaults_ts_filename('TomorrowlandLandingPage4')
        _tl_texts = (tl['dir'] / _tl_fn).read_text() if (tl['dir'] / _tl_fn).exists() else ''
        _tl_all = tsx + _tl_texts
        check('T-1', '步骤标题文字完整',
              all(s in _tl_all for s in ['Get your Card', 'Top-up your card', 'Use it throughout']))
        check('T-2', '步骤描述卡片 → overflow:hidden',
              node_has_css(scss, tsx, 'I39641:9303;39641:6374', 'overflow', 'hidden'))
        check('T-3', 'Gateway 3 张照片在 TSX',
              all(s in tsx for s in [
                  'I39641:9277;40011:4472', 'I39641:9277;40011:4469', 'I39641:9277;40011:4466']))
        check('T-4', '"here" 渲染为橙色 span',
              any('I39641:9303;39641:6379' in l and 'span style' in l for l in tsx.split('\n')))
        check('T-5', 'Rectangle 34 → border: 1px',
              node_has_css(scss, tsx, 'I39641:9277;39641:6288', 'border'))
        tl_b = _nb(scss, tsx, 'I39641:9277;39641:6283')
        check('T-6', 'Rectangle 31 → mix-blend-mode: plus-lighter（LINEAR_DODGE 正确映射）',
              tl_b is not None and 'mix-blend-mode: plus-lighter' in tl_b)
        check('T-7', '宽矩形渐变 → 不含旧 -36.8deg',
              tl_b is None or ('-36' not in tl_b and '323.2' not in tl_b))
        tl_g = _nb(scss, tsx, 'I39641:9277;39641:6284')
        check('T-8', 'Group → left ≈ 875px',
              tl_g is not None and bool(re.search(r'left:\s*8[67]\d', tl_g)))

    # ── ExamplePage2（内部测试，需要对应 Figma 转换输出）───────────────────
    ai = read_page(out_dir, 'ExamplePage2', css_ext)
    if not ai:
        print('  ○  (页面 ExamplePage2 未找到，跳过 G-7…G-10)')
    else:
        tsx, scss = ai['tsx'], ai['scss']
        check('G-7', 'logo GROUP (169:33976) 在 TSX 中',
              'data-figma-id="169:33976"' in tsx)
        # CSS class name is auto-generated from the Figma layer name in the design file
        bm_logo = re.search(r'\.n01_base[^\s]*logo-dark\s*\{([^}]+)\}', scss, re.DOTALL)
        b_logo = bm_logo.group(1) if bm_logo else ''
        check('G-8', 'logo-dark → width 或 display:flex',
              'width:' in b_logo or 'display: flex' in b_logo or bm_logo is None)
        b_g9 = _nb(scss, tsx, '169:35626')
        check('G-9', 'LINE 连接线 → 无旋转 transform',
              b_g9 is None or 'transform:' not in b_g9 or 'rotate' not in b_g9)
        check('G-10', 'AI API Key → 有 line-height',
              node_has_css(scss, tsx, '169:35559', 'line-height'))

    # ── BtcPizzaDay ──────────────────────────────────────────────────────
    btc = read_page(out_dir, 'BtcPizzaDay', css_ext)
    if not btc:
        print('  ○  (页面 BtcPizzaDay 未找到，跳过 B-*)')
    else:
        tsx, scss = btc['tsx'], btc['scss']
        check('B-1', 'BtcPizzaDay 页面非空', len(scss) > 1000)
        check('B-2', '311:14890 → display:flex',
              node_has_css(scss, tsx, '311:14890', 'display', 'flex'))
        b_b3 = _nb(scss, tsx, '311:14890')
        check('B-3', '311:14890 → 无 z-index',
              b_b3 is None or 'z-index:' not in b_b3)
        check('B-4', 'HUAWEI/DJI 容器 → min-height',
              node_has_css(scss, tsx, '311:15301', 'min-height'))
        check('B-5', 'price-trend → top: 50%',
              node_has_css(scss, tsx, '311:15164', 'top', '50%'))
        check('B-6', 'ellipse-39 → left:50%',
              node_has_css(scss, tsx, '311:15278', 'left', '50%'))
        check('B-7', 'shutterstock → left:50%',
              node_has_css(scss, tsx, '311:15197', 'left', '50%'))
        b_b8 = _nb(scss, tsx, '311:15443')
        check('B-8', 'gold 卡片 → align-self:stretch 无显式 width',
              b_b8 is not None and 'align-self: stretch' in b_b8 and ' width: ' not in b_b8)
        b_b9 = _nb(scss, tsx, '311:15165')
        check('B-9', 'price trend → <img> 无 border',
              b_b9 is not None and 'border:' not in b_b9 and
              any('311:15165' in l and '<img' in l for l in tsx.split('\n')))
        check('B-10', 'glass card → <div> 非 <img>',
              any('311:15272' in l and '<img' not in l for l in tsx.split('\n')))

    # ── N11 ──────────────────────────────────────────────────────────────
    n11 = read_page(out_dir, 'N11', css_ext)
    if not n11:
        print('  ○  (页面 N11 未找到，跳过 N-*)')
    else:
        tsx, scss = n11['tsx'], n11['scss']
        bm_n1 = re.search(r'\.frame-482128\s*\{([^}]+)\}', scss, re.DOTALL)
        b_n1 = bm_n1.group(1) if bm_n1 else ''
        check('N-1', 'footer nav → 无 width:10px/height:8px（修复内联图标坍塌 flex 容器）',
              'width: 10px' not in b_n1 and 'height: 8px' not in b_n1)
        _n11_fn = texts_defaults_ts_filename('N11')
        _n11_defaults = (n11['dir'] / _n11_fn).read_text() if (n11['dir'] / _n11_fn).exists() else ''
        check('N-2', 'footer nav → 含 "Newcomer Academy"', 'Newcomer Academy' in (tsx + _n11_defaults))
        check('N-3', '重复 footer → display:none',
              node_has_css(scss, tsx, '2573:27805', 'display', 'none'))
        b_n4 = _nb(scss, tsx, 'I2573:27872;1254:51953;1254:48768')
        check('N-4', 'footer nav 图标 → 非 position:absolute',
              b_n4 is None or 'position: absolute' not in b_n4)
        b_n5 = _nb(scss, tsx, '6880:8426')
        check('N-5', 'Tab #1 → border-bottom:2px 无多余边框',
              b_n5 is not None and 'border-bottom:' in b_n5 and ' border: ' not in b_n5)

    # ── Node20233464 ─────────────────────────────────────────────────────
    node = read_page(out_dir, 'Node20233464', css_ext)
    if not node:
        print('  ○  (页面 Node20233464 未找到，跳过 M-*)')
    else:
        tsx, scss = node['tsx'], node['scss']
        bm1 = re.search(r'\.live-trading\s*\{([^}]+)\}', scss, re.DOTALL)
        b_m1 = bm1.group(1) if bm1 else ''
        check('M-1', '渐变文字 → background-image + background-clip:text',
              'background-image:' in b_m1 and 'background-clip: text' in b_m1 and
              'color: transparent' in b_m1)
        b_m2 = _nb(scss, tsx, '1484:26401')
        check('M-2', '卡片渐变描边 → box-shadow',
              b_m2 is not None and 'box-shadow' in b_m2)
        b_m3 = _nb(scss, tsx, '202:33776')
        check('M-3', 'SVG → 无多余 border',
              b_m3 is None or ('border:' not in b_m3 and 'border-top:' not in b_m3))
        b_m4 = _nb(scss, tsx, '202:33504')
        check('M-4', 'glow text → text-shadow 无 box-shadow',
              b_m4 is not None and 'text-shadow:' in b_m4 and 'box-shadow:' not in b_m4)
        b_m5 = _nb(scss, tsx, '202:33571')
        check('M-5', 'VIP section → overflow:hidden',
              b_m5 is not None and 'overflow: hidden' in b_m5)
        check('M-6', '"5000" 金色文字 → 含 span style',
              any('1484:26447' in l and 'span style' in l for l in tsx.split('\n')))
        bm7 = re.search(r'\.demo-trading\s*\{([^}]+)\}', scss, re.DOTALL)
        b_m7 = bm7.group(1) if bm7 else ''
        check('M-7', 'DEMO TRADING → background-image + background-clip:text',
              'background-image:' in b_m7 and 'background-clip: text' in b_m7 and
              'color: transparent' in b_m7)

    # ── Brand6PreKyc 剩余 P-* + G-11 ────────────────────────────────────
    if brand_files:
        tsx, scss = brand_files['tsx'], brand_files['scss']
        b_p4 = _nb(scss, tsx, '4030:41611')
        if b_p4 is not None:
            check('P-4', 'Trade card → align-self:stretch 无 width',
                  'align-self: stretch' in b_p4 and ' width:' not in b_p4)
        b_p7 = _nb(scss, tsx, '4030:41877')
        if b_p7 is not None:
            check('P-7', '大图片 → left:50% 非 width:100%',
                  ('left: 50%' in b_p7 or 'left: calc(50%' in b_p7) and
                  'width: 100%' not in b_p7)
        b_p8 = _nb(scss, tsx, '4030:41805')
        if b_p8 is not None:
            check('P-8', '图片父容器 → overflow:hidden', 'overflow: hidden' in b_p8)
        check('P-9', 'world/Black icon → 单 <img>',
              any('4030:41882' in l and '<img' in l for l in tsx.split('\n')))
        # P-10: text gap overlay fix — '10%' (4030:41640) must NOT be position:absolute
        # (was: check white-space:pre-line on merged text, now text is split + overlay is in flow)
        b_p10 = _nb(scss, tsx, '4030:41640')
        check('P-10', '10% overlay (4030:41640) → 无 position:absolute（text gap overlay fix）',
              b_p10 is not None and 'position: absolute' not in b_p10)
        b_p11 = _nb(scss, tsx, 'I4030:41475;16543:263189')
        if b_p11 is not None:
            check('P-11', 'tab-1 → border-bottom only',
                  'border-bottom:' in b_p11 and ' border: ' not in b_p11)
        check('P-13', '图片容器 4030:41526 → overflow:hidden',
              node_has_css(scss, tsx, '4030:41526', 'overflow', 'hidden'))
        check('P-14', 'FRAME 复合图标 → 单 <img>',
              any('4030:41460' in l and '<img' in l and '.svg' in l for l in tsx.split('\n')))
        check('P-15', '透明占位文字 → color:transparent',
              node_has_css(scss, tsx, '4030:41482', 'color', 'transparent'))
        line_p16 = next((l for l in tsx.split('\n') if 'data-figma-id="4030:41669"' in l), None)
        check('P-16', '混色文本 → 含换行表达式',
              line_p16 is not None and r"{'\n'}" in (line_p16 or ''))
        b_p17a = _nb(scss, tsx, '4030:41763')
        b_p17b = _nb(scss, tsx, '4030:41794')
        if b_p17a is not None and b_p17b is not None:
            check('P-17', 'Market Card → align-items:flex-start',
                  'align-items: flex-start' in (b_p17a + b_p17b) and
                  'align-items: flex-end' not in (b_p17a + b_p17b))
        b_p18 = _nb(scss, tsx, '4030:41664')
        if b_p18 is not None:
            # TODO: 未来实现响应式全宽 patch 后改为 width:100%; left:0
            check('P-18', '背景图 4030:41664 → 存在 position:absolute（绝对定位背景图）',
                  'position: absolute' in b_p18)
        b_g11 = _nb(scss, tsx, '4030:41376')
        if b_g11 is not None:
            check('G-11', '步骤指示点 → top:1px 非 top:50%',
                  'top: 1px' in b_g11 and 'top: 50%' not in b_g11)
        b_g12 = _nb(scss, tsx, '4030:41675')
        if b_g12 is not None:
            check('G-12', 'Stats 分隔线 → 无 position:absolute，有 border',
                  'position:' not in b_g12 and 'border:' in b_g12)
        b_g13 = _nb(scss, tsx, 'I4030:41667;1:13752;15017:20136')
        if b_g13 is not None:
            check('G-13', 'outline FRAME → 无 transform（清除 1.57° 精度误差）',
                  'transform:' not in b_g13)

        # P-BG1: 背景图 4030:41664 当前为固定宽度居中，TODO: 未来实现响应式 patch
        b_pbg1 = _nb(scss, tsx, '4030:41664')
        if b_pbg1 is not None:
            check('P-BG1', '背景图 4030:41664 → width ≈ 1376px（固定宽度，待响应式 patch 实现后更新）',
                  '1375' in b_pbg1 or '1376' in b_pbg1)
            check('P-BG1b', '背景图 4030:41664 → left 包含 calc(50%+99.75px) 居中偏移',
                  'calc(50% + 99.75px)' in b_pbg1)
            check('P-BG1c', '背景图 4030:41664 → 含 translateX(-50%) 居中 transform',
                  'translateX(-50%)' in b_pbg1)

        # P-PA18: Patch18 修复：第三张卡片 4030:41890 align-items 必须为 center
        b_ppa18 = _nb(scss, tsx, '4030:41890')
        if b_ppa18 is not None:
            check('P-PA18', '容器 4030:41890 → align-items: center（不再被 Patch18 误设为 flex-end）',
                  'align-items: center' in b_ppa18 and 'align-items: flex-end' not in b_ppa18)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='产物回归测试')
    parser.add_argument('--out', default='.figma-to-code/3-page-code',
                        help='产物目录路径')
    parser.add_argument('--css-ext', default='less',
                        help='CSS 文件扩展名（默认: less）')
    args = parser.parse_args()

    reset()

    print('\n' + '═' * 44)
    print('  产物回归测试（P-*/G-*/M-*）')
    print('═' * 44)

    out_path = Path(args.out)
    if out_path.exists() and any(out_path.iterdir()):
        run_product_tests(str(out_path), args.css_ext)
    else:
        print(f'\n  ○  产物目录不存在或为空: {args.out}，跳过')

    ok = print_summary('verify_fixes')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
