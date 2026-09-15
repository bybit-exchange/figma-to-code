from __future__ import annotations
"""
token_resolver.py — Fetch, parse, cache, and apply design tokens.

Fetches a design token CSS file at most once per 24h.
Cache lives in .figma-to-code/1-design-token/ (passed in by convert.py).
Network failure or missing config → silent empty map (CSS values kept as raw hex/px).

Configuration:
  Set DESIGN_TOKEN_CSS_URL environment variable to point to your design system's
  CSS custom properties file (e.g. a file that defines --color-primary, --font-size-md, etc).
  If not set, token resolution is skipped and raw hex/px values are kept as-is.
"""


import json
import os
import re
import time
import urllib.request
from pathlib import Path

# Read from environment variable — no URL means token resolution is disabled.
_TOKEN_CSS_URL = os.environ.get("DESIGN_TOKEN_CSS_URL", "")
_CACHE_TTL = 86400  # 1 day in seconds
_CACHE_FILENAME = "design-tokens.json"

# CSS properties that carry color values
_COLOR_PROPS = frozenset({
    "color", "background-color", "background",
    "border-color", "border-top-color", "border-right-color",
    "border-bottom-color", "border-left-color",
    "outline-color", "fill", "stroke",
})
_FONT_SIZE_PROPS = frozenset({"font-size"})
_FONT_WEIGHT_PROPS = frozenset({"font-weight"})
_FONT_FAMILY_PROPS = frozenset({"font-family"})
_BORDER_RADIUS_PROPS = frozenset({
    "border-radius",
    "border-top-left-radius", "border-top-right-radius",
    "border-bottom-left-radius", "border-bottom-right-radius",
})

# Tokens that must never substitute a text `color` property.
# These are background/faint-text tokens that change significantly between themes:
#   - bg tokens: dark=black/near-black, light=white/near-white → text disappears on bg
#   - faint text tokens: acceptable in dark mode but near-invisible in light mode
# Add your own design system's background and faint-text token names (without leading '--').
_BG_SEMANTIC_TOKENS: frozenset = frozenset()

# bg-fill tokens that are theme-sensitive and must not be used as background-color.
# Detected by naming convention; this list is intentionally empty — detection is
# handled by _BG_FILL_PATTERN below so any design system can benefit.
_BG_FILL_TOKENS: frozenset = frozenset()

# Matches token names that semantically represent page/area background fills.
# e.g. --color-bg-page, --theme-bg-surface, --xxx-bg-card
_BG_FILL_PATTERN    = re.compile(r'(?:^|-)bg-(?:page|area|card|float|surface|layer|base|default)(?:-|$)', re.IGNORECASE)
_BG_SEMANTIC_PATTERN = re.compile(r'(?:^|-)(?:bg|background)(?:-|$)', re.IGNORECASE)


def _is_bg_semantic_token(var_str: str) -> bool:
    """Return True if the token name suggests a background semantic role.
    Uses naming convention (e.g. --xxx-bg-page, --bg-surface) rather than a hardcoded list.
    Background tokens should not replace text `color` values.
    """
    m = re.search(r'--([^)]+)\)', var_str)
    if not m:
        return False
    name = m.group(1)
    return bool(_BG_SEMANTIC_PATTERN.search(name)) or name in _BG_SEMANTIC_TOKENS


def _is_bg_fill_token(var_str: str) -> bool:
    """Return True if token name indicates a theme-sensitive page/area fill.
    Uses naming convention (e.g. --xxx-bg-page, --bg-area) rather than a hardcoded list.
    These should not replace explicit background-color values on sub-sections.
    """
    m = re.search(r'--([^)]+)\)', var_str)
    if not m:
        return False
    name = m.group(1)
    return bool(_BG_FILL_PATTERN.search(name)) or name in _BG_FILL_TOKENS


def _is_interaction_token(var_str: str) -> bool:
    """Return True for interaction-state or transparency overlay tokens.
    Detected by naming convention: tokens with -trans-/-hover/-active/-pressed/-focus/-disabled/-state-.
    These semi-transparent or state-specific tokens should not replace solid fill colors.
    """
    m = re.search(r'--([^)]+)\)', var_str)
    if not m:
        return False
    name = m.group(1)
    return bool(re.search(
        r'(?:^|-)(?:trans|hover|active|pressed|focus|state)(?:-|$)',
        name, re.IGNORECASE
    ))


def _normalize_hex(v: str) -> str:
    """Normalize hex color to lowercase 6-digit form."""
    v = v.strip().lower()
    if re.match(r'^#[0-9a-f]{3}$', v):
        return '#' + ''.join(c * 2 for c in v[1:])
    return v


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int] | None:
    """Parse normalized #rrggbb to (R, G, B) ints."""
    if not hex_str or len(hex_str) != 7 or hex_str[0] != '#':
        return None
    try:
        return (int(hex_str[1:3], 16), int(hex_str[3:5], 16), int(hex_str[5:7], 16))
    except ValueError:
        return None


def _fuzzy_match_color(hex_str: str, colors: dict, max_delta: int = 2) -> str | None:
    """Find the closest BDS token for a hex color within max_delta per channel."""
    target = _hex_to_rgb(hex_str)
    if not target:
        return None
    best_token = None
    best_dist = (max_delta + 1) * 3  # max possible dist is 3*max_delta; init above that
    for known_hex, token_ref in colors.items():
        known = _hex_to_rgb(known_hex)
        if not known:
            continue
        dr = abs(target[0] - known[0])
        dg = abs(target[1] - known[1])
        db = abs(target[2] - known[2])
        if dr <= max_delta and dg <= max_delta and db <= max_delta:
            dist = dr + dg + db
            if dist < best_dist:
                best_dist = dist
                best_token = token_ref
    return best_token


def _normalize_rgba(v: str) -> str | None:
    """
    Normalize any rgba/rgb variant to canonical 'rgba(R, G, B, A)' form.
    Handles:
      rgba(56, 68, 82, 0.06)    — IR output (decimal alpha)
      rgba(56, 68, 82, 6%)      — BDS comma + percent alpha
      rgb(56 68 82 / 6%)        — BDS CSS Level 4 space format
      rgb(56, 68, 82)           — opaque comma
      rgb(56 68 82)             — opaque space
    Returns None if parsing fails.
    """
    s = v.strip()
    m = re.match(
        r'^rgba?\s*\(\s*'
        r'(\d+(?:\.\d+)?)\s*[,\s]\s*'   # R
        r'(\d+(?:\.\d+)?)\s*[,\s]\s*'   # G
        r'(\d+(?:\.\d+)?)'               # B
        r'(?:\s*[,/]\s*(\d+(?:\.\d+)?%?))?'  # optional alpha
        r'\s*\)$',
        s, re.IGNORECASE,
    )
    if not m:
        return None
    r, g, b = int(float(m.group(1))), int(float(m.group(2))), int(float(m.group(3)))
    alpha_raw = m.group(4)
    if alpha_raw is None:
        a = 1.0
    elif alpha_raw.endswith('%'):
        a = round(float(alpha_raw[:-1]) / 100, 4)
    else:
        a = round(float(alpha_raw), 4)
    if a >= 1.0:
        return f'rgba({r}, {g}, {b}, 1)'
    return f'rgba({r}, {g}, {b}, {a})'


def _extract_scope(css_text: str, pos: int) -> str:
    """
    Given a position in CSS text, determine the enclosing selector scope.
    Returns 'dark', 'light', 'root', 'site-<name>', or 'other'.

    Strategy: take the FIRST comma-separated selector token to determine scope.
    Examples:
      :root                                     → 'root' (light)
      :root,:root[data-theme=dark] .moly-*      → 'root' (light, first token is plain :root)
      :root[data-theme=dark],...                → 'dark'
      :root[data-site=usa],...                  → 'site-usa'
      :root[data-site=usa][data-theme=dark],... → 'site-usa-dark'
    """
    depth = 0
    selector = ''
    i = pos - 1
    while i >= 0:
        ch = css_text[i]
        if ch == '}':
            depth += 1
        elif ch == '{':
            if depth == 0:
                j = i - 1
                while j >= 0 and css_text[j] not in ('}', '{'):
                    j -= 1
                selector = css_text[j + 1:i].strip()
                break
            depth -= 1
        i -= 1

    # Use only the first selector token (before any comma)
    first = selector.split(',')[0].strip().lower()

    is_dark = bool(re.search(r'data-theme\s*=\s*["\']?dark["\']?', first))
    site_m = re.search(r'data-site\s*=\s*["\']?(\w+)["\']?', first)
    site = site_m.group(1) if site_m else None

    if site:
        return f'site-{site}-dark' if is_dark else f'site-{site}'
    if is_dark:
        return 'dark'
    # Plain :root / html / empty → light (global default theme)
    if re.match(r'^(:root|html)?$', first):
        return 'root'
    return 'other'


def _parse_css(css_text: str) -> dict:
    """
    Extract all CSS custom properties (--*) from CSS text.
    First-wins per scope for duplicate values.
    Also builds color_themes: hex → list of scopes, for design-theme diagnosis.
    Skips var() indirect references.
    """
    colors: dict[str, str] = {}
    # hex → list of theme scopes where this value appears (for diagnosis)
    color_themes: dict[str, list] = {}
    font_sizes: dict[str, str] = {}
    font_weights: dict[str, str] = {}
    border_radii: dict[str, str] = {}
    font_family: str | None = None

    pattern = re.compile(r'(--[\w-]+)\s*:\s*([^;]+);')
    for m in pattern.finditer(css_text):
        name = m.group(1).strip()
        value = m.group(2).strip()

        if value.startswith('var('):
            continue

        token_ref = f"var({name})"

        if re.match(r'^#[0-9a-f]{3,8}$', value.lower()):
            key = _normalize_hex(value)
            scope = _extract_scope(css_text, m.start())
            if key not in colors:
                colors[key] = token_ref
            # Track all scopes for this hex value
            if key not in color_themes:
                color_themes[key] = []
            if scope not in color_themes[key]:
                color_themes[key].append(scope)

        elif value.lower().startswith('rgb'):
            key = _normalize_rgba(value)
            if key and key not in colors:
                colors[key] = token_ref
                scope = _extract_scope(css_text, m.start())
                if key not in color_themes:
                    color_themes[key] = []
                if scope not in color_themes[key]:
                    color_themes[key].append(scope)

        elif re.match(r'^\d+(\.\d+)?px$', value) and 'font-size' in name:
            if value not in font_sizes:
                font_sizes[value] = token_ref

        elif re.match(r'^\d+$', value) and 'font-weight' in name:
            if value not in font_weights:
                font_weights[value] = token_ref

        elif re.match(r'^\d+(\.\d+)?(px|%)$', value) and 'border-radius' in name:
            if value not in border_radii:
                border_radii[value] = token_ref

        elif 'font-family' in name and font_family is None:
            font_family = token_ref

    return {
        "fetched_at": int(time.time()),
        "colors": colors,
        "color_themes": color_themes,
        "font_sizes": font_sizes,
        "font_weights": font_weights,
        "border_radii": border_radii,
        "font_family": font_family,
    }


_CSS_RAW_FILENAME = "design-css.txt"  # raw CSS text, kept for cache re-parsing


def load_tokens(cache_dir: Path) -> dict:
    """
    Load design tokens from cache if fresh (< 24h) AND schema is current.
    Falls back to re-parsing saved raw CSS before fetching from URL.
    Returns empty map when DESIGN_TOKEN_CSS_URL is not configured, or on network failure.
    No exception is raised.
    """
    empty: dict = {"colors": {}, "color_themes": {}, "font_sizes": {}, "font_weights": {}, "border_radii": {}, "font_family": None}

    # Skip entirely if no token CSS URL is configured
    if not _TOKEN_CSS_URL:
        return empty

    cache_file = cache_dir / _CACHE_FILENAME
    raw_css_file = cache_dir / _CSS_RAW_FILENAME

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            is_fresh = time.time() - cached.get("fetched_at", 0) < _CACHE_TTL
            has_color_themes = "color_themes" in cached
            if is_fresh and has_color_themes:
                return cached
            # Cache exists but schema is outdated — re-parse raw CSS if available
            if raw_css_file.exists():
                data = _parse_css(raw_css_file.read_text(encoding="utf-8"))
                cache_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                return data
        except Exception:
            pass

    try:
        with urllib.request.urlopen(_TOKEN_CSS_URL, timeout=10) as resp:
            css_text = resp.read().decode("utf-8")
        data = _parse_css(css_text)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        raw_css_file.write_text(css_text, encoding="utf-8")
        return data
    except Exception:
        return empty


def _resolve_css_dict(css: dict, tokens: dict) -> None:
    """In-place replace raw CSS values with BDS token var() references."""
    colors = tokens.get("colors", {})
    font_sizes = tokens.get("font_sizes", {})
    font_weights = tokens.get("font_weights", {})
    border_radii = tokens.get("border_radii", {})
    font_family = tokens.get("font_family")

    for prop in list(css.keys()):
        val = css[prop]
        if not isinstance(val, str):
            continue

        if prop in _COLOR_PROPS:
            stripped = val.strip()
            if stripped.startswith("#"):
                norm = _normalize_hex(stripped)
                resolved = colors.get(norm)
                if not resolved:
                    resolved = _fuzzy_match_color(norm, colors)
                if resolved:
                    if _is_interaction_token(resolved):
                        pass  # keep raw value
                    elif prop == "color" and _is_bg_semantic_token(resolved):
                        pass  # keep raw hex
                    elif prop == "background-color" and _is_bg_fill_token(resolved):
                        pass  # keep raw hex — bg-fill tokens are theme-sensitive
                    else:
                        css[prop] = resolved
            elif stripped.lower().startswith("rgb"):
                resolved = colors.get(_normalize_rgba(stripped) or stripped)
                if resolved:
                    if _is_interaction_token(resolved):
                        pass  # keep raw rgba
                    elif prop == "color" and _is_bg_semantic_token(resolved):
                        pass  # keep raw hex
                    elif prop == "background-color" and _is_bg_fill_token(resolved):
                        pass  # keep raw hex
                    else:
                        css[prop] = resolved

        elif prop in _FONT_SIZE_PROPS:
            resolved = font_sizes.get(val.strip())
            if resolved:
                css[prop] = resolved

        elif prop in _FONT_WEIGHT_PROPS:
            resolved = font_weights.get(str(val).strip())
            if resolved:
                css[prop] = resolved

        elif prop in _FONT_FAMILY_PROPS:
            if font_family and "inter" in val.lower():
                css[prop] = font_family

        elif prop in _BORDER_RADIUS_PROPS:
            stripped = val.strip()
            # Only map single-value radius — "4px 8px 4px 8px" stays as-is
            if " " not in stripped:
                resolved = border_radii.get(stripped)
                if resolved:
                    css[prop] = resolved


def resolve_ir_tokens(ir: dict, tokens: dict) -> None:
    """Recursively apply BDS token resolution to all CSS dicts in an IR tree (in-place)."""
    if not tokens.get("colors") and not tokens.get("font_sizes"):
        return  # empty map — skip traversal entirely
    css = ir.get("css")
    if isinstance(css, dict):
        _resolve_css_dict(css, tokens)
    for child in ir.get("children", []):
        resolve_ir_tokens(child, tokens)


def _collect_ir_colors(ir: dict, acc: list) -> None:
    """Collect all raw hex color values still in IR CSS (pre-resolution)."""
    css = ir.get("css") or {}
    for prop, val in css.items():
        if prop in _COLOR_PROPS and isinstance(val, str):
            stripped = val.strip()
            if stripped.startswith('#'):
                acc.append(_normalize_hex(stripped))
            elif stripped.lower().startswith('rgb'):
                acc.append(stripped)
    for child in ir.get("children", []):
        _collect_ir_colors(child, acc)


def diagnose_design_theme(ir: dict, tokens: dict) -> dict:
    """
    Diagnose the theme the Figma design was created in by comparing color values
    against BDS token scopes.

    Returns:
        {
            "design_theme": "dark" | "light" | "root" | "unknown",
            "confidence": 0.0–1.0,
            "votes": {"dark": N, "light": N, "root": N, "other": N, "unmatched": N},
            "mismatch": bool,  # True if design_theme differs from runtime_theme
            "runtime_theme": str,
        }
    """
    color_themes = tokens.get("color_themes", {})
    colors_map = tokens.get("colors", {})
    if not color_themes:
        return {"design_theme": "unknown", "confidence": 0.0, "votes": {}, "mismatch": False, "runtime_theme": "unknown"}

    # Collect raw hex values from IR before they get replaced
    raw_colors: list = []
    _collect_ir_colors(ir, raw_colors)

    votes: dict[str, int] = {"dark": 0, "light": 0, "root": 0, "other": 0, "unmatched": 0}
    for hex_val in raw_colors:
        scopes = color_themes.get(hex_val)
        if not scopes:
            # Hex not in any BDS token — skip
            if hex_val not in colors_map:
                votes["unmatched"] += 1
            continue
        # A hex value may appear in multiple scopes (e.g. root + dark);
        # credit the most specific theme scope (dark/light > root > other).
        for priority in ("dark", "light", "root", "other"):
            if priority in scopes:
                votes[priority] += 1
                break

    total = votes["dark"] + votes["light"] + votes["root"]
    if total == 0:
        return {"design_theme": "unknown", "confidence": 0.0, "votes": votes, "mismatch": False, "runtime_theme": "unknown"}

    # Determine design theme: whichever has most votes (dark/light wins over root)
    if votes["dark"] > votes["light"]:
        design_theme = "dark"
        confidence = votes["dark"] / total
    elif votes["light"] > votes["dark"]:
        design_theme = "light"
        confidence = votes["light"] / total
    else:
        design_theme = "root"
        confidence = votes["root"] / total if total > 0 else 0.0

    return {
        "design_theme": design_theme,
        "confidence": round(confidence, 2),
        "votes": votes,
        "mismatch": False,  # caller fills in after comparing with runtime
        "runtime_theme": "unknown",
    }


# Container node types that can receive theme-override-* class
_CONTAINER_TYPES = frozenset({"FRAME", "COMPONENT", "INSTANCE", "GROUP", "COMPONENT_SET"})

# 只有 static token（不跟随 data-theme 变化的固定色值）才触发反色标记。
# 自适应 token 跟随页面主题变化，不需要反色。
# 添加你的设计系统中的静态黑/白色 token 名称。
_STATIC_DARK_TOKENS: frozenset = frozenset()
_STATIC_LIGHT_TOKENS: frozenset = frozenset()


def _hex_luminance(hex_str: str) -> float | None:
    """计算 hex 颜色的相对亮度 (0~1)。"""
    h = hex_str.lstrip("#").lower()
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    if len(h) < 6:
        return None
    r = int(h[0:2], 16) / 255
    g = int(h[2:4], 16) / 255
    b = int(h[4:6], 16) / 255
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _bg_theme_category(bg: str, raw_bg_hex: str | None = None) -> str | None:
    """判断背景色属于哪个主题。返回 'dark'/'light'/None。

    优先用 raw_bg_hex（Figma 实际渲染色值）做 luminance 判断。
    当无 raw_bg_hex 时，尝试直接解析 hex/rgb 色值。
    """
    stripped = bg.strip()

    # 固定色 token（不随主题变化）跳过判断。
    # 如有设计系统特有的固定色 token 名称前缀，可在此添加检测。
    if 'var(--bds-brand-' in stripped or 'var(--Brand-' in stripped:
        return None

    # 特殊极性反转（某些文字色 token 在亮色模式下为深色值）。
    # 此处为设计系统特有逻辑，外部用户可根据自身 token 命名规范调整。
    _invert = (stripped.startswith("var(--bds-gray-t")
               and not '-trans-' in stripped)

    # 优先用 Figma 实际色值判断
    if raw_bg_hex:
        lum = _hex_luminance(_normalize_hex(raw_bg_hex))
        if lum is not None:
            if _invert:
                # 文字色 token：白色 rawBgHex = 暗色模式设计 → 'dark'
                if lum >= 0.6:
                    return 'dark'
                if lum < 0.3:
                    return 'light'
            else:
                if lum < 0.3:
                    return 'dark'
                if lum >= 0.6:
                    return 'light'
        return None

    if stripped.startswith("#"):
        lum = _hex_luminance(_normalize_hex(stripped))
        if lum is not None:
            if lum < 0.3:
                return 'dark'
            if lum >= 0.6:
                return 'light'
        return None

    if stripped.lower().startswith("rgb"):
        norm = _normalize_rgba(stripped)
        if norm:
            m = re.match(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', norm)
            if m:
                r = int(m.group(1)) / 255
                g = int(m.group(2)) / 255
                b = int(m.group(3)) / 255
                lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                if lum < 0.3:
                    return 'dark'
                if lum >= 0.6:
                    return 'light'
        return None

    if stripped.startswith("var(--bds-"):
        # 没有 raw_bg_hex 时无法判断自适应 token 的实际色值，不标记
        return None

    if 'gradient' in stripped.lower():
        _hex_m = re.search(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b', stripped)
        if _hex_m:
            lum = _hex_luminance(_normalize_hex(_hex_m.group(0)))
            if lum is not None:
                if lum < 0.3:
                    return 'dark'
                if lum >= 0.6:
                    return 'light'
    return None


def tag_mixed_theme_nodes(ir: dict, page_theme: str, **_kwargs) -> None:
    """遍历 IR 树，标记与页面主题相反的容器节点。

    纯 luminance 判断：背景色亮度与页面主题相反即标记 themeOverride。
    标记后子节点继承，不再重复标记。
    """
    if page_theme not in ("dark", "light"):
        return

    opposite = "light" if page_theme == "dark" else "dark"

    def walk(node: dict, inside_override: bool = False) -> None:
        if inside_override:
            for child in node.get("children", []):
                walk(child, True)
            return

        figma_type = node.get("figmaType", "")
        css = node.get("css") or {}
        bg = css.get("background-color") or css.get("background")
        raw_bg_hex = css.get("_rawBgHex")

        tagged = False
        if figma_type in _CONTAINER_TYPES and bg and isinstance(bg, str):
            cat = _bg_theme_category(bg, raw_bg_hex)
            if cat == opposite:
                node["themeOverride"] = opposite
                tagged = True

        # Also check H5 responsive background overrides (e.g. light page where a section
        # has white PC background but dark #000000 H5 responsive background).
        # Real case: EU Deposit Campaign Hero 250:2620 — PC bg=#ffffff, H5 bg=#000000.
        if not tagged and figma_type in _CONTAINER_TYPES:
            for resp in (node.get("responsive") or []):
                resp_css = resp.get("css") or {}
                resp_bg = resp_css.get("background-color") or resp_css.get("backgroundColor")
                resp_raw = resp_css.get("_rawBgHex")
                if resp_bg and isinstance(resp_bg, str):
                    resp_cat = _bg_theme_category(resp_bg, resp_raw)
                    if resp_cat == opposite:
                        node["themeOverride"] = opposite
                        tagged = True
                        break

        for child in node.get("children", []):
            walk(child, tagged)

    # Skip the root node itself (it represents the whole page)
    for child in ir.get("children", []):
        walk(child, False)

    # Second pass: propagate themeOverride to sibling nodes that visually overlap
    def propagate_to_overlapping_siblings(node: dict) -> None:
        children = node.get("children") or []
        if not children:
            return

        tagged_ranges = []
        for ch in children:
            override = ch.get("themeOverride")
            if not override:
                continue
            css = ch.get("css") or {}
            top_str = css.get("top", "")
            height_str = css.get("height") or css.get("min-height") or ""
            top_m = re.match(r'(-?\d+(?:\.\d+)?)', str(top_str))
            height_m = re.match(r'(-?\d+(?:\.\d+)?)', str(height_str))
            if top_m and height_m:
                t = float(top_m.group(1))
                h = float(height_m.group(1))
                tagged_ranges.append((t, t + h, override))

        for ch in children:
            if ch.get("themeOverride"):
                continue
            css = ch.get("css") or {}
            bg = (css.get("background-color") or css.get("background") or "").strip()
            if bg and bg not in ("transparent", "none", "rgba(0, 0, 0, 0)"):
                continue
            top_str = css.get("top", "")
            top_m = re.match(r'(-?\d+(?:\.\d+)?)', str(top_str))
            if not top_m:
                continue
            top = float(top_m.group(1))
            for rng_top, rng_bot, override in tagged_ranges:
                if rng_top <= top <= rng_bot:
                    ch["themeOverride"] = override
                    break

        for ch in children:
            propagate_to_overlapping_siblings(ch)

    propagate_to_overlapping_siblings(ir)


# ── BDS text-token revert for background-color ─────────────────────────────

# ── BDS text-token revert for background-color ─────────────────────────────

_T1_TITLE_DARK = '#ffffff'
_T1_TITLE_LIGHT = '#121214'


def _revert_text_token_for_bg(value: str, raw_hex: str = '') -> str:
    """bds-gray-t1-title 用作 background-color 时追加 -revert。

    设计稿颜色值与 token 的 dark/light 模式值比对：一致则不反色，不一致则加 -revert。
    """
    if 'bds-gray-t1-title' not in value:
        return value
    if raw_hex:
        normalized = raw_hex.lower().strip()
        if normalized in (_T1_TITLE_DARK, _T1_TITLE_LIGHT):
            return value
    return value.replace('bds-gray-t1-title', 'bds-gray-t1-title-revert')
