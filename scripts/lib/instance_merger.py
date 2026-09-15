from __future__ import annotations
"""
Phase 1 叶子组件识别：从 IR 聚合重复节点 + zip-walk 差异提取 + IR 节点标记。

输入：ir_tree（ir.json 根节点，含 componentId + bb 字段）
输出：分组列表 + 差异 props + IR 节点标记
"""
import re
from collections import Counter, defaultdict


_ICON_MAX_SIZE = 32   # px，宽或高 ≤ 32px 视为图标/分隔线，跳过
_SIZE_BUCKET   = 20   # px，结构哈希尺寸 bucket 粒度
_STRUCT_DEPTH  = 3    # 结构哈希递归深度
_DECO_TYPES    = frozenset({
    'LINE', 'ELLIPSE', 'VECTOR', 'RECTANGLE',
    'POLYGON', 'STAR', 'BOOLEAN_OPERATION',
})


def _struct_key(node: dict, depth: int = 0, *, deep: bool = False) -> str:
    """
    计算节点树的结构指纹（不含文本/图片内容，只含形状和层级）。
    替代 figmaName 作为 Pass B 的分组 key：
      - 相同结构（即使 figmaName 不同）→ 相同 key → 同一组
      - 相同 figmaName 但内部结构不同 → 不同 key → 不分组

    deep=True 时不对顶层 INSTANCE 节点做 early return，用于 Pass A 内部
    区分相同 componentId 但子树结构不同的 variant 实例。
    """
    ftype = node.get('figmaType', '')
    bb    = node.get('bb') or {}
    h = round(bb.get('height', 0) / _SIZE_BUCKET) * _SIZE_BUCKET
    w = round(bb.get('width',  0) / _SIZE_BUCKET) * _SIZE_BUCKET
    # depth>=1 的内层节点宽度由内容驱动（文字长短、hug-content 容器等），
    # 不应参与结构哈希；否则文本内容不同的同结构卡片会产生不同 key。
    if depth >= 1:
        w = 0
        # 图片/矢量节点的高度由图片内容驱动，不是结构属性；布局容器（FRAME/GROUP/SECTION 等）
        # 的高度由内容 Auto Layout 决定，同样不是结构属性。两者均清零，只保留节点类型
        # 和层级作为结构指纹。TEXT 节点高度暂保留（行高属于排版结构的一部分）。
        if node.get('isImageNode') or node.get('isVectorNode') or not node.get('isTextNode'):
            h = 0
    flags = ('T' if node.get('isTextNode') else '') + ('I' if node.get('isImageNode') else '')

    # Component Instance：用 componentId 作为子树代表，不深入递归
    # deep 模式下跳过此 early return（仅对顶层节点生效，子节点仍走 depth 限制）
    # depth>=1 的内层 INSTANCE（如卡片内的币种图表槽位）使用占位符 '_' 代替具体
    # componentId，避免不同内容的同结构卡片因 child componentId 不同而产生不同 key。
    if node.get('isComponentInstance') and node.get('componentId') and not deep:
        cid = '_' if depth >= 1 else node['componentId']
        return f'inst:{cid}_{h}x{w}'

    if depth >= _STRUCT_DEPTH:
        return f'{ftype}_{h}x{w}{flags}[…]'

    children    = node.get('children') or []
    child_parts = ','.join(_struct_key(c, depth + 1) for c in children)
    return f'{ftype}_{h}x{w}{flags}[{child_parts}]'


def group_instances(ir_tree: dict, semantic: dict = None, skip_ids: set | None = None) -> list:
    """
    从 IR 树聚合重复节点，返回叶子组件分组：
      [{'componentId', 'figmaName', 'instances': [ir_node...], 'count'}]

    Pass A：isComponentInstance=True + 相同 componentId（Figma 官方结构哈希）
    Pass B：isComponentInstance=False + 相同 _struct_key（计算出的结构指纹）
            用结构哈希替代 figmaName：名字不同但结构相同 → 同一组；
            名字相同但结构不同（Container 误分组）→ 不同组。

    过滤：count < 2 | size ≤ 32px | 装饰类型(RECT/ELLIPSE/VECTOR) | nChildren = 0
    祖先过滤：Pass B 按面积从大到小处理，被大节点覆盖的内层重复结构自动跳过。
    skip_ids：figmaId 集合，这些节点由 Code Connect 直接处理，跳过正常分组路径。
    """
    comp_groups:   dict = defaultdict(list)  # Pass A: componentId → nodes
    struct_groups: dict = defaultdict(list)  # Pass B: struct_key → nodes
    parent_map:    dict = {}                 # figmaId → parent figmaId

    _walk(ir_tree, comp_groups, struct_groups, parent_map, parent_fid=None, skip_ids=skip_ids or set())

    result    = []
    seen_fids: set = set()

    def _is_descendant(fid: str) -> bool:
        """沿 parent_map 向上追溯，若经过已 seen 的节点则为后代。"""
        cur = fid
        while cur:
            if cur in seen_fids:
                return True
            cur = parent_map.get(cur)
        return False

    # Pass A 结果
    # 相同 componentId 的实例可能是不同 variant（内部子树结构差异很大），
    # 需要按结构指纹细分子组，避免将 BaseLayer 和 VIPLayer 这样结构完全不同
    # 的实例强行合并为同一叶子组件。
    for comp_id, nodes in comp_groups.items():
        if len(nodes) < 2:
            continue
        rep = nodes[0]
        bb  = rep.get('bb') or {}
        if bb.get('width', 0) <= _ICON_MAX_SIZE or bb.get('height', 0) <= _ICON_MAX_SIZE:
            continue
        # 按结构指纹 + 根节点颜色签名细分：
        # 结构相同但视觉颜色不同的实例（如绿色/红色按钮 variant 通过手动 fill override
        # 实现，而非 Figma Variant）分到各自的子组，避免 needs_wrapper 将颜色不同的
        # 实例包裹在另一颜色组件外层导致 CSS 覆盖和 figma-id 重复错误。
        # 颜色签名：所有影响视觉的颜色属性（background-color / background /
        # color / border-color / outline-color 等）拼接成一个 key。
        # deep=True 确保 INSTANCE 节点不做 early return，深入对比子树结构
        _COLOR_PROPS = ('background-color', 'background', 'color',
                        'border', 'border-color', 'border-radius',
                        'outline', 'outline-color', 'box-shadow')
        sub_groups: dict = defaultdict(list)
        for n in nodes:
            sk = _struct_key(n, deep=True)
            root_css = n.get('css', {})
            color_sig = '|'.join(root_css.get(p, '') for p in _COLOR_PROPS)
            sub_groups[(sk, color_sig)].append(n)
        for sg_nodes in sub_groups.values():
            if len(sg_nodes) < 2:
                continue
            result.append({
                'componentId': comp_id,
                'figmaName':   sg_nodes[0].get('figmaName', ''),
                'instances':   sg_nodes,
                'count':       len(sg_nodes),
            })
            for n in sg_nodes:
                seen_fids.add(n.get('figmaId', ''))

    # Pass B 结果：count 优先（次数多的内层卡片先提取），同 count 时面积大的优先
    def _area(nodes):
        bb = nodes[0].get('bb') or {}
        return bb.get('width', 0) * bb.get('height', 0)

    # count 优先：出现次数多的节点（内层重复卡片）先于面积大的外层容器提取
    for key, nodes in sorted(struct_groups.items(), key=lambda kv: (-len(kv[1]), -_area(kv[1]))):
        if len(nodes) < 2:
            continue
        nodes = [n for n in nodes if not _is_descendant(n.get('figmaId', ''))]
        if len(nodes) < 2:
            continue
        rep = nodes[0]
        bb  = rep.get('bb') or {}
        if bb.get('width', 0) <= _ICON_MAX_SIZE or bb.get('height', 0) <= _ICON_MAX_SIZE:
            continue
        # 取出现最多的 figmaName 作为显示名（结构相同但名字可能不同）
        name_counts = Counter(n.get('figmaName', '') for n in nodes)
        display_name = name_counts.most_common(1)[0][0] or 'Component'
        result.append({
            'componentId': f'__struct__{key[:24]}',  # 合成 ID，与 Pass A 区分
            'figmaName':   display_name,
            'instances':   nodes,
            'count':       len(nodes),
        })
        for n in nodes:
            seen_fids.add(n.get('figmaId', ''))

    # 孤节点吸收：Pass B 结束后，对 count=1 的未归组节点，
    # 若与某已有 group 仅差一个 height≤_BADGE_MAX_H 且无子节点的 FRAME 子节点，
    # 则将其吸收进该 group（optional badge 模式）。
    # 典型场景：纵向块中 card1 有"New User Exclusive" badge，card2/3 没有。
    _BADGE_MAX_H = 30  # px

    orphan_candidates = [
        nodes_list[0]
        for sk, nodes_list in struct_groups.items()
        if len(nodes_list) == 1 and nodes_list[0].get('figmaId', '') not in seen_fids
    ]
    for orphan in orphan_candidates:
        o_ch = orphan.get('children') or []
        absorbed = False
        for g in result:
            if absorbed:
                break
            t_ch = g['instances'][0].get('children') or []
            if len(o_ch) != len(t_ch) + 1:
                continue
            for i in range(len(o_ch)):
                remaining = o_ch[:i] + o_ch[i + 1:]
                if len(remaining) != len(t_ch):
                    continue
                structs_match = all(
                    _struct_key(r, 1) == _struct_key(t, 1)
                    for r, t in zip(remaining, t_ch)
                )
                if not structs_match:
                    continue
                extra = o_ch[i]
                h_extra = (extra.get('bb') or {}).get('height', 100)
                has_extra_children = bool(extra.get('children'))
                is_badge = (
                    extra.get('figmaType') == 'FRAME'
                    and not extra.get('isComponentInstance')
                    and not has_extra_children
                    and h_extra <= _BADGE_MAX_H
                )
                if is_badge:
                    g['instances'].append(orphan)
                    g['count'] += 1
                    seen_fids.add(orphan.get('figmaId', ''))
                    absorbed = True
                    break

    return sorted(
        result,
        key=lambda x: -(
            (x['instances'][0].get('bb') or {}).get('width', 0) *
            (x['instances'][0].get('bb') or {}).get('height', 0)
        )
    )


def _walk(node: dict, comp_groups: dict, struct_groups: dict,
          parent_map: dict, parent_fid: str | None, skip_ids: set | None = None) -> None:
    fid = node.get('figmaId', '')
    if fid and parent_fid:
        parent_map[fid] = parent_fid

    if skip_ids and fid in skip_ids:
        return

    if node.get('isComponentInstance') and node.get('componentId'):
        comp_groups[node['componentId']].append(node)
    elif not node.get('isComponentInstance'):
        ftype      = node.get('figmaType', '')
        bb         = node.get('bb') or {}
        h, w       = bb.get('height', 0), bb.get('width', 0)
        n_children = len(node.get('children') or [])
        # 过滤：装饰类型、无子节点（纯图形/图片）、太小的节点
        if (h > _ICON_MAX_SIZE and w > _ICON_MAX_SIZE
                and ftype not in _DECO_TYPES
                and n_children >= 1):
            # 用结构哈希代替 figmaName，精确识别"相同结构"
            key = _struct_key(node)
            struct_groups[key].append(node)

    for child in (node.get('children') or []):
        _walk(child, comp_groups, struct_groups, parent_map, fid, skip_ids=skip_ids)


# ── 差异提取（zip-walk，操作 IR 节点）─────────────────────────────────────────

_MAX_VARYING_PROPS = 8  # 超过此阈值说明两个实例内容差异过大，不是同一组件


def extract_varying_props(instances: list) -> list | None:
    """
    Zip-walk 对比 N 个 IR 实例树，返回差异 prop 列表：
      text 差异：[{'path': [...], 'propName': str, 'type': 'text', 'values': [...]}]
      image 差异：[{'path': [...], 'propName': str, 'type': 'image', 'values': [...]}]
    超过 _MAX_VARYING_PROPS 时返回 None（误分组信号，调用方应跳过该分组，inline 渲染）。
    无差异时返回 []（实例完全相同，可共用同一组件）。
    """
    if len(instances) < 2:
        return []
    props: list = []
    _walk_diff(instances[0], instances[1:], path=[], props=props)
    # 超过阈值：两实例结构相似但内容完全不同，是误分组（如期权PB的不同功能面板）
    # 返回 None 而非 []，让调用方跳过该分组，各实例 inline 渲染，避免内容丢失
    if len(props) > _MAX_VARYING_PROPS:
        return None
    # 同一 leaf 内多个节点可能有相同 figmaName（如 "text1"），导致重复 prop 名。
    # 从第二次起追加序号（text1 → text1、text12、text13 …）。
    seen: dict = {}
    for vp in props:
        name = vp['propName']
        if name in seen:
            seen[name] += 1
            vp['propName'] = f'{name}{seen[name]}'
        else:
            seen[name] = 1
    return props


def _node_at_path(root: dict, path: list):
    node = root
    for idx in path:
        children = node.get('children') or []
        if idx >= len(children):
            return None
        node = children[idx]
    return node


def _has_text_descendants(n: dict) -> bool:
    """节点树中是否有 TEXT 子孙节点（有文字内容）。有文字的容器不是纯图标，不应作为 icon_tree。"""
    if n.get('isTextNode') and n.get('textContent'):
        return True
    for c in (n.get('children') or []):
        if _has_text_descendants(c):
            return True
    return False


def _count_assets(n: dict) -> int:
    """递归计算节点内 image/vector 子孙（含自身）的 localAssetPath 数量。
    返回值 > 1 表示多层 SVG 合成图标，需整体作为 ReactNode prop 传入。
    超过 2 个就提前返回，调用方只需判断 >1。
    """
    cnt = 1 if ((n.get('isImageNode') or n.get('isVectorNode')) and n.get('localAssetPath')) else 0
    for c in (n.get('children') or []):
        cnt += _count_assets(c)
        if cnt > 1:
            return cnt
    return cnt


def _find_first_lap(n: dict) -> str:
    """深度优先找到第一个有 localAssetPath 的 image/vector 子孙（含自身）。"""
    if (n.get('isImageNode') or n.get('isVectorNode')) and n.get('localAssetPath'):
        return n['localAssetPath']
    for c in (n.get('children') or []):
        r = _find_first_lap(c)
        if r:
            return r
    return ''


def _walk_diff(template: dict, others: list, path: list, props: list) -> None:
    # 文本节点：比较 textContent
    if template.get('isTextNode'):
        values = [template.get('textContent', '')] + [
            (_node_at_path(o, path) or {}).get('textContent', '') for o in others
        ]
        if len(set(str(v) for v in values)) > 1:
            name = _to_camel(template.get('figmaName', ''))
            if not name or re.match(r'^(node|frame|text|group)\d*$', name, re.IGNORECASE):
                name = ''
                text = template.get('textContent', '')
                words = re.findall(r'[a-zA-Z]{2,}', text)
                if words:
                    name = words[0].lower() + ''.join(w.capitalize() for w in words[1:3])
            if not name:
                name = f'text{path[-1] if path else 0}'
            # 收集各实例的 textSegments（用于在调用处生成多色 JSX）
            raw_segments = [template.get('textSegments')] + [
                (_node_at_path(o, path) or {}).get('textSegments') for o in others
            ]
            # 收集 CSS 文字颜色，用于检测实例间颜色变体
            template_color = template.get('css', {}).get('color', '')
            all_css_colors = [template_color] + [
                (_node_at_path(o, path) or {}).get('css', {}).get('color', '')
                for o in others
            ]
            has_color_variants = len(set(str(c) for c in all_css_colors)) > 1
            # 丰富 segments：
            # 1. 有 textSegments + CSS 颜色不同 → 为 trailing 文本补显式颜色 segment
            # 2. 无 textSegments + CSS 颜色不同 → 将整段文本包成显式颜色 segment
            all_segments = []
            for idx, (segs, val, css_color) in enumerate(
                    zip(raw_segments, values, all_css_colors)):
                if idx > 0 and css_color and css_color != template_color:
                    if segs:
                        # 补充 trailing 文本的显式颜色 segment
                        covered = ''.join(s.get('text', '') for s in segs)
                        trailing = (val[len(covered):]
                                    if isinstance(val, str) and val.startswith(covered)
                                    else '')
                        if trailing:
                            segs = list(segs) + [{'text': trailing, 'color': css_color}]
                    elif has_color_variants and val:
                        # 无 textSegments 但颜色不同：整段文本显式着色
                        segs = [{'text': str(val), 'color': css_color}]
                all_segments.append(segs)
            has_segments = any(s for s in all_segments)
            entry = {'path': path[:], 'type': 'text', 'propName': name, 'values': values}
            if has_segments:
                entry['segments'] = all_segments
            props.append(entry)
        return

    # 图片/矢量节点：比较 localAssetPath（图片节点 isImageNode=True，SVG 图标 isVectorNode=True）
    if template.get('isImageNode') or template.get('isVectorNode'):
        _all_img_nodes = [template] + [(_node_at_path(o, path) or {}) for o in others]
        values = [n.get('localAssetPath', '') for n in _all_img_nodes]
        # 只有所有实例都有有效路径才认为是 varying（空值 = 路径不存在 = 结构不对称，跳过）
        if len(set(str(v) for v in values)) > 1 and all(v for v in values):
            name = _to_camel(template.get('figmaName', '')) or f'img{path[-1] if path else 0}'
            _entry = {'path': path[:], 'type': 'image', 'propName': name, 'values': values}
            # 捕获各实例 CSS 位置/尺寸差异，供调用方通过 {propName}Style 传入内联覆盖
            _CSS_POS = frozenset({'width', 'height', 'left', 'top', 'right', 'bottom',
                                  'min-width', 'min-height'})
            _css_vals = [
                {k: v for k, v in (n.get('css') or {}).items() if k in _CSS_POS}
                for n in _all_img_nodes
            ]
            if len(set(str(c) for c in _css_vals)) > 1:
                _entry['cssValues'] = _css_vals
            props.append(_entry)
        return

    # 多层图标整体检测：template 内有多个 image/vector 子孙（多层 SVG 合成图标），
    # 不应拆散成多个独立 image prop，而应整体作为 icon_tree prop 传入。
    # 检测条件：max(_count_assets across all nodes) > 1，且首层路径在实例间不同。
    # 护栏：有文字子孙节点的容器（Row/Card 等）不是纯图标，不触发。
    # 注意：不再使用尺寸护栏（_ICON_TREE_MAX_SIZE），因为图标尺寸差异很大（40-128px），
    # 而复杂容器的真正区分特征是"包含文字"，与尺寸无关。
    _others_nodes = [(_node_at_path(o, path) or {}) for o in others]
    _all_at_path = [template] + _others_nodes
    if not _has_text_descendants(template):
        _max_assets = max(_count_assets(n) for n in _all_at_path)
        if _max_assets > 1:
            _lap_values = [_find_first_lap(n) for n in _all_at_path]
            if len(set(str(v) for v in _lap_values)) > 1 and all(v for v in _lap_values):
                name = _to_camel(template.get('figmaName', '')) or f'icon{path[-1] if path else 0}'
                props.append({'path': path[:], 'type': 'icon_tree', 'propName': name,
                             'values': _all_at_path})  # 存储 IR 节点，供 tsx_generator 渲染 JSX fragment
                return  # 整体作为图标 prop，不递归子节点

    # 结构不对称图标检测：模板处是容器（非 image/vector）但某些 other 实例在同路径
    # 直接是 image/vector 节点（有 localAssetPath）。单层图标整体作为 image prop。
    _any_other_is_asset = any(
        (on.get('isImageNode') or on.get('isVectorNode')) and on.get('localAssetPath', '')
        for on in _others_nodes
    )
    if _any_other_is_asset:
        _values = [_find_first_lap(template)] + [
            on.get('localAssetPath', '') if (on.get('isImageNode') or on.get('isVectorNode'))
            else _find_first_lap(on)
            for on in _others_nodes
        ]
        if len(set(str(v) for v in _values)) > 1 and all(v for v in _values):
            name = _to_camel(template.get('figmaName', '')) or f'icon{path[-1] if path else 0}'
            props.append({'path': path[:], 'type': 'image', 'propName': name, 'values': _values})
            return  # 整体作为图标 prop，不再递归子节点

    for i, child in enumerate(template.get('children') or []):
        _walk_diff(child, others, path + [i], props)


# ── 实例数据构建 ───────────────────────────────────────────────────────────────

def build_instances_data(instances: list, varying_props: list) -> list:
    """返回每个实例的 prop 数据字典列表：[{propName: value, ...}]"""
    result = []
    for idx in range(len(instances)):
        row = {vp['propName']: (vp['values'][idx] if idx < len(vp['values']) else '')
               for vp in varying_props}
        # 存储 textSegments 信息（用于在调用处生成多色 JSX）
        for vp in varying_props:
            segs = vp.get('segments')
            if segs and idx < len(segs) and segs[idx]:
                row[f'__segments__{vp["propName"]}'] = segs[idx]
        # 存储 per-instance CSS 样式（用于在调用处生成 imageStyle 内联覆盖）
        for vp in varying_props:
            css_vals = vp.get('cssValues')
            if css_vals and idx < len(css_vals) and css_vals[idx]:
                row[f'{vp["propName"]}Style'] = css_vals[idx]
        result.append(row)
    return result


# ── IR 节点标记（检测结果 → 代码生成）──────────────────────────────────────────

def mark_varying_nodes(template_ir: dict, varying_props: list) -> None:
    """
    In-place：在 IR 节点的对应路径处设置 _prop_name/_prop_type，
    供 tsx_generator._render_node 替换为 {propName}。
    """
    for vp in varying_props:
        node = _node_at_path(template_ir, vp['path'])
        if node is not None:
            node['_prop_name'] = vp['propName']
            _is_reactnode = vp.get('segments') or vp.get('type') == 'icon_tree'
            node['_prop_type'] = 'React.ReactNode' if _is_reactnode else 'string'
            node['_prop_kind'] = vp.get('type', 'text')  # 'text' | 'image' | 'icon_tree'
            if vp.get('cssValues'):
                node['_css_style_prop'] = f'{vp["propName"]}Style'


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _to_kebab(s: str) -> str:
    s = re.sub(r'([a-z])([A-Z])', r'\1-\2', s)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1-\2', s)
    s = re.sub(r'([a-zA-Z])([0-9])', r'\1-\2', s)
    # 用 re.sub 替换所有 Unicode 空白（含 \xa0 非断行空格），而非只替换 ASCII 空格
    return re.sub(r'\s+', '-', s.lower()).strip('-')


def _to_camel(s: str) -> str:
    """将 figmaName 转为合法的 camelCase JS 标识符名。
    figmaName 有时等于文本内容（中文/特殊字符），直接用会产生非法标识符。
    先移除所有非 ASCII 字母/数字字符，再做 camelCase 转换。
    返回空字符串表示无法从 figmaName 推断合法名，调用方应用 path-based fallback。
    """
    # 只保留 ASCII 字母和数字（中文、中文标点、省略号等全部过滤掉）
    ascii_only = re.sub(r'[^a-zA-Z0-9\s\-_]', ' ', s)
    kebab = _to_kebab(ascii_only)
    parts = kebab.split('-')
    name  = parts[0] + ''.join(p.capitalize() for p in parts[1:] if p)
    name  = re.sub(r'^[0-9]+', '', name)
    return name  # 空字符串时由调用方使用 path-based fallback
