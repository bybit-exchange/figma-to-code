#!/usr/bin/env python3
"""
产物契约测试 — 对 convert.py 的输出目录进行结构/语法验证。

用法：
  # 测试单个组件目录
  python3 scripts/tests/test_output.py .figma-to-code/3-page-code/42-100-MyPage

  # 测试整个 3-page-code 目录下所有产物
  python3 scripts/tests/test_output.py .figma-to-code/3-page-code --all
"""

import sys
import re
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.paths import INDEX_TS_FILENAME

# ─── 测试结果收集 ─────────────────────────────────────────────────────

_pass = 0
_fail = 0
_failures = []

def ok(msg):
    global _pass
    _pass += 1
    print(f'  ✓  {msg}')

def fail(msg, detail=''):
    global _fail
    _fail += 1
    _failures.append(msg)
    print(f'  ✗  {msg}')
    if detail:
        print(f'       {detail}')

def section(title):
    print(f'\n── {title}')

# ─── 单个产物目录的契约测试 ───────────────────────────────────────────

def test_component_dir(comp_dir: Path):
    """测试单个组件目录是否符合产物契约"""
    comp_dir = Path(comp_dir)
    if not comp_dir.is_dir():
        fail(f'目录不存在: {comp_dir}')
        return

    # 从目录名推断组件名：{Name}-{nodeId}
    parts   = comp_dir.name.split('-', 2)  # nodeId contains one '-'
    # 更可靠：找目录下的 .tsx 文件
    tsx_files = list(comp_dir.glob('*.tsx'))
    name = tsx_files[0].stem if tsx_files else comp_dir.name

    section(f'组件: {comp_dir.name}')

    # ── 1. 文件存在 ────────────────────────────────────────────────
    tsx_p  = comp_dir / f'{name}.tsx'
    less_p_list = list(comp_dir.glob(f'{name}.module.*'))
    idx_p  = comp_dir / INDEX_TS_FILENAME

    tsx_exists  = tsx_p.exists()
    less_exists = bool(less_p_list)
    idx_exists  = idx_p.exists()

    if tsx_exists:     ok(f'{name}.tsx 存在')
    else:              fail(f'{name}.tsx 不存在')

    if less_exists:    ok(f'{name}.module.{{less|scss}} 存在 ({less_p_list[0].name})')
    else:              fail(f'{name}.module.{{less|scss}} 不存在')

    if idx_exists:     ok('index.ts 存在')
    else:              fail('index.ts 不存在')

    # ── 2. TSX 结构契约 ────────────────────────────────────────────
    if tsx_exists:
        tsx = tsx_p.read_text(encoding='utf-8')

        # 2a. import styles from './Name.module.{ext}';
        if re.search(rf"import styles from '\./{re.escape(name)}\.module\.\w+'", tsx):
            ok('TSX: import styles 语句格式正确')
        else:
            fail('TSX: 缺少或格式错误的 import styles', f'期望: import styles from \'./{name}.module.{{ext}}\';')

        # 2b. export [default] function Name(
        if re.search(rf'export(?:\s+default)?\s+function\s+{re.escape(name)}\s*\(', tsx):
            ok(f'TSX: export function {name} 存在')
        else:
            fail(f'TSX: 缺少 export function {name}')

        # 2c. data-figma-id 属性存在（至少一个）
        if 'data-figma-id=' in tsx:
            ok('TSX: 包含 data-figma-id 属性')
        else:
            fail('TSX: 缺少 data-figma-id 属性')

        # 2d. 无明显占位符残留
        # NOTE: '// TODO: replace with useI18n' is intentional i18n template comment
        tsx_no_i18n_todo = re.sub(r'//\s*TODO:\s*replace with.*useI18n.*', '', tsx)
        bad_patterns = ['FIXME', 'undefined', '${undefined}']
        if 'TODO' in tsx_no_i18n_todo:
            bad_patterns.append('TODO')
        found_bad = [p for p in bad_patterns if p in tsx_no_i18n_todo]
        if not found_bad:
            ok('TSX: 无占位符残留')
        else:
            fail(f'TSX: 含占位符 {found_bad}')

        # 2e. JSX 标签基本平衡（开闭标签数差不超过 1）
        open_tags  = len(re.findall(r'<[a-zA-Z][a-zA-Z0-9]*[\s/>]', tsx))
        close_tags = len(re.findall(r'</[a-zA-Z]', tsx))
        self_close = len(re.findall(r'/>', tsx))
        if abs(open_tags - close_tags - self_close) <= 2:
            ok(f'TSX: JSX 标签基本平衡 (open={open_tags}, close={close_tags}, self={self_close})')
        else:
            fail(f'TSX: JSX 标签可能不平衡 (open={open_tags}, close={close_tags}, self={self_close})')

    # ── 3. Less/SCSS 结构契约 ──────────────────────────────────────
    if less_exists:
        style_p = less_p_list[0]
        style   = style_p.read_text(encoding='utf-8')

        # 3a. 至少一个 CSS 类块
        class_blocks = re.findall(r'^\.[a-zA-Z][\w-]*\s*\{', style, re.MULTILINE)
        if class_blocks:
            ok(f'样式: 包含 {len(class_blocks)} 个 CSS 类块')
        else:
            fail('样式: 无 CSS 类块')

        # 3b. 无 undefinedpx / NaNpx
        bad_vals = re.findall(r'(undefinedpx|NaNpx|undefined;)', style)
        if not bad_vals:
            ok('样式: 无无效 CSS 值 (undefinedpx/NaNpx)')
        else:
            fail(f'样式: 含无效 CSS 值 {bad_vals[:3]}')

        # 3c. 花括号平衡
        opens  = style.count('{')
        closes = style.count('}')
        if opens == closes:
            ok(f'样式: 花括号平衡 ({opens} 对)')
        else:
            fail(f'样式: 花括号不平衡 ({{ ={opens}, }} ={closes})')

    # ── 4. index.ts 结构 ──────────────────────────────────────────
    if idx_exists:
        idx = idx_p.read_text(encoding='utf-8')
        if f"from './{name}'" in idx:
            ok('index.ts: 导出路径正确')
        else:
            fail('index.ts: 导出路径可能错误')

    # ── 5. 检查对应的 IR 缓存 ──────────────────────────────────────
    # 从目录名提取 nodeId 前缀（格式：{nodeId段}-{name}）
    ir_dir = Path('.figma-to-code/2-figma-extract')
    if ir_dir.exists():
        # 目录名形如 42-100-MyPage，nodeId = "42:100"
        dir_parts = comp_dir.name.split('-')
        if len(dir_parts) >= 2:
            # 尝试前两段作为 nodeId
            possible_nid = f'{dir_parts[0]}-{dir_parts[1]}'
            ir_path = ir_dir / f'{possible_nid}.ir.json'
            if ir_path.exists():
                try:
                    ir = json.loads(ir_path.read_text())
                    required_keys = {'figmaId', 'figmaName', 'figmaType', 'css', 'children'}
                    missing = required_keys - set(ir.keys())
                    if not missing:
                        ok(f'IR: 包含必填字段 {required_keys}')
                    else:
                        fail(f'IR: 缺少字段 {missing}')
                except json.JSONDecodeError as e:
                    fail(f'IR: JSON 解析失败: {e}')

# ─── 入口 ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    target = Path(sys.argv[1])
    all_mode = '--all' in sys.argv

    if all_mode or (target.is_dir() and not list(target.glob('*.tsx'))):
        # 目录模式：测试目录下所有子目录
        dirs = sorted(d for d in target.iterdir() if d.is_dir()) if target.is_dir() else []
        if not dirs:
            print(f'❌  {target} 下没有子目录')
            sys.exit(1)
        for d in dirs:
            test_component_dir(d)
    else:
        test_component_dir(target)

    # ── 汇总 ──────────────────────────────────────────────────────
    print('\n' + '═' * 44)
    total = _pass + _fail
    if _fail == 0:
        print(f'  ✅  全部通过 ({_pass}/{total})')
    else:
        print(f'  ❌  {_fail} 项失败，{_pass} 项通过 ({_pass}/{total})')
        print('\n  失败项：')
        for f in _failures:
            print(f'    · {f}')
    print('═' * 44)
    sys.exit(0 if _fail == 0 else 1)

if __name__ == '__main__':
    main()
