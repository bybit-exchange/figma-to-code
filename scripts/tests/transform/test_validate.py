#!/usr/bin/env python3
"""
test_validate.py — validate.py 单元测试
"""
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tests.helpers import check, reset, print_summary
from lib.paths import PAGE_TSX_FILENAME, SPLIT_A_SUBDIR, STAGE_SUBDIR


def run_tests():
    import importlib
    validate = importlib.import_module('validate')

    # ── U-395: run_assets_validation 必须先检查 name-prefix 目录，再走 rglob ────
    # Bug introduced in c4eb224b: convert.py 将输出目录命名改为 {name}-{node_id_safe}
    # (Page-315-29892)，但 validate.py TSX 搜索候选只有 {prefix}-{name}
    # (315-29892-Page) 和 {name}，均不命中，退化到 rglob。
    # 当 3-page-code/ 下同时存在另一个叫 Page.tsx 的组件（如 PageByMeta-314-11779）
    # 且该文件有 /assets/ 引用时，rglob 可能先找到错误文件，导致 Layer 0 误报缺失。
    # Real data: node 315:29892 (Page_首页) from Page-315-29892 conversion (2026-07-21)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        out = tmp / '3-page-code'

        # 正确目录：convert.py name-prefix 格式生成
        correct_dir = out / 'Page-315-29892'
        correct_dir.mkdir(parents=True)
        (correct_dir / PAGE_TSX_FILENAME).write_text(
            'export default function PagePage() { return <div />; }'  # 无 /assets/ 引用
        )
        (correct_dir / 'asset-path-maps.json').write_text(json.dumps({
            'componentName': 'Page',
            'nodeIdSafe': '315-29892',
            'assetsDir': 'Page',
            'stagingDir': 'Page-315-29892',
        }))

        # 冲突目录：另一个组件也有 Page.tsx，且含 /assets/ 引用
        conflict_dir = out / 'PageByMeta-314-11779' / SPLIT_A_SUBDIR / STAGE_SUBDIR
        conflict_dir.mkdir(parents=True)
        (conflict_dir / PAGE_TSX_FILENAME).write_text(
            '<img src="/assets/PageByMeta-314-11779/I314-11791.svg" />'
        )

        # 真实 IR：node 315:29892 Page_首页
        ir = {'figmaName': 'Page_首页', 'figmaId': '315:29892'}

        # 模拟 rglob 先返回冲突文件（APFS hash 顺序下 PageBy < Page-）
        rglob_order = [conflict_dir / PAGE_TSX_FILENAME, correct_dir / PAGE_TSX_FILENAME]

        with patch.object(validate, 'OUT_DIR', out), \
             patch.object(validate, 'ASSETS_STAGING_DIR', tmp / '1-assets'), \
             patch.object(Path, 'rglob', return_value=iter(rglob_order)):
            result = validate.run_assets_validation(ir)

        # 修复前：rglob 先取到冲突文件 → 发现 /assets/ 引用 → 找不到源文件 →
        #   missing=1，pass=False
        # 修复后：候选 1 直接命中 Page-315-29892/Page.tsx（无引用）→ skip → pass=True
        check('U-395',
              'run_assets_validation: name-prefix dir checked before rglob (c4eb224b regression)',
              result['pass'] is True and result.get('missing', -1) == 0)


    # ── U-396: 无 asset-path-maps.json 时，通过前缀匹配找到 {name}-{nodeId} 暂存目录 ─────
    # Bug: DemoTradingPC node 202:33464 → convert.py 将资源暂存到
    # 1-assets/Node20233464-202-33464/，但 validate.py 在无 maps 文件时
    # _staging_dir_name 默认为 'Node20233464'，找不到暂存目录，导致 130 个资源缺失。
    # Real data: node 202:33464 (DemoTradingPC) from DemoTradingPC conversion.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        out_dir = tmp / '3-page-code'
        staging_base = tmp / '1-assets'
        public_base = tmp / 'public' / 'assets'

        # TSX 文件（convert.py 生成，命名为 name-prefix 格式）
        tsx_dir = out_dir / 'Node20233464-202-33464'
        tsx_dir.mkdir(parents=True)
        (tsx_dir / 'Node20233464.tsx').write_text(
            '<img src="/assets/Node20233464-202-33464/I202-33464.png" />'
        )
        # 不创建 asset-path-maps.json

        # 暂存目录：{name}-{nodeId} 格式（convert.py 实际产物）
        staging_dir = staging_base / 'Node20233464-202-33464'
        staging_dir.mkdir(parents=True)
        (staging_dir / 'I202-33464.png').write_bytes(b'\x89PNG')

        ir = {'figmaName': 'Node20233464', 'figmaId': '202:33464'}

        with patch.object(validate, 'OUT_DIR', out_dir), \
             patch.object(validate, 'ASSETS_STAGING_DIR', staging_base), \
             patch.object(validate, 'PUBLIC_ASSETS_DIR', public_base):
            result = validate.run_assets_validation(ir)

        # 修复前：_staging_dir_name='Node20233464'，staging_dir 不存在 → 整体复制跳过
        #         逐文件 source 路径也错 → missing=1 → pass=False
        # 修复后：前缀匹配到 Node20233464-202-33464 → 整体复制 → pass=True
        check('U-396',
              'run_assets_validation: prefix-match staging dir when asset-path-maps.json absent',
              result['pass'] is True and result.get('missing', -1) == 0)


if __name__ == '__main__':
    reset()
    run_tests()
    ok = print_summary()
    sys.exit(0 if ok else 1)
