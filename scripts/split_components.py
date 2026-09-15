from __future__ import annotations
#!/usr/bin/env python3
"""
P4 语义化组件拆分入口（Path A 脚本路径）。

用法：
  python3 split_components.py '<URL>' --analyze
  python3 split_components.py --node-id=42-100 --analyze
  python3 split_components.py --node-id=42-100 --apply
  python3 split_components.py --node-id=42-100 --auto   # 一步完成
  --css-ext=less|scss  样式后缀（默认 less）
  --dest=<path>        落地目标目录
"""

import sys
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))
from lib.semantic_extractor import extract_semantic_context
from lib.boundary_detector import detect_boundaries
from lib.split_codegen import (generate_components, update_root_index,
                                _load_orig_figma_class_map, _load_orig_css_dict,
                                _load_orig_figma_asset_map, _load_figma_maps_json)
from lib.code_connect import load_code_connect_json
from lib.paths import (
    BASE_DIR, ASSETS_STAGING_DIR, PUBLIC_ASSETS_DIR,
    ir_file, raw_data_file, semantic_file, code_connect_file,
    PLAN_SUBPATH, PLAN_NAMING_SUBPATH, ASSET_MAPS_FILENAME,
    SPLIT_A_SUBDIR, STAGE_SUBDIR, COMPONENTS_SUBDIR, PAGES_SRC_DIR,
    INDEX_TS_FILENAME, INDEX_TSX_FILENAME, PAGE_TSX_FILENAME, HOOKS_SUBDIR,
    figma_name_to_pascal,
)


def _inject_asset_paths(ir: dict, fid_to_src: dict) -> None:
    """将 fid_to_src 中的路径注入到 IR 树每个节点的 localAssetPath（原地操作）。"""
    fid = ir.get('figmaId', '')
    if fid and fid in fid_to_src:
        ir['localAssetPath'] = fid_to_src[fid]
    for c in (ir.get('children') or []):
        _inject_asset_paths(c, fid_to_src)


def _expand_ir_max_css(ir0: dict, other_instances: list) -> None:
    """Zip-walk ir0 against other_instances by structural position.
    For each node pair, update ir0's css with max numeric px values from counterparts.
    Ensures the leaf component template is sized for the widest/tallest content variant.
    """
    if not other_instances:
        return
    _SIZE_PROPS = ('width', 'height', 'min-width', 'max-width', 'min-height')

    def _walk(n0, others):
        css0 = n0.get('css')
        if css0:
            for nk in others:
                # Skip size expansion when both nodes are image assets with different paths.
                # Different images have naturally different sizes; equalizing them corrupts
                # icon_tree instancesData (e.g. 484px image getting 1075px sibling's width).
                if (n0.get('isImageNode') and nk.get('isImageNode')
                        and n0.get('localAssetPath') != nk.get('localAssetPath')):
                    continue
                css_k = nk.get('css') or {}
                for prop in _SIZE_PROPS:
                    if prop not in css_k:
                        continue
                    m0 = re.match(r'([0-9.]+)px', str(css0.get(prop, '')))
                    mk = re.match(r'([0-9.]+)px', str(css_k[prop]))
                    if m0 and mk and float(mk.group(1)) > float(m0.group(1)):
                        css0[prop] = css_k[prop]
            # If template has a fixed height but ANY other instance has HUG (height=None
            # or absent), clear the template height so the shared component stays flexible.
            # This prevents the FIXED instance (e.g. OnePlatform h=88) from forcing HUG
            # instances (e.g. Earn/SpotX/End h=auto) into a fixed height, which causes
            # 4-8px excess per section and an 18px cumulative vertical offset.
            if css0.get('height') and re.match(r'[0-9.]+px', str(css0['height'])):
                any_hug = any(
                    (nk.get('css') or {}).get('height') is None
                    or 'height' not in (nk.get('css') or {})
                    for nk in others
                    if nk.get('css') is not None
                )
                if any_hug:
                    css0['height'] = None
        ch0 = n0.get('children') or []
        for i, c0 in enumerate(ch0):
            cn = [o.get('children', [])[i] for o in others if i < len(o.get('children') or [])]
            _walk(c0, cn)

    _walk(ir0, other_instances)


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
        'analyze': flags.get('analyze') == 'true',
        'apply':   flags.get('apply')   == 'true',
        'auto':    flags.get('auto')    == 'true',
        'dest':    flags.get('dest'),
        'css_ext': flags.get('css-ext', 'less'),
        'apply_renames': flags.get('apply-renames'),  # path to renames.json
        'skip-cc': flags.get('skip-cc') == 'true',
        'enable_i18n': flags.get('i18n') == 'true',
        'exact_dest': flags.get('exact-dest'),
    }


def _url_to_node_id(url: str) -> str | None:
    try:
        qs = parse_qs(urlparse(url).query)
        raw = qs.get('node-id', [''])[0]
        return raw.replace(':', '-') if raw else None
    except Exception:
        return None


def _get_page_code_dir(base: Path, node_id_safe: str) -> Path | None:
    single_page = base / '3-page-code'
    if not single_page.exists():
        return None

    def _best(candidates: list) -> Path | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # 多候选时：优先选有 split-a/plan.json 的目录
        # 若都有，再选 plan.pageComponent 与目录名前缀匹配的（语义名优先于 Figma 帧名）
        with_plan = [(c, c / PLAN_SUBPATH) for c in candidates
                     if (c / PLAN_SUBPATH).exists()]
        if not with_plan:
            return candidates[0]  # 都没有 plan，fallback
        if len(with_plan) == 1:
            return with_plan[0][0]  # 只有一个有 plan
        # 都有 plan：读 pageComponent 找目录名匹配的
        for c, plan_path in with_plan:
            try:
                pc = json.loads(plan_path.read_text()).get('pageComponent', '')
                if pc and c.name.startswith(pc):
                    return c
            except Exception:
                pass
        return with_plan[0][0]  # fallback 到第一个有 plan 的

    # New format: {name}-{node_id}
    result = _best(list(single_page.glob(f'*-{node_id_safe}')))
    if result:
        return result
    # Backward compat: old format {node_id}-{name}
    return _best(list(single_page.glob(f'{node_id_safe}-*')))


# ── --analyze ─────────────────────────────────────────────────────────────────

def cmd_analyze(node_id_safe: str, css_ext: str, skip_cc: bool = False) -> dict:
    """检测边界，写 plan.json，返回 plan dict（供 --auto 复用）。"""
    ir_path       = ir_file(node_id_safe)
    semantic_path = semantic_file(node_id_safe)

    page_dir = _get_page_code_dir(BASE_DIR, node_id_safe)
    if not page_dir:
        print('❌  3-page-code 目录不存在，请先运行 P1（convert.py）')
        sys.exit(1)
    split_a_dir = page_dir / SPLIT_A_SUBDIR
    split_a_dir.mkdir(parents=True, exist_ok=True)

    if not ir_path.exists():
        print(f'❌  IR 不存在: {ir_path}')
        sys.exit(1)

    ir = json.loads(ir_path.read_text())

    raw_path = raw_data_file(node_id_safe)
    raw_data = json.loads(raw_path.read_text()) if raw_path.exists() else {}

    # ── Code Connect 缓存加载 ──
    cc_path = code_connect_file(node_id_safe)
    code_connect_map = load_code_connect_json(cc_path)
    if not code_connect_map and not skip_cc:
        print(f'❌  Code Connect 数据缺失: {cc_path}')
        print(f'   组件实例将渲染为 <div> 而非 Moly 组件。')
        print(f'   请先执行: get_code_connect_map(fileKey=..., nodeId="{node_id_safe.replace("-",":")}") 并保存结果。')
        print(f'   若确认跳过，添加 --skip-cc 参数。')
        sys.exit(1)

    if semantic_path.exists():
        semantic = json.loads(semantic_path.read_text())
    elif raw_data:
        semantic = extract_semantic_context(raw_data)
        semantic_path.write_text(json.dumps(semantic, indent=2, ensure_ascii=False))
    else:
        semantic = {'componentInstances': {}, 'interactiveNodes': {}, 'readyFrames': []}

    # 从原始数据构建 figmaId → absoluteBoundingBox.{y,x} 映射，用于 Section 排序和定位
    _y_map: dict = {}
    _x_map: dict = {}
    def _collect_y(nodes: list) -> None:
        for n in nodes:
            fid = n.get('id', '')
            abb = n.get('absoluteBoundingBox') or {}
            if fid and 'y' in abb:
                _y_map[fid] = abb['y']
            if fid and 'x' in abb:
                _x_map[fid] = abb['x']
            _collect_y(n.get('children') or [])
    for v in raw_data.get('nodes', {}).values():
        _collect_y([v.get('document') or {}])

    # 提前推断 pageComponent 名（用于 fid_to_src 加载）
    # 优先从 page_dir 中已有的 TSX 文件名读取，与 convert.py 命名保持一致；
    # 避免 _infer_page_name 大小写规则不同导致 img src 路径错误（如 KYC vs Kyc）。
    root_sem  = ir.get('semantic') or {}
    _existing_page_tsx = next(page_dir.glob('*.tsx'), None)
    page_name = (_existing_page_tsx.stem if _existing_page_tsx else None) \
                or root_sem.get('componentName') \
                or _infer_page_name(ir)

    # 把 fid_to_src 提前注入 IR：_walk_diff 在 detect_boundaries 里运行，
    # 需要 localAssetPath 已存在于 IR 节点，才能检测图片 varying props。
    # Prefer sidecar (written by convert.py with patch+normalize+resolve results)
    _maps_class, _maps_src = _load_figma_maps_json(node_id_safe, page_name)
    fid_to_src_early = _maps_src or _load_orig_figma_asset_map(node_id_safe, page_name)
    if fid_to_src_early:
        _inject_asset_paths(ir, fid_to_src_early)

    # 从 raw data 提取 components 元数据（用于 icon componentId → name 推导）
    _components_meta = {}
    if raw_data and 'nodes' in raw_data:
        _root_key = next(iter(raw_data['nodes']), '')
        if _root_key:
            _components_meta = raw_data['nodes'][_root_key].get('components', {})

    # 检测 + 生成均使用 IR（ir_builder.py 已写入 componentId + bb）
    boundaries = detect_boundaries(ir, semantic, code_connect_map=code_connect_map,
                                   components_meta=_components_meta or None)

    # 顶层叶子组件
    top_leaves = []
    for lc in boundaries['leafComponents']:
        ir_node = lc['instances'][0]
        # 确保模板 IR 尺寸为所有实例中的最大值，避免宽/高内容被裁剪
        _expand_ir_max_css(ir_node, lc['instances'][1:])
        name = _infer_component_name(ir_node)
        ir_node['semantic'] = _infer_semantic(ir_node)
        top_leaves.append({
            'name': name, 'ir': ir_node,
            'varyingProps': lc['varyingProps'],
            'instancesData': lc['instancesData'],
            # 所有实例（含模板）的 figma-id，供 validate_split.py 排除非模板子树
            'allInstanceFigmaIds': [
                inst.get('figmaId', '') for inst in lc['instances']
                if inst.get('figmaId')
            ],
            'ccComponent': lc.get('ccComponent'),
            'ccImport': lc.get('ccImport'),
            'allImports': lc.get('allImports'),
            'snippet': lc.get('snippet'),
            'confidence': lc.get('confidence'),
            'source': lc.get('source'),
        })

    # Section 组件（含内部叶子）—— 先收集，后按 y 坐标排序，再命名
    sections = []
    for s in boundaries['sections']:
        ir_s = s['node']
        ir_s['semantic'] = _infer_semantic(ir_s)

        s_leaves = []
        _used_leaf_names: dict = {}  # name → count，用于同 Section 内重名去重
        for lc in s.get('leafComponents', []):
            ir_node = lc['instances'][0]
            _expand_ir_max_css(ir_node, lc['instances'][1:])
            lc_name = _infer_component_name(ir_node)
            # 同 Section 内重名叶子追加序号（Container → Container2, Container3...）
            if lc_name in _used_leaf_names:
                _used_leaf_names[lc_name] += 1
                lc_name = f'{lc_name}{_used_leaf_names[lc_name]}'
            else:
                _used_leaf_names[lc_name] = 1
            ir_node['semantic'] = _infer_semantic(ir_node)
            s_leaves.append({
                'name': lc_name, 'ir': ir_node,
                'varyingProps': lc['varyingProps'],
                'instancesData': lc['instancesData'],
                'allInstanceFigmaIds': [
                    inst.get('figmaId', '') for inst in lc['instances']
                    if inst.get('figmaId')
                ],
                'ccComponent': lc.get('ccComponent'),
                'ccImport': lc.get('ccImport'),
                'allImports': lc.get('allImports'),
                'snippet': lc.get('snippet'),
                'confidence': lc.get('confidence'),
                'source': lc.get('source'),
            })

        sections.append({
            'name': None,  # 排序后再赋名
            '_raw_name': _infer_component_name(ir_s),
            'ir': ir_s,
            'ccComponent': None, 'ccImport': None,
            'leafComponents': s_leaves,
        })

    # 按视觉 y 坐标排序（Figma 图层顺序 ≠ 视觉顺序，必须用 absoluteBoundingBox.y 修正）
    if _y_map:
        sections.sort(key=lambda s: _y_map.get(s['ir'].get('figmaId', ''), float('inf')))

    # 排序后赋语义化名称（三级 fallback：figmaName → 首文本 → 叶子名 → Section{N}）
    for idx, s in enumerate(sections):
        s.pop('_raw_name')
        s['name'] = _infer_section_name(s['ir'], s.get('leafComponents', []), idx)

    # 去重：相同名称加数字后缀（BuyYourSection → BuyYourSection, BuyYourSection2）
    _seen_names: dict = {}
    for s in sections:
        name = s['name']
        if name in _seen_names:
            _seen_names[name] += 1
            s['name'] = f'{name}{_seen_names[name]}'
        else:
            _seen_names[name] = 1

    # 内联节点（h≤200 的非装饰 depth-1 节点，保证所有 figma-id 出现在 Page.tsx）
    inline_nodes = []
    for idx_n, n in enumerate(boundaries.get('inlineNodes', [])):
        ir_n = n['node']
        fid = ir_n.get('figmaId', '')
        # 无 absoluteBoundingBox 的节点（如 BOOLEAN_OPERATION）用 -1e9+idx 排到最前，
        # 保持其在文档中的相对顺序
        y_val = _y_map.get(fid, -1e9 + idx_n)
        x_val = _x_map.get(fid, None)
        inline_nodes.append({'ir': ir_n, 'y': y_val, 'x': x_val})

    # 给 sections 也加 y/x，供 Page.tsx 渲染时按视觉顺序混排 + 定位固定尺寸 section
    for s in sections:
        fid = s['ir'].get('figmaId', '')
        s['y'] = _y_map.get(fid, float('inf'))
        s['x'] = _x_map.get(fid, None)  # Figma canvas absolute x（固定尺寸 section 定位用）

    # 子 Section 检测：Section 内部若有直接 FRAME 子节点后代数 > 20，同级全拆。
    # 用后代节点复杂度代替高度阈值，避免高度不一致导致拆分结果不稳定。
    def _count_desc(node: dict) -> int:
        return sum(1 + _count_desc(c) for c in (node.get('children') or []))

    _DECORATIVE_FTYPES = frozenset({
        'LINE', 'ELLIPSE', 'VECTOR', 'RECTANGLE', 'POLYGON', 'STAR',
        'BOOLEAN_OPERATION', 'TEXT',
    })
    for s in sections:
        s_ir = s.get('ir') or {}
        children = [
            c for c in (s_ir.get('children') or [])
            if c.get('figmaType') == 'FRAME'
            and not c.get('isDecorativeElement')
            and not c.get('isComponentInstance')
        ]
        if len(children) < 2:
            continue
        # 若有任一子节点后代数 > 20 → 同级全部提取为子 Section
        if any(_count_desc(c) > 20 for c in children):
            parent_name = s['name']
            s['subSections'] = [
                {
                    'name': _deconflict_subsection_name(
                        _infer_section_name(c, [], idx), parent_name
                    ),
                    'ir': c,
                    'leafComponents': [],
                }
                for idx, c in enumerate(children)
            ]

    # 加载原始 CSS 映射 + figmaId→className 映射，写入 plan 供调试和 generate_components 使用
    fid_to_class = _maps_class or _load_orig_figma_class_map(node_id_safe, page_name)
    orig_css_map  = _load_orig_css_dict(node_id_safe, page_name)

    plan = {
        'generatedBy': 'split-a',
        'nodeId': node_id_safe,
        'irPath': str(ir_path.resolve()),  # 用于 generate_components 的全局归一化
        'pageComponent': page_name,
        'cssExt': css_ext,
        'fidToClass': fid_to_class,
        'cssMap': orig_css_map,
        'fidToSrc': fid_to_src_early,
        'leafComponents': top_leaves,
        'sections': sections,
        'inlineNodes': inline_nodes,
        'codeConnectComponents': [
            {k: v for k, v in cc.items() if k != 'node'}
            for cc in boundaries.get('codeConnectComponents', [])
        ],
        'expandedWrapperFigmaIds': boundaries.get('expandedWrapperFigmaIds', []),
    }

    plan_path = page_dir / PLAN_SUBPATH
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False))

    lc_count = len(top_leaves)
    s_count = len(sections)
    s_leaf_count = sum(len(s.get('leafComponents', [])) for s in sections)
    total = lc_count + s_count
    print(f'\n✅  分析完成：{s_count} 个 Section（含 {s_leaf_count} 个内部叶子），'
          f'{lc_count} 个顶层叶子（共 {total} 个顶层组件）')
    print(f'   plan.json: {plan_path}')
    if total > 10:
        print(f'⚠   顶层组件数量 {total} 超过 10，建议检查边界规则')
    print(f'\n   运行生成：python3 split_components.py --node-id={node_id_safe} --apply')

    # Output naming context for Claude rename pass
    naming_ctx = _generate_naming_context(plan)
    naming_path = page_dir / PLAN_NAMING_SUBPATH
    naming_path.write_text(json.dumps(naming_ctx, indent=2, ensure_ascii=False))
    if naming_ctx['sections'] or naming_ctx.get('subSections') or naming_ctx['leaves'] or naming_ctx['props']:
        print(f'   plan.naming.json: {naming_path} ({len(naming_ctx["sections"])} sections, '
              f'{len(naming_ctx.get("subSections", []))} subSections, '
              f'{len(naming_ctx["leaves"])} leaves, {len(naming_ctx["props"])} props need naming)')
    return plan


# ── 依赖自动安装 ──────────────────────────────────────────────────────────────

def _auto_install_deps(stage_dir: Path) -> None:
    """扫描生成代码中的外部 import，与 package.json 对比，自动安装缺失的包。"""
    import subprocess

    # 收集所有 .tsx/.ts 文件中的外部包名
    pkg_names: set = set()
    for f in stage_dir.rglob('*.tsx'):
        _extract_pkg_names(f, pkg_names)
    for f in stage_dir.rglob('*.ts'):
        _extract_pkg_names(f, pkg_names)

    if not pkg_names:
        return

    # 读取 package.json 已有依赖
    pkg_json_path = _find_package_json()
    if not pkg_json_path:
        return

    pkg_json = json.loads(pkg_json_path.read_text())
    existing = set()
    for key in ('dependencies', 'devDependencies', 'peerDependencies'):
        existing.update((pkg_json.get(key) or {}).keys())

    # 找出缺失的包
    missing = pkg_names - existing
    # 排除 react/react-dom 等框架包（通常已存在，或由 CDN 提供）
    framework_pkgs = {'react', 'react-dom', 'react/jsx-runtime'}
    missing -= framework_pkgs

    if not missing:
        return

    print(f'\n📦  检测到新依赖包：{", ".join(sorted(missing))}')
    # 检测项目使用的包管理器
    pm = _detect_package_manager(pkg_json_path.parent)
    for pkg in sorted(missing):
        cmd = f'{pm} add {pkg}@latest' if pm == 'yarn' else f'{pm} install {pkg}@latest'
        print(f'   安装: {cmd}')
        try:
            subprocess.run(cmd.split(), cwd=str(pkg_json_path.parent),
                           capture_output=True, timeout=60)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f'   ⚠ 安装失败: {e}')


def _extract_pkg_names(file_path: Path, pkg_names: set) -> None:
    """从文件中提取所有外部包名（排除相对路径和内置模块）。"""
    content = file_path.read_text()
    for m in re.finditer(r'''(?:from|import)\s+['"]([^'"./][^'"]*?)['"]''', content):
        mod = m.group(1)
        # 取 scoped 包的完整名（如 @your-org/icons）
        parts = mod.split('/')
        if parts[0].startswith('@') and len(parts) >= 2:
            pkg_names.add(f'{parts[0]}/{parts[1]}')
        else:
            pkg_names.add(parts[0])


def _find_package_json() -> Path | None:
    """向上查找 package.json。"""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        p = parent / 'package.json'
        if p.exists():
            return p
    return None


def _detect_package_manager(project_dir: Path) -> str:
    """检测项目使用的包管理器。"""
    if (project_dir / 'yarn.lock').exists():
        return 'yarn'
    if (project_dir / 'pnpm-lock.yaml').exists():
        return 'pnpm'
    return 'npm'


# ── --apply ───────────────────────────────────────────────────────────────────

def cmd_apply(node_id_safe: str, dest: str | None, css_ext: str,
              plan: dict | None = None, no_i18n: bool = True,
              exact_dest: str | None = None) -> None:
    """读取 plan.json（或直接接收 plan dict），生成产物并落地。"""
    page_dir = _get_page_code_dir(BASE_DIR, node_id_safe)
    if not page_dir:
        print('❌  3-page-code 目录不存在')
        sys.exit(1)

    if plan is None:
        plan_path = page_dir / PLAN_SUBPATH
        if not plan_path.exists():
            print(f'❌  plan.json 不存在: {plan_path}，请先运行 --analyze')
            sys.exit(1)
        plan = json.loads(plan_path.read_text())

    # 去重 section 名称（防止重复 import 和目录覆盖）
    _seen: dict = {}
    for s in plan.get('sections', []):
        name = s['name']
        if name in _seen:
            _seen[name] += 1
            s['name'] = f'{name}{_seen[name]}'
        else:
            _seen[name] = 1

    # 暂存到 split-a/stage/
    stage_dir = page_dir / SPLIT_A_SUBDIR / STAGE_SUBDIR
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    result = generate_components(plan, stage_dir, css_ext=plan.get('cssExt', css_ext), no_i18n=no_i18n)

    s_count = len(result['sectionNames'])
    tl_count = len(result['topLeafNames'])
    print(f'✅  产物已生成到暂存区: {stage_dir}')
    print(f'   Section: {s_count} 个，顶层叶子: {tl_count} 个')

    # CSS 类名语义化：apply-renames 时将 cssClassRenames 写入 plan.json，
    # 在这里读取并对 stage 做字符串替换（stage 已生成，不会再被覆盖）。
    _css_renames = plan.get('cssClassRenames', {})
    if _css_renames:
        _css_updated = _apply_css_class_renames(stage_dir, _css_renames)
        if _css_updated:
            print(f'   CSS 类名替换（来自 cssClassRenames）：{len(_css_renames)} 个映射，{_css_updated} 个文件已更新')

    # 落地
    # --exact-dest: 直接指定落地路径，不追加 pageComponent 层。
    # 用于 P1.4-Ind 双稿独立模式重建：pc/ 或 h5/ 已存在时不能用 --dest（会冲突），
    # 用临时 dest 再 mv 的绕路方案也不再需要了。
    if exact_dest:
        target = Path(exact_dest)
        target.mkdir(parents=True, exist_ok=True)
        pages_dir = target  # 仅用于后续资源复制逻辑中的 public/assets 路径计算
    else:
        pages_dir = _find_pages_dir(dest)
    if pages_dir:
        if not exact_dest:
            # 产物目录名不含 nodeId（映射记录在 asset-path-maps.json）
            target = pages_dir / plan['pageComponent']
        # 若存在旧格式目录（含 nodeId 后缀），询问用户如何处理冲突
        for stale in pages_dir.glob(f'*-{node_id_safe}'):
            if stale.is_dir() and stale != target:
                print(f'\n⚠   检测到旧名称目录与新产物冲突：')
                print(f'    旧目录：{stale}')
                print(f'    新目录：{target}')
                print(f'    请选择操作：')
                print(f'    [1] 删除旧目录，保留新产物（推荐，页面已重命名）')
                print(f'    [2] 保留两者（旧目录不变，新目录另行创建）')
                print(f'    [3] 取消落地')
                try:
                    choice = input('    输入选项 (1/2/3，默认 1): ').strip() or '1'
                except (EOFError, OSError):
                    choice = '1'  # 非交互环境默认删除旧目录
                if choice == '1':
                    shutil.rmtree(stale, ignore_errors=True)
                    print(f'    ✓ 已删除旧目录：{stale}')
                elif choice == '3':
                    print('    ✗ 已取消落地操作。')
                    return
                else:
                    print(f'    ℹ 保留两者，旧目录不变。')
        if not target.exists():
            target.mkdir(parents=True)
        # 落地规则：
        #   Page.tsx           → target/index.tsx（提升并重命名，作为入口）
        #   其余文件           → target/ 根（CSS、texts.ts 等与 index.tsx 同级）
        #   hooks/ 目录        → target/hooks/（与 components/ 平级）
        #   components/ 目录   → target/components/（整体拷贝，stage 结构即落地结构）
        _css_ext = plan.get('cssExt', 'less')
        comp_dir = target / COMPONENTS_SUBDIR
        if comp_dir.exists():
            shutil.rmtree(comp_dir)
        for item in stage_dir.iterdir():
            if item.is_dir():
                if item.name == HOOKS_SUBDIR:
                    hooks_dest = target / HOOKS_SUBDIR
                    if hooks_dest.exists():
                        shutil.rmtree(hooks_dest)
                    shutil.copytree(item, hooks_dest)
                elif item.name == COMPONENTS_SUBDIR:
                    shutil.copytree(item, comp_dir)
                else:
                    # 兜底：stage 中若仍有非 components/ 的 section 目录，放入 components/
                    if not comp_dir.exists():
                        comp_dir.mkdir()
                    dest_item = comp_dir / item.name
                    if dest_item.exists():
                        shutil.rmtree(dest_item)
                    shutil.copytree(item, dest_item)
            elif item.name == PAGE_TSX_FILENAME:
                # Page.tsx 提升为根级 index.tsx（不再需要 index.ts 重导出壳）
                shutil.copy2(item, target / INDEX_TSX_FILENAME)
            else:
                # 所有其他文件（CSS、texts.ts 等）均落在 target 根
                shutil.copy2(item, target / item.name)
        # 不再生成 index.ts（index.tsx 直接作为入口）
        # 清理旧文件：重导出壳 + convert.py 单文件产物
        _old_files = [
            target / INDEX_TS_FILENAME,                    # 旧重导出壳，被 index.tsx 替代
            target / PAGE_TSX_FILENAME,                    # convert.py 可能遗留
            target / f'{plan["pageComponent"]}.tsx',       # convert.py 单文件 TSX
            target / f'Page.module.{_css_ext}',           # 旧 Page CSS
        ]
        for f in _old_files:
            if f.exists():
                f.unlink()
        print(f'✅  已落地: {comp_dir}/')

        # 产物后处理：去除 nodeId
        comp_name = plan['pageComponent']
        # plan.pageComponent 是唯一命名真理来源（来自 figma_name_to_pascal），直接用于路径前缀。
        # 不再扫描 public/assets/ 目录：旧命名遗留目录会覆盖正确大小写，导致路径不一致。
        new_asset_prefix = f'/assets/{comp_name}/'

        # 实际 public/assets 目录名可能与 pageComponent 大小写不同（Figma 原始名）。
        # 扫描所有匹配 *-{node_id_safe} 的目录，收集所有可能的旧前缀，全部替换。
        _pub_base = PUBLIC_ASSETS_DIR
        _old_prefixes: set = {f'/assets/{comp_name}-{node_id_safe}/'}
        if _pub_base.exists():
            for _d in _pub_base.glob(f'*-{node_id_safe}'):
                if _d.is_dir():
                    _old_prefixes.add(f'/assets/{_d.name}/')
        # 兜底：从 plan.fidToSrc 中提取实际使用的路径前缀（icon_tree 等直接内嵌 IR localAssetPath）。
        # 当旧 assets 目录已被重命名后，上面的目录扫描找不到，但 localAssetPath 中仍有旧前缀。
        import re as _re
        for _src_path in (plan.get('fidToSrc') or {}).values():
            _m = _re.match(r'(/assets/[^/]+-' + node_id_safe + r'/)', _src_path)
            if _m:
                _old_prefixes.add(_m.group(1))
                break  # 所有条目前缀相同，找到一个即可

        # 1. 更新 target 目录下所有 .tsx 的资源路径（去除 nodeId）
        _tsx_updated = 0
        for _tsx in target.rglob('*.tsx'):
            _c = _tsx.read_text(encoding='utf-8')
            _changed = False
            for _old_pref in _old_prefixes:
                if _old_pref in _c:
                    _c = _c.replace(_old_pref, new_asset_prefix)
                    _changed = True
            if _changed:
                _tsx.write_text(_c, encoding='utf-8')
                _tsx_updated += 1
        if _tsx_updated:
            print(f'   TSX 资源路径已更新（去除 nodeId）：{_tsx_updated} 个文件')

        # 2. 重命名 public/assets/*-{nodeId} → {Name}（扫描所有旧名称，含 rename 前的原始名）
        _pub_base = PUBLIC_ASSETS_DIR
        _pub_new = _pub_base / comp_name
        for _pub_old in _pub_base.glob(f'*-{node_id_safe}'):
            if _pub_old.is_dir() and _pub_old != _pub_new:
                if not _pub_new.exists():
                    _pub_old.rename(_pub_new)
                    print(f'   public/assets 已重命名：{_pub_old.name} → {_pub_new.name}')
                else:
                    shutil.rmtree(_pub_old, ignore_errors=True)
                    print(f'   public/assets 旧目录已删除：{_pub_old.name}')

        # Fallback：若 public/assets 下无该组件目录，从 1-assets 暂存区复制。
        # 优先读已有的 asset-path-maps.json 获取 stagingDir（记录了上次落地时的暂存目录名），
        # 避免重复扫描；若无记录则扫描 1-assets/*-{nodeId}/ 兜底。
        _int_base = ASSETS_STAGING_DIR
        _maps_path = page_dir / ASSET_MAPS_FILENAME
        _resolved_staging: Path | None = None
        if _maps_path.exists():
            try:
                _sd = json.loads(_maps_path.read_text()).get('stagingDir', '')
                if _sd and (_int_base / _sd).is_dir():
                    _resolved_staging = _int_base / _sd
            except Exception:
                pass
        if _resolved_staging is None:  # 无记录或记录失效，扫描兜底
            _candidates = sorted(_int_base.glob(f'*-{node_id_safe}')) if _int_base.exists() else []
            _resolved_staging = next((c for c in _candidates if c.is_dir()), None)
        if not _pub_new.exists() and _resolved_staging:
            _pub_new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(_resolved_staging), str(_pub_new))
            _n = sum(1 for _ in _pub_new.iterdir())
            print(f'   public/assets 已从暂存区复制：{_resolved_staging.name} → {_pub_new.name}/ ({_n} 个文件)')

        # 3. 生成 asset-path-maps.json（记录中间产物与最终产物的映射，供后续落地和 validate.py 使用）
        _map = {
            'componentName': comp_name,
            'nodeIdSafe': node_id_safe,
            'pagesDir': comp_name,
            'assetsDir': comp_name,
            'stagingDir': _resolved_staging.name if _resolved_staging else '',
        }
        _maps_path.write_text(json.dumps(_map, indent=2, ensure_ascii=False))
    else:
        print(f'⚠   未找到 pages 目录，暂存区产物在: {stage_dir}')

    # 依赖检测与自动安装
    _auto_install_deps(stage_dir)

    print(f'\n📋  下一步（必须门禁）：')
    print(f'   python3 validate_split.py --node-id={node_id_safe} --css-ext={plan.get("cssExt", css_ext)}')


# ── --auto ────────────────────────────────────────────────────────────────────

def cmd_auto(node_id_safe: str, dest: str | None, css_ext: str,
             skip_cc: bool = False, no_i18n: bool = True) -> None:
    """只做分析，生成 plan.json 和 plan.naming.json（不落地）。
    落地请在 AI rename pass 完成后，显式运行 --apply。"""
    print(f'🔄  自动模式：分析（node-id={node_id_safe}）')
    cmd_analyze(node_id_safe, css_ext, skip_cc=skip_cc)
    print()
    print('📋  分析完成。请执行 AI rename pass 后，运行 --apply --dest=<路径> 落地。')


# ── --apply-renames ───────────────────────────────────────────────────────────

def cmd_apply_renames(node_id_safe: str, renames_path: str) -> None:
    """Read renames.json and write name changes back into plan.json."""
    page_dir = _get_page_code_dir(BASE_DIR, node_id_safe)
    if not page_dir:
        print('❌  3-page-code 目录不存在')
        sys.exit(1)

    plan_path = page_dir / PLAN_SUBPATH
    if not plan_path.exists():
        print(f'❌  plan.json 不存在: {plan_path}')
        sys.exit(1)

    resolved = Path(renames_path)
    if not resolved.exists():
        # 若仅传文件名（如 renames.json），从 split-a/ 目录下查找
        fallback = page_dir / PLAN_SUBPATH.parent / resolved.name
        if fallback.exists():
            resolved = fallback
        else:
            print(f'❌  renames.json 不存在: {resolved}（也不在 {fallback}）')
            sys.exit(1)
    renames = json.loads(resolved.read_text())
    plan = json.loads(plan_path.read_text())

    count = 0
    old_page_comp = plan.get('pageComponent', '')  # 记录改名前的旧组件名

    # Page component rename（顶层目录名 = pageComponent + '-' + nodeId）
    if 'page' in renames and renames['page']:
        plan['pageComponent'] = renames['page']
        count += 1

    # Section rename
    for old_name, new_name in renames.get('sections', {}).items():
        for s in plan.get('sections', []):
            if s['name'] == old_name:
                s['name'] = new_name
                count += 1

    # SubSection rename（Section 内部子 Section）
    for old_name, new_name in renames.get('subSections', {}).items():
        for s in plan.get('sections', []):
            for ss in s.get('subSections', []):
                if ss['name'] == old_name:
                    ss['name'] = new_name
                    count += 1

    # Leaf rename (section-internal + top-level)
    for old_name, new_name in renames.get('leaves', {}).items():
        for s in plan.get('sections', []):
            for lc in s.get('leafComponents', []):
                if lc['name'] == old_name:
                    lc['name'] = new_name
                    count += 1
        for lc in plan.get('leafComponents', []):
            if lc['name'] == old_name:
                lc['name'] = new_name
                count += 1

    # Props rename
    for key, new_name in renames.get('props', {}).items():
        parts = key.rsplit('.', 1)
        if len(parts) != 2:
            continue
        leaf_name, old_prop = parts
        for s in plan.get('sections', []):
            for lc in s.get('leafComponents', []):
                if lc['name'] == leaf_name:
                    for vp in lc.get('varyingProps', []):
                        if vp.get('propName') == old_prop:
                            vp['propName'] = new_name
                            count += 1

    # CSS 类名语义化：将 cssClasses 映射写入 plan.json（cssClassRenames），
    # 由 cmd_apply 在 stage 生成后应用，避免 --apply 重建 stage 时覆盖替换结果。
    css_class_renames: dict = renames.get('cssClasses', {})
    if css_class_renames:
        plan['cssClassRenames'] = css_class_renames

    # Re-run deconflict: AI rename pass may set a subSection name equal to its parent
    # section, which would cause 'Identifier already declared' in the generated index.tsx.
    deconflict_count = 0
    for s in plan.get('sections', []):
        parent_name = s.get('name', '')
        for ss in s.get('subSections', []):
            original = ss.get('name', '')
            ss['name'] = _deconflict_subsection_name(original, parent_name)
            if ss['name'] != original:
                deconflict_count += 1

    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    print(f'✅  已应用 {count} 项重命名到 plan.json')
    if deconflict_count:
        print(f'   ⚠️  {deconflict_count} 个 subSection 与父 Section 同名，已自动去重（Section→Inner）')
    if css_class_renames:
        print(f'   cssClassRenames 已写入 plan.json：{len(css_class_renames)} 个映射')


# ── 语义自动推断 ───────────────────────────────────────────────────────────────

def _infer_html_tag(figma_name: str) -> str:
    name = figma_name.lower()
    if any(k in name for k in ('nav', 'navbar', 'navigation')):
        return 'nav'
    if 'header' in name:
        return 'header'
    if 'footer' in name:
        return 'footer'
    if any(k in name for k in ('hero', 'banner')):
        return 'section'
    if any(k in name for k in ('btn', 'button', 'cta')):
        return 'button'
    return 'div'


def _infer_component_name(node: dict) -> str:
    raw = node.get('figmaName', 'Component')

    # If node has ccComponent mapping, use that name when figmaName is generic
    cc_comp = node.get('ccComponent')
    if cc_comp and _is_generic_figma_name(raw):
        return cc_comp

    # Non-generic figmaName → PascalCase (existing logic)
    if not _is_generic_figma_name(raw):
        name = re.sub(r'[^a-zA-Z0-9]', '', raw.title().replace(' ', '')) or 'Component'
        m = re.match(r'^(\d+)(.*)', name)
        if m:
            name = (m.group(2) or 'Comp') + m.group(1)
        return name

    # Generic figmaName → try first text child
    first_text = _find_first_text(node, max_depth=2)
    if first_text:
        text_name = _text_to_name(first_text, max_words=2)
        if text_name:
            return text_name

    # Fallback: use raw figmaName processing (current behavior)
    name = re.sub(r'[^a-zA-Z0-9]', '', raw.title().replace(' ', '')) or 'Component'
    m = re.match(r'^(\d+)(.*)', name)
    if m:
        name = (m.group(2) or 'Comp') + m.group(1)
    return name


_GENERIC_NAME_RE = re.compile(
    r'^(Frame|Group|Rectangle|Ellipse|Vector|AutoLayout|Container|Wrapper|Div)\s*\d*$|^Component$',
    re.IGNORECASE,
)

def _is_generic_figma_name(name: str) -> bool:
    return bool(_GENERIC_NAME_RE.match(name))


def _find_first_text(node: dict, max_depth: int = 2, _depth: int = 0) -> str | None:
    """DFS 查找第一个 TEXT 节点的 textContent，超过 max_depth 停止。"""
    if node.get('figmaType') == 'TEXT' or node.get('isTextNode'):
        return node.get('textContent') or node.get('text') or None
    if _depth >= max_depth:
        return None
    for child in node.get('children', []):
        result = _find_first_text(child, max_depth, _depth + 1)
        if result:
            return result
    return None


def _text_to_name(text: str, max_words: int = 3) -> str | None:
    """从文本中提取前 N 个英文单词（≥2字符），转为 PascalCase，中文/无英文返回 None。"""
    words = re.findall(r'[a-zA-Z]{2,}', text)[:max_words]
    if not words:
        return None
    return ''.join(w.capitalize() for w in words)


_GENERIC_SECTION_RE = re.compile(
    r'^Section\d*$'  # Section1, Section2, Section (fallback)
    r'|^(Frame|Group|Rectangle|Ellipse|Vector)\d+Section$',  # Frame{N}Section (from generic leaf name)
    re.IGNORECASE,
)
_GENERIC_LEAF_RE = re.compile(r'^(Frame|Group|Rectangle|Comp)\d*$', re.IGNORECASE)

# CSS class names that are generic and need semantic naming.
# 匹配三种格式：
#   纯数字：frame-2147229790, node-169-33992
#   实例路径（I-prefix）：node-I171-12615-5382-9726（figmaId I171:12615;5382:9726 的编码形式）
#   normalize 消歧义产生的 n-prefix：frame-n6777-32817（_sanitize 对数字开头的 figmaId 前缀 n）
# 排除 variant 中缀（如 frame-2147224387-n169-35630，n 在数字之后，[\d-]* 先停在 n 处使 $ 失配）。
_GENERIC_CSS_CLASS_RE = re.compile(r'^(frame|node)-(I?n?\d)[\d-]*$')


def _apply_css_class_renames(stage_dir: Path, css_renames: dict) -> int:
    """对 stage 目录内所有 TSX/CSS 文件做 CSS 类名字符串替换，返回更新文件数。

    frame-*/node-* 类名含唯一 nodeId，直接 str.replace 安全，无需词边界保护。
    """
    updated = 0
    for f in stage_dir.rglob('*'):
        if not f.is_file() or f.suffix not in ('.tsx', '.ts', '.less', '.scss', '.css'):
            continue
        content = f.read_text(encoding='utf-8')
        new_content = content
        for old_cls, new_cls in css_renames.items():
            new_content = new_content.replace(old_cls, new_cls)
        if new_content != content:
            f.write_text(new_content, encoding='utf-8')
            updated += 1
    return updated


def _collect_generic_css_classes(ir_node: dict, result: list, section_name: str,
                                  seen: set, fid_to_class: dict,
                                  max_count: int = 0) -> None:
    """递归收集 IR 树中所有 frame-*/node-* 类名（含上下文），用于 plan.naming.json cssClasses。

    类名从 plan.fidToClass 查（IR 节点无 cssClass 字段）。
    跳过已处理节点（figmaId 去重）。max_count > 0 时限制 result 总长度（骨架层用）。
    """
    if max_count > 0 and len(result) >= max_count:
        return
    fid = ir_node.get('figmaId', '')
    if fid in seen:
        return
    seen.add(fid)

    cls = fid_to_class.get(fid, '')
    if cls and _GENERIC_CSS_CLASS_RE.match(cls):
        texts: list = []
        _collect_texts(ir_node, texts, max_depth=2, max_count=2)
        css = ir_node.get('css') or {}
        result.append({
            'currentClass': cls,
            'figmaId': fid,
            'figmaName': ir_node.get('figmaName', ''),
            'section': section_name,
            'firstTexts': texts,
            'css': {k: css[k] for k in ('display', 'flex-direction', 'width', 'height') if k in css},
        })

    for child in (ir_node.get('children') or []):
        _collect_generic_css_classes(child, result, section_name, seen, fid_to_class, max_count)


def _collect_texts(node: dict, result: list, max_depth: int = 3, max_count: int = 3, _depth: int = 0):
    """Collect up to max_count text contents from IR tree."""
    if len(result) >= max_count or _depth > max_depth:
        return
    if node.get('figmaType') == 'TEXT' or node.get('isTextNode'):
        text = (node.get('textContent') or '')[:60]
        if text:
            result.append(text)
    for ch in (node.get('children') or []):
        _collect_texts(ch, result, max_depth, max_count, _depth + 1)


def _generate_naming_context(plan: dict, root_ir: dict | None = None) -> dict:
    """Extract minimal naming context for Claude rename pass.
    Only includes items that still have generic names after rule-based enhancement.

    root_ir: 可选的根 IR 节点（用于收集主页骨架层的 frame-*/node-* 节点）。
             若不传，自动从 plan['irPath'] 加载；加载失败则静默跳过。
    """
    sections = []
    for s in plan.get('sections', []):
        if not _GENERIC_SECTION_RE.match(s['name']):
            continue
        texts = []
        _collect_texts(s['ir'], texts, max_depth=3, max_count=3)
        sections.append({
            'currentName': s['name'],
            'figmaName': s['ir'].get('figmaName', ''),
            'firstTexts': texts,
            'leafNames': [lc['name'] for lc in s.get('leafComponents', [])],
            'childCount': len(s['ir'].get('children') or []),
        })

    # subSections：Section 内部的子 Section，通用名（Section{N}）需要语义化
    sub_sections = []
    for s in plan.get('sections', []):
        for ss in s.get('subSections', []):
            if not _GENERIC_SECTION_RE.match(ss['name']):
                continue
            texts = []
            _collect_texts(ss['ir'], texts, max_depth=3, max_count=3)
            sub_sections.append({
                'currentName': ss['name'],
                'sectionName': s['name'],       # 父 section 名
                'figmaName': ss['ir'].get('figmaName', ''),
                'firstTexts': texts,
                'childCount': len(ss['ir'].get('children') or []),
            })

    leaves = []
    for s in plan.get('sections', []):
        for lc in s.get('leafComponents', []):
            if not _GENERIC_LEAF_RE.match(lc['name']):
                continue
            texts = []
            _collect_texts(lc['ir'], texts, max_depth=2, max_count=2)
            leaves.append({
                'currentName': lc['name'],
                'section': s['name'],
                'figmaName': lc['ir'].get('figmaName', ''),
                'firstTexts': texts,
                'varyingPropNames': [vp.get('propName', '') for vp in lc.get('varyingProps', [])],
            })
    for lc in plan.get('leafComponents', []):
        if not _GENERIC_LEAF_RE.match(lc['name']):
            continue
        texts = []
        _collect_texts(lc['ir'], texts, max_depth=2, max_count=2)
        leaves.append({
            'currentName': lc['name'],
            'section': '__top__',
            'figmaName': lc['ir'].get('figmaName', ''),
            'firstTexts': texts,
            'varyingPropNames': [vp.get('propName', '') for vp in lc.get('varyingProps', [])],
        })

    props = []
    for s in plan.get('sections', []):
        for lc in s.get('leafComponents', []):
            for vp in lc.get('varyingProps', []):
                pname = vp.get('propName', '')
                if re.match(r'^(text|img)\d*$', pname):
                    props.append({
                        'leaf': lc['name'],
                        'currentName': pname,
                        'sampleValues': [str(v)[:50] for v in vp.get('values', [])[:4]],
                    })

    # cssClasses：收集所有 frame-*/node-* 类名（含上下文），供 AI 批量语义化
    # fidToClass 是 figmaId → CSS class name 的映射，存在于 plan 顶层
    css_classes: list = []
    seen_fids: set = set()
    fid_to_class: dict = plan.get('fidToClass', {})
    for s in plan.get('sections', []):
        _collect_generic_css_classes(s['ir'], css_classes, s['name'], seen_fids, fid_to_class)
    for lc in plan.get('leafComponents', []):
        _collect_generic_css_classes(lc['ir'], css_classes, '__top__', seen_fids, fid_to_class)

    # 主页骨架层：sections/leafComponents 之外的顶层 frame-*/node-* 节点
    # （navigation、hero 包装层等直接挂在根节点下，不在任何 section IR 子树里）
    _root = root_ir
    if _root is None and plan.get('irPath'):
        try:
            _root = json.loads(Path(plan['irPath']).read_text())
        except Exception:
            pass
    if _root:
        for _child in (_root.get('children') or []):
            _collect_generic_css_classes(
                _child, css_classes, '__page_root__', seen_fids, fid_to_class,
            )

    return {
        'pageName': plan.get('pageComponent', ''),
        'sections': sections,
        'subSections': sub_sections,   # Section 内部的通用名子 Section
        'leaves': leaves,
        'props': props,
        'cssClasses': css_classes,     # frame-*/node-* CSS 类名，待语义化
    }


def _infer_section_name(ir_node: dict, leaves: list, idx: int) -> str:
    """三级 fallback 为 Section 生成语义化名称。

    Level 1: figmaName 有意义 → 使用 _infer_component_name 生成的名称 + Section 后缀
    Level 2: 从第一个文本节点提取英文词 → XxxSection
    Level 3: 叶子组件名 + Section
    Fallback: Section{N}
    """
    raw = ir_node.get('figmaName', '')
    # Level 1: figmaName 非 generic
    if not _is_generic_figma_name(raw) and raw:
        name = _infer_component_name(ir_node)
        return name if name.endswith('Section') else name + 'Section'
    # Level 2: 从节点内文本提取英文词
    text = _find_first_text(ir_node, max_depth=2)
    if text:
        text_name = _text_to_name(text)
        if text_name:
            return text_name + 'Section'
    # Level 3: 叶子组件名（跳过 generic 叶子名，避免 Frame2147229790 → Frame2147229790Section）
    for _leaf in leaves:
        _ln = _leaf.get('name', '') if isinstance(_leaf, dict) else str(_leaf)
        if _GENERIC_LEAF_RE.match(_ln):
            continue  # 叶子名本身也是通用名，不用于推断 section 名
        _ln = re.sub(r'[^a-zA-Z0-9]', '', _ln)
        if _ln:
            return _ln + 'Section'
    return f'Section{idx + 1}'


def _deconflict_subsection_name(sub_name: str, parent_name: str) -> str:
    """Avoid TypeScript identifier collision when a subSection gets the same name as its parent.

    Root cause: _infer_section_name uses Level-2 text extraction independently for parent and
    child — if both frames contain the same leading text (e.g. "Pick Your Delivery"), both get
    identical PascalCase names, causing 'Identifier already declared' in the generated index.tsx.
    Fix: rename the child by replacing the trailing 'Section' with 'Inner'.
    """
    if sub_name != parent_name:
        return sub_name
    if sub_name.endswith('Section'):
        return sub_name[:-len('Section')] + 'Inner'
    return sub_name + 'Inner'


def _infer_semantic(node: dict) -> dict:
    figma_name = node.get('figmaName', '')
    return {
        'htmlTag': _infer_html_tag(figma_name),
        'className': _to_kebab(figma_name) or 'component',
        'componentName': _infer_component_name(node),
        'props': [],
        'isExtractedComponent': True,
    }


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _infer_page_name(ir: dict) -> str:
    """Derive page component name from IR — mirrors convert.py's _fallback + to_pascal."""
    pascal = figma_name_to_pascal(ir.get('figmaName', ''), ir.get('figmaId', '') or '')
    if pascal and pascal[0].isalpha():
        return pascal
    fid = re.sub(r'[^0-9]', '', ir.get('figmaId', ''))
    return f'Page{fid}' if fid else 'Page'


def _find_pages_dir(hint: str | None) -> Path | None:
    if hint:
        p = Path(hint)
        return p if p.exists() else None
    candidates = list(Path('.').rglob(str(PAGES_SRC_DIR)))
    return candidates[0] if candidates else None


def _to_kebab(s: str) -> str:
    s = re.sub(r'([a-z])([A-Z])', r'\1-\2', s)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1-\2', s)
    s = re.sub(r'([a-zA-Z])([0-9])', r'\1-\2', s)
    return s.lower().strip('-').replace(' ', '-')


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    if not args['node_id_safe']:
        print('用法:')
        print('  python3 split_components.py <URL> --analyze')
        print('  python3 split_components.py --node-id=42-100 --apply')
        print('  python3 split_components.py --node-id=42-100 --auto')
        print('  python3 split_components.py --node-id=42-100 --apply-renames=renames.json')
        sys.exit(1)
    skip_cc = args.get('skip-cc', False)
    no_i18n = not args.get('enable_i18n', False)
    if args['apply_renames']:
        cmd_apply_renames(args['node_id_safe'], args['apply_renames'])
    elif args['auto']:
        cmd_auto(args['node_id_safe'], args['dest'], args['css_ext'], skip_cc=skip_cc, no_i18n=no_i18n)
    elif args['analyze']:
        cmd_analyze(args['node_id_safe'], args['css_ext'], skip_cc=skip_cc)
    elif args['apply']:
        cmd_apply(args['node_id_safe'], args['dest'], args['css_ext'], no_i18n=no_i18n,
                  exact_dest=args.get('exact_dest'))
    else:
        print('请指定 --analyze、--apply、--auto 或 --apply-renames')
        sys.exit(1)


if __name__ == '__main__':
    main()
