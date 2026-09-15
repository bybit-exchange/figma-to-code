#!/usr/bin/env python3
"""
h5_overlay.py — 为既有手写 PC 代码补充 H5 响应式覆盖层。

用法：
  python3 h5_overlay.py --h5-url='<Figma URL>' \
    --existing-page=src/pages/EuDepositIncentiveLp --css-ext=less

产物原则：只追加，不修改原有文件内容。
回滚：删除所有 *.h5.module.less 及 @import 行。
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.node_matcher import extract_leaf_texts, match_score, get_top_sections
from lib.paths import ir_file, COMPONENTS_SUBDIR


def append_h5_import(less_path: Path) -> None:
    """在 less_path 末尾追加 @import './index.h5.module.less'（幂等）。"""
    import_line = "@import './index.h5.module.less';"
    content = less_path.read_text(encoding='utf-8')
    if import_line in content:
        return
    with open(less_path, 'a', encoding='utf-8') as f:
        if not content.endswith('\n'):
            f.write('\n')
        f.write(f'\n{import_line}\n')


def write_css_overlay(
    comp_dir: Path,
    h5_css: dict,
    breakpoint: int = 768,
    css_ext: str = 'less',
) -> None:
    """
    写入 index.h5.module.less（纯 @media 规则）并在 index.module.less 追加 @import。
    不修改 index.tsx 或 index.module.less 的原有内容。
    """
    if not h5_css:
        return

    css_lines = '\n'.join(f'    {k}: {v};' for k, v in h5_css.items())
    # 从 index.module.less 取根 class 名（第一行 .xxx {）
    less_path = comp_dir / f'index.module.{css_ext}'
    root_cls = 'root'
    if less_path.exists():
        for line in less_path.read_text().splitlines():
            line = line.strip()
            if line.startswith('.') and line.endswith('{'):
                root_cls = line[1:-1].strip()
                break

    overlay_content = (
        f'@media (max-width: {breakpoint}px) {{\n'
        f'  .{root_cls} {{\n'
        f'{css_lines}\n'
        f'  }}\n'
        f'}}\n'
    )
    overlay_path = comp_dir / f'index.h5.module.{css_ext}'
    overlay_path.write_text(overlay_content, encoding='utf-8')

    if less_path.exists():
        append_h5_import(less_path)


def match_h5_to_components(
    h5_sections: list[dict],
    component_names: list[str],
    component_texts: dict[str, set[str]],
    threshold: float = 0.30,
) -> list[str | None]:
    """
    将 H5 sections 按文本相似度匹配到既有组件名。
    返回与 h5_sections 等长的列表，无匹配时为 None。
    """
    results: list[str | None] = []
    used: set[str] = set()

    for section in h5_sections:
        h5_texts = extract_leaf_texts(section)
        best_name: str | None = None
        best_score = threshold

        for name in component_names:
            if name in used:
                continue
            comp_texts = component_texts.get(name, set())
            if not h5_texts and not comp_texts:
                score = 0.5
            elif not h5_texts or not comp_texts:
                score = 0.0
            else:
                inter = len(h5_texts & comp_texts)
                union = len(h5_texts | comp_texts)
                score = inter / union if union else 0.0

            if score > best_score:
                best_score = score
                best_name = name

        if best_name:
            used.add(best_name)
        results.append(best_name)

    return results


def _scan_component_texts(page_dir: Path) -> dict[str, set[str]]:
    """扫描 page_dir/components/ 下各组件 tsx 文件，提取文本内容用于匹配。"""
    comp_texts: dict[str, set[str]] = {}
    comp_root = page_dir / COMPONENTS_SUBDIR
    if not comp_root.exists():
        return comp_texts
    for tsx in comp_root.rglob('*.tsx'):
        comp_name = tsx.parent.name
        # 简单提取引号内字符串作为文本指纹
        import re
        texts = set(re.findall(r'"([^"]{4,})"', tsx.read_text()))
        comp_texts.setdefault(comp_name, set()).update(texts)
    return comp_texts


def run(h5_url: str, existing_page: str, css_ext: str, breakpoint: int, force_split: bool = False) -> None:
    page_dir = Path(existing_page)
    if not page_dir.exists():
        print(f'❌  页面目录不存在: {page_dir}')
        sys.exit(1)

    # 1. 拉取 H5 IR
    print('⟳  拉取 H5 IR ...')
    convert = Path(__file__).parent / 'convert.py'
    result = subprocess.run([sys.executable, str(convert), h5_url, f'--css-ext={css_ext}'])
    if result.returncode != 0:
        print('❌  convert.py 失败')
        sys.exit(1)

    import re
    node_id_m = re.search(r'node-id=([0-9]+-[0-9]+)', h5_url)
    if not node_id_m:
        node_id_m = re.search(r'node-id=([0-9]%3A[0-9]+)', h5_url)
    node_id_safe = node_id_m.group(1).replace('%3A', '-').replace(':', '-') if node_id_m else ''
    ir_path = ir_file(node_id_safe)
    if not ir_path.exists():
        print(f'❌  IR 不存在: {ir_path}')
        sys.exit(1)

    h5_ir = json.loads(ir_path.read_text())
    h5_sections = get_top_sections(h5_ir)

    # 2. 扫描既有组件
    comp_dir = page_dir / COMPONENTS_SUBDIR
    comp_names = [d.name for d in comp_dir.iterdir() if d.is_dir()] if comp_dir.exists() else []
    comp_texts = _scan_component_texts(page_dir)

    # 3. 匹配
    matches = match_h5_to_components(h5_sections, comp_names, comp_texts)

    # 4. 生成覆盖层
    for h5_sec, comp_name in zip(h5_sections, matches):
        if not comp_name:
            print(f'⚠️  未匹配到组件: {h5_sec.get("figmaName")}，跳过')
            continue

        target_comp_dir = comp_dir / comp_name
        if not force_split:
            write_css_overlay(target_comp_dir, h5_sec.get('css') or {}, breakpoint=breakpoint, css_ext=css_ext)
            print(f'✓  CSS overlay: {comp_name}/index.h5.module.{css_ext}')
        else:
            from lib.tsx_generator import generate_tsx
            h5_tsx = generate_tsx(h5_sec, css_ext=css_ext)
            (target_comp_dir / f'{comp_name}H5.tsx').write_text(h5_tsx)
            print(f'✓  H5 component: {comp_name}/{comp_name}H5.tsx')

    print('\n完成。回滚方法: 删除所有 *.h5.module.less 及对应 @import 行。')


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Add H5 overlay to existing PC page code')
    p.add_argument('--h5-url', required=True)
    p.add_argument('--existing-page', required=True)
    p.add_argument('--css-ext', default='less')
    p.add_argument('--breakpoint', type=int, default=768)
    p.add_argument('--force-split', action='store_true', default=False,
                   help='Generate ComponentH5.tsx instead of CSS overlay')
    return p.parse_args()


if __name__ == '__main__':
    args = _parse_args()
    run(args.h5_url, args.existing_page, args.css_ext, args.breakpoint, force_split=args.force_split)
