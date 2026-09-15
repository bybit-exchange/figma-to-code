#!/usr/bin/env python3
from __future__ import annotations
"""
validate_split.py — P4 拆分产物校验（必须门禁）

拆分后调用此脚本，确保 components/ 与原 IR 的 CSS 值一致、结构完整。

层次：
  Split Layer 0: 文件完整性（必要文件全部存在）
  Split Layer 1: CSS 漂移检测（对比 ir.json，> 1px = error）
  Split Layer 2: 类名覆盖检查（IR 中所有类名在某个组件 CSS 里出现）
  Split Layer 3: Less/SCSS 编译检查

评分：100 - 扣分，≥ 90 = pass，< 90 必须修复。

用法:
  python3 scripts/validate_split.py '<Figma URL>'
  python3 scripts/validate_split.py --node-id=42-100 [--css-ext=less]
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))
from lib.scss_generator import _collect_classes
from lib.normalize_classes import normalize_class_names_in_place
from lib.paths import (
    BASE_DIR, IR_DIR, PAGE_CODE_DIR,
    ir_file, PLAN_SUBPATH, COMPONENTS_SUBDIR, PAGES_SRC_DIR,
    SPLIT_A_SUBDIR, SPLIT_B_SUBDIR, STAGE_SUBDIR,
    INDEX_TS_FILENAME, INDEX_TSX_FILENAME, PAGE_TSX_FILENAME,
    to_pascal,
)

# apply_fallback_semantics 在 convert.py 里定义，按需导入
def _apply_semantics(ir: dict) -> dict:
    """
    复现 convert.py 的语义推断流程：
    apply_fallback_semantics → normalize_class_names_in_place
    ir.json 只存了 Script Layer 的原始 IR（无 semantic 字段），
    必须先跑这两步才能得到 className 映射。
    """
    def _fallback(node: dict) -> dict:
        name = re.sub(r'[^a-zA-Z0-9-_]', '', node.get('figmaName', '').replace(' ', '-')).lower()
        return {
            'htmlTag': 'span' if node.get('isTextNode') else 'img' if node.get('isImageNode') else 'div',
            'componentName': to_pascal(name) if node.get('figmaType') == 'COMPONENT' else None,
            'className': name or f'node-{(node.get("figmaId") or "").replace(":", "-")}',
            'isExtractedComponent': node.get('figmaType') == 'COMPONENT',
            'props': [],
        }

    def _walk(node: dict) -> dict:
        return {**node, 'semantic': _fallback(node),
                'children': [_walk(c) for c in node.get('children', [])]}

    resolved = _walk(ir)
    normalize_class_names_in_place(resolved)
    return resolved


# ── 参数解析 ──────────────────────────────────────────────────────────────────

def _parse_args() -> dict:
    raw = sys.argv[1:]
    figma_url = next((a for a in raw if not a.startswith('--')), None)
    flags = {}
    for a in (a for a in raw if a.startswith('--')):
        stripped = a.lstrip('-')
        k, v = (stripped.split('=', 1) if '=' in stripped else (stripped, 'true'))
        flags[k] = v

    node_id_safe = None
    if figma_url:
        node_id_safe = _url_to_node_id(figma_url)
    if not node_id_safe and flags.get('node-id'):
        node_id_safe = flags['node-id'].replace(':', '-')

    return {
        'node_id_safe': node_id_safe,
        'css_ext': flags.get('css-ext', 'less'),
    }


def _url_to_node_id(url: str) -> str | None:
    try:
        u = urlparse(url)
        qs = parse_qs(u.query)
        raw = qs.get('node-id', [''])[0]
        return raw.replace(':', '-') if raw else None
    except Exception:
        return None


# ── CSS 工具 ──────────────────────────────────────────────────────────────────

def _parse_flat_css(text: str) -> dict:
    """
    从 Less/SCSS 平铺输出中提取 {className: {prop: val}}。
    scss_generator.py 生成的是顶层 .class { ... } 块，无嵌套，正则可靠。
    """
    # 去除注释（含 RTL ignore 标记）
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', '', text)
    result = {}
    for m in re.finditer(r'\.([\w-]+)\s*\{([^{}]*)\}', text, re.DOTALL):
        cls = m.group(1)
        body = m.group(2)
        props = {}
        for pm in re.finditer(r'([\w-]+)\s*:\s*([^;]+);', body):
            props[pm.group(1).strip()] = pm.group(2).strip()
        if props and cls not in result:
            result[cls] = props
    return result


def _px(val: str) -> float | None:
    """提取 px 数值，非 px 格式返回 None。"""
    m = re.match(r'^(-?[\d.]+)px$', str(val).strip())
    return float(m.group(1)) if m else None


def _values_match(expected: str, actual: str) -> tuple[bool, str]:
    """
    比较两个 CSS 值。返回 (is_ok, severity)。
    px 值容忍 ≤ 1px，其余精确匹配。
    """
    if str(expected) == str(actual):
        return True, ''
    e_px, a_px = _px(str(expected)), _px(str(actual))
    if e_px is not None and a_px is not None:
        return (True, '') if abs(e_px - a_px) <= 1.0 else (False, 'error')
    return False, 'error'


def _build_ir_css_map(ir: dict) -> dict:
    """从 IR 树提取 {className: cssDict}，复用 scss_generator 的 _collect_classes。"""
    return _collect_classes(ir)


# ── TSX 结构校验（Layer 0 内部工具）────────────────────────────────────────

def _check_tsx(tsx_path: Path, components_dir: Path) -> list:
    """
    对单个 TSX 文件做静态结构检查：
    1. export default 存在
    2. 本地 import 路径可解析（.tsx / .ts / 目录/index.ts）
    3. JSX 中无 {undefined} / {NaN} 值
    """
    issues = []
    try:
        content = tsx_path.read_text()
    except Exception as e:
        return [{'level': 'error', 'msg': f'{tsx_path.name}: 读取失败 — {e}'}]

    label = tsx_path.name

    # 1. export default
    if 'export default' not in content:
        issues.append({'level': 'error', 'msg': f'{label}: 缺少 export default'})

    # 2. 本地 import 路径解析
    for m in re.finditer(r"from\s+['\"](\.[^'\"]+)['\"]", content):
        imp = m.group(1)
        base = (tsx_path.parent / imp).resolve()
        # 支持多点文件名（如 H5.texts.ts），用 str + suffix 而非 with_suffix
        base_str = str(base)
        exists = (
            base.exists()
            or Path(base_str + '.tsx').exists()
            or Path(base_str + '.ts').exists()
            or (base / INDEX_TS_FILENAME).exists()
            or (base / INDEX_TSX_FILENAME).exists()
        )
        if not exists:
            issues.append({'level': 'error', 'msg': f'{label}: import 路径不存在: {imp}'})

    # 3. 无 undefined / NaN 在 JSX 表达式里
    if re.search(r'\{undefined\}|\{NaN\}', content):
        issues.append({'level': 'error', 'msg': f'{label}: 包含 {{undefined}} 或 {{NaN}}'})

    # 4. 未加引号的连字符对象 key（非法 JS 标识符）：如 {button-text: 'xxx'}
    # 匹配 { 后面紧跟 word-word: 的模式（排除已加引号的情况）
    for m in re.finditer(r'\{[^{}\'"]*?(\b\w[\w]*-[\w-]+)\s*:', content):
        bad_key = m.group(1)
        issues.append({'level': 'error', 'msg': f'{label}: 非法 JS 对象 key（需加引号）: {bad_key}'})

    # 5. TypeScript interface/type 中的连字符属性名（非法 TS 标识符）
    for m in re.finditer(r'^\s{2}(\w[\w]*-[\w-]+)\s*:', content, re.MULTILINE):
        bad_prop = m.group(1)
        issues.append({'level': 'error', 'msg': f'{label}: TypeScript 接口含非法属性名: {bad_prop}'})

    # 6. 变量名或函数名以数字开头（如 const 5TypeTab = ... 或 import 5TypeTab from ...）
    if re.search(r'\b(import|const|function)\s+\d', content):
        issues.append({'level': 'error', 'msg': f'{label}: 含以数字开头的变量/组件名'})

    return issues


# ── Split Layer 0-3（旧结构，仅用于 Path B / split-b，当前未实现）────────────
# 新结构（split-a）使用下方 _run_layer_*_new 系列函数（~700 行起）。

def _run_layer_0(components_dir: Path, component_names: list, css_ext: str) -> list:
    issues = []

    # 必要文件存在性
    for f in [INDEX_TS_FILENAME, PAGE_TSX_FILENAME]:
        if not (components_dir / f).exists():
            issues.append({'level': 'error', 'msg': f'缺少 components/{f}'})
    for name in component_names:
        for f in [f'{name}.tsx', f'{name}.module.{css_ext}', INDEX_TS_FILENAME]:
            if not (components_dir / name / f).exists():
                issues.append({'level': 'error', 'msg': f'缺少 {name}/{f}'})

    # TSX 结构校验：Page.tsx + 所有组件 .tsx
    for tsx_path in [components_dir / PAGE_TSX_FILENAME] + [
        components_dir / name / f'{name}.tsx' for name in component_names
    ]:
        if tsx_path.exists():
            issues.extend(_check_tsx(tsx_path, components_dir))

    return issues


# ── Split Layer 1: CSS 漂移检测 ──────────────────────────────────────────────

def _run_layer_1(components_dir: Path, ir_css_map: dict, component_names: list,
                  css_ext: str) -> list:
    if not ir_css_map:
        return [{'level': 'warning', 'msg': '无 ir.json，跳过 CSS 漂移检测'}]

    issues = []
    for name in component_names:
        css_path = components_dir / name / f'{name}.module.{css_ext}'
        if not css_path.exists():
            continue
        actual_classes = _parse_flat_css(css_path.read_text())
        for cls, actual_props in actual_classes.items():
            if cls not in ir_css_map:
                continue  # Moly wrapper 等非 IR 来源的 class，跳过
            expected_props = ir_css_map[cls]
            for prop, expected_val in expected_props.items():
                if expected_val is None or expected_val == '':
                    continue
                actual_val = actual_props.get(prop)
                if actual_val is None:
                    issues.append({
                        'level': 'error',
                        'msg': f'{name}: .{cls} 缺少 {prop}: {expected_val}',
                    })
                    continue
                ok, severity = _values_match(str(expected_val), str(actual_val))
                if not ok:
                    e_px, a_px = _px(str(expected_val)), _px(str(actual_val))
                    delta = (f' (diff {abs(e_px - a_px):.1f}px)'
                             if e_px is not None and a_px is not None else '')
                    issues.append({
                        'level': severity,
                        'msg': f'{name}: .{cls} {prop}: 期望 {expected_val}，实际 {actual_val}{delta}',
                    })
    return issues


# ── Split Layer 2: 类名覆盖检查 ──────────────────────────────────────────────

def _run_layer_2(components_dir: Path, ir_css_map: dict, component_names: list,
                  css_ext: str) -> list:
    if not ir_css_map:
        return [{'level': 'warning', 'msg': '无 ir.json，跳过类名覆盖检查'}]

    found: set = set()
    for name in component_names:
        css_path = components_dir / name / f'{name}.module.{css_ext}'
        if css_path.exists():
            found.update(_parse_flat_css(css_path.read_text()).keys())

    issues = []
    for cls in ir_css_map:
        if cls not in found:
            issues.append({
                'level': 'warning',
                'msg': f'.{cls} 未在任何组件 CSS 中找到（Moly 替换或遗漏）',
            })
    return issues


# ── Split Layer 3: 编译检查 ──────────────────────────────────────────────────

def _run_layer_3(components_dir: Path, component_names: list, css_ext: str) -> list:
    compiler = 'lessc' if css_ext == 'less' else 'sass'
    try:
        subprocess.run([compiler, '--version'], capture_output=True, check=True, timeout=5)
    except Exception:
        return [{'level': 'warning', 'msg': f'{compiler} 不可用，跳过编译检查'}]

    issues = []
    for name in component_names:
        css_path = components_dir / name / f'{name}.module.{css_ext}'
        if not css_path.exists():
            continue
        try:
            result = subprocess.run(
                [compiler, str(css_path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                err = result.stderr.strip()[:200]
                issues.append({'level': 'error', 'msg': f'{name}: 编译失败 — {err}'})
        except Exception as e:
            issues.append({'level': 'error', 'msg': f'{name}: 编译异常 — {e}'})
    return issues


# ── 评分与报告 ────────────────────────────────────────────────────────────────

def _score(all_issues: list) -> int:
    score = 100
    for issue in all_issues:
        lvl = issue['level']
        score -= 5 if lvl == 'error' else (1 if lvl == 'warning' else 0)
    return max(0, score)


def _print_report(layer_results: dict, score: int) -> None:
    W = 48
    print(f'\n╔{"═" * W}╗')
    print(f'║{"拆分产物校验报告":^{W}}║')
    print(f'╠{"═" * W}╣')
    for layer_name, issues in layer_results.items():
        errors   = sum(1 for i in issues if i['level'] == 'error')
        warnings = sum(1 for i in issues if i['level'] == 'warning')
        if errors:
            status = f'✗  {errors} error{"s" if errors > 1 else ""}'
        elif warnings:
            status = f'⚠  {warnings} warning{"s" if warnings > 1 else ""}'
        else:
            status = '✓  通过'
        label = f'  {layer_name}'
        print(f'║{label:<30}{status:<{W - 30}}║')
        for issue in issues[:5]:  # 每层最多展示 5 条
            icon = '✗' if issue['level'] == 'error' else '⚠'
            msg = issue['msg']
            if len(msg) > W - 6:
                msg = msg[:W - 9] + '...'
            print(f'║    {icon} {msg:<{W - 6}}║')
        if len(issues) > 5:
            print(f'║    … 共 {len(issues)} 条{"":<{W - 10}}║')
    print(f'╠{"═" * W}╣')
    verdict = '✅ PASS' if score >= 90 else '❌ FAIL（必须修复后重新拆分）'
    print(f'║  综合评分: {score}/100  {verdict:<{W - 16}}║')
    print(f'╚{"═" * W}╝\n')


# ── Layer 5: Per-figmaId CSS 属性对比（与原始单组件页面对比）─────────────────

def _collect_tsx_css_pairs(search_dir: Path) -> list[tuple[str, dict]]:
    """
    递归扫描目录下所有 .tsx 文件，解析对应的 CSS module 文件。
    返回 [(tsx_content, css_map)] 列表，css_map = {className: {prop: val}}。
    """
    pairs = []
    for tsx_path in search_dir.rglob('*.tsx'):
        content = tsx_path.read_text()
        m = re.search(r"import styles from '\./([^']+)'", content)
        if not m:
            continue
        css_path = tsx_path.parent / m.group(1)
        if not css_path.exists():
            continue
        pairs.append((content, _parse_flat_css(css_path.read_text())))
    return pairs


def _extract_figma_css_map(pairs: list[tuple[str, dict]]) -> dict:
    """
    从 [(tsx_content, css_map)] 中提取 {figmaId: {prop: val}}。
    className={styles['cls']} 与 data-figma-id="id" 在同行（tsx_generator 保证）。
    """
    result = {}
    for tsx_content, css_map in pairs:
        for m in re.finditer(
            r"className=\{styles\['([\w-]+)'\]\}[^<>\n]*?data-figma-id=\"([^\"]+)\"",
            tsx_content,
        ):
            cls, fid = m.group(1), m.group(2)
            if fid not in result and cls in css_map:
                result[fid] = css_map[cls]
        # 属性反序（data-figma-id 在前）
        for m in re.finditer(
            r"data-figma-id=\"([^\"]+)\"[^<>\n]*?className=\{styles\['([\w-]+)'\]\}",
            tsx_content,
        ):
            fid, cls = m.group(1), m.group(2)
            if fid not in result and cls in css_map:
                result[fid] = css_map[cls]
    return result


def _run_layer_5_per_node_css(new_page_dir: Path, page_root: Path, node_id_safe: str) -> list:
    """
    Layer 5: Per-figmaId CSS 对比。
    以原始单组件页面（src/pages/{Name}/）为 ground truth，
    对比 split-a 产物中每个 figma-id 对应的 CSS 属性。
    """
    # merged-* 节点是 PC+H5 合并产物，没有对应的原始单组件目录——跳过而非报 warning。
    if node_id_safe.startswith('merged-'):
        return []

    # 找原始单文件目录（src/pages/{Name}/ 直接，非 components/ 子目录）
    orig_page_dir: Path | None = None
    for d in Path('.').rglob(f'{node_id_safe}-*'):
        if d.is_dir() and 'src/pages' in str(d) and d.name.startswith(node_id_safe):
            # 确认是直接页面目录（不是 components/ 本身的父）
            orig_tsx = list(d.glob('*.tsx'))
            if orig_tsx:
                orig_page_dir = d
                break

    if not orig_page_dir:
        return [{'level': 'warning', 'msg': '找不到原始单组件目录，跳过 Layer 5 CSS 对比'}]

    orig_pairs = _collect_tsx_css_pairs_single(orig_page_dir)
    if not orig_pairs:
        return [{'level': 'warning', 'msg': '原始页面无有效 TSX/CSS 对，跳过 Layer 5'}]
    orig_fid_css = _extract_figma_css_map(orig_pairs)

    # 新结构：index.tsx + 页面根 CSS 在 page_root（target 根），也需要纳入比较
    split_pairs = (
        _collect_tsx_css_pairs_single(page_root)
        + _collect_tsx_css_pairs(new_page_dir)
    )
    split_fid_css = _extract_figma_css_map(split_pairs)

    # 读取 plan 识别被有意剥离 width 的 CSS 类和对应 figmaId
    stripped_width_classes = _load_stripped_width_classes(node_id_safe)
    # 从 plan fidToClass 构建 figmaId → css_class 映射，用于 width 豁免
    fid_to_css_class: dict = {}
    base_pc = PAGE_CODE_DIR
    for plan_path in base_pc.glob(f'{node_id_safe}-*/{PLAN_SUBPATH}'):
        try:
            _p = json.loads(plan_path.read_text())
            fid_to_css_class = _p.get('fidToClass', {})
        except Exception:
            pass
        break

    issues = []
    diffs = []
    for fid, expected_props in orig_fid_css.items():
        actual_props = split_fid_css.get(fid)
        if actual_props is None:
            continue  # 无 CSS class 的节点（可能是装饰/继承样式），跳过
        # 该 figmaId 对应的 CSS 类（用于 width 豁免检查）
        fid_css_cls = fid_to_css_class.get(fid, '')
        for prop, expected_val in expected_props.items():
            if expected_val is None or expected_val == '':
                continue
            # opacity:0 是 Figma 隐藏层约定，_css_from_orig 有意去除，跳过此检查
            if prop == 'opacity' and str(expected_val).strip() in ('0', '0.0'):
                continue
            # width 被宽度冲突检测有意移除，跳过
            if prop == 'width' and fid_css_cls in stripped_width_classes:
                continue
            actual_val = actual_props.get(prop)
            if actual_val is None:
                diffs.append({'level': 'error',
                              'msg': f'{fid}: 缺少 {prop}: {expected_val}'})
                continue
            ok, severity = _values_match(str(expected_val), str(actual_val))
            if not ok:
                e_px, a_px = _px(str(expected_val)), _px(str(actual_val))
                delta = (f' (diff {abs(e_px - a_px):.1f}px)'
                         if e_px is not None and a_px is not None else '')
                diffs.append({'level': severity,
                              'msg': f'{fid}: {prop} 期望 {expected_val}，实际 {actual_val}{delta}'})

    if not diffs:
        issues.append({'level': 'info',
                       'msg': (f'CSS per-node 全匹配 ✓ '
                               f'({len(orig_fid_css)} 原始节点 / '
                               f'{len(split_fid_css)} split-a 节点)')})
    else:
        issues.extend(diffs[:20])
        if len(diffs) > 20:
            issues.append({'level': 'error', 'msg': f'… 共 {len(diffs)} 条差异（仅显示前 20）'})
    return issues


def _collect_tsx_css_pairs_single(page_dir: Path) -> list[tuple[str, dict]]:
    """只取 page_dir 直接子文件（不递归）中的 TSX + CSS 对。用于原始单文件页面。"""
    pairs = []
    for tsx_path in page_dir.glob('*.tsx'):
        content = tsx_path.read_text()
        m = re.search(r"import styles from '\./([^']+)'", content)
        if not m:
            continue
        css_path = tsx_path.parent / m.group(1)
        if not css_path.exists():
            continue
        pairs.append((content, _parse_flat_css(css_path.read_text())))
    return pairs


def _load_stripped_width_classes(node_id_safe: str) -> set:
    """
    读取 plan.json 里的 fidToClass + cssMap，识别被宽度冲突检测剥离了 width 的 CSS 类。
    这些类的 'width 缺失' 是有意为之，不应被 Layer 1/5 报为漂移。
    """
    stripped: set = set()
    base = PAGE_CODE_DIR
    plan_path = next(base.glob(f'{node_id_safe}-*/{PLAN_SUBPATH}'), None)
    if not plan_path or not plan_path.exists():
        return stripped
    try:
        plan = json.loads(plan_path.read_text())
    except Exception:
        return stripped

    fid_to_class = plan.get('fidToClass', {})
    css_map = plan.get('cssMap', {})

    def _check_leaf(lc: dict) -> None:
        tmpl_fid = (lc.get('ir') or {}).get('figmaId', '')
        tmpl_cls = fid_to_class.get(tmpl_fid, '')
        if not tmpl_cls or 'width' not in css_map.get(tmpl_cls, {}):
            return
        # 检查是否有实例无 CSS width
        for fid in lc.get('allInstanceFigmaIds', []):
            if fid == tmpl_fid:
                continue
            inst_cls = fid_to_class.get(fid, '')
            if inst_cls and 'width' not in css_map.get(inst_cls, {}):
                stripped.add(tmpl_cls)
                return

    for s in plan.get('sections', []):
        for lc in s.get('leafComponents', []):
            _check_leaf(lc)
    for lc in plan.get('leafComponents', []):
        _check_leaf(lc)
    return stripped




# ── 新结构 Layer 1/2：CSS 漂移 + 类名覆盖 ─────────────────────────────────

def _load_orig_css_for_validation(node_id_safe: str) -> dict:
    """为验证加载原始单文件 CSS（已包含 convert.py patch 的正确值）。"""
    base = PAGE_CODE_DIR
    if not base.exists():
        return {}
    # 支持两种命名格式：{name}-{nodeId}（新）和 {nodeId}-{name}（旧，向后兼容）
    candidates = list(base.glob(f'{node_id_safe}-*/')) + list(base.glob(f'*-{node_id_safe}/'))
    for d in candidates:
        if not d.is_dir():
            continue
        # 1. 按目录名后缀（适配两种格式）
        parts = d.name.split('-')
        node_parts = node_id_safe.split('-')
        if d.name.startswith(node_id_safe + '-'):
            page_name = d.name[len(node_id_safe) + 1:]
        elif d.name.endswith('-' + node_id_safe):
            page_name = d.name[:-(len(node_id_safe) + 1)]
        else:
            page_name = d.name
        for ext in ('scss', 'less', 'css'):
            p = d / f'{page_name}.module.{ext}'
            if p.exists():
                return _parse_flat_css(p.read_text())
        # 2. Fallback: 取目录下任意 .module.{ext} 文件
        for ext in ('scss', 'less', 'css'):
            css_files = [f for f in d.glob(f'*.module.{ext}') if f.is_file()]
            if css_files:
                return _parse_flat_css(css_files[0].read_text())
    return {}


def _run_layer_1_new(page_dir: Path, page_root: Path, ir_css_map: dict, css_ext: str,
                     node_id_safe: str = '') -> list:
    """新结构 Layer 1：CSS 漂移 — 以原始单文件 CSS（含 convert.py patch）为 ground truth。
    IR CSS map 仅用于 fallback（原始文件不存在时）。
    """
    # 优先用原始 CSS 文件（包含 patch 后的正确值）
    orig_css_map = _load_orig_css_for_validation(node_id_safe) if node_id_safe else {}
    ground_truth = orig_css_map if orig_css_map else ir_css_map

    if not ground_truth:
        return [{'level': 'warning', 'msg': '无原始 CSS / IR，跳过 CSS 漂移检测'}]

    # 收集每个 class 的所有属性出现情况（跨所有 split-a CSS 文件）
    # page_dir = components/，页面根 CSS 在 page_dir.parent/（新结构）
    # key: cls -> {prop: [(val, source_file), ...]}
    all_occurrences: dict = {}
    _root_css = sorted(page_root.glob(f'*.module.{css_ext}'))
    _comp_css = sorted(page_dir.rglob(f'*.module.{css_ext}'))
    for css_path in _root_css + _comp_css:
        for cls, props in _parse_flat_css(css_path.read_text()).items():
            if cls not in all_occurrences:
                all_occurrences[cls] = {}
            for prop, val in props.items():
                if prop not in all_occurrences[cls]:
                    all_occurrences[cls][prop] = []
                all_occurrences[cls][prop].append((val, css_path.name))

    # 识别被宽度冲突检测有意剥离 width 的 CSS 类（不应报漂移）
    stripped_width_classes = _load_stripped_width_classes(node_id_safe) if node_id_safe else set()

    issues = []
    for cls, prop_occurrences in all_occurrences.items():
        if cls not in ground_truth:
            continue
        expected_props = ground_truth[cls]
        for prop, expected_val in expected_props.items():
            if expected_val is None or expected_val == '':
                continue
            # _raw* 是 IR 内部属性（如 _rawBgHex），不会写入 CSS 文件，跳过
            if prop.startswith('_'):
                continue
            # opacity:0 是 Figma 隐藏层约定，_css_from_orig 会有意去除，跳过此检查
            if prop == 'opacity' and str(expected_val).strip() in ('0', '0.0'):
                continue
            # width 被宽度冲突检测有意移除的 class 跳过
            if prop == 'width' and cls in stripped_width_classes:
                continue
            occurrences = prop_occurrences.get(prop, [])
            # base class 回退：非 Moly leaf wrapper 类名格式为 {leaf-root}-n{figmaId}
            # leaf 的视觉属性在其自身 CSS 文件中（.leaf-root），wrapper 正确 strip。
            # 若 strip 后的 base class 在 all_occurrences 中有该属性，则不算丢失。
            _base_cls = re.sub(r'-n\d+-\d+$', '', cls)
            _base_has_prop = (
                _base_cls != cls
                and any(
                    _values_match(str(expected_val), str(v))[0]
                    for v, _ in all_occurrences.get(_base_cls, {}).get(prop, [])
                )
            )
            # U-376 扩展：同前缀的其他实例类也算 base。
            # 场景：OneTap wrapper 'frame-n6777-32832' 的 padding 被有意置 0，
            # 但另一实例 'frame-n6777-32817' 的同名 class 中保留了原值。
            # re.sub 将两者都剥离到 'frame'，但 CSS 文件用的是带 figmaId 的完整类名，
            # 所以需要检查 'frame-n*' 前缀的所有类是否包含该属性的期望值。
            if not _base_has_prop and _base_cls != cls:
                _alt_prefix = _base_cls + '-n'
                for _alt_c in all_occurrences:
                    if _alt_c != cls and _alt_c.startswith(_alt_prefix):
                        if any(_values_match(str(expected_val), str(v))[0]
                               for v, _ in all_occurrences.get(_alt_c, {}).get(prop, [])):
                            _base_has_prop = True
                            break
            if not occurrences:
                if _base_has_prop:
                    continue  # 属性在 leaf 自身 class 中，非 Moly leaf wrapper 正确行为
                # 属性在所有文件中都缺失，取任意一个 source_file 报告
                any_file = next(
                    (f for vals in prop_occurrences.values() for _, f in vals),
                    'unknown.less',
                )
                issues.append({
                    'level': 'error',
                    'msg': f'{any_file}: .{cls} 缺少 {prop}: {expected_val}',
                })
                continue
            # 若任意一个文件的值匹配，视为通过
            any_ok = False
            worst_issue = None
            for actual_val, source_file in occurrences:
                ok, severity = _values_match(str(expected_val), str(actual_val))
                if ok:
                    any_ok = True
                    break
                if worst_issue is None:
                    e_px, a_px = _px(str(expected_val)), _px(str(actual_val))
                    delta = (f' (diff {abs(e_px - a_px):.1f}px)'
                             if e_px is not None and a_px is not None else '')
                    worst_issue = {
                        'level': severity,
                        'msg': f'{source_file}: .{cls} {prop}: 期望 {expected_val}，实际 {actual_val}{delta}',
                    }
            if not any_ok and worst_issue:
                if _base_has_prop:
                    continue  # 值在 base class 中匹配，视为通过
                issues.append(worst_issue)
    return issues


def _run_layer_2_new(page_dir: Path, page_root: Path, ir_css_map: dict, css_ext: str) -> list:
    """新结构 Layer 2：类名覆盖 — IR 里每个 className 必须出现在至少一个 split-a CSS 文件中。"""
    if not ir_css_map:
        return [{'level': 'warning', 'msg': '无 ir.json，跳过类名覆盖检查'}]

    found: set = set()
    for css_path in list(page_root.glob(f'*.module.{css_ext}')) + list(page_dir.rglob(f'*.module.{css_ext}')):
        found.update(_parse_flat_css(css_path.read_text()).keys())

    issues = []
    missing = [cls for cls in ir_css_map if cls not in found]
    for cls in missing[:10]:
        issues.append({'level': 'warning', 'msg': f'.{cls} 未在任何 split-a CSS 中出现'})
    if len(missing) > 10:
        issues.append({'level': 'warning', 'msg': f'… 共 {len(missing)} 个 class 未出现（仅显示前 10）'})
    return issues


# ── 新结构校验（split-a 落地 src/pages/ 方式）──────────────────────────────

def _run_layer_0_new(page_dir: Path, page_root: Path, section_names: list, css_ext: str) -> list:
    """新目录结构 Layer 0：index.tsx + {Name}.module.css 在根，每个 Section 有 index.tsx + index.module.css"""
    issues = []
    if not (page_root / INDEX_TSX_FILENAME).exists():
        issues.append({'level': 'error', 'msg': '缺少 index.tsx（入口文件，应在 components/ 父目录）'})
    # 检查页面根 CSS：{PageName}.module.{ext} 在根目录（与 index.tsx 同级）
    page_css_files = list(page_root.glob(f'*.module.{css_ext}'))
    if not page_css_files:
        issues.append({'level': 'warning', 'msg': f'缺少根级 *.module.{css_ext}（页面根容器样式）'})
    for name in section_names:
        s_dir = page_dir / name
        if not (s_dir / INDEX_TSX_FILENAME).exists():
            issues.append({'level': 'error', 'msg': f'{name}/index.tsx 不存在'})
        if not (s_dir / f'index.module.{css_ext}').exists():
            issues.append({'level': 'warning', 'msg': f'{name}/index.module.{css_ext} 不存在'})
    # TSX 结构校验：根级 index.tsx + Section index.tsx
    for tsx_path in [page_root / INDEX_TSX_FILENAME] + [page_dir / n / INDEX_TSX_FILENAME for n in section_names]:
        if tsx_path.exists():
            issues.extend(_check_tsx(tsx_path, page_dir))
    return issues


def _run_layer_3_new(page_dir: Path, page_root: Path, css_ext: str) -> list:
    """新目录结构 Layer 3：所有 .module.{ext} 文件编译无报错（lessc/sass 不可用时降为 warning）"""
    if css_ext not in ('less', 'scss'):
        return []
    issues = []
    for css_file in list(page_root.glob(f'*.module.{css_ext}')) + list(page_dir.rglob(f'*.module.{css_ext}')):
        try:
            # npx 按需拉取：--package=less 指定包名（bin 叫 lessc），避免 "could not determine executable" 报错
            # npm_config_package_lock=false：防止 npx 在项目根生成 package-lock.json
            cmd = (['npx', '--package=less', 'lessc', '--no-color', str(css_file)] if css_ext == 'less'
                   else ['npx', '--package=sass', 'sass', '--no-source-map', str(css_file)])
            env = {**os.environ, 'npm_config_package_lock': 'false'}
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            issues.append({'level': 'warning', 'msg': '编译器不可用（less/sass 未安装），跳过 Layer 3'})
            return issues
        if result.returncode != 0:
            # npx 找不到包（环境未安装 less/sass）→ warning，跳过剩余文件
            if 'could not determine executable' in result.stderr or 'npm error' in result.stderr:
                issues.append({'level': 'warning', 'msg': f'{css_ext} 编译器未安装（lessc/sass 不在 PATH），跳过 Layer 3'})
                return issues
            rel = css_file.relative_to(page_dir) if css_file.is_relative_to(page_dir) else css_file.name
            issues.append({'level': 'error', 'msg': f'{rel}: {result.stderr.strip()[:80]}'})
    return issues


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    if not args['node_id_safe']:
        print('用法: python3 scripts/validate_split.py <URL> | --node-id=42-100')
        sys.exit(1)

    node_id_safe = args['node_id_safe']
    css_ext = args['css_ext']
    # 读取 ir.json（可选，有则启用 Layer 1/2 校验）
    ir = None
    ir_path = ir_file(node_id_safe)
    if ir_path.exists():
        ir = json.loads(ir_path.read_text())
        resolved_ir = _apply_semantics(ir)
        ir_css_map = _build_ir_css_map(resolved_ir)
    else:
        print(f'⚠   ir.json 不存在，Layer 1/2 将跳过')
        ir_css_map = {}

    # ── 优先查找新落地结构：src/pages/{Name}/ ─────────────────────────────────
    new_page_dir = None
    # 1. 从 plan.json 读取 pageComponent 名称，直接定位落地目录（已改名，不含 nodeId）
    plan_path = PAGE_CODE_DIR
    _plan_comp_name = None
    _css_class_renames: dict = {}   # cssClassRenames from plan.json（用于 Layer 2 类名翻译）
    _plan_candidates = list(plan_path.glob(f'{node_id_safe}-*/{PLAN_SUBPATH}')) + \
                       list(plan_path.glob(f'*-{node_id_safe}/{PLAN_SUBPATH}'))
    for _pp in _plan_candidates:
        try:
            _plan_data = json.loads(_pp.read_text())
            _plan_comp_name = _plan_data.get('pageComponent')
            _css_class_renames = _plan_data.get('cssClassRenames', {})
        except Exception:
            pass
        break
    if _plan_comp_name:
        for _pages_root in Path('.').rglob(str(PAGES_SRC_DIR)):
            if not _pages_root.is_dir():
                continue
            # 大小写不敏感匹配（plan.json 用 PascalCase，目录名可能稍有不同）
            for _d in _pages_root.iterdir():
                if _d.is_dir() and _d.name.lower() == _plan_comp_name.lower():
                    _comp = _d / COMPONENTS_SUBDIR
                    if _comp.is_dir() and (_d / INDEX_TSX_FILENAME).exists():
                        new_page_dir = _comp
                        break
            if new_page_dir:
                break
    # 2. fallback：nodeId 模式搜索（{nodeId}-name / name-{nodeId}）
    if not new_page_dir:
        for pattern in (f'{node_id_safe}-*', f'*-{node_id_safe}'):
            for d in Path('.').rglob(pattern):
                if d.is_dir() and 'src/pages' in str(d):
                    comp = d / COMPONENTS_SUBDIR
                    if comp.is_dir() and (d / INDEX_TSX_FILENAME).exists():
                        new_page_dir = comp
                        break
            if new_page_dir:
                break
    # fallback：stage 目录（尚未落地时，stage 里仍使用 Page.tsx）
    # 同时支持 nodeId-name 和 name-nodeId 两种命名格式
    if not new_page_dir:
        page_code = PAGE_CODE_DIR
        for _pat in (f'{node_id_safe}-*/{SPLIT_A_SUBDIR}/{STAGE_SUBDIR}', f'*-{node_id_safe}/{SPLIT_A_SUBDIR}/{STAGE_SUBDIR}'):
            for d in page_code.glob(_pat):
                if d.is_dir() and ((d / PAGE_TSX_FILENAME).exists() or (d / INDEX_TSX_FILENAME).exists()):
                    new_page_dir = d
                    break
            if new_page_dir:
                break

    if new_page_dir:
        # 统一 page_dir / page_root：
        #   dest 路径（paths 1/2）：new_page_dir = dest/PageName/components/，sections 是直接子目录
        #   stage 路径（fallback）：new_page_dir = stage/，sections 在 stage/components/ 下
        if (new_page_dir / COMPONENTS_SUBDIR).is_dir():
            page_dir = new_page_dir / COMPONENTS_SUBDIR
            page_root = new_page_dir
        else:
            page_dir = new_page_dir
            page_root = new_page_dir.parent
        # 新结构：Section 是 page_dir 下的直接子目录（含 index.tsx），common/ 是共享叶子
        section_names = sorted(
            p.name for p in page_dir.iterdir()
            if p.is_dir() and not p.name.startswith('.') and p.name not in ('common', 'hooks')
        )
        print(f'\n🔍  校验（新结构）: {page_dir}')
        print(f'   Section 数: {len(section_names)}  IR 类名数: {len(ir_css_map)}')
        l0 = _run_layer_0_new(page_dir, page_root, section_names, css_ext)
        l1 = _run_layer_1_new(page_dir, page_root, ir_css_map, css_ext, node_id_safe)
        # Layer 2：若存在 cssClassRenames，将 IR 类名翻译为语义名后再与 split CSS 比对，
        # 避免 frame-*/node-* → 语义名重命名后误报 warning。
        # 支持带 node-suffix 的类名：先精确匹配，再前缀匹配（如 frame-xxx-nNNN → semantic-nNNN）
        def _translate_cls(k: str, renames: dict) -> str:
            if k in renames:
                return renames[k]
            for old, new in renames.items():
                if k.startswith(old + '-'):
                    return new + k[len(old):]
            return k
        _ir_css_map_l2 = {_translate_cls(k, _css_class_renames): v for k, v in ir_css_map.items()} \
            if _css_class_renames else ir_css_map
        l2 = _run_layer_2_new(page_dir, page_root, _ir_css_map_l2, css_ext)
        l3 = _run_layer_3_new(page_dir, page_root, css_ext)
        l5 = _run_layer_5_per_node_css(page_dir, page_root, node_id_safe)
        score = _score(l0 + l1 + l2 + l3 + l5)
        _print_report({
            'Split Layer 0 文件完整性':      l0,
            'Split Layer 1 CSS 漂移(IR)':    l1,
            'Split Layer 2 类名覆盖(IR)':    l2,
            'Split Layer 3 编译':            l3,
            'Split Layer 5 CSS per-node':    l5,
        }, score)
        if score < 90:
            sys.exit(1)
        return

    # ── 兼容旧结构：split-a/components/ ──────────────────────────────────────
    components_dir = None
    for variant in [SPLIT_B_SUBDIR, SPLIT_A_SUBDIR]:
        if PAGE_CODE_DIR.exists():
            for d in PAGE_CODE_DIR.glob(f'{node_id_safe}-*/{variant}/{COMPONENTS_SUBDIR}'):
                if d.is_dir():
                    components_dir = d
                    break
        if components_dir:
            break

    if not components_dir:
        for d in Path('.').rglob('components'):
            if d.is_dir() and f'{node_id_safe}-' in str(d.parent):
                components_dir = d
                break

    if not components_dir or not components_dir.exists():
        print('❌  未找到落地产物，请先完成 P4 拆分落地')
        sys.exit(1)

    # 枚举组件名（排除 index.ts / Page.tsx 等文件）
    component_names = sorted(
        p.name for p in components_dir.iterdir()
        if p.is_dir() and not p.name.startswith('.')
    )

    print(f'\n🔍  校验: {components_dir}')
    print(f'   组件数: {len(component_names)}'
          f'  IR 类名数: {len(ir_css_map)}')

    l0 = _run_layer_0(components_dir, component_names, css_ext)
    l1 = _run_layer_1(components_dir, ir_css_map, component_names, css_ext)
    l2 = _run_layer_2(components_dir, ir_css_map, component_names, css_ext)
    l3 = _run_layer_3(components_dir, component_names, css_ext)

    score = _score(l0 + l1 + l2 + l3)
    _print_report({
        'Split Layer 0 文件完整性': l0,
        'Split Layer 1 CSS 漂移':  l1,
        'Split Layer 2 类名覆盖':  l2,
        'Split Layer 3 编译':      l3,
    }, score)

    if score < 90:
        sys.exit(1)


if __name__ == '__main__':
    main()
