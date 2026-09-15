#!/usr/bin/env python3
"""
visual_report.py — 生成可视化回归报告

读取 run_visual_report.sh 写入的 results.json，对每个页面：
  1. 从本地路径加载渲染截图
  2. 通过 Figma API 下载设计稿截图
  3. 用 pillow 计算像素 diff，得出相似度
  4. 生成自包含 HTML 报告（图片 base64 内联）

用法：
  python3 visual_report.py --results-json=results.json --out=visual-report.html
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ── 依赖检测 ─────────────────────────────────────────────────────────────────

try:
    from PIL import Image, ImageChops, ImageEnhance, ImageFilter
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# ── Figma token ───────────────────────────────────────────────────────────────

def _get_figma_token() -> str | None:
    for env in ('FIGMA_TOKEN', 'FIGMA_ACCESS_TOKEN'):
        v = os.environ.get(env)
        if v:
            return v
    token_file = Path.home() / '.claude' / 'figma-token'
    if token_file.exists():
        return token_file.read_text().strip()
    return None

# ── Figma API 截图下载 ─────────────────────────────────────────────────────────

def _fetch_figma_image(file_key: str, node_id: str, token: str, scale: int) -> bytes | None:
    """单次下载 Figma 截图（指定 scale），返回 PNG bytes 或 None。"""
    import io as _io
    node_id_encoded = node_id.replace(':', '-').replace('%3A', '-')
    url = (
        f'https://api.figma.com/v1/images/{file_key}'
        f'?ids={urllib.parse.quote(node_id)}&format=png&scale={scale}'
    )
    req = urllib.request.Request(url, headers={'X-Figma-Token': token})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read())
    img_url = (data.get('images') or {}).get(node_id) or (data.get('images') or {}).get(node_id_encoded)
    if not img_url:
        return None
    with urllib.request.urlopen(img_url, timeout=90) as img_resp:
        raw = img_resp.read()
    return raw


def download_figma_screenshot(file_key: str, node_id: str, token: str, viewport: int = 1440) -> bytes | None:
    """通过 Figma REST API 下载节点截图，返回 PNG bytes 或 None。超时自动重试 3 次。
    先请求 scale=2。若 Figma 因图像过大降采样导致宽度 < 1.95×viewport，
    则回退到 scale=1 以避免非整数缩放引入的 LANCZOS 模糊误差。
    """
    import time, io as _io
    for attempt in range(1, 4):
        try:
            raw = _fetch_figma_image(file_key, node_id, token, scale=2)
            if raw is None:
                print(f'  ⚠  Figma 未返回截图 URL', file=sys.stderr)
                return None
            # 检查是否是真 2x：宽度应为 viewport × 2（允许 ±5%）
            if HAS_PILLOW:
                from PIL import Image as _Img
                img_w = _Img.open(_io.BytesIO(raw)).size[0]
                expected_2x = viewport * 2
                if img_w < expected_2x * 0.95:
                    # Figma 因大小限制降采样——改用 scale=1 获得精确 1x 截图
                    print(f'   Figma 截图宽度 {img_w}px < 期望 {expected_2x}px，回退到 scale=1', file=sys.stderr)
                    raw1 = _fetch_figma_image(file_key, node_id, token, scale=1)
                    if raw1 is not None:
                        raw = raw1
                        print(f'   使用 scale=1 截图（{_Img.open(_io.BytesIO(raw)).size[0]}px 宽）', file=sys.stderr)
            return raw
        except Exception as e:
            print(f'  ⚠  Figma 截图下载失败（第 {attempt}/3 次）: {e}', file=sys.stderr)
            if attempt < 3:
                time.sleep(3 * attempt)
    return None

# ── 像素 diff ─────────────────────────────────────────────────────────────────

def compute_diff(rendered_bytes: bytes, figma_bytes: bytes) -> tuple[float, bytes | None]:
    """
    计算相似度 (0-100%) 和差异高亮图（红色通道放大 5x）。
    返回 (similarity_pct, diff_png_bytes_or_None)。
    """
    if not HAS_PILLOW:
        return 0.0, None
    try:
        img_rendered = Image.open(io.BytesIO(rendered_bytes)).convert('RGBA')
        img_figma = Image.open(io.BytesIO(figma_bytes)).convert('RGBA')

        # 统一尺寸（以 Figma 截图为基准）
        if img_rendered.size != img_figma.size:
            img_rendered = img_rendered.resize(img_figma.size, Image.LANCZOS)

        diff = ImageChops.difference(img_rendered.convert('RGB'), img_figma.convert('RGB'))
        diff_gray = diff.convert('L')

        total_pixels = diff_gray.width * diff_gray.height
        histogram = diff_gray.histogram()
        # 计算 ~3.5% 阈值以内的像素占比（灰度值 0~9 视为"相同"）
        identical = sum(histogram[:10])  # 0~9 灰度值视为"相同"
        similarity = identical / total_pixels * 100.0

        # 差异高亮图：contrast 放大 5x，染成红色调
        enhanced = ImageEnhance.Contrast(diff_gray).enhance(5.0)
        enhanced_rgb = enhanced.convert('RGB')
        r, g, b = enhanced_rgb.split()
        # 红色通道保留，其余压暗
        zero_channel = Image.new('L', enhanced_rgb.size, 0)
        highlight = Image.merge('RGB', [r, zero_channel, zero_channel])

        # 叠加到 Figma 截图上（半透明混合）
        base = img_figma.convert('RGB').copy()
        base.paste(highlight, mask=enhanced)

        buf = io.BytesIO()
        base.save(buf, format='PNG')
        return similarity, buf.getvalue()
    except Exception as e:
        print(f'  ⚠  diff 计算失败: {e}', file=sys.stderr)
        return 0.0, None

# ── base64 工具 ───────────────────────────────────────────────────────────────

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')

def _img_tag(data: bytes | None, alt: str, placeholder: str = '') -> str:
    if data:
        return f'<img src="data:image/png;base64,{_b64(data)}" alt="{alt}" style="max-width:100%;border:1px solid #333;" />'
    return f'<div class="placeholder">{placeholder or alt}</div>'

# ── HTML 报告生成 ──────────────────────────────────────────────────────────────

_STATUS_ICON = {True: '✅', False: '❌', None: '—'}

def _status(v: bool | None) -> str:
    return _STATUS_ICON.get(v, '—')

def _sim_class(pct: float | None) -> str:
    if pct is None:
        return 'sim-na'
    if pct >= 97:
        return 'sim-good'
    if pct >= 90:
        return 'sim-warn'
    return 'sim-bad'

def _validation_table(val: dict[str, Any]) -> str:
    steps = [
        ('test_all',      '单元测试 (test_all)'),
        ('pipeline',      '中间产物 (pipeline)'),
        ('validate_split','拆分校验 (validate_split)'),
        ('validate',      '层校验 (validate)'),
        ('test_product',  '产物契约 (test_product)'),
    ]
    rows = []
    for key, label in steps:
        entry = val.get(key) or {}
        passed = entry.get('passed')
        icon = _status(passed)
        output = entry.get('output', '（无输出）')
        escaped = output.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        rows.append(f'''
        <details class="step-detail">
          <summary><span class="step-icon">{icon}</span> {label}</summary>
          <pre class="step-output">{escaped}</pre>
        </details>''')
    return '\n'.join(rows)

def generate_html(entries: list[dict], mode: str = 'fast') -> str:
    summary_rows = []
    detail_sections = []

    for e in entries:
        name = e.get('name', 'unknown')
        val = e.get('validation_results') or {}
        sim = e.get('similarity_pct')
        sim_str = f'{sim:.1f}%' if sim is not None else '—'
        sim_cls = _sim_class(sim)
        sim_icon = '✅' if (sim or 0) >= 97 else ('⚠️' if (sim or 0) >= 90 else '❌')
        if sim is None:
            sim_icon = '—'

        def _v(k: str) -> str:
            entry = val.get(k) or {}
            return _status(entry.get('passed'))

        summary_rows.append(f'''
      <tr>
        <td class="page-name">{name}</td>
        <td>{_v("test_all")}</td>
        <td>{_v("pipeline")}</td>
        <td>{_v("validate_split")}</td>
        <td>{_v("validate")}</td>
        <td>{_v("test_product")}</td>
        <td class="{sim_cls}">{sim_icon} {sim_str}</td>
      </tr>''')

        figma_img = e.get('_figma_img_bytes')
        rendered_img = e.get('_rendered_img_bytes')
        diff_img = e.get('_diff_img_bytes')

        figma_fail = e.get('_figma_fail_reason', '')
        figma_placeholder = f'Figma 截图获取失败：{figma_fail}' if figma_fail else ''
        figma_html = _img_tag(figma_img, 'Figma 设计稿', figma_placeholder)
        rendered_html = _img_tag(rendered_img, '渲染截图', '渲染截图（Playwright 未安装或截图失败）' if not rendered_img else '')
        diff_html = _img_tag(diff_img, '差异高亮', '差异图（需要 pillow + 两张截图均存在）' if not diff_img else '')

        detail_sections.append(f'''
    <section class="page-section" id="page-{name}">
      <h2>{name}</h2>
      <div class="validation-steps">
        <h3>验证步骤</h3>
        {_validation_table(val)}
      </div>
      <div class="screenshots">
        <div class="screenshot-col">
          <div class="screenshot-label">Figma 设计稿</div>
          {figma_html}
        </div>
        <div class="screenshot-col">
          <div class="screenshot-label">渲染截图</div>
          {rendered_html}
        </div>
        <div class="screenshot-col">
          <div class="screenshot-label">差异高亮</div>
          {diff_html}
        </div>
      </div>
    </section>''')

    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Figma-to-Code 视觉回归报告</title>
  <style>
    :root {{
      --bg: #0d1117; --surface: #161b22; --border: #30363d;
      --text: #c9d1d9; --text-dim: #8b949e; --accent: #58a6ff;
      --good: #3fb950; --warn: #d29922; --bad: #f85149;
    }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
    header {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 20px 32px; }}
    header h1 {{ margin: 0; font-size: 20px; color: #fff; }}
    header p {{ margin: 4px 0 0; color: var(--text-dim); font-size: 13px; }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 32px; }}
    h2 {{ color: var(--accent); font-size: 18px; border-bottom: 1px solid var(--border); padding-bottom: 8px; margin-top: 48px; }}
    h3 {{ color: var(--text-dim); font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; margin: 24px 0 8px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th {{ background: var(--surface); color: var(--text-dim); font-weight: 600; padding: 10px 16px; text-align: left; border: 1px solid var(--border); font-size: 12px; text-transform: uppercase; }}
    td {{ padding: 10px 16px; border: 1px solid var(--border); text-align: center; }}
    td.page-name {{ text-align: left; font-weight: 500; color: var(--accent); }}
    .sim-good {{ color: var(--good); font-weight: 600; }}
    .sim-warn {{ color: var(--warn); font-weight: 600; }}
    .sim-bad  {{ color: var(--bad); font-weight: 600; }}
    .sim-na   {{ color: var(--text-dim); }}
    tr:hover {{ background: rgba(88, 166, 255, 0.05); }}
    .page-section {{ margin-top: 48px; }}
    .validation-steps {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 16px; margin: 16px 0; }}
    details.step-detail {{ margin: 4px 0; }}
    details.step-detail > summary {{
      cursor: pointer; padding: 6px 8px; border-radius: 4px; font-size: 13px;
      list-style: none; display: flex; align-items: center; gap: 8px;
    }}
    details.step-detail > summary:hover {{ background: rgba(255,255,255,0.05); }}
    .step-icon {{ font-size: 16px; }}
    .step-output {{
      margin: 8px 0 0 28px; padding: 10px 12px; background: #0d1117;
      border: 1px solid var(--border); border-radius: 4px; font-size: 12px;
      color: var(--text-dim); white-space: pre-wrap; overflow-x: auto;
      max-height: 300px; overflow-y: auto;
    }}
    .screenshots {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-top: 16px; }}
    .screenshot-col {{ display: flex; flex-direction: column; gap: 8px; }}
    .screenshot-label {{ font-size: 12px; color: var(--text-dim); font-weight: 600; text-transform: uppercase; }}
    .screenshot-col img {{ width: 100%; border-radius: 4px; }}
    .placeholder {{
      background: var(--surface); border: 1px dashed var(--border); border-radius: 4px;
      padding: 32px 16px; text-align: center; color: var(--text-dim); font-size: 12px;
    }}
    .toc {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 16px 24px; margin: 24px 0; }}
    .toc a {{ color: var(--accent); text-decoration: none; font-size: 14px; }}
    .toc a:hover {{ text-decoration: underline; }}
    .toc li {{ margin: 4px 0; }}
  </style>
</head>
<body>
  <header>
    <h1>Figma-to-Code 视觉回归报告</h1>
    <p>共 {len(entries)} 个页面 &nbsp;|&nbsp; 模式: {'🚀 FAST（跳过 CC，快速算法验证）' if mode == 'fast' else '✨ FULL（含 Code Connect，Moly 精确映射）'} &nbsp;|&nbsp; figma-to-code 自动生成</p>
  </header>
  <div class="container">
    <h2>汇总</h2>
    <table>
      <thead>
        <tr>
          <th>页面</th>
          <th>单元测试</th>
          <th>中间产物</th>
          <th>拆分校验</th>
          <th>层校验</th>
          <th>产物契约</th>
          <th>视觉还原度</th>
        </tr>
      </thead>
      <tbody>
        {''.join(summary_rows)}
      </tbody>
    </table>

    <div class="toc">
      <h3>页面目录</h3>
      <ul>
        {''.join(f'<li><a href="#page-{e.get("name","")}">{e.get("name","unknown")}</a></li>' for e in entries)}
      </ul>
    </div>

    {''.join(detail_sections)}
  </div>
</body>
</html>'''

# ── 主流程 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='生成 figma-to-code 视觉回归报告')
    parser.add_argument('--results-json', required=True, help='run_visual_report.sh 输出的 results.json 路径')
    parser.add_argument('--out', default='visual-report.html', help='HTML 报告输出路径')
    parser.add_argument('--mode', default='fast', choices=['fast', 'full'], help='测试模式: fast（默认）| full（含 CC）')
    args = parser.parse_args()

    results_path = Path(args.results_json)
    if not results_path.exists():
        print(f'❌  results.json 不存在: {results_path}', file=sys.stderr)
        sys.exit(1)

    entries: list[dict] = json.loads(results_path.read_text())
    if not isinstance(entries, list):
        print('❌  results.json 格式错误，期望 JSON 数组', file=sys.stderr)
        sys.exit(1)

    figma_token = _get_figma_token()
    if not figma_token:
        print('⚠  未找到 FIGMA_TOKEN，Figma 截图列将为空', file=sys.stderr)

    if not HAS_PILLOW:
        print('⚠  pillow 未安装，跳过 diff 计算。安装: pip3 install pillow', file=sys.stderr)

    for i, entry in enumerate(entries):
        name = entry.get('name', f'page_{i}')
        print(f'\n▶  处理页面: {name}')

        # 加载渲染截图
        rendered_bytes: bytes | None = None
        rendered_path = entry.get('rendered_screenshot')
        if rendered_path and Path(rendered_path).exists():
            rendered_bytes = Path(rendered_path).read_bytes()
            print(f'   渲染截图: {Path(rendered_path).name} ({len(rendered_bytes)//1024}KB)')
        else:
            print(f'   渲染截图: 未找到（{rendered_path}）')

        # 下载 Figma 截图
        figma_bytes: bytes | None = None
        figma_fail_reason = ''
        if figma_token:
            file_key = entry.get('file_key', '')
            node_id = entry.get('node_id', '')
            if file_key and node_id:
                print(f'   下载 Figma 截图（node: {node_id}）...')
                figma_bytes = download_figma_screenshot(file_key, node_id, figma_token, entry.get('viewport', 1440))
                if figma_bytes:
                    print(f'   Figma 截图: {len(figma_bytes)//1024}KB')
                else:
                    figma_fail_reason = '网络超时或 API 错误'
            else:
                figma_fail_reason = '缺少 file_key / node_id'
                print('   跳过 Figma 截图（缺少 file_key 或 node_id）')
        else:
            figma_fail_reason = '未设置 FIGMA_TOKEN'
            print('   跳过 Figma 截图（无 token）')

        # 计算 diff
        diff_bytes: bytes | None = None
        similarity: float | None = None
        if rendered_bytes and figma_bytes and HAS_PILLOW:
            print('   计算像素 diff...')
            similarity, diff_bytes = compute_diff(rendered_bytes, figma_bytes)
            print(f'   相似度: {similarity:.1f}%')
        elif rendered_bytes and figma_bytes and not HAS_PILLOW:
            print('   跳过 diff（pillow 未安装）')

        entry['similarity_pct'] = similarity
        entry['_figma_img_bytes'] = figma_bytes
        entry['_figma_fail_reason'] = figma_fail_reason
        entry['_rendered_img_bytes'] = rendered_bytes
        entry['_diff_img_bytes'] = diff_bytes

    html = generate_html(entries, mode=args.mode)
    out_path = Path(args.out)
    out_path.write_text(html, encoding='utf-8')
    print(f'\n✅  报告已生成: {out_path.resolve()}')
    print(f'   包含 {len(entries)} 个页面')

    # 打印汇总
    print('\n── 汇总 ───────────────────────────────────────')
    for e in entries:
        sim = e.get('similarity_pct')
        sim_str = f'{sim:.1f}%' if sim is not None else '未知'
        icon = '✅' if (sim or 0) >= 97 else ('⚠️ ' if (sim or 0) >= 90 else '❌')
        if sim is None:
            icon = '──'
        print(f'  {icon} {e.get("name", "unknown"):30s}  {sim_str}')
    print('─' * 46)


if __name__ == '__main__':
    main()
