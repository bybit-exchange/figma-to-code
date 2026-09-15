"""
auto_rename.py — 启发式 CSS 类名自动重命名

从 plan.naming.json 的条目（frame-* / node-*）生成语义化 kebab-case 名称，
无需 Claude 交互。命名规则（优先级从高到低）：

1. firstTexts[0]：最短的第一条文本，截取前 3 个词
2. figmaName：非 "Frame N" / "Group N" 的有意义名称
3. CSS 布局推断：flex-direction + width 组合出 row/col/full-row 等
4. 兜底：截取 node-id 后几位 + 序号

用法：
  python3 scripts/lib/auto_rename.py --node-id=42-100
  python3 scripts/lib/auto_rename.py --all   # 处理所有 plan.naming.json
"""
from __future__ import annotations

import re
import sys
import json
import glob
import argparse
from pathlib import Path

# ── 词组清理工具 ──────────────────────────────────────────────────────────────

_STOP = {
    'the', 'a', 'an', 'and', 'or', 'of', 'in', 'on', 'at', 'to', 'for',
    'is', 'are', 'be', 'by', 'with', 'from', 'this', 'that',
}
_GENERIC_FRAME = re.compile(r'^(?:Frame|Group|Rectangle|Ellipse|Vector)\s*\d*$', re.IGNORECASE)


def _to_kebab(s: str, max_words: int = 4) -> str:
    """将任意字符串转换为 kebab-case，最多 max_words 个词。"""
    # 非 ASCII → 直接跳过（中文等不能转 kebab，走下一优先级）
    if not s.isascii():
        return ''
    s = re.sub(r'[^\w\s-]', '', s)  # 去除特殊字符
    s = re.sub(r'[-_\s]+', ' ', s).strip().lower()
    words = [w for w in s.split() if w and w not in _STOP][:max_words]
    return '-'.join(words) if words else ''


def _from_texts(texts: list) -> str:
    """从 firstTexts 推断名称：取最短且有意义的文本片段。"""
    best = ''
    for t in texts[:3]:
        t = str(t).strip()
        if not t or not t.isascii():
            continue
        k = _to_kebab(t, max_words=3)
        if k and (not best or len(k) < len(best)):
            best = k
    return best[:30]


def _from_figma_name(name: str) -> str:
    """从 figmaName 推断名称，跳过 Frame/Group/Rectangle + 纯数字。"""
    if _GENERIC_FRAME.match(name):
        return ''
    return _to_kebab(name, max_words=3)[:30]


def _from_css(css: dict) -> str:
    """从 CSS 布局属性推断容器角色。"""
    direction = css.get('flex-direction', '')
    is_full_width = css.get('width') == '100%'
    has_height = css.get('height') not in (None, '', '100%')

    if direction == 'row':
        return 'full-row' if is_full_width else 'row'
    elif direction == 'column':
        return 'full-col' if is_full_width else 'col'
    elif is_full_width:
        return 'full-wrap'
    elif has_height:
        return 'block'
    return 'wrap'


def _infer_name(entry: dict, existing: set, counters: dict) -> str:
    """
    为单个 naming entry 推断语义名称，保证在 existing 集合内唯一。
    """
    texts = entry.get('firstTexts', [])
    figma_name = entry.get('figmaName', '')
    css = entry.get('css', {})
    current_cls = entry.get('currentClass', '')

    # 特殊：figmaId 含 "路径" 文本 / SVG path 节点 → 统一 icon-svg-path
    if figma_name in ('路径', '|', '/') or (
        current_cls.startswith('node-I') and figma_name == '路径'
    ):
        base = 'icon-svg-path'
    else:
        base = (_from_texts(texts)
                or _from_figma_name(figma_name)
                or _from_css(css))

    if not base:
        base = 'wrap'

    # 确保唯一：第一次不加后缀，之后加 -2, -3, ...
    name = base
    if name in existing:
        n = counters.get(base, 1) + 1
        counters[base] = n
        name = f'{base}-{n}'
    else:
        counters[base] = 1

    existing.add(name)
    return name


# ── 主流程 ────────────────────────────────────────────────────────────────────

def process_naming_file(naming_path: Path, dry_run: bool = False) -> int:
    """
    读取 plan.naming.json，为 frame-* / node-* 类生成 renames.json。
    返回写入的条目数。
    """
    naming = json.loads(naming_path.read_text())
    entries = naming.get('cssClasses', [])

    # 去重：同一 currentClass 取 firstTexts 最丰富的一条
    seen: dict = {}
    for e in entries:
        k = e['currentClass']
        if k not in seen or len(e.get('firstTexts', [])) > len(seen[k].get('firstTexts', [])):
            seen[k] = e

    candidates = [v for k, v in seen.items() if k.startswith(('frame-', 'node-'))]

    renames_path = naming_path.parent / 'renames.json'
    renames = json.loads(renames_path.read_text()) if renames_path.exists() else {}
    renames.setdefault('cssClasses', {})

    # 已有名称不覆盖
    existing_targets = set(renames['cssClasses'].values())
    counters: dict = {}
    batch_map: dict = {}

    for entry in candidates:
        cls = entry['currentClass']
        if cls in renames['cssClasses']:
            continue  # 已有映射，跳过
        name = _infer_name(entry, set(existing_targets) | set(batch_map.values()), counters)
        batch_map[cls] = name

    if batch_map:
        renames['cssClasses'].update(batch_map)
        if not dry_run:
            renames_path.write_text(json.dumps(renames, ensure_ascii=False, indent=2))

    return len(batch_map)


def main() -> None:
    parser = argparse.ArgumentParser(description='启发式 CSS 类名自动重命名')
    parser.add_argument('--node-id', help='指定 node-id（如 42-100）')
    parser.add_argument('--all', action='store_true', help='处理所有 plan.naming.json')
    parser.add_argument('--dry-run', action='store_true', help='只输出，不写文件')
    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent.parent / '.figma-to-code' / '3-page-code'
    if not base_dir.exists():
        # 相对路径兜底（从 skill 根运行时）
        base_dir = Path('.figma-to-code') / '3-page-code'

    if args.node_id:
        pattern = str(base_dir / f'*{args.node_id}*/split-a/plan.naming.json')
    elif args.all:
        pattern = str(base_dir / '*/split-a/plan.naming.json')
    else:
        parser.print_help()
        sys.exit(1)

    files = sorted(glob.glob(pattern))
    if not files:
        print(f'未找到 plan.naming.json（pattern: {pattern}）')
        sys.exit(1)

    total = 0
    for f in files:
        path = Path(f)
        n = process_naming_file(path, dry_run=args.dry_run)
        label = path.parts[-3]
        action = '(dry-run)' if args.dry_run else ''
        print(f'✓  {label}: 新增 {n} 条 {action}')
        total += n

    print(f'\n共新增 {total} 条映射')


if __name__ == '__main__':
    main()
