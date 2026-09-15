from __future__ import annotations
"""
scss_generator.py — SCSS text generation from an IR tree.


The IR is a plain Python dict:
  {
    "semantic": {"className": str, ...},
    "css":      dict,   # CSS property → value
    "children": list[dict],
  }
"""


# CSS properties that postcss-rtlcss would incorrectly flip for gradient-text
# nodes; wrapped in /* rtl:begin:ignore */ … /* rtl:end:ignore */ comments.
_RTL_IGNORE_PROPS = frozenset({
    "background",
    "background-image",
    "background-color",
    "-webkit-background-clip",
    "background-clip",
    "-webkit-text-fill-color",
})


def generate_scss(ir: dict, variables_path: str | None = None) -> str:
    """
    Walk *ir* and emit an SCSS string with one rule block per unique class.

    *variables_path* — when provided, prepends ``@use '<path>' as *;`` to the output.
    """
    classes = _collect_classes(ir)
    if not classes:
        return ""

    responsive_map = _collect_responsive(ir)
    visibility_map = _collect_visibility(ir)

    # Merged root with h5RootHeight: inject mobile min-height so H5
    # absolutely-positioned content is not clipped when the PC block child
    # is hidden at ≤768px and collapses the container height to 0.
    h5_root_height = ir.get('h5RootHeight')
    if h5_root_height:
        root_cls = (ir.get('semantic') or {}).get('className')
        if root_cls and root_cls not in responsive_map:
            responsive_map[root_cls] = [{
                'breakpoint': 768,
                'css': {
                    'width': '100%',
                    'minHeight': f'{int(h5_root_height)}px',
                    'flexDirection': 'column',
                    'justifyContent': 'flex-start',
                    'alignItems': 'flex-start',
                },
                'confidence': 1.0,
            }]

    rule_blocks = []
    for cls, css in classes.items():
        rule_blocks.append(_render_rule(cls, css))
        # 新增：紧跟该 class 的 @media 块
        rule_blocks.extend(
            _render_media_blocks(cls, responsive_map.get(cls), visibility_map.get(cls))
        )

    rules = "\n\n".join(rule_blocks)
    use_decl = f"@use '{variables_path}' as *;\n\n" if variables_path else ""
    return f"{use_decl}{rules}\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _inject_var_fallbacks(css: dict) -> dict:
    """Inject _rawColorHex/_rawBgHex as var() fallbacks for color/background-color.

    When design token CSS is not loaded in dev environments, var(--token) with no
    fallback resolves to invalid → color inherits as black. The raw hex recorded at
    extraction time provides the design-intended fallback value.
    Only modifies simple var(--token) calls (no comma = no existing fallback);
    color-mix() and already-fallback var() are left untouched.
    """
    raw_color = css.get('_rawColorHex', '')
    raw_bg = css.get('_rawBgHex', '')
    if not raw_color and not raw_bg:
        return css
    result = dict(css)
    if raw_color:
        val = css.get('color', '')
        if val and val.startswith('var(') and ',' not in val:
            result['color'] = val[:-1] + f', {raw_color})'
    if raw_bg:
        val = css.get('background-color', '')
        if val and val.startswith('var(') and ',' not in val:
            result['background-color'] = val[:-1] + f', {raw_bg})'
    return result


def _render_rule(cls: str, css: dict) -> str:
    """Render a single ``.cls { ... }`` block, honouring RTL-ignore wrapping."""
    css = _inject_var_fallbacks(css)

    is_gradient_text = (
        css.get("background-clip") == "text"
        or css.get("-webkit-background-clip") == "text"
    )

    # Separate _before: prefixed properties into a ::before pseudo-element block
    before_props = [(k[8:], v) for k, v in css.items() if k.startswith("_before:") and v]

    # Filter out null / empty values and internal metadata fields (prefixed with _)
    entries = [(k, v) for k, v in css.items() if v is not None and v != "" and not k.startswith("_")]

    if is_gradient_text:
        before = [(k, v) for k, v in entries if k not in _RTL_IGNORE_PROPS]
        ignored = [(k, v) for k, v in entries if k in _RTL_IGNORE_PROPS]

        def fmt(pairs: list[tuple[str, str]]) -> str:
            return "\n".join(f"  {k}: {v};" for k, v in pairs)

        parts: list[str] = []
        if before:
            parts.append(fmt(before))
        if ignored:
            parts.append(
                f"  /* rtl:begin:ignore */\n{fmt(ignored)}\n  /* rtl:end:ignore */"
            )
        decls = "\n".join(parts)
    else:
        decls = "\n".join(f"  {k}: {v};" for k, v in entries)

    # Append ::before pseudo-element for IMAGE fill with opacity
    if before_props:
        before_decls = "\n".join(f"    {k}: {v};" for k, v in before_props)
        pseudo = (
            f"\n\n  &::before {{\n"
            f"    content: '';\n"
            f"    position: absolute;\n"
            f"    inset: 0;\n"
            f"    z-index: 0;\n"
            f"{before_decls}\n"
            f"  }}"
        )
        return f".{cls} {{\n{decls}{pseudo}\n}}"

    return f".{cls} {{\n{decls}\n}}"


def _collect_classes(ir: dict, result: dict | None = None) -> dict:
    """
    Collect (className → css) pairs from *ir* in document order.

    The first occurrence of a class name wins (subsequent nodes with the same
    name but different CSS are ignored — normalise first with normalize_classes).
    """
    if result is None:
        result = {}

    semantic = ir.get("semantic")
    css = ir.get("css", {})
    if semantic and semantic.get("className") and css:
        name = semantic["className"]
        if name not in result:
            result[name] = css

    for child in ir.get("children", []):
        _collect_classes(child, result)

    return result


# ── 新增：响应式辅助 ── 追加到 scss_generator.py 末尾 ────────────────────────

def _collect_responsive(ir: dict, result: dict | None = None) -> dict:
    """Collect {className: responsive_list} pairs from IR tree."""
    if result is None:
        result = {}
    sem = ir.get('semantic') or {}
    resp = ir.get('responsive')
    cls = sem.get('className')
    if cls and resp and cls not in result:
        result[cls] = resp
    for child in ir.get('children') or []:
        _collect_responsive(child, result)
    return result


def _collect_visibility(ir: dict, result: dict | None = None) -> dict:
    """Collect {className: 'pcOnly'|'h5Only'} from IR tree."""
    if result is None:
        result = {}
    sem = ir.get('semantic') or {}
    cls = sem.get('className')
    if cls:
        if ir.get('pcOnly'):
            result[cls] = 'pcOnly'
        elif ir.get('h5Only'):
            result[cls] = 'h5Only'
    for child in ir.get('children') or []:
        _collect_visibility(child, result)
    return result


def _render_media_blocks(cls: str, responsive_list: list, visibility: str | None) -> list:
    """Emit @media rule blocks for a class. Returns list of block strings."""
    blocks = []

    for resp in (responsive_list or []):
        bp = resp.get('breakpoint', 768)
        resp_css = {k: v for k, v in (resp.get('css') or {}).items() if v}
        if resp_css:
            # camelCase → kebab-case for CSS output
            props = '\n'.join(
                f'    {_camel_to_kebab(k)}: {v};'
                for k, v in resp_css.items()
            )
            blocks.append(f'@media (max-width: {bp}px) {{\n  .{cls} {{\n{props}\n  }}\n}}')

    if visibility == 'pcOnly':
        bp = (responsive_list or [{}])[0].get('breakpoint', 768) if responsive_list else 768
        blocks.append(f'@media (max-width: {bp}px) {{\n  .{cls} {{\n    display: none;\n  }}\n}}')
    elif visibility == 'h5Only':
        bp = (responsive_list or [{}])[0].get('breakpoint', 768) if responsive_list else 768
        blocks.append(f'@media (min-width: {bp + 1}px) {{\n  .{cls} {{\n    display: none;\n  }}\n}}')

    return blocks


def _camel_to_kebab(name: str) -> str:
    import re as _re
    return _re.sub(r'([A-Z])', r'-\1', name).lower()
