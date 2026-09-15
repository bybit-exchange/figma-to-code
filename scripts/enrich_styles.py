#!/usr/bin/env python3
"""
enrich_styles.py — Build a complete styleId → design token mapping for Figma files.

Color Styles from remote Figma libraries cannot be resolved via the public REST API.
This script enriches the pipeline by calling get_variable_defs (Figma MCP) per node to
get style names, then maps them to design tokens via var_token_map.json.

The result is cached as {nodeId}-{fileKey}-style-names.json and used by convert.py's
styles_thread for P0b lookups — no MCP calls needed at conversion time.

Usage (must run inside Claude Code session with Figma MCP available):
  # Step 1: Analyze raw-data to find unresolved style IDs
  python3 scripts/enrich_styles.py --analyze [raw-data-dir]

  # Step 2: Claude Code calls get_variable_defs for each entry in the analysis output,
  #          then feeds results back:
  python3 scripts/enrich_styles.py --merge /tmp/enrich_results.json [raw-data-dir]

  # Step 3: Verify coverage
  python3 scripts/enrich_styles.py --report [raw-data-dir]

Architecture:
  Raw node data: node.styles.fill = "2:317"
    → {fileKey}-style-names.json: {"2:317": "Gray [T]/T1_Title"}
    → var_token_map.json: {"--gray-t1-title": "--color-text-primary"}
    → CSS output: color: var(--color-text-primary)
"""

import sys
import json
import re
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.figma_vars import normalize_var_name, load_var_token_map, get_style_cache_path
from lib.paths import RAW_DATA_DIR

# Page to fileKey mapping (update when adding new pages)
PAGE_FILEKEYS = {
    '2573-27801': '29VA6x1XgSPCSqhhheVUM6',
    '169-33787':  '5hamPntIzfLiCoFbPokZVq',
    '235-14237':  '5hamPntIzfLiCoFbPokZVq',
    '39641-6863': '0BZEdeiejU0C8wEtrgsRwC',
    '39603-19204':'0BZEdeiejU0C8wEtrgsRwC',
    '286-66610':  '9co762ElxNGFH0zPh83bkx',
    '202-33464':  'ybajbfX956lHs9XHdkhhiQ',
    '1484-27139': 'ybajbfX956lHs9XHdkhhiQ',
    '4030-41365': '5Sth3zm7Qn4LvWIGze1xvO',
    '4030-39510': '5Sth3zm7Qn4LvWIGze1xvO',
}


def _is_color(val: str) -> bool:
    if not val or not isinstance(val, str):
        return False
    if val.startswith('Font(') or val.startswith('Effect('):
        return False
    if val in ('true', 'false') or re.match(r'^\d+(\.\d+)?$', val):
        return False
    return bool(re.match(r'^#[0-9a-fA-F]{3,8}$', val))


def cmd_analyze(raw_data_dir: Path):
    """Scan all raw-data files and find unresolved styleIds needing enrichment."""
    print(f'\n🔍  分析 {raw_data_dir} 中的未解析 Style ID...\n')

    # Load existing style-names caches
    existing: dict = {}  # {fileKey: {styleId: name}}
    for f in raw_data_dir.glob('*-style-names.json'):
        stem = f.stem.replace('-style-names', '')
        fk = stem.split('-', 1)[-1] if '-' in stem else stem
        try:
            d = json.loads(f.read_text())
            existing[fk] = d
        except Exception:
            pass

    # Find unique (fileKey, styleId, best_nodeId, nodeType, fillHex)
    unresolved: dict = {}  # key = "fileKey:styleId"
    for node_prefix, file_key in PAGE_FILEKEYS.items():
        rf = raw_data_dir / f'{node_prefix}-raw-data.json'
        if not rf.exists():
            continue
        data = json.loads(rf.read_text())

        # Already cached?
        cached_for_file = existing.get(file_key, {})

        def walk(node):
            style_fill = (node.get('styles') or {}).get('fill', '')
            if style_fill and style_fill not in cached_for_file:
                for fill in (node.get('fills') or []):
                    if fill.get('boundVariables'):
                        continue  # already has variable binding
                    c = fill.get('color', {})
                    r, g, b = (int(c.get(k, 0) * 255) for k in 'rgb')
                    hex_val = f'#{r:02x}{g:02x}{b:02x}'
                    key = f'{file_key}:{style_fill}'
                    node_type = node.get('type', '')
                    node_id = node.get('id', '')
                    # Prefer simple nodeIds (123:456) over compound instance paths (I..;..)
                    # get_variable_defs only accepts simple nodeIds
                    is_simple = bool(__import__('re').match(r'^\d+:\d+$', node_id))
                    type_rank = {'TEXT': 0, 'FRAME': 1, 'RECTANGLE': 2}.get(node_type, 3)
                    # Simple nodeId beats compound; within same simplicity, prefer TEXT
                    existing_entry = unresolved.get(key)
                    existing_id = (existing_entry or {}).get('node_id', '')
                    existing_simple = bool(__import__('re').match(r'^\d+:\d+$', existing_id))
                    existing_type_rank = {'TEXT': 0, 'FRAME': 1, 'RECTANGLE': 2}.get(
                        (existing_entry or {}).get('node_type', ''), 3
                    )
                    # Rank: (compound, type) vs (simple, type) — simple always beats compound
                    new_rank = (0 if is_simple else 1, type_rank)
                    old_rank = (0 if existing_simple else 1, existing_type_rank)
                    if not existing_entry or new_rank < old_rank:
                        unresolved[key] = {
                            'file_key': file_key,
                            'style_id': style_fill,
                            'node_id': node_id,
                            'node_type': node_type,
                            'fill_hex': hex_val,
                            'node_id_simple': is_simple,
                        }
            for ch in (node.get('children') or []):
                walk(ch)

        for ndata in (data.get('nodes') or {}).values():
            walk(ndata.get('document') or {})

    if not unresolved:
        print('✅  无未解析的 Style ID（已全部缓存）')
        return

    output_path = Path('/tmp/styles_to_enrich.json')
    with open(output_path, 'w') as f:
        json.dump(unresolved, f, indent=2, ensure_ascii=False)

    # Group by fileKey for display
    by_file: dict = {}
    for key, info in unresolved.items():
        fk = info['file_key']
        by_file.setdefault(fk, []).append(info)

    print(f'📋  需要富化的 Style ID: {len(unresolved)} 个\n')
    for fk, entries in sorted(by_file.items(), key=lambda x: -len(x[1])):
        text_count = sum(1 for e in entries if e['node_type'] == 'TEXT')
        print(f'  {fk[:8]}...  {len(entries):>4} 个  (TEXT: {text_count})')

    print(f'\n分析结果已保存: {output_path}')
    print('\n下一步 — 在 Claude Code 中运行:')
    print('  读取 /tmp/styles_to_enrich.json，对每条 entry 调用:')
    print('  get_variable_defs(fileKey, nodeId)')
    print('  然后运行: python3 scripts/enrich_styles.py --merge /tmp/enrich_results.json')


def cmd_merge(results_path: Path, raw_data_dir: Path):
    """Merge get_variable_defs results into the GLOBAL var_id_token_direct.json.

    Results are stored in the SKILL directory (not project directory) so all
    projects that process the same Figma files benefit without re-running.

    results_path: JSON file mapping "fileKey:styleId" → style_name
      e.g. {"FILE_KEY:2:317": "Gray [T]/T1_Title", ...}
    """
    if not results_path.exists():
        print(f'❌  找不到结果文件: {results_path}')
        sys.exit(1)

    results: dict = json.loads(results_path.read_text())
    var_token_map = load_var_token_map()

    # Load the GLOBAL direct-mapping dict (skill-level, not project-level)
    direct_map_path = Path(__file__).parent / 'lib' / 'var_id_token_direct.json'
    try:
        direct_map: dict = json.loads(direct_map_path.read_text())
    except Exception:
        direct_map = {}

    new_css_names: set = set()
    total_mapped = 0
    total_unmapped = 0

    for key, style_name in results.items():
        parts = key.split(':', 1)
        if len(parts) != 2:
            continue
        fk, style_id = parts
        css_name = normalize_var_name(style_name)
        token_val = var_token_map.get(css_name)
        direct_key = f'STYLE:{fk}:{style_id}'

        if token_val:
            direct_map[direct_key] = token_val
            total_mapped += 1
        else:
            new_css_names.add(f'{css_name}  (from "{style_name}")')
            total_unmapped += 1

    # Write global dict back to skill directory
    direct_map_path.write_text(json.dumps(direct_map, indent=2, ensure_ascii=False))
    print(f'✅  写入全局字典: {direct_map_path}')
    print(f'   新增 {total_mapped} 条  |  映射成功 {total_mapped}  |  待补充 {total_unmapped}')

    if new_css_names:
        print('\n以下 CSS 名称需要添加到 var_token_map.json（然后重新 --merge）:')
        for name in sorted(new_css_names):
            print(f'  "{name.split("  ")[0]}": "???",')

    print(f'\n💡  此字典存储在 skill 目录，所有项目共用，无需重复富化')


def cmd_report(raw_data_dir: Path):
    """Show coverage report for all cached style-names."""
    print(f'\n📊  Style ID 缓存覆盖率报告\n')
    for node_prefix, file_key in sorted(PAGE_FILEKEYS.items()):
        cache = get_style_cache_path(raw_data_dir, file_key, '')
        if cache.exists():
            d = json.loads(cache.read_text())
            data = d.get('data', {}) if d.get('_status') == 'ok' else {}
            print(f'  {node_prefix:<15} {cache.name:<45} {len(data):>4} 条')
        else:
            print(f'  {node_prefix:<15} (无缓存)')


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    raw_data_dir = Path(args[-1]) if len(args) > 1 and not args[-1].startswith('--') else RAW_DATA_DIR

    if cmd == '--analyze':
        cmd_analyze(raw_data_dir)
    elif cmd == '--merge':
        results_path = Path(args[1]) if len(args) > 1 else Path('/tmp/enrich_results.json')
        cmd_merge(results_path, raw_data_dir)
    elif cmd == '--report':
        cmd_report(raw_data_dir)
    else:
        print(f'未知命令: {cmd}')
        print('用法: enrich_styles.py --analyze | --merge <results.json> | --report')
        sys.exit(1)


if __name__ == '__main__':
    main()
