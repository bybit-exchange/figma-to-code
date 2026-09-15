from __future__ import annotations
"""
两阶段组件边界检测（基于 IR）。

Phase 1：isComponentInstance + componentId + bb 尺寸 > 32px + count >= 2 → 叶子组件
Phase 2：depth=1 + bb.height > 200px + 非装饰类型 → Section（含 depth 穿透）

输入：ir（ir.json 根节点，含 componentId + bb 字段）+ semantic（备用）
输出：{'leafComponents': [...], 'sections': [...]}
"""
from .instance_merger import group_instances, extract_varying_props, build_instances_data

_DECORATIVE_TYPES = frozenset({
    'LINE', 'ELLIPSE', 'VECTOR', 'RECTANGLE',
    'POLYGON', 'STAR', 'BOOLEAN_OPERATION',
})


def detect_boundaries(ir: dict, semantic: dict, code_connect_map: dict | None = None,
                      components_meta: dict | None = None) -> dict:
    cc_map = code_connect_map or {}

    # 预构建 componentId → icon info 映射（用于 Button icon 子节点查找）
    _icon_comp_id_map: dict = {}  # {componentId: {'name': str, 'import': str}}
    if cc_map:
        _icon_comp_id_map = _build_icon_comp_id_map(ir, cc_map)
    # 补充：从 raw components 元数据按命名规则推导未覆盖的 icon
    if components_meta:
        for cid, comp in components_meta.items():
            if cid in _icon_comp_id_map:
                continue
            raw_name = comp.get('name', '')
            if raw_name.startswith('icon_'):
                icon_comp_name = _icon_raw_name_to_comp(raw_name)
                if icon_comp_name:
                    _icon_comp_id_map[cid] = {
                        'name': icon_comp_name,
                        'import': ''
                    }

    # Code Connect 预处理：找出 IR 中所有 figmaId 在 cc_map 中的 INSTANCE 节点
    cc_components = []
    cc_matched_ids: set = set()
    if cc_map:
        _collect_cc_nodes(ir, cc_map, cc_components, cc_matched_ids,
                          icon_comp_id_map=_icon_comp_id_map)

    # Phase 1：叶子组件（从 IR 聚合）
    # CC 节点仍参与正常分组（保证 count >= 2），后处理时注入 CC 属性
    raw_groups = group_instances(ir, semantic)
    leaf_components = []
    all_leaf_figma_ids: set = set()

    for g in raw_groups:
        varying = extract_varying_props(g['instances'])
        if varying is None:
            # 误分组：内容差异超过阈值，不创建 leaf 组件，各实例 inline 渲染
            continue
        instances_data = build_instances_data(g['instances'], varying)
        figma_ids = {n['figmaId'] for n in g['instances']}
        all_leaf_figma_ids.update(figma_ids)

        # 检查该分组内是否有任意实例命中了 CC
        cc_hit = next(
            (cc for cc in cc_components if cc['nodeId'] in figma_ids),
            None
        )
        leaf_entry: dict = {
            'componentId':   g['componentId'],
            'figmaName':     g['figmaName'],
            'figmaIds':      figma_ids,
            'instances':     g['instances'],
            'varyingProps':  varying,
            'instancesData': instances_data,
            'count':         g['count'],
            'reason':        'repeated-instance',
            'ccComponent': None,
            'ccImport':    None,
            'confidence':    None,
            'source':        None,
        }
        if cc_hit:
            leaf_entry['ccComponent'] = cc_hit['ccComponent']
            leaf_entry['ccImport']    = cc_hit['ccImport']
            leaf_entry['allImports']    = cc_hit.get('allImports', [])
            leaf_entry['snippet']       = cc_hit.get('snippet', '')
            leaf_entry['confidence']    = 1.0
            leaf_entry['source']        = 'code-connect'
        leaf_components.append(leaf_entry)

    # Phase 1b：CC 单次节点——cc_map 命中但未被 group_instances 收集（count=1，非重复组件）
    # 这些节点应作为叶子组件处理，确保 Code Connect 组件替换生效
    for cc_entry in cc_components:
        if cc_entry['nodeId'] not in all_leaf_figma_ids:
            node = cc_entry['node']
            leaf_entry = {
                'componentId':   cc_entry['nodeId'],
                'figmaName':     cc_entry['figmaName'],
                'figmaIds':      {cc_entry['nodeId']},
                'instances':     [node],
                'varyingProps':  [],
                'instancesData': [],
                'count':         1,
                'reason':        'code-connect-single',
                'ccComponent': cc_entry['ccComponent'],
                'ccImport':    cc_entry['ccImport'],
                'allImports':    cc_entry.get('allImports', []),
                'snippet':       cc_entry.get('snippet', ''),
                'confidence':    1.0,
                'source':        'code-connect',
            }
            leaf_components.append(leaf_entry)
            all_leaf_figma_ids.add(cc_entry['nodeId'])

    # Phase 2：Section（IR depth=1 子节点）
    page_children = ir.get('children') or []

    sections, inline_nodes = _find_sections(page_children, leaf_components, all_leaf_figma_ids)

    # depth 穿透：0 sections + 单一 FRAME 包裹层
    if not sections and len(page_children) == 1 and page_children[0].get('figmaType') == 'FRAME':
        inner = page_children[0].get('children') or []
        sections, inline_nodes = _find_sections(inner, leaf_components, all_leaf_figma_ids)

    # 单包装层展开：设计稿中多余的中间 FRAME 层（如 Frame 2147224865）
    # 会将内部各区块压缩为一个 Section。
    # 判断包装层：Section 候选是 FRAME + 直接子节点中无 ComponentInstance/TEXT
    # （所有实质内容在子 FRAME 层）→ 展开，子节点用低阈值（min_h=50）重判。
    # 限制：只对第一层检测结果展开（_from_expansion=False），
    # 避免展开后的子节点被二次展开（如 VolDataSection 被错误拆散）。
    expanded_sections: list = []
    expanded_inlines: list = []
    kept_sections: list = []
    expanded_wrapper_fids: list = []  # 被展开的包装层 figmaId（供 validate_split 跳过）
    for s in sections:
        node = s['node']
        if not s.get('_from_expansion') and _is_wrapper_frame(node):
            sub_s, sub_i = _find_sections(
                node.get('children') or [],
                leaf_components, all_leaf_figma_ids,
                min_h=50,
            )
            if sub_s:  # 展开后有 Section → 用子节点替换包装层
                for ss in sub_s:
                    ss['_from_expansion'] = True  # 标记为展开产物，不再递归展开
                expanded_sections.extend(sub_s)
                expanded_inlines.extend(sub_i)
                fid = node.get('figmaId', '')
                if fid:
                    expanded_wrapper_fids.append(fid)
                continue
        kept_sections.append(s)
    if expanded_sections:
        sections = kept_sections + expanded_sections
        inline_nodes = inline_nodes + expanded_inlines

    # 顶层叶子 = 未被任何 Section 认领
    section_owned_comp_ids = {lc['componentId'] for s in sections for lc in s['leafComponents']}
    top_leaves = [lc for lc in leaf_components if lc['componentId'] not in section_owned_comp_ids]

    return {
        'leafComponents':         top_leaves,
        'sections':               sections,
        'inlineNodes':            inline_nodes,
        'codeConnectComponents':  cc_components,
        'expandedWrapperFigmaIds': expanded_wrapper_fids,
    }


def _icon_raw_name_to_comp(raw_name: str) -> str:
    """将 Figma raw component name 转换为 icon 组件名（PascalCase）。
    例如: 'icon_fire solid' → 'IconFireSolid', 'icon_share' → 'IconShare'"""
    if not raw_name.startswith('icon_'):
        return ''
    # 去掉 icon_ 前缀，按空格和下划线分词，PascalCase
    suffix = raw_name[5:]  # remove 'icon_'
    words = suffix.replace('_', ' ').split()
    return 'Icon' + ''.join(w.capitalize() for w in words)


def _build_icon_comp_id_map(ir: dict, cc_map: dict) -> dict:
    """遍历 IR，找到 CC map 中 Icon 条目对应的 componentId，构建 componentId→icon 映射。"""
    icon_entries = {}
    for fid, entry in cc_map.items():
        cn = entry.get('componentName', '')
        if cn.startswith('Icon') or cn.endswith('Icon'):
            icon_entries[fid] = entry

    if not icon_entries:
        return {}

    result: dict = {}

    def _scan(node: dict):
        fid = node.get('figmaId', '')
        if fid in icon_entries:
            cid = node.get('componentId', '')
            if cid and cid not in result:
                entry = icon_entries[fid]
                imports = entry.get('allImports', [])
                imp_str = imports[0] if imports else ''
                result[cid] = {'name': entry['componentName'], 'import': imp_str}
        for c in (node.get('children') or []):
            _scan(c)

    _scan(ir)
    return result


def _collect_cc_nodes(node: dict, cc_map: dict, cc_components: list, cc_matched_ids: set,
                      _cc_name_index: dict | None = None,
                      icon_comp_id_map: dict | None = None) -> None:
    """递归遍历 IR 树，收集所有 componentId 或 figmaId 在 cc_map 中的 INSTANCE 节点。
    匹配优先级: figmaId > componentId > 组件名称 fallback。"""
    if _cc_name_index is None:
        _cc_name_index = {}
        for _k, _v in cc_map.items():
            _name = _v.get('componentName', '')
            if _name and _name not in _cc_name_index:
                _cc_name_index[_name] = _v
    fid = node.get('figmaId', '')
    cid = node.get('componentId', '')
    cc_key = fid if fid in cc_map else (cid if cid in cc_map else '')
    cc: dict | None = None
    if cc_key:
        cc = cc_map[cc_key]
    # 不使用名称 fallback：只有精确匹配 CC（figmaId 或 componentId）的节点才转为 CC 组件
    if cc and node.get('isComponentInstance'):
        all_imports = list(cc.get('allImports', []))
        snippet = cc.get('snippet', '')

        # Button 专属：检测子节点中的 icon，补充 icon import
        _has_icon_import = any('Icon' in imp for imp in all_imports)
        if cc['componentName'] == 'Button' and not _has_icon_import:
            for child in (node.get('children') or []):
                child_name = (child.get('figmaName') or '').lower()
                if child.get('isComponentInstance') and 'icon' in child_name:
                    child_cid = child.get('componentId', '')
                    icon_info = (icon_comp_id_map or {}).get(child_cid)
                    if icon_info:
                        icon_imp = icon_info['import']
                        if icon_imp not in all_imports:
                            all_imports.append(icon_imp)
                    else:
                        # 无法映射的 icon，注入注释标记
                        if '{/* icon:' not in snippet:
                            _close = snippet.find('>')
                            if _close != -1 and not snippet.rstrip().endswith('/>'):
                                snippet = snippet[:_close + 1] + f'\n  {{/* icon: {child_name} (componentId={child_cid}) */}}' + snippet[_close + 1:]

        cc_components.append({
            'nodeId':       fid,
            'figmaName':    node.get('figmaName', ''),
            'ccComponent': cc['componentName'],
            'ccImport':   cc['ccImport'],
            'allImports':   all_imports,
            'snippet':      snippet,
            'confidence':   1.0,
            'source':       'code-connect',
            'isIcon':       cc.get('isIcon', False),
            'node':         node,
        })
        cc_matched_ids.add(fid)
        return  # CC 匹配的组件实例不递归子节点（内部按钮等不应被独立提取）
    for child in (node.get('children') or []):
        _collect_cc_nodes(child, cc_map, cc_components, cc_matched_ids, _cc_name_index,
                          icon_comp_id_map=icon_comp_id_map)


def _find_sections(children: list, leaf_components: list, leaf_figma_ids: set,
                   min_h: int = 200):
    """返回 (sections, inline_nodes)。
    sections：h>min_h 的非装饰节点（包含大型 ComponentInstance）。
    inline_nodes：其余需在 Page.tsx 中 inline 渲染的节点（保全所有 figma-id）。
    min_h：高度阈值，默认 200；包装层展开时传入更低阈值（50）以识别 nav bar 等小节。
    """
    sections = []
    inline_nodes = []
    for child in children:
        ftype = child.get('figmaType', '')
        # 真正的装饰层（isDecorativeElement=True）才彻底跳过
        if child.get('isDecorativeElement'):
            continue
        # 已被叶子组件识别的实例跳过（leaf_figma_ids 里的 instance 由叶子逻辑处理）
        if child.get('isComponentInstance') and child.get('figmaId', '') in leaf_figma_ids:
            continue
        bb = child.get('bb') or {}
        h = bb.get('height', 0)
        child_ids: set = set()
        _collect_ids(child, child_ids)
        owned = [lc for lc in leaf_components if lc['figmaIds'].intersection(child_ids)]
        # h>min_h 的非装饰节点 → Section。
        # 大型 ComponentInstance（如 TomorrowLand 的页面模块）也作为 Section，
        # 因为 leaf 逻辑只提取"重复出现"的实例；唯一大型实例是独立功能区块。
        if h > min_h and ftype not in _DECORATIVE_TYPES:
            sections.append({
                'node':          child,
                'figmaName':     child.get('figmaName', ''),
                'reason':        'top-level-section',
                'leafComponents': owned,
            })
        else:
            # 小节点、装饰类型 → inline 渲染到 Page.tsx，保全 figma-id + CSS
            inline_nodes.append({'node': child, 'figmaName': child.get('figmaName', '')})
    return sections, inline_nodes


def _is_wrapper_frame(node: dict) -> bool:
    """检测节点是否为单一包装层 FRAME（设计稿多余的中间层）。

    包装层特征：既有大子框架（高度 > 200px）又有小子框架（高度 < 100px）。
    小子框架（nav 下方的选择器、标题等）的存在说明这一层本身有布局结构，
    是需要被"穿透"的中间容器，而非纯内容 Section。

    与"有子 Section 的普通 Section"区别：
    - 纯内容 Section（如 VolDataSection）：所有子 FRAME 都是大块（h >= 100px），
      内部各子块均为有意义的子 Section，父级本身不被展开。
    - 包装层（如 Frame 2147224865）：有 h < 100 的小子框架夹杂在大框架之间，
      说明父级是一个包含各种元素的"容器层"而非内容 Section。

    条件：
    1. figmaType == 'FRAME'（不是 ComponentInstance）
    2. 直接子节点中无 ComponentInstance 或 TEXT（内容在子 FRAME 层）
    3. 直接 FRAME 子节点 ≥ 2 个
    4. 至少 1 个直接 FRAME 子节点 h > 200（内部有大区块）
    5. 至少 1 个直接 FRAME 子节点 h < 100（有小的 inline-worthy 元素）← 关键区分点
    """
    if node.get('figmaType') != 'FRAME' or node.get('isComponentInstance'):
        return False
    # 有实质性 padding（≥50px）的 FRAME 是真正的内容 Section，不是包装层。
    # 包装层（如 Frame 2147224865）通常没有大 padding；有大 padding 的容器
    # （如 Hot assets - V3 的 padding: 100px 120px 20px 120px）应保留完整结构。
    _css = node.get('css') or {}
    _pad_str = _css.get('padding', '') or _css.get('padding-top', '') or ''
    if _pad_str:
        # 解析 padding shorthand：1-4 个 px 值
        _pad_vals = []
        for part in str(_pad_str).split():
            try:
                _pad_vals.append(abs(float(part.replace('px', ''))))
            except ValueError:
                pass
        if _pad_vals and max(_pad_vals) >= 50:
            return False
    children = node.get('children') or []
    # 有直接 ComponentInstance 或 TEXT 子节点 → 不是纯包装层，有自身内容
    has_direct_content = any(
        c.get('isComponentInstance') or c.get('figmaType') == 'TEXT'
        for c in children
        if not c.get('isDecorativeElement')
    )
    if has_direct_content:
        return False
    frame_children = [c for c in children
                      if c.get('figmaType') == 'FRAME' and not c.get('isDecorativeElement')]
    if len(frame_children) < 2:
        return False
    heights = [(c.get('bb') or {}).get('height', 0) for c in frame_children]
    has_large = any(h > 200 for h in heights)   # 有大区块
    has_small = any(h < 100 for h in heights)   # 有小 inline 元素
    return has_large and has_small


def _collect_ids(node: dict, result: set) -> None:
    result.add(node.get('figmaId', ''))
    for child in (node.get('children') or []):
        _collect_ids(child, result)
