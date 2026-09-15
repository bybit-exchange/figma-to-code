#!/usr/bin/env python3
"""
test_pipeline.py — Pipeline 完整性检查（与 test_all.py 平级）

检查 convert → analyze → apply 各阶段的中间产物是否齐全、内部引用是否一致。
不运行 pipeline，只检查已有产物。需要先运行过 convert.py 和 split_components.py。

用法：
  # 自动检测 .figma-to-code/3-page-code/ 下所有产物目录
  python3 scripts/tests/test_pipeline.py

  # 指定 node-id
  python3 scripts/tests/test_pipeline.py --node-id=42-100

  # 只检查到 Stage 2（跳过 apply 产物）
  python3 scripts/tests/test_pipeline.py --up-to=analyze

覆盖范围：
  Stage 1  convert.py 产物：IR / raw-data / figma-maps / 初始 TSX+CSS
  Stage 2  split --analyze 产物：plan.json 结构完整性
  Stage 3  split --apply 产物：section / leaf 文件与 plan 一致
  Cross    内部引用：TSX import 路径可解析到真实文件
"""
from __future__ import annotations

import sys
import re
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.paths import (
    IR_DIR, RAW_DATA_DIR, PAGE_CODE_DIR,
    SPLIT_A_SUBDIR, STAGE_SUBDIR, HOOKS_SUBDIR, COMPONENTS_SUBDIR,
    INDEX_TS_FILENAME, INDEX_TSX_FILENAME,
    USE_PAGE_ENV_FILENAME,
    ir_file, raw_data_file, semantic_file, code_connect_file,
    texts_ts_filename, texts_defaults_ts_filename,
)

# ── 结果收集 ──────────────────────────────────────────────────────────

_results: list[tuple[str, bool, str]] = []  # (label, passed, detail)


def ok(label: str, detail: str = '') -> None:
    _results.append((label, True, detail))
    print(f'  ✓  {label}' + (f'  ({detail})' if detail else ''))


def fail(label: str, detail: str = '') -> None:
    _results.append((label, False, detail))
    print(f'  ✗  {label}' + (f'\n       {detail}' if detail else ''))


def section(title: str) -> None:
    print(f'\n── {title}')


def _exists(path: Path, label: str) -> bool:
    if path.exists():
        ok(label, path.name)
        return True
    fail(label, f'不存在: {path}')
    return False


def _valid_json(path: Path, label: str) -> dict | list | None:
    if not path.exists():
        fail(label, f'文件不存在: {path}')
        return None
    try:
        data = json.loads(path.read_text())
        ok(label)
        return data
    except json.JSONDecodeError as e:
        fail(label, f'JSON 解析失败: {e}')
        return None


# ── Stage 1：convert.py 产物 ──────────────────────────────────────────

def check_stage1(node_id_safe: str, page_dir: Path, css_ext: str) -> str | None:
    """返回 page_name，或 None 表示严重缺失。"""
    section('Stage 1 — convert.py 产物')

    # IR
    ir_path = ir_file(node_id_safe)
    ir_data = _valid_json(ir_path, 'IR 文件可解析')
    if not ir_data:
        return None

    # raw-data（可选，network-skip 模式不生成）
    raw_path = raw_data_file(node_id_safe)
    if raw_path.exists():
        ok('raw-data.json 存在')
    else:
        print(f'  ○  raw-data.json 不存在（network-skip 模式，可接受）')

    # figma-maps.json
    maps_path = page_dir / 'figma-maps.json'
    maps_data = _valid_json(maps_path, 'figma-maps.json 可解析')
    if maps_data:
        fid_to_class = maps_data.get('fidToClass', {})
        if fid_to_class:
            ok(f'figma-maps.json 含 {len(fid_to_class)} 个 figmaId→class 映射')
        else:
            fail('figma-maps.json fidToClass 为空')

    # 推断 page_name
    tsx_files = list(page_dir.glob('*.tsx'))
    if not tsx_files:
        fail('page_dir 下找不到 .tsx 文件，无法推断 page_name')
        return None
    page_name = tsx_files[0].stem
    ok(f'page_name 推断为: {page_name}')

    # 初始 TSX / CSS
    _exists(page_dir / f'{page_name}.tsx',              f'{page_name}.tsx 存在')
    _exists(page_dir / f'{page_name}.module.{css_ext}', f'{page_name}.module.{css_ext} 存在')
    _exists(page_dir / INDEX_TS_FILENAME,               'index.ts 存在')
    # hooks/usePageEnv.ts 由 split --apply 生成到 stage/hooks/，convert 阶段不存在
    if (page_dir / HOOKS_SUBDIR / USE_PAGE_ENV_FILENAME).exists():
        ok(f'hooks/{USE_PAGE_ENV_FILENAME} 存在（convert 已生成）')
    else:
        print(f'  ○  hooks/{USE_PAGE_ENV_FILENAME} 不存在（由 split --apply 生成到 stage/hooks/，可接受）')

    # semantic（可选）
    sem_path = semantic_file(node_id_safe)
    if sem_path.exists():
        ok('semantic.json 存在')
    else:
        print('  ○  semantic.json 不存在（无语义分析数据，可接受）')

    return page_name


# ── Stage 2：split --analyze 产物 ────────────────────────────────────

def check_stage2(page_dir: Path) -> dict | None:
    """返回 plan dict，或 None 表示严重缺失。"""
    section('Stage 2 — split --analyze 产物')

    plan_path = page_dir / SPLIT_A_SUBDIR / 'plan.json'
    plan = _valid_json(plan_path, 'plan.json 可解析')
    if not plan:
        return None

    # 必须字段
    for field in ('pageComponent', 'nodeId', 'sections'):
        if field in plan:
            ok(f'plan.json 含字段: {field}')
        else:
            fail(f'plan.json 缺少字段: {field}')

    sections = plan.get('sections', [])
    leaves_total = sum(len(s.get('leafComponents', [])) for s in sections)
    ok(f'plan 包含 {len(sections)} 个 section，{leaves_total} 个 leaf')

    naming_path = page_dir / SPLIT_A_SUBDIR / 'plan.naming.json'
    _exists(naming_path, 'plan.naming.json 存在')

    return plan


# ── Stage 3：split --apply 产物 ───────────────────────────────────────

def check_stage3(page_dir: Path, plan: dict, css_ext: str) -> Path | None:
    """返回 stage_dir，或 None 表示严重缺失。"""
    section('Stage 3 — split --apply 产物')

    stage_dir = page_dir / SPLIT_A_SUBDIR / STAGE_SUBDIR
    if not stage_dir.exists():
        fail('stage/ 目录不存在', '请先运行: split_components.py --apply')
        return None
    ok('stage/ 目录存在')

    # Page.tsx / CSS
    _exists(stage_dir / INDEX_TSX_FILENAME,              f'{INDEX_TSX_FILENAME} 存在')
    page_name = plan.get('pageComponent', '')
    _exists(stage_dir / f'{page_name}.module.{css_ext}', f'{page_name}.module.{css_ext} 存在')
    # stage/ 无 index.ts（入口在 page_dir 根）；hooks/usePageEnv.ts 由 split 生成到 stage/hooks/
    _exists(stage_dir / HOOKS_SUBDIR / USE_PAGE_ENV_FILENAME, f'stage/hooks/{USE_PAGE_ENV_FILENAME} 存在')

    # i18n texts（如果 plan 含 i18nTokens）
    if plan.get('i18nTokens'):
        _exists(stage_dir / texts_ts_filename(page_name),         f'{page_name}.texts.ts 存在')
        _exists(stage_dir / texts_defaults_ts_filename(page_name), f'{page_name}.texts.defaults.ts 存在')

    # 每个 section 目录 + 入口文件（leaf 文件细节由 validate_split 负责）
    for s in plan.get('sections', []):
        s_name = s['name']
        s_dir = stage_dir / COMPONENTS_SUBDIR / s_name
        _exists(s_dir / INDEX_TSX_FILENAME,           f'components/{s_name}/index.tsx 存在')
        _exists(s_dir / f'index.module.{css_ext}',    f'components/{s_name}/index.module.{css_ext} 存在')

    return stage_dir


# ── Cross：TSX import 路径内部一致性 ─────────────────────────────────

_IMPORT_RE = re.compile(r"""from\s+['"](\./[^'"]+|\.{1,2}/[^'"]+)['"]""")


def check_cross_references(stage_dir: Path) -> None:
    section('Cross — TSX import 路径可解析性')

    tsx_files = list(stage_dir.rglob('*.tsx'))
    if not tsx_files:
        fail('stage/ 下未找到任何 .tsx 文件')
        return

    broken: list[str] = []
    staging_pattern: list[str] = []  # 暂存区路径差异（./components/X → ./X），非真正错误
    checked = 0
    for tsx in tsx_files:
        content = tsx.read_text(encoding='utf-8', errors='replace')
        for m in _IMPORT_RE.finditer(content):
            raw = m.group(1)
            resolved = False
            for ext in ('', '.tsx', '.ts', '/index.tsx', '/index.ts'):
                candidate = (tsx.parent / (raw + ext)).resolve()
                if candidate.exists():
                    checked += 1
                    resolved = True
                    break
            if not resolved:
                # 暂存区特殊情况1：Page.tsx 里 import './components/Section' 但 stage/ 是 './Section'
                stripped = raw.replace('./components/', './')
                for ext in ('', '.tsx', '.ts', '/index.tsx', '/index.ts'):
                    candidate = (tsx.parent / (stripped + ext)).resolve()
                    if candidate.exists():
                        staging_pattern.append(f'{tsx.relative_to(stage_dir)}: {raw} → 暂存路径: {stripped}')
                        resolved = True
                        break
            if not resolved:
                # 暂存区特殊情况2：section leaf 里 ../../hooks/usePageEnv 应为 ../hooks/usePageEnv
                # split --apply 生成的相对深度多一级，--dest 落地后路径正确
                fixed = raw.replace('../../hooks/', '../hooks/')
                if fixed != raw:
                    for ext in ('', '.tsx', '.ts', '/index.tsx', '/index.ts'):
                        candidate = (tsx.parent / (fixed + ext)).resolve()
                        if candidate.exists():
                            staging_pattern.append(
                                f'{tsx.relative_to(stage_dir)}: {raw} → 暂存路径: {fixed}'
                            )
                            resolved = True
                            break
            if not resolved:
                broken.append(f'{tsx.relative_to(stage_dir)}: {raw}')

    if staging_pattern:
        print(f'  ○  {len(staging_pattern)} 条 import 使用落地路径（./components/X），暂存区为 ./X，--apply --dest 后可解析')
    if broken:
        for b in broken[:10]:
            fail(f'import 无法解析: {b}')
        if len(broken) > 10:
            fail(f'... 还有 {len(broken) - 10} 条未显示')
    else:
        ok(f'所有相对 import 均可解析（共检查 {checked} 条，暂存路径差异 {len(staging_pattern)} 条）')


# ── 主流程 ────────────────────────────────────────────────────────────

def _auto_detect_node_ids() -> list[str]:
    """从 IR_DIR 下找所有 .ir.json 推断 node_id_safe 列表。"""
    if not IR_DIR.exists():
        return []
    return [p.stem.replace('.ir', '').replace('ir', '')
            for p in IR_DIR.glob('*.ir.json')]


def _find_page_dir(node_id_safe: str) -> Path | None:
    if not PAGE_CODE_DIR.exists():
        return None
    # 匹配 {name}-{id} 或 {id}-{name} 两种格式
    candidates = (
        [d for d in PAGE_CODE_DIR.glob(f'*-{node_id_safe}') if d.is_dir()] +
        [d for d in PAGE_CODE_DIR.glob(f'{node_id_safe}-*') if d.is_dir()]
    )
    return candidates[0] if candidates else None


def run_checks(node_id_safe: str, css_ext: str, up_to: str) -> bool:
    print(f'\n{"╔" + "═" * 44 + "╗"}')
    print(f'║  Pipeline 完整性检查: {node_id_safe:<22} ║')
    print(f'{"╚" + "═" * 44 + "╝"}')

    page_dir = _find_page_dir(node_id_safe)
    if not page_dir:
        fail('找不到 page_dir', f'PAGE_CODE_DIR={PAGE_CODE_DIR}，node_id={node_id_safe}')
        return False
    print(f'\n  page_dir: {page_dir}')

    # Stage 1
    page_name = check_stage1(node_id_safe, page_dir, css_ext)

    if up_to == 'convert':
        return _summary()

    # Stage 2
    plan = check_stage2(page_dir) if page_name else None

    if up_to == 'analyze' or not plan:
        return _summary()

    # Stage 3
    stage_dir = check_stage3(page_dir, plan, css_ext)

    if up_to == 'apply' or not stage_dir:
        return _summary()

    # Cross-reference
    check_cross_references(stage_dir)

    return _summary()


def _summary() -> bool:
    total = len(_results)
    passed = sum(1 for _, p, _ in _results if p)
    failed = total - passed
    all_pass = failed == 0

    print(f'\n{"═" * 46}')
    print('  Pipeline 完整性汇总')
    print('═' * 46)
    status_str = '✅ 全部通过' if all_pass else f'❌ {failed} 项失败'
    print(f'  {status_str}  ({passed}/{total} checks)')
    if not all_pass:
        print()
        for label, passed_, detail in _results:
            if not passed_:
                print(f'  ✗  {label}')
                if detail:
                    print(f'       {detail}')
    print('═' * 46)
    return all_pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description='figma-to-code pipeline 中间产物完整性检查',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--node-id', help='指定 node-id（如 42-100），默认自动检测')
    parser.add_argument('--css-ext', default='less', help='样式文件后缀（默认: less）')
    parser.add_argument(
        '--up-to', choices=['convert', 'analyze', 'apply', 'all'],
        default='all', help='检查到哪个阶段（默认: all）',
    )
    args = parser.parse_args()

    node_ids = [args.node_id] if args.node_id else _auto_detect_node_ids()

    if not node_ids:
        print('\n❌  未找到任何产物，请先运行 convert.py：')
        print("    python3 scripts/convert.py 'https://www.figma.com/design/...'")
        sys.exit(2)

    all_pass = True
    for nid in node_ids:
        _results.clear()
        if not run_checks(nid, args.css_ext, args.up_to):
            all_pass = False

    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
