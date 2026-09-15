from __future__ import annotations
"""
从拆分计划生成新目录结构。

落地结构（暂存在 out_dir/）：
  index.tsx              ← 页面组件入口（与 dest/ 结构完全一致，直接可复制）
  {PageName}.module.less
  hooks/
    usePageEnv.ts
  components/            ← sections 层（与 dest/components/ 直接对应）
    {SectionName}/
      index.tsx          ← Section 主文件
      index.module.less
      {LeafName}.tsx     ← 平铺叶子文件
      {LeafName}.module.less
  common/                ← 顶层叶子（跨 Section）
    {LeafName}.tsx
    {LeafName}.module.less

Plan 格式：
{
  'pageComponent': 'MyPage',
  'nodeId': '169-33787',
  'cssExt': 'less',
  'leafComponents': [...],   # 顶层叶子（cross-section）
  'sections': [
    {
      'name': 'HeroSection',
      'ir': {...},
      'ccComponent': None,
      'leafComponents': [    # Section 内部叶子
        {'name', 'ir', 'varyingProps', 'instancesData', 'ccComponent'}
      ]
    }
  ]
}
"""

import re
import json
from pathlib import Path
import re as _re
from .paths import PAGE_CODE_DIR, IR_DIR, code_connect_file, INDEX_TS_FILENAME, INDEX_TSX_FILENAME, PAGE_TSX_FILENAME, HOOKS_SUBDIR, COMPONENTS_SUBDIR, USE_PAGE_ENV_FILENAME, texts_ts_filename, texts_defaults_ts_filename


def _find_page_code_dirs(node_id: str) -> list:
    """Find page-code directories matching node_id (new format *-{id} first, then old {id}-*)."""
    code_base = PAGE_CODE_DIR
    if not code_base.exists():
        return []
    return ([d for d in code_base.glob(f'*-{node_id}/') if d.is_dir()] +
            [d for d in code_base.glob(f'{node_id}-*/') if d.is_dir()])


from . import tsx_generator as _tsx_mod
from .tsx_generator import (
    generate_tsx, render_jsx_body, render_jsx_body_with_leaf_refs,
    collect_text_tokens_flat, generate_texts_file, generate_texts_defaults_file,
    parse_existing_texts_keys, detect_page_theme,
)
from .scss_generator import generate_scss, _collect_classes, _render_rule, _inject_var_fallbacks
from .instance_merger import mark_varying_nodes
from .normalize_classes import normalize_class_names_in_place


def _read_root_class(node_id_safe: str, page_name: str, css_ext: str) -> str | None:
    for dir_pattern in (f'{page_name}-{node_id_safe}', f'{node_id_safe}-{page_name}'):
        candidates = [
            PAGE_CODE_DIR / dir_pattern / f'{page_name}.module.{css_ext}',
            PAGE_CODE_DIR / dir_pattern / f'{page_name}.module.scss',
            PAGE_CODE_DIR / dir_pattern / f'{page_name}.module.less',
        ]
        for path in candidates:
            if path.exists():
                m = re.search(r'^\.([\w-]+)\s*\{', path.read_text(), re.MULTILINE)
                if m:
                    return m.group(1)
    return None


def _is_section_overlay(node_y: float, node_h: float,
                        section_y_ranges: list) -> bool:
    """判断 inline node 是否为 Section 的视觉叠加层（overlay）。
    overlay 的节点应保持 position:absolute，不转为 flow 布局。

    判断条件：节点必须「从某个 Section 内部出发」AND「在某个 Section 内部结束」。
    这样可以同时处理两种 overlay 模式：
      1. 完全落在单个 section 内（经典 overlay）
      2. 跨多个连续 section（如页面级背景图/deco 层，见 desktop1526 6777:36044）

    不算 overlay 的情况（INL-12/18 精确回归保护）：
      - 节点从 section 外部出发（node_y < section_start）
      - 节点延伸超出所有 section（node_bottom > 所有 section_end）
    """
    if node_y == float('inf') or node_h <= 0 or not section_y_ranges:
        return False
    node_bottom = node_y + node_h
    # 条件 1：节点的起始点在某个 section 内（sy_start <= node_y < sy_end）
    starts_inside = any(sy_start <= node_y < sy_end for sy_start, sy_end in section_y_ranges)
    if not starts_inside:
        return False
    # 条件 2：节点的结束点在某个 section 内（sy_start < node_bottom <= sy_end）
    ends_inside = any(sy_start < node_bottom <= sy_end for sy_start, sy_end in section_y_ranges)
    return ends_inside


def _compute_section_gap(section_top_px: float, section_y: float | None,
                         inline_nodes: list, all_sections: list,
                         current_section_figma_id: str | None = None) -> float:
    """计算 Section 根节点上方的真实视觉间距（用于 margin-top）。

    section_top_px 是该 section 在 Figma 画布中相对于页面根的 Y 偏移。
    这个偏移 = 上方内容的总高度 + 真实间距。
    本函数从中减去"上方内容总高度"，得到真正需要的 margin-top。

    若 section_y 不可用，退化返回原始 section_top_px（保持原行为）。
    """
    if section_y is None or section_y == float('inf') or section_top_px <= 0:
        return section_top_px
    page_root_y = section_y - section_top_px
    content_bottom = 0.0
    # 统计所有 inline nodes（在本 section 之上的）的底部（相对坐标）
    for node in inline_nodes:
        node_y = node.get('y')
        if node_y is None or node_y == float('inf'):
            continue
        if node_y >= section_y:
            continue
        node_h = (node.get('ir') or {}).get('bb', {}).get('height', 0) or 0
        node_rel_top = node_y - page_root_y
        content_bottom = max(content_bottom, node_rel_top + node_h)
    # 统计所有在本 section 之上的其他 sections 的底部
    for s in all_sections:
        if s.get('ir', {}).get('figmaId') == current_section_figma_id:
            continue
        s_y = s.get('y')
        if s_y is None or s_y == float('inf') or s_y >= section_y:
            continue
        s_css = (s.get('ir') or {}).get('css', {})
        _m = re.match(r'([0-9.]+)px', str(s_css.get('top', '0px')))
        s_top_px = float(_m.group(1)) if _m else 0
        s_h = (s.get('ir') or {}).get('bb', {}).get('height', 0) or 0
        content_bottom = max(content_bottom, s_top_px + s_h)
    return max(0.0, section_top_px - content_bottom)


_FLOW_OVERLAP_TOL = 0.5  # px — Figma 子像素精度导致相邻 section 的 bottom/top 有微小浮点偏差

def _page_can_flow(inline_nodes: list, section_y_ranges: list,
                   page_has_flex: bool = True) -> bool:
    """判断页面是否适合将 inline nodes 转为 flow 布局。
    条件：
    1. 页面根有 flex/grid display（纯 absolute 布局页面不做 flow 转换）
    2. 所有 flow items（non-overlay inlines + sections）之间无实质性 Y 重叠（容差 0.5px）。
    """
    if not page_has_flex:
        return False
    flow_items = list(section_y_ranges)
    for n in inline_nodes:
        n_y = n.get('y', float('inf'))
        n_h = (n.get('ir') or {}).get('bb', {}).get('height', 0)
        if not _is_section_overlay(n_y, n_h, section_y_ranges):
            if n_y != float('inf') and n_h > 0:
                flow_items.append((n_y, n_y + n_h))
    flow_items.sort()
    for i in range(len(flow_items) - 1):
        # 容差 _FLOW_OVERLAP_TOL：Figma 子像素精度造成相邻 section 存在 < 0.5px 的浮点偏差，
        # 不应被误判为真实重叠（如 desktop1526 各 section 严格相邻但有 0.0001px 误差）。
        if flow_items[i][1] > flow_items[i + 1][0] + _FLOW_OVERLAP_TOL:
            return False
    return True


def _read_root_css_block(node_id_safe: str, page_name: str, css_ext: str,
                          root_class: str | None = None) -> str | None:
    """从原始单文件 CSS 读取根类的完整 CSS 块（含花括号内容）。"""
    for d in _find_page_code_dirs(node_id_safe):
        for ext in ('scss', 'less', 'css'):
            # 按 page_name 或目录名找 CSS 文件（兼容新旧格式目录名）
            d_name = d.name
            if d_name.endswith(f'-{node_id_safe}'):
                dir_name = d_name[: -(len(node_id_safe) + 1)]
            elif d_name.startswith(f'{node_id_safe}-'):
                dir_name = d_name[len(node_id_safe) + 1:]
            else:
                dir_name = d_name
            named = [d / f'{n}.module.{ext}' for n in (page_name, dir_name)]
            # Fallback：--apply-renames 只改目录名，CSS 文件名可能仍是旧的 Figma 名；
            # 扫描目录内所有 *.module.{ext}（排除 split-a/ 子目录）作为候补。
            fallback = [p for p in sorted(d.glob(f'*.module.{ext}')) if p not in named]
            for path in named + fallback:
                if path.exists():
                    content = path.read_text()
                    if root_class:
                        m = re.search(rf'\.{re.escape(root_class)}\s*\{{([^}}]*)\}}',
                                      content, re.DOTALL)
                        if m:
                            block = f'.{root_class} {{\n{m.group(1)}}}\n'
                            # Also include @media blocks that target the root class
                            # (e.g. mobile min-height for merged roots with h5RootHeight)
                            media_pat = (
                                rf'(@media[^{{{{]*\{{[^{{{{]*\.{re.escape(root_class)}'
                                rf'\s*\{{[^}}]*\}}[^}}]*\}})'
                            )
                            for mm in re.finditer(media_pat, content, re.DOTALL):
                                block += '\n' + mm.group(1) + '\n'
                            return block
                    m = re.search(r'^\.([\w-]+)\s*\{([^}]*)\}', content,
                                  re.MULTILINE | re.DOTALL)
                    if m:
                        # Use root_class as the output class name if provided,
                        # so the page module scss matches the TSX styles[] key.
                        cls_out = root_class if root_class else m.group(1)
                        return f'.{cls_out} {{\n{m.group(2)}}}\n'
    return None


# TEXT-specific CSS properties that should never appear on a page root container.
# Presence of any of these in the root CSS block indicates a class naming collision
# where a TEXT node's class name matches the page root container class name.
_PAGE_ROOT_TEXT_INDICATORS = ('-webkit-text-fill-color', 'background-clip: text',)

# CSS properties that belong exclusively to TEXT nodes; stripped when falling back to IR CSS.
_TEXT_ONLY_PROPS = frozenset({
    'color', '-webkit-text-fill-color', 'background-clip', '-webkit-background-clip',
    'font-size', 'font-weight', 'font-family', 'font-style', 'font-variant',
    'line-height', 'letter-spacing', 'text-align', 'text-transform',
    'white-space', 'text-overflow', 'text-decoration', 'text-indent',
    'background-image',
})


def _sanitize_page_root_css(root_css: str | None, root_class: str, ir_css: dict) -> str:
    """Detect CSS class naming collision on the page root container.

    When a TEXT node's figmaName generates the same CSS class as the page root
    container (e.g. figmaName "Demo Trading" → class 'demo-trading'), convert.py
    may write TEXT-specific properties (font-size, white-space:nowrap,
    -webkit-text-fill-color) into the root CSS block. These properties inherit
    page-wide, breaking all child text rendering.

    Detection: presence of '-webkit-text-fill-color' or 'background-clip: text'
    in root_css is unambiguous proof it belongs to a TEXT node, not a container.

    Fix: generate root CSS from IR dict, stripping TEXT-only properties.
    """
    if not root_css or not any(ind in root_css for ind in _PAGE_ROOT_TEXT_INDICATORS):
        return root_css or f'.{root_class} {{\n  position: relative;\n}}\n'
    # CSS block is TEXT node CSS masquerading as root container CSS.
    # Fall back to IR-derived layout CSS.
    layout_css = {k: v for k, v in ir_css.items() if k not in _TEXT_ONLY_PROPS}
    if not layout_css:
        return f'.{root_class} {{\n  position: relative;\n}}\n'
    props = '\n'.join(f'  {k}: {v};' for k, v in layout_css.items())
    return f'.{root_class} {{\n{props}\n}}\n'


def _apply_fallback_semantics(ir: dict) -> dict:
    """为 IR 树的每个节点注入 semantic（复现 convert.py 的 apply_fallback_semantics）。
    返回新 dict，不修改原始 IR。
    """
    def _fallback(node: dict) -> dict:
        name = _re.sub(r'[^a-zA-Z0-9-_]', '', node.get('figmaName', '').replace(' ', '-')).lower()
        return {
            'htmlTag': 'span' if node.get('isTextNode') else 'img' if node.get('isImageNode') else 'div',
            'componentName': None,
            'className': name or f'node-{(node.get("figmaId") or "").replace(":", "-")}',
            'isExtractedComponent': False,
            'props': [],
        }

    def _walk(node: dict) -> dict:
        return {**node, 'semantic': _fallback(node),
                'children': [_walk(c) for c in (node.get('children') or [])]}

    resolved = _walk(ir)
    normalize_class_names_in_place(resolved)
    return resolved


def _sanitize_classnames(ir_node: dict) -> None:
    """原地清洗 IR 树中所有 semantic.className，移除 CSS selector 非法字符。"""
    sem = ir_node.get('semantic')
    if sem and sem.get('className'):
        name = sem['className']
        name = re.sub(r'[^a-zA-Z0-9_-]', '-', name)
        name = re.sub(r'-{2,}', '-', name).strip('-')
        if name and name[0].isdigit():
            name = 'n-' + name
        sem['className'] = name or 'cls'
    for child in (ir_node.get('children') or []):
        _sanitize_classnames(child)


def _infer_supplement_semantics(ir_node: dict) -> None:
    """U-444: For supplement IR nodes without 'semantic' data, infer minimal
    htmlTag + className so generate_tsx can render them instead of producing
    empty divs with styles[''].

    Root cause: supplementNode entries come from merge_responsive.py which copies
    raw Figma IR without semantic enrichment. Without htmlTag/className, generate_tsx
    produces <div className={styles['']}> with no children rendered.
    """
    if not ir_node.get('semantic'):
        figma_name = ir_node.get('figmaName') or 'node'
        html_tag = 'span' if ir_node.get('isTextNode') else 'div'
        cls = re.sub(r'[^a-zA-Z0-9_-]', '-', figma_name).lower()
        cls = re.sub(r'-{2,}', '-', cls).strip('-')
        if cls and cls[0].isdigit():
            cls = 'n' + cls
        ir_node['semantic'] = {
            'htmlTag': html_tag,
            'className': cls or 'node',
            'componentName': None,
            'props': [],
            'isExtractedComponent': False,
        }
    for child in (ir_node.get('children') or []):
        _infer_supplement_semantics(child)


def _strip_prop_markers(ir_node: dict) -> None:
    """移除 IR 树中所有 _prop_name/_prop_type 标记。
    mark_varying_nodes 在 leaf IR 上原地打标记，但 node_index 共享引用导致
    section/page 的 render_jsx_body 也会遇到这些标记，生成未定义变量引用（如 {buttonText}）。
    在 leaf TSX 生成后立即清除，防止污染 section/page 渲染上下文。
    """
    ir_node.pop('_prop_name', None)
    ir_node.pop('_prop_type', None)
    ir_node.pop('_prop_kind', None)
    ir_node.pop('_css_style_prop', None)
    for child in (ir_node.get('children') or []):
        _strip_prop_markers(child)


def _build_node_index(split_plan: dict) -> dict:
    """
    全局归一化：保证 className 文档顺序与原始单文件一致。
    优先从 plan['irPath'] 读取原始 IR（document order 完全一致）；
    无 irPath 时退化为 synthetic root（子树顺序可能与原始不同）。
    返回 figmaId → normalized_node 映射。
    """
    ir_path_str = split_plan.get('irPath', '')
    full_ir = None
    if ir_path_str:
        p = Path(ir_path_str)
        if p.exists():
            full_ir = json.loads(p.read_text())

    if full_ir is None:
        # 退化：synthetic root（顺序与原始 IR 可能不同）
        subtrees = []
        for s in split_plan.get('sections', []):
            subtrees.append(s['ir'])
            for lc in s.get('leafComponents', []):
                subtrees.append(lc['ir'])
        for n in split_plan.get('inlineNodes', []):
            subtrees.append(n['ir'])
        for lc in split_plan.get('leafComponents', []):
            subtrees.append(lc['ir'])
        full_ir = {
            'figmaId': '__split_root__', 'figmaName': '__split_root__',
            'figmaType': 'FRAME', 'css': {}, 'children': subtrees,
        }

    normalized = _apply_fallback_semantics(full_ir)

    def _index(node: dict, idx: dict) -> dict:
        fid = node.get('figmaId', '')
        if fid and not fid.startswith('__'):
            idx[fid] = node
        for c in node.get('children', []):
            _index(c, idx)
        return idx

    return _index(normalized, {})


def _get_normalized(raw_ir: dict, node_index: dict) -> dict:
    """从全局索引取已归一化的 IR 子树；未命中时退化为 per-subtree 归一化。"""
    fid = raw_ir.get('figmaId', '')
    if fid and fid in node_index:
        result = node_index[fid]
        _sanitize_classnames(result)
        return result
    result = _apply_fallback_semantics(raw_ir)
    _sanitize_classnames(result)
    return result


def _parse_flat_css_dict(text: str) -> dict:
    """解析 CSS/Less/SCSS 平铺文本，返回 {className: {prop: val}}。"""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', '', text)
    result = {}
    for m in re.finditer(r'\.([\w-]+)\s*\{([^{}]*)\}', text, re.DOTALL):
        cls, body = m.group(1), m.group(2)
        props = {}
        for pm in re.finditer(r'([\w-]+)\s*:\s*([^;]+);', body):
            props[pm.group(1).strip()] = pm.group(2).strip()
        if props and cls not in result:
            result[cls] = props
    return result


def _load_orig_css_dict(node_id: str, page_name: str) -> dict:
    """
    从 .figma-to-code/3-page-code/{Name}-{nodeId}/ 读取原始 CSS 文件。
    原始文件包含 convert.py 各 patch 应用后的正确 CSS 值，
    split-a CSS 以此为来源（而非从 IR 重新生成），保证值一致。
    返回 {className: {prop: val}}。
    """
    for d in _find_page_code_dirs(node_id):
        # 1. 先按 page_name 精确匹配
        for ext in ('scss', 'less', 'css'):
            p = d / f'{page_name}.module.{ext}'
            if p.exists():
                return _parse_flat_css_dict(p.read_text())
        # 2. 再按目录名推导 page_name
        d_name = d.name
        dir_page_name = (d_name[:-(len(node_id)+1)] if d_name.endswith(f'-{node_id}')
                         else d_name[len(node_id)+1:])
        for ext in ('scss', 'less', 'css'):
            p = d / f'{dir_page_name}.module.{ext}'
            if p.exists():
                return _parse_flat_css_dict(p.read_text())
        # 3. Fallback: 取该目录下任意 .module.{ext} 文件
        for ext in ('scss', 'less', 'css'):
            css_files = [f for f in d.glob(f'*.module.{ext}') if f.is_file()]
            if css_files:
                return _parse_flat_css_dict(css_files[0].read_text())
    return {}


def _load_figma_maps_json(node_id: str, page_name: str) -> tuple:
    """
    从 figma-maps.json sidecar 读取 className 和 assetPath 映射。
    convert.py 在 generate_tsx 时写入此文件（含 patch+normalize+asset_resolve 结果）。
    返回 (fid_to_class: dict, fid_to_src: dict)。文件不存在时返回 ({}, {})。
    """
    for d in _find_page_code_dirs(node_id):
        maps_file = d / 'figma-maps.json'
        if maps_file.exists():
            data = json.loads(maps_file.read_text())
            return data.get('fidToClass', {}), data.get('fidToSrc', {})
    return {}, {}


def _load_orig_figma_class_map(node_id: str, page_name: str) -> dict:
    """
    从原始单文件 TSX 提取 {figmaId: className} 映射。
    确保 split-a 使用与原始完全相同的 CSS class 名，
    解决 convert.py 的 CSS patch 改变 css 值从而影响 normalize_class_names 去重的问题。
    """
    for d in _find_page_code_dirs(node_id):
        # 搜索该目录下的 .tsx 文件（排除 index.ts）
        tsx_files = [f for f in d.glob('*.tsx') if f.is_file()]
        if not tsx_files:
            continue
        content = tsx_files[0].read_text()
        result = {}
        # 提取 className={styles['cls']} ... data-figma-id="id" 模式
        for m in re.finditer(
            r"className=\{styles\['([\w-]+)'\]\}[^<>\n]*?data-figma-id=\"([^\"]+)\"",
            content,
        ):
            cls, fid = m.group(1), m.group(2)
            if fid not in result:
                result[fid] = cls
        for m in re.finditer(
            r"data-figma-id=\"([^\"]+)\"[^<>\n]*?className=\{styles\['([\w-]+)'\]\}",
            content,
        ):
            fid, cls = m.group(1), m.group(2)
            if fid not in result:
                result[fid] = cls
        if result:
            return result
    return {}


def _lookup_ir_display(ir_node: dict, target_fid: str) -> str:
    """Return the CSS display value for target_fid in the IR subtree.

    Used to emit the correct display value (flex|block|inline) in h5Only
    mobile show rules, instead of always using 'block' which would override
    the element's natural display:flex CSS and break flex layouts.
    Returns 'block' as fallback when not found or display not set.
    """
    if ir_node.get('figmaId') == target_fid:
        return (ir_node.get('css') or {}).get('display', 'block') or 'block'
    for child in (ir_node.get('children') or []):
        found = _lookup_ir_display(child, target_fid)
        if found != 'block_not_found':
            return found
    return 'block_not_found'


def _h5only_show_display(ir_node: dict, fid: str) -> str:
    """Return the display value to use in a h5Only mobile show rule for the given fid.

    Uses the node's actual CSS display value so flex containers keep display:flex
    (not display:block) when shown on mobile, preserving their flex layout.
    """
    if not fid:
        return 'block'
    result = _lookup_ir_display(ir_node, fid)
    if result == 'block_not_found':
        return 'block'
    # Only override block → flex/inline-flex; other values (grid etc.) also use
    # the actual value, but fallback to 'block' for unknown types.
    return result if result in ('flex', 'inline-flex', 'grid', 'inline') else 'block'


def _collect_inline_responsive_flags(ir_node: dict, fid_cls_map: dict, skip_fids: set) -> dict:
    """Scan section IR for pcOnly/h5Only inline nodes, return {css_class → 'h5Only'|'pcOnly'}.

    Skips figmaIds in skip_fids (subSections handled separately via _subsection_responsive).
    Used to generate @media responsive display CSS for section-level children that carry
    pcOnly/h5Only flags from merge_responsive._merge_children but have no wrapper div.

    SAFETY: only returns classes that are UNIQUE within the section. If a CSS class is shared
    by multiple nodes (e.g. two instances of 'hero-text-col'), generating display:none for
    that class would hide ALL nodes with that class — including ones that should stay visible.
    """
    # First pass: collect all class names in the section (including shared non-flagged nodes)
    def _all_classes(node: dict, skip: set, seen: set) -> None:
        fid = node.get('figmaId', '')
        if fid in skip:
            return
        cls = fid_cls_map.get(fid, '')
        if cls:
            seen.add(cls)
        for c in (node.get('children') or []):
            _all_classes(c, skip, seen)

    # Second pass: collect pcOnly/h5Only candidates (only unique-class nodes are safe)
    def _flagged(node: dict, skip: set, candidates: dict) -> None:
        fid = node.get('figmaId', '')
        if fid in skip:
            return
        if node.get('pcOnly') or node.get('h5Only'):
            cls = fid_cls_map.get(fid, '')
            flag = 'h5Only' if node.get('h5Only') else 'pcOnly'
            if cls:
                # Store first occurrence; conflict (same class, different flags) → skip
                if cls not in candidates:
                    candidates[cls] = flag
                elif candidates[cls] != flag:
                    candidates[cls] = None  # conflict → skip
            elif fid:
                # No class in fid_cls_map (e.g. H5-only node absent from merged
                # figma-maps.json). Use attr:figmaId key directly so the result
                # collection emits a [data-figma-id="..."] selector without going
                # through the class-lookup path.
                # Real case: EarnPointsOnCardPaySection 250:3901 (h5Only Iphone)
                # — not in PC figma-maps → no display:none on PC → layout broken.
                attr_key = f'attr:{fid}'
                if attr_key not in candidates:
                    candidates[attr_key] = flag
        for c in (node.get('children') or []):
            _flagged(c, skip, candidates)

    all_cls: set = set()
    _all_classes(ir_node, skip_fids, all_cls)

    candidates: dict = {}
    _flagged(ir_node, skip_fids, candidates)

    # Only return candidates whose class appears exactly once (safe to hide/show)
    # Count class occurrences
    def _count_cls(node: dict, skip: set, counts: dict) -> None:
        fid = node.get('figmaId', '')
        if fid in skip:
            return
        cls = fid_cls_map.get(fid, '')
        if cls:
            counts[cls] = counts.get(cls, 0) + 1
        for c in (node.get('children') or []):
            _count_cls(c, skip, counts)

    counts: dict = {}
    _count_cls(ir_node, skip_fids, counts)

    def _find_all_fids_for_class(node: dict, target_cls: str, skip: set) -> list:
        """Return [(figmaId, flag), ...] for all pcOnly/h5Only nodes with target class."""
        fid = node.get('figmaId', '')
        matches = []
        if fid in skip:
            return matches
        if fid_cls_map.get(fid, '') == target_cls and (node.get('pcOnly') or node.get('h5Only')):
            matches.append((fid, 'h5Only' if node.get('h5Only') else 'pcOnly'))
        for c in (node.get('children') or []):
            matches.extend(_find_all_fids_for_class(c, target_cls, skip))
        return matches

    # For unique classes: use class selector. For shared classes: fall back to
    # [data-figma-id="..."] attribute selector to avoid hiding unrelated instances.
    result = {}
    for cls, flag in candidates.items():
        if cls.startswith('attr:'):
            # Pre-formed attr selector from nodes with no class in fid_cls_map.
            # These already have the correct 'attr:{figmaId}' key; pass them through.
            if flag is not None:
                result[cls] = flag
            continue
        if flag is None:
            # Conflict: same class shared by BOTH a pcOnly and h5Only node (platform pair,
            # e.g. PC bg and H5 bg use the same CSS class). Generate individual attr selectors
            # for each node so both get correct responsive CSS.
            # Real case: OriginalCopySection task-hover-bg-556 (250:2665 pcOnly, 250:3881 h5Only).
            for fid_c, flag_c in _find_all_fids_for_class(ir_node, cls, skip_fids):
                result[f'attr:{fid_c}'] = flag_c
            continue
        if counts.get(cls, 0) == 1:
            # Use attr:[figmaId] selector instead of class selector.
            # CSS module-scoped class rules in the section CSS don't apply to leaf
            # component elements (they have different hashed class names from their
            # own CSS module). [data-figma-id="..."] attribute selectors are global
            # and work across module boundaries.
            fid_for_cls = _find_all_fids_for_class(ir_node, cls, skip_fids)
            if fid_for_cls:
                result[f'attr:{fid_for_cls[0][0]}'] = flag
            else:
                result[cls] = flag  # fallback: no figmaId found, use class selector
        else:
            # shared class — always emit per-node attr:[figmaId] selectors.
            # CSS module scoping prevents class selectors in section CSS from applying
            # to leaf components (their class names have different module hashes).
            # [data-figma-id="..."] attribute selectors work globally across all modules.
            fid_for_cls = _find_all_fids_for_class(ir_node, cls, skip_fids)
            for fid_c, flag_c in fid_for_cls:
                result[f'attr:{fid_c}'] = flag_c
    return result


def _is_dark_hex_bg(hex_color: str) -> bool:
    """Return True if hex color has perceived luminance < 0.2 (dark background)."""
    h = hex_color.strip().lstrip('#')
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    if len(h) != 6:
        return False
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.2
    except Exception:
        return False


def _collect_responsive_overrides_from_ir(
        ir_node: dict, fid_cls_map: dict, skip_fids: set,
        allow_semantic_fallback: bool = False) -> list:
    """Collect @media responsive override blocks from merged IR responsive[] arrays.
    Returns list of (css_class, breakpoint, css_dict) tuples.

    The merge_responsive.py phase stores CSS diffs in node.responsive[]. These overrides
    are not applied by _css_from_orig (which uses orig_css_map). This helper gathers them
    so the section CSS can include the correct responsive @media blocks.

    Real case: hero-content-wrapper (250:2623) has responsive=[{breakpoint:768,
    css:{margin-left:0px, margin-top:0px}}] from css_diff. Without collecting these,
    the PC canvas margins (120px left, 203px top) remain on mobile, causing layout issues.

    allow_semantic_fallback: when True, uses ir_node.semantic.className as a fallback
    if fid_cls_map has no mapping for the node's figmaId. Only set to True for
    page-level inline nodes (butterflies etc.) — NOT for section children, to avoid
    unexpected responsive CSS in single-draft or merged pages.
    """
    result = []
    fid = ir_node.get('figmaId', '')
    if fid in skip_fids:
        return result
    # U-460: semantic fallback is opt-in (allow_semantic_fallback=True) so it only
    # applies to page-level inline nodes, never to section children or single-draft paths.
    cls = fid_cls_map.get(fid, '')
    if not cls and allow_semantic_fallback:
        cls = (ir_node.get('semantic') or {}).get('className', '')
    if cls:
        _base_css = ir_node.get('css') or {}
        _base_pos = _base_css.get('position', '')
        _base_height = _base_css.get('height') or ''
        _children = ir_node.get('children') or []
        # U-446: strip fixed height from abs-pos nodes whose IMMEDIATE children include
        # pcOnly/h5Only variants AND whose base CSS has no explicit height.
        # When base CSS has no height (container auto-sizes on PC) but H5 responsive
        # override adds a fixed height, that height creates blank space when pcOnly
        # children are hidden. Nodes with an explicit base height (e.g. 837px) getting
        # a SMALLER H5 height (566px) are legitimate downscales — do NOT strip those.
        _has_platform_variant_children = any(
            c.get('pcOnly') or c.get('h5Only') for c in _children
        )
        for r in (ir_node.get('responsive') or []):
            css = dict(r.get('css') or {})
            bp = r.get('breakpoint', 768)
            if css:
                _h5_height = css.get('height', '') or ''
                # U-446 extended: strip height from H5 responsive when pcOnly/h5Only
                # children are present and the H5 height would create blank space.
                # Covers three patterns (all require pcOnly/h5Only immediate children):
                #   A) base already absolute, no base height → H5 adds height
                #   B) base is relative, H5 switches to absolute + adds height (no base h)
                #   C) base has explicit height, but H5 height EXCEEDS it (upscale, not
                #      a legitimate downscale — creates extra blank space on H5)
                # Pattern D (keep): base has height, H5 height < base height → downscale, keep.
                _h5_makes_absolute = (css.get('position', '') == 'absolute'
                                      and _base_pos != 'absolute')
                _base_h_px = (float(_base_height.replace('px', ''))
                              if _base_height.endswith('px') else None)
                _h5_h_px = (float(_h5_height.replace('px', ''))
                            if _h5_height.endswith('px') else None)
                # U-446 case C ratio guard: only strip when H5 significantly exceeds PC
                # base (≥1.35x). Below this ratio, the H5 section is likely larger
                # because the H5 CONTENT is larger, not because pcOnly content is hidden.
                # e.g. vip-content-row: 682/494=1.38 → strip ✓
                #      ExamplePage: 654/546=1.20 → keep ✓
                _CASE_C_RATIO = 1.35
                _h5_exceeds_base = (_h5_h_px is not None and _base_h_px is not None
                                    and _h5_h_px > _base_h_px
                                    and _h5_h_px >= _base_h_px * _CASE_C_RATIO)
                # U-448: when overflow:hidden is set AND base has an explicit height,
                # do NOT strip the H5 height. Stripping causes the base height to be
                # inherited, and overflow:hidden at that inherited value clips H5-specific
                # flow content that requires the larger H5 height.
                # (When base has NO explicit height, stripping is safe: node auto-sizes.)
                _h5_has_overflow_hidden = css.get('overflow') == 'hidden'
                _base_has_explicit_height = _base_height.endswith('px') if _base_height else False
                _overflow_clips_if_stripped = _h5_has_overflow_hidden and _base_has_explicit_height
                if (_has_platform_variant_children
                        and _h5_height.endswith('px')
                        and (_base_pos == 'absolute' or _h5_makes_absolute)
                        and (not _base_height or _h5_exceeds_base)
                        and not _overflow_clips_if_stripped):
                    css.pop('height', None)
                if css:
                    result.append((cls, bp, css))
        # U-453: When responsive CSS changes flex-direction to 'column', direct
        # children with fixed px widths > mobile viewport and no responsive CSS
        # of their own would overflow on H5. Emit width:100% for such children.
        # Real case: ExampleSection benefitIconLabel row→column on H5
        # causes header-text-cta (width:540px) to overflow 390px viewport.
        _resp_col_bp = next(
            (r.get('breakpoint', 768) for r in (ir_node.get('responsive') or [])
             if r.get('css', {}).get('flex-direction') == 'column'),
            None
        )
        if _resp_col_bp is not None:
            _H5_VP_W = 390
            for _u449_child in _children:
                _u449_fid = _u449_child.get('figmaId', '')
                if _u449_fid in skip_fids:
                    continue
                _u449_cls = fid_cls_map.get(_u449_fid, '')
                if not _u449_cls:
                    continue
                _u449_css = _u449_child.get('css') or {}
                _u449_w = _u449_css.get('width', '')
                _u449_pos = _u449_css.get('position', '')
                if (
                    _u449_pos in ('relative', '')
                    and _u449_w.endswith('px')
                    and float(_u449_w.replace('px', '')) > _H5_VP_W
                    and not (_u449_child.get('responsive') or [])
                ):
                    result.append((_u449_cls, _resp_col_bp, {'width': '100%'}))
                    # U-456: after widening a direct child to 100%, scan its descendants
                    # for fixed-px widths that would still overflow the H5 viewport.
                    # Real case: header-text-cta (540px) gets width:100%, but its
                    # grandchild a-new-rhythm-for-real-world-finance (534px) still overflows.
                    def _emit_overflow_descendants(node, _bp, _vp, _res):
                        for _desc in (node.get('children') or []):
                            _dfid = _desc.get('figmaId', '')
                            if _dfid in skip_fids:
                                continue
                            _dcls = fid_cls_map.get(_dfid, '')
                            _dcss = _desc.get('css') or {}
                            _dw = _dcss.get('width', '')
                            if (_dcls
                                    and _dw.endswith('px')
                                    and float(_dw.replace('px', '')) > _vp
                                    and not (_desc.get('responsive') or [])):
                                _res.append((_dcls, _bp, {'max-width': '100%'}))
                            _emit_overflow_descendants(_desc, _bp, _vp, _res)
                    _emit_overflow_descendants(_u449_child, _resp_col_bp, _H5_VP_W, result)
        # U-465: structuralSplit direct children are PC-base nodes matched against an H5
        # supplement that differs too much to share (merge_responsive._merge_node_pair sets
        # structuralSplit=True when structural_similarity < threshold). On H5, these PC nodes
        # must be hidden so the H5 supplement (rendered separately via data-figma-id rules or
        # as a sibling in TSX) can show without overlap.
        # Real case: ExampleSection 6690 (header-text-cta, structuralSplit=True)
        # overlaps with H5 text 19856 on mobile — issue #22.
        for _u465_child in _children:
            if not _u465_child.get('structuralSplit'):
                continue
            _u465_fid = _u465_child.get('figmaId', '')
            if _u465_fid in skip_fids:
                continue
            _u465_cls = fid_cls_map.get(_u465_fid, '')
            if not _u465_cls:
                continue
            result.append((_u465_cls, 768, {'display': 'none'}))
    for child in (ir_node.get('children') or []):
        result.extend(_collect_responsive_overrides_from_ir(
            child, fid_cls_map, skip_fids, allow_semantic_fallback=allow_semantic_fallback))
    return result


def _strip_section_root_responsive_props(css_props: dict, base_position: str = '') -> dict:
    """Strip canvas absolute-positioning from a section root's H5 responsive override.

    When merge_responsive.py creates responsive[] overrides, the H5 supplement's root
    node may carry position:absolute + top/left/height from Figma's canvas coordinates.
    In normal-flow (can_flow) pages these must be stripped so the section stays in the
    document flow on mobile — same logic as rule 6 in _css_from_orig (is_section_root).

    Only modifies the dict when position == 'absolute'; all other cases pass through
    unchanged so margin/padding responsive adjustments are preserved (U-437, etc.).

    Extended (U-457): when base CSS has position:absolute but H5 responsive doesn't
    set position, still inject position:relative and strip offset properties.
    """
    resp_has_absolute = css_props.get('position') == 'absolute'
    base_is_absolute = base_position == 'absolute'
    if not resp_has_absolute and not base_is_absolute:
        return css_props
    result = dict(css_props)
    if resp_has_absolute:
        # Original behavior: strip top/left/right/bottom, height, fixed px width
        for _p in ('position', 'left', 'right', 'bottom', 'top'):
            result.pop(_p, None)
        result.pop('height', None)
        # Strip fixed px width; base CSS already has width:100% for section roots
        if result.get('width', '').endswith('px'):
            result.pop('width', None)
    else:
        # U-457: base is absolute, H5 responsive doesn't set position — strip offsets only
        for _p in ('position', 'left', 'right', 'bottom', 'top'):
            result.pop(_p, None)
    result['position'] = 'relative'
    return result


def _h5only_wrapper_height_rule(ss_ir_css: dict) -> str:
    """Return extra CSS properties for an h5-only subsection wrapper when the subsection
    root has position:absolute.

    Absolutely-positioned children don't contribute to their parent's height, so an
    h5-only wrapper div with only `display:block; width:100%` collapses to height:0
    when its child's root has position:absolute. Fix: make the wrapper
    position:relative with the same height as the child's fixed px height.

    Returns '' when the subsection root is not absolutely positioned or has no
    fixed-px height (e.g. auto-sized containers), so the caller emits no extra rules.

    Real case: TomorrowlandLandingPage4 HeroSection has
      position:absolute; left:0; top:0; width:393px; height:630px
    causing h5-only-hero-section wrapper to collapse to height:0 on mobile.
    """
    if ss_ir_css.get('position') != 'absolute':
        return ''
    h = str(ss_ir_css.get('height', ''))
    if not h.endswith('px'):
        return ''
    overflow = ss_ir_css.get('overflow', '')
    overflow_rule = f'\n    overflow: {overflow};' if overflow else ''
    return f'\n    position: relative;\n    height: {h};{overflow_rule}'


def _has_h5only_descendants(ir_node: dict) -> bool:
    """Return True if any descendant (recursive) has h5Only=True."""
    for c in (ir_node.get('children') or []):
        if c.get('h5Only') or _has_h5only_descendants(c):
            return True
    return False


def _find_same_name_pc_for_h5only_subsections(subsections: list) -> set:
    """Return set of subSection names where a PC version has an h5Only namesake.

    When an h5Only subSection shares a name with a non-h5Only/non-pcOnly subSection,
    the non-h5Only one should be pcOnly (they're platform-specific versions of the same
    component type). This handles cases where _merge_children's h5Only >= pairs rule
    doesn't fire because other pairs outnumber h5Only siblings.

    EXCEPTION: If the PC subSection already contains h5Only descendants (H5 content
    merged inside it), do NOT mark it pcOnly — that would hide the H5 content too.
    Real case: PC FaqSection (I250:3796;14668:69867) has h5Only FAQ expandable items
    inside it. Marking the whole section pcOnly hides those items on mobile.
    """
    h5_only_names = {ss['name'] for ss in subsections if (ss.get('ir') or {}).get('h5Only')}
    return {
        ss['name'] for ss in subsections
        if (ss['name'] in h5_only_names
            and not (ss.get('ir') or {}).get('h5Only')
            and not (ss.get('ir') or {}).get('pcOnly')
            and not _has_h5only_descendants(ss.get('ir') or {}))
    }


def _disambiguate_h5only_subsection_names(subsections: list) -> dict:
    """Return {figmaId → disambiguatedName} for h5Only subSections whose name collides
    with a same-name PC subSection.

    When a h5Only subsection (e.g. H5 FAQ header, I250:4920;14668:58717) shares its name
    with a non-h5Only subsection (e.g. PC FaqSection, I250:3796;14668:69867), both get
    mapped to the same component file. The h5-wrapper in the parent TSX renders the full
    PC component instead of only the H5 header, causing duplicate rendering on mobile.

    Fix: rename the h5Only subsection to '{name}H5' so it gets a SEPARATE component file.
    The parent TSX will then render <FaqSectionH5> (just the H5 header) inside the
    h5-wrapper, and <FaqSection> separately — eliminating the duplication.

    Returns {figmaId: 'FaqSectionH5'} for each h5Only subsection with a same-name PC sibling.
    """
    pc_names = {ss['name'] for ss in subsections if not (ss.get('ir') or {}).get('h5Only')}
    result = {}
    for ss in subsections:
        ss_ir = ss.get('ir') or {}
        if ss_ir.get('h5Only') and ss['name'] in pc_names:
            fid = ss_ir.get('figmaId', '')
            if fid:
                result[fid] = f'{ss["name"]}H5'
    return result


def _apply_orig_classnames(ir: dict, fid_to_class: dict) -> None:
    """将 fid_to_class 中的 className 覆写到 IR 树每个节点的 semantic.className。"""
    fid = ir.get('figmaId', '')
    if fid and fid in fid_to_class:
        sem = ir.get('semantic')
        if sem:
            sem['className'] = fid_to_class[fid]
        else:
            ir['semantic'] = {'className': fid_to_class[fid]}
    for c in (ir.get('children') or []):
        _apply_orig_classnames(c, fid_to_class)


def _apply_expanded_sizes(ir_normalized: dict, ir_plan: dict) -> None:
    """
    将 plan IR（已经过 _expand_ir_max_css 处理）的 SIZE 属性覆写到 normalized IR 对应节点。
    normalized IR 来自全页 IR 文件（未扩展），plan IR 来自 plan.json（已扩展）。
    使用 zip-walk 按结构位置匹配（与 _expand_ir_max_css 相同策略）。
    """
    _SIZE_PROPS_SET = ('width', 'height', 'min-width', 'max-width', 'min-height')
    norm_css = ir_normalized.get('css')
    plan_css = ir_plan.get('css') or {}
    if norm_css and plan_css:
        for prop in _SIZE_PROPS_SET:
            if prop in plan_css:
                norm_css[prop] = plan_css[prop]
    norm_children = ir_normalized.get('children') or []
    plan_children = ir_plan.get('children') or []
    for nc, pc in zip(norm_children, plan_children):
        _apply_expanded_sizes(nc, pc)


def _load_orig_figma_asset_map(node_id: str, page_name: str) -> dict:
    """
    从原始单文件 TSX 提取 {figmaId: localAssetPath} 映射。
    convert.py 在内存中设置 localAssetPath 但不写回 IR JSON；
    split-a 通过读原始 TSX 的 src 属性来恢复这些路径。
    """
    for d in _find_page_code_dirs(node_id):
        tsx_files = [f for f in d.glob('*.tsx') if f.is_file()]
        if not tsx_files:
            continue
        content = tsx_files[0].read_text()
        result = {}
        # 匹配 <img ... src="..." ... data-figma-id="..." ... />
        for m in re.finditer(r'<img[^>]*/>', content, re.DOTALL):
            tag = m.group(0)
            src = re.search(r'src="([^"]+)"', tag)
            fid = re.search(r'data-figma-id="([^"]+)"', tag)
            if src and fid and src.group(1):
                fid_val = fid.group(1)
                if fid_val not in result:
                    result[fid_val] = src.group(1)
        if result:
            return result
    return {}


def _apply_orig_asset_paths(ir: dict, fid_to_src: dict) -> None:
    """将 fid_to_src 中的路径注入到 IR 树每个节点的 localAssetPath。"""
    fid = ir.get('figmaId', '')
    if fid and fid in fid_to_src:
        ir['localAssetPath'] = fid_to_src[fid]
    for c in (ir.get('children') or []):
        _apply_orig_asset_paths(c, fid_to_src)


def _resolve_missing_asset_paths(node_index: dict, asset_comp: str) -> None:
    """U-438: Assign deterministic paths for vector/image nodes with no localAssetPath.

    figma-maps.json can become stale when merge_responsive.py adds H5 supplement
    nodes to the merged IR after convert.py last ran. Any such node keeps
    localAssetPath=None, causing tsx_generator to emit an empty <div> instead of
    an <img>. This function fills the gap using the same deterministic naming
    rule as convert.py::resolve_asset_paths.
    """
    for fid, node in node_index.items():
        if not node.get('localAssetPath'):
            safe = re.sub(r'[:/;]', '-', fid)
            if node.get('isVectorNode'):
                node['localAssetPath'] = f'/assets/{asset_comp}/{safe}.svg'
            elif node.get('isImageNode'):
                node['localAssetPath'] = f'/assets/{asset_comp}/{safe}.png'


_LEAF_SIZE_PROPS = frozenset({'width', 'height', 'min-width', 'max-width', 'min-height'})


_VISUAL_ONLY_PROPS = frozenset({
    # 仅影响视觉外观（背景/边框/阴影/透明度），不影响 flex 布局、尺寸、间距。
    # 用于判断 variant wrapper（如斑马纹偶数行）的 CSS 差异是否可以通过 rowClassName 传递，
    # 而无需嵌套 wrapper div（会造成双重 flex+padding 问题）。
    'background-color', 'background', 'border-radius', 'box-shadow',
    'border', 'border-top', 'border-bottom', 'border-left', 'border-right',
    'outline', 'opacity', 'color',
})

_MOLY_WRAPPER_POSITIONING_PROPS = frozenset({
    'position', 'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'align-self', 'flex-shrink', 'flex-grow', 'flex', 'order',
    'grid-area', 'grid-column', 'grid-row',
    'top', 'left', 'right', 'bottom', 'z-index',
    'display', 'flex-direction', 'justify-content', 'align-items',
    'width', 'height', 'min-width', 'max-width', 'min-height', 'max-height',
    'gap', 'transform', 'overflow', 'pointer-events',
})

# 不套 wrapper、通过 className 直传样式的 CC 组件
# 叶子组件根 class 应 strip 的外部定位属性（由外层 Section wrapper div 提供）
# width/height/padding/border-radius 等是组件自身视觉/布局属性，不能 strip
_LEAF_ROOT_STRIP_PROPS = frozenset({
    'position', 'top', 'left', 'right', 'bottom',
    'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'flex-shrink', 'align-self',
    # translate-based transform is a positioning prop (used with left/top centering pattern);
    # the wrapper div already carries the full positioning — leaf must not repeat it.
    'transform',
})

_MOLY_CLASSNAME_COMPONENTS = frozenset({'Button', 'Tag'})

# 需要生成字号覆写的 CC 组件（内部有文本结构，外部 class 无法穿透）
_MOLY_TEXT_OVERRIDE_COMPONENTS = frozenset({'Collapse', 'Tabs'})

# TEXT_OVERRIDE 组件（Tabs/Collapse）wrapper CSS 保留规则：
# - 定位属性：外层提供位置，CC 组件不干预
# - 容器视觉属性（background-color / border-radius / padding）：
#   CC 组件本身不提供这些装饰，空 CSS 文件（'/* 使用组件库内部样式 */'）
#   无法携带，必须保留在 wrapper class 上，否则样式永久丢失
# - 容器布局属性（display / flex-direction / align-items / gap / height / overflow）：
#   这些是 wrapper 容器的外观属性，不是组件库内部布局
_MOLY_TEXT_OVERRIDE_POSITIONING_PROPS = frozenset({
    'position', 'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'align-self', 'flex-shrink', 'flex-grow', 'flex', 'order',
    'grid-area', 'grid-column', 'grid-row',
    'top', 'left', 'right', 'bottom', 'z-index',
    'width', 'max-width', 'min-width',
    # 容器尺寸
    'height', 'min-height', 'max-height',
    # 容器布局
    'display', 'flex-direction', 'justify-content', 'align-items', 'gap',
    # 容器视觉装饰（CC 组件不提供，wrapper 必须保留）
    'overflow', 'background-color', 'border-radius',
    'padding', 'padding-top', 'padding-bottom', 'padding-left', 'padding-right',
    'border', 'border-top', 'border-bottom', 'border-left', 'border-right',
    'filter', 'box-shadow',
})

# Button/Tag CC 确认时：保留定位+尺寸+视觉覆盖属性
# CC variant 的默认样式经常与 Figma 设计不一致，需要 CSS 覆盖
_MOLY_BUTTON_CC_CONFIRMED_PROPS = frozenset({
    'position', 'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'align-self', 'flex-shrink', 'flex-grow', 'flex', 'order',
    'grid-area', 'grid-column', 'grid-row',
    'top', 'left', 'right', 'bottom', 'z-index',
    'width', 'height', 'min-width', 'max-width', 'min-height', 'max-height',
    'border-radius', 'padding', 'background-color', 'background', 'border', 'color',
    'overflow', 'display', 'flex-direction', 'justify-content', 'align-items', 'gap',
})

# Button/Tag 无 CC 确认（boundary_detector 推断的）：保留全部视觉属性作为 fallback
_MOLY_BUTTON_CLASSNAME_PROPS = frozenset({
    'position', 'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'align-self', 'flex-shrink', 'flex-grow', 'flex', 'order',
    'grid-area', 'grid-column', 'grid-row',
    'top', 'left', 'right', 'bottom', 'z-index',
    'width', 'height', 'min-width', 'max-width', 'min-height', 'max-height',
    'border-radius', 'padding', 'background-color', 'background', 'border', 'color',
    'text-align', 'display', 'justify-content', 'align-items',
})


def _css_from_orig(ir: dict, orig_css_map: dict, css_ext: str,
                   strip_root_props: set | None = None,
                   moly_wrapper_classes: set | None = None,
                   moly_button_classes: set | None = None,
                   moly_button_cc_confirmed: set | None = None,
                   moly_text_override_classes: set | None = None,
                   root_z_index: int | None = None,
                   is_section_root: bool = False,
                   is_inline_root: bool = False,
                   section_orig_top: float = 0,
                   section_top_adjusted_px: float | None = None,
                   moly_flex_child_classes: set | None = None) -> str:
    """
    为 IR 子树生成 CSS，使用 orig_css_map 中的值（包含 convert.py 的 patch）。
    orig_css_map 没有的 class（新类）退化到 IR css 值。

    覆盖规则（以设计稿为准）：
    1. SIZE 属性（width/height 等）：若 IR 值 > orig 值，优先使用 IR 值。
    2. opacity=0：Figma 的"隐藏层"标记不应注入叶子组件 CSS，否则所有实例变不可见。
    3. strip_root_props：根 class 中需要剔除的属性集合（如 width 冲突时）。
    4. moly_wrapper_classes：Code Connect 组件的 wrapper class 名集合，只保留定位属性。
    5. root_z_index：根 class 有 position:absolute/relative 时，注入 z-index 保持 Figma 层叠顺序。
    6. is_section_root：Section 根 class 自动剥离画布定位（position:absolute + top/left/right/bottom + 固定 height）。
    7. is_inline_root：Inline node 根 class 剥离画布定位（同 Section，但保留 height；bottom overlay 不转换）。

    strip_root_props 用于处理"template 有固定 width 但某些实例无 width"的情况。
    """
    classes = _collect_classes(ir)  # {className: ir_css_dict}
    if not classes:
        return ''
    # 根 class = IR 根节点的 className（DFS 第一个）
    root_cls = (ir.get('semantic') or {}).get('className', '')
    blocks = []
    for cls, ir_props in classes.items():
        orig_props = orig_css_map.get(cls)
        if orig_props:
            final_props = dict(orig_props)
            # 规则 1：SIZE 属性取 max(orig, IR)，保证扩展后的尺寸生效
            for prop in _LEAF_SIZE_PROPS:
                if prop not in ir_props or ir_props[prop] is None:
                    # IR 没有 height（或值为 None） → 该节点是 HUG（高度由内容决定），
                    # 不应继承 orig_css_map 中同名类的 height（防止跨实例污染，如
                    # One Platform 的 FIXED frame-2147224743 h=88px 污染 Earn 的
                    # HUG 实例 h=84px，导致 Earn/SpotX/End section 各多 4-8px）。
                    # 其他 SIZE 属性（width / min-width 等）保持原有继承行为。
                    if prop == 'height':
                        final_props.pop(prop, None)
                    continue
                ir_val = str(ir_props[prop])
                orig_val = str(final_props.get(prop, ''))
                im = re.match(r'([0-9.]+)px', ir_val)
                om = re.match(r'([0-9.]+)px', orig_val)
                if im and om:
                    ir_px = float(im.group(1))
                    orig_px = float(om.group(1))
                    if ir_px > orig_px:
                        # 两者都是 px 值且 IR 值更大才覆盖（不覆盖 100%/auto 等相对值）
                        final_props[prop] = ir_props[prop]
                    elif (prop == 'height' and ir_px < orig_px
                          and ir_props.get('overflow') == 'hidden'):
                        # U-463: IR height < orig AND overflow:hidden → context clip constraint.
                        # Real case: Options icon in WhyInstitutionsChooseUsSection4 (H5):
                        # component natural size 67.8px, context bb 52px with overflow:hidden.
                        # Must use IR 52px to prevent the icon from expanding the card height.
                        final_props[prop] = ir_props[prop]
            # 规则 2：去除 opacity: 0（Figma 隐藏层标记，不应出现在可见组件 CSS）
            opacity = str(final_props.get('opacity', '')).strip()
            if opacity in ('0', '0.0'):
                final_props.pop('opacity', None)
        else:
            final_props = ir_props
        # 规则 3：根 class 剔除冲突属性（如 width 在不同实例间不一致，移除让父容器控制）
        if strip_root_props and cls == root_cls:
            # position:relative 不能 strip（子元素 absolute 定位需要它作为参照）
            _effective_strip = strip_root_props
            if final_props.get('position') == 'relative':
                _effective_strip = strip_root_props - {'position'}
            had_position = 'position' in final_props and final_props['position'] not in ('relative', 'static', '')
            final_props = {k: v for k, v in final_props.items() if k not in _effective_strip}
            # 剥离 position:absolute 后，若根有 overflow:hidden，
            # 需补 position:relative 确保裁剪和定位参照正确
            if had_position and 'position' in _effective_strip and 'position' not in final_props:
                if final_props.get('overflow') == 'hidden':
                    final_props['position'] = 'relative'
        # 规则 6：Section 根 class 剥离 Figma 画布定位（拆分后由 Page 布局控制）
        if is_section_root and cls == root_cls:
            if final_props.get('position') == 'absolute':
                _orig_top = final_props.get('top')
                for _p in ('position', 'left', 'right', 'bottom'):
                    final_props.pop(_p, None)
                # top → margin-top，但只保留真实间距（剔除上方内容已占用的高度）
                # section_top_adjusted_px 为 None 时退化到原始 top 值
                if section_top_adjusted_px is not None:
                    _effective_top = f'{section_top_adjusted_px:.4g}px' if section_top_adjusted_px > 0 else '0px'
                else:
                    _effective_top = _orig_top or '0px'
                final_props.pop('top', None)
                if _effective_top and _effective_top != '0px':
                    final_props['margin-top'] = _effective_top
                # 固定 height 在 absolute 布局中是画布尺寸，移除让内容撑开
                final_props.pop('height', None)
                # 补 position:relative 保证内部 absolute 子节点有参照
                final_props['position'] = 'relative'
        # 规则 7：Inline node 根 class 剥离画布定位（同 Section 逻辑，但保留 height）
        if is_inline_root and cls == root_cls:
            if final_props.get('position') == 'absolute':
                # bottom overlay（如固定底部按钮栏）转为 sticky，避免覆盖 flow 内容（U-461）
                _has_bottom = 'bottom' in final_props
                _top_val = final_props.get('top')
                if _has_bottom and (_top_val is None or _top_val == '0px'):
                    final_props['position'] = 'sticky'  # U-461: preserves height in flow, sticks to viewport bottom
                    final_props.pop('top', None)
                else:
                    _orig_top = final_props.get('top', '0px')
                    _orig_left = final_props.get('left', '0px')
                    for _p in ('position', 'left', 'right', 'bottom', 'top'):
                        final_props.pop(_p, None)
                    # top → margin-top（减去 section_orig_top 偏移）
                    _tm = re.match(r'([0-9.]+)px', str(_orig_top))
                    _adjusted = max(0, float(_tm.group(1)) - section_orig_top) if _tm else 0
                    if _adjusted > 0:
                        final_props['margin-top'] = f'{_adjusted:.4g}px'
                    # left → margin-left（非零时保留水平偏移）
                    if _orig_left and _orig_left != '0px':
                        final_props['margin-left'] = _orig_left
                    final_props['position'] = 'relative'
                    # 从 absolute 转为 relative 后仍需 z-index 维持层叠顺序
                    if root_z_index is not None and 'z-index' not in final_props:
                        final_props['z-index'] = str(root_z_index)
        # 规则 4：CC wrapper class 只保留定位属性（视觉由 CC 组件自身提供）
        if moly_text_override_classes and cls in moly_text_override_classes:
            # TEXT_OVERRIDE 组件用更窄的白名单（不含 width/height/display 等内部布局属性）
            final_props = {k: v for k, v in final_props.items()
                          if k in _MOLY_TEXT_OVERRIDE_POSITIONING_PROPS}
        elif moly_wrapper_classes and cls in moly_wrapper_classes:
            final_props = {k: v for k, v in final_props.items()
                          if k in _MOLY_WRAPPER_POSITIONING_PROPS}
        # 规则 4b：Button/Tag className CSS 处理
        # CC 确认的：strip 视觉属性，CC variant 控制样式
        if moly_button_cc_confirmed and cls in moly_button_cc_confirmed:
            final_props = {k: v for k, v in final_props.items()
                          if k in _MOLY_BUTTON_CC_CONFIRMED_PROPS}
        # 非 CC 但 boundary_detector 推断的：保留视觉属性作为 fallback
        elif moly_button_classes and cls in moly_button_classes:
            final_props = {k: v for k, v in final_props.items()
                          if k in _MOLY_BUTTON_CLASSNAME_PROPS}
        # 规则 5：positioned 根 class 注入 z-index 保持 Figma 层叠顺序。
        # absolute 和 relative 都需要：CSS stacking 中 absolute+z-index:N 会盖住
        # relative+z-index:auto（即使 relative 元素在 DOM 中更靠后），导致背景矩形
        # 遮挡 position:relative 的 PC section 内容。
        if (root_z_index is not None and cls == root_cls
                and final_props.get('position') in ('absolute', 'relative')
                and 'z-index' not in final_props):
            final_props['z-index'] = str(root_z_index)
        # 规则 8：fixed-px width + padding 的元素注入 box-sizing:border-box（Figma 尺寸含 padding）。
        # Figma 始终以 border-box 报告节点宽度，若元素同时有固定 px 宽度和内边距，
        # 不加 box-sizing:border-box 会导致实际渲染宽度超出设计值。
        _w = str(final_props.get('width', ''))
        _has_padding = any(k.startswith('padding') for k in final_props)
        if (re.match(r'[0-9.]+px$', _w) and _has_padding
                and 'box-sizing' not in final_props):
            final_props['box-sizing'] = 'border-box'
        blocks.append(_render_rule(cls, final_props))
        # 规则 U-296：Code Connect flex wrapper 补充子选择器，使 CC 组件撑满父容器。
        # 仅对「真正的 Code Connect 组件 wrapper」注入（moly_flex_child_classes），
        # 不对 varying-instance 唯一 class（如 'eth' 卡片容器）注入——否则空的
        # 间隔子节点因 flex-basis:0 塌陷，导致整张卡片高度崩溃（U-323 bug）。
        if (moly_flex_child_classes and cls in moly_flex_child_classes
                and final_props.get('display', '').startswith('flex')):
            blocks.append(f'.{cls} > * {{\n  flex: 1;\n  min-width: 0;\n}}')
    return '\n\n'.join(blocks) + '\n'


def _generate_cc_text_overrides(overrides: list, page_theme: str = 'light') -> str:
    """生成 Code Connect 组件（Collapse/Tabs）的字号覆写 CSS 块。

    overrides 是 list of (cls, component_name, title_font_size, title_line_height,
                          content_font_size, content_line_height)。
    page_theme: 'dark' | 'light'，用于决定 Collapse button 的颜色 fallback 值。
    注意：CSS 选择器（如 .moly-collapse）来自具体组件库，需根据实际组件库调整。
    """
    _color_fallback = '#ffffff' if page_theme == 'dark' else '#1e1e1e'
    blocks = []
    for entry in overrides:
        cls, comp_name, t_fs, t_lh, c_fs, c_lh = entry[:6]
        item_gap = entry[6] if len(entry) > 6 else None
        content_gap = entry[7] if len(entry) > 7 else None
        # 使用父级 CSS Module class 嵌套 :global(.moly-collapse) 来锚定选择器，
        # 避免依赖 className 传入的 class（可能被 emotion 覆盖）
        lines = [f'.{cls} {{']
        if comp_name == 'Collapse':
            # 根容器间距
            if item_gap:
                lines.append(f'  :global(.moly-collapse) {{')
                lines.append(f'    gap: {item_gap};')
                lines.append(f'  }}')
            # item trigger 间距
            _h3_pad = content_gap or '8px'
            lines.append(f'  :global(.moly-collapse h3) {{')
            lines.append(f'    padding: {_h3_pad} 0;')
            lines.append(f'  }}')
            if t_fs:
                # button 全宽 + space-between 让 icon 右对齐；color 保证 dark 背景可见
                lines.append(f'  :global(.moly-collapse h3 button) {{')
                lines.append(f'    font-size: {t_fs};')
                if t_lh:
                    lines.append(f'    line-height: {t_lh};')
                lines.append(f'    width: 100%;')
                lines.append(f'    justify-content: space-between;')
                lines.append(f'    padding: 0;')
                lines.append(f'    color: {_color_fallback};')
                lines.append(f'  }}')
                lines.append(f'  :global(.moly-collapse h3 button span) {{')
                lines.append(f'    font-size: {t_fs};')
                if t_lh:
                    lines.append(f'    line-height: {t_lh};')
                lines.append(f'    white-space: normal;')
                lines.append(f'    text-align: left;')
                lines.append(f'  }}')
                # icon 固定大小不缩小
                lines.append(f'  :global(.moly-collapse h3 button svg) {{')
                lines.append(f'    flex-shrink: 0;')
                lines.append(f'    margin-left: auto;')
                lines.append(f'  }}')
            if c_fs:
                lines.append(f'  :global(.moly-collapse [role="region"] > div) {{')
                lines.append(f'    font-size: {c_fs};')
                if c_lh:
                    lines.append(f'    line-height: {c_lh};')
                lines.append(f'    padding: {content_gap or "4px"} 0 {content_gap or "8px"};')
                lines.append(f'  }}')
        elif comp_name == 'Tabs':
            if t_fs:
                lines.append(f'  :global(button[role="tab"]) {{')
                lines.append(f'    font-size: {t_fs};')
                if t_lh:
                    lines.append(f'    line-height: {t_lh};')
                lines.append(f'  }}')
        lines.append('}')
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks) + '\n' if blocks else ''


def _extract_moly_text_sizes(ir_node: dict) -> tuple:
    """从 CC 组件 IR 节点提取标题/内容的 font-size、line-height 和间距。

    Returns: (title_fs, title_lh, content_fs, content_lh, item_gap, content_gap)
    """
    title_fs = title_lh = content_fs = content_lh = None
    item_gap = content_gap = None
    children = ir_node.get('children') or []
    if not children:
        return (None, None, None, None, None, None)

    # 根节点 gap = items 间距
    root_css = ir_node.get('css') or {}
    item_gap = root_css.get('gap')

    first_child = children[0]
    # 第一个子节点内部的 'content' FRAME 的 gap = title/answer 间距
    fc_css = first_child.get('css') or {}
    content_gap = None
    for gc in (first_child.get('children') or []):
        gc_css = gc.get('css') or {}
        if gc_css.get('gap'):
            content_gap = gc_css['gap']
            break
    if not content_gap:
        content_gap = fc_css.get('gap')

    texts_found = []

    def _find_texts(node):
        if node.get('isTextNode'):
            css = node.get('css') or {}
            texts_found.append((css.get('font-size'), css.get('line-height')))
        for c in (node.get('children') or []):
            _find_texts(c)

    _find_texts(first_child)
    if texts_found:
        title_fs, title_lh = texts_found[0]
    if len(texts_found) > 1:
        content_fs, content_lh = texts_found[1]
    return (title_fs, title_lh, content_fs, content_lh, item_gap, content_gap)


_POSITION_PROPS = {'position', 'left', 'top', 'right', 'bottom'}


def _detect_width_conflict(lc: dict, node_index: dict,
                           fid_to_class: dict, orig_css_map: dict) -> set:
    """
    检测 leaf 组件根 class 在不同实例间是否存在布局冲突。
    返回需要从根 class 剔除的属性集合。

    检测两类冲突：
    1. width 冲突：template 有固定 width 但某实例无 → strip width
    2. position 冲突：template 有 position:absolute 但某实例无 position
       → strip position + left/top/right/bottom（由各 wrapper 控制定位）
    """
    template_fid = (lc.get('ir') or {}).get('figmaId', '')
    tmpl_cls = fid_to_class.get(template_fid, '')
    if not tmpl_cls:
        return set()

    tmpl_props = orig_css_map.get(tmpl_cls, {})
    strip = set()

    # 1. width 冲突检测
    if tmpl_props.get('width'):
        for fid in lc.get('allInstanceFigmaIds', []):
            if fid == template_fid:
                continue
            inst_cls = fid_to_class.get(fid, '')
            if inst_cls and 'width' not in orig_css_map.get(inst_cls, {}):
                strip.add('width')
                break

    # 2. position 冲突检测：template 有 position:absolute 但某实例无 position，
    #    或所有实例都有 position:absolute 但 left/top/right/bottom 值不同（实例特定定位）
    if tmpl_props.get('position') == 'absolute':
        for fid in lc.get('allInstanceFigmaIds', []):
            if fid == template_fid:
                continue
            inst_cls = fid_to_class.get(fid, '')
            inst_props = orig_css_map.get(inst_cls, {}) if inst_cls else {}
            if 'position' not in inst_props or inst_props.get('position') != 'absolute':
                for p in _POSITION_PROPS:
                    if p in tmpl_props:
                        strip.add(p)
                break
            # 值冲突：left/top/right/bottom 值不同 → 定位是实例特定的
            # U-452c: 忽略 < 1px 的亚像素差异（Figma 浮点坐标误差，如 top:0px vs top:-0.25px）。
            for coord in ('left', 'top', 'right', 'bottom'):
                if coord not in tmpl_props:
                    continue
                tmpl_val = tmpl_props[coord]
                inst_val = inst_props.get(coord)
                if inst_val == tmpl_val:
                    continue
                # Check for sub-pixel difference (< 1px) — treat as same position.
                _is_subpixel = False
                if tmpl_val and inst_val:
                    try:
                        _diff = abs(float(str(tmpl_val).replace('px', '').strip()) -
                                    float(str(inst_val).replace('px', '').strip()))
                        _is_subpixel = _diff < 1.0
                    except ValueError:
                        pass
                if not _is_subpixel:
                    for p in _POSITION_PROPS:
                        if p in tmpl_props:
                            strip.add(p)
                    break
            if strip & _POSITION_PROPS:
                break

    # 3. border 冲突检测：template 有 border 但某实例的 border 值不同
    if tmpl_props.get('border'):
        for fid in lc.get('allInstanceFigmaIds', []):
            if fid == template_fid:
                continue
            inst_cls = fid_to_class.get(fid, '')
            inst_props = orig_css_map.get(inst_cls, {}) if inst_cls else {}
            if inst_props.get('border') and inst_props['border'] != tmpl_props['border']:
                strip.add('border')
                break

    return strip


def _collect_fids_in_subtree(ir: dict) -> set:
    """返回 IR 子树中所有节点的 figmaId 集合（含根节点）。"""
    fids: set = set()
    stack = [ir]
    while stack:
        node = stack.pop()
        fid = node.get('figmaId')
        if fid:
            fids.add(fid)
        stack.extend(node.get('children') or [])
    return fids


def _find_all_leaf_instances(section_ir: dict, all_candidates: dict) -> dict:
    """
    单次 DFS 遍历 section_ir，返回 {figmaId: leaf_idx} 映射。
    all_candidates: {figmaId: leaf_component_index}（所有叶子的候选实例合并）。
    遇到任意候选节点后不再递归其子节点——这确保父 leaf 被找到后，
    其子树内的另一个 leaf 不会被误计为独立实例（如 MarketingButton6 在 Frame2147223934 内部）。
    """
    found: dict = {}

    def _walk(node):
        fid = node.get('figmaId', '')
        if fid and fid in all_candidates:
            found[fid] = all_candidates[fid]
            return  # 不递归已匹配节点的子树
        for c in (node.get('children') or []):
            _walk(c)

    _walk(section_ir)
    return found


def _maybe_convert_centered_abs_left(ir_node: dict, design_width_px: float = 375.0) -> None:
    """For an H5 supplement root: if position=absolute and left represents horizontal
    centering in the 375px design frame, convert to calc() for responsive behavior.
    Also sets z-index:10 when none is present so the element renders above PC section
    stacking contexts (e.g. AdvantagesSection z-index:5).
    Mutates ir_node['css'] in place.
    """
    css = ir_node.get('css') or {}
    if css.get('position') != 'absolute':
        return
    left_raw = css.get('left', '')
    width_raw = css.get('width', '')
    if not (isinstance(left_raw, str) and left_raw.endswith('px')):
        return
    if not (isinstance(width_raw, str) and width_raw.endswith('px')):
        return
    try:
        left_val = float(left_raw[:-2])
        width_val = float(width_raw[:-2])
    except ValueError:
        return
    if left_val <= 0:
        return
    if abs(left_val - (design_width_px - width_val) / 2) < 1.0:
        css['left'] = f'calc((100% - {width_raw}) / 2)'
        if not css.get('z-index'):
            css['z-index'] = '10'


def _maybe_adjust_h5_top_for_nav_offset(ir_node: dict, h5_nav_height: float) -> None:
    """When H5 supplement is absolutely positioned with top ≈ h5_nav_height + small offset,
    subtract h5_nav_height from top so the element aligns to the content area in the web
    (where H5 navigation is display:none and content starts at y=0).

    Applies when: position=absolute, h5_nav_height>0, and top is within
    [0.8 * h5_nav_height, 1.5 * h5_nav_height] (element positioned just past nav bar).
    Mutates ir_node['css'] in place.

    Real case: DemoTrading 1484:27154 top:91px, nav height:88px → top:3px.
    """
    if h5_nav_height <= 0:
        return
    css = ir_node.get('css') or {}
    if css.get('position') != 'absolute':
        return
    top_raw = css.get('top', '')
    if not (isinstance(top_raw, str) and top_raw.endswith('px')):
        return
    try:
        top_val = float(top_raw[:-2])
    except ValueError:
        return
    if not (0.8 * h5_nav_height <= top_val <= 1.5 * h5_nav_height):
        return
    new_top = max(0.0, top_val - h5_nav_height)
    if new_top == int(new_top):
        css['top'] = f'{int(new_top)}px'
    else:
        css['top'] = f'{new_top:.1f}px'


def _generate_structural_split_section(
    section_name: str,
    ir: dict,
    out_dir: Path,
    css_ext: str = 'less',
    node_index: 'dict | None' = None,
    fid_to_class: 'dict | None' = None,
    h5_nav_height: float = 0.0,
) -> None:
    """
    For a structuralSplit node, generate three files:
      {section_name}PC.tsx   — PC version (from ir)
      {section_name}H5.tsx   — H5 version (from ir['supplementNode'])
      index.tsx              — isMobile switch component

    h5_nav_height: height in px of the H5 navigation bar supplement. When >0,
    any H5 supplement with position:absolute and top ≈ h5_nav_height will have
    its top adjusted by subtracting h5_nav_height (content-area offset correction).

    node_index / fid_to_class: when provided, PC IR is fully normalized via
    _get_normalized + _apply_orig_classnames so deep nodes get correct CSS class
    names (matching the original single-file product). Without these, only
    split-boundary nodes have semantic and ~95% of CSS is silently lost.
    H5 supplement nodes are NOT in node_index (supplementNode is not a children
    entry), so H5 IR keeps the shallow approach to avoid className collisions.
    """
    import copy as _copy
    raw_pc = {k: v for k, v in ir.items() if k not in ('structuralSplit', 'supplementNode')}
    if node_index is not None:
        pc_ir = _get_normalized(raw_pc, node_index)
        if fid_to_class:
            _apply_orig_classnames(pc_ir, fid_to_class)
    else:
        pc_ir = _copy.deepcopy(raw_pc)
    h5_ir = _copy.deepcopy(ir['supplementNode'])
    # U-444: infer htmlTag + className for supplement nodes without semantic data
    # so generate_tsx can render H5 content instead of producing empty divs.
    _infer_supplement_semantics(h5_ir)

    _sanitize_classnames(pc_ir)
    _sanitize_classnames(h5_ir)

    pc_name = f'{section_name}PC'
    h5_name = f'{section_name}H5'

    # Set componentName in semantic so generate_tsx uses the correct name
    pc_sem = {**(pc_ir.get('semantic') or {}), 'componentName': pc_name}
    h5_sem = {**(h5_ir.get('semantic') or {}), 'componentName': h5_name}

    # structuralSplit files always land at components/{SectionName}/ (depth-2 from page root),
    # so hooks/ (sibling of components/) requires two levels up.
    _hooks_rel = '../../hooks/usePageEnv'
    try:
        pc_tsx = generate_tsx({**pc_ir, 'semantic': pc_sem}, css_ext=css_ext,
                              inject_page_env=False, hooks_rel_path=_hooks_rel)
    except Exception:
        pc_tsx = (
            f"import {{ memo }} from 'react';\n"
            f"import styles from './{pc_name}.module.{css_ext}';\n\n"
            f"export const {pc_name} = memo(function {pc_name}() {{\n"
            f"  return <div className={{styles['{section_name.lower()}']}}></div>;\n"
            f"}});\n\nexport default {pc_name};\n"
        )
    try:
        h5_tsx = generate_tsx({**h5_ir, 'semantic': h5_sem}, css_ext=css_ext,
                              inject_page_env=False, hooks_rel_path=_hooks_rel)
    except Exception:
        h5_tsx = (
            f"import {{ memo }} from 'react';\n"
            f"import styles from './{h5_name}.module.{css_ext}';\n\n"
            f"export const {h5_name} = memo(function {h5_name}() {{\n"
            f"  return <div className={{styles['{section_name.lower()}']}}></div>;\n"
            f"}});\n\nexport default {h5_name};\n"
        )

    (out_dir / f'{pc_name}.tsx').write_text(pc_tsx)
    (out_dir / f'{h5_name}.tsx').write_text(h5_tsx)

    # Generate CSS module files so the import statements in the TSX files resolve.
    try:
        pc_css = generate_scss(pc_ir) or ''
    except Exception:
        pc_root_cls = (pc_ir.get('semantic') or {}).get('className', section_name.lower())
        pc_css = f'.{pc_root_cls} {{\n}}\n'
    (out_dir / f'{pc_name}.module.{css_ext}').write_text(pc_css)

    _maybe_convert_centered_abs_left(h5_ir)
    _maybe_adjust_h5_top_for_nav_offset(h5_ir, h5_nav_height)
    # U-462: Convert position:absolute bottom:0 to sticky in H5 root (bottom overlay prevention).
    # generate_scss doesn't apply split_codegen rule-6/7, so mutate h5_ir root CSS here.
    # Real case: DemoTrading StartDemoTradingSectionH5 button bar overlapping FAQ content.
    _h5_root_css = h5_ir.get('css') or {}
    if (_h5_root_css.get('position') == 'absolute'
            and 'bottom' in _h5_root_css
            and _h5_root_css.get('top') in (None, '0px', 0)):
        h5_ir = {**h5_ir, 'css': {**_h5_root_css, 'position': 'sticky'}}
    try:
        h5_css = generate_scss(h5_ir) or ''
    except Exception:
        h5_root_cls = (h5_ir.get('semantic') or {}).get('className', section_name.lower())
        h5_css = f'.{h5_root_cls} {{\n}}\n'
    (out_dir / f'{h5_name}.module.{css_ext}').write_text(h5_css)

    # Check for supplementText nodes in the base (PC) IR and emit a TODO comment
    _supp_nodes = _find_supplement_text_nodes(pc_ir)
    if _supp_nodes:
        _cls_list = ', '.join(_supp_nodes)
        _supp_comment = (
            f"// ⚠️  supplementText nodes detected in this section.\n"
            f"// These nodes have H5-specific text variants stored in their IR.\n"
            f"// To implement: replace hardcoded text with:\n"
            f"//   const isMobile = useMobileSize()\n"
            f"//   const label = isMobile ? \"<H5 text>\" : \"<PC text>\"\n"
            f"// Affected node classes: {_cls_list}\n"
        )
    else:
        _supp_comment = None

    index_content = (
        f"import {{ memo, useState, useEffect }} from 'react';\n"
        f"import {pc_name} from './{pc_name}';\n"
        f"import {h5_name} from './{h5_name}';\n"
        f"\n"
        f"function useIsMobile() {{\n"
        f"  const [m, setM] = useState(() => typeof window !== 'undefined' && window.innerWidth < 768);\n"
        f"  useEffect(() => {{\n"
        f"    const mq = window.matchMedia('(max-width: 767px)');\n"
        f"    setM(mq.matches);\n"
        f"    const h = (e: MediaQueryListEvent) => setM(e.matches);\n"
        f"    mq.addEventListener('change', h);\n"
        f"    return () => mq.removeEventListener('change', h);\n"
        f"  }}, []);\n"
        f"  return m;\n"
        f"}}\n"
        f"\n"
        + (f"{_supp_comment}\n" if _supp_comment else "")
        + f"export const {section_name} = memo(function {section_name}() {{\n"
        f"  const isMobile = useIsMobile();\n"
        f"  return isMobile ? <{h5_name} /> : <{pc_name} />;\n"
        f"}});\n"
        f"\n"
        f"export default {section_name};\n"
    )
    (out_dir / INDEX_TSX_FILENAME).write_text(index_content)
    # Empty CSS module required by validate_split Layer 0 (index.tsx imports no styles,
    # but Layer 0 checks for its existence as a structural completeness signal).
    (out_dir / f'index.module.{css_ext}').write_text('')


def _find_supplement_text_nodes(ir: dict) -> list[str]:
    """Recursively collect semantic.className (or figmaName) from nodes where supplementText is set."""
    results: list[str] = []

    def _walk(node: dict) -> None:
        if node.get('supplementText'):
            sem = node.get('semantic') or {}
            label = sem.get('className') or node.get('figmaName', '')
            if label:
                results.append(label)
        for child in (node.get('children') or []):
            _walk(child)

    _walk(ir)
    return results


def _render_supplement_text_node(node: dict, css_ext: str = 'less') -> str:
    """
    Generate conditional text JSX for a node with supplementText.
    Returns a JSX snippet string (not a full component wrapper).
    isMobile ? h5Text : pcText pattern for use within a component that
    already has useMobileSize in scope.
    """
    pc_text = node.get('textContent', '')
    h5_text = node.get('supplementText', pc_text)
    cls = (node.get('semantic') or {}).get('className', 'text')
    return (
        f"  const isMobile = useMobileSize();\n"
        f"  const _text_{cls} = isMobile ? {json.dumps(h5_text)} : {json.dumps(pc_text)};\n"
        f"  // render: <span className={{styles['{cls}']}}{{_text_{cls}}}</span>\n"
    )


def _kendall_tau_distance(order_a: list, order_b: list) -> float:
    """Normalised Kendall τ distance between two orderings. 0=identical, 1=fully reversed."""
    common = [x for x in order_a if x in set(order_b)]
    if len(common) <= 1:
        return 0.0
    b_pos = {name: i for i, name in enumerate(order_b)}
    inversions = 0
    total = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            total += 1
            if b_pos.get(common[i], 0) > b_pos.get(common[j], 0):
                inversions += 1
    return inversions / total if total else 0.0


def _generate_page_tsx_with_order(
    page_name: str,
    body_items: list,
    root_class: str,
    css_ext: str,
    h5_order: list | None = None,
    **kwargs,
) -> str:
    """
    Thin wrapper around _generate_page_tsx that passes h5_order through.
    All dual-branch logic (tau check, h5_only/pc_only filtering, i18n wiring)
    lives inside _generate_page_tsx so kwargs like has_i18n and page_theme
    are always respected.
    """
    return _generate_page_tsx(page_name, body_items, root_class, css_ext,
                               h5_order=h5_order, **kwargs)


def _propagate_plan_css_overrides(plan_ir: dict, norm_ir: dict) -> None:
    """递归将 plan IR 的 CSS 变更传播到 normalized IR。
    _get_normalized 以 node_index 替换 plan IR，丢弃了 detect/_expand_ir_max_css
    对 plan IR 做的两类修改：
      1. height=None  → HUG 实例存在，不应继承 orig_css_map 的 height
      2. 更大的 SIZE 值（如 image 子节点被扩展至最大实例尺寸）
    zip-walk plan IR 和 normalized IR，逐节点逐 CSS 属性同步。
    真实案例：EarnUseCase 右列 image height 从 665px 扩展到 811px
    （portrait 图片 4030:41654 vs 左列 landscape 4030:41602）。
    """
    _PLAN_SIZE_PROPS = frozenset({'width', 'height', 'min-width', 'max-width', 'min-height'})

    def _walk(plan_node, norm_node):
        plan_css = plan_node.get('css') or {}
        norm_css = norm_node.get('css')
        is_image = plan_node.get('isImageNode', False)
        if norm_css is not None:
            for prop, val in plan_css.items():
                if val is None:
                    # Propagate explicit None (HUG) → remove from norm
                    if prop in norm_css:
                        norm_css[prop] = None
                elif prop in _PLAN_SIZE_PROPS and prop in norm_css and not is_image:
                    # Propagate larger SIZE values (from _expand_ir_max_css).
                    # SKIP for image nodes: instances with different aspect ratios
                    # must NOT share a single expanded size — it distorts both images.
                    # (Real case: EarnUseCase 4030:41602 landscape vs 4030:41654 portrait)
                    mp = re.match(r'([0-9.]+)px', str(val))
                    mn = re.match(r'([0-9.]+)px', str(norm_css.get(prop, '')))
                    if mp and mn and float(mp.group(1)) > float(mn.group(1)):
                        norm_css[prop] = val
        # Recurse into children by position
        plan_ch = plan_node.get('children') or []
        norm_ch = norm_node.get('children') or []
        for i, pc in enumerate(plan_ch):
            if i < len(norm_ch):
                _walk(pc, norm_ch[i])

    _walk(plan_ir, norm_ir)


def generate_components(split_plan: dict, out_dir: Path, css_ext: str = 'less', no_i18n: bool = False) -> dict:
    """
    生成新目录结构到 out_dir/。
    返回 {'sectionNames': [...], 'topLeafNames': [...]}
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load CC data to distinguish CC-confirmed vs boundary-detector-inferred CC components
    _node_id_safe = split_plan.get('nodeId', '').replace(':', '-')
    _cc_json_path = code_connect_file(_node_id_safe)
    _cc_confirmed_fids: set = set()
    _cc_snippets: dict = {}  # {fid: snippet_str}
    if _cc_json_path.exists():
        try:
            import json as _json
            _cc_data = _json.loads(_cc_json_path.read_text())
            _cc_confirmed_fids = set(_cc_data.keys())
            _cc_snippets = {k: v.get('snippet', '') for k, v in _cc_data.items()}
        except Exception:
            pass
    section_names = []

    # 全局归一化：保证所有子树的 className 与原始单文件一致
    node_index = _build_node_index(split_plan)

    # i18n: collect text tokens from full page (opt-in, default off)
    _i18n_tokens = [] if no_i18n else collect_text_tokens_flat(node_index.values())
    # Post-rename: enhance token IDs for split path only (doesn't affect convert.py)
    _used_enhanced: set = {t['tokenId'] for t in _i18n_tokens}
    for token in _i18n_tokens:
        enhanced = _enhance_token_id(token)
        if enhanced and enhanced not in _used_enhanced:
            _used_enhanced.discard(token['tokenId'])
            token['tokenId'] = enhanced
            _used_enhanced.add(enhanced)
    _texts_map: dict = {t['figmaId']: t['tokenId'] for t in _i18n_tokens if t['figmaId']}
    _page_comp = split_plan['pageComponent']
    if _i18n_tokens:
        _texts_ts_path = out_dir / texts_ts_filename(_page_comp)
        _existing_keys = parse_existing_texts_keys(_texts_ts_path)
        _node_id = split_plan.get('nodeId', '')
        _figma_source = (
            f'https://www.figma.com/design/?node-id={_node_id}'
            if _node_id else ''
        )
        _texts_ts_path.write_text(
            generate_texts_file(_i18n_tokens, _page_comp, existing_keys=_existing_keys)
        )
        (out_dir / texts_defaults_ts_filename(_page_comp)).write_text(
            generate_texts_defaults_file(_i18n_tokens, _page_comp, figma_source=_figma_source)
        )

    # 原始 CSS（包含 convert.py 所有 patch 后的正确值），用于 CSS 分发
    node_id_key = split_plan.get('nodeId', '')
    page_name_for_css = split_plan.get('pageComponent', '')
    orig_css_map = _load_orig_css_dict(node_id_key, page_name_for_css)

    # 页面级主题检测（从完整 IR 根节点背景色判断，传给所有叶子/section TSX 生成）
    _page_theme: str = 'light'
    _ir_path_str = split_plan.get('irPath', '')
    if _ir_path_str and Path(_ir_path_str).exists():
        _full_ir = json.loads(Path(_ir_path_str).read_text())
        _page_theme = detect_page_theme(_full_ir)
    else:
        _full_ir = None

    # h5SectionOrder for dual-branch index.tsx generation (Task 4)
    h5_section_order: list | None = _full_ir.get('h5SectionOrder') if _full_ir else None
    if h5_section_order is None:
        h5_section_order = split_plan.get('h5SectionOrder') or None

    # 移动端检测
    _is_mobile_page = False
    if _full_ir:
        _root_w_str = (_full_ir.get('css') or {}).get('width', '')
        _root_w_val = float(_root_w_str.replace('px', '')) if 'px' in _root_w_str else 0
        if _root_w_val == 0:
            _root_w_val = (_full_ir.get('bb') or {}).get('width', 0)
        _is_mobile_page = 0 < _root_w_val <= 768

    # Figma 层叠顺序：顶层 children 的 figmaId → childIndex（用于 z-index 注入）
    _figma_layer_order: dict = {}
    if _full_ir:
        for _idx, _child in enumerate(_full_ir.get('children', [])):
            _fid = _child.get('figmaId', '')
            if _fid:
                _figma_layer_order[_fid] = _idx

    # 原始 figmaId→className 映射（来自原始 TSX），覆写归一化 className，
    # 解决 convert.py CSS patch 改变去重结果的问题
    fid_to_class = _load_orig_figma_class_map(node_id_key, page_name_for_css)
    # 原始 figmaId→localAssetPath 映射。
    # 优先从 figma-maps.json（convert.py 写入的完整映射）获取，
    # fallback 到从 TSX 正则提取（仅覆盖 <img> 标签，遗漏 background-image 和 decorative 节点）。
    maps_fid_to_class, fid_to_src = _load_figma_maps_json(node_id_key, page_name_for_css)
    # 补充：CC 节点在原始 TSX 中被 snippet 替换，不含 data-figma-id，
    # _load_orig_figma_class_map 无法提取。figma-maps.json 有完整映射。
    if maps_fid_to_class:
        for fid, cls in maps_fid_to_class.items():
            if fid not in fid_to_class:
                fid_to_class[fid] = cls
    if not fid_to_src:
        fid_to_src = _load_orig_figma_asset_map(node_id_key, page_name_for_css)
    # 一次性注入到 node_index 所有节点：node_index 里的节点是共享引用，
    # 注入后所有 _get_normalized 返回的子树自动带有正确的 localAssetPath。
    if fid_to_src:
        for fid, node in node_index.items():
            if fid in fid_to_src:
                node['localAssetPath'] = fid_to_src[fid]
    # U-438: Fill deterministic paths for asset nodes that landed in the merged IR
    # after convert.py last ran (figma-maps.json stale). Must run after fid_to_src
    # injection so existing entries are not overwritten.
    _pcode_dirs = _find_page_code_dirs(node_id_key)
    _asset_comp = _pcode_dirs[0].name if _pcode_dirs else node_id_key
    _resolve_missing_asset_paths(node_index, _asset_comp)

    # 预计算 Section Y 范围 + page_can_flow，决定是否启用 absolute→flow 转换
    _section_y_ranges = []
    _section_orig_top = 0.0
    for _s in split_plan.get('sections', []):
        _s_css = (_s.get('ir') or {}).get('css') or {}
        _s_top_str = str(_s_css.get('top', '0px'))
        _m = re.match(r'([0-9.]+)px', _s_top_str)
        if _m:
            _section_orig_top = max(_section_orig_top, float(_m.group(1)))
        _s_y = _s.get('y', float('inf'))
        _s_h = (_s.get('ir') or {}).get('bb', {}).get('height', 0)
        if _s_y != float('inf') and _s_h > 0:
            _section_y_ranges.append((_s_y, _s_y + _s_h))
    _all_inline_nodes = split_plan.get('inlineNodes', [])
    # 检测页面根是否有 flex/grid display — 纯 absolute 页面不做 flow 转换
    _page_root_css = {}
    _ir_path = split_plan.get('irPath', '')
    if _ir_path:
        _p = Path(_ir_path)
        if _p.exists():
            _full = json.loads(_p.read_text())
            _page_root_css = _full.get('css') or {}
    _page_display = _page_root_css.get('display', '')
    _page_has_flex = 'flex' in _page_display or 'grid' in _page_display
    _can_flow = _page_can_flow(_all_inline_nodes, _section_y_ranges, page_has_flex=_page_has_flex)

    # 非 flow 页面（根无 flex）：从有已知 CSS top 的 Section/Inline 反推 page_root_y，
    # 用于给 position:relative 且无 CSS top 的 Section 补充页面级绝对定位。
    # 场景：AI Hub / BTC Pizza Day / Pre KYC 等 Figma 纯绝对布局落地页。
    _page_root_y: float | None = None
    _page_root_x: float | None = None
    # page_root_x：从全宽 section（bb.width >= 1440）的画布绝对 x 推导页面原点 x。
    # flow 页和非 flow 页均需要，用于计算窄 section 的水平偏移量。
    for _ref_s in split_plan.get('sections', []):
        _ref_bb_w = ((_ref_s.get('ir') or {}).get('bb') or {}).get('width', 0)
        if float(_ref_bb_w) >= 1430 and _ref_s.get('x') is not None:
            _page_root_x = float(_ref_s['x'])
            break
    if not _can_flow:
        for _ref_s in split_plan.get('sections', []):
            _ref_top = str((_ref_s.get('ir') or {}).get('css', {}).get('top', '0'))
            _m = _re.match(r'([1-9][0-9.]*)px', _ref_top)
            if _m and _ref_s.get('y', float('inf')) != float('inf'):
                _page_root_y = _ref_s['y'] - float(_m.group(1))
                break
        if _page_root_y is None:
            for _ref_n in _all_inline_nodes:
                _ref_top = str((_ref_n.get('ir') or {}).get('css', {}).get('top', '0'))
                _m = _re.match(r'([1-9][0-9.]*)px', _ref_top)
                if _m and _ref_n.get('y', float('inf')) != float('inf'):
                    _page_root_y = _ref_n['y'] - float(_m.group(1))
                    break

    # 1. 生成每个 Section
    _sections_needing_theme: set = set()
    _section_abs_tops:   dict = {}  # section_name → abs page-relative y（非 flow 页专用）
    _section_abs_lefts:  dict = {}  # section_name → actual left px (fixed-width sections only)
    _section_abs_widths: dict = {}  # section_name → actual width px (fixed-width sections only)
    components_dir = out_dir / COMPONENTS_SUBDIR
    components_dir.mkdir(exist_ok=True)
    # U-449: detect H5 navigation bar height from the merged IR's top-level children.
    # The navigation section is rendered inline in the page (not in split_plan sections),
    # so we scan _full_ir.get('children') for the first structuralSplit node whose H5
    # supplement is absolute-positioned at top≈0 with nav-bar-range height (40–120px).
    # Used to correct H5 supplement top values that include the hidden nav bar offset.
    _h5_nav_height = 0.0
    _ir_children_src = (
        (_full_ir.get('children') or []) if _full_ir else []
    ) or [s.get('ir') or {} for s in split_plan.get('sections', [])]
    for _child in _ir_children_src:
        if not _child.get('structuralSplit'):
            continue
        _supp = _child.get('supplementNode') or {}
        _supp_css = _supp.get('css') or {}
        _supp_bb = _supp.get('bb') or {}
        if _supp_css.get('position') == 'absolute':
            try:
                _supp_top = float(str(_supp_css.get('top', '0px')).replace('px', ''))
            except ValueError:
                _supp_top = 999.0
            _supp_h = float(_supp_bb.get('height', 0))
            if _supp_top < 5.0 and 40 <= _supp_h <= 120:
                # U-451: skip nav-height detection when U-450 will render this
                # supplement as a flow element (it has CSS or children). When U-450
                # renders the nav as flow, the nav occupies its height in the page,
                # so H5 text overlays at top=(nav_h+offset) are already correct —
                # U-449 must NOT subtract nav_h from their top values.
                _u451_supp_has_content = bool(_supp.get('css') or _supp.get('children'))
                if not _u451_supp_has_content:
                    _h5_nav_height = _supp_h
                break

    # U-454: Pre-compute H5 layout bottom for flow sections positioned after abs sections.
    # On H5, sections whose responsive[] adds position:absolute + top/height are taken out
    # of flow. Remaining position:relative sections appear in the normal flow, which on H5
    # starts below the h5-only wrappers. They need margin-top to appear below the last abs
    # section's bottom edge.
    # _h5_abs_sections_bottom: largest (top+height) across all H5-abs responsive sections
    # _h5_flow_preceding_height: sum of h5-only wrapper heights (they ARE in flow)
    _h5_abs_sections_bottom: float = 0.0
    _h5_flow_preceding_height: float = 0.0
    for _u454_s in split_plan.get('sections', []):
        _u454_ir = _u454_s.get('ir', {})
        _u454_css = _u454_ir.get('css') or {}
        for _u454_r in (_u454_ir.get('responsive') or []):
            if _u454_r.get('breakpoint', 768) == 768:
                _u454_rc = _u454_r.get('css', {})
                _u454_t = _u454_rc.get('top', '')
                _u454_h = _u454_rc.get('height', '')
                if _u454_t.endswith('px') and _u454_h.endswith('px'):
                    try:
                        _u454_bottom = float(_u454_t[:-2]) + float(_u454_h[:-2])
                        _h5_abs_sections_bottom = max(_h5_abs_sections_bottom, _u454_bottom)
                    except ValueError:
                        pass
        # h5Only wrappers are in normal flow; their height = root height when abs with px h
        if _u454_ir.get('h5Only'):
            _u454_wh = _u454_css.get('height', '')
            if _u454_css.get('position') == 'absolute' and _u454_wh.endswith('px'):
                try:
                    _h5_flow_preceding_height += float(_u454_wh[:-2])
                except ValueError:
                    pass

    for s in split_plan.get('sections', []):
        s_name = s['name']
        s_dir = components_dir / s_name
        s_dir.mkdir(exist_ok=True)

        # structuralSplit: generate PC/H5/index trio and skip normal section processing
        if s['ir'].get('structuralSplit'):
            _generate_structural_split_section(
                s_name, s['ir'], s_dir, css_ext=css_ext,
                node_index=node_index, fid_to_class=fid_to_class,
                h5_nav_height=_h5_nav_height,
            )
            _section_abs_tops[s_name] = None
            _section_abs_lefts[s_name] = None
            _section_abs_widths[s_name] = None
            section_names.append(s_name)
            continue

        # 使用全局归一化的 IR（class 名与原始 CSS 对齐）
        s_ir = _get_normalized(s['ir'], node_index)
        if fid_to_class:
            _apply_orig_classnames(s_ir, fid_to_class)

        # 1a. 生成 Section 内部叶子组件（平铺文件）
        leaf_names = []
        for lc in s.get('leafComponents', []):
            lc_name = lc['name']
            lc_ir = _get_normalized(lc['ir'], node_index)
            # Propagate plan IR CSS overrides to the normalized IR.
            # _get_normalized returns the node_index entry, discarding changes that
            # detect/_expand_ir_max_css made to the plan IR:
            #   - height=None  → HUG instances exist; skip orig height
            #   - height=Npx larger than original → _expand_ir_max_css expanded it
            # Walk plan IR and normalized IR in lock-step, applying overrides recursively.
            _propagate_plan_css_overrides(lc.get('ir') or {}, lc_ir)
            if fid_to_class:
                _apply_orig_classnames(lc_ir, fid_to_class)
            mark_varying_nodes(lc_ir, lc.get('varyingProps') or [])
            if lc.get('ccComponent'):
                # Button variant 推断：从实例 CSS 检测 primary vs outline
                _vp = lc.get('varyingProps') or []
                if lc['ccComponent'] == 'Button' and 'variant' not in str(_vp):
                    _variants = []
                    for _fid in (lc.get('allInstanceFigmaIds') or []):
                        _inst_node = node_index.get(_fid)
                        if _inst_node:
                            _bg = (_inst_node.get('css') or {}).get('background-color', '')
                            _bd = (_inst_node.get('css') or {}).get('border', '')
                            if _bg and 'transparent' not in _bg:
                                _variants.append('primary')
                            elif _bd:
                                _variants.append('outline')
                            else:
                                _variants.append('primary')
                        else:
                            _variants.append('primary')
                    if len(set(_variants)) > 1:
                        _vp = list(_vp) + [{'path': [], 'type': 'prop', 'propName': 'variant', 'values': _variants}]
                _snippet = lc.get('snippet')
                # 内联判断：与 _generate_section_tsx 的 _can_inline 条件保持一致。
                # 单实例 + 无 varyingProps（含 Button 推断后）+ 有 snippet
                # → Section 直接内联 CC snippet，叶子文件永远不被 import → 跳过生成文件。
                # leaf_names 仍需追加（_generate_section_tsx 通过 leaf_names[i] 索引）。
                _all_fids = lc.get('allInstanceFigmaIds') or []
                _will_be_inlined = (
                    len(_all_fids) == 1
                    and not _vp
                    and bool(_snippet)
                    and '<' in _snippet  # JSX requires '<'; Dart/Flutter snippets use ClassName() syntax
                )
                if _will_be_inlined:
                    leaf_names.append(lc_name)
                    _strip_prop_markers(lc_ir)
                    continue
                # Mobile pages: adjust component props for small screens
                if _is_mobile_page and _snippet:
                    if lc['ccComponent'] == 'Button' and 'block' not in _snippet:
                        _snippet = _snippet.replace('<Button ', '<Button block ', 1)
                    if lc['ccComponent'] == 'Countdown':
                        _snippet = re.sub(r'size="[^"]*"', 'size="small"', _snippet)
                # U-423: skip non-JSX (Dart/Flutter) snippets — pass None so _write_moly_flat
                # falls back to empty <Component {...props} /> instead of outputting Dart code.
                _jsx_only_snippet = _snippet if (_snippet and '<' in _snippet) else None
                _write_moly_flat(s_dir, lc_name,
                                 lc['ccComponent'],
                                 lc.get('ccImport') or 'your-component-lib',
                                 css_ext,
                                 all_imports=lc.get('allImports'),
                                 snippet=_jsx_only_snippet,
                                 varying_props=_vp,
                                 instance_ir=lc.get('ir'))
            else:
                # 检测此 leaf 是否有 visual-only variant 实例（如斑马纹偶数行）
                # 若有，生成 rowClassName? prop 支持，避免 wrapper div 导致双重 flex+padding。
                _lc_inst_fids = set(lc.get('allInstanceFigmaIds') or [])
                _lc_root_cls = (lc_ir.get('semantic') or {}).get('className', '')
                _lc_has_variant = False
                if _lc_inst_fids and fid_to_class and orig_css_map and _lc_root_cls:
                    _lc_root_css = orig_css_map.get(_lc_root_cls, {})
                    for _lf in _lc_inst_fids:
                        _lf_cls = fid_to_class.get(_lf, '')
                        if _lf_cls and _lf_cls != _lc_root_cls:
                            _lf_css = orig_css_map.get(_lf_cls, {})
                            _lf_diff = {k for k in set(_lf_css) | set(_lc_root_css)
                                        if _lf_css.get(k) != _lc_root_css.get(k)}
                            if _lf_diff and _lf_diff.issubset(_VISUAL_ONLY_PROPS):
                                _lc_has_variant = True
                                break
                (s_dir / f'{lc_name}.tsx').write_text(
                    _generate_leaf_tsx(lc_ir, lc_name, css_ext, page_theme=_page_theme,
                                       texts_map=_texts_map, page_comp=_page_comp,
                                       texts_rel_prefix='../../',
                                       support_row_class_name=_lc_has_variant)
                )
                _strip = _detect_width_conflict(lc, node_index, fid_to_class, orig_css_map)
                # 叶子组件根 class strip 外部容器属性（由外层 wrapper div 提供）
                # 但如果实例 class 与模板共享（无 wrapper），leaf 需自行保留 align-self/flex-shrink
                _inst_fids = set(lc.get('allInstanceFigmaIds') or [])
                _root_cls = (lc_ir.get('semantic') or {}).get('className', '')
                # 判断是否有 wrapper：与 tsx_generator 逻辑对齐
                # inst_cls == root_cls 时不渲染 wrapper；inst_cls 被外部共享时也不渲染
                # U-452: 当 root_cls 是语义名（不在 fid_to_class）而实例 class 等于 leaf
                # 模板 figmaId 的原始 class 时，leaf IS 自身根节点 → _has_wrapper=False。
                _has_wrapper = True
                _lc_tpl_cls = fid_to_class.get(lc_ir.get('figmaId', ''), '') if fid_to_class else ''
                if fid_to_class:
                    for _ifid in _inst_fids:
                        _icls = fid_to_class.get(_ifid, '')
                        if _icls:
                            if _icls == _root_cls or (_lc_tpl_cls and _icls == _lc_tpl_cls):
                                _has_wrapper = False
                                break
                            _other_users = [f for f, c in fid_to_class.items()
                                            if c == _icls and f not in _inst_fids]
                            if _other_users:
                                _has_wrapper = False
                                break
                # No-wrapper case（inst_cls == root_cls）：section 的容器 div 仍会以相同类名包裹 leaf，
                # 容器 div 的 CSS（来自 section module）已携带 position/transform 定位；
                # leaf root 不应重复 transform（否则双重 translateX 导致内容偏移）。
                _base_strip = _LEAF_ROOT_STRIP_PROPS if _has_wrapper else frozenset({
                    'position', 'top', 'left', 'right', 'bottom', 'transform',
                })
                if not (_strip and _strip & _POSITION_PROPS):
                    _all_inst_pos = set()
                    # U-429: Use IR position as the authoritative source when IR says 'absolute'.
                    # Previously this only read orig_css_map, which produces two bugs:
                    # (a) first build: orig empty → _all_inst_pos={''} → position stripped
                    #     → position:relative added (because overflow:hidden), taking up flex space.
                    # (b) subsequent builds: orig stale 'relative' → same wrong strip path.
                    # Fix: when IR has position:absolute, trust it over any stale orig value.
                    # Real case: TaskHoverBg556 (250:2665 pcOnly / 250:3881 h5Only) in EU LP.
                    _ir_pos = (lc_ir.get('css') or {}).get('position', '')
                    for _ifid in lc.get('allInstanceFigmaIds', []):
                        _icls = fid_to_class.get(_ifid, '')
                        _iprops = orig_css_map.get(_icls, {}) if _icls else {}
                        _orig_pos = _iprops.get('position', '')
                        # Trust IR when it says absolute (IR is Figma-authoritative);
                        # use orig otherwise so intentional relative/static is preserved.
                        _pos = _ir_pos if _ir_pos == 'absolute' else (_orig_pos or _ir_pos)
                        _all_inst_pos.add(_pos)
                    if len(_all_inst_pos) == 1 and 'absolute' in _all_inst_pos:
                        _base_strip = _LEAF_ROOT_STRIP_PROPS - _POSITION_PROPS
                _strip = (_strip or set()) | _base_strip
                # U-375/U-405: When inst_cls == root_cls (no-wrapper flag) but position
                # conflict forces a call-site wrapper, strip percentage dimensions only —
                # otherwise the wrapper and the leaf both carry the same %-value, and CSS
                # Modules resolves the percentage against the wrapper's parent → double-nesting.
                # Real case: Telegram (6777:33202) — wrapper 62.5%×62.5% + leaf 62.5%×62.5%
                # = icon shrunk to ~25px (barely visible).
                # Fix U-405: check each dimension independently.
                # Bug: the original 'or' stripped height:480px whenever width was a percent
                # (e.g., EarnUseCase: width:100%, height:480px → height stripped incorrectly).
                if not _has_wrapper and _strip & _POSITION_PROPS:
                    _root_css_for_dim = orig_css_map.get(
                        (lc_ir.get('semantic') or {}).get('className', ''), {})
                    _w_val = _root_css_for_dim.get('width', '')
                    _h_val = _root_css_for_dim.get('height', '')
                    # Only strip non-100% percentages: 100%×100%=100% (no double-nesting).
                    # Stripping width:100% would collapse the leaf to content width.
                    if '%' in str(_w_val) and str(_w_val).strip() not in ('100%',):
                        _strip = _strip | {'width'}
                    if '%' in str(_h_val) and str(_h_val).strip() not in ('100%',):
                        _strip = _strip | {'height'}
                _leaf_css = _css_from_orig(lc_ir, orig_css_map, css_ext,
                                           strip_root_props=_strip)
                # 补充宽度：如果组件根 CSS 没有 width 但子节点有 width:100%，
                # 说明子节点依赖父容器宽度；注入 bb.width 避免内容撑开导致宽度不一致
                _root_cls = (lc_ir.get('semantic') or {}).get('className', '')
                _bb = lc_ir.get('bb') or {}
                _bb_w = _bb.get('width', 0)
                if _root_cls and _bb_w and 'width' not in _strip:
                    _root_re = re.compile(r'(\.' + re.escape(_root_cls) + r'\s*\{)([^}]*)\}')
                    _rm = _root_re.search(_leaf_css)
                    if _rm and 'width' not in _rm.group(2):
                        # 检查子节点是否有 width: 100%
                        _has_child_full_width = any(
                            'width: 100%' in (_css_from_orig(c, orig_css_map, css_ext) if (c.get('semantic') or {}).get('className') else '')
                            for c in (lc_ir.get('children') or [])
                            if (c.get('semantic') or {}).get('className')
                        )
                        if _has_child_full_width:
                            _inject = f"width: {_bb_w:.0f}px;\n  "
                            _leaf_css = _leaf_css[:_rm.end(1)] + '\n  ' + _inject + _rm.group(2) + '}' + _leaf_css[_rm.end():]
                # vector 图形组件去掉 overflow:hidden — 两种情况：
                # 1. 子节点用百分比 absolute 定位（精度差异导致裁剪）
                # 2. 子节点是 decorative vector/image（flex 定位），但图标高度因 padding
                #    挤压而超出内容区域被裁剪（如 Telegram/Discord icon 26.52px > 24px）
                # Real case 2: 6777:33202 telegram — padding:8px 4px 挤出 24px 内容高，
                # 图标 26.52px > 24px，overflow:hidden 裁掉底部约 3px。
                _lc_children = lc_ir.get('children') or []
                _all_children_abs = all(
                    (c.get('css') or {}).get('position') == 'absolute'
                    for c in _lc_children
                    if c.get('css')
                )
                _all_children_deco = bool(_lc_children) and all(
                    c.get('isVectorNode') or c.get('isImageNode') or c.get('isDecorativeElement')
                    for c in _lc_children
                )
                # Guard: border-radius + overflow:hidden = shaped clipping container.
                # Removing overflow:hidden from such a leaf would let abs-positioned
                # children bleed outside the rounded rectangle.
                # Real case: Pic (250:3291) — 240x240 border-radius:16px container
                # clips a 1075px-wide decorative image; stripping causes image bleed.
                _has_border_radius = bool(
                    (lc_ir.get('css') or {}).get('border-radius')
                )
                if (_all_children_abs or _all_children_deco) and \
                        (lc_ir.get('css') or {}).get('overflow') == 'hidden' and \
                        not _has_border_radius:
                    _leaf_css = _leaf_css.replace('overflow: hidden;\n', '')
                    _leaf_css = _leaf_css.replace('overflow: hidden;', '')
                # When a component has varying image props (type='image' varyingProp),
                # different instances may use images with different aspect ratios.
                # The shared CSS uses template dimensions that may not match other instances,
                # causing severe distortion (e.g. EarnUseCase: landscape vs portrait).
                # Inject object-fit:cover on image nodes to prevent distortion at the cost
                # of a different crop (which is less harmful than aspect ratio distortion).
                _vp_image_props = {
                    vp.get('propName', '')
                    for vp in (lc.get('varyingProps') or [])
                    if vp.get('type') == 'image'
                }
                if _vp_image_props:
                    def _find_img_classes(node, img_prop_names, result=None):
                        if result is None: result = []
                        if node.get('isImageNode'):
                            cls = (node.get('semantic') or {}).get('className', '')
                            if cls:
                                result.append(cls)
                        for ch in (node.get('children') or []):
                            _find_img_classes(ch, img_prop_names, result)
                        return result
                    for _img_cls in _find_img_classes(lc_ir, _vp_image_props):
                        _cls_re = re.compile(
                            r'(\.' + re.escape(_img_cls) + r'\s*\{)([^}]*)\}', re.DOTALL)
                        def _inject_obj_fit(m):
                            body = m.group(2)
                            if 'object-fit' not in body:
                                body = body.rstrip('\n') + '\n  object-fit: cover;\n'
                            return m.group(1) + body + '}'
                        _leaf_css = _cls_re.sub(_inject_obj_fit, _leaf_css)
                (s_dir / f'{lc_name}.module.{css_ext}').write_text(_leaf_css)
                # U-376: Strip duplicate padding from wrapper instance classes.
                # When a wrapper instance (inst_cls != root_cls) has the SAME padding
                # as the leaf component's root class, the rendered padding is doubled.
                # Real case: OneTap 6777:32832 → wrapper 'frame-n6777-32832' has
                # padding:32px 36px 0 36px = same as OneTap root 'frame'. Result:
                # 32px × 2 = 64px top spacing → mt5-terminal taller → button misaligned.
                if orig_css_map and _lc_inst_fids and _lc_root_cls:
                    _lc_root_padding = (orig_css_map.get(_lc_root_cls) or {}).get('padding')
                    if _lc_root_padding:
                        for _wfid in _lc_inst_fids:
                            _wcls = fid_to_class.get(_wfid, '') if fid_to_class else ''
                            if _wcls and _wcls != _lc_root_cls:
                                _wcss = orig_css_map.get(_wcls)
                                if _wcss and _wcss.get('padding') == _lc_root_padding:
                                    # U-393: skip partial-coverage wrapper classes.
                                    # If only 1 of N *wrapper* instances uses this class,
                                    # it's a variant wrapper — its padding/width are
                                    # intentional, not duplicated from the leaf root.
                                    # NOTE: denominator = wrapper instances only (class ≠
                                    # root); root-class instances need no wrapper and must
                                    # not inflate N, or the guard fires when there is only
                                    # 1 wrapper instance alongside 1 no-wrapper instance.
                                    # Real case: OneTap 6777:32832 (wrapper) + 6777:32817
                                    # (root, no wrapper) → old guard 1<2 skipped the strip.
                                    _fids_using_wcls = sum(
                                        1 for _f in _lc_inst_fids
                                        if (fid_to_class.get(_f) if fid_to_class else '') == _wcls
                                    )
                                    _fids_with_any_wrapper = sum(
                                        1 for _f in _lc_inst_fids
                                        if (fid_to_class.get(_f) if fid_to_class else '')
                                        not in ('', _lc_root_cls)
                                    )
                                    if _fids_using_wcls < _fids_with_any_wrapper:
                                        continue
                                    # Remove duplicate padding (leaf component handles it).
                                    # Also remove fixed pixel width — parent flex determines size.
                                    # The Layer 1 validator's _base_has_prop logic will accept
                                    # the missing property when a sibling instance class has it.
                                    _strip_k = {'padding'}
                                    _ww = str(_wcss.get('width', ''))
                                    if _ww.endswith('px') and _ww != '100%':
                                        _strip_k.add('width')
                                    orig_css_map[_wcls] = {
                                        k: v for k, v in _wcss.items()
                                        if k not in _strip_k
                                    }
            # _prop_name 标记已用于 leaf TSX 生成，必须立即清除：
            # node_index 共享引用导致 section render_jsx_body 也会遇到这些标记，
            # 否则生成未定义变量（如 {buttonText}），导致 React 渲染报错。
            _strip_prop_markers(lc_ir)
            leaf_names.append(lc_name)

        # 1a-sub. 生成 SubSection 子目录（Section 内部复杂子 FRAME → 独立子组件）
        # 规则：subSections 由 cmd_analyze 按节点复杂度（后代数 > 20）检测，平级同拆。
        _subsection_map: dict = {}  # {root_figmaId → sub_section_name}
        _subsection_responsive: dict = {}  # {ss_name → 'h5Only'|'pcOnly'}
        _subsection_overflow_x: dict = {}  # {ss_name → 'auto'} for h5Only sections with wide content
        _subsection_h5_root_css: dict = {}  # {ss_name → root css dict} for h5Only subsection roots
        # h5Only subsection disambiguation: detect h5Only subsections with same name as PC siblings.
        # e.g. FaqSection (h5Only, just header) + FaqSection (PC, full) → rename h5Only to FaqSectionH5
        # so both get SEPARATE component files and the parent TSX doesn't duplicate H5 content.
        _h5only_name_overrides = _disambiguate_h5only_subsection_names(s.get('subSections', []))
        # U-443: same-named subSections (e.g. 6 H5 "Why Institutions Choose Us" in DemoTrading)
        # must each get a unique directory/component. Track occurrences and suffix with 2, 3, ...
        _ss_name_counts: dict = {}
        # U-459: track leaf instance FIDs inside subSections; section-level moly text
        # override loop skips these so the :global() rule is emitted in subSection CSS.
        _subsection_leaf_fids: set = set()
        for ss in s.get('subSections', []):
            ss_ir_raw = ss['ir']
            _ss_fid_raw = ss_ir_raw.get('figmaId', '')
            ss_name = _h5only_name_overrides.get(_ss_fid_raw, ss['name'])
            _ss_name_counts[ss_name] = _ss_name_counts.get(ss_name, 0) + 1
            if _ss_name_counts[ss_name] > 1:
                ss_name = f'{ss_name}{_ss_name_counts[ss_name]}'
            ss_ir_norm = _get_normalized(ss_ir_raw, node_index)
            if fid_to_class:
                _apply_orig_classnames(ss_ir_norm, fid_to_class)
            ss_sub_dir = s_dir / ss_name
            ss_sub_dir.mkdir(exist_ok=True)
            # index.tsx — 子 Section 作为独立组件
            # hooks_rel_path 根据目录深度动态计算（components/ 为基准）
            _ss_depth = len(ss_sub_dir.relative_to(components_dir).parts)  # 从 components/ 到 ss_sub_dir
            # hooks/ 与 components/ 平级，需多出一层 ../
            _ss_hooks_rel = '../' * (_ss_depth + 1) + 'hooks/usePageEnv'
            # texts 文件在 target 根（与 index.tsx 同级），比 components/ 多出一层
            _ss_texts_rel = '../' * (_ss_depth + 1)
            # 检测父 Section 的叶子组件中哪些实例在本子 Section 内
            # 有则切换到 _generate_section_tsx（支持叶子替换），路径从父目录相对引用
            _ss_fids = _collect_fids_in_subtree(ss_ir_raw)
            _ss_leaves: list = []
            _ss_lnames: list = []
            for _li, _lc in enumerate(s.get('leafComponents', [])):
                _lc_fids = set(_lc.get('allInstanceFigmaIds') or
                               [(_lc.get('ir') or {}).get('figmaId', '')])
                if _lc_fids & _ss_fids and _li < len(leaf_names):
                    _ss_leaves.append(_lc)
                    _ss_lnames.append(leaf_names[_li])

            if _ss_leaves:
                # 叶子文件已生成于父 Section 目录，子 Section 从 '../' import
                _ss_texts = '/'.join(['..'] * (_ss_depth + 1))
                ss_tsx, _ = _generate_section_tsx(
                    ss_name, ss_ir_norm, ss_ir_raw,
                    _ss_leaves, _ss_lnames,
                    css_ext, node_index,
                    orig_css_map=orig_css_map,
                    fid_to_class=fid_to_class,
                    texts_map=_texts_map or None,
                    page_comp=_page_comp,
                    is_mobile=_is_mobile_page,
                    cc_snippets=_cc_snippets or None,
                    page_theme=_page_theme,
                    leaf_import_prefix='../',
                    hooks_rel=_ss_hooks_rel,
                    texts_rel=_ss_texts,
                )
            else:
                ss_tsx = _generate_leaf_tsx(ss_ir_norm, ss_name, css_ext,
                                            page_theme=_page_theme,
                                            texts_map=_texts_map, page_comp=_page_comp,
                                            texts_rel_prefix=_ss_texts_rel,
                                            hooks_rel_path=_ss_hooks_rel)
            (ss_sub_dir / INDEX_TSX_FILENAME).write_text(ss_tsx)
            # 子 Section 的 leaf wrapper 需与顶层 Section 相同的 visual-strip 逻辑：
            # leaf 组件自身已输出完整视觉 CSS（padding/background/border），
            # 子 Section CSS 中的 wrapper class 只需保留定位属性。
            _ss_moly_wrappers: set = set()
            if fid_to_class and _ss_leaves:
                for _ssl in _ss_leaves:
                    if not _ssl.get('ccComponent'):
                        _ssl_inst_fids = set(_ssl.get('allInstanceFigmaIds') or [])
                        for _sfid in _ssl_inst_fids:
                            _scls = fid_to_class.get(_sfid, '')
                            if not _scls:
                                continue
                            _s_other_users = [f for f, c in fid_to_class.items()
                                              if c == _scls and f not in _ssl_inst_fids]
                            if not _s_other_users:
                                # 部分覆盖检测（同顶层 section 逻辑）：
                                # 若只有部分叶子实例使用这个 class，是变体视觉包装层，不能 strip。
                                # Fix: node 6777:33313 from Arena SectionRankingSection (6777-33238)
                                _fids_using_scls = sum(1 for f in _ssl_inst_fids
                                                       if fid_to_class.get(f) == _scls)
                                if _fids_using_scls < len(_ssl_inst_fids):
                                    # U-295: 区分斑马纹变体 vs 实例唯一 class
                                    _ssl_root_cls = (_ssl.get('ir') or {}).get(
                                        'semantic', {}).get('className', '')
                                    _ssl_non_using_unique = all(
                                        fid_to_class.get(f, '') not in ('', _ssl_root_cls)
                                        for f in _ssl_inst_fids if fid_to_class.get(f) != _scls
                                    )
                                    if _ssl_non_using_unique:
                                        _ss_moly_wrappers.add(_scls)
                                    # else: 变体包装层，跳过 strip
                                else:
                                    _ss_moly_wrappers.add(_scls)
            # CSS 文件名与 TSX 中 import 保持一致：
            # _generate_section_tsx → import './index.module.{ext}' → 写 index.module.{ext}
            # _generate_leaf_tsx   → import './{ss_name}.module.{ext}' → 写 {ss_name}.module.{ext}
            ss_css = _css_from_orig(ss_ir_norm, orig_css_map or {}, css_ext,
                                    moly_wrapper_classes=_ss_moly_wrappers or None)
            # 追加 subSection 内部 inline pcOnly/h5Only 节点的响应式 CSS。
            # 顶层 Section 在 lines 2084-2103 做同样的操作；subSection 也需要，否则
            # FaqSection 内的 pcOnly/h5Only 子节点永远不会生成 display:none 规则。
            if fid_to_class:
                import re as _re_ss_inline
                _ss_inline_resp = _collect_inline_responsive_flags(ss_ir_raw, fid_to_class, set())
                for _ir_cls, _ir_resp in _ss_inline_resp.items():
                    if _ir_cls.startswith('attr:'):
                        _sel = f'[data-figma-id="{_ir_cls[5:]}"]'
                    else:
                        _sel = f'.{_ir_cls}'
                    if _ir_resp == 'h5Only':
                        _ss_fid = _ir_cls[5:] if _ir_cls.startswith('attr:') else None
                        _ss_disp = _h5only_show_display(ss_ir_raw, _ss_fid)
                        ss_css += (
                            f'\n{_sel} {{\n  display: none !important;\n}}\n'
                            f'\n@media (max-width: 768px) {{\n  {_sel} {{\n    display: {_ss_disp} !important;\n  }}\n}}\n'
                        )
                    elif _ir_resp == 'pcOnly':
                        ss_css += (
                            f'\n@media (max-width: 768px) {{\n  {_sel} {{\n    display: none !important;\n  }}\n}}\n'
                        )
                _ss_resp_overrides = _collect_responsive_overrides_from_ir(ss_ir_raw, fid_to_class, set())
                if _ss_resp_overrides:
                    _ss_resp_by_key: dict = {}
                    for _rc_cls, _rc_bp, _rc_css in _ss_resp_overrides:
                        k = (_rc_cls, _rc_bp)
                        if k not in _ss_resp_by_key:
                            _ss_resp_by_key[k] = {}
                        _ss_resp_by_key[k].update(_rc_css)
                    for (_rc_cls, _rc_bp), _rc_props in _ss_resp_by_key.items():
                        # U-437: apply var() fallbacks for design tokens in responsive CSS.
                        # _rawBgHex/_rawColorHex in the css_dict carry the design-time hex
                        # so var(--color-bg) → var(--color-bg, #121214).
                        _rc_props = _inject_var_fallbacks(_rc_props)
                        _prop_lines = []
                        for _pk, _pv in _rc_props.items():
                            if _pk.startswith('--'):
                                _pk_kebab = _pk
                            else:
                                _pk_kebab = _re_ss_inline.sub(r'([A-Z])', r'-\1', _pk).lower().lstrip('-')
                            _prop_lines.append(f'    {_pk_kebab}: {_pv};')
                        ss_css += (
                            f'\n@media (max-width: {_rc_bp}px) {{\n  .{_rc_cls} {{\n'
                            f'{chr(10).join(_prop_lines)}\n  }}\n}}\n'
                        )
            # U-459: generate :global(.moly-collapse/.moly-tabs) overrides for moly leaves
            # inside this subSection; also register their FIDs so the section-level loop
            # can skip them (avoiding the :global rule in the wrong CSS Module file).
            if fid_to_class and _ss_leaves:
                _ss_moly_text_overrides: list = []
                for _ssl in _ss_leaves:
                    if _ssl.get('ccComponent') not in _MOLY_TEXT_OVERRIDE_COMPONENTS:
                        continue
                    for _sfid in (_ssl.get('allInstanceFigmaIds') or []):
                        _scls = fid_to_class.get(_sfid, '')
                        if not _scls:
                            continue
                        _subsection_leaf_fids.add(_sfid)
                        _ov_node = node_index.get(_sfid)
                        if _ov_node:
                            _t_fs, _t_lh, _c_fs, _c_lh, _i_gap, _c_gap = (
                                _extract_moly_text_sizes(_ov_node))
                            if _t_fs:
                                _ss_moly_text_overrides.append(
                                    (_scls, _ssl['ccComponent'],
                                     _t_fs, _t_lh, _c_fs, _c_lh, _i_gap, _c_gap))
                if _ss_moly_text_overrides:
                    ss_css += '\n' + _generate_cc_text_overrides(_ss_moly_text_overrides, page_theme=_page_theme)
            _ss_css_name = 'index' if _ss_leaves else ss_name
            (ss_sub_dir / f'{_ss_css_name}.module.{css_ext}').write_text(
                ss_css or f'/* {ss_name} */\n')
            # 注册 root figmaId，供父 Section TSX 替换用
            root_fid = ss_ir_raw.get('figmaId', '')
            if root_fid:
                _subsection_map[root_fid] = ss_name
            if ss_ir_raw.get('h5Only'):
                _subsection_responsive[ss_name] = 'h5Only'
                # Store root CSS to derive wrapper sizing rules (U-447: absolute root collapses wrapper).
                _subsection_h5_root_css[ss_name] = ss_ir_raw.get('css') or {}
                # Propagate overflow-x from IR responsive array (set by Fix C when h5Only
                # sibling has wide-table descendants, e.g. EU campaign TieredRewardsSection
                # tier table 908px that overflows 390px mobile viewport).
                for _resp_entry in (ss_ir_raw.get('responsive') or []):
                    _ovx = _resp_entry.get('css', {}).get('overflow-x')
                    if _ovx:
                        _subsection_overflow_x[ss_name] = _ovx
                        break
            elif ss_ir_raw.get('pcOnly'):
                _subsection_responsive[ss_name] = 'pcOnly'

        # Detect PC subSections that share a name with h5Only subSections → add pcOnly wrapper
        # Real case: Component10Section has FaqSection (PC) and FaqSection (H5, h5Only).
        # Use a separate set to avoid key collision with _subsection_responsive dict
        # (both would map to 'FaqSection' but need different responsive rules).
        _same_name_pc = _find_same_name_pc_for_h5only_subsections(s.get('subSections', []))
        if _same_name_pc:
            for _ss in s.get('subSections', []):
                _ss_ir = _ss.get('ir') or {}
                if _ss['name'] in _same_name_pc and not _ss_ir.get('h5Only'):
                    _ss_fid = _ss_ir.get('figmaId', '')
                    if _ss_fid and _ss_fid in node_index:
                        node_index[_ss_fid]['pcOnly'] = True  # _render_node reads from node_index
                    # Use name-qualified key to avoid collision with h5Only entry
                    _subsection_responsive[f'pc:{_ss["name"]}'] = 'pcOnly'

        # 1b. 生成 Section index.tsx（使用 render_jsx_body_with_leaf_refs 替换 leaf 引用）
        _section_tsx, _section_needs_theme = _generate_section_tsx(
            s_name, s_ir, s['ir'],
            s.get('leafComponents', []),
            leaf_names, css_ext, node_index,
            orig_css_map=orig_css_map,
            fid_to_class=fid_to_class,
            texts_map=_texts_map or None,
            page_comp=_page_comp,
            is_mobile=_is_mobile_page,
            cc_snippets=_cc_snippets or None,
            page_theme=_page_theme,
            subsection_map=_subsection_map or None)
        (s_dir / INDEX_TSX_FILENAME).write_text(_section_tsx)
        if _section_needs_theme:
            _sections_needing_theme.add(s_name)
        # 1c. 生成 Section index.module.less（CSS 来自原始文件，保留所有 patch 值）
        # CC wrapper class 只保留定位属性，视觉由 CC 组件自身提供
        # Button 用 className（无 wrapper），其他 CC 组件用 wrapper div
        _moly_wrappers: set = set()
        _moly_cc_wrappers: set = set()   # only actual Code Connect component wrappers (for > * rule)
        _moly_button_classes: set = set()
        _moly_button_cc_confirmed: set = set()
        _moly_text_override_cls: set = set()
        _moly_text_overrides: list = []
        if fid_to_class:
            for lc in s.get('leafComponents', []):
                if lc.get('ccComponent'):
                    for fid in (lc.get('allInstanceFigmaIds') or []):
                        cls = fid_to_class.get(fid, '')
                        if cls:
                            if lc.get('ccComponent') in _MOLY_CLASSNAME_COMPONENTS:
                                if fid in _cc_confirmed_fids:
                                    _moly_button_cc_confirmed.add(cls)
                                else:
                                    _moly_button_classes.add(cls)
                                _btn_node = node_index.get(fid)
                                if _btn_node and orig_css_map and cls in orig_css_map:
                                    _text_color = None
                                    for _bc in (_btn_node.get('children') or []):
                                        _bc_css = _bc.get('css') or {}
                                        if _bc.get('isTextNode') and 'color' in _bc_css:
                                            _text_color = _bc_css['color']
                                            break
                                        # depth-2: some CC components (e.g. Tag) wrap
                                        # text in a layout-control intermediate node
                                        if not _bc.get('isTextNode'):
                                            for _gc in (_bc.get('children') or []):
                                                _gc_css = _gc.get('css') or {}
                                                if _gc.get('isTextNode') and 'color' in _gc_css:
                                                    _text_color = _gc_css['color']
                                                    break
                                        if _text_color:
                                            break
                                    if _text_color:
                                        orig_css_map[cls] = {**orig_css_map[cls], 'color': _text_color}
                            elif lc.get('ccComponent') in _MOLY_TEXT_OVERRIDE_COMPONENTS:
                                # U-459: skip FIDs that live inside a subSection —
                                # their :global() override is emitted in the subSection CSS.
                                if fid in _subsection_leaf_fids:
                                    continue
                                _moly_text_override_cls.add(cls)
                                _ov_node = node_index.get(fid)
                                if _ov_node:
                                    t_fs, t_lh, c_fs, c_lh, i_gap, c_gap = _extract_moly_text_sizes(_ov_node)
                                    if t_fs:
                                        _moly_text_overrides.append(
                                            (cls, lc['ccComponent'], t_fs, t_lh, c_fs, c_lh, i_gap, c_gap))
                            else:
                                # Skip stripping if wrapper has backdrop-filter or
                                # background-color — it's a styled container (glass button),
                                # not just a positioning div. Real case: node 181:3025
                                # (events wrapping IconShare) in Web-merged-181-2980.
                                _wrapper_css = (orig_css_map or {}).get(cls, {})
                                if not (_wrapper_css.get('backdrop-filter')
                                        or _wrapper_css.get('-webkit-backdrop-filter')):
                                    _moly_wrappers.add(cls)
                                    _moly_cc_wrappers.add(cls)  # true Code Connect wrapper → gets > * rule
                else:
                    # 非 CC leaf wrapper strip 逻辑：
                    # leaf 组件自身已输出完整视觉 CSS，section wrapper 只需定位。
                    # 但如果 class 同时被非 leaf-instance 的节点使用（section 内联渲染），
                    # 不能 strip（否则内联位置丢视觉）。
                    _inst_fids = set(lc.get('allInstanceFigmaIds') or [])
                    for fid in _inst_fids:
                        cls = fid_to_class.get(fid, '')
                        if not cls:
                            continue
                        # 检查是否有其他非 instance 节点也用这个 class
                        _other_users = [f for f, c in fid_to_class.items()
                                        if c == cls and f not in _inst_fids]
                        if not _other_users:
                            # 部分覆盖检测：若只有「部分」叶子实例使用这个 class（如斑马纹偶数行），
                            # 该 class 提供变体视觉样式（background-color/padding 等），
                            # 不能 strip（否则视觉样式丢失）。
                            # 例：Arena ranking-row-2 仅被 2/4 个 RankingRow1 实例使用。
                            # Fix: node 6777:33313 from Arena (6777-33238)
                            _fids_using_cls = sum(1 for f in _inst_fids
                                                  if fid_to_class.get(f) == cls)
                            if _fids_using_cls < len(_inst_fids):
                                # 两种情况需要区分（U-295）：
                                # 1. 斑马纹变体（Arena ranking-row-2）：不使用该 class 的实例
                                #    fallback 到 root_cls → 该 class 提供差异视觉，不能 strip。
                                # 2. 实例唯一 class（TopUp container-I...-6388/6400）：每个
                                #    实例有自己的唯一 class，不使用该 class 的实例有另一个唯一
                                #    class，而非 root_cls → 应 strip 视觉属性。
                                _root_cls_val = (lc_ir.get('semantic') or {}).get('className', '')
                                _non_using_all_unique = all(
                                    fid_to_class.get(f, '') not in ('', _root_cls_val)
                                    for f in _inst_fids if fid_to_class.get(f) != cls
                                )
                                if _non_using_all_unique:
                                    # 实例唯一 class — strip 视觉属性
                                    # Exception: styled containers (backdrop-filter = glass
                                    # effect) must keep their visual properties.
                                    _inst_css = (orig_css_map or {}).get(cls, {})
                                    if not (_inst_css.get('backdrop-filter')
                                            or _inst_css.get('-webkit-backdrop-filter')):
                                        _moly_wrappers.add(cls)
                                # else: 变体包装层 — 跳过 strip
                            else:
                                _inst_css = (orig_css_map or {}).get(cls, {})
                                if not (_inst_css.get('backdrop-filter')
                                        or _inst_css.get('-webkit-backdrop-filter')):
                                    _moly_wrappers.add(cls)
        _s_fid = s['ir'].get('figmaId', '')
        _s_z = _figma_layer_order.get(_s_fid)
        # 计算 section 根节点的真实垂直间距（排除上方内容已占用的高度）
        # 非 flow 页：计算 Section 的页面相对 y，存入 dict 供 Page.tsx wrapper 使用
        # （不修改 Section CSS，因为同名 class 可能被 Section 内层元素复用，
        #   CSS 改动会导致内层元素错误地被绝对定位）
        _s_abs_top: float | None = None
        _s_abs_left: float | None = None
        _s_abs_width: float | None = None
        if not _can_flow and _page_root_y is not None:
            _s_ir_css_top = str((s.get('ir') or {}).get('css', {}).get('top', '0'))
            if not _re.match(r'[1-9]', _s_ir_css_top):  # 无正数 CSS top（relative section）
                _s_y = s.get('y', float('inf'))
                if _s_y != float('inf'):
                    _s_abs_top = max(0.0, _s_y - _page_root_y)
        # 固定尺寸 section 水平偏移：用画布绝对 x（absoluteBoundingBox.x）减去页面原点 x。
        # 非 flow 页：abs_left + abs_width 用于 absolute wrapper。
        # flow 页：abs_left > 0 的窄 section 在 Page.tsx 中加 marginLeft wrapper 居中。
        # 例外：section IR CSS 已有 margin-left:auto 时自行居中，不再叠加 marginLeft。
        if _page_root_x is not None:
            _s_x = s.get('x')  # Figma canvas absolute x（来自 _x_map）
            if _s_x is not None:
                _s_left_px = float(_s_x) - _page_root_x
                _s_ir_css = (s.get('ir') or {}).get('css', {})
                _s_self_centers = _can_flow and _s_ir_css.get('margin-left') == 'auto'
                if _s_left_px > 0.5 and not _s_self_centers:  # 超过 0.5px 才认为是有意偏移
                    _s_abs_left = _s_left_px
                    if not _can_flow:
                        _s_bb_w = ((s.get('ir') or {}).get('bb') or {}).get('width')
                        if _s_bb_w and float(_s_bb_w) < 1440:
                            _s_abs_width = float(_s_bb_w)
        _section_abs_tops[s_name] = _s_abs_top
        _section_abs_lefts[s_name] = _s_abs_left
        _section_abs_widths[s_name] = _s_abs_width

        _s_adj_top: float | None = None
        if _can_flow:
            _s_css_top_str = str((s.get('ir') or {}).get('css', {}).get('top', '0px'))
            _s_top_m = re.match(r'([0-9.]+)px', _s_css_top_str)
            if _s_top_m:
                _s_top_px = float(_s_top_m.group(1))
                _s_adj_top = _compute_section_gap(
                    _s_top_px, s.get('y'), _all_inline_nodes,
                    split_plan.get('sections', []), current_section_figma_id=_s_fid)
        _section_css = _css_from_orig(s_ir, orig_css_map, css_ext,
                           moly_wrapper_classes=_moly_wrappers or None,
                           moly_button_classes=_moly_button_classes or None,
                           moly_button_cc_confirmed=_moly_button_cc_confirmed or None,
                           moly_text_override_classes=_moly_text_override_cls or None,
                           root_z_index=_s_z,
                           is_section_root=_can_flow,
                           section_top_adjusted_px=_s_adj_top,
                           moly_flex_child_classes=_moly_cc_wrappers or None)
        # 追加 CC 组件字号覆写
        if _moly_text_overrides:
            _section_css += '\n' + _generate_cc_text_overrides(_moly_text_overrides, page_theme=_page_theme)
        # 追加 h5Only/pcOnly subsection wrapper 的响应式 CSS（由 _render_node 生成包装 div）
        if _subsection_responsive:
            import re as _re_ss_css
            for _ss_nm, _ss_resp in _subsection_responsive.items():
                # 'pc:FaqSection' uses 'pc:' prefix for same-name PC/H5 pairs — strip before kebab
                _raw_nm = _ss_nm[3:] if _ss_nm.startswith('pc:') else _ss_nm
                _ss_kebab = _re_ss_css.sub(r'([A-Z])', r'-\1', _raw_nm).lower().lstrip('-')
                if _ss_resp == 'h5Only':
                    _wc = f'h5-{_ss_kebab}'
                    _ovx = _subsection_overflow_x.get(_ss_nm, '')
                    _ovx_rule = f'\n    overflow-x: {_ovx};' if _ovx else ''
                    # U-447: if root is absolute-positioned, add position:relative + height
                    # so the wrapper provides a sizing context for the abs child.
                    _abs_height_rule = _h5only_wrapper_height_rule(
                        _subsection_h5_root_css.get(_ss_nm, {}))
                    _section_css += (
                        f'\n.{_wc} {{\n  display: none;\n}}\n'
                        f'\n@media (max-width: 768px) {{\n  .{_wc} {{\n    display: block;\n    width: 100%;{_ovx_rule}{_abs_height_rule}\n  }}\n}}\n'
                    )
                elif _ss_resp == 'pcOnly':
                    _wc = f'pc-{_ss_kebab}'
                    # If the pcOnly subsection root is absolutely positioned, the wrapper
                    # div must also be position:absolute so it's removed from the section's
                    # flex flow and doesn't consume a gap slot (otherwise: height-0 flex
                    # item still generates a gap, adding extra height to the section).
                    # Real case: CalculateYourCashbackSection BgSection (250:3751) — a
                    # position:absolute decorative bg → wrapper adds an extra 48px gap.
                    _raw_nm_for_abs = _ss_nm[3:] if _ss_nm.startswith('pc:') else _ss_nm
                    _pconly_ss_ir = next(
                        ((ss.get('ir') or {}) for ss in s.get('subSections', [])
                         if ss.get('name') == _raw_nm_for_abs),
                        {}
                    )
                    _pconly_ss_pos = (_pconly_ss_ir.get('css') or {}).get('position', '')
                    if _pconly_ss_pos == 'absolute':
                        _section_css += (
                            f'\n.{_wc} {{\n  position: absolute;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  overflow: hidden;\n}}\n'
                            f'\n@media (max-width: 768px) {{\n  .{_wc} {{\n    display: none;\n  }}\n}}\n'
                        )
                    else:
                        _section_css += (
                            f'\n@media (max-width: 768px) {{\n  .{_wc} {{\n    display: none;\n  }}\n}}\n'
                        )
        # 追加 inline pcOnly/h5Only 节点的响应式 CSS（非 subSection 的 section 直接子节点）
        if fid_to_class:
            _inline_resp = _collect_inline_responsive_flags(
                s['ir'], fid_to_class, set(_subsection_map.keys())
            )
            for _ir_cls, _ir_resp in _inline_resp.items():
                # 'attr:FIGMAID' = shared-class node → use [data-figma-id="..."] selector
                if _ir_cls.startswith('attr:'):
                    _sel = f'[data-figma-id="{_ir_cls[5:]}"]'
                else:
                    _sel = f'.{_ir_cls}'
                if _ir_resp == 'h5Only':
                    _fid_for_disp = _ir_cls[5:] if _ir_cls.startswith('attr:') else None
                    _show_disp = _h5only_show_display(s['ir'], _fid_for_disp)
                    _section_css += (
                        f'\n{_sel} {{\n  display: none !important;\n}}\n'
                        f'\n@media (max-width: 768px) {{\n  {_sel} {{\n    display: {_show_disp} !important;\n  }}\n}}\n'
                    )
                elif _ir_resp == 'pcOnly':
                    _section_css += (
                        f'\n@media (max-width: 768px) {{\n  {_sel} {{\n    display: none !important;\n  }}\n}}\n'
                    )
        # 追加 merge_responsive.py 的 CSS diff responsive[] 覆盖（margin-left:0, margin-top:0 等）
        # _css_from_orig 使用 orig_css_map 不读取 responsive[]，需单独收集生成 @media 块
        if fid_to_class:
            import re as _re_resp_css
            # U-442: section root class — used to strip canvas absolute-positioning from
            # H5 responsive overrides (same logic as is_section_root in _css_from_orig rule 6).
            _section_root_fid = (s.get('ir') or {}).get('figmaId', '')
            _section_root_cls = fid_to_class.get(_section_root_fid, '')
            _resp_overrides = _collect_responsive_overrides_from_ir(
                s['ir'], fid_to_class, set(_subsection_map.keys())
            )
            # U-454: Flow sections (position:relative) following H5-absolute sections need
            # margin-top to appear below the last abs section's bottom edge on H5.
            # Real case: FaqExpandableSection (y:inf, position:relative) naturally appears
            # at ~1000px in H5 flow (after h5-only wrappers) but design places it at ~3675px.
            _s_ir_obj = s.get('ir') or {}
            _s_base_pos = (_s_ir_obj.get('css') or {}).get('position', '')
            _s_no_h5_resp = not (_s_ir_obj.get('responsive') or [])
            if (
                _can_flow
                and _h5_abs_sections_bottom > 0
                and _h5_flow_preceding_height >= 0
                and _section_root_cls
                and _s_base_pos == 'relative'
                and not _s_ir_obj.get('h5Only')
                and not _s_ir_obj.get('pcOnly')
                and _s_no_h5_resp
            ):
                _u454_margin = _h5_abs_sections_bottom - _h5_flow_preceding_height
                if _u454_margin > 0:
                    _resp_overrides.append((_section_root_cls, 768, {'margin-top': f'{_u454_margin:.2f}px'}))
            # Group by (class, breakpoint) and merge css dicts
            _resp_by_key: dict = {}
            for _rc_cls, _rc_bp, _rc_css in _resp_overrides:
                k = (_rc_cls, _rc_bp)
                if k not in _resp_by_key:
                    _resp_by_key[k] = {}
                _resp_by_key[k].update(_rc_css)
            for (_rc_cls, _rc_bp), _rc_props in _resp_by_key.items():
                # U-442: strip canvas absolute-positioning from section root responsive overrides
                if _can_flow and _section_root_cls and _rc_cls == _section_root_cls:
                    _rc_props = _strip_section_root_responsive_props(_rc_props, base_position=_s_base_pos)
                # U-437: inject var() fallbacks from _rawBgHex/_rawColorHex
                _rc_props = _inject_var_fallbacks(_rc_props)
                if not _rc_props:
                    continue
                _prop_lines = []
                for _pk, _pv in _rc_props.items():
                    if _pk.startswith('--'):
                        _pk_kebab = _pk  # CSS custom property — preserve -- prefix as-is
                    else:
                        _pk_kebab = _re_resp_css.sub(r'([A-Z])', r'-\1', _pk).lower().lstrip('-')
                    _prop_lines.append(f'    {_pk_kebab}: {_pv};')
                _props_block = '\n'.join(_prop_lines)
                _section_css += (
                    f'\n@media (max-width: {_rc_bp}px) {{\n  .{_rc_cls} {{\n'
                    f'{_props_block}\n  }}\n}}\n'
                )
        # Fix D: When section has h5Only subsections, zero out section root padding/gap on mobile.
        # PC padding is designed for PC layout; h5Only content has its own padding.
        # Real case: CalculateYourCashbackSection padding:80px 120px + gap:48px → 496px excess on mobile.
        # TieredRewardsSection/2 padding:60px top+bottom → 120px excess each.
        _has_h5only_subs = any(v == 'h5Only' for v in _subsection_responsive.values())
        if _has_h5only_subs:
            import re as _re_padfix
            _root_m = _re_padfix.search(r'\.([\w-]+)\s*\{([^}]*)\}', _section_css)
            if _root_m:
                _root_cls_name = _root_m.group(1)
                _root_body = _root_m.group(2)
                _root_has_padding = bool(_re_padfix.search(r'\bpadding\s*:', _root_body))
                _root_has_gap = bool(_re_padfix.search(r'\bgap\s*:', _root_body))
                if _root_has_padding or _root_has_gap:
                    _reset_parts = []
                    if _root_has_padding:
                        _reset_parts.append('    padding: 0;')
                    if _root_has_gap:
                        _reset_parts.append('    gap: 0;')
                    _section_css += (
                        f'\n@media (max-width: 768px) {{\n  .{_root_cls_name} {{\n'
                        + '\n'.join(_reset_parts)
                        + '\n  }\n}\n'
                    )
        (s_dir / f'index.module.{css_ext}').write_text(_section_css)
        # supplementText TODO file: if section IR has supplementText nodes, emit reminder
        _supp_todo_nodes = _find_supplement_text_nodes(s['ir'])
        if _supp_todo_nodes:
            _todo_content = (
                f"# supplementText TODO for {s_name}\n\n"
                f"The following nodes have H5-specific text variants stored in their IR.\n"
                f"To implement responsive text, replace hardcoded text with:\n\n"
                f"```tsx\n"
                f"const isMobile = useMobileSize()\n"
                f"const label = isMobile ? '<H5 text>' : '<PC text>'\n"
                f"```\n\n"
                f"## Affected node classes\n\n"
                + ''.join(f"- {n}\n" for n in _supp_todo_nodes)
            )
            (s_dir / 'supplementText-todo.md').write_text(_todo_content)
        section_names.append(s_name)

    # 2. 顶层叶子组件 → common/
    top_leaves = split_plan.get('leafComponents', [])
    top_leaf_names = []
    if top_leaves:
        common_dir = out_dir / 'common'
        common_dir.mkdir(exist_ok=True)
        for lc in top_leaves:
            lc_name = lc['name']
            lc_ir = _get_normalized(lc['ir'], node_index)
            _propagate_plan_css_overrides(lc.get('ir') or {}, lc_ir)
            if fid_to_class:
                _apply_orig_classnames(lc_ir, fid_to_class)
            mark_varying_nodes(lc_ir, lc.get('varyingProps') or [])
            if lc.get('ccComponent'):
                _write_moly_flat(common_dir, lc_name,
                                 lc['ccComponent'],
                                 lc.get('ccImport') or 'your-component-lib',
                                 css_ext,
                                 all_imports=lc.get('allImports'),
                                 snippet=lc.get('snippet'),
                                 varying_props=lc.get('varyingProps'),
                                 instance_ir=lc.get('ir'))
            else:
                (common_dir / f'{lc_name}.tsx').write_text(
                    _generate_leaf_tsx(lc_ir, lc_name, css_ext, page_theme=_page_theme,
                                       texts_map=_texts_map, page_comp=_page_comp,
                                       texts_rel_prefix='../../')
                )
                (common_dir / f'{lc_name}.module.{css_ext}').write_text(
                    _css_from_orig(lc_ir, orig_css_map, css_ext,
                                   strip_root_props=_detect_width_conflict(
                                       lc, node_index, fid_to_class, orig_css_map))
                )
            _strip_prop_markers(lc_ir)
            top_leaf_names.append(lc_name)

    # 3. 处理 inlineNodes（h≤200 的非装饰 depth-1 节点，inline 渲染到 Page.tsx）
    inline_items = []   # [{'y': float, 'jsx': str}]
    inline_css_parts = []
    for n in _all_inline_nodes:
        n_ir = _get_normalized(n['ir'], node_index)
        if fid_to_class:
            _apply_orig_classnames(n_ir, fid_to_class)
        jsx = render_jsx_body(n_ir, texts_map=_texts_map or None)
        _n_fid = n['ir'].get('figmaId', '')
        _n_z = _figma_layer_order.get(_n_fid)
        # 检测 inline node 是否与某个 Section 在 Y 轴重叠（overlay），重叠则保持 absolute
        _n_y = n.get('y', float('inf'))
        _n_h = (n.get('ir') or {}).get('bb', {}).get('height', 0)
        _is_overlay = _is_section_overlay(_n_y, _n_h, _section_y_ranges)
        # 只有页面结构适合 flow 且节点非 overlay 时才启用 is_inline_root
        _use_inline_root = _can_flow and not _is_overlay
        css = _css_from_orig(n_ir, orig_css_map, css_ext, root_z_index=_n_z,
                             is_inline_root=_use_inline_root,
                             section_orig_top=_section_orig_top)
        # 非 flow 页：inline node 若 position:relative 且无 CSS top，补充页面绝对定位
        if not _can_flow and not _is_overlay and _page_root_y is not None:
            _n_css_top = str((n.get('ir') or {}).get('css', {}).get('top', '0'))
            if not _re.match(r'[1-9]', _n_css_top):
                _n_y = n.get('y', float('inf'))
                if _n_y != float('inf'):
                    _n_abs_top = max(0.0, _n_y - _page_root_y)
                    if _n_abs_top > 0:
                        _n_root_cls = (n_ir.get('semantic') or {}).get('className', '')
                        if _n_root_cls and css:
                            _n_bb = (n.get('ir') or {}).get('bb', {})
                            _n_bb_h = _n_bb.get('height', 0)
                            _n_bb_w = _n_bb.get('width', 0)
                            _n_h_rule = f'\n  height: {_n_bb_h:.4g}px;' if _n_bb_h > 0 else ''
                            _n_w_rule = f'\n  width: {_n_bb_w:.4g}px;' if _n_bb_w > 0 else ''
                            _n_l_rule = ''
                            _n_x = n.get('x')
                            if _n_x is not None and _page_root_x is not None:
                                _n_abs_left = max(0.0, float(_n_x) - _page_root_x)
                                _n_l_rule = f'\n  left: {_n_abs_left:.4g}px;'
                            _n_repl = (r'\1position: absolute;\n  top: '
                                       + f'{_n_abs_top:.4g}px;'
                                       + _n_l_rule
                                       + _n_h_rule
                                       + _n_w_rule)
                            css = _re.sub(
                                r'(\.' + _re.escape(_n_root_cls) + r'\s*\{[^}]*?)position:\s*relative\s*;?',
                                _n_repl,
                                css, count=1, flags=_re.DOTALL
                            )
        if jsx.strip():
            inline_items.append({'y': n.get('y', float('inf')), 'jsx': jsx})
        if css.strip():
            inline_css_parts.append(css)
        # U-460: collect responsive @media overrides for inline nodes (e.g. page-level
        # butterflies with H5 top/left values in the merged IR responsive[] array).
        # Without this, page-level absolute elements stay at their PC positions on mobile.
        for (_n_cls, _n_bp, _n_css_d) in _collect_responsive_overrides_from_ir(
                n_ir, fid_to_class, set(), allow_semantic_fallback=True):
            _n_resp_block = f'\n@media (max-width: {_n_bp}px) {{\n  .{_n_cls} {{\n'
            for _n_k, _n_v in _n_css_d.items():
                _n_resp_block += f'    {_n_k}: {_n_v};\n'
            _n_resp_block += f'  }}\n}}\n'
            inline_css_parts.append(_n_resp_block)
        _n_root_cls2 = (n_ir.get('semantic') or {}).get('className', '')
        if _n_root_cls2 and n['ir'].get('h5Only'):
            inline_css_parts.append(
                f'@media (min-width: 769px) {{\n  .{_n_root_cls2} {{\n    display: none;\n  }}\n}}'
            )
        elif _n_root_cls2 and n['ir'].get('pcOnly'):
            inline_css_parts.append(
                f'@media (max-width: 768px) {{\n  .{_n_root_cls2} {{\n    display: none;\n  }}\n}}'
            )
        elif _n_root_cls2 and n['ir'].get('structuralSplit'):
            # U-445: structuralSplit inline nodes were matched from PC base; their
            # base content is PC-only (the H5 supplement differs too much to share).
            # Hide the PC content on H5 — same as pcOnly, without needing pcOnly flag.
            inline_css_parts.append(
                f'@media (max-width: 768px) {{\n  .{_n_root_cls2} {{\n    display: none;\n  }}\n}}'
            )
            # U-450: Render the H5 supplement inline alongside the PC version.
            # Without this, the H5 navigation bar (e.g. DemoTrading App_bar 88px)
            # is completely absent on mobile — only the PC nav existed and got hidden.
            _u450_supp = n['ir'].get('supplementNode')
            if _u450_supp and (_u450_supp.get('css') or _u450_supp.get('children')):
                import copy as _copy_u450
                _u450_h5_ir = _copy_u450.deepcopy(_u450_supp)
                # Convert position:absolute at top≈0 to position:relative so the
                # supplement occupies its natural height in the flex-column page flow.
                # Without this, the nav overlays page content at z-index:auto=0,
                # and the hero section starts at y=0 instead of y=nav_height.
                _u450_supp_css = _u450_h5_ir.get('css') or {}
                try:
                    _u450_supp_top = float(str(_u450_supp_css.get('top', '999px')).rstrip('px'))
                except ValueError:
                    _u450_supp_top = 999.0
                if _u450_supp_css.get('position') == 'absolute' and _u450_supp_top < 10:
                    _u450_h5_ir['css'] = {k: v for k, v in _u450_supp_css.items()
                                          if k not in ('top', 'left')}
                    _u450_h5_ir['css']['position'] = 'relative'
                    _u450_h5_ir['css']['width'] = '100%'
                _infer_supplement_semantics(_u450_h5_ir)
                _u450_h5_jsx = render_jsx_body(_u450_h5_ir, texts_map=_texts_map or None)
                if _u450_h5_jsx.strip():
                    inline_items.append({'y': n.get('y', float('inf')), 'jsx': _u450_h5_jsx})
                _u450_h5_cls = (_u450_h5_ir.get('semantic') or {}).get('className', '')
                if _u450_h5_cls:
                    _u450_h5_css = _css_from_orig(_u450_h5_ir, orig_css_map, css_ext)
                    if _u450_h5_css.strip():
                        inline_css_parts.append(_u450_h5_css)
                    # Hide H5 supplement on desktop
                    inline_css_parts.append(
                        f'@media (min-width: 769px) {{\n  .{_u450_h5_cls} {{\n    display: none;\n  }}\n}}'
                    )

    # 4. 生成 index.tsx（页面入口）+ {PageName}.module.{css_ext}
    page_name = split_plan['pageComponent']
    node_id = split_plan.get('nodeId', '')
    node_id_colon = node_id.replace('-', ':')
    # 优先从原始 TSX 获取 root class（确保与原始一致）
    root_class = (fid_to_class.get(node_id_colon)
                  or _read_root_class(node_id, page_name, css_ext)
                  or _to_kebab(page_name))

    # 按 y 坐标合并 sections 和 inline nodes（视觉从上到下顺序）
    body_items = []
    for i, s_name in enumerate(section_names):
        s_entry = split_plan['sections'][i] if i < len(split_plan['sections']) else {}
        s_ir_root = s_entry.get('ir', {})
        _s_css = s_ir_root.get('css', {})
        _s_css_top = str(_s_css.get('top', '0px')).strip()
        _s_has_own_bottom = bool(
            _s_css.get('bottom') and
            _s_css.get('position') == 'absolute' and
            not re.match(r'[1-9]', _s_css_top)
        )
        body_items.append({
            'y': s_entry.get('y', float('inf')),
            'type': 'section',
            'name': s_name,
            'abs_top':   _section_abs_tops.get(s_name),
            'abs_left':  _section_abs_lefts.get(s_name),
            'abs_width': _section_abs_widths.get(s_name),
            'h5_only': s_ir_root.get('h5Only', False),
            'pc_only': s_ir_root.get('pcOnly', False),
            'has_own_bottom': _s_has_own_bottom,
            'root_css': _s_css,
        })
    for item in inline_items:
        body_items.append({'y': item['y'], 'type': 'inline', 'jsx': item['jsx']})
    body_items.sort(key=lambda x: x['y'])

    (out_dir / INDEX_TSX_FILENAME).write_text(
        _generate_page_tsx_with_order(page_name, body_items, root_class, css_ext,
                                      node_id=node_id_colon,
                                      has_i18n=bool(_i18n_tokens),
                                      page_comp=_page_comp,
                                      sections_needing_theme=_sections_needing_theme,
                                      page_theme=_page_theme,
                                      h5_order=h5_section_order)
    )
    # hooks/usePageEnv.ts：暂存在 out_dir/hooks/，落地时 cmd_apply 会将其提升到
    # target/hooks/（与 components/ 平级），故 TSX 中的 import 路径按最终位置计算。
    hooks_dir = out_dir / HOOKS_SUBDIR
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / USE_PAGE_ENV_FILENAME).write_text(
        _generate_usePageEnv_hook_file(_page_theme)
    )
    # Page.module.{css_ext}：原始根容器 CSS（含 patch）+ inline 节点 CSS
    root_css = _read_root_css_block(node_id, page_name, css_ext, root_class=root_class)
    root_css = _sanitize_page_root_css(root_css, root_class, _page_root_css)
    page_css = (root_css or f'.{root_class} {{\n  position: relative;\n}}\n')
    if inline_css_parts:
        page_css += '\n' + '\n'.join(inline_css_parts)
    # Append responsive CSS for h5Only/pcOnly sections
    import re as _re3
    for _bi in body_items:
        if _bi['type'] != 'section':
            continue
        _h5 = _bi.get('h5_only', False)
        _pc = _bi.get('pc_only', False)
        if not _h5 and not _pc:
            continue
        _n = _bi['name']
        _prefix = 'h5-only' if _h5 else 'pc-only'
        _kebab_n = _re3.sub(r'([A-Z])', r'-\1', _n).lower().lstrip('-')
        _wc = f'{_prefix}-{_kebab_n}'
        if _h5:
            # U-447: if h5-only section root is absolute, wrapper needs position:relative + height
            _h5_abs_rule = _h5only_wrapper_height_rule(_bi.get('root_css') or {})
            page_css += (
                f'\n.{_wc} {{\n  display: none;\n}}\n'
                f'\n@media (max-width: 768px) {{\n  .{_wc} {{\n    display: block;\n    width: 100%;{_h5_abs_rule}\n  }}\n}}\n'
            )
        else:
            page_css += (
                f'\n@media (max-width: 768px) {{\n  .{_wc} {{\n    display: none;\n  }}\n}}\n'
            )
    (out_dir / f'{page_name}.module.{css_ext}').write_text(page_css)

    return {'sectionNames': section_names, 'topLeafNames': top_leaf_names}


def update_root_index(page_dir: Path, page_name: str) -> None:
    """将 index.ts 切换为指向 ./components/Page，原内容以 SPLIT_A_ORIGINAL 单行保存。"""
    index_path = page_dir / INDEX_TS_FILENAME
    new_export = "export { default } from './components/Page'\n"
    if not index_path.exists():
        index_path.write_text(new_export)
        return
    original = index_path.read_text()
    if "'./components/Page'" in original:
        return
    # 将原始内容压缩为单行（\n → \\n），避免破坏 JS 语法
    encoded = original.rstrip('\n').replace('\\', '\\\\').replace('\n', '\\n')
    index_path.write_text(f"{new_export}// SPLIT_A_ORIGINAL: {encoded}\n")


# ── 代码生成辅助 ───────────────────────────────────────────────────────────────

def _inject_figma_id_prop(tsx: str, component_name: str) -> str:
    """Post-process leaf component TSX to make data-figma-id a receivable prop.

    Transforms the first hardcoded data-figma-id on the root element into a
    prop-driven attribute: data-figma-id={dataFigmaId ?? "original-id"}.
    This allows callers to pass different ids per instance.
    """
    # Find the first data-figma-id="xxx" (root element's id)
    _fid_m = _re.search(r'data-figma-id="([^"]+)"', tsx)
    if not _fid_m:
        return tsx
    original_fid = _fid_m.group(1)

    # 1. Add to existing interface OR create one
    iface_pattern = rf'(interface\s+{_re.escape(component_name)}Props\s*\{{)([^}}]*)\}}'
    _iface_m = _re.search(iface_pattern, tsx, flags=_re.DOTALL)
    if _iface_m:
        # Append to existing interface
        tsx = _re.sub(
            iface_pattern,
            lambda m: m.group(1) + m.group(2) + "  'data-figma-id'?: string;\n}",
            tsx, count=1, flags=_re.DOTALL,
        )
        # Add to destructuring: { a, b } → { a, b, 'data-figma-id': dataFigmaId }
        tsx = _re.sub(
            rf'(\{{[^}}]+\}})\s*:\s*{_re.escape(component_name)}Props',
            lambda m: m.group(1).rstrip('}').rstrip()
                      + ", 'data-figma-id': dataFigmaId }: " + component_name + 'Props',
            tsx, count=1, flags=_re.DOTALL,
        )
    else:
        # No interface yet — create one and inject into function signature
        iface_block = (f"\ninterface {component_name}Props {{\n"
                       f"  'data-figma-id'?: string;\n}}\n")
        tsx = _re.sub(
            rf'(export default function\s+{_re.escape(component_name)})\(\)',
            lambda m: iface_block + m.group(1)
                      + "({ 'data-figma-id': dataFigmaId }: " + component_name + 'Props)',
            tsx, count=1,
        )

    # 2. Replace first data-figma-id="original" with dynamic expression
    tsx = tsx.replace(
        f'data-figma-id="{original_fid}"',
        f'data-figma-id={{dataFigmaId ?? "{original_fid}"}}',
        1,
    )
    return tsx


def _generate_leaf_tsx(ir: dict, leaf_name: str, css_ext: str,
                       page_theme: str = 'light',
                       texts_map: dict | None = None,
                       page_comp: str = '',
                       texts_rel_prefix: str = './',
                       hooks_rel_path: str = '../../hooks/usePageEnv',
                       support_row_class_name: bool = False) -> str:
    sem = {**(ir.get('semantic') or {}), 'componentName': leaf_name}
    tsx = generate_tsx({**ir, 'semantic': sem}, css_ext=css_ext, page_theme=page_theme,
                       texts_map=texts_map,
                       component_name_for_texts=page_comp or None,
                       texts_rel_prefix=texts_rel_prefix,
                       inject_page_env=False,
                       hooks_rel_path=hooks_rel_path,
                       support_row_class_name=support_row_class_name)
    return _inject_figma_id_prop(tsx, leaf_name)


def _generate_section_tsx(section_name: str, ir: dict, raw_section_ir: dict,
                           leaf_components: list, leaf_names: list,
                           css_ext: str, node_index: dict,
                           orig_css_map: dict | None = None,
                           fid_to_class: dict | None = None,
                           texts_map: dict | None = None,
                           page_comp: str = '',
                           is_mobile: bool = False,
                           cc_snippets: dict | None = None,
                           page_theme: str = 'light',
                           subsection_map: dict | None = None,
                           leaf_import_prefix: str = './',
                           hooks_rel: str = '../../hooks/usePageEnv',
                           texts_rel: str = '../..') -> tuple:
    # 构建合并候选集：{figmaId → (leaf_idx, global_fid_idx)}，所有叶子的实例一起参与 DFS
    all_candidates: dict = {}
    lc_meta = []  # [(lc_name, vp, instances_data, all_fids_list, root_cls, is_moly)]
    for i, lc in enumerate(leaf_components):
        lc_name = leaf_names[i]
        lc_ir_raw = lc['ir']
        instances_data = lc.get('instancesData', [])
        vp = lc.get('varyingProps', [])
        lc_norm = _get_normalized(lc_ir_raw, node_index)
        root_cls = (lc_norm.get('semantic') or {}).get('className', '')
        all_fids_list = lc.get('allInstanceFigmaIds') or [lc_ir_raw.get('figmaId', '')]
        is_moly = bool(lc.get('ccComponent'))
        lc_meta.append((lc_name, vp, instances_data, all_fids_list, root_cls, is_moly))
        for gidx, fid in enumerate(all_fids_list):
            if fid and fid not in all_candidates:
                all_candidates[fid] = (i, gidx)  # (leaf_idx, global_fid_idx)

    # 单次 DFS：父 leaf 被找到后停止递归，防止其子树内的嵌套 leaf 被误计
    found_map = _find_all_leaf_instances(raw_section_ir, all_candidates)

    # 从 DFS 结果构建 leaf_map
    leaf_map: dict = {}
    for fid, (leaf_idx, global_idx) in found_map.items():
        lc_name, vp, instances_data, all_fids_list, root_cls, is_moly = lc_meta[leaf_idx]
        inst_data = instances_data[global_idx] if global_idx < len(instances_data) else {}
        # Fallback：text prop 为空时，优先用 varyingProps.values[global_idx]，
        # 再从 instance IR 提取实际文本。
        # Fix: instancesData 可能用通用键（text1/text12）而非语义 propName（amount/subAmount），
        # 导致 inst_data.get(propName) 为空，旧逻辑用 _extract_instance_texts()[0]（第一个文本节点）
        # 作为所有缺失 prop 的回退值，致使多个不同 prop 拿到相同的错误值。
        # varyingProps.values[global_idx] 才是该实例该 prop 的权威值。
        if inst_data:
            for _vp in vp:
                if _vp.get('type') == 'text' and not inst_data.get(_vp['propName']):
                    _vp_values = _vp.get('values', [])
                    if global_idx < len(_vp_values) and _vp_values[global_idx]:
                        inst_data = {**inst_data, _vp['propName']: _vp_values[global_idx]}
                    else:
                        _inst_ir = node_index.get(fid)
                        if _inst_ir:
                            _fallback_texts = _extract_instance_texts(_inst_ir)
                            if _fallback_texts:
                                inst_data = {**inst_data, _vp['propName']: _fallback_texts[0]}
        # i18n: 将 text 类型 varyingProp 的值替换为 t() 调用标记
        if texts_map and inst_data:
            _inst_ir = node_index.get(fid)
            if _inst_ir:
                _text_fids = _extract_instance_text_fids(_inst_ir)
                _texts_vals = _extract_instance_texts(_inst_ir)
                for _vp in vp:
                    if _vp.get('type') != 'text':
                        continue
                    _prop_val = inst_data.get(_vp['propName'], '')
                    if not _prop_val:
                        continue
                    # 按值匹配找到对应的 figmaId
                    for _ti, _tv in enumerate(_texts_vals):
                        if _tv == _prop_val and _ti < len(_text_fids):
                            _token = texts_map.get(_text_fids[_ti])
                            if _token:
                                inst_data = {**inst_data,
                                             _vp['propName']: f"__i18n__:{_token}"}
                            break
        # 防止 wrapper 双层背景：若实例的 CSS class 与组件根 class 视觉上完全相同，
        # 将 rootClassName 设为实例的 class，使 tsx_generator 跳过 wrapper div。
        # 但 Code Connect 组件始终需要 wrapper（它不渲染 figma CSS，定位必须由外层提供）。
        effective_root_cls = root_cls
        _variant_cls_for_fid: str | None = None
        if not is_moly and orig_css_map and fid_to_class:
            inst_cls = fid_to_class.get(fid, '')
            if inst_cls and inst_cls != root_cls:
                def _strip_invisible(css: dict) -> dict:
                    """去除 opacity:0 后再比较（该属性已被 _css_from_orig 去除）。"""
                    return {k: v for k, v in css.items()
                            if not (k == 'opacity' and str(v).strip() in ('0', '0.0'))}
                # U-452: When root_cls is a semantic name not present in orig_css_map,
                # orig_css_map.get(root_cls) always returns {} → comparison always fails →
                # effective_root_cls='' → wrapper div generated → double absolute positioning.
                # Fix: when root_cls not in orig_css_map, look up the leaf template's
                # original class via fid_to_class; any instance is treated as template-like
                # (the leaf owns its own positioning, no wrapper needed).
                _lc_tpl_fid = (leaf_components[leaf_idx].get('ir') or {}).get('figmaId', '')
                _lc_tpl_orig_cls = fid_to_class.get(_lc_tpl_fid, '') if fid_to_class else ''
                # U-467: Also enter U-452 block when root_cls IS in orig_css_map but its
                # CSS has a different `position` than the leaf template's original class.
                # This detects semantic name collisions (e.g. root_cls='container' maps to
                # an unrelated node with position:relative, while the leaf template is
                # position:absolute) — the comparison in the else branch would be meaningless.
                _root_cls_is_collision = (
                    _lc_tpl_orig_cls
                    and root_cls in orig_css_map
                    and orig_css_map.get(root_cls, {}).get('position')
                       != orig_css_map.get(_lc_tpl_orig_cls, {}).get('position')
                )
                if _lc_tpl_orig_cls and (_lc_tpl_orig_cls == root_cls
                                          or root_cls not in orig_css_map
                                          or _root_cls_is_collision):
                    # U-452: Leaf template's original class IS the root class (same node),
                    # OR root_cls is a semantic name absent from orig_css_map.
                    # In both cases: leaf IS its own root node → no wrapper needed.
                    # NOTE: inst_cls may differ from root_cls by sub-pixel coords or padding
                    # stripped by U-376, but these are artifacts — the leaf owns all its CSS.
                    # U-456: Exception — a non-template instance with position:absolute needs
                    # a wrapper div. The leaf component strips positioning from its own CSS
                    # (prevents double-abs), so the parent must apply the instance's absolute
                    # positioning via a wrapper div (inst_cls CSS class with position:absolute).
                    # U-458: Also generate a wrapper when inst_cls has non-zero margin
                    # (margin-top/bottom/left/right). Without a wrapper the inst_cls CSS is
                    # generated in index.module.scss but never applied (the leaf uses its own
                    # CSS module), so the margin is silently lost and the component floats to
                    # the wrong position (e.g. a button with margin-top:615px appears at top:0).
                    _inst_css_for_w = orig_css_map.get(inst_cls, {})
                    _inst_has_nontrivial_margin = any(
                        str(_inst_css_for_w.get(p, '0')).strip()
                        not in ('', '0', '0px', '0em', '0rem')
                        for p in ('margin-top', 'margin-bottom',
                                  'margin-left', 'margin-right')
                    )
                    _tpl_position = orig_css_map.get(_lc_tpl_orig_cls, {}).get('position', '')
                    _u456_inst_needs_abs_wrapper = (
                        inst_cls != _lc_tpl_orig_cls
                        and (_inst_css_for_w.get('position') == 'absolute'
                             or _inst_has_nontrivial_margin
                             # U-456-ext: instance is position:relative (flex item) but
                             # template root is position:absolute (inner overlay layer).
                             # Without a wrapper the component's absolute root bleeds out
                             # of the flex row. Real case: BtcDark 311:14974 (48×48
                             # position:relative icon in flex row) vs template .background
                             # (position:absolute; width:100%; height:100%).
                             or (_inst_css_for_w.get('position') == 'relative'
                                 and _tpl_position == 'absolute')
                        )
                    )
                    # U-467: Cancel U-456 when the COORDINATE diff vs the template is
                    # only sub-pixel rounding (< 1px). The leaf already carries
                    # position:absolute at effectively the same coordinates — a wrapper
                    # div would create double absolute positioning and clip the content.
                    # Note: U-376 may have already stripped 'padding'/'width' from the
                    # instance's orig_css_map entry, so we compare only coordinate props
                    # (top/left/right/bottom) to avoid false positives from that stripping.
                    # Real case: TomorrowlandLandingPage4 node 39641:6400 (top:-0.25px)
                    # vs template 39641:6388 (top:0px) → wrapper caused empty white box.
                    if _u456_inst_needs_abs_wrapper and not _inst_has_nontrivial_margin:
                        _tpl_css_u467 = orig_css_map.get(_lc_tpl_orig_cls, {})
                        _coord_props = ('top', 'left', 'right', 'bottom')
                        _coord_meaningful: set = set()
                        for _dk in _coord_props:
                            _rv_raw = _tpl_css_u467.get(_dk, '0')
                            _iv_raw = _inst_css_for_w.get(_dk, '0')
                            if _rv_raw == _iv_raw:
                                continue
                            try:
                                _rv = float(str(_rv_raw).replace('px', '').strip())
                                _iv = float(str(_iv_raw).replace('px', '').strip())
                                if abs(_rv - _iv) < 1.0:
                                    continue
                            except ValueError:
                                pass
                            _coord_meaningful.add(_dk)
                        # Also ensure non-coordinate props (position, left exact value, etc.)
                        # don't indicate a fundamentally different layout
                        _inst_pos = _inst_css_for_w.get('position', '')
                        _tpl_pos = _tpl_css_u467.get('position', '')
                        if (not _coord_meaningful
                                and _inst_pos == _tpl_pos
                                and _inst_css_for_w.get('left') == _tpl_css_u467.get('left')):
                            _u456_inst_needs_abs_wrapper = False
                    if not _u456_inst_needs_abs_wrapper:
                        # U-459: Check if the only CSS diff is visual-only (background/border).
                        # If so, use rowClassName path (no wrapper div, visual style as prop).
                        # Handles zebra-stripe/variant patterns where inst_cls and tpl_orig_cls
                        # differ only in visual appearance (e.g. background-color for even rows).
                        _tpl_css_u459 = orig_css_map.get(_lc_tpl_orig_cls, {})
                        _inst_css_u459 = orig_css_map.get(inst_cls, {})
                        _u459_diff = {
                            k for k in set(_tpl_css_u459) | set(_inst_css_u459)
                            if _tpl_css_u459.get(k) != _inst_css_u459.get(k)
                        }
                        if _u459_diff and _u459_diff.issubset(_VISUAL_ONLY_PROPS):
                            _variant_cls_for_fid = inst_cls
                        else:
                            effective_root_cls = inst_cls
                    # else: stays as root_cls → tsx_generator: inst_cls != root_cls → wrapper
                elif _strip_invisible(orig_css_map.get(inst_cls, {})) == \
                   _strip_invisible(orig_css_map.get(root_cls, {})):
                    effective_root_cls = inst_cls  # same visual CSS → no wrapper needed
                else:
                    # CSS differs — check if the diff is visual-only (background/border/shadow).
                    # Visual-only diff → pass inst_cls as rowClassName prop; no wrapper div.
                    # Layout diffs (padding, flex-*, width, etc.) still need a wrapper.
                    # Fix: Arena ranking-row-2 (6777:33313) zebra-stripe pattern.
                    # Note: root_cls is normalized (hyphens removed by _get_normalized), but
                    # orig_css_map uses original hyphenated keys from plan.json cssMap.
                    # Use a fallback lookup that tries both normalized and hyphenated forms.
                    _r_css = (orig_css_map.get(root_cls) or
                              next((v for k, v in orig_css_map.items()
                                    if k.replace('-', '') == root_cls or
                                    k == root_cls), {}))
                    _i_css = orig_css_map.get(inst_cls, {})
                    _diff_keys = {k for k in set(_r_css) | set(_i_css)
                                  if _r_css.get(k) != _i_css.get(k)}
                    # U-452c: Filter sub-pixel coordinate differences (< 1px, Figma rounding).
                    # e.g., top:0px vs top:-0.25px is imperceptible; don't treat as layout diff.
                    _diff_keys_filtered: set = set()
                    for _dk in _diff_keys:
                        if _dk in ('top', 'left', 'right', 'bottom'):
                            _rv = str(_r_css.get(_dk, ''))
                            _iv = str(_i_css.get(_dk, ''))
                            if _rv and _iv:
                                try:
                                    if abs(float(_rv.replace('px', '').strip()) -
                                           float(_iv.replace('px', '').strip())) < 1.0:
                                        continue  # sub-pixel diff → ignore
                                except ValueError:
                                    pass
                        _diff_keys_filtered.add(_dk)
                    if _diff_keys_filtered and _diff_keys_filtered.issubset(_VISUAL_ONLY_PROPS):
                        # Visual-only diff → merge via rowClassName, no wrapper
                        _variant_cls_for_fid = inst_cls
                        # effective_root_cls stays as root_cls (leaf renders its own root)
                    elif not _diff_keys_filtered or _diff_keys_filtered <= {'width'}:
                        # U-452c: Only sub-pixel coord diffs (filtered) or only missing width
                        # (leaf already defines its own width) → no wrapper needed.
                        effective_root_cls = inst_cls
                    else:
                        effective_root_cls = ''  # CSS differs (position/size) → force wrapper div
            elif inst_cls and inst_cls == root_cls:
                # Template 实例：若组件存在 position/width 冲突（属性被 strip），
                # section CSS 中该 class 保留了原始定位，必须渲染 wrapper div 才能生效。
                lc_obj = leaf_components[leaf_idx]
                _strip = _detect_width_conflict(lc_obj, node_index, fid_to_class, orig_css_map)
                if _strip & _POSITION_PROPS:
                    effective_root_cls = ''  # 强制渲染 wrapper div
        # Button variant 推断：CC snippet 为基础，CSS 视觉特征可修正
        _effective_vp = vp
        if leaf_components[leaf_idx].get('ccComponent') == 'Button':
            _v = None
            # P1: 从 CC snippet 解析 variant
            _snippet = (cc_snippets or {}).get(fid, '')
            if _snippet:
                _vm = re.search(r'variant="([^"]+)"', _snippet)
                if _vm:
                    _v = _vm.group(1)
            # P2: CSS 视觉修正 — 当实际是 outline 样式时覆盖 CC 声明
            _inst_node = node_index.get(fid)
            if _inst_node:
                _bg = (_inst_node.get('css') or {}).get('background-color', '')
                _bd = (_inst_node.get('css') or {}).get('border', '')
                _is_outline = _bd and (not _bg or 'transparent' in _bg)
                if _is_outline and not _v:  # only infer 'outline' when CC had no variant
                    _v = 'outline'
                elif not _v:
                    _v = 'primary'
            if _v:
                inst_data = {**inst_data, 'variant': _v}
                if not any(v.get('propName') == 'variant' for v in _effective_vp):
                    _effective_vp = list(_effective_vp) + [{'propName': 'variant', 'type': 'prop', 'values': []}]
        # 内联判断：count=1 + 无 varyingProps + 是 Code Connect → 直接内联 snippet
        _lc_obj = leaf_components[leaf_idx]
        _can_inline = (
            is_moly
            and _lc_obj.get('ccComponent') not in ('',)
            and len(all_fids_list) == 1
            and not vp
            and _lc_obj.get('snippet')
            and '<' in _lc_obj.get('snippet', '')  # JSX requires '<'; skip Dart/Flutter snippets
        )
        _inline_snippet = None
        if _can_inline:
            _inline_snippet = _lc_obj.get('snippet', '').strip()
            # Python→JS 布尔值修正（JSX 属性 + object 值语法）+ 清理空表达式
            if _inline_snippet:
                _inline_snippet = re.sub(r'\{False\}', '{false}', _inline_snippet)
                _inline_snippet = re.sub(r'\{True\}',  '{true}',  _inline_snippet)
                _inline_snippet = re.sub(r'\{None\}',  '{null}',  _inline_snippet)
                _inline_snippet = _inline_snippet.replace(': True', ': true').replace(': False', ': false')
                _inline_snippet = re.sub(r'\s*\w+=\{\}', '', _inline_snippet)
            # Tabs/Collapse items 用 IR 实际内容替换占位数据
            _inst_ir_for_inline = node_index.get(fid)
            if _inline_snippet and _inst_ir_for_inline:
                _inline_snippet = _patch_items_prop(_inline_snippet, _inst_ir_for_inline)
                # 注入实例文本：优先使用 i18n token，否则硬编码
                _itexts = _extract_instance_texts(_inst_ir_for_inline)
                _itext_fids = _extract_instance_text_fids(_inst_ir_for_inline)
                if _itexts:
                    # 确定注入内容：如果 texts_map 有 token 则用 t() 调用
                    _inject_text = _itexts[0]
                    if texts_map and _itext_fids:
                        _token_id = texts_map.get(_itext_fids[0])
                        if _token_id:
                            _inject_text = "{t('" + _token_id + "')}"
                    # U-470: \s* covers both ></Tag> (zero-whitespace) and >\n  </Tag>
                    if re.search(r'>\s*</', _inline_snippet):
                        _inline_snippet = re.sub(
                            r'>\s*</', f'>{_inject_text}</', _inline_snippet, count=1)
                    else:
                        # U-326: 自闭合 React 组件 + 实例有文本 → 展开并注入（内联路径）
                        # U-471: input-like CC components (Search/Input/Textarea) render
                        # <input> internally — void element, cannot have children (React crash).
                        _INPUT_LIKE_MOLY = {'Search', 'Input', 'Textarea', 'InputNumber'}
                        _sc_m = re.search(r'<([A-Z][A-Za-z0-9]*)\b([^>]*?)\s*/>', _inline_snippet)
                        if (_sc_m and not _sc_m.group(1).startswith('Icon')
                                and _sc_m.group(1) not in _INPUT_LIKE_MOLY):
                            _sc_t = _sc_m.group(1)
                            _sc_a = _sc_m.group(2)
                            _inline_snippet = _inline_snippet.replace(
                                _sc_m.group(0),
                                f'<{_sc_t}{_sc_a}>{_inject_text}</{_sc_t}>'
                            )
                        else:
                            # snippet 有非空 children 文本但与实例文本不同 → 替换
                            _clean_il = re.sub(r'\{/\*.*?\*/\}', '', _inline_snippet, flags=re.DOTALL)
                            _ex = re.search(r'>\s*([^<>{}\n][^<>{}]*?)\s*</', _clean_il)
                            if _ex and _ex.group(1).strip():
                                _inline_snippet = _inline_snippet.replace(
                                    _ex.group(1).strip(), _inject_text, 1)
            # 移动端 Countdown 强制 size="small"
            if (_inline_snippet and _lc_obj.get('ccComponent') == 'Countdown'
                    and is_mobile):
                _inline_snippet = re.sub(r'size="[^"]*"', 'size="small"', _inline_snippet)
            # Icon 补全（文本注入之后）
            if _inline_snippet:
                _inline_snippet = _patch_cc_snippet(_inline_snippet, _lc_obj.get('allImports'),
                                                    instance_ir=_inst_ir_for_inline)
            # Button variant 修正：CSS 视觉特征覆盖 CC snippet 中的 variant
            if _inline_snippet and _lc_obj.get('ccComponent') == 'Button' and _v:
                _inline_snippet = re.sub(r'variant="[^"]*"', f'variant="{_v}"', _inline_snippet)
            # Icon color 注入：当 IR 指定了非默认颜色时，注入 color prop 到内联 snippet
            _inline_icon_color = (_lc_obj.get('ir') or {}).get('iconColor')
            if _inline_snippet and _inline_icon_color and 'color=' not in _inline_snippet:
                _inline_snippet = re.sub(r'\s*/>', f' color="{_inline_icon_color}" />', _inline_snippet, count=1)

        leaf_map[fid] = {
            'componentName': lc_name,
            'varyingProps': _effective_vp,
            'instanceData': inst_data,
            'rootClassName': effective_root_cls,
            'variantCls': _variant_cls_for_fid,
            'isMoly': is_moly,
            'ccComponent': _lc_obj.get('ccComponent', ''),
            'inlineSnippet': _inline_snippet,
            'iconColor': (node_index.get(fid) or {}).get('iconColor', ''),
        }

    jsx_body = render_jsx_body_with_leaf_refs(ir, leaf_map, texts_map=texts_map,
                                              subsection_map=subsection_map)

    # 只 import 实际出现在 JSX 里的叶子组件（部分 leaf 可能因父节点替换而未被渲染）
    # 内联组件不从本地 import，改为从组件库 import（按 module 分组）
    used_leaf_names = {info['componentName'] for info in leaf_map.values()}
    _inlined_names = {info['componentName'] for info in leaf_map.values() if info.get('inlineSnippet')}
    _inline_import_groups: dict = {}  # {module: set(names)}
    for info in leaf_map.values():
        if info.get('inlineSnippet'):
            # 从对应 leaf component 的 allImports 解析正确的模块路径
            _lc_idx = next((i for i, lc in enumerate(leaf_components)
                           if lc.get('ccComponent') == info.get('ccComponent')), -1)
            if _lc_idx >= 0:
                for imp_str in (leaf_components[_lc_idx].get('allImports') or []):
                    _names, _mod = _parse_import_statement(imp_str)
                    if _names and _mod:
                        _inline_import_groups.setdefault(_mod, set()).update(_names)

    import_lines = [
        "import React from 'react';",
        f"import styles from './index.module.{css_ext}';",
    ]
    for _mod, _names in sorted(_inline_import_groups.items()):
        import_lines.append(f"import {{ {', '.join(sorted(_names))} }} from '{_mod}'")
    for lc_name in leaf_names:
        if lc_name in used_leaf_names and lc_name not in _inlined_names:
            import_lines.append(f"import {lc_name} from '{leaf_import_prefix}{lc_name}';")
    # 子 Section import（每个子 Section 作为独立组件引用）
    if subsection_map:
        for ss_name in sorted(set(subsection_map.values())):
            if f'<{ss_name}' in jsx_body:
                import_lines.append(f"import {ss_name} from './{ss_name}';")

    # U-373 补充：扫描 jsx_body 中由 icon_color 路径生成的 <Icon*> 组件，追加 import。
    # 这些 icon 不经过 leaf_map CC，import 不会被 _inline_import_groups 自动追踪。
    # 过滤：只追加 ICON_PACKAGE 中实际存在的 Icon，避免 ReferenceError。
    _already_imported_icons = {
        name for line in import_lines
        for name in re.findall(r'Icon[A-Z][a-zA-Z0-9]*', line)
    }
    _section_icon_comps = set(re.findall(r'<(Icon[A-Z][a-zA-Z0-9]*)', jsx_body))
    _new_icons = _section_icon_comps - _already_imported_icons
    if _new_icons:
        from lib.tsx_generator import _get_valid_icon_comps as _gv_sec, _ICON_PACKAGE as _ICON_PKG_SEC
        _valid_sec = _gv_sec()
        _filtered_new = {ic for ic in _new_icons if _valid_sec and ic in _valid_sec}
        if _filtered_new and _ICON_PKG_SEC:
            import_lines.append(
                f"import {{ {', '.join(sorted(_filtered_new))} }} from '{_ICON_PKG_SEC}';"
            )

    t_import = (
        f"import type {{ PageT }} from '{texts_rel}/{page_comp}.texts';"
    ) if (texts_map and page_comp) else ""

    # Build props: only t (i18n). pageTheme is now obtained via shared hook import.
    needs_page_theme = 'pageTheme' in jsx_body
    props_parts = []
    type_parts = []
    if texts_map:
        props_parts.append('t')
        type_parts.append('t: PageT')

    if needs_page_theme:
        import_lines.append(f"import {{ usePageEnv }} from '{hooks_rel}';")

    if type_parts:
        t_param = "{ " + ", ".join(props_parts) + " }: { " + "; ".join(type_parts) + " }"
    else:
        t_param = ""

    page_env_hook_call = (
        f"  const {{ pageTheme }} = usePageEnv('{page_theme}')"
    ) if needs_page_theme else None

    # Guard: if any subSection import source shares the section_name, the function
    # declaration would clash with the import in Vite/esbuild scope-hoisting.
    # Use a 'Root' suffix so the function name is always distinct from its imports.
    _imported_ss_names = {v for v in (subsection_map or {}).values() if f'<{v}' in jsx_body}
    _fn_name = f'{section_name}Root' if section_name in _imported_ss_names else section_name

    tsx_content = '\n'.join(filter(None, [
        '\n'.join(import_lines),
        t_import,
        '',
        f"export default function {_fn_name}({t_param}) {{",
        page_env_hook_call,
        "  return (",
        jsx_body,
        "  );",
        "}",
        "",
    ]))
    # Always return False: sections no longer signal needs_page_theme to Page.tsx,
    # because they call usePageEnv directly instead of receiving pageTheme as a prop.
    return tsx_content, False


def _generate_page_tsx(page_name: str, body_items: list,
                        root_class: str, css_ext: str,
                        node_id: str = '',
                        has_i18n: bool = False,
                        page_comp: str = '',
                        sections_needing_theme: set | None = None,
                        page_theme: str = 'light',
                        h5_order: list | None = None) -> str:
    """
    body_items: [{'y': float, 'type': 'section'|'inline', 'name': str, 'jsx': str}]
    已按 y 坐标排序，按视觉从上到下顺序渲染 section 引用和 inline 节点。

    h5_order: optional list of section names in H5 display order. When provided
    and the Kendall τ distance > 0.3, a dual-branch component is generated that
    uses useMobileSize() to select PC vs H5 section ordering. h5_only sections
    are excluded from the PC branch; pc_only sections are excluded from the H5 branch.
    """
    # ── Dual-branch detection ────────────────────────────────────────────────
    _H5_ORDER_THRESHOLD = 0.3
    _use_dual_branch = False
    _pc_body_items = body_items
    _h5_body_items: list = []
    if h5_order:
        _pc_sec_names = [item['name'] for item in body_items
                         if item['type'] == 'section' and not item.get('h5_only', False)]
        _h5_common = [n for n in h5_order if n in set(_pc_sec_names)]
        _tau = _kendall_tau_distance(_pc_sec_names, _h5_common)
        if _tau > _H5_ORDER_THRESHOLD:
            _use_dual_branch = True
            # PC branch: exclude h5_only sections
            _pc_body_items = [item for item in body_items if not item.get('h5_only', False)]
            # H5 branch: exclude pc_only sections; sort sections by h5_order; inlines appended last
            _h5_only_names = [item['name'] for item in body_items
                               if item['type'] == 'section' and item.get('h5_only', False)]
            _all_h5_names_ordered = _h5_common + _h5_only_names
            _h5_sec_order_map = {n: i for i, n in enumerate(_all_h5_names_ordered)}
            _h5_non_pc = [item for item in body_items if not item.get('pc_only', False)]
            _h5_secs = sorted(
                [item for item in _h5_non_pc if item['type'] == 'section'],
                key=lambda x: _h5_sec_order_map.get(x.get('name', ''), 999),
            )
            _h5_inlines = [item for item in _h5_non_pc if item['type'] != 'section']
            _h5_body_items = _h5_secs + _h5_inlines

    # ── Section names for imports (union of both branches) ───────────────────
    if _use_dual_branch:
        _all_sec_names_seen: dict = {}  # name → first-seen body_items order
        for item in body_items:
            if item['type'] == 'section':
                _all_sec_names_seen.setdefault(item['name'], len(_all_sec_names_seen))
        section_names = sorted(_all_sec_names_seen, key=lambda n: _all_sec_names_seen[n])
    else:
        section_names = [item['name'] for item in body_items if item['type'] == 'section']

    import_lines = [
        "import React from 'react';",
        f"import styles from './{page_name}.module.{css_ext}';",
        "import { usePageEnv } from './hooks/usePageEnv';",
    ]
    if _use_dual_branch:
        import_lines.append("import { useState, useEffect } from 'react';")
    for s_name in section_names:
        import_lines.append(f"import {s_name} from './components/{s_name}';")

    texts_name = page_comp or page_name
    if has_i18n:
        import_lines += [
            f"import {{ PAGE_TEXTS, I18N_NS, PageTextToken }} from './{texts_name}.texts';",
            f"import {{ TEXT_DEFAULTS }} from './{texts_name}.texts.defaults';",
        ]

    hook_block = ""
    hook_call = ""
    if has_i18n:
        hook_block = (
            "\nfunction usePageTexts("
            "defaults: Partial<Record<PageTextToken, string>> = {}) {\n"
            "  // TODO: replace with: const { t: rawT } = useI18n(I18N_NS)\n"
            "  const t = (tokenId: PageTextToken, "
            "params?: Record<string, unknown>): string => {\n"
            "    const key = PAGE_TEXTS[tokenId]\n"
            "    const fallback = defaults[tokenId] ?? ''\n"
            "    // if (key) return rawT(key, { defaultValue: fallback, ...params })\n"
            "    if (!params) return fallback\n"
            "    return fallback.replace(/{{(\\w+)}}/g, "
            "(_, k) => String(params?.[k] ?? ''))\n"
            "  }\n"
            "  return { t }\n"
            "}\n"
            f"\nexport type PageT = ReturnType<typeof usePageTexts>['t']\n"
        )
        hook_call = "  const { t } = usePageTexts(TEXT_DEFAULTS)"

    # ── Body-building helper (reused for PC and H5 branches) ─────────────────
    def _build_body(items: list, indent: str = '      ') -> str:
        lines: list = []
        extra = '  '  # extra indent for children inside wrappers
        for item in items:
            if item['type'] == 'section':
                attrs = " t={t}" if has_i18n else ""
                abs_top = item.get('abs_top')
                if abs_top is not None and abs_top > 0 and not item.get('has_own_bottom'):
                    abs_left  = item.get('abs_left')
                    abs_width = item.get('abs_width')
                    top_str   = f'{abs_top:.4g}px'
                    left_val  = f"'{abs_left:.4g}px'" if abs_left else '0'
                    width_val = f"'{abs_width:.4g}px'" if abs_width else "'100%'"
                    lines.append(
                        f"{indent}<div style={{{{ position: 'absolute', top: '{top_str}', left: {left_val}, width: {width_val} }}}}>"
                    )
                    lines.append(f"{indent}{extra}<{item['name']}{attrs} />")
                    lines.append(f"{indent}</div>")
                else:
                    _flow_abs_left = item.get('abs_left')
                    h5_only = item.get('h5_only', False)
                    pc_only = item.get('pc_only', False)
                    if h5_only or pc_only:
                        import re as _re2
                        _prefix = 'h5-only' if h5_only else 'pc-only'
                        _kebab_name = _re2.sub(r'([A-Z])', r'-\1', item['name']).lower().lstrip('-')
                        _wrapper_cls = f'{_prefix}-{_kebab_name}'
                        lines.append(f"{indent}<div className={{styles['{_wrapper_cls}']}}>")
                        lines.append(f"{indent}{extra}<{item['name']}{attrs} />")
                        lines.append(f"{indent}</div>")
                    elif _flow_abs_left and float(_flow_abs_left) > 0:
                        _ml = f'{float(_flow_abs_left):.4g}px'
                        lines.append(f"{indent}<div style={{{{ marginLeft: '{_ml}' }}}}>" )
                        lines.append(f"{indent}{extra}<{item['name']}{attrs} />")
                        lines.append(f"{indent}</div>")
                    else:
                        lines.append(f"{indent}<{item['name']}{attrs} />")
            else:
                for line in item['jsx'].splitlines():
                    lines.append(f"{indent}{line}")
        return '\n'.join(lines)

    body = _build_body(_pc_body_items if _use_dual_branch else body_items)

    # U-373 补充：扫描 inline body 中由 icon_color 路径生成的 <Icon*> 组件，追加 import。
    # _generate_page_tsx 手动构建 import_lines，不走 generate_tsx，需要单独追踪。
    # 过滤：只追加 ICON_PACKAGE 中实际存在的 Icon，避免 ReferenceError。
    _all_body_text = body
    if _use_dual_branch:
        _all_body_text += '\n' + _build_body(_h5_body_items)
    _page_icon_comps = set(re.findall(r'<(Icon[A-Z][a-zA-Z0-9]*)', _all_body_text))
    if _page_icon_comps:
        from lib.tsx_generator import _get_valid_icon_comps as _get_valid_pg, _ICON_PACKAGE as _ICON_PKG_PG
        _valid_pg = _get_valid_pg()
        _filtered_pg = {ic for ic in _page_icon_comps if _valid_pg and ic in _valid_pg}
        if _filtered_pg and _ICON_PKG_PG:
            import_lines.append(
                f"import {{ {', '.join(sorted(_filtered_pg))} }} from '{_ICON_PKG_PG}';"
            )

    page_env_call = f"  const {{ pageTheme }} = usePageEnv('{page_theme}')"

    figma_id_attr = f' data-figma-id="{node_id}"' if node_id else ''

    _use_is_mobile_fn = (
        "\nfunction useIsMobile() {\n"
        "  const [m, setM] = useState(() => typeof window !== 'undefined' && window.innerWidth < 768);\n"
        "  useEffect(() => {\n"
        "    const mq = window.matchMedia('(max-width: 767px)');\n"
        "    setM(mq.matches);\n"
        "    const h = (e: MediaQueryListEvent) => setM(e.matches);\n"
        "    mq.addEventListener('change', h);\n"
        "    return () => mq.removeEventListener('change', h);\n"
        "  }, []);\n"
        "  return m;\n"
        "}\n"
    )

    if _use_dual_branch:
        h5_body = _build_body(_h5_body_items, indent='        ')
        return '\n'.join(filter(None, [
            '\n'.join(import_lines),
            _use_is_mobile_fn,
            hook_block,
            f"export default function {page_name}Page() {{",
            hook_call,
            page_env_call,
            "  const isMobile = useIsMobile();",
            "  if (isMobile) {",
            "    return (",
            f"      <div className={{styles['{root_class}']}}{figma_id_attr}>",
            h5_body,
            "      </div>",
            "    );",
            "  }",
            "  return (",
            f"    <div className={{styles['{root_class}']}}{figma_id_attr}>",
            body,
            "    </div>",
            "  );",
            "}",
            "",
        ]))

    return '\n'.join(filter(None, [
        '\n'.join(import_lines),
        hook_block,
        f"export default function {page_name}Page() {{",
        hook_call,
        page_env_call,
        "  return (",
        f"    <div className={{styles['{root_class}']}}{figma_id_attr}>",
        body,
        "    </div>",
        "  );",
        "}",
        "",
    ]))


def _generate_usePageEnv_hook_file(page_theme: str = 'dark') -> str:
    """Generate the content of hooks/usePageEnv.ts for a split page.

    Each split page writes exactly one copy of this file; all components in the
    page import from it so the hook body is maintained in a single place.
    """
    return (
        "import { useEffect, useState } from 'react';\n"
        "\n"
        "export function usePageEnv(theme: 'dark' | 'light' = 'dark') {\n"
        "  const [pageTheme, setPageTheme] = useState<'dark' | 'light'>(theme)\n"
        "  useEffect(() => {\n"
        "    const html = document.documentElement\n"
        "    const app = document.getElementById('app')\n"
        "    const prevTheme = html.getAttribute('data-theme')\n"
        "    const prevMinWidth = app?.style.minWidth\n"
        "    html.setAttribute('data-theme', theme)\n"
        "    setPageTheme(theme)\n"
        "    if (app) {\n"
        "      app.style.minWidth = 'unset'\n"
        "      // setProperty with 'important' priority to override globals.less\n"
        "      // '#app { overflow-x: scroll !important }' which prevents plain\n"
        "      // inline-style overrides and forces overflow-y to auto.\n"
        "      app.style.setProperty('overflow-x', 'visible', 'important')\n"
        "      app.style.setProperty('overflow-y', 'visible', 'important')\n"
        "    }\n"
        "    return () => {\n"
        "      if (prevTheme !== null) html.setAttribute('data-theme', prevTheme)\n"
        "      else html.removeAttribute('data-theme')\n"
        "      if (app) {\n"
        "        app.style.minWidth = prevMinWidth ?? ''\n"
        "        app.style.removeProperty('overflow-x')\n"
        "        app.style.removeProperty('overflow-y')\n"
        "      }\n"
        "    }\n"
        "  }, [theme])\n"
        "  return { pageTheme }\n"
        "}\n"
    )


def _parse_import_statement(import_str: str) -> tuple:
    """解析 import 语句字符串，返回 (named_imports_list, module_path)。
    例如: 'import { Button } from "your-component-lib";' → (['Button'], 'your-component-lib')
    """
    m = re.match(r'import\s*\{([^}]+)\}\s*from\s*["\']([^"\']+)["\']', import_str)
    if m:
        names = [n.strip() for n in m.group(1).split(',') if n.strip()]
        return names, m.group(2)
    return [], import_str


def _extract_instance_texts(ir_node: dict) -> list:
    """递归提取 IR 节点中所有 text 内容。"""
    texts = []
    if ir_node.get('isTextNode'):
        tc = ir_node.get('textContent', '')
        if tc:
            texts.append(tc)
    for c in (ir_node.get('children') or []):
        texts.extend(_extract_instance_texts(c))
    return texts


def _extract_instance_text_fids(ir_node: dict) -> list:
    """递归提取 IR 节点中所有文本节点的 figmaId（与 _extract_instance_texts 顺序一致）。"""
    fids = []
    if ir_node.get('isTextNode'):
        tc = ir_node.get('textContent', '')
        if tc:
            fids.append(ir_node.get('figmaId', ''))
    for c in (ir_node.get('children') or []):
        fids.extend(_extract_instance_text_fids(c))
    return fids


_CC_PLACEHOLDER_TEXTS = ('Tab 1', 'Tab 2', 'Tab 3', 'Tab 4', 'Tab 5',
                         'Label', 'Question title', 'Answer content',
                         'Button', 'Register Now')


def _patch_items_prop(snippet: str, ir_node: dict) -> str:
    """Replace items=[...] in Tabs/Collapse CC snippets with actual content from IR.

    For Tabs: extract tab label texts from child instances.
    For Collapse: extract Q/A from each child node (first text=question, second=answer).
    """
    if 'items={[' not in snippet:
        return snippet

    is_tabs = snippet.strip().startswith('<Tabs')
    is_collapse = snippet.strip().startswith('<Collapse')

    if is_tabs:
        texts = _extract_instance_texts(ir_node)
        if texts:
            items_str = ', '.join(
                f'{{ key: "{i+1}", label: "{t}" }}' for i, t in enumerate(texts)
            )
            new_items = f'items={{[{items_str}]}}'
            snippet = re.sub(r'items=\{[\s\S]*?\]}', new_items, snippet)
            # 默认选中第一个 tab
            if 'defaultValue' not in snippet:
                snippet = re.sub(r'(/?>)', r' defaultValue="1"\1', snippet, count=1)
    elif is_collapse:
        children = ir_node.get('children') or []
        items = []
        for i, child in enumerate(children):
            child_texts = _extract_instance_texts(child)
            if child_texts:
                q = child_texts[0].replace('"', '\\"')
                a = child_texts[1].replace('"', '\\"') if len(child_texts) > 1 else ''
                items.append(f'{{ key: "{i+1}", label: "{q}", children: "{a}" }}')
        if items:
            items_str = ',\n        '.join(items)
            new_items = f'items={{[\n        {items_str},\n    ]}}'
            snippet = re.sub(r'items=\{[\s\S]*?\]}', new_items, snippet)

    return snippet


def _write_moly_flat(parent_dir: Path, name: str, moly_name: str,
                      cc_import: str, css_ext: str,
                      all_imports: list | None = None,
                      snippet: str | None = None,
                      varying_props: list | None = None,
                      instance_ir: dict | None = None) -> None:
    """生成 CC 组件包装文件。
    如果有 snippet + allImports（来自 Code Connect），使用完整的 snippet 作为组件体；
    否则退化为空壳 <Component {...props} />。
    varying_props 中 type='text' 的项会将 snippet 中的静态文本替换为动态 prop。
    instance_ir: 实例 IR 节点，用于提取文本替换 CC snippet 中的占位文本。
    """
    # Python→JS 布尔值修正（CC 配置可能用 Python 语法）
    if snippet:
        snippet = snippet.replace(': True', ': true').replace(': False', ': false')
    # CC items prop 替换：Tabs/Collapse 的 items 数组用 IR 实际内容重建
    if snippet and instance_ir:
        snippet = _patch_items_prop(snippet, instance_ir)

    # CC 占位文本替换：用实例 IR 的实际文本替换 snippet 中的占位符
    if snippet and instance_ir:
        inst_texts = _extract_instance_texts(instance_ir)
        if inst_texts:
            replaced = False
            text_idx = 0
            # 1. 替换已知占位文本（精确匹配：必须是 >"placeholder"< 或 >placeholder< 形式）
            for placeholder in _CC_PLACEHOLDER_TEXTS:
                has_quoted = f'"{placeholder}"' in snippet
                has_child = f'>{placeholder}<' in snippet
                if (has_quoted or has_child) and text_idx < len(inst_texts):
                    if has_quoted:
                        snippet = snippet.replace(f'"{placeholder}"', f'"{inst_texts[text_idx]}"', 1)
                    if has_child:
                        snippet = snippet.replace(f'>{placeholder}<', f'>{inst_texts[text_idx]}<', 1)
                    text_idx += 1
                    replaced = True
            # 2. 标签 children 为空白时，注入实例文本
            if not replaced:
                # U-469: \s* covers both ></Tag> (zero-whitespace) and >\n  </Tag>
                empty_children = re.search(r'>\s*</', snippet)
                if empty_children and inst_texts:
                    snippet = re.sub(r'>\s*</', f'>\n        {inst_texts[0]}\n      </', snippet, count=1)
                    replaced = True
            # U-326: 自闭合 React 组件 + 实例有文本 → 展开为有 children 的标签
            # 处理 CC snippet 如 <Button variant="primary" size="small" />，
            # 当实例 IR 有文本节点（按钮 label）时展开并注入。
            # Icon* 等无 children 组件排除在外。
            if not replaced and inst_texts:
                _sc_match = re.search(r'<([A-Z][A-Za-z0-9]*)\b([^>]*?)\s*/>', snippet)
                if _sc_match and not _sc_match.group(1).startswith('Icon'):
                    _sc_tag = _sc_match.group(1)
                    _sc_attrs = _sc_match.group(2)
                    snippet = snippet.replace(
                        _sc_match.group(0),
                        f'<{_sc_tag}{_sc_attrs}>\n        {inst_texts[0]}\n      </{_sc_tag}>'
                    )
                    replaced = True
            # 3. snippet 有非空 children 文本但与实例文本不同 → 替换为实例文本
            if not replaced and inst_texts:
                # 去掉 JSX 注释后匹配纯文本
                _clean = re.sub(r'\{/\*.*?\*/\}', '', snippet, flags=re.DOTALL)
                _existing_text = re.search(r'>\s*([^<>{}\n][^<>{}]*?)\s*</', _clean)
                if _existing_text:
                    _old = _existing_text.group(1).strip()
                    if _old and _old != inst_texts[0]:
                        snippet = snippet.replace(_old, inst_texts[0], 1)

    # 将 varyingProps 注入到 snippet
    if snippet and varying_props:
        for vp in varying_props:
            prop_name = vp.get('propName', '')
            default_val = (vp.get('values') or [''])[0]
            if not prop_name or prop_name[0].isdigit():
                continue
            if vp.get('type') == 'text':
                if default_val and default_val in snippet:
                    # 如果文本在引号内（items 数组场景），连引号一起替换为变量引用
                    _quoted = f'"{default_val}"'
                    if _quoted in snippet:
                        snippet = snippet.replace(_quoted, prop_name, 1)
                    else:
                        snippet = snippet.replace(default_val, '{' + prop_name + '}', 1)
                elif default_val:
                    # default_val 不在 snippet 中（CC snippet 文本与实例文本不同）
                    # 尝试替换标签 children 中的非空文本为 prop 变量
                    _child_text_match = re.search(r'>\s*([^<>{}\s][^<>{}]*?)\s*</', snippet)
                    if _child_text_match:
                        _old_text = _child_text_match.group(1).strip()
                        snippet = snippet.replace(_old_text, '{' + prop_name + '}', 1)
            elif vp.get('type') == 'prop':
                # JSX prop 替换：variant="primary" → variant={variant}
                pattern = f'{prop_name}="{default_val}"'
                if pattern in snippet:
                    snippet = snippet.replace(pattern, f'{prop_name}={{{prop_name}}}', 1)
    # Icon 补全：检测 allImports 中声明但未出现在 snippet 中的 Icon，注入到 children 末尾
    # 放在文本/varyingProps 替换之后，避免破坏 regex 匹配
    if snippet:
        snippet = _patch_cc_snippet(snippet, all_imports, instance_ir=instance_ir)

    # 解析所有 import 语句，按 module 分组
    import_groups: dict = {}  # {module: set(names)}
    if all_imports:
        for imp_str in all_imports:
            names, mod = _parse_import_statement(imp_str)
            if names and mod:
                import_groups.setdefault(mod, set()).update(names)
    else:
        # 退化：从 ccImport 解析
        names, mod = _parse_import_statement(cc_import)
        if names and mod:
            import_groups[mod] = set(names)
        elif moly_name:
            # No import path available — skip rather than hardcoding an internal package.
            # The component will render as a bare reference; the user should add the
            # correct import for their own component library.
            pass

    # 确定组件体（必须在渲染 import_lines 之前，因为 fallback 可能更新 import_groups）
    if snippet and snippet.strip():
        jsx_body = snippet.strip()
    else:
        # Fallback when no JSX snippet available (e.g. Dart/Flutter CC snippet was filtered out).
        _imported_names = {n for _names in import_groups.values() for n in _names}
        if moly_name and moly_name not in _imported_names:
            # moly_name not found in any parsed web import — likely a mobile-only Dart/Flutter
            # component with no web equivalent. Render null to avoid importing a non-existent symbol.
            jsx_body = "null"
        else:
            # Component IS declared in web imports — use bare tag; {...rest} spread injected below.
            jsx_body = f"<{moly_name} />"

    # 渲染 import 行（在 jsx_body / fallback 更新 import_groups 之后进行，确保 moly_name 已加入）
    import_lines = []
    alias_map: dict = {}  # {original_name: aliased_name}
    for mod, names in import_groups.items():
        sorted_names = sorted(names)
        parts = []
        for n in sorted_names:
            if n == name:
                alias = f'_{n}'
                alias_map[n] = alias
                parts.append(f'{n} as {alias}')
            else:
                parts.append(n)
        import_lines.append(f"import {{ {', '.join(parts)} }} from '{mod}'")

    # 清理空表达式 attr={} → 删除（空表达式是非法 JSX）
    jsx_body = re.sub(r'\s*\w+=\{\}', '', jsx_body)

    # 如果 snippet 中用到了别名替换的名字，更新 jsx_body
    for orig, aliased in alias_map.items():
        jsx_body = jsx_body.replace(f'<{orig}', f'<{aliased}')
        jsx_body = jsx_body.replace(f'</{orig}>', f'</{aliased}>')

    # 确定函数签名和 props
    has_snippet = bool(snippet and snippet.strip())
    snippet_props = _extract_props_from_snippet(snippet) if has_snippet else {}

    # 纯 Icon 组件（snippet 仅为 <IconXxx />，无 props）→ 直接 re-export，不包装函数
    # 例外：如果 IR 中有 iconColor（Figma 设计稿中指定了非默认色），需要注入 color prop
    _icon_color = (instance_ir or {}).get('iconColor')
    _is_pure_icon = (has_snippet and not snippet_props and
                     re.match(r'^<\w+\s*/\s*>$', jsx_body.strip()))
    if _is_pure_icon and len(import_groups) == 1 and not _icon_color:
        mod = next(iter(import_groups))
        tsx_parts = []
        tsx_parts.append(f"export {{ {moly_name} as default }} from '{mod}'")
        tsx_parts.append('')
        (parent_dir / f'{name}.tsx').write_text('\n'.join(tsx_parts))
        (parent_dir / f'{name}.module.{css_ext}').write_text(
            f'/* {name} 使用组件库内部样式 */\n'
        )
        return
    # 纯 Icon + iconColor：生成带 color prop 的包装
    if _is_pure_icon and _icon_color:
        mod = next(iter(import_groups))
        tsx_parts = []
        tsx_parts.append(f"import {{ {moly_name} }} from '{mod}'")
        tsx_parts.append('')
        tsx_parts.append(f"export default function {name}(props: {{ [key: string]: any }}) {{")
        tsx_parts.append(f'  return <{moly_name} color="{_icon_color}" {{...props}} />')
        tsx_parts.append('}')
        tsx_parts.append('')
        (parent_dir / f'{name}.tsx').write_text('\n'.join(tsx_parts))
        (parent_dir / f'{name}.module.{css_ext}').write_text(
            f'/* {name} 使用组件库内部样式 */\n'
        )
        return

    # 所有 CC 组件：注入 {...rest} 透传 data-figma-id 等属性
    # className 组件（Button/Tag）额外注入 className prop
    _is_button = (moly_name in _MOLY_CLASSNAME_COMPONENTS)
    if jsx_body.strip().startswith('<'):
        _first_close = re.search(r'(/?>)', jsx_body)
        if _first_close:
            _insert_pos = _first_close.start()
            _inject = ' className={className} {...rest}' if _is_button else ' {...rest}'
            jsx_body = jsx_body[:_insert_pos] + _inject + jsx_body[_insert_pos:]

    tsx_parts = []
    tsx_parts.append('\n'.join(import_lines))
    tsx_parts.append('')

    if has_snippet and not snippet_props:
        if _is_button:
            tsx_parts.append(f"export default function {name}({{ className, ...rest }}: {{ className?: string; [key: string]: any }}) {{")
        else:
            tsx_parts.append(f"export default function {name}({{ ...rest }}: {{ [key: string]: any }}) {{")
    elif has_snippet and snippet_props:
        props_lines = [f"  {pn}?: string" for pn in snippet_props]
        if _is_button:
            props_lines.append("  className?: string")
        props_lines.append("  [key: string]: any")
        tsx_parts.append(f"interface {name}Props {{")
        tsx_parts.extend(props_lines)
        tsx_parts.append('}')
        tsx_parts.append('')
        params_list = list(snippet_props.keys())
        if _is_button:
            params_list.append('className')
        params = ', '.join(params_list) + ', ...rest'
        tsx_parts.append(f"export default function {name}({{ {params} }}: {name}Props) {{")
    else:
        # 退化：无 snippet，透传 props 给 CC 组件
        tsx_parts.append(f"interface {name}Props {{")
        if _is_button:
            tsx_parts.append(f"  className?: string")
        tsx_parts.append(f"  [key: string]: unknown")
        tsx_parts.append('}')
        tsx_parts.append('')
        if _is_button:
            tsx_parts.append(f"export default function {name}({{ className, ...rest }}: {name}Props) {{")
        else:
            tsx_parts.append(f"export default function {name}({{ ...rest }}: {name}Props) {{")
    tsx_parts.append('  return (')
    for line in jsx_body.splitlines():
        tsx_parts.append(f'    {line}')
    tsx_parts.append('  )')
    tsx_parts.append('}')
    tsx_parts.append('')

    (parent_dir / f'{name}.tsx').write_text('\n'.join(tsx_parts))
    (parent_dir / f'{name}.module.{css_ext}').write_text(
        f'/* {name} 使用组件库内部样式 */\n'
    )


def _patch_cc_snippet(snippet: str | None, all_imports: list | None,
                      instance_ir: dict | None = None) -> str | None:
    """补全 CC snippet：检测 allImports 中声明但未出现在 snippet 中的 Icon* 组件，
    根据 IR 中 icon/text 顺序决定注入位置。返回补全后的 snippet（或原样返回）。"""
    if not snippet or not all_imports:
        return snippet
    missing_icons = []
    for imp_str in all_imports:
        m = re.match(r'import\s*\{([^}]+)\}', imp_str)
        if not m:
            continue
        for name in m.group(1).split(','):
            name = name.strip()
            if name.startswith('Icon') and name not in snippet:
                missing_icons.append(name)
    if not missing_icons:
        return snippet
    icon_jsx = '\n'.join(f'      <{ic} />' for ic in missing_icons)
    if snippet.rstrip().endswith('/>'):
        return snippet
    # 判断 icon 在 text 前还是后（从 IR 子节点顺序推断）
    _icon_before_text = False
    if instance_ir:
        _children = instance_ir.get('children') or []
        _first_text_idx = -1
        _first_icon_idx = -1
        for _ci, _ch in enumerate(_children):
            if _ch.get('isTextNode') and _first_text_idx < 0:
                _first_text_idx = _ci
            if (_ch.get('isVectorNode') or _ch.get('isImageNode')) and _first_icon_idx < 0:
                _first_icon_idx = _ci
        if _first_icon_idx >= 0 and _first_text_idx >= 0:
            _icon_before_text = _first_icon_idx < _first_text_idx
    if _icon_before_text:
        # icon 在 text 前：插入到开标签后
        first_close = snippet.find('>')
        if first_close != -1:
            return snippet[:first_close + 1] + '\n' + icon_jsx + snippet[first_close + 1:]
    else:
        # icon 在 text 后（默认）：插入到闭标签前
        close_tag = re.search(r'\s*</\w+>\s*$', snippet)
        if close_tag:
            pos = close_tag.start()
            return snippet[:pos] + '\n' + icon_jsx + snippet[pos:]
    return snippet


def _extract_props_from_snippet(snippet: str | None) -> dict:
    """从 snippet JSX 中提取动态 prop 占位符 {propName} 和 items 中的裸变量引用。
    返回 {propName: defaultValue}。
    """
    if not snippet:
        return {}
    props = {}
    # 1. 匹配 {variableName} 占位符（JSX 文本变量）
    for m in re.finditer(r'\{(\w+)\}', snippet):
        name = m.group(1)
        if (name not in ('true', 'false', 'null', 'undefined') and
                not name[0].isupper() and not name[0].isdigit()):
            props[name] = ''
    # 2. 匹配 items 数组中的裸变量引用（如 label: varName, children: varName）
    for m in re.finditer(r'(?:label|children|title):\s*([a-z]\w*)', snippet):
        name = m.group(1)
        if name not in ('true', 'false', 'null', 'undefined', 'none'):
            props[name] = ''
    return props


_FIGMA_ID_PATTERN = re.compile(r'_[iI]?\d[\d_:-]+$')


def _enhance_token_id(token: dict) -> str | None:
    """Attempt to produce a more semantic token ID. Returns enhanced ID or None (keep original)."""
    text = token.get('default', '')
    current = token.get('tokenId', '')

    # Rule 1: short English text (≤5 words, ≤40 chars) → snake_case of text
    words = re.findall(r'[a-zA-Z]+', text)
    if words and len(words) <= 5 and len(text) <= 40:
        enhanced = '_'.join(w.lower() for w in words)
        if enhanced and enhanced != current:
            return enhanced

    # Rule 2: current ID has trailing Figma ID pattern → strip it
    if _FIGMA_ID_PATTERN.search(current):
        stripped = _FIGMA_ID_PATTERN.sub('', current).rstrip('_')
        if stripped and len(stripped) >= 3:
            return stripped

    return None


def _to_kebab(s: str) -> str:
    s = re.sub(r'([a-z])([A-Z])', r'\1-\2', s)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1-\2', s)
    s = re.sub(r'([a-zA-Z])([0-9])', r'\1-\2', s)
    return s.lower().strip('-').replace(' ', '-')


def _to_const(name: str) -> str:
    """'CardTabAdvanced' → 'CARD_TAB_ADVANCED_DATA'"""
    return _to_kebab(name).replace('-', '_').upper() + '_DATA'
