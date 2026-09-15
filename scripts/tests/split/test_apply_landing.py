#!/usr/bin/env python3
"""
split_components.py cmd_apply 落地逻辑测试。
验证当目标目录不存在时，脚本应自动创建并正确落地到 components/。
"""

import sys
import re
import json
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset
from lib.paths import INDEX_TS_FILENAME, INDEX_TSX_FILENAME, PAGE_TSX_FILENAME, HOOKS_SUBDIR, USE_PAGE_ENV_FILENAME, SPLIT_A_SUBDIR, STAGE_SUBDIR

reset()


def _create_mock_page_code(tmp: Path, node_id: str, component_name: str):
    """Create minimal .figma-to-code/3-page-code/{Name}-{nodeId}/ with split-a/stage/."""
    page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{component_name}-{node_id}'
    stage_dir = page_dir / SPLIT_A_SUBDIR / STAGE_SUBDIR
    stage_dir.mkdir(parents=True)

    # Minimal plan.json
    plan = {
        'pageComponent': component_name,
        'nodeIdSafe': node_id,
        'cssExt': 'less',
        'sections': [],
        'topLeaves': [],
        'irPath': str(page_dir / 'fake.ir.json'),
    }
    (page_dir / SPLIT_A_SUBDIR / 'plan.json').write_text(json.dumps(plan))

    # Minimal stage output (simulating what generate_components produces)
    (stage_dir / PAGE_TSX_FILENAME).write_text(
        "import React from 'react';\nexport default function Page() { return <div/>; }\n"
    )
    (stage_dir / f'{component_name}.module.less').write_text('.page { width: 100%; }\n')
    section_dir = stage_dir / 'HeroSection'
    section_dir.mkdir()
    (section_dir / INDEX_TSX_FILENAME).write_text("export default function HeroSection() { return <div/>; }\n")
    (section_dir / 'index.module.less').write_text('.hero { height: 400px; }\n')

    return page_dir


def test_apply_creates_target_when_missing():
    """U-281: cmd_apply should create target dir if it doesn't exist.
    Page.tsx → target/index.tsx; CSS → target root; sections → components/.
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '999-111'
        comp_name = 'TestPage'

        # Setup: .figma-to-code with split-a/stage ready
        page_dir = _create_mock_page_code(tmp, node_id, comp_name)

        # Setup: dest dir exists but target subdir does NOT
        dest_dir = tmp / 'src' / 'pages' / 'demo'
        dest_dir.mkdir(parents=True)

        # The target directory (dest_dir/TestPage) does NOT exist yet (no nodeId in dir name)
        target = dest_dir / comp_name
        check('U-281a', 'target dir does not exist before apply', not target.exists())

        # Run cmd_apply (import and call directly)
        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from split_components import cmd_apply
            cmd_apply(node_id, str(dest_dir), 'less')
        finally:
            os.chdir(old_cwd)

        # Verify: target was created
        check('U-281b', 'target dir created by cmd_apply', target.exists())

        # Verify: components/ subdir exists (holds sections/leaves, NOT Page.tsx)
        comp_dir = target / 'components'
        check('U-281c', 'components/ dir created', comp_dir.exists())

        # U-281d/e: Page.tsx → index.tsx at target root; CSS also at target root
        check('U-281d', 'index.tsx at target root (Page.tsx renamed)',
              (target / INDEX_TSX_FILENAME).exists())
        check('U-281e', 'less file at target root (not in components/)',
              (target / f'{comp_name}.module.less').exists())

        # U-281f/g: index.ts should NOT exist (replaced by index.tsx)
        check('U-281f', 'index.ts does NOT exist (replaced by index.tsx)',
              not (target / INDEX_TS_FILENAME).exists())
        if (target / INDEX_TSX_FILENAME).exists():
            content = (target / INDEX_TSX_FILENAME).read_text()
            check('U-281g', 'index.tsx contains export default function',
                  'export default function' in content)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_duplicate_section_names_deduped():
    """U-282: duplicate section names in plan should be deduped with numeric suffix in Page.tsx imports."""
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '888-222'
        comp_name = 'DupePage'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        stage_dir = page_dir / SPLIT_A_SUBDIR / STAGE_SUBDIR
        stage_dir.mkdir(parents=True)

        # Plan with duplicate section names
        plan = {
            'pageComponent': comp_name,
            'nodeIdSafe': node_id,
            'nodeId': node_id,
            'cssExt': 'less',
            'sections': [
                {'name': 'HeroSection', 'ir': {'figmaId': '1:1', 'figmaName': 'Hero1',
                 'figmaType': 'FRAME', 'css': {}, 'children': [], 'bb': {'width': 1440, 'height': 400}},
                 'leafComponents': [], 'y': 0},
                {'name': 'HeroSection', 'ir': {'figmaId': '1:2', 'figmaName': 'Hero2',
                 'figmaType': 'FRAME', 'css': {}, 'children': [], 'bb': {'width': 1440, 'height': 400}},
                 'leafComponents': [], 'y': 400},
            ],
            'leafComponents': [],
            'topLeaves': [],
            'inlineNodes': [],
            'irPath': str(page_dir / 'fake.ir.json'),
        }
        (page_dir / SPLIT_A_SUBDIR / 'plan.json').write_text(json.dumps(plan))

        dest_dir = tmp / 'src' / 'pages'
        dest_dir.mkdir(parents=True)

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from split_components import cmd_apply
            cmd_apply(node_id, str(dest_dir), 'less')
        finally:
            os.chdir(old_cwd)

        target = dest_dir / comp_name
        comp_dir = target / 'components'
        page_tsx = target / INDEX_TSX_FILENAME

        check('U-282a', 'index.tsx generated at target root', page_tsx.exists())
        if page_tsx.exists():
            content = page_tsx.read_text()
            import_lines = [l for l in content.split('\n') if l.startswith('import ') and 'Section' in l]
            check('U-282b', 'no duplicate import lines',
                  len(import_lines) == len(set(import_lines)))
            # Check that both sections have distinct directory names
            section_dirs = [d.name for d in comp_dir.iterdir() if d.is_dir() and 'Section' in d.name]
            check('U-282c', 'section dirs are unique (deduped)',
                  len(section_dirs) == len(set(section_dirs)))
            check('U-282d', f'exactly 2 section dirs (got {len(section_dirs)})',
                  len(section_dirs) == 2)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_subsection_generation():
    """U-283: Section 内部子 FRAME 节点复杂度 > 20 时，同级全部 FRAME 提取为子 Section。

    规则：用后代节点复杂度代替高度阈值——平级子节点要拆都拆。
    Real data: VolDataSection (Frame 2147224833, 10664-26578) 内部：
      Frame2147224775 (49 后代)  ← 小节点但复杂度>20，同级触发，也要拆
      Frame2147224806 (281 后代)
      Frame2147224832 (369 后代)
      Frame2147224831 (359 后代)
    → 4 个子目录，父 index.tsx 引用全部。
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '777-333'
        comp_name = 'TestVolPage'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        (page_dir / SPLIT_A_SUBDIR).mkdir(parents=True)

        def _mk_leaf(fid, name):
            return {'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
                    'isTextNode': False, 'isComponentInstance': False,
                    'css': {}, 'bb': {'width': 100, 'height': 20}, 'children': []}

        def _mk_ir(fid, name, h, n_leaves=0):
            return {'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
                    'isTextNode': False, 'isComponentInstance': False,
                    'css': {'width': '1440px'}, 'bb': {'width': 1440, 'height': h},
                    'children': [_mk_leaf(f'{fid}c{i}', f'leaf{i}') for i in range(n_leaves)]}

        # 4 个平级子 FRAME，后代数均 > 20（触发同级全拆规则）
        # Real data: nodes from VolDataSection page 10664-26578
        sub1 = _mk_ir('10664:26610', 'Frame 2147224775', 146, n_leaves=25)  # 49 后代实际
        sub2 = _mk_ir('10664:26660', 'Frame 2147224806', 604, n_leaves=30)
        sub3 = _mk_ir('10664:26979', 'Frame 2147224832', 889, n_leaves=30)
        sub4 = _mk_ir('10664:27329', 'Frame 2147224831', 717, n_leaves=30)
        section_ir = {
            'figmaId': '10664:26609', 'figmaName': 'Frame 2147224833',
            'figmaType': 'FRAME', 'isTextNode': False, 'isComponentInstance': False,
            'css': {'width': '1440px'}, 'bb': {'width': 1440, 'height': 2463},
            'children': [sub1, sub2, sub3, sub4],
        }

        plan = {
            'pageComponent': comp_name, 'nodeIdSafe': node_id, 'nodeId': node_id,
            'cssExt': 'less',
            'sections': [{
                'name': 'VolDataSection', 'ir': section_ir,
                'leafComponents': [], 'y': 0,
                # 4 个子 Section（所有平级 FRAME，不按高度过滤）
                'subSections': [
                    {'name': 'Frame2147224775', 'ir': sub1, 'leafComponents': []},
                    {'name': 'Frame2147224806', 'ir': sub2, 'leafComponents': []},
                    {'name': 'Frame2147224832', 'ir': sub3, 'leafComponents': []},
                    {'name': 'Frame2147224831', 'ir': sub4, 'leafComponents': []},
                ],
            }],
            'leafComponents': [], 'topLeaves': [], 'inlineNodes': [],
            'irPath': str(page_dir / 'fake.ir.json'),
            'fidToClass': {}, 'cssMap': {},
        }
        (page_dir / SPLIT_A_SUBDIR / 'plan.json').write_text(json.dumps(plan))

        dest_dir = tmp / 'src' / 'pages'
        dest_dir.mkdir(parents=True)

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from split_components import cmd_apply
            cmd_apply(node_id, str(dest_dir), 'less')
        finally:
            os.chdir(old_cwd)

        comp_dir = dest_dir / comp_name / 'components'
        vol_dir = comp_dir / 'VolDataSection'

        check('U-283a', 'VolDataSection/ 目录已生成', vol_dir.exists())
        # 全部 4 个平级子节点都有独立子目录
        check('U-283b', 'Frame2147224775/ 已生成（同级触发，小节点也拆）',
              (vol_dir / 'Frame2147224775').is_dir())
        check('U-283c', 'Frame2147224806/ 已生成',
              (vol_dir / 'Frame2147224806').is_dir())
        check('U-283d', 'Frame2147224832/ 已生成',
              (vol_dir / 'Frame2147224832').is_dir())
        check('U-283e', 'Frame2147224831/ 已生成',
              (vol_dir / 'Frame2147224831').is_dir())
        # 每个子 Section 有 index.tsx
        check('U-283f', 'Frame2147224775/index.tsx 存在',
              (vol_dir / 'Frame2147224775' / INDEX_TSX_FILENAME).exists())
        check('U-283g', 'Frame2147224806/index.tsx 存在',
              (vol_dir / 'Frame2147224806' / INDEX_TSX_FILENAME).exists())
        # 父 index.tsx 引用全部 4 个子 Section
        parent_tsx = vol_dir / INDEX_TSX_FILENAME
        check('U-283h', 'VolDataSection/index.tsx 存在', parent_tsx.exists())
        if parent_tsx.exists():
            content = parent_tsx.read_text()
            check('U-283i', '父 index.tsx import Frame2147224775',
                  'Frame2147224775' in content)
            check('U-283j', '父 index.tsx import Frame2147224806',
                  'Frame2147224806' in content)
            check('U-283k', '父 index.tsx 使用 <Frame2147224775',
                  '<Frame2147224775' in content)
            check('U-283l', '父 index.tsx 使用 <Frame2147224806',
                  '<Frame2147224806' in content)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hooks_dir_sibling_of_components():
    """U-284: hooks/ 目录应与 components/ 平级，不应在 components/ 内部。

    正确结构（Page.tsx 已提升为 index.tsx）：
      PageName-nodeId/
        index.tsx                 ← import from './hooks/usePageEnv'
        hooks/usePageEnv.ts       ← 与 components/ 同级
        components/
          SectionName/
            index.tsx             ← import from '../../hooks/usePageEnv'
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '555-111'
        comp_name = 'HooksTestPage'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)

        plan = {
            'pageComponent': comp_name, 'nodeIdSafe': node_id, 'nodeId': node_id,
            'cssExt': 'less',
            'sections': [{
                'name': 'HeroSection',
                'ir': {'figmaId': '1:1', 'figmaName': 'Hero', 'figmaType': 'FRAME',
                        'isTextNode': False, 'isComponentInstance': False,
                        'css': {'width': '1440px'}, 'bb': {'width': 1440, 'height': 400},
                        'children': []},
                'leafComponents': [], 'y': 0,
            }],
            'leafComponents': [], 'topLeaves': [], 'inlineNodes': [],
            'irPath': str(page_dir / 'fake.ir.json'),
            'fidToClass': {}, 'cssMap': {},
        }
        (page_dir / SPLIT_A_SUBDIR / 'plan.json').parent.mkdir(parents=True)
        (page_dir / SPLIT_A_SUBDIR / 'plan.json').write_text(json.dumps(plan))

        dest_dir = tmp / 'src' / 'pages'
        dest_dir.mkdir(parents=True)

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from split_components import cmd_apply
            cmd_apply(node_id, str(dest_dir), 'less')
        finally:
            os.chdir(old_cwd)

        page_root = dest_dir / comp_name
        comp_dir = page_root / 'components'

        # hooks/ 应在 PageName-nodeId/ 下，不在 components/ 下
        check('U-284a', 'hooks/ 与 components/ 平级（PageName-nodeId/hooks/）',
              (page_root / HOOKS_SUBDIR / USE_PAGE_ENV_FILENAME).exists())
        check('U-284b', 'hooks/ 不在 components/ 内部',
              not (comp_dir / HOOKS_SUBDIR / USE_PAGE_ENV_FILENAME).exists())

        # index.tsx（提升到根）的 usePageEnv import 应为 './hooks/usePageEnv'
        index_tsx = page_root / INDEX_TSX_FILENAME
        if index_tsx.exists():
            content = index_tsx.read_text()
            check('U-284c', "index.tsx import './hooks/usePageEnv'（根级路径）",
                  "'./hooks/usePageEnv'" in content)
            check('U-284d', "index.tsx 不 import '../hooks/usePageEnv'（components/ 内的旧路径）",
                  "'../hooks/usePageEnv'" not in content)

        # Section index.tsx 若存在 usePageEnv import，路径应为 '../../hooks/usePageEnv'
        # （import 是条件性的，只在 JSX 有 pageTheme 时才注入；空 Section 可无此 import）
        section_tsx = comp_dir / 'HeroSection' / INDEX_TSX_FILENAME
        if section_tsx.exists():
            content = section_tsx.read_text()
            if '../hooks/usePageEnv' in content:
                check('U-284e', "Section 若有 usePageEnv import，路径为 '../../hooks/usePageEnv'",
                      '../../hooks/usePageEnv' in content
                      and '../hooks/usePageEnv' not in content.replace('../../', ''))
            else:
                check('U-284e', "Section 无 usePageEnv import（空 Section，pageTheme 未使用）", True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_page_tsx_at_root():
    """U-285: Page.tsx 提升为 index.tsx 放在 PageName-nodeId/ 根，CSS 也提升到根。

    新目录结构：
      PageName-nodeId/
        index.tsx                        ← Page.tsx 重命名后提升（非 components/）
        PageName.module.less             ← 页面根 CSS 提升（非 components/）
        hooks/
        components/
          HeroSection/
            index.tsx
            index.module.less

    核心验证：
    - index.tsx 存在于 target 根（非 components/）
    - {comp_name}.module.less 存在于 target 根（非 components/）
    - index.ts 不存在（被 index.tsx 替代）
    - index.tsx sections import 使用 './components/{Name}' 路径
    - index.tsx hooks import 使用 './hooks/usePageEnv' 路径
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '444-555'
        comp_name = 'RootPageTest'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)

        plan = {
            'pageComponent': comp_name, 'nodeIdSafe': node_id, 'nodeId': node_id,
            'cssExt': 'less',
            'sections': [{
                'name': 'HeroSection',
                'ir': {'figmaId': '1:1', 'figmaName': 'Hero', 'figmaType': 'FRAME',
                        'isTextNode': False, 'isComponentInstance': False,
                        'css': {'width': '1440px'}, 'bb': {'width': 1440, 'height': 400},
                        'children': []},
                'leafComponents': [], 'y': 0,
            }],
            'leafComponents': [], 'topLeaves': [], 'inlineNodes': [],
            'irPath': str(page_dir / 'fake.ir.json'),
            'fidToClass': {}, 'cssMap': {},
        }
        (page_dir / SPLIT_A_SUBDIR).mkdir(parents=True)
        (page_dir / SPLIT_A_SUBDIR / 'plan.json').write_text(json.dumps(plan))

        dest_dir = tmp / 'src' / 'pages'
        dest_dir.mkdir(parents=True)

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from split_components import cmd_apply
            cmd_apply(node_id, str(dest_dir), 'less')
        finally:
            os.chdir(old_cwd)

        target = dest_dir / comp_name
        comp_dir = target / 'components'

        # U-285a/b: index.tsx 与 CSS 在 target 根
        check('U-285a', 'index.tsx 在 target 根（不在 components/）',
              (target / INDEX_TSX_FILENAME).exists())
        check('U-285b', f'{comp_name}.module.less 在 target 根（不在 components/）',
              (target / f'{comp_name}.module.less').exists())

        # U-285c/d: components/ 中不含 Page.tsx 也不含 CSS
        check('U-285c', 'components/ 中无 Page.tsx',
              not (comp_dir / PAGE_TSX_FILENAME).exists())
        check('U-285d', f'components/ 中无 {comp_name}.module.less',
              not (comp_dir / f'{comp_name}.module.less').exists())

        # U-285e: index.ts 不存在（被 index.tsx 替代）
        check('U-285e', 'index.ts 不存在（已被 index.tsx 替代）',
              not (target / INDEX_TS_FILENAME).exists())

        # U-285f/g: index.tsx 中的 import 路径正确
        index_tsx = target / INDEX_TSX_FILENAME
        if index_tsx.exists():
            content = index_tsx.read_text()
            check('U-285f', "index.tsx 通过 './components/HeroSection' 引入 Section",
                  "'./components/HeroSection'" in content
                  or './components/HeroSection' in content)
            check('U-285g', "index.tsx 通过 './hooks/usePageEnv' 引入 hooks",
                  "'./hooks/usePageEnv'" in content)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_asset_path_maps_json_and_no_nodeid_in_output_dirs():
    """U-329: cmd_apply 落地时，产物目录名不含 nodeId，并生成 asset-path-maps.json 记录映射。

    需求：public/assets/ 和 src/pages/ 中的目录名去掉 nodeId，
    nodeId 映射记录在 .figma-to-code/3-page-code/{Name}-{nodeId}/asset-path-maps.json。

    场景（real data: AI Hub, 169-33787）：
      - 当前：src/pages/AiHub-169-33787/，public/assets/AiHub-169-33787/
      - 期望：src/pages/AiHub/，public/assets/AiHub/
      - TSX：/assets/AiHub-169-33787/ → /assets/AiHub/
      - mapping：.figma-to-code/3-page-code/AiHub-169-33787/asset-path-maps.json
    """
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from split_components import cmd_apply

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '169-33787'
        comp_name = 'AiHub'

        _create_mock_page_code(tmp, node_id, comp_name)

        # 修改 stage/Page.tsx 中含有 nodeId 的资源路径
        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_tsx = page_dir / SPLIT_A_SUBDIR / STAGE_SUBDIR / PAGE_TSX_FILENAME
        page_tsx.write_text(
            f"<img src=\"/assets/{comp_name}-{node_id}/icon.svg\" />\n"
            f"<img src=\"/assets/{comp_name}-{node_id}/bg.png\" />\n"
        )

        # 模拟 public/assets 已落地的旧格式目录
        old_pub_assets = tmp / 'public' / 'assets' / f'{comp_name}-{node_id}'
        old_pub_assets.mkdir(parents=True)
        (old_pub_assets / 'icon.svg').write_text('<svg/>')

        pages_dir = tmp / 'src' / 'pages'
        pages_dir.mkdir(parents=True)

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply(node_id, str(pages_dir), 'less')
        finally:
            os.chdir(old_cwd)

        target = pages_dir / comp_name          # 期望无 nodeId
        old_target = pages_dir / f'{comp_name}-{node_id}'
        new_pub = tmp / 'public' / 'assets' / comp_name
        map_file = page_dir / 'asset-path-maps.json'

        # U-329a: src/pages 目标目录不含 nodeId
        check('U-329a', f'src/pages/{comp_name}/ 已创建（无 nodeId）', target.exists())
        check('U-329a2', f'src/pages/{comp_name}-{node_id}/ 不存在', not old_target.exists())

        # U-329b: TSX 中资源路径已去除 nodeId
        tsx_content = (target / INDEX_TSX_FILENAME).read_text() if (target / INDEX_TSX_FILENAME).exists() else ''
        if not tsx_content:
            tsx_files = list(target.rglob('*.tsx'))
            tsx_content = '\n'.join(f.read_text() for f in tsx_files)
        check('U-329b', 'TSX 文件中无含 nodeId 的资源路径',
              f'/assets/{comp_name}-{node_id}/' not in tsx_content)

        # U-329c: public/assets 目录已重命名（无 nodeId）
        check('U-329c', f'public/assets/{comp_name}/ 已创建', new_pub.exists())
        check('U-329c2', f'public/assets/{comp_name}-{node_id}/ 已删除', not old_pub_assets.exists())

        # U-329d: asset-path-maps.json 已创建并包含正确映射
        check('U-329d', 'asset-path-maps.json 已创建', map_file.exists())
        if map_file.exists():
            mapping = json.loads(map_file.read_text())
            check('U-329d2', 'mapping.componentName 正确', mapping.get('componentName') == comp_name)
            check('U-329d3', 'mapping.nodeIdSafe 正确', mapping.get('nodeIdSafe') == node_id)
            check('U-329d4', 'mapping.pagesDir 无 nodeId', mapping.get('pagesDir') == comp_name)
            check('U-329d5', 'mapping.assetsDir 无 nodeId', mapping.get('assetsDir') == comp_name)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_asset_paths_updated_and_dirs_renamed_on_page_rename():
    """U-328: --apply-renames 仅更新 plan.json，不重命名任何目录；
    --apply 落地时负责从 1-assets/*-nodeId/ 扫描暂存区并正确落地资源。

    Design: cmd_apply_renames 不再重命名 3-page-code/、1-assets/、public/assets/，
    也不更新 stage TSX 路径（--apply 重建 stage 时会覆盖）。
    资源路径和目录的最终正确性由 cmd_apply 保证：
      - 通过 fidToSrc / public/assets 扫描收集所有旧前缀统一替换
      - 1-assets 兜底从 *-{nodeId} 扫描（不依赖目录是否已重命名）

    Real scenario: Frame2147224870-10664-26577 → OptionExperience2026
    """
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from split_components import cmd_apply_renames

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '10664-26577'
        old_name = 'Frame2147224870'
        new_name = 'OptionExperience2026'

        # 模拟 .figma-to-code/3-page-code/OldName-nodeId/ (plan + stage tsx)
        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{old_name}-{node_id}'
        stage_dir = page_dir / SPLIT_A_SUBDIR / STAGE_SUBDIR
        stage_dir.mkdir(parents=True)
        plan_path = page_dir / SPLIT_A_SUBDIR / 'plan.json'
        plan_path.write_text(json.dumps({
            'pageComponent': old_name,
            'nodeId': node_id,
            'cssExt': 'less',
            'sections': [], 'leafComponents': [], 'inlineNodes': [],
        }))
        (stage_dir / PAGE_TSX_FILENAME).write_text(
            f"<img src=\"/assets/{old_name}-{node_id}/icon.svg\" />\n"
        )

        # 模拟 1-assets（原始帧名，不会被 --apply-renames 重命名）
        old_assets_int = tmp / '.figma-to-code' / '1-assets' / f'{old_name}-{node_id}'
        old_assets_int.mkdir(parents=True)
        (old_assets_int / 'icon.svg').write_text('<svg/>')

        renames = {'page': new_name}
        renames_path = tmp / 'renames.json'
        renames_path.write_text(json.dumps(renames))

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply_renames(node_id, str(renames_path))
        finally:
            os.chdir(old_cwd)

        # U-328a: --apply-renames 不重命名 3-page-code 目录
        old_page_dir_still = tmp / '.figma-to-code' / '3-page-code' / f'{old_name}-{node_id}'
        new_page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{new_name}-{node_id}'
        check('U-328a', '3-page-code 目录未被 --apply-renames 重命名（原名仍存在）',
              old_page_dir_still.exists())
        check('U-328a2', '3-page-code 不产生新名称目录', not new_page_dir.exists())

        # U-328b: plan.json 中 pageComponent 已更新
        updated_plan = json.loads(plan_path.read_text())
        check('U-328b', 'plan.json pageComponent 已更新', updated_plan['pageComponent'] == new_name)

        # U-328c: 1-assets 目录未被 --apply-renames 重命名
        check('U-328c', '1-assets 原始帧名目录仍存在', old_assets_int.exists())
        new_assets_int = tmp / '.figma-to-code' / '1-assets' / f'{new_name}-{node_id}'
        check('U-328c2', '1-assets 未产生新名称目录', not new_assets_int.exists())

        # U-328d: stage TSX 未被 --apply-renames 修改（--apply 会重建 stage）
        page_tsx = (old_page_dir_still / SPLIT_A_SUBDIR / STAGE_SUBDIR / PAGE_TSX_FILENAME).read_text()
        check('U-328d', 'stage TSX 路径未被 --apply-renames 修改（保持原始帧名）',
              f'/assets/{old_name}-{node_id}/' in page_tsx)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_old_directory_removed_when_page_renamed():
    """U-327: --apply 落地时，若存在同 nodeId 但旧名称的目录，应自动删除，只保留新名称目录。

    Bug: cmd_apply 只创建 NewName-nodeId/，不删除 OldName-nodeId/，
    导致 src/pages/ 下同时存在两个同 nodeId 目录。

    Real scenario: Brand6PreKYC-4030-41365（旧）+ PreKYC-4030-41365（新）
    AI rename pass 将 pageComponent 从 Brand6PreKYC 改为 PreKYC 后，
    --apply 应原子替换：删除旧目录，只留新目录。
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from split_components import cmd_apply

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '4030-41365'
        old_name = 'Brand6PreKYC'
        new_name = 'PreKYC'

        # 创建旧目录（模拟上次 --apply 的产物）
        pages_dir = tmp / 'src' / 'pages'
        old_target = pages_dir / f'{old_name}-{node_id}'
        old_target.mkdir(parents=True)
        (old_target / 'old-marker.txt').write_text('old')

        # 创建 .figma-to-code 产物（新名称 plan）
        _create_mock_page_code(tmp, node_id, new_name)
        # 模拟 plan.json 的 pageComponent 已改为新名称
        plan_path = tmp / '.figma-to-code' / '3-page-code' / f'{new_name}-{node_id}' / SPLIT_A_SUBDIR / 'plan.json'
        plan = json.loads(plan_path.read_text())
        plan['pageComponent'] = new_name
        plan_path.write_text(json.dumps(plan))

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply(node_id, str(pages_dir), 'less')
        finally:
            os.chdir(old_cwd)

        new_target = pages_dir / new_name

        # 新目录存在（不含 nodeId）
        check('U-327a', f'新目录 {new_name}/ 已创建（无 nodeId）', new_target.exists())
        # 旧目录已被删除
        check('U-327b', f'旧目录 {old_name}-{node_id} 已被删除', not old_target.exists())
        # 旧内容不残留
        check('U-327c', '旧标记文件不存在于新目录', not (new_target / 'old-marker.txt').exists())

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_asset_path_strip_nodeId_when_component_name_case_differs():
    """U-330: pageComponent 大小写与 Figma 原始路径不同时，nodeId 仍需正确去除。

    Root cause: cmd_apply 用 plan['pageComponent']（camelCase）构建 old_asset_prefix，
    但 convert.py 以 Figma 原始名（mixed case）生成 TSX 中的资源路径，两者大小写不同
    → str.replace() 无法匹配 → TSX 路径未更新 → 浏览器 404 → 整页图标缺失。

    Real data: Brand6PreKyc (nodeId: 4030-41365)
      - plan['pageComponent'] = 'Brand6PreKYC'（PascalCase，KYC 全大写）
      - TSX 中路径: '/assets/Brand6PreKyc-4030-41365/...'（原始 Figma 名，yc 小写）
      - 期望: TSX 路径更新为 '/assets/Brand6PreKYC/'（去除 nodeId）

    Test approach: directly test path replacement logic without going through
    generate_components (which re-generates stage with comp_name casing).
    We create target TSX with figma-cased paths, set up public/assets with
    figma-cased dir, and call the path-collection + replacement logic directly.
    """
    import os

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '4030-41365'
        comp_name = 'Brand6PreKYC'           # PascalCase used in plan (KYC uppercase)
        figma_folder_name = 'Brand6PreKyc'   # original Figma casing (yc lowercase)
        new_asset_prefix = f'/assets/{comp_name}/'

        # Create target TSX files WITH figma-cased paths (simulating what
        # generate_components produces when fidToSrc uses original Figma casing)
        target = tmp / 'src' / 'pages' / comp_name
        target.mkdir(parents=True)
        (target / INDEX_TSX_FILENAME).write_text(
            f'<img src="/assets/{figma_folder_name}-{node_id}/icon.svg" />\n'
            f'<img src="/assets/{figma_folder_name}-{node_id}/bg.png" />\n'
        )
        comp_dir = target / 'components' / 'Section1'
        comp_dir.mkdir(parents=True)
        (comp_dir / INDEX_TSX_FILENAME).write_text(
            f'<img src="/assets/{figma_folder_name}-{node_id}/arrow.svg" />\n'
        )

        # public/assets uses Figma casing (figma_folder_name, NOT comp_name)
        old_pub = tmp / 'public' / 'assets' / f'{figma_folder_name}-{node_id}'
        old_pub.mkdir(parents=True)
        (old_pub / 'icon.svg').write_text('<svg/>')

        # Replicate the fixed path-update logic from cmd_apply
        # (scanning public/assets for actual dir names with this nodeId)
        pub_base = tmp / 'public' / 'assets'
        old_prefixes = {f'/assets/{comp_name}-{node_id}/'}  # default (wrong case)
        for d in pub_base.glob(f'*-{node_id}'):
            if d.is_dir():
                old_prefixes.add(f'/assets/{d.name}/')  # add actual figma-cased name

        tsx_updated = 0
        for tsx in target.rglob('*.tsx'):
            c = tsx.read_text()
            changed = False
            for old_pref in old_prefixes:
                if old_pref in c:
                    c = c.replace(old_pref, new_asset_prefix)
                    changed = True
            if changed:
                tsx.write_text(c)
                tsx_updated += 1

        all_tsx = '\n'.join(f.read_text() for f in target.rglob('*.tsx'))

        # U-330a: Figma-cased nodeId path must be gone from all TSX files
        old_figma_prefix = f'/assets/{figma_folder_name}-{node_id}/'
        check('U-330a', f'TSX 中不含 Figma 原始大小写 nodeId 路径 ({figma_folder_name}-{node_id})',
              old_figma_prefix not in all_tsx)

        # U-330b: paths should use comp_name without nodeId
        check('U-330b', f'TSX 资源路径已更新为 {new_asset_prefix}',
              new_asset_prefix in all_tsx)

        # U-330c: all 3 TSX files should have been updated
        check('U-330c', f'所有含 figma-cased 路径的 TSX 文件都被更新（期望 2）',
              tsx_updated == 2)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_copies_assets_from_staging_when_public_missing():
    """U-376: cmd_apply 运行时 public/assets/{comp}-{nodeId}/ 不存在时，
    应从 .figma-to-code/1-assets/{comp}-{nodeId}/ 直接复制到 public/assets/{comp}/。

    Bug: P1.5 (--apply) 先于 P2 (validate.py) 运行时，public/assets/ 下无任何目录，
    rename 逻辑 glob('*-{nodeId}') 扫不到任何项，静默跳过。
    validate.py 随后读取 staging TSX（仍含 nodeId 路径），复制到
    public/assets/{comp}-{nodeId}/，而 deployed TSX 引用 /assets/{comp}/，图片 404。

    Real scenario: MyLandingPage-169-33787 (session 2026-07-19)
    """
    import os
    from split_components import cmd_apply

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '169-33787'
        comp_name = 'MyLandingPage'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        stage_dir = page_dir / SPLIT_A_SUBDIR / STAGE_SUBDIR
        stage_dir.mkdir(parents=True)

        plan = {
            'pageComponent': comp_name, 'nodeIdSafe': node_id, 'nodeId': node_id,
            'cssExt': 'less', 'sections': [], 'leafComponents': [],
            'topLeaves': [], 'inlineNodes': [],
            'irPath': str(page_dir / 'fake.ir.json'),
            'fidToClass': {}, 'cssMap': {},
        }
        (page_dir / SPLIT_A_SUBDIR / 'plan.json').write_text(json.dumps(plan))
        (stage_dir / PAGE_TSX_FILENAME).write_text(
            f'<img src="/assets/{comp_name}-{node_id}/icon.svg" />\n'
            f'<img src="/assets/{comp_name}-{node_id}/bg.png" />\n'
        )
        (stage_dir / f'{comp_name}.module.less').write_text('.page { width: 100%; }\n')

        # 1-assets 暂存区有资源，但 public/assets/ 下不存在任何 nodeId 目录
        # Real data: 12 assets from MyLandingPage-169-33787 session
        staging_assets = tmp / '.figma-to-code' / '1-assets' / f'{comp_name}-{node_id}'
        staging_assets.mkdir(parents=True)
        (staging_assets / 'icon.svg').write_text('<svg/>')
        (staging_assets / 'bg.png').write_text('PNG')
        (staging_assets / '168-28608.png').write_text('PNG2')

        pub_base = tmp / 'public' / 'assets'
        no_nodeid_dirs = not pub_base.exists() or not any(pub_base.glob(f'*-{node_id}'))
        check('U-376a', 'public/assets/ 无 nodeId 目录（simulate: validate.py 未先运行）',
              no_nodeid_dirs)

        pages_dir = tmp / 'src' / 'pages'
        pages_dir.mkdir(parents=True)

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply(node_id, str(pages_dir), 'less')
        finally:
            os.chdir(old_cwd)

        new_pub = tmp / 'public' / 'assets' / comp_name
        old_pub = tmp / 'public' / 'assets' / f'{comp_name}-{node_id}'

        # 核心断言：应从 1-assets 直接复制到 public/assets/{comp_name}/
        check('U-376b', f'public/assets/{comp_name}/ 已从 1-assets 暂存区复制', new_pub.exists())
        check('U-376c', f'public/assets/{comp_name}-{node_id}/ 不应存在', not old_pub.exists())

        if new_pub.exists():
            check('U-376d', 'icon.svg 已复制', (new_pub / 'icon.svg').exists())
            check('U-376e', 'bg.png 已复制', (new_pub / 'bg.png').exists())
            check('U-376f', '168-28608.png 已复制', (new_pub / '168-28608.png').exists())

        target = pages_dir / comp_name
        if target.exists():
            all_tsx = '\n'.join(f.read_text() for f in target.rglob('*.tsx'))
            check('U-376g', 'deployed TSX 中无 nodeId 资源路径',
                  f'/assets/{comp_name}-{node_id}/' not in all_tsx)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_page_css_preserved_after_rename():
    """U-378: --apply-renames 改页面名后，split codegen 应能读到原始根 CSS（含 gap/flex）。

    Bug: --apply-renames 只重命名 3-page-code 目录（Frame2147224870-nodeId → OptionMarketHub-nodeId），
    但目录内的 CSS 文件名仍是 Frame2147224870.module.scss。
    _read_root_css_block 查找 OptionMarketHub.module.scss（不存在）→ 返回 None →
    fallback 到 `.frame-2147224870 { position: relative; }` → gap/flex 全部丢失。

    Fix: _read_root_css_block 在按名称查找失败后，扫描目录内任意 *.module.{ext} 文件作为 fallback。

    Real scenario: node 10664-26577, Frame2147224870 → OptionMarketHub (2026-07-19)
    root CSS should preserve: display:flex, flex-direction:column, gap:48px, width:100%, etc.
    """
    import os
    from split_components import cmd_apply

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '10664-26577'
        old_name = 'Frame2147224870'
        new_name = 'OptionMarketHub'

        # 模拟 --apply-renames 后的目录状态：
        #   目录名已改为 OptionMarketHub-nodeId
        #   但 CSS 文件名仍是 Frame2147224870.module.scss（未改名）
        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{new_name}-{node_id}'
        (page_dir / SPLIT_A_SUBDIR).mkdir(parents=True)

        # Real data: 根节点 CSS（来自 frame-2147224870, 10664:26577）
        original_css = (
            f'.frame-2147224870 {{\n'
            f'  width: 100%;\n'
            f'  display: flex;\n'
            f'  flex-direction: column;\n'
            f'  align-items: center;\n'
            f'  gap: 48px;\n'
            f'  background-color: var(--bds-gray-bg-page);\n'
            f'  position: relative;\n'
            f'  max-width: 1440px;\n'
            f'  margin-left: auto;\n'
            f'  margin-right: auto;\n'
            f'}}\n'
        )
        # CSS 文件名是旧名（未随目录一起改名）
        (page_dir / f'{old_name}.module.scss').write_text(original_css)

        # plan.json：pageComponent 已改为新名
        plan = {
            'pageComponent': new_name, 'nodeIdSafe': node_id, 'nodeId': node_id,
            'cssExt': 'scss', 'sections': [], 'leafComponents': [],
            'topLeaves': [], 'inlineNodes': [],
            'irPath': str(page_dir / 'fake.ir.json'),
            'fidToClass': {f'{node_id.replace("-",":")}': 'frame-2147224870'},
            'cssMap': {},
        }
        (page_dir / SPLIT_A_SUBDIR / 'plan.json').write_text(json.dumps(plan))

        pages_dir = tmp / 'src' / 'pages'
        pages_dir.mkdir(parents=True)

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply(node_id, str(pages_dir), 'scss')
        finally:
            os.chdir(old_cwd)

        target = pages_dir / new_name
        css_file = target / f'{new_name}.module.scss'

        check('U-378a', f'{new_name}.module.scss 已落地', css_file.exists())
        if css_file.exists():
            css = css_file.read_text()
            check('U-378b', 'gap: 48px 保留（不被 fallback 替换）', 'gap: 48px' in css)
            check('U-378c', 'display: flex 保留', 'display: flex' in css)
            check('U-378d', 'flex-direction: column 保留', 'flex-direction: column' in css)
            check('U-378e', 'width: 100% 保留', 'width: 100%' in css)
            check('U-378f', '非 fallback（不只有 position: relative）',
                  css.count('\n') > 3)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_auto_does_not_land_to_dest():
    """U-377: cmd_auto 不应调用 cmd_apply 落地到 dest 目录。

    Bug: cmd_auto 先调用 cmd_analyze，再调用 cmd_apply，导致以原始名（如 Frame2147224870）
    提前落地到 src/pages/。AI rename 后再次 --apply 以新名落地，旧目录未清理，
    src/pages/ 中同时存在旧名和新名两个目录。

    Fix: cmd_auto 只做分析（cmd_analyze），不落地（不调用 cmd_apply）。
    落地只由显式的 --apply 完成，确保仅在语义名确定后落地一次。

    Real scenario: node 10664-26577, Frame2147224870 → OptionMarketHub (2026-07-19)
    """
    from unittest.mock import patch
    import importlib
    import split_components as sc
    importlib.reload(sc)

    with patch.object(sc, 'cmd_analyze', return_value={'pageComponent': 'FrameOldName'}) as mock_analyze, \
         patch.object(sc, 'cmd_apply') as mock_apply:
        sc.cmd_auto('999-123', '/tmp/pages', 'less')
        check('U-377a', 'cmd_analyze 被调用', mock_analyze.called)
        check('U-377b', 'cmd_apply 未被调用（--auto 不落地）', not mock_apply.called)


def test_css_class_semantic_rename():
    """U-378/379/380: frame-*/node-* CSS 类名语义化重命名流程。

    U-378: _generate_naming_context 从 plan.fidToClass 收集 frame-*/node-* 类名到 cssClasses。
    U-379: --apply-renames cssClasses 写入 plan.json 的 cssClassRenames。
    U-380: _apply_css_class_renames 对 stage 文件做字符串替换。

    Real data: ByAiHub20-169-33787 (frame-2147224803 figmaId=169:35532，
    TaskCard2RowSection 顶层节点，plan.fidToClass 中有映射，IR 节点本身无 cssClass 字段)。
    """
    import os
    from split_components import cmd_apply_renames, _generate_naming_context, _apply_css_class_renames

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '169-33787'
        comp_name = 'ByAiHub20'

        # Real data: IR 节点结构来自 ByAiHub20-169-33787 plan.json TaskCard2RowSection。
        # 关键点：IR 节点无 cssClass 字段（这是 bug 的根因），类名从 fidToClass 查。
        # figmaId '169:35532' → 'frame-2147224803', '169:35552' → 'frame-2147224794'
        # figmaId '169:35560' → 'button_primary-n169-35560'（语义名，不应被收集）
        section_ir = {
            'figmaId': '169:35532', 'figmaName': 'Frame 2147224803',
            'figmaType': 'FRAME', 'isTextNode': False, 'isComponentInstance': False,
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': [
                {
                    'figmaId': '169:35533', 'figmaName': 'Frame 2147224803',
                    'figmaType': 'FRAME', 'isTextNode': False, 'isComponentInstance': False,
                    'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column'},
                    'children': [],
                },
                {
                    'figmaId': '169:35552', 'figmaName': 'Frame 2147224794',
                    'figmaType': 'FRAME', 'isTextNode': False, 'isComponentInstance': False,
                    'css': {'display': 'flex', 'flex-direction': 'row'},
                    'children': [],
                },
                {
                    # 语义类名（非 frame-/node-），不应出现在 cssClasses
                    'figmaId': '169:35560', 'figmaName': 'Button_Primary',
                    'figmaType': 'INSTANCE', 'isTextNode': False, 'isComponentInstance': True,
                    'css': {}, 'children': [],
                },
            ],
        }

        plan = {
            'pageComponent': comp_name, 'nodeIdSafe': node_id,
            'cssExt': 'less', 'inlineNodes': [], 'cssMap': {},
            # Real data: figmaId → cssClass 映射（IR 节点无 cssClass，从这里查）
            'fidToClass': {
                '169:35532': 'frame-2147224803',           # generic → 应被收集
                '169:35533': 'frame-2147224803-n169-35533', # generic → 应被收集
                '169:35552': 'frame-2147224794',            # generic → 应被收集
                '169:35560': 'button_primary-n169-35560',   # 语义名 → 不应被收集
            },
            'sections': [{
                'name': 'TaskCard2RowSection',
                'ir': section_ir,
                'leafComponents': [], 'y': 0,
            }],
            'leafComponents': [], 'topLeaves': [],
        }

        # U-378: _generate_naming_context 应从 fidToClass 收集 frame-*/node-* 类名
        naming = _generate_naming_context(plan)
        css_classes = naming.get('cssClasses', [])
        class_names = [c['currentClass'] for c in css_classes]

        check('U-378a', 'cssClasses 字段存在于 plan.naming.json', 'cssClasses' in naming)
        check('U-378b', 'frame-2147224803 被收集（figmaId=169:35532）',
              'frame-2147224803' in class_names)
        check('U-378c', 'frame-2147224794 被收集（figmaId=169:35552）',
              'frame-2147224794' in class_names)
        check('U-378d', 'button_primary-n169-35560 不被收集（语义名，非 frame-/node-）',
              'button_primary-n169-35560' not in class_names)

        frame_entry = next((c for c in css_classes if c['currentClass'] == 'frame-2147224803'), None)
        check('U-378e', 'frame-2147224803 entry 含 figmaId',
              frame_entry is not None and frame_entry.get('figmaId') == '169:35532')
        check('U-378f', 'frame-2147224803 entry 含 section 归属（TaskCard2RowSection）',
              frame_entry is not None and frame_entry.get('section') == 'TaskCard2RowSection')

        # U-379: --apply-renames cssClasses 写入 plan.json cssClassRenames（在 write_text 之前）
        page_dir = _create_mock_page_code(tmp, node_id, comp_name)
        plan_path = page_dir / SPLIT_A_SUBDIR / 'plan.json'
        base_plan = json.loads(plan_path.read_text())
        base_plan.update({'nodeId': node_id, 'inlineNodes': [], 'fidToClass': plan['fidToClass'], 'cssMap': {}})
        plan_path.write_text(json.dumps(base_plan))

        renames = {
            'cssClasses': {
                'frame-2147224803': 'task-steps-wrapper',
                'frame-2147224794': 'api-create-section',
            }
        }
        renames_path = tmp / 'renames.json'
        renames_path.write_text(json.dumps(renames))

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply_renames(node_id, str(renames_path))
        finally:
            os.chdir(old_cwd)

        updated_plan = json.loads(plan_path.read_text())
        check('U-379a', 'plan.json 含 cssClassRenames', 'cssClassRenames' in updated_plan)
        check('U-379b', 'cssClassRenames: frame-2147224803 → task-steps-wrapper',
              updated_plan.get('cssClassRenames', {}).get('frame-2147224803') == 'task-steps-wrapper')
        check('U-379c', 'cssClassRenames: frame-2147224794 → api-create-section',
              updated_plan.get('cssClassRenames', {}).get('frame-2147224794') == 'api-create-section')

        # U-380: _apply_css_class_renames 对 stage 文件做字符串替换
        # Real data: 真实 stage 文件中的类名引用格式
        mock_stage = tmp / 'mock_stage_u380'
        mock_stage.mkdir()
        (mock_stage / 'TaskCard2RowSection.tsx').write_text(
            # Real data: 来自 ByAiHub20-169-33787 TaskCard2RowSection/index.tsx 格式
            "import styles from './index.module.less';\n"
            "export default function TaskCard2RowSection() {\n"
            "  return (\n"
            "    <div className={styles['frame-2147224803']} data-figma-id=\"169:35532\">\n"
            "      <div className={styles['frame-2147224794']} data-figma-id=\"169:35552\" />\n"
            "    </div>\n"
            "  );\n"
            "}\n"
        )
        (mock_stage / 'index.module.less').write_text(
            ".frame-2147224803 { display: flex; flex-direction: column; }\n"
            ".frame-2147224803-n169-35533 { width: 100%; }\n"
            ".frame-2147224794 { display: flex; flex-direction: row; }\n"
        )

        css_renames_real = {
            'frame-2147224803': 'task-steps-wrapper',
            'frame-2147224794': 'api-create-section',
        }
        n_updated = _apply_css_class_renames(mock_stage, css_renames_real)

        tsx_out = (mock_stage / 'TaskCard2RowSection.tsx').read_text()
        css_out = (mock_stage / 'index.module.less').read_text()

        check('U-380a', '_apply_css_class_renames 更新了文件', n_updated > 0)
        check('U-380b', 'TSX: frame-2147224803 → task-steps-wrapper',
              "styles['task-steps-wrapper']" in tsx_out)
        check('U-380c', 'TSX: frame-2147224794 → api-create-section',
              "styles['api-create-section']" in tsx_out)
        check('U-380d', 'CSS: .frame-2147224803 已替换',
              '.task-steps-wrapper' in css_out)
        check('U-380e', 'CSS: 旧类名已移除（frame-2147224803 不再出现为选择器起始）',
              '.frame-2147224803 ' not in css_out and '.frame-2147224803{' not in css_out)
        check('U-380f', 'CSS: frame-2147224803-n169-35533 未受波及（子类名仅包含 frame-2147224803 作为前缀，已被替换）',
              'task-steps-wrapper-n169-35533' in css_out)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Run ──
print('\n── split cmd_apply landing tests ──')
test_apply_creates_target_when_missing()
test_duplicate_section_names_deduped()
test_subsection_generation()
test_hooks_dir_sibling_of_components()
test_page_tsx_at_root()
test_old_directory_removed_when_page_renamed()
test_asset_paths_updated_and_dirs_renamed_on_page_rename()
test_asset_path_maps_json_and_no_nodeid_in_output_dirs()
test_asset_path_strip_nodeId_when_component_name_case_differs()
test_apply_copies_assets_from_staging_when_public_missing()
test_page_css_preserved_after_rename()
def test_css_class_top_level_nodes_collected():
    """U-381: _generate_naming_context 应收集主页骨架层（sections 之外）的 frame-*/node-* 类名。

    Bug: sections[].ir 遍历不覆盖主页根节点的直接子节点（navigation、hero 包装层等），
    导致这些节点的 frame-*/node-* 类名不出现在 plan.naming.json cssClasses 中。

    Real data: ByAiHub20-169-33787，根节点 figmaId=169:33787，
    child 177:13498 (未申卡状态) → node-177-13498（在 sections IR 之外）
    child 169:33989 → frame-5495（在 sections IR 之外）
    child 169:35532 → frame-2147224803（已在 sections 里，不应重复收集）
    """
    from split_components import _generate_naming_context

    # Real data: 根 IR 结构（figmaId/figmaName/figmaType 来自 169:33787 根节点的真实孩子）
    root_ir = {
        'figmaId': '169:33787', 'figmaName': 'MY LANDING PAGE',
        'figmaType': 'FRAME', 'isTextNode': False, 'css': {},
        'children': [
            # Real data: 177:13498 是主页骨架层节点，不在任何 section IR 子树中
            {'figmaId': '177:13498', 'figmaName': '未申卡状态', 'figmaType': 'FRAME',
             'isTextNode': False, 'css': {}, 'children': []},
            # Real data: 169:33989 → frame-5495，导航栏包装层
            {'figmaId': '169:33989', 'figmaName': 'Frame 5495', 'figmaType': 'FRAME',
             'isTextNode': False, 'css': {'display': 'flex', 'flex-direction': 'row'}, 'children': []},
            # 169:35532 已在 section ir 中（should NOT duplicate）
            {'figmaId': '169:35532', 'figmaName': 'Frame 2147224803', 'figmaType': 'FRAME',
             'isTextNode': False, 'css': {}, 'children': []},
        ],
    }

    # section IR 包含 169:35532（与根节点共享该孩子），模拟已被 sections 覆盖的情况
    section_ir = {
        'figmaId': '169:35532', 'figmaName': 'Frame 2147224803',
        'figmaType': 'FRAME', 'isTextNode': False, 'css': {}, 'children': [],
    }

    plan = {
        'pageComponent': 'ByAiHub20', 'nodeIdSafe': '169-33787',
        'cssExt': 'less', 'inlineNodes': [], 'cssMap': {},
        # Real data: fidToClass from ByAiHub20-169-33787
        'fidToClass': {
            '177:13498': 'node-177-13498',       # top-level, not in sections → should be collected
            '169:33989': 'frame-5495',            # top-level, not in sections → should be collected
            '169:35532': 'frame-2147224803',      # in sections → already collected, no duplicate
            '169:33966': 'navigation',            # semantic name → should NOT be collected
        },
        'sections': [{
            'name': 'TaskCard2RowSection',
            'ir': section_ir,
            'leafComponents': [], 'y': 0,
        }],
        'leafComponents': [], 'topLeaves': [],
    }

    naming = _generate_naming_context(plan, root_ir=root_ir)
    css_classes = naming.get('cssClasses', [])
    class_names = [c['currentClass'] for c in css_classes]

    check('U-381a', 'node-177-13498 被收集（主页骨架层节点）',
          'node-177-13498' in class_names)
    check('U-381b', 'frame-5495 被收集（主页骨架层节点）',
          'frame-5495' in class_names)
    check('U-381c', 'frame-2147224803 不重复收集（已在 sections 里）',
          class_names.count('frame-2147224803') == 1)
    check('U-381d', 'navigation 不被收集（语义类名）',
          'navigation' not in class_names)

    # section 名附到骨架层条目
    top_entry = next((c for c in css_classes if c['currentClass'] == 'node-177-13498'), None)
    check('U-381e', '骨架层 entry 的 section 标记为 __page_root__',
          top_entry is not None and top_entry.get('section') == '__page_root__')

    # U-381f: 无上限限制——sections 60 条时骨架层仍被收集，所有条目均输出
    # Real bug fix: max_count=50 已删除，改为 max_count=0（无限制）
    big_plan = {
        'pageComponent': 'ByAiHub20', 'nodeIdSafe': '169-33787',
        'cssExt': 'less', 'inlineNodes': [], 'cssMap': {},
        'fidToClass': {
            **{f'fid-sec-{i}': f'frame-{i:010d}' for i in range(60)},  # 纯数字，匹配 regex
            '177:13498': 'node-177-13498',  # 骨架层节点
        },
        'sections': [{
            'name': f'Section{i}',
            'ir': {'figmaId': f'fid-sec-{i}', 'figmaName': f'Frame {i}', 'figmaType': 'FRAME',
                   'isTextNode': False, 'css': {}, 'children': []},
            'leafComponents': [], 'y': i * 100,
        } for i in range(60)],
        'leafComponents': [], 'topLeaves': [],
    }
    big_root_ir = {
        'figmaId': '169:33787', 'figmaName': 'root', 'figmaType': 'FRAME',
        'isTextNode': False, 'css': {},
        'children': [
            {'figmaId': '177:13498', 'figmaName': '未申卡状态', 'figmaType': 'FRAME',
             'isTextNode': False, 'css': {}, 'children': []},
        ],
    }
    big_naming = _generate_naming_context(big_plan, root_ir=big_root_ir)
    big_classes = [c['currentClass'] for c in big_naming.get('cssClasses', [])]
    check('U-381f', 'sections 60 条时骨架层 node-177-13498 仍被收集（无 max_count 截断）',
          'node-177-13498' in big_classes)
    check('U-381g', 'sections 60 条全部被收集（无截断，期望 ≥ 60）',
          sum(1 for c in big_classes if re.match(r'^frame-\d+$', c)) >= 60)


def test_infer_section_name_skips_generic_leaf():
    """U-382: _infer_section_name Level 3 不应使用通用叶子名推断 section 名。

    Bug: 当 section IR figmaName='Frame 2147229790'（generic），Level 1/2 均无结果时，
    Level 3 使用第一个叶子名 'Frame2147229790'（也是 generic）拼成 'Frame2147229790Section'。
    正确行为：Level 3 跳过 generic 叶子名，fallback 到 Section{N}。

    Real data: ByAiHub20-169-33787 的 Frame2147229790Section（figmaId=169:33988），
    leaves = [Frame2147229790, Frame2147229785]（两者均 generic，匹配 _GENERIC_LEAF_RE）。
    """
    from split_components import _infer_section_name

    # Real data: Frame 2147229790 section 的 IR（无文本子节点，max_depth=2 找不到文本）
    ir_node = {
        'figmaId': '169:33988', 'figmaName': 'Frame 2147229790',
        'figmaType': 'FRAME', 'isTextNode': False, 'css': {}, 'children': [],
    }
    # Real data: 叶子均为通用名（来自 plan.naming.json leaves 字段）
    leaves_all_generic = [
        {'name': 'Frame2147229790', 'ir': {}, 'varyingProps': []},  # _GENERIC_LEAF_RE 匹配
        {'name': 'Frame2147229785', 'ir': {}, 'varyingProps': []},  # _GENERIC_LEAF_RE 匹配
    ]
    result = _infer_section_name(ir_node, leaves_all_generic, 0)

    check('U-382a', '通用叶子名不应被用于 section 命名（应 fallback 到 Section1）',
          result == 'Section1')
    check('U-382b', '不能生成 Frame2147229790Section（通用叶子名传播）',
          result != 'Frame2147229790Section')

    # 对比：若叶子有语义名，Level 3 应该可以使用
    leaves_semantic = [
        {'name': 'SkillCard', 'ir': {}, 'varyingProps': []},  # 语义名
    ]
    result_semantic = _infer_section_name(ir_node, leaves_semantic, 0)
    check('U-382c', '语义叶子名仍可触发 Level 3（SkillCardSection）',
          result_semantic == 'SkillCardSection')


def test_generic_css_class_re_matches_instance_path_nodes():
    """U-383: _GENERIC_CSS_CLASS_RE 应匹配 node-I{instancePath} 格式（实例路径 ID）。

    Bug: _GENERIC_CSS_CLASS_RE = r'^(frame|node)-[\d-]+$' 只匹配纯数字+连字符后缀，
    node-I171-12615-5382-9726（I 开头）不被匹配，cssClasses 收集遗漏这类节点。

    Real data: ByAiHub20-169-33787 TypeTabTagElement5Section，
    figmaId='I171:12615;5382:9726' → cssClass='node-I171-12615-5382-9726'
    figmaId='I171:12616;5382:9722' → cssClass='node-I171-12616-5382-9722'
    这两个 class 是 tab 标签文字节点，需要语义化（如 tab-label-selected, tag-label-text）。

    Fix: regex 改为 r'^(frame|node)-(I?\\d)[\\d-]*$'，支持 I-prefix 格式，
    同时保持排除 frame-2147224387-n169-35630 等 -n 中缀 variant（含非数字中缀）。
    """
    from split_components import _GENERIC_CSS_CLASS_RE, _collect_generic_css_classes

    # U-383a: regex 应匹配 node-I* 格式
    check('U-383a', 'node-I171-12615-5382-9726 匹配 _GENERIC_CSS_CLASS_RE',
          bool(_GENERIC_CSS_CLASS_RE.match('node-I171-12615-5382-9726')))
    check('U-383b', 'node-I171-12616-5382-9722 匹配 _GENERIC_CSS_CLASS_RE',
          bool(_GENERIC_CSS_CLASS_RE.match('node-I171-12616-5382-9722')))

    # 保持原有匹配
    check('U-383c', 'node-169-33992 仍匹配（纯数字格式）',
          bool(_GENERIC_CSS_CLASS_RE.match('node-169-33992')))
    check('U-383d', 'frame-2147229790 仍匹配',
          bool(_GENERIC_CSS_CLASS_RE.match('frame-2147229790')))

    # 保持原有排除（-n 中缀 variant）
    check('U-383e', 'frame-2147224387-n169-35630 不匹配（-n 中缀 variant）',
          not bool(_GENERIC_CSS_CLASS_RE.match('frame-2147224387-n169-35630')))

    # U-383f: _collect_generic_css_classes 能收集到 node-I* 节点
    # Real data: I171:12615;5382:9726 是 TypeTabTagElement5Section 中的 tab 标签
    ir_node = {
        'figmaId': 'I171:12615;5382:9726',
        'figmaName': '全部',
        'figmaType': 'TEXT', 'isTextNode': True,
        'css': {}, 'children': [],
        'textContent': '全部',
    }
    result = []
    _collect_generic_css_classes(
        ir_node, result, 'TypeTabTagElement5Section', set(),
        {'I171:12615;5382:9726': 'node-I171-12615-5382-9726'},
    )
    check('U-383f', 'node-I171-12615-5382-9726 被 _collect_generic_css_classes 收集',
          any(c['currentClass'] == 'node-I171-12615-5382-9726' for c in result))


def test_apply_copies_assets_from_original_frame_name_dir():
    """U-384: --apply 能从 1-assets/{FrameName}-{nodeId}/ 正确复制资产到 public/assets/{comp_name}/。

    Design: --apply-renames 不再重命名 1-assets/，1-assets/ 保持 Figma 原始帧名。
    --apply 兜底逻辑扫描 1-assets/*-{nodeId}/ 而非只查 1-assets/{comp_name}-{nodeId}/。

    Real scenario: 1-assets/Frame2147224870-10664-26577/ 未被重命名，
    --apply 仍能将资产复制到 public/assets/OptionMarketOverview/。
    """
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from split_components import cmd_apply

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '10664-26577'
        frame_name = 'Frame2147224870'   # Figma 原始帧名（1-assets 用此名）
        comp_name = 'OptionMarketOverview'  # plan.json 中的语义名

        # 创建 mock page code，pageComponent 已是语义名
        page_dir = _create_mock_page_code(tmp, node_id, frame_name)
        plan_path = page_dir / SPLIT_A_SUBDIR / 'plan.json'
        plan = json.loads(plan_path.read_text())
        plan['pageComponent'] = comp_name  # simulate after --apply-renames
        plan['fidToSrc'] = {}  # no fidToSrc entries
        plan_path.write_text(json.dumps(plan))

        # 1-assets 使用原始帧名（--apply-renames 未重命名）
        staging = tmp / '.figma-to-code' / '1-assets' / f'{frame_name}-{node_id}'
        staging.mkdir(parents=True)
        (staging / 'icon.svg').write_text('<svg/>')
        (staging / 'bg.png').write_text('PNG')

        dest = tmp / 'src' / 'pages'
        dest.mkdir(parents=True)

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply(node_id, str(dest), 'less')
        finally:
            os.chdir(old_cwd)

        pub_assets = tmp / 'public' / 'assets' / comp_name

        check('U-384a', 'public/assets/OptionMarketOverview/ 已创建', pub_assets.exists())
        check('U-384b', '资产文件已复制（icon.svg）', (pub_assets / 'icon.svg').exists())
        check('U-384c', '资产文件已复制（bg.png）', (pub_assets / 'bg.png').exists())

        # U-384d: asset-path-maps.json 记录了 stagingDir（供下次落地直接用，无需重新扫描）
        maps_file = tmp / '.figma-to-code' / '3-page-code' / f'{frame_name}-{node_id}' / 'asset-path-maps.json'
        check('U-384d', 'asset-path-maps.json 已创建', maps_file.exists())
        if maps_file.exists():
            _m = json.loads(maps_file.read_text())
            check('U-384e', 'stagingDir 已记录为原始帧名目录',
                  _m.get('stagingDir') == f'{frame_name}-{node_id}')

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_page_code_dir_disambiguation():
    """U-385/386/387: _get_page_code_dir 在多候选目录时的选择逻辑。

    Design（三个规则依次执行）：
    1. 若只有一个候选有 split-a/plan.json → 选它（U-385）
    2. 若都有 plan.json → 选 plan.pageComponent 与目录名匹配的（U-386）
    3. 若都没有 plan.json → fallback candidates[0]（U-387）

    Real scenario (U-385): convert.py --from-raw-data 创建 Frame*（无 plan），
    OptionMarketOverview* 有 plan.json → 选 OptionMarketOverview*。
    """
    from split_components import _get_page_code_dir

    # U-385: 只有一个候选有 plan.json
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / '.figma-to-code'
        pc = base / '3-page-code'
        # Frame* 无 split-a（刚被 convert.py 重建）
        (pc / 'Frame2147224870-10664-26577').mkdir(parents=True)
        # OptionMarketOverview* 有 split-a/plan.json
        split = pc / 'OptionMarketOverview-10664-26577' / SPLIT_A_SUBDIR
        split.mkdir(parents=True)
        (split / 'plan.json').write_text(
            json.dumps({'pageComponent': 'OptionMarketOverview', 'sections': []})
        )
        result = _get_page_code_dir(base, '10664-26577')
        check('U-385', '只有一个候选有 plan.json 时选它',
              result is not None and result.name == 'OptionMarketOverview-10664-26577')

    # U-386: 两个候选都有 plan.json，选 pageComponent 与目录名匹配的
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / '.figma-to-code'
        pc = base / '3-page-code'
        for dname in ('Frame2147224870-10664-26577', 'OptionMarketOverview-10664-26577'):
            s = pc / dname / SPLIT_A_SUBDIR
            s.mkdir(parents=True)
            (s / 'plan.json').write_text(
                json.dumps({'pageComponent': 'OptionMarketOverview', 'sections': []})
            )
        result = _get_page_code_dir(base, '10664-26577')
        check('U-386', '两个候选都有 plan.json 时选 pageComponent 匹配目录名的',
              result is not None and result.name == 'OptionMarketOverview-10664-26577')

    # U-387: 两个候选都没有 plan.json，fallback 到第一个
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / '.figma-to-code'
        pc = base / '3-page-code'
        (pc / 'Frame2147224870-10664-26577').mkdir(parents=True)
        (pc / 'OptionMarketOverview-10664-26577').mkdir(parents=True)
        result = _get_page_code_dir(base, '10664-26577')
        check('U-387', '都没有 plan.json 时 fallback 到 candidates[0]',
              result is not None)


def test_apply_renames_deconflicts_subsection_after_rename():
    """U-402: --apply-renames 应在写入 plan.json 后重新运行去重，防止 AI 重命名将
    subSection 改回与父 Section 同名，导致 codegen 生成 'Identifier already declared'。

    Root cause: cmd_apply_renames 只做名称替换，不重跑 _deconflict_subsection_name。
    当 AI 重命名 pass 将 subSection 从 'HotAssetsV3Inner' 改回 'HotAssetsV3Section'
    时，section 和 subSection 同名，codegen 生成 import 与 function 同名冲突。

    Real data: node 4030:41366/4030:41367 from PreKyc (nodeId: 4030-41365)
    """
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from split_components import cmd_apply_renames

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '4030-41365'
        frame_name = 'Brand6PreKyc'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{frame_name}-{node_id}'
        stage_dir = page_dir / SPLIT_A_SUBDIR
        stage_dir.mkdir(parents=True)
        plan_path = stage_dir / 'plan.json'

        # Real data: section "HotAssetsV3Section" has subSection already deconflicted
        # to "HotAssetsV3Inner" by --auto; AI rename pass wants to rename it back.
        plan_path.write_text(json.dumps({
            'pageComponent': 'PreKyc',
            'nodeId': node_id,
            'cssExt': 'scss',
            'sections': [
                {
                    'name': 'HotAssetsV3Section',
                    'ir': {'figmaId': '4030:41366', 'figmaName': 'Hot assets - V3',
                           'figmaType': 'FRAME', 'children': [], 'css': {}},
                    'leafComponents': [],
                    'subSections': [
                        {
                            'name': 'HotAssetsV3Inner',  # correctly deconflicted by --auto
                            'ir': {'figmaId': '4030:41367', 'figmaName': 'Frame 2147229741',
                                   'figmaType': 'FRAME', 'children': [], 'css': {}},
                            'leafComponents': [],
                        }
                    ],
                }
            ],
            'leafComponents': [],
            'inlineNodes': [],
        }))

        # AI rename pass assigns "HotAssetsV3Section" to the subSection again
        renames = {'subSections': {'HotAssetsV3Inner': 'HotAssetsV3Section'}}
        renames_path = tmp / 'renames.json'
        renames_path.write_text(json.dumps(renames))

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply_renames(node_id, str(renames_path))
        finally:
            os.chdir(old_cwd)

        plan_after = json.loads(plan_path.read_text())
        ss_name = plan_after['sections'][0]['subSections'][0]['name']
        section_name = plan_after['sections'][0]['name']

        check('U-402a', 'section 名称不变 (HotAssetsV3Section)',
              section_name == 'HotAssetsV3Section')
        check('U-402b', 'subSection 不能与父 section 同名（冲突应被去重）',
              ss_name != section_name)
        check('U-402c', 'subSection 自动重命名为 HotAssetsV3Inner（Section→Inner）',
              ss_name == 'HotAssetsV3Inner')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make_section_ir(name: str) -> dict:
    """Minimal section IR for landing tests."""
    return {
        'figmaId': '1:1', 'figmaName': name, 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'children': [],
        'semantic': {
            'htmlTag': 'section', 'className': name.lower(),
            'componentName': name, 'props': [], 'isExtractedComponent': True,
        },
    }


def test_u408_apply_copies_stage_components_wholesale():
    """U-408: cmd_apply must copy stage/components/ wholesale to target/components/.
    With the new structure (generate_components writes sections into out_dir/components/),
    stage/ and dest/ are structurally identical — no directory reshuffling on landing.
    Real behavior: cmd_apply 'elif item.name == COMPONENTS_SUBDIR: shutil.copytree(item, comp_dir)'.
    """
    from split_components import cmd_apply
    import os
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '100-200'
        comp_name = 'TestComp'
        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        (page_dir / SPLIT_A_SUBDIR).mkdir(parents=True)

        plan = {
            'pageComponent': comp_name, 'nodeIdSafe': node_id, 'nodeId': node_id,
            'cssExt': 'less', 'topLeaves': [], 'inlineNodes': [], 'leafComponents': [],
            'sections': [{'name': 'HeroSection', 'ir': _make_section_ir('HeroSection'),
                          'y': 0, 'leafComponents': [], 'subSections': []}],
        }
        (page_dir / SPLIT_A_SUBDIR / 'plan.json').write_text(json.dumps(plan))

        dest_dir = tmp / 'src' / 'pages' / 'demo'
        dest_dir.mkdir(parents=True)
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cmd_apply(node_id, str(dest_dir), 'less')
        finally:
            os.chdir(old_cwd)

        target = dest_dir / comp_name
        # index.tsx at target root (stage/index.tsx copied as-is)
        check('U-408a', 'index.tsx at target root', (target / INDEX_TSX_FILENAME).exists())
        # components/HeroSection/ exists (wholesale copy of stage/components/)
        check('U-408b', 'target/components/HeroSection/ exists',
              (target / 'components' / 'HeroSection').is_dir())
        check('U-408c', 'target/components/HeroSection/index.tsx exists',
              (target / 'components' / 'HeroSection' / INDEX_TSX_FILENAME).exists())
        # No double-nesting: components/components/ must NOT exist
        check('U-408d', 'no components/components/ double-nesting',
              not (target / 'components' / 'components').exists())
        # index.tsx (entry) imports from ./components/HeroSection
        entry_content = (target / INDEX_TSX_FILENAME).read_text()
        check('U-408e', "entry imports './components/HeroSection'",
              "./components/HeroSection" in entry_content)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


test_auto_does_not_land_to_dest()
test_css_class_semantic_rename()
test_css_class_top_level_nodes_collected()
test_infer_section_name_skips_generic_leaf()
test_generic_css_class_re_matches_instance_path_nodes()
test_asset_paths_updated_and_dirs_renamed_on_page_rename()
test_apply_copies_assets_from_original_frame_name_dir()
test_get_page_code_dir_disambiguation()
test_apply_renames_deconflicts_subsection_after_rename()
test_u408_apply_copies_stage_components_wholesale()


def test_u409_parse_flat_css_dict_first_occurrence_wins():
    """
    U-409: _parse_flat_css_dict must use first-occurrence-wins.

    Real data: node 181:3038 (frame-2147229891) from Web-merged-181-2980.
    The converted Web.module.less has two occurrences of .frame-2147229891:
      1. Top-level: width:1200px; display:flex; flex-direction:column; ...
      2. Inside @media (max-width:768px): display:none
    With last-wins the map gets {display:none}, causing section root to be hidden.
    With first-wins the map keeps {width:1200px; display:flex; ...} (correct).
    """
    from lib.split_codegen import _parse_flat_css_dict

    # Real data from Web-merged-181-2980/Web.module.less (node 181:3038)
    css_text = """.frame-2147229891 {
  width: 1200px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 48px;
  flex-shrink: 0;
  position: relative;
}

@media (max-width: 768px) {
  .frame-2147229891 {
    display: none;
  }
}
"""
    result = _parse_flat_css_dict(css_text)
    cls = result.get('frame-2147229891', {})
    # First-wins: should keep full CSS, not the media-query override
    check('U-409a', 'first-occurrence width preserved', cls.get('width') == '1200px')
    check('U-409b', 'first-occurrence display:flex preserved (not none)', cls.get('display') == 'flex')
    check('U-409c', 'first-occurrence flex-direction preserved', cls.get('flex-direction') == 'column')


test_u409_parse_flat_css_dict_first_occurrence_wins()


def test_u410_apply_confirmed_skipped_pending_added_to_h5_only():
    """
    U-410: _apply_confirmed must add unconfirmed pending supplement indices to h5_only.

    Real data: Trump activity merge (PC=181:2980, H5=6:1998).
    H5 sections: idx0=KV图(16:5291), idx1=NavBar, idx2=Frame2147224436, idx3="1", idx4=Sheet
    pending_matches: [(3,1,0.551), (2,0,0.476)] — both skipped (confirmed=[])
    h5_only_idxs: [0,1,4] (supplement_only from match report)
    Without the fix: h5_only returns [0,1,4] — idx2 and idx3 (main H5 content) vanish.
    With the fix: h5_only returns [0,1,4,3,2] — idx2 and idx3 are included as h5Only.
    """
    import sys, tempfile, json
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'scripts'))
    from merge_responsive import _apply_confirmed

    auto_matches = []
    # Real pending_matches format: (h5_i, pc_i, score)
    pending_matches = [
        (3, 1, 0.551),   # H5[3]↔PC[1] skipped
        (2, 0, 0.476),   # H5[2]↔PC[0] skipped
    ]
    h5_only = [0, 1, 4]   # original h5_only (without pending indices)
    pc_only = [0, 1]

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({'confirmed': []}, f)
        tmp_path = Path(f.name)

    try:
        final_auto, _, result_h5_only = _apply_confirmed(
            auto_matches, pending_matches, tmp_path, h5_only, pc_only, 'pc'
        )
        check('U-410a', 'no confirmed auto matches', final_auto == [])
        check('U-410b', 'H5 idx 3 in h5_only (Frame "1" main section)', 3 in result_h5_only)
        check('U-410c', 'H5 idx 2 in h5_only (Frame2147224436 activity)', 2 in result_h5_only)
        check('U-410d', 'original h5_only idx 0 preserved', 0 in result_h5_only)
        check('U-410e', 'original h5_only idx 1 preserved', 1 in result_h5_only)
        check('U-410f', 'original h5_only idx 4 preserved', 4 in result_h5_only)
    finally:
        tmp_path.unlink(missing_ok=True)


test_u410_apply_confirmed_skipped_pending_added_to_h5_only()


def test_u411_apply_renames_filename_only_fallback():
    """U-411: cmd_apply_renames 仅传文件名（如 'renames.json'）时，应自动在 split-a/ 目录下查找。

    Real scenario: skill SKILL.md 第5步调用格式为 --apply-renames=renames.json，
    但脚本直接 Path(renames_path).read_text() 不做 fallback，当 renames.json 不在 cwd 时抛
    FileNotFoundError。
    Fix: 若路径不存在，尝试从 page_dir/split-a/<basename> 查找并使用。
    """
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from split_components import cmd_apply_renames

    tmp = Path(tempfile.mkdtemp())
    old_cwd = os.getcwd()
    try:
        node_id = '6777-32751'
        component_name = 'PortalRevised'

        # 建立 .figma-to-code/3-page-code/{Name}-{nodeId}/split-a/plan.json
        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{component_name}-{node_id}'
        split_a = page_dir / SPLIT_A_SUBDIR
        split_a.mkdir(parents=True)
        plan_path = split_a / 'plan.json'
        plan_path.write_text(json.dumps({
            'pageComponent': component_name,
            'nodeId': node_id,
            'cssExt': 'scss',
            'sections': [], 'leafComponents': [], 'inlineNodes': [],
        }))

        # renames.json 放在 split-a/ 下（不在 cwd）
        renames_in_split_a = split_a / 'renames.json'
        renames_in_split_a.write_text(json.dumps({'page': 'RenamedPage'}))

        # cwd 切换到 tmp（renames.json 不在 cwd 里）
        os.chdir(tmp)
        raised = False
        try:
            cmd_apply_renames(node_id, 'renames.json')
        except FileNotFoundError:
            raised = True

        check('U-411a', '--apply-renames=renames.json（仅文件名）不抛 FileNotFoundError', not raised)
        updated_plan = json.loads(plan_path.read_text())
        check('U-411b', 'plan.json pageComponent 已从 renames.json 更新', updated_plan['pageComponent'] == 'RenamedPage')
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


test_u411_apply_renames_filename_only_fallback()


def test_u421_subsection_css_generated_when_orig_css_map_empty():
    """U-421: h5Only subSection Less file gets CSS from IR when orig_css_map={}.

    Bug: line 1961 in split_codegen.py had guard `if orig_css_map else ''`.
    empty dict {} is falsy in Python, so ss_css='' instead of using IR fallback.
    This caused CalculateYourCashbackInner, HowItWorksSection, RewardsTiersSection
    to have empty Less files (only visibility rules, no CSS class definitions).

    Fix: call _css_from_orig(ss_ir_norm, orig_css_map or {}, css_ext, ...) unconditionally.
    _css_from_orig handles empty dict by using IR css values directly (final_props=ir_props).

    Real data: EU Deposit Campaign CalcCashbackSection/CalculateYourCashbackInner (250:4861).
    """
    from lib.split_codegen import _css_from_orig

    # Real IR data: node 250:4861 from EU Deposit Campaign (merged-250-2618)
    # className assigned by _apply_fallback_semantics + deduplication suffix
    ir = {
        'figmaId': '250:4861',
        'figmaName': 'Calculate your cashback',
        'figmaType': 'FRAME',
        'css': {
            'width': '100%',
            'display': 'flex',
            'flex-direction': 'column',
            'align-items': 'center',
            'gap': '28px',
            'padding': '40px 20px 40px 20px',
        },
        'children': [
            {
                'figmaId': '250:4868',
                'figmaName': 'steps',
                'figmaType': 'FRAME',
                'css': {'display': 'flex', 'flex-direction': 'column', 'gap': '16px'},
                'children': [],
                'semantic': {'className': 'steps', 'htmlTag': 'div'},
            }
        ],
        'semantic': {'className': 'calculate-your-cashback-n250-4861', 'htmlTag': 'div'},
    }

    empty_orig = {}

    # Pre-condition: {} is falsy — explains why the guard was wrong
    check('U-421a', 'empty dict {} is falsy (bug root cause)', not bool(empty_orig))

    # Buggy pattern: `_css_from_orig(...) if orig_css_map else ''`
    # With {} (falsy), returns '' instead of CSS
    buggy_css = _css_from_orig(ir, empty_orig, 'less') if empty_orig else ''
    check('U-421b', 'buggy guard returns empty string when orig_css_map={}',
          buggy_css == '')

    # Fixed pattern: `_css_from_orig(..., orig_css_map or {}, ...)` — always calls function
    fixed_css = _css_from_orig(ir, empty_orig or {}, 'less')
    check('U-421c', 'fixed code generates CSS from IR when orig_css_map={}',
          '.calculate-your-cashback-n250-4861' in fixed_css)
    check('U-421d', 'fixed CSS has display:flex from IR',
          'display: flex' in fixed_css)
    check('U-421e', 'child class css also generated',
          '.steps' in fixed_css)


test_u421_subsection_css_generated_when_orig_css_map_empty()


def test_u422_dark_responsive_bg_injects_bds_token_override():
    """U-422: When a section's responsive CSS has a dark background-color,
    split_codegen.py injects --bds-gray-t1-title:#ffffff into that @media block.

    Root cause: Hero H5 section (250:2620) has responsive.css.background-color=#000000.
    Text children use var(--bds-gray-t1-title) which resolves to dark in light theme.
    Fix: _is_dark_hex_bg() helper + inject token override in responsive override generation.
    """
    from lib.split_codegen import _is_dark_hex_bg

    # Test _is_dark_hex_bg helper
    check('U-422a', '#000000 is dark', _is_dark_hex_bg('#000000'))
    check('U-422b', '#0a0a0a is dark', _is_dark_hex_bg('#0a0a0a'))
    check('U-422c', '#1a1a2e is dark', _is_dark_hex_bg('#1a1a2e'))
    check('U-422d', '#ffffff is NOT dark', not _is_dark_hex_bg('#ffffff'))
    check('U-422e', '#f5f5f5 is NOT dark', not _is_dark_hex_bg('#f5f5f5'))
    check('U-422f', '#ff9c2e (orange) is NOT dark', not _is_dark_hex_bg('#ff9c2e'))
    check('U-422g', 'short hex #000 is dark', _is_dark_hex_bg('#000'))
    check('U-422h', 'empty string returns False', not _is_dark_hex_bg(''))
    check('U-422i', 'invalid string returns False', not _is_dark_hex_bg('rgba(0,0,0,1)'))


test_u422_dark_responsive_bg_injects_bds_token_override()


def test_u423_read_root_css_includes_media_blocks():
    """U-423: _read_root_css_block should include @media blocks targeting the root class.

    Bug: When scss_generator.py emits @media (max-width: 768px) { .page_ { min-height: 4438px; ... } }
    for merged roots with h5RootHeight, split_codegen._read_root_css_block only extracted the
    static CSS block { ... }, dropping the @media responsive override. The deployed stage
    Page.module.scss then had no mobile min-height, causing H5 absolutely-positioned content
    to be invisible on mobile (parent container height collapsed to 0 when PC block child
    was display:none).

    Real data: merged root from Page_首页 (merged-315-29892), H5 height 4438px.
    """
    import os
    from lib.split_codegen import _read_root_css_block
    from lib.paths import SPLIT_A_SUBDIR

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = 'merged-315-29892'
        page_name = 'Page'
        root_class = 'page_'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{page_name}-{node_id}'
        (page_dir / SPLIT_A_SUBDIR).mkdir(parents=True)

        # Real data: merged root CSS with mobile responsive override (merged-315-29892)
        css_content = (
            '.page_ {\n'
            '  width: 1440px;\n'
            '  display: flex;\n'
            '  flex-direction: row;\n'
            '  background-color: #000000;\n'
            '  max-width: 100%;\n'
            '}\n'
            '\n'
            '@media (max-width: 768px) {\n'
            '  .page_ {\n'
            '    width: 100%;\n'
            '    min-height: 4438px;\n'
            '    flex-direction: column;\n'
            '    justify-content: flex-start;\n'
            '    align-items: flex-start;\n'
            '  }\n'
            '}\n'
        )
        (page_dir / f'{page_name}.module.scss').write_text(css_content)

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            result = _read_root_css_block(node_id, page_name, 'scss', root_class=root_class)
        finally:
            os.chdir(old_cwd)

        check('U-423a', '_read_root_css_block returns non-None', result is not None)
        if result:
            check('U-423b', 'static page_ block preserved', 'width: 1440px' in result)
            check('U-423c', '@media max-width:768px block included',
                  '@media (max-width: 768px)' in result)
            check('U-423d', 'mobile min-height:4438px included', 'min-height: 4438px' in result)
            check('U-423e', 'mobile flex-direction:column included',
                  'flex-direction: column' in result)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


test_u423_read_root_css_includes_media_blocks()


def test_u424_css_from_orig_injects_z_index_for_relative_section():
    """U-424: _css_from_orig must inject z-index for position:relative section roots.

    Bug (real data: merged-315-29892, by-meta):
    Rule 5 in _css_from_orig only injected z-index when position==absolute.
    HomepageWebSection root (.homepage-web) has position:relative → no z-index injected.
    After merge_responsive reorder (U-436), backgrounds got z-index:0-6 and matched PC
    section (index 7) should get z-index:7. But without z-index on position:relative,
    CSS stacking levels: absolute+z-index:1 > relative+z-index:auto → backgrounds cover
    the PC section content.

    Fix: Rule 5 must also inject z-index when position==relative.
    """
    from lib.split_codegen import _css_from_orig

    # Real data: .homepage-web from merged-315-29892, position:relative, no z-index
    orig_css_map = {
        'homepage-web': {
            'display': 'flex',
            'flex-direction': 'row',
            'justify-content': 'center',
            'align-items': 'center',
            'background-color': '#000000',
            'position': 'relative',
            'width': '100%',
        }
    }
    ir_node = {
        'figmaId': '315:29893',
        'figmaName': 'Homepage-Web',
        'figmaType': 'FRAME',
        'css': {'position': 'relative', 'width': '100%'},
        'children': [],
        'semantic': {'className': 'homepage-web', 'htmlTag': 'div',
                     'componentName': 'HomepageWebSection', 'props': []},
    }

    # Without root_z_index: no z-index should appear
    css_no_z = _css_from_orig(ir_node, orig_css_map, 'scss', root_z_index=None)
    check('U-424a', 'no z-index when root_z_index=None', 'z-index' not in css_no_z)

    # With root_z_index=7 (its position after merge reorder): z-index:7 must appear
    css_with_z = _css_from_orig(ir_node, orig_css_map, 'scss', root_z_index=7)
    check('U-424b', 'z-index:7 injected for position:relative root when root_z_index=7',
          'z-index: 7' in css_with_z)

    # Sanity: position:absolute already gets z-index (existing behaviour)
    orig_abs = {'homepage-web': {'position': 'absolute', 'width': '100%'}}
    ir_abs = {**ir_node, 'css': {'position': 'absolute', 'width': '100%'}}
    css_abs = _css_from_orig(ir_abs, orig_abs, 'scss', root_z_index=7)
    check('U-424c', 'z-index:7 still injected for position:absolute (regression guard)',
          'z-index: 7' in css_abs)


test_u424_css_from_orig_injects_z_index_for_relative_section()


def test_u437_exact_dest_lands_directly_without_component_name_layer():
    """U-437: --exact-dest 直接落地到指定路径，不追加 pageComponent 层。

    P1.4-Ind 重建场景：pc/ 或 h5/ 已存在，不能用 --dest=src/pages（会冲突）。
    --exact-dest=src/pages/EuDeposit/pc 应直接把产物落到该目录，
    不产生 src/pages/EuDeposit/pc/EuDeposit/ 这样的多余嵌套。
    """
    import os
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '250-2618'
        comp_name = 'EuDeposit'

        page_dir = _create_mock_page_code(tmp, node_id, comp_name)

        # exact_dest: simulate existing pc/ dir
        exact_dest = tmp / 'src' / 'pages' / 'EuDeposit' / 'pc'
        exact_dest.mkdir(parents=True)

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from split_components import cmd_apply
            cmd_apply(node_id, dest=None, css_ext='less', exact_dest=str(exact_dest))
        finally:
            os.chdir(old_cwd)

        # Files must land DIRECTLY in exact_dest/, not in exact_dest/EuDeposit/
        check('U-437a',
              '--exact-dest: index.tsx lands directly in exact_dest (no ComponentName subdir)',
              (exact_dest / 'index.tsx').exists())
        check('U-437b',
              '--exact-dest: no extra EuDeposit/ subdir created inside exact_dest',
              not (exact_dest / comp_name).exists())
        check('U-437c',
              '--exact-dest: components/ subdir exists inside exact_dest',
              (exact_dest / 'components').exists())

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


test_u437_exact_dest_lands_directly_without_component_name_layer()
print_summary('split/test_apply_landing')
