#!/usr/bin/env python3
"""
test_all.py — 自包含单元测试入口

从各子目录 index.py 加载 SUITES 列表，自动发现并执行所有测试。
新增测试只需在对应目录的 index.py 中追加一行即可。

⚠️  本脚本只包含自包含单元测试（无外部文件依赖，随时可跑）。
    产物验证测试（需要先 convert.py 生成产物）请用：
      python3 scripts/tests/test_product.py

用法：
  python3 scripts/tests/test_all.py
"""

import sys
import re
import subprocess
import argparse
import importlib.util
from pathlib import Path

TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent

STAGES = [
    ('extract', 'Extract（CSS 提取 + IR 构建）'),
    ('transform', 'Transform（后处理 + Token 解析）'),
    ('codegen', 'CodeGen（TSX/SCSS 生成）'),
    ('split', 'Split（组件边界 + 拆分）'),
    ('semantic', 'Semantic（语义提取）'),
    ('merge', 'Merge（响应式合并 + 节点匹配）'),
]


def load_index(stage_dir):
    """Load SUITES from a stage directory's index.py."""
    index_path = TESTS_DIR / stage_dir / 'index.py'
    if not index_path.exists():
        return []
    spec = importlib.util.spec_from_file_location(f'{stage_dir}.index', str(index_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, 'SUITES', [])


def parse_case_count(output):
    """Extract total case count from test output."""
    # Patterns: "(N/N)", "N/N passed", "(N/N)" in summary lines
    matches = re.findall(r'\((\d+)/(\d+)\)', output)
    if matches:
        # Take the last match (summary line)
        return int(matches[-1][1])
    matches = re.findall(r'(\d+)/(\d+) passed', output)
    if matches:
        return int(matches[-1][1])
    # Count individual check lines
    checks = len(re.findall(r'^\s*[✓✗]', output, re.MULTILINE))
    return checks if checks > 0 else None


def run_suite(label, cmd):
    """Run a test file and return (passed, case_count)."""
    print(f'\n{"─" * 44}')
    print(f'▶  {label}')
    print('─' * 44)
    result = subprocess.run(cmd, cwd=str(SCRIPTS_DIR),
                            capture_output=True, text=True)
    output = result.stdout + result.stderr
    print(output, end='')
    cases = parse_case_count(output)
    return result.returncode == 0, cases


def main():
    parser = argparse.ArgumentParser(description='figma-to-code 自包含单元测试')
    args = parser.parse_args()

    py = sys.executable
    results = []  # [(name, passed, case_count)]

    for stage_idx, (stage_dir, stage_label) in enumerate(STAGES, 1):
        suites = load_index(stage_dir)
        for suite_idx, (filename, desc) in enumerate(suites, 1):
            fp = TESTS_DIR / stage_dir / filename
            if not fp.exists():
                print(f'  ⚠  {stage_dir}/{filename} 不存在，跳过')
                continue
            label = f'Stage {stage_idx}{chr(96 + suite_idx)} — {desc}'
            passed, cases = run_suite(label, [py, str(fp)])
            results.append((f'{stage_dir}/{filename}', passed, cases))

    # Summary
    total_cases = sum(c for _, _, c in results if c)
    print(f'\n{"═" * 44}')
    print('  全量测试汇总')
    print('═' * 44)
    all_pass = True
    for name, passed, cases in results:
        status = '✅' if passed else '❌'
        case_str = f' ({cases} cases)' if cases else ''
        print(f'  {status}  {name}{case_str}')
        if not passed:
            all_pass = False
    print('═' * 44)
    passed_suites = sum(1 for _, p, _ in results if p)
    total_suites = len(results)
    status_str = '✅ 全部通过' if all_pass else '❌ 存在失败'
    print(f'  {status_str}  ({passed_suites}/{total_suites} suites, {total_cases} cases total)')
    print('═' * 44)
    print()
    print('  产物验证测试（需先运行 convert.py）请用：')
    print('    python3 scripts/tests/test_product.py')
    print()
    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
