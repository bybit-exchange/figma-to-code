from __future__ import annotations
"""
tsx_generator.py — TSX React component text generation from an IR tree.


IR shape (Python dict):
  {
    "figmaId":            str,
    "semantic": {
      "htmlTag":          str,
      "className":        str,
      "componentName":    str | None,
      "props":            list[{"name": str, "type": str, "replaces": str}],
    },
    "css":                dict,
    "isDecorativeElement": bool,
    "isVectorNode":        bool,
    "isImageNode":         bool,
    "isTextNode":          bool,
    "textContent":         str | None,
    "textSegments":        list[{"text": str, "color": str | None}] | None,
    "localAssetPath":      str | None,
    "children":            list[dict],
  }
"""

import json
import os
import re
from lib.paths import to_pascal as _to_pascal


# ── 图片合成整帧导出开关 ────────────────────────────────────────────────────────
# True = 检测到纯装饰性图片合成子树时，输出单个 <img> 而非递归子节点
# False = 保持原有逐节点渲染行为
FLATTEN_IMAGE_COMPOSITIONS = False

_FLATTEN_DECO_TYPES = frozenset({
    'LINE', 'ELLIPSE', 'VECTOR', 'RECTANGLE',
    'POLYGON', 'STAR', 'BOOLEAN_OPERATION',
})

# 图标包名称，通过 ICON_PACKAGE 环境变量配置（如 @your-org/icons）。
# 未配置时图标 import 不生成。配置后，通过 Code Connect 标注的图标组件才会追加 import。
_ICON_PACKAGE: str = os.environ.get('ICON_PACKAGE', '')

# U-373 校验缓存：从项目 node_modules 读取图标包（ICON_PACKAGE）的合法 Icon 名称集。
# None = 未初始化；set() = 未配置/读取失败；set(names) = 已加载
_VALID_ICON_COMPS: set | None = None

def _get_valid_icon_comps() -> set:
    """检测项目中是否安装了图标包（ICON_PACKAGE），返回合法的图标组件名称集合。
    未配置 ICON_PACKAGE 或读取失败时返回空集合——不生成任何图标 import。"""
    global _VALID_ICON_COMPS
    if _VALID_ICON_COMPS is not None:
        return _VALID_ICON_COMPS
    if not _ICON_PACKAGE:
        _VALID_ICON_COMPS = set()
        return _VALID_ICON_COMPS
    import subprocess, json as _json
    try:
        pkg = _ICON_PACKAGE.replace("'", "\\'")
        res = subprocess.run(
            ['node', '-e',
             f"try{{const m=require('{pkg}');"
             "console.log(JSON.stringify(Object.keys(m).filter(k=>k.startsWith('Icon'))))}}catch(e){}"],
            capture_output=True, text=True, timeout=8,
        )
        if res.returncode == 0 and res.stdout.strip():
            _VALID_ICON_COMPS = set(_json.loads(res.stdout.strip()))
            return _VALID_ICON_COMPS
    except Exception:
        pass
    _VALID_ICON_COMPS = set()
    return _VALID_ICON_COMPS


def _is_deco_subtree(node: dict) -> bool:
    """递归判断节点子树是否为纯装饰性（无文本、内部全是图片/向量/装饰元素）。
    对 isComponentInstance 和 FRAME 容器都穿透检查 children。
    空子节点列表的非叶节点（如空 div）也视为装饰性。"""
    if node.get('isTextNode'):
        return False
    if node.get('isImageNode') or node.get('isVectorNode'):
        return True
    if node.get('figmaType', '') in _FLATTEN_DECO_TYPES:
        return True
    for c in (node.get('children') or []):
        if not _is_deco_subtree(c):
            return False
    return True


def _is_image_composition(node: dict) -> bool:
    """检测一个节点是否为"纯装饰性图片合成"——适合整帧导出为单张图片。

    判定标准（基于语义，非任意阈值）：
    1. 至少有 2 个直接子节点使用 position:absolute（层叠重叠 = 合成）
    2. 所有直接子节点的子树都是纯装饰性的（递归穿透 instance/FRAME）
    3. 父节点自身不是流式布局容器（不是 flex row/column with gap）

    设计原理：
    - "图片合成"的本质是：多个视觉层叠加在一起形成一个画面
    - 单个 img 容器不是合成（只有1层）
    - flex 排列的图片列表不是合成（不重叠）
    - 2+ 绝对定位的纯装饰层 = 合成
    """
    children = node.get('children') or []
    if len(children) < 2:
        return False

    # 检查父节点是否为流式布局（flex + gap 的列表型容器，不是合成）
    css = node.get('css') or {}
    if css.get('display') == 'flex' and css.get('gap'):
        return False

    absolute_deco_count = 0

    for child in children:
        # 子节点必须全部是装饰性的
        if not _is_deco_subtree(child):
            return False
        # 统计绝对定位的装饰子节点
        child_css = child.get('css') or {}
        if child_css.get('position') == 'absolute':
            absolute_deco_count += 1

    # 至少 2 个绝对定位层才构成"合成"（层叠重叠）
    return absolute_deco_count >= 2


def _bg_luminance(hex_str: str) -> float | None:
    """计算 hex 颜色的相对亮度 (0~1)。"""
    m = re.match(r'#([0-9a-fA-F]{3,6})', hex_str)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    r = int(h[0:2], 16) / 255
    g = int(h[2:4], 16) / 255
    b = int(h[4:6], 16) / 255
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _count_themed_bg_nodes(node: dict) -> tuple[int, int]:
    """全树递归统计 dark/light 背景节点数量（亮度投票）。

    使用 _rawBgHex 亮度投票：当节点背景色来自 CSS 变量（design token）且有 _rawBgHex
    字段时，根据亮度判断属于 dark（lum < 0.3）或 light 背景。
    无 _rawBgHex 的节点跳过，不计入投票。
    """
    dark = 0
    light = 0
    css = node.get("css") or {}
    bg = css.get("background-color", "")
    if "var(--" in bg:
        raw_hex = css.get("_rawBgHex", "")
        lum = _bg_luminance(raw_hex) if raw_hex else None
        if lum is not None:
            if lum < 0.3:
                dark += 1
            else:
                light += 1
    for child in (node.get("children") or []):
        cd, cl = _count_themed_bg_nodes(child)
        dark += cd
        light += cl
    return dark, light


def _collect_large_text_nodes(node: dict, min_px: int = 20) -> list:
    """递归收集 font-size >= min_px 的 TEXT 节点。"""
    results = []
    if node.get("isTextNode") or node.get("figmaType") == "TEXT":
        fs_str = (node.get("css") or {}).get("font-size", "")
        m = re.match(r'(\d+)', fs_str)
        if m and int(m.group(1)) >= min_px:
            results.append(node)
    for child in (node.get("children") or []):
        results.extend(_collect_large_text_nodes(child, min_px))
    return results


def detect_page_theme(ir: dict) -> str:
    """根据根节点背景色亮度判断页面主题，design token / 大标题文字颜色统计辅助。

    策略：
    1. 根节点有明确背景色（hex/_rawBgHex）→ 直接用亮度判断（最可靠）
    2. design token 强信号：dark>0 且 light==0 → dark
    3. design token 兜底：dark > light → dark
    4. 首屏大标题（font-size ≥ 32px）_rawColorHex 投票：浅色文字 → dark 主题
       适用于根节点透明且子节点 section 用 hardcoded hex 背景的场景
    """
    css = ir.get("css", {})
    # 优先用根节点 Figma 实际渲染的色值（最可靠）
    raw_hex = css.get("_rawBgHex", "")
    if raw_hex:
        lum = _bg_luminance(raw_hex)
        if lum is not None:
            return "dark" if lum < 0.3 else "light"
    # 直接解析根节点 background-color（hex 值场景）
    raw_bg = css.get("background-color", "")
    if raw_bg:
        lum = _bg_luminance(raw_bg)
        if lum is not None:
            return "dark" if lum < 0.3 else "light"
        fb = re.search(r',\s*(#[0-9a-fA-F]{3,6})\s*\)', raw_bg)
        if fb:
            lum = _bg_luminance(fb.group(1))
            if lum is not None:
                return "dark" if lum < 0.3 else "light"
    # 根节点无明确背景色时，用子树 design token 统计辅助判断
    dark_count, light_count = _count_themed_bg_nodes(ir)
    if dark_count >= 3 and light_count == 0:
        return "dark"
    if dark_count > light_count:
        return "dark"
    # design token 背景色明确显示 light 多数 → 直接返回，不做 text voting
    # 防止单个深色 Hero section 的白色大标题翻转整页主题判断
    if light_count > dark_count:
        return "light"
    # design token 投票完全无结论（dark==light，通常两者都为 0）：
    # 用首屏大标题 _rawColorHex 投票。
    # 跳过无大文字的节（如 Navigation navbar），最多扫前 3 个节
    text_light = text_dark = 0
    for section in (ir.get("children") or [])[:3]:
        nodes = list(_collect_large_text_nodes(section, min_px=20))
        if not nodes:
            continue  # 无大文字节（navbar 等），跳到下一个
        for text_node in nodes:
            raw_c = (text_node.get("css") or {}).get("_rawColorHex", "")
            lum = _bg_luminance(raw_c) if raw_c else None
            if lum is not None:
                if lum > 0.5:
                    text_light += 1   # 浅色文字 → 深色背景
                else:
                    text_dark += 1    # 深色文字 → 浅色背景
        if text_light + text_dark > 0:
            break  # 找到有大文字的节，停止扫描
    if text_light > text_dark:
        return "dark"
    if text_dark > text_light:
        return "light"
    return "light"


# Keep backward-compat alias used by convert.py
_detect_page_theme = detect_page_theme


def generate_tsx(ir: dict, css_ext: str = "less",
                 texts_map: dict | None = None,
                 component_name_for_texts: str | None = None,
                 page_theme: str | None = None,
                 skip_subtrees: set | None = None,
                 texts_rel_prefix: str = './',
                 inject_page_env: bool = True,
                 hooks_rel_path: str = '../hooks/usePageEnv',
                 support_row_class_name: bool = False) -> str:
    """
    Generate a TSX module string for the root IR node.

    *css_ext* sets the CSS module file extension (default 'less').
    *texts_map* maps figmaId → tokenId; when provided, injects usePageTexts hook.
    *page_theme* overrides internal theme detection ('dark'|'light'). When omitted,
    falls back to _detect_page_theme(ir) — but prefer passing it explicitly from
    convert.py so both tag_mixed_theme_nodes and generate_tsx use the same theme.
    *inject_page_env* controls whether usePageEnv is inlined (True, standalone mode)
    or imported from a shared hook file (False, split-page mode).
    Always emits ``export default function``.
    """
    sem = ir.get("semantic")
    if not sem:
        raise ValueError(f"IR node {ir.get('figmaId')} missing semantic")

    name = sem.get("componentName") or _to_pascal(sem.get("className", "Component"))

    # 从 IR 树收集 _prop_name 标记 → Props interface
    marked_props = _collect_prop_markers(ir)
    # 兼容旧 semantic.props 机制
    legacy_props = sem.get("props") or []

    props_interface = ""
    props_param = ""
    if marked_props:
        prop_lines = "\n".join(
            f"  {p['name']}{'?' if p.get('optional') else ''}: {p['type']};"
            for p in marked_props
        )
        props_interface = f"\ninterface {name}Props {{\n{prop_lines}\n}}\n"
        names_str = ", ".join(p["name"] for p in marked_props)
        props_param = f"{{ {names_str} }}: {name}Props"
    elif legacy_props:
        prop_lines = "\n".join(f"  {p['name']}: {p['type']};" for p in legacy_props)
        props_interface = f"\ninterface {name}Props {{\n{prop_lines}\n}}\n"
        names_str = ", ".join(p["name"] for p in legacy_props)
        props_param = f"{{ {names_str} }}: {name}Props"

    # rowClassName prop: visual-variant support (e.g. zebra-stripe even rows).
    # When support_row_class_name=True, add rowClassName?: string to the interface and
    # merge it with the root element's className so callers can pass variant CSS classes.
    # Fix: Arena ranking-row-2 (6777:33313) — variant wrapper causes double flex+padding.
    if support_row_class_name:
        if props_interface:
            # Append rowClassName before the closing '}\n'
            props_interface = props_interface.rstrip('\n').rstrip('}') + "  rowClassName?: string;\n}\n"
            # Inject rowClassName into params
            if props_param:
                props_param = props_param.replace(
                    f"{{ {names_str} }}: {name}Props",
                    f"{{ {names_str}, rowClassName }}: {name}Props"
                )
        else:
            props_interface = f"\ninterface {name}Props {{\n  rowClassName?: string;\n}}\n"
            props_param = f"{{ rowClassName }}: {name}Props"

    jsx = _render_node(ir, legacy_props, depth=1, texts_map=texts_map, skip_subtrees=skip_subtrees)

    # U-373 补充：扫描 JSX 中由 icon_color 路径生成的 <Icon*> 组件，自动追加 import。
    # convert.py 的 flat 模式不使用 leaf_map CC，icon import 不会被自动追踪。
    # 过滤：只追加 ICON_PACKAGE 中实际存在的 Icon，避免 ReferenceError。
    import re as _re_icon
    _icon_comps_in_jsx = set(_re_icon.findall(r'<(Icon[A-Z][a-zA-Z0-9]*)', jsx))
    _icon_import_line: str | None = None
    if _icon_comps_in_jsx and _ICON_PACKAGE:
        _valid_for_import = _get_valid_icon_comps()
        _filtered_icons = {
            ic for ic in _icon_comps_in_jsx
            if _valid_for_import and ic in _valid_for_import
        }
        if _filtered_icons:
            _icon_import_line = (
                f"import {{ {', '.join(sorted(_filtered_icons))} }}"
                f" from '{_ICON_PACKAGE}';"
            )

    if support_row_class_name:
        # Merge rowClassName into the root element's className (first occurrence only)
        _root_cls = (ir.get('semantic') or {}).get('className', '')
        if _root_cls:
            _old_cls = f"className={{styles['{_root_cls}']}}"
            _new_cls = f"className={{[styles['{_root_cls}'], rowClassName].filter(Boolean).join(' ')}}"
            jsx = jsx.replace(_old_cls, _new_cls, 1)

    texts_name = component_name_for_texts or name
    _tp = texts_rel_prefix
    texts_imports = (
        f"import {{ PAGE_TEXTS, I18N_NS }} from '{_tp}{texts_name}.texts';\n"
        f"import type {{ PageTextToken }} from '{_tp}{texts_name}.texts';\n"
        f"import {{ TEXT_DEFAULTS }} from '{_tp}{texts_name}.texts.defaults';"
    ) if texts_map else ""

    hook_code = (
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
    ) if texts_map else ""

    hook_call = (
        f"  const {{ t }} = usePageTexts(TEXT_DEFAULTS)"
    ) if texts_map else ""

    # 页面主题：优先使用调用方传入的（由 diagnose_design_theme hex-based 检测），
    # 回退到 token-name counting（可能误判 adaptive token）
    _resolved_theme = page_theme if page_theme in ("dark", "light") else _detect_page_theme(ir)

    if inject_page_env:
        # 独立组件模式：内联 usePageEnv 定义（保持向后兼容）
        react_import = f"import React, {{ useEffect, useState }} from 'react';"
        page_env_import = None
        env_hook_code = (
            f"\nfunction usePageEnv() {{\n"
            f"  const [pageTheme, setPageTheme] = useState<'dark'|'light'>('{_resolved_theme}')\n"
            f"  useEffect(() => {{\n"
            f"    const html = document.documentElement\n"
            f"    const app = document.getElementById('app')\n"
            f"    const prevTheme = html.getAttribute('data-theme')\n"
            f"    const prevMinWidth = app?.style.minWidth\n"
            f"    const prevOverflowX = app?.style.overflowX\n"
            f"    html.setAttribute('data-theme', '{_resolved_theme}')\n"
            f"    setPageTheme('{_resolved_theme}')\n"
            f"    if (app) {{\n"
            f"      app.style.minWidth = 'unset'\n"
            f"      app.style.overflowX = 'unset'\n"
            f"    }}\n"
            f"    return () => {{\n"
            f"      if (prevTheme !== null) html.setAttribute('data-theme', prevTheme)\n"
            f"      else html.removeAttribute('data-theme')\n"
            f"      if (app) {{\n"
            f"        app.style.minWidth = prevMinWidth ?? ''\n"
            f"        app.style.overflowX = prevOverflowX ?? ''\n"
            f"      }}\n"
            f"    }}\n"
            f"  }}, [])\n"
            f"  return {{ pageTheme }}\n"
            f"}}\n"
        )
        page_env_call = "  const { pageTheme } = usePageEnv()"
    else:
        # split-page 模式：从页面级共享 hook 文件引入
        react_import = f"import React from 'react';"
        page_env_import = f"import {{ usePageEnv }} from '{hooks_rel_path}';"
        env_hook_code = None
        page_env_call = f"  const {{ pageTheme }} = usePageEnv('{_resolved_theme}')"

    lines = [
        react_import,
        f"import styles from './{name}.module.{css_ext}';",
        page_env_import,
        _icon_import_line,
        texts_imports,
        props_interface,
        hook_code,
        env_hook_code,
        f"export default function {name}({props_param}) {{",
        hook_call,
        page_env_call,
        "  return (",
        jsx,
        "  );",
        "}",
        "",
    ]
    return "\n".join(line for line in lines if line is not None)


def generate_index(component_name: str) -> str:
    """Generate a barrel index file: export { default } from './Name'."""
    return f"export {{ default }} from './{component_name}';\n"


def render_jsx_body(ir: dict, texts_map: dict | None = None) -> str:
    """Return only the JSX subtree for an IR node (no component wrapper).
    Used by split_codegen to embed IR content inside Section components.
    """
    return _render_node(ir, [], depth=1, texts_map=texts_map)


def render_jsx_body_with_leaf_refs(ir: dict, leaf_map: dict,
                                    texts_map: dict | None = None,
                                    subsection_map: dict | None = None) -> str:
    """Like render_jsx_body, but replaces leaf instance nodes with <ComponentName /> calls.

    leaf_map: {figmaId → {'componentName': str, 'varyingProps': list, 'instanceData': dict}}
    subsection_map: {figmaId → sub_section_name} — nodes replaced with <SubSectionName />.
    When the renderer encounters a node whose figmaId is in leaf_map, it emits
    <ComponentName prop="val" /> instead of recursing into the subtree.
    """
    return _render_node(ir, [], depth=1, leaf_map=leaf_map, texts_map=texts_map,
                        subsection_map=subsection_map)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_node(ir: dict, props: list[dict], depth: int,
                 leaf_map: dict | None = None,
                 texts_map: dict | None = None,
                 skip_subtrees: set | None = None,
                 subsection_map: dict | None = None) -> str:
    """Recursively render *ir* as a JSX element string."""
    # Skip subtrees that will be generated by split_codegen
    if skip_subtrees:
        fid = ir.get("figmaId", "")
        if fid in skip_subtrees:
            indent = "  " * (depth + 1)
            sem = ir.get("semantic")
            cls_name = sem.get('className', '') if sem else ''
            return (
                f'{indent}{{/* split-component: {cls_name} */}}\n'
                f'{indent}<div className={{styles[\'{cls_name}\']}} '
                f'data-figma-id="{fid}" />'
            )

    # SubSection reference: replace entire sub-section node with <SubSectionName />
    if subsection_map:
        fid = ir.get("figmaId", "")
        if fid in subsection_map:
            ss_name = subsection_map[fid]
            indent = "  " * (depth + 1)
            inner = f'<{ss_name} data-figma-id="{fid}" />'
            if ir.get('h5Only'):
                import re as _re_ss
                kebab = _re_ss.sub(r'([A-Z])', r'-\1', ss_name).lower().lstrip('-')
                return (f"{indent}<div className={{styles['h5-{kebab}']}}"
                        f">{inner}</div>")
            elif ir.get('pcOnly'):
                import re as _re_ss
                kebab = _re_ss.sub(r'([A-Z])', r'-\1', ss_name).lower().lstrip('-')
                return (f"{indent}<div className={{styles['pc-{kebab}']}}"
                        f">{inner}</div>")
            return f'{indent}{inner}'

    # Leaf component reference shortcut: render <ComponentName prop="val" /> instead of inline subtree
    if leaf_map:
        fid = ir.get("figmaId", "")
        # U-372: CC Tag used as an absolutely-positioned badge with a custom asymmetric
        # border-radius (e.g. "100px 0px 0px 100px") must NOT use the CC mapping.
        # The component's internal border-radius rule wins over className, so the custom shape
        # is silently lost.  Skip CC entirely and fall through to standard div rendering.
        _fid_css_badge = ir.get("css") or {}
        _leaf_info_badge = leaf_map.get(fid)
        _is_abs_tag_badge = (
            _leaf_info_badge is not None
            and _leaf_info_badge.get("ccComponent") == "Tag"
            and _fid_css_badge.get("position") == "absolute"
            and "0px" in _fid_css_badge.get("border-radius", "")
        )
        if fid in leaf_map and not _is_abs_tag_badge:
            info = leaf_map[fid]
            indent = "  " * (depth + 1)

            # 内联模式：直接渲染 snippet（不生成独立组件文件）
            inline_snippet = info.get("inlineSnippet")
            if inline_snippet:
                sem = ir.get("semantic")
                inst_cls = sem.get("className", "") if sem else ""
                snippet_jsx = inline_snippet.strip()
                import re as _re

                def _find_jsx_close_pos(s: str) -> int:
                    # U-434: skip '>' inside JSX expression values {…} to avoid splitting
                    # arrow function props like onChange={() => {}} at the inner '>'.
                    depth = 0
                    i = 0
                    while i < len(s):
                        c = s[i]
                        if c == '{':
                            depth += 1
                        elif c == '}':
                            depth -= 1
                        elif depth == 0:
                            if s[i:i+2] == '/>':
                                return i
                            elif c == '>':
                                return i
                        i += 1
                    return -1

                # 配置化：直传 className 的组件（不套 wrapper div）
                _INLINE_CLASSNAME_COMPONENTS = {"Button", "Tag"}
                _moly_comp = info.get("ccComponent", "")
                _is_icon = _moly_comp.startswith("Icon")
                _inject_cls = info.get("injectClassName",
                                       _moly_comp in _INLINE_CLASSNAME_COMPONENTS or _is_icon)
                # Icon 组件注入 color prop（来自 leaf_map 的 iconColor 字段）
                if _is_icon:
                    _icon_color = info.get("iconColor", "")
                    if _icon_color:
                        _fc_color_pos = _find_jsx_close_pos(snippet_jsx)
                        if _fc_color_pos >= 0:
                            snippet_jsx = snippet_jsx[:_fc_color_pos] + f' color="{_icon_color}"' + snippet_jsx[_fc_color_pos:]
                if _inject_cls and inst_cls:
                    cls_attr = f' className={{styles[\'{inst_cls}\']}}'
                    _pos = _find_jsx_close_pos(snippet_jsx)
                    if _pos >= 0:
                        snippet_jsx = snippet_jsx[:_pos] + cls_attr + f' data-figma-id="{fid}"' + snippet_jsx[_pos:]
                    return f'{indent}{snippet_jsx}'
                else:
                    # 非 className 组件：用 wrapper div 包裹定位样式
                    _pos = _find_jsx_close_pos(snippet_jsx)
                    if _pos >= 0:
                        snippet_jsx = snippet_jsx[:_pos] + f' data-figma-id="{fid}"' + snippet_jsx[_pos:]
                    if inst_cls:
                        # data-figma-id on wrapper div so CSS [data-figma-id="..."] selectors
                        # can match — CC components don't forward the prop to the DOM root.
                        return "\n".join([
                            f'{indent}<div className={{styles[\'{inst_cls}\']}} data-figma-id="{fid}">',
                            f'{indent}  {snippet_jsx}',
                            f'{indent}</div>',
                        ])
                    return f'{indent}{snippet_jsx}'

            comp_name = info["componentName"]
            varying = info.get("varyingProps") or []
            inst_data = info.get("instanceData") or {}
            if varying and inst_data:
                prop_parts = []
                for vp in varying:
                    raw_val = inst_data.get(vp["propName"], "")
                    # icon_tree: IR 节点 dict → 渲染为 JSX fragment（多层 SVG 合成图标）
                    if vp.get('type') == 'icon_tree' and isinstance(raw_val, dict):
                        icon_jsx = _render_icon_tree_jsx(raw_val)
                        prop_parts.append(f'{vp["propName"]}={{{icon_jsx}}}')
                        continue
                    val = str(raw_val)
                    segments = inst_data.get(f'__segments__{vp["propName"]}')
                    # i18n 标记：__i18n__:tokenId → {t('tokenId')}
                    if val.startswith('__i18n__:'):
                        token_id = val[len('__i18n__:'):]
                        prop_parts.append(f"{vp['propName']}={{t('{token_id}')}}")
                    elif segments and len(segments) >= 1:
                        seg_jsx = _render_segments_as_jsx(segments, full_text=val)
                        prop_parts.append(f'{vp["propName"]}={{<>{seg_jsx}</>}}')
                    else:
                        val_escaped = val.replace('"', "&quot;")
                        prop_parts.append(f'{vp["propName"]}="{val_escaped}"')
                    # Per-instance CSS style override: emit {propName}Style when cssValues present
                    if vp.get('cssValues'):
                        _sdict = inst_data.get(f'{vp["propName"]}Style')
                        if _sdict and isinstance(_sdict, dict):
                            # CSS keys → React camelCase (e.g. min-width → minWidth)
                            def _to_react_key(k):
                                parts = k.split('-')
                                return parts[0] + ''.join(p.capitalize() for p in parts[1:])
                            _sitems = ', '.join(
                                f'{_to_react_key(k)}: {json.dumps(v)}'
                                for k, v in _sdict.items() if v is not None
                            )
                            if _sitems:
                                prop_parts.append(f'{vp["propName"]}Style={{{{ {_sitems} }}}}')
                comp_call = f'<{comp_name} {" ".join(prop_parts)} />'
            else:
                comp_call = f'<{comp_name} />'
            # Direct instances (no ";" in figmaId) may have their own positioning CSS
            # (e.g. width:48px; height:48px; position:relative) that must be preserved
            # as a wrapper div.  Inner component nodes (I...; style) rely on their outer
            # component's div and need no extra wrapper.
            # Skip wrapper when the instance's CSS class is the SAME as the component's
            # root class — the component already renders that class as its root element,
            # so a wrapper would create duplicate double-nested absolute positioning.
            sem = ir.get("semantic")
            inst_cls = sem.get("className", "") if sem else ""
            root_cls = info.get("rootClassName", "")
            is_moly = info.get("isMoly", False)
            _CLASSNAME_COMPONENTS = {"Button", "Tag"}
            is_moly_button = info.get("ccComponent") in _CLASSNAME_COMPONENTS
            if is_moly_button and inst_cls:
                # Button：不套 wrapper，通过 className prop 传递定位样式
                cls = f"styles['{inst_cls}']"
                if '/>' in comp_call:
                    comp_call = comp_call.replace(
                        '/>',
                        f' className={{{cls}}} data-figma-id="{fid}" />')
                elif '>' in comp_call:
                    idx = comp_call.index('>')
                    comp_call = (comp_call[:idx] +
                                 f' className={{{cls}}} data-figma-id="{fid}"' +
                                 comp_call[idx:])
                return f'{indent}{comp_call}'
            # variantCls: visual-only CSS diff → pass as rowClassName prop, no wrapper div.
            # Fix: Arena ranking-row-2 (6777:33313) — wrapper div caused double flex+padding.
            _variant_cls = info.get('variantCls', '')
            if _variant_cls:
                _rcls = f"styles['{_variant_cls}']"
                _close_pos = comp_call.rfind('/>')
                if _close_pos >= 0:
                    comp_call = (comp_call[:_close_pos]
                                 + f'rowClassName={{{_rcls}}} data-figma-id="{fid}" />'
                                 + comp_call[_close_pos + 2:])
                return f'{indent}{comp_call}'

            needs_wrapper = is_moly or (
                sem and inst_cls and inst_cls != root_cls
            )
            if needs_wrapper and inst_cls:
                cls = f"styles['{inst_cls}']"
                # Also pass data-figma-id to the leaf component so its root div gets the
                # wrapper's figmaId. Without this, leaf components using a pcOnly default
                # figmaId (e.g. Trade "250:2637") stay hidden inside h5Only wrappers on mobile.
                # Real case: EarnFromSection Trade icon (250:3851 h5Only wrapper, 250:2637 pcOnly default).
                _comp_call_fid = comp_call
                _rfind_pos = comp_call.rfind('/>')
                if _rfind_pos >= 0:
                    _comp_call_fid = comp_call[:_rfind_pos] + f'data-figma-id="{fid}" />' + comp_call[_rfind_pos+2:]
                return "\n".join([
                    f'{indent}<div className={{{cls}}} data-figma-id="{fid}">',
                    f'{indent}  {_comp_call_fid}',
                    f'{indent}</div>',
                ])
            # 无 wrapper 路径：将 data-figma-id 注入组件调用的关闭标签（便于 DOM 定位各实例）。
            # 用 rfind 替换最后一个 />（组件关闭标签），避免误替换 prop 值中 JSX fragment 的 </>。
            _close_pos = comp_call.rfind('/>')
            if _close_pos >= 0:
                comp_call = comp_call[:_close_pos] + f'data-figma-id="{fid}" />' + comp_call[_close_pos+2:]
            return f'{indent}{comp_call}'

    sem = ir.get("semantic")
    if not sem:
        return ""

    indent = "  " * (depth + 1)
    tag = sem.get("htmlTag", "div")
    cls_name = sem.get('className', '')
    theme_override = ir.get("themeOverride")
    if theme_override in ("light", "dark"):
        # 动态计算：只在页面主题与 section 主题相反时注入主题覆盖 class
        # theme 由 usePageEnv() 提供，随 data-theme 变化而变化
        cls = (
            f"styles['{cls_name}'] + "
            f"(pageTheme !== '{theme_override}' ? ' theme-override-{theme_override}' : '')"
        )
    else:
        cls = f"styles['{cls_name}']"
    figma_id = ir.get("figmaId", "")

    # --- Image varying prop on any node (e.g. Figma component instance acting as icon) ---
    # When mark_varying_nodes marks a non-image/vector node as _prop_kind='image',
    # render it as <img src={propName}> instead of recursing into its children.
    if ir.get("_prop_kind") == "image" and ir.get("_prop_name"):
        _prop_n = ir["_prop_name"]
        _style_p = ir.get("_css_style_prop")
        _style_a = f' style={{{_style_p}}}' if _style_p else ''
        return (
            f'{indent}<img className={{{cls}}}{_style_a} src={{{_prop_n}}} '
            f'alt="" aria-hidden data-figma-id="{figma_id}" />'
        )

    # --- icon_tree varying prop: multi-layer SVG icon passed as ReactNode ---
    # Render a wrapper div with the container's className/figmaId (preserves positioning CSS),
    # then render {propName} inside it. The ReactNode fragment from the parent contains only
    # the SVG layers (no outer wrapper), so they inherit position: relative from this div.
    if ir.get("_prop_kind") == "icon_tree" and ir.get("_prop_name"):
        _prop_n = ir["_prop_name"]
        return (
            f'{indent}<div className={{{cls}}} data-figma-id="{figma_id}">'
            f'{{{_prop_n}}}</div>'
        )

    # --- Decorative elements (background SVGs, overlay layers) ---
    if ir.get("isDecorativeElement"):
        if ir.get("isVectorNode"):
            css = ir.get("css") or {}
            if css.get("backdrop-filter") or css.get("-webkit-backdrop-filter"):
                return (
                    f'{indent}<div className={{{cls}}} aria-hidden '
                    f'data-figma-id="{figma_id}" />'
                )
            # U-373: Icon component instances with iconColor render as icon component
            # (e.g. icon_thumbsup with #06c167 → <IconThumbsup color="#06c167" />),
            # not as a black SVG <img>. figmaName "icon_*" → "Icon*" component.
            # Skip names with "/" (Figma variant separator like "icon_arrow/tri") —
            # those don't map 1:1 to a component and would produce invalid JSX.
            _icon_color_val = ir.get("iconColor", "")
            _fname = ir.get("figmaName", "")
            if (_icon_color_val and ir.get("isComponentInstance")
                    and _fname.startswith("icon_") and "/" not in _fname):
                _parts = _fname[5:].split("_")
                _icon_comp = "Icon" + "".join(p.capitalize() for p in _parts if p)
                # U-373 校验：仅当 ICON_PACKAGE 确实导出该 Icon 时才使用组件渲染。
                # 包未配置或不存在时回退到 SVG <img>，避免悬空引用导致编译错误。
                _valid_icons = _get_valid_icon_comps()
                if _valid_icons and _icon_comp in _valid_icons:
                    return (
                        f'{indent}<{_icon_comp} color="{_icon_color_val}" className={{{cls}}} '
                        f'data-figma-id="{figma_id}" />'
                    )
                # 校验失败 → fall through to <img> rendering below
            if ir.get("localAssetPath"):
                _prop_n = ir.get("_prop_name")
                _src = f'{{{_prop_n}}}' if _prop_n else f'"{ir["localAssetPath"]}"'
                return (
                    f'{indent}<img className={{{cls}}} src={_src} '
                    f'alt="" aria-hidden data-figma-id="{figma_id}" />'
                )
            return (
                f'{indent}<div className={{{cls}}} aria-hidden '
                f'data-figma-id="{figma_id}" />{{/* decorative svg */}}'
            )
        if ir.get("isImageNode"):
            src = f'"{ir["localAssetPath"]}"' if ir.get("localAssetPath") else '""'
            return (
                f'{indent}<img className={{{cls}}} src={src} '
                f'alt="" aria-hidden data-figma-id="{figma_id}" />'
            )
        return (
            f'{indent}<div className={{{cls}}} aria-hidden '
            f'data-figma-id="{figma_id}" />'
        )

    # --- Image nodes ---
    if ir.get("isImageNode"):
        # _prop_name 标记优先（由 instance_merger.mark_varying_nodes 设置，type='image'）
        prop_name_marker = ir.get("_prop_name")
        if prop_name_marker:
            _style_pm = ir.get("_css_style_prop")
            _style_am = f' style={{{_style_pm}}}' if _style_pm else ''
            return (
                f'{indent}<img className={{{cls}}}{_style_am} src={{{prop_name_marker}}} '
                f'alt={{""}} data-figma-id="{figma_id}" />'
            )
        src_prop = next((p for p in props if p.get("replaces") == "imageSrc"), None)
        alt_prop = next((p for p in props if p.get("replaces") == "imageAlt"), None)
        if ir.get("localAssetPath"):
            src_val = f'"{ir["localAssetPath"]}"'
        elif src_prop:
            src_val = f"{{{src_prop['name']}}}"
        else:
            # U-436: no localAssetPath and no src prop — image was never exported from Figma.
            # Generate an empty div instead of <img src=""> which the browser interprets as
            # the current page URL, showing a broken image icon. Real case: EU deposit
            # campaign 250:4537 (Spotify_Icon_RGB_Green, h5Only) — never exported to SVG
            # asset, but split_codegen classifies it as isImageNode → htmlTag='img'.
            return (
                f'{indent}<div className={{{cls}}} data-figma-id="{figma_id}" />'
            )
        alt_val = f"{{{alt_prop['name']}}}" if alt_prop else '{""}'
        return (
            f'{indent}<img className={{{cls}}} src={src_val} '
            f'alt={alt_val} data-figma-id="{figma_id}" />'
        )

    # --- Text nodes ---
    if ir.get("isTextNode"):
        # _prop_name 标记优先（由 instance_merger.mark_varying_nodes 设置）
        prop_name_marker = ir.get("_prop_name")
        if prop_name_marker:
            return (
                f'{indent}<{tag} className={{{cls}}} '
                f'data-figma-id="{figma_id}">{{{prop_name_marker}}}</{tag}>'
            )

        text_prop = next((p for p in props if p.get("replaces") == "textContent"), None)

        # i18n: replace simple text nodes with {t('tokenId')}
        if texts_map and not text_prop and _is_simple_text_node(ir):
            token_id = texts_map.get(figma_id)
            if token_id:
                return (
                    f'{indent}<{tag} className={{{cls}}} '
                    f"data-figma-id=\"{figma_id}\">{{t('{token_id}')}}</{tag}>"
                )

        # List nodes (UNORDERED / ORDERED lineTypes) → <ul>/<ol> + <li>
        line_types = ir.get("lineTypes") or []
        is_list_node = any(t in ("UNORDERED", "ORDERED") for t in line_types)
        if is_list_node and not text_prop:
            raw_content = ir.get("textContent") or ""
            lines = [l for l in raw_content.split("\n") if l.strip()]
            unique_types = set(line_types)
            list_tag = "ol" if unique_types == {"ORDERED"} else "ul"
            items = []
            for line_text in lines:
                safe = line_text.replace("{", "&#123;").replace("}", "&#125;")
                items.append(f"{indent}  <li>{safe}</li>")
            items_str = "\n".join(items)
            return (
                f"{indent}<{list_tag} className={{{cls}}} "
                f'data-figma-id="{figma_id}">\n'
                f"{items_str}\n"
                f"{indent}</{list_tag}>"
            )

        # Multi-colour text segments
        segments = ir.get("textSegments") or []
        if not text_prop and len(segments) >= 1:
            seg_content = "".join(_render_text_segment(seg) for seg in segments)
            covered = "".join(seg.get("text", "") for seg in segments)
            full_text = ir.get("textContent") or ""
            trailing = full_text[len(covered):] if full_text.startswith(covered) else ""
            if trailing:
                safe = (trailing.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                seg_content += safe
            return (
                f'{indent}<{tag} className={{{cls}}} '
                f'data-figma-id="{figma_id}">{seg_content}</{tag}>'
            )

        raw_content = ir.get("textContent") or ""
        if text_prop:
            content = f"{{{text_prop['name']}}}"
        elif "\n" in raw_content:
            # Escape backtick-template to avoid JSX whitespace collapsing newlines
            escaped = (
                raw_content
                .replace("\\", "\\\\")
                .replace("`", "\\`")
                .replace("$", "\\$")
            )
            content = f"{{`{escaped}`}}"
        else:
            content = raw_content

        return (
            f'{indent}<{tag} className={{{cls}}} '
            f'data-figma-id="{figma_id}">{content}</{tag}>'
        )

    # --- Generic container / interactive nodes ---
    extra_attrs_parts: list[str] = []
    href_prop = next((p for p in props if p.get("replaces") == "href"), None)
    click_prop = next((p for p in props if p.get("replaces") == "onClick"), None)
    if href_prop:
        extra_attrs_parts.append(f"href={{{href_prop['name']}}}")
    if click_prop:
        extra_attrs_parts.append(f"onClick={{{click_prop['name']}}}")

    attrs_str = (" " + " ".join(extra_attrs_parts)) if extra_attrs_parts else ""

    children = ir.get("children") or []
    if not children:
        return (
            f'{indent}<{tag} className={{{cls}}}{attrs_str} '
            f'data-figma-id="{figma_id}" />'
        )

    # --- 图片合成整帧导出：检测纯装饰性图片子树，输出单个 <img> ---
    if FLATTEN_IMAGE_COMPOSITIONS and _is_image_composition(ir):
        # 使用节点自身的 localAssetPath（由上游 convert.py 注入），
        # 若不存在则生成预期路径供后续资源导出使用
        asset_path = ir.get('localAssetPath', '')
        if not asset_path:
            fid_safe = figma_id.replace(':', '-').replace(';', '-')
            asset_path = f'/assets/__flatten__/{fid_safe}.png'
        return (
            f'{indent}<img className={{{cls}}} src="{asset_path}" '
            f'alt={{""}} data-figma-id="{figma_id}" />'
        )

    child_lines = "\n".join(
        _render_node(child, props, depth + 1, leaf_map, texts_map,
                     skip_subtrees=skip_subtrees, subsection_map=subsection_map)
        for child in children
        if child.get("semantic")
    )
    child_lines = "\n".join(line for line in child_lines.splitlines() if line)

    return "\n".join([
        f'{indent}<{tag} className={{{cls}}}{attrs_str} data-figma-id="{figma_id}">',
        child_lines,
        f"{indent}</{tag}>",
    ])


def _render_text_segment(seg: dict) -> str:
    """Render a single text segment (possibly colour/font-annotated) as JSX."""
    text = (
        seg.get("text", "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "{'\\n'}")
    )
    gradient = seg.get("gradient")
    if gradient:
        # CSS gradient text: background-clip:text + transparent text-fill-color
        style = (
            f"background:'{gradient}',"
            f"WebkitBackgroundClip:'text',"
            f"WebkitTextFillColor:'transparent',"
            f"backgroundClip:'text'"
        )
        return f"<span style={{{{{style}}}}}>{text}</span>"

    # Build style dict for color + font overrides
    style_parts = []
    color = seg.get("color")
    if color:
        style_parts.append(f"color:'{color}'")
    font_size = seg.get("fontSize")
    if font_size:
        style_parts.append(f"fontSize:'{font_size}'")
    font_weight = seg.get("fontWeight")
    if font_weight:
        style_parts.append(f"fontWeight:{font_weight}")

    if style_parts:
        style_str = ",".join(style_parts)
        return f"<span style={{{{{style_str}}}}}>{text}</span>"
    return text


def _render_segments_as_jsx(segments: list, full_text: str = '') -> str:
    """将 textSegments 列表渲染为内联 JSX（用于 varying prop 传值）。
    如果 segments 没有覆盖完整文本，追加剩余部分。"""
    result = "".join(_render_text_segment(seg) for seg in segments)
    if full_text:
        covered = "".join(seg.get("text", "") for seg in segments)
        if full_text.startswith(covered) and len(covered) < len(full_text):
            trailing = full_text[len(covered):]
            safe = (trailing.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;")
                    .replace("\n", "{'\\n'}"))
            result += safe
    return result


def _collect_prop_markers(ir: dict, result: list | None = None, _seen: dict | None = None) -> list:
    """递归收集 IR 树中所有带 _prop_name 标记的节点，构建 Props interface 列表。
    对重名 prop 追加数字后缀，并 in-place 更新 IR 节点 _prop_name 保持 _render_node 一致。"""
    if result is None:
        result = []
    if _seen is None:
        _seen = {}
    if ir.get("_prop_name"):
        name = ir["_prop_name"]
        if name in _seen:
            _seen[name] += 1
            name = f'{name}{_seen[name]}'
            ir["_prop_name"] = name
        else:
            _seen[name] = 1
        result.append({"name": name, "type": ir.get("_prop_type", "string")})
        if ir.get("_css_style_prop"):
            result.append({"name": ir["_css_style_prop"],
                           "type": "React.CSSProperties", "optional": True})
    for child in ir.get("children") or []:
        _collect_prop_markers(child, result, _seen)
    return result


def _collect_icon_layers(node: dict, result: list) -> None:
    """深度优先收集所有有 localAssetPath 的 image/vector 节点（含自身）。"""
    if (node.get('isImageNode') or node.get('isVectorNode')) and node.get('localAssetPath'):
        result.append(node)
        return
    for child in (node.get('children') or []):
        _collect_icon_layers(child, result)


# CSS 属性列表（icon 节点，保持 camelCase）
_ICON_CSS_PROPS = ('position', 'top', 'right', 'bottom', 'left',
                   'width', 'height', 'transform', 'object-fit',
                   'border-radius', 'overflow', 'pointer-events', 'opacity',
                   'flex-shrink')
_ICON_CSS_CAMEL = {
    'object-fit': 'objectFit', 'border-radius': 'borderRadius',
    'pointer-events': 'pointerEvents', 'flex-shrink': 'flexShrink',
}


def _icon_style(css: dict, default_position: str = 'absolute') -> str:
    """将 IR css dict 转换为内联 JSX style 对象字符串（用于 icon_tree 渲染）。"""
    parts = []
    if 'position' not in css:
        parts.append(f"position: '{default_position}'")
    for prop in _ICON_CSS_PROPS:
        val = css.get(prop)
        if val is None:
            continue
        key = _ICON_CSS_CAMEL.get(prop, prop)
        parts.append(f"{key}: '{val}'")
    # Only inject left/top defaults when neither the opposite axis anchor (right/bottom)
    # nor the axis itself is already specified. When an element uses right-anchor
    # positioning (e.g. right:-64.81px), adding left:0 would make left win in LTR,
    # overriding the intended right-based placement.
    if not any(p.startswith('left') or p.startswith('right') for p in parts):
        parts.append("left: '0px'")
    if not any(p.startswith('top') or p.startswith('bottom') for p in parts):
        parts.append("top: '0px'")
    return f"{{{{{', '.join(parts)}}}}}"


def _render_icon_subtree(node: dict, depth: int) -> str:
    """递归渲染图标子树为 JSX（保留中间容器，确保 % 位置相对于正确父元素）。"""
    indent = '  ' * depth
    fid = node.get('figmaId', '')
    fid_attr = f' data-figma-id="{fid}"' if fid else ''

    # 叶子：image/vector
    if (node.get('isImageNode') or node.get('isVectorNode')) and node.get('localAssetPath'):
        lap = node['localAssetPath']
        if node.get('isImageNode') and not node.get('isVectorNode'):
            # U-432: Photo fills render as container div + inner object-cover img to match
            # the Figma fill intent. The extractor captures the bounding-box offset (e.g.
            # left:-37px, top:-16px) which describes the CONTAINER position, not the image
            # scale. Using the offset as direct <img> style produces a 1:1-scale crop that
            # differs from Figma's object-cover center crop — visible as the wrong image
            # area in the rendered card (e.g. EU deposit 250:3300 showed a blurry background
            # strip instead of the intended QR+phone scene).
            container_style = _icon_style(node.get('css') or {}, default_position='absolute')
            inner_style = ("{{position: 'absolute', inset: '0', width: '100%', "
                           "height: '100%', objectFit: 'cover', pointerEvents: 'none'}}")
            return (f'{indent}<div style={container_style}{fid_attr}>\n'
                    f'{indent}  <img src="{lap}" style={inner_style} alt="" aria-hidden />\n'
                    f'{indent}</div>')
        style = _icon_style(node.get('css') or {}, default_position='absolute')
        return f'{indent}<img src="{lap}" style={style} alt="" aria-hidden{fid_attr} />'

    # 中间容器：渲染为 div，子节点递归
    children = node.get('children') or []
    if not children:
        return ''
    css = dict(node.get('css') or {})
    bb = node.get('bb') or {}
    if 'width' not in css and bb.get('width'):
        css['width'] = f"{bb['width']:.4g}px"
    if 'height' not in css and bb.get('height'):
        css['height'] = f"{bb['height']:.4g}px"
    # 容器需要 position:relative 为子节点提供定位上下文
    if css.get('position') != 'absolute':
        css['position'] = 'relative'
    style = _icon_style(css, default_position='relative')
    child_lines = [_render_icon_subtree(c, depth + 1) for c in children]
    inner = '\n'.join(l for l in child_lines if l)
    if not inner:
        return ''
    return f'{indent}<div style={style}{fid_attr}>\n{inner}\n{indent}</div>'


def _render_icon_tree_jsx(node: dict) -> str:
    """将多层 SVG 合成图标的 IR 节点渲染为内联 JSX 表达式（用于 Section 传 ReactNode prop）。

    递归渲染图标 IR 子树为内联 JSX（保留中间容器结构，CSS % 相对于正确父元素）。
    输出不含外层容器 div（由叶子组件的 className 提供）。
    """
    # 单层图标：直接 image/vector
    # 用节点 BB 尺寸居中显示（不用原始 CSS，原始 CSS 含卡片级定位如 left:140px）
    if (node.get('isImageNode') or node.get('isVectorNode')) and node.get('localAssetPath'):
        lap = node['localAssetPath']
        bb = node.get('bb') or {}
        w_raw = bb.get('width', 0)
        h_raw = bb.get('height', 0)
        w = f"{w_raw:.4g}px" if w_raw else '100%'
        h = f"{h_raw:.4g}px" if h_raw else '100%'
        centered = (f"{{{{position: 'absolute', top: '50%', left: '50%', "
                    f"transform: 'translate(-50%, -50%)', width: '{w}', height: '{h}'}}}}")
        fid = node.get('figmaId', '')
        fid_attr = f' data-figma-id="{fid}"' if fid else ''
        return f'\n        <img src="{lap}" style={centered} alt="" aria-hidden{fid_attr} />\n      '

    # 多层：递归渲染子节点，保留中间容器
    children = node.get('children') or []
    if not children:
        return '\n        {/* empty icon */}\n      '
    child_lines = [_render_icon_subtree(c, depth=3) for c in children]
    inner = '\n'.join(l for l in child_lines if l)
    if not inner:
        return '\n        {/* no layers */}\n      '
    # 若容器自身有 background-color，在内容前插入绝对定位的背景层
    bg = (node.get('css') or {}).get('background-color', '')
    if bg:
        bg_div = f"          <div style={{{{position: 'absolute', inset: 0, backgroundColor: '{bg}'}}}} />"
        inner = bg_div + '\n' + inner
    return f'\n        <>\n{inner}\n        </>\n      '


# ---------------------------------------------------------------------------
# i18n text token helpers
# ---------------------------------------------------------------------------

def _make_token_id(base: str, used_ids: set) -> str:
    """Assign a unique token ID from *base*, updating *used_ids* in-place."""
    if base not in used_ids:
        used_ids.add(base)
        return base
    i = 1
    while f'{base}_{i}' in used_ids:
        i += 1
    tid = f'{base}_{i}'
    used_ids.add(tid)
    return tid


def _is_simple_text_node(node: dict) -> bool:
    """Return True if this text node is suitable for i18n replacement.
    Skips: _prop_name markers, list nodes, multi-segment, multiline text.
    """
    if not node.get('isTextNode'):
        return False
    if node.get('_prop_name'):
        return False
    line_types = node.get('lineTypes') or []
    if any(lt in ('UNORDERED', 'ORDERED') for lt in line_types):
        return False
    if node.get('textSegments') and len(node['textSegments']) >= 1:
        return False
    text = node.get('textContent') or ''
    if '\n' in text:
        return False
    return True


def collect_text_tokens(ir: dict, used_ids: set | None = None) -> list[dict]:
    """Collect i18n text tokens from an IR tree (DFS pre-order).

    *used_ids* is a shared dedup set; pass the same set across multiple
    ``collect_text_tokens`` calls to get globally unique token IDs.
    Returns [{'tokenId': str, 'default': str, 'figmaId': str}].
    """
    if used_ids is None:
        used_ids = set()
    tokens: list[dict] = []

    def _walk(node: dict) -> None:
        if _is_simple_text_node(node):
            sem = node.get('semantic') or {}
            cls = sem.get('className', 'text')
            base = re.sub(r'[^a-zA-Z0-9]+', '_', cls).strip('_').lower() or 'text'
            tokens.append({
                'tokenId': _make_token_id(base, used_ids),
                'default': node.get('textContent') or '',
                'figmaId': node.get('figmaId', ''),
            })
        for child in node.get('children') or []:
            _walk(child)

    _walk(ir)
    return tokens


def collect_text_tokens_flat(nodes, used_ids: set | None = None) -> list[dict]:
    """Collect i18n text tokens from a flat iterable of nodes (already DFS-ordered).

    Used by split_codegen to collect from node_index without double-counting
    (each figmaId appears exactly once in node_index).
    """
    if used_ids is None:
        used_ids = set()
    tokens: list[dict] = []
    for node in nodes:
        if _is_simple_text_node(node):
            sem = node.get('semantic') or {}
            cls = sem.get('className', 'text')
            base = re.sub(r'[^a-zA-Z0-9]+', '_', cls).strip('_').lower() or 'text'
            tokens.append({
                'tokenId': _make_token_id(base, used_ids),
                'default': node.get('textContent') or '',
                'figmaId': node.get('figmaId', ''),
            })
    return tokens


def parse_existing_texts_keys(texts_ts_path) -> dict:
    """Read an existing texts.ts and return {tokenId: key} for already-filled keys.

    Only returns entries where the key is non-empty so that re-running
    generate_texts_file preserves developer-filled keys without overwriting them.
    Accepts a str or Path.
    """
    from pathlib import Path as _Path
    p = _Path(texts_ts_path)
    if not p.exists():
        return {}
    content = p.read_text(encoding='utf-8')
    result: dict[str, str] = {}
    for m in re.finditer(r"^\s+(\w+):\s+'([^']+)',", content, re.MULTILINE):
        token_id, key = m.group(1), m.group(2)
        result[token_id] = key
    return result


def generate_texts_file(tokens: list[dict], component_name: str,
                        existing_keys: dict | None = None) -> str:
    """Generate {Name}.texts.ts content (permanent key mapping, never delete).

    *existing_keys* is a {tokenId: key} dict from a previously written texts.ts;
    any already-filled key is preserved — only empty / new tokens stay blank.
    """
    if existing_keys is None:
        existing_keys = {}
    lines = [
        "// Auto-generated — fill in the key fields to enable i18n",
        "// Leave key as empty string to use Figma original text as fallback",
        "",
        "export const I18N_NS = ''  // TODO: fill in namespace",
        "",
        "export const PAGE_TEXTS = {",
    ]
    for tok in tokens:
        tid = tok['tokenId']
        key_val = f"'{existing_keys[tid]}'" if existing_keys.get(tid) else "''"
        lines.append(f"  {tid}: {key_val},")
    lines += [
        "} as const",
        "",
        "export type PageTextToken = keyof typeof PAGE_TEXTS",
        "",
        "// PageT is the type of the t() function passed to child components",
        "export type PageT = (tokenId: PageTextToken, params?: Record<string, unknown>) => string",
        "",
    ]
    return "\n".join(lines)


def generate_texts_defaults_file(tokens: list[dict], component_name: str,
                                  figma_source: str = '') -> str:
    """Generate {Name}.texts.defaults.ts content (delete when all keys are filled).

    *figma_source* is appended as a header comment so the file is traceable to its
    design origin (e.g. the Figma URL or node ID string).
    Each token entry is annotated with its figmaId for node-level traceability.
    """
    lines = [
        f"import type {{ PageTextToken }} from './{component_name}.texts'",
        "",
    ]
    if figma_source:
        lines.append(f"// 设计稿来源: {figma_source}")
    lines += [
        "// Development fallback — delete this file once all i18n keys are filled",
        "export const TEXT_DEFAULTS: Record<PageTextToken, string> = {",
    ]
    for tok in tokens:
        fid = tok.get('figmaId', '')
        if fid:
            lines.append(f"  // figmaId: {fid}")
        escaped = (
            (tok['default'] or '')
            .replace('\\', '\\\\')
            .replace("'", "\\'")
            .replace('\r', '')
        )
        lines.append(f"  {tok['tokenId']}: '{escaped}',")
    lines += ["}", ""]
    return "\n".join(lines)
