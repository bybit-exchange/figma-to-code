#!/usr/bin/env python3
"""
test_visual.py — 视觉回归测试入口

委托给 scripts/tests/visual/run_visual_report.sh，提供与其他 test_*.py 一致的调用界面。

⚠️  本脚本需要外部依赖（Figma API、playwright、Node/pnpm）。
    纯算法单元测试（无外部依赖）请用：python3 scripts/tests/test_all.py
    中间产物完整性检查请用：        python3 scripts/tests/test_pipeline.py

用法：
  # 从 cases.json 运行所有配置的设计稿（默认 fast 模式）
  python3 scripts/tests/test_visual.py

  # 指定 Figma URL
  python3 scripts/tests/test_visual.py --url='https://www.figma.com/design/...'

  # 完整模式（含 Code Connect / Moly 映射）
  python3 scripts/tests/test_visual.py --full

  # 从缓存 IR 重建（跳过 Figma API，适合脚本迭代验证）
  python3 scripts/tests/test_visual.py --from-ir

  # 保留临时 sandbox 目录供调试
  python3 scripts/tests/test_visual.py --url='...' --keep

  # 使用固定工作目录（自动保留所有产物，方便持续调试）
  python3 scripts/tests/test_visual.py --work-dir=/tmp/figma-visual-debug

  # 自定义报告路径
  python3 scripts/tests/test_visual.py --out=my-report.html

选项：
  --url=URL          指定 Figma URL（可重复，未指定时从 visual/cases.json 读取）
  --fast             快速模式，跳过 Code Connect（默认）
  --full             完整模式，含 Code Connect / Moly 映射
  --from-ir          跳过 Figma API，从缓存 IR 重建
  --viewport=N       截图宽度（默认 1440；cases.json 中可按页面单独配置）
  --out=FILE         报告输出路径（默认自动生成带时间戳的文件名）
  --keep             保留临时 sandbox 目录（方便 pnpm run dev 调试）
  --work-dir=PATH    使用固定工作目录（自动复用 / 创建，隐含 --keep）
                     调试方法：
                       cd <PATH>/sandbox && pnpm run dev
                       open <PATH>/screenshots/
"""
from __future__ import annotations

import os
import sys
import argparse
import subprocess
from pathlib import Path

TESTS_DIR   = Path(__file__).parent
SCRIPTS_DIR = TESTS_DIR.parent
VISUAL_DIR  = TESTS_DIR / 'visual'
RUN_SH      = VISUAL_DIR / 'run_visual_report.sh'


def _check_deps() -> list[str]:
    """Return warnings for missing optional dependencies."""
    warnings = []
    # playwright
    r = subprocess.run(
        [sys.executable, '-c', 'from playwright.sync_api import sync_playwright'],
        capture_output=True,
    )
    if r.returncode != 0:
        warnings.append('playwright 未安装（截图将跳过）: pip3 install playwright && playwright install chromium')
    # pillow
    r = subprocess.run(
        [sys.executable, '-c', 'from PIL import Image'],
        capture_output=True,
    )
    if r.returncode != 0:
        warnings.append('pillow 未安装（diff 将跳过）: pip3 install pillow')
    # node / pnpm
    r = subprocess.run(['which', 'pnpm'], capture_output=True)
    if r.returncode != 0:
        warnings.append('pnpm 未找到（sandbox 构建将失败）')
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description='figma-to-code 视觉回归测试入口',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--url', action='append', dest='urls', metavar='URL',
                        help='Figma URL（可重复；未指定则从 visual/cases.json 读取）')
    parser.add_argument('--fast', action='store_true', default=False,
                        help='快速模式，跳过 Code Connect（默认）')
    parser.add_argument('--full', action='store_true', default=False,
                        help='完整模式，含 Code Connect / Moly 映射')
    parser.add_argument('--from-ir', action='store_true', default=False,
                        help='跳过 Figma API，从缓存 IR 重建')
    parser.add_argument('--from-raw-data', action='store_true', default=False,
                        help='跳过 Figma API，从 1-raw-data/ 重新提取 IR（保留原始 JSON）')
    parser.add_argument('--viewport', type=int, default=None,
                        help='截图宽度（默认 1440）')
    parser.add_argument('--out', default=None,
                        help='报告输出路径')
    parser.add_argument('--keep', action='store_true', default=False,
                        help='保留临时 sandbox 目录')
    parser.add_argument('--work-dir', default=None, metavar='PATH',
                        help='固定工作目录（自动创建 / 复用，隐含 --keep）')
    args = parser.parse_args()

    # 依赖检查
    warnings = _check_deps()
    for w in warnings:
        print(f'⚠  {w}')

    if not RUN_SH.exists():
        print(f'❌  找不到 run_visual_report.sh: {RUN_SH}')
        return 1

    # 组装 shell 命令
    cmd: list[str] = ['bash', str(RUN_SH)]

    if args.urls:
        cmd.extend(args.urls)

    if args.full:
        cmd.append('--full')
    else:
        cmd.append('--fast')

    if args.from_ir:
        cmd.append('--from-ir')

    if args.from_raw_data:
        cmd.append('--from-raw-data')

    if args.viewport is not None:
        cmd.append(f'--viewport={args.viewport}')

    if args.out:
        cmd.append(f'--out={args.out}')

    if args.keep:
        cmd.append('--keep')

    if args.work_dir:
        cmd.append(f'--work-dir={args.work_dir}')

    # 从 skill 根目录运行
    skill_dir = SCRIPTS_DIR.parent
    result = subprocess.run(cmd, cwd=str(skill_dir))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
