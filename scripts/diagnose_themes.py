#!/usr/bin/env python3
"""
diagnose_themes.py — 诊断各设计稿的主题倾向（light/dark/mixed）

用法（在 trade-option 目录下运行）：
  python3 /path/to/scripts/diagnose_themes.py
"""
import json, re, sys, urllib.request
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))
from lib.token_resolver import _parse_css, _normalize_hex, _COLOR_PROPS, diagnose_design_theme
from lib.paths import DESIGN_CSS_PATH, IR_DIR

import os
_TOKEN_CSS_URL = os.environ.get("DESIGN_TOKEN_CSS_URL", "")

def fetch_design_css() -> str:
    if not _TOKEN_CSS_URL:
        return ""
    cache = DESIGN_CSS_PATH
    if cache.exists():
        return cache.read_text(encoding='utf-8')
    try:
        print("  拉取 token CSS...")
        with urllib.request.urlopen(_TOKEN_CSS_URL, timeout=15) as r:
            text = r.read().decode('utf-8')
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding='utf-8')
        return text
    except Exception as e:
        print(f"  ⚠️  拉取失败: {e}")
        return ""


def scope_to_theme(scope: str) -> str:
    """Map a scope string to canonical 'dark' / 'light' / 'other'."""
    if scope == 'dark' or scope.endswith('-dark'):
        return 'dark'
    if scope in ('root', 'other'):
        return scope
    if scope.startswith('site-'):
        return 'light'  # site-specific light variants
    return 'other'


def node_theme_votes(node: dict, color_themes: dict) -> Counter:
    """递归统计节点及其子树中颜色值的主题投票。"""
    votes = Counter()
    css = node.get("css") or {}
    for prop, val in css.items():
        if prop not in _COLOR_PROPS or not isinstance(val, str):
            continue
        stripped = val.strip()
        if stripped.startswith('#'):
            key = _normalize_hex(stripped)
        elif stripped.lower().startswith('rgb'):
            key = stripped
        else:
            continue
        scopes = color_themes.get(key)
        if not scopes:
            continue
        # Pick the most specific scope and map to canonical theme
        for s in ("dark",) + tuple(s for s in scopes if s.endswith('-dark')) + \
                 ("light",) + tuple(s for s in scopes if s.startswith('site-') and not s.endswith('-dark')) + \
                 ("root", "other"):
            if s in scopes:
                votes[scope_to_theme(s)] += 1
                break
    for child in node.get("children", []):
        votes.update(node_theme_votes(child, color_themes))
    return votes


def find_mixed_nodes(node: dict, color_themes: dict, results: list, depth: int = 0):
    """找出自身颜色主题与页面主要主题相反的节点（混色节点）。"""
    css = node.get("css") or {}
    node_votes = Counter()
    for prop, val in css.items():
        if prop not in _COLOR_PROPS or not isinstance(val, str):
            continue
        stripped = val.strip()
        key = _normalize_hex(stripped) if stripped.startswith('#') else stripped
        scopes = color_themes.get(key)
        if not scopes:
            continue
        for s in ("dark",) + tuple(s for s in scopes if s.endswith('-dark')) + \
                 tuple(s for s in scopes if s.startswith('site-') and not s.endswith('-dark')) + \
                 ("root", "other"):
            if s in scopes:
                node_votes[scope_to_theme(s)] += 1
                break
    if node_votes:
        results.append({
            "figmaId": node.get("figmaId"),
            "name": node.get("figmaName", "")[:40],
            "votes": dict(node_votes),
        })
    for child in node.get("children", []):
        find_mixed_nodes(child, color_themes, results, depth + 1)


def main():
    ir_dir = IR_DIR
    if not ir_dir.exists():
        print("❌  找不到 .figma-to-code/2-figma-extract，请在 trade-option 目录下运行")
        sys.exit(1)

    css_text = fetch_design_css()
    if not css_text:
        print("❌  无法获取 design token CSS，诊断终止")
        sys.exit(1)

    tokens = _parse_css(css_text)
    color_themes = tokens.get("color_themes", {})

    # 统计 color_themes 中 light/dark 各有多少不同 hex
    lt_count = sum(1 for s in color_themes.values() if 'light' in s)
    dk_count = sum(1 for s in color_themes.values() if 'dark' in s)
    rt_count = sum(1 for s in color_themes.values() if 'root' in s and 'dark' not in s and 'light' not in s)
    print(f"\n📊  design token 颜色词典: light={lt_count}  dark={dk_count}  root_only={rt_count}  total={len(color_themes)}\n")
    print("=" * 60)

    ir_files = sorted(ir_dir.glob("*.ir.json"))
    for ir_path in ir_files:
        ir = json.loads(ir_path.read_text())
        page_votes = node_theme_votes(ir, color_themes)
        total = page_votes['dark'] + page_votes['light'] + page_votes['root']
        if total == 0:
            design_theme, conf = "unknown", 0.0
        elif page_votes['light'] >= page_votes['dark']:
            design_theme = "light"
            conf = round(page_votes['light'] / total, 2) if total else 0
        else:
            design_theme = "dark"
            conf = round(page_votes['dark'] / total, 2) if total else 0

        name = ir.get("figmaName", ir_path.stem)
        bar_l = '█' * page_votes['light']
        bar_d = '░' * page_votes['dark']
        print(f"\n  {ir_path.stem}  ({name})")
        print(f"    设计主题: {design_theme}  置信度: {conf:.0%}")
        print(f"    light={page_votes['light']}  dark={page_votes['dark']}  root={page_votes['root']}  other={page_votes['other']}")
        print(f"    [{bar_l}{bar_d}]")

        # 找混色节点（自身带有与主页面主题不同的颜色）
        opposite = "dark" if design_theme == "light" else "light"
        node_results = []
        find_mixed_nodes(ir, color_themes, node_results)
        mixed = [n for n in node_results if n['votes'].get(opposite, 0) > 0 and n['votes'].get(design_theme, 0) == 0]
        if mixed:
            print(f"    ⚠️  混色节点（只含 {opposite} 颜色）: {len(mixed)} 个")
            for n in mixed[:5]:
                print(f"       {n['figmaId']}  {n['name']}  votes={n['votes']}")
            if len(mixed) > 5:
                print(f"       ... 还有 {len(mixed) - 5} 个")

    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()
