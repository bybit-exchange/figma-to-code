#!/usr/bin/env python3
"""
test_product.py — 产物验证入口（与 test_all.py 平级）

test_all.py  = 自包含单元测试，无外部依赖，随时可跑
test_product.py = 产物验证测试，需要先运行 convert.py 生成产物

用法：
  # 验证默认产物目录
  python3 scripts/tests/test_product.py

  # 指定产物目录
  python3 scripts/tests/test_product.py --out=.figma-to-code/3-page-code

  # 仅跑契约测试（任意设计稿产物均可，不依赖特定设计稿）
  python3 scripts/tests/test_product.py --contract-only

  # 指定样式扩展名
  python3 scripts/tests/test_product.py --css-ext=scss

前置条件：
  必须先运行 convert.py 生成产物，否则本脚本直接退出（不静默跳过）：
    python3 scripts/convert.py 'https://www.figma.com/design/...'
  或使用 run_cross_test.sh 一键完成转换+验证：
    bash scripts/run_cross_test.sh 'https://www.figma.com/design/...'
"""

from __future__ import annotations
import sys
import argparse
import subprocess
from pathlib import Path

TESTS_DIR = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent


def run_suite(label: str, cmd: list) -> tuple[bool, int | None]:
    import re
    print(f'\n{"─" * 44}')
    print(f'▶  {label}')
    print('─' * 44)
    result = subprocess.run(cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
    output = result.stdout + result.stderr
    print(output, end='')
    matches = re.findall(r'\((\d+)/(\d+)\)', output)
    cases = int(matches[-1][1]) if matches else None
    return result.returncode == 0, cases


def main() -> None:
    parser = argparse.ArgumentParser(
        description='figma-to-code 产物验证测试（需要先运行 convert.py）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='提示：自包含单元测试请用 test_all.py；产物验证测试请用本脚本。',
    )
    parser.add_argument(
        '--out', default='.figma-to-code/3-page-code',
        help='产物目录（默认: .figma-to-code/3-page-code）',
    )
    parser.add_argument(
        '--css-ext', default='less',
        help='样式文件后缀（默认: less）',
    )
    parser.add_argument(
        '--contract-only', action='store_true',
        help='仅跑契约测试（文件结构/语法），跳过依赖特定设计稿的回归测试',
    )
    args = parser.parse_args()

    out_path = Path(args.out).resolve()

    # 明确报错，不静默跳过
    if not out_path.exists() or not any(out_path.iterdir()):
        print(f'\n❌  产物目录不存在或为空：{out_path}')
        print()
        print('  请先运行 convert.py 生成产物：')
        print("    python3 scripts/convert.py 'https://www.figma.com/design/...'")
        print()
        print('  或使用一键脚本（转换 + 验证）：')
        print("    bash scripts/run_cross_test.sh 'https://www.figma.com/design/...'")
        sys.exit(2)

    py = sys.executable
    out_abs = str(out_path)
    results: list[tuple[str, bool, int | None]] = []

    # ── 契约测试：文件结构 / TSX 语法 / 非法值 ─────────────────────────
    contract_file = TESTS_DIR / 'product' / 'test_output.py'
    if contract_file.exists():
        passed, cases = run_suite(
            '产物契约测试（文件结构 / TSX 语法 / 非法值）',
            [py, str(contract_file), out_abs, '--all'],
        )
        results.append(('product/test_output.py', passed, cases))

    # ── 回归测试：特定设计稿产物的具体值校验 ──────────────────────────
    if not args.contract_only:
        regression_file = TESTS_DIR / 'product' / 'test_product_regression.py'
        if regression_file.exists():
            passed, cases = run_suite(
                '产物回归测试（P-*/G-*/M-* 具体值校验）',
                [py, str(regression_file), f'--out={out_abs}', f'--css-ext={args.css_ext}'],
            )
            results.append(('product/test_product_regression.py', passed, cases))
    else:
        print('\n  ○  --contract-only：跳过回归测试')

    # ── 汇总 ────────────────────────────────────────────────────────────
    total_cases = sum(c for _, _, c in results if c)
    print(f'\n{"═" * 44}')
    print('  产物验证汇总')
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
    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
