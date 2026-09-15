from __future__ import annotations
#!/usr/bin/env python3
"""
Figma → React 转换入口

用法:
  python3 scripts/convert.py 'https://www.figma.com/design/AbCd123/Name?node-id=42-100'
  python3 scripts/convert.py 'https://...' --css-ext=less
  python3 scripts/convert.py 'https://...' --from-raw-data   # 跳过 API，从 1-raw-data/ 重跑 IR 提取
  python3 scripts/convert.py 'https://...' --from-ir      # 跳过 API + IR 提取，直接从 2-figma-extract/ 重生成代码
  python3 scripts/convert.py 'https://...' --skip-assets --public-dir=public/assets
  python3 scripts/convert.py 'https://...' --clamp         # 启用 clamp()（默认不启用，输出固定 px）
  python3 scripts/convert.py 'https://...' --i18n          # 启用 i18n 文本提取（默认不提取）

模式说明（切入点不同）:
  正常模式        : Figma API → 1-raw-data/ → 2-figma-extract/ → 3-page-code/
  --from-raw-data : 跳过 API   →   1-raw-data/ → 2-figma-extract/ → 3-page-code/
  --from-ir    : 跳过 API + IR 提取         →  2-figma-extract/ → 3-page-code/

Token 读取顺序:
  1. ~/.claude/figma-token 文件
  2. FIGMA_ACCESS_TOKEN 环境变量
  均无则停止并提示
"""

import sys
import os
import json
import re
import subprocess
import threading
from typing import Optional
import time
from pathlib import Path
from urllib import request as urllib_request, parse as urllib_parse

# ─── Lib imports ──────────────────────────────────────────────────────
# Add scripts/ dir to sys.path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.ir_builder import build_ir
from lib.tsx_generator import (
    generate_tsx, generate_index, detect_page_theme,
    collect_text_tokens, generate_texts_file, generate_texts_defaults_file,
    parse_existing_texts_keys,
)
from lib.scss_generator import generate_scss
from lib.normalize_classes import normalize_class_names_in_place
from lib.brand_replace import brand_replace_ir, rename_staging_dirs
from lib.token_resolver import tag_mixed_theme_nodes
from lib.figma_vars import load_figma_var_map, load_figma_style_map
from lib.css_extractor import clear_var_miss, get_var_miss
from lib.color import px, should_adaptive
from lib.paths import (
    BASE_DIR, RAW_DATA_DIR, ASSETS_STAGING_DIR, IR_DIR, SCSS_VARS_PATH,
    ir_file, raw_data_file, semantic_file, code_connect_file, var_miss_file,
    INDEX_TS_FILENAME, texts_ts_filename, texts_defaults_ts_filename,
    page_code_subdir, to_pascal,
)


# ─── Utilities ────────────────────────────────────────────────────────

def chunk(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _px(val, fallback: float = 0) -> float:
    """Safely parse a CSS pixel value to float. Returns fallback for % / auto / None."""
    if not val:
        return fallback
    s = str(val).strip()
    if s.endswith('%') or s in ('auto', 'fit-content', 'max-content', 'min-content'):
        return fallback
    try:
        return float(s.replace('px', ''))
    except (ValueError, TypeError):
        return fallback


def _collect_fid_to_class(ir: dict) -> dict:
    """Walk resolved IR, collect figmaId → className mapping."""
    result = {}
    def _walk(node):
        fid = node.get('figmaId', '')
        sem = node.get('semantic') or {}
        cls = sem.get('className', '')
        if fid and cls:
            result[fid] = cls
        for child in (node.get('children') or []):
            _walk(child)
    _walk(ir)
    return result


def _collect_fid_to_src(ir: dict) -> dict:
    """Walk resolved IR, collect figmaId → localAssetPath mapping."""
    result = {}
    def _walk(node):
        fid = node.get('figmaId', '')
        src = node.get('localAssetPath', '')
        if fid and src:
            result[fid] = src
        for child in (node.get('children') or []):
            _walk(child)
    _walk(ir)
    return result


# ─── Argument parsing ─────────────────────────────────────────────────

def parse_args():
    raw_args = sys.argv[1:]

    # First non-flag arg is the Figma URL
    figma_url = next((a for a in raw_args if not a.startswith('--')), None)

    flags = {}
    for a in (a for a in raw_args if a.startswith('--')):
        stripped = a.lstrip('-')
        if '=' in stripped:
            k, v = stripped.split('=', 1)
        else:
            k, v = stripped, 'true'
        flags[k] = v

    parsed = parse_figma_url(figma_url) if figma_url else None

    file_key = (parsed or {}).get('file_key') or flags.get('fileKey') or flags.get('file-key') or None
    node_id  = (parsed or {}).get('node_id')  or flags.get('nodeId')  or flags.get('node-id')  or None
    css_ext  = flags.get('css-ext', 'less')
    skip_assets  = flags.get('skip-assets') == 'true'
    from_ir    = flags.get('from-ir') == 'true'
    from_raw_data = flags.get('from-raw-data') == 'true'
    public_dir_flag = flags.get('public-dir')
    split_mode = flags.get('split') == 'true'
    no_clamp = flags.get('clamp') != 'true'
    enable_i18n = flags.get('i18n') == 'true'

    return {
        'figma_url':       figma_url,
        'parsed':          parsed,
        'file_key':        file_key,
        'node_id':         node_id,
        'css_ext':         css_ext,
        'skip_assets':     skip_assets,
        'from_ir':      from_ir,
        'from_raw_data':   from_raw_data,
        'public_dir_flag': public_dir_flag,
        'split_mode':      split_mode,
        'no_clamp':        no_clamp,
        'enable_i18n':     enable_i18n,
    }


# ─── Figma URL parser ─────────────────────────────────────────────────

def parse_figma_url(url: str):
    if not url:
        return None
    try:
        from urllib.parse import urlparse, parse_qs
        u = urlparse(url)
        m = re.search(r'/(?:design|file)/([^/]+)', u.path)
        if not m:
            return None
        file_key = m.group(1)
        qs = parse_qs(u.query)
        node_id_raw = qs.get('node-id', [''])[0]
        node_id = node_id_raw.replace('-', ':')
        return {'file_key': file_key, 'node_id': node_id or None}
    except Exception:
        return None


# ─── Token loading ────────────────────────────────────────────────────

def load_figma_token() -> Optional[str]:
    token_file = Path.home() / '.claude' / 'figma-token'
    if token_file.exists():
        t = token_file.read_text().strip()
        if t:
            return t
    return os.environ.get('FIGMA_ACCESS_TOKEN')


# ─── HTTP helpers ─────────────────────────────────────────────────────

def figma_get(url: str, token: str) -> dict:
    req = urllib_request.Request(url, headers={'X-Figma-Token': token})
    with urllib_request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())



# ─── Figma node fetch ─────────────────────────────────────────────────

def fetch_figma_node(file_key: str, node_id: str, token: str, raw_data_dir: Path) -> Optional[dict]:
    encoded = urllib_parse.quote(node_id)
    url = f'https://api.figma.com/v1/files/{file_key}/nodes?ids={encoded}'
    data = figma_get(url, token)

    raw_data_dir.mkdir(parents=True, exist_ok=True)
    safe = node_id.replace(':', '-')
    raw_data_file(safe).write_text(json.dumps(data, indent=2))

    doc = data.get('nodes', {})
    node = doc.get(node_id) or doc.get(node_id.replace(':', '-')) or {}
    return node.get('document')


# ─── Sass var map ─────────────────────────────────────────────────────

def load_sass_var_map() -> dict:
    var_path = SCSS_VARS_PATH
    if not var_path.exists():
        return {}
    result = {}
    last_id = ''
    for line in var_path.read_text().split('\n'):
        m = re.search(r'//\s*figma-id:\s*(.+)', line)
        if m:
            last_id = m.group(1).strip()
        m2 = re.match(r'\s*(\$[\w-]+)\s*:', line)
        if m2 and last_id:
            result[last_id] = m2.group(1)
            last_id = ''
    return result


# ─── Asset downloading ────────────────────────────────────────────────

def download_file(url: str, dest: Path, retries: int = 3):
    for attempt in range(retries):
        try:
            subprocess.run(
                ['curl', '-sSL', '--max-time', '30', '-o', str(dest), url],
                check=True,
            )
            return
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1 * (attempt + 1))


def fetch_export_urls(fk: str, ids: list, fmt: str, token: str) -> dict:
    result = {}
    for batch in chunk(ids, 100):
        encoded = urllib_parse.quote(','.join(batch))
        url = f'https://api.figma.com/v1/images/{fk}?ids={encoded}&format={fmt}&scale=2'
        for attempt in range(3):
            try:
                data = figma_get(url, token)
                result.update(data.get('images', {}))
                break
            except Exception as e:
                if attempt == 2:
                    print(f'       ⚠  Figma images API 失败（3次重试后放弃）: {e}')
                else:
                    print(f'       ⚠  Figma images API 错误（第{attempt+1}/3次）: {e}，{2**attempt}s 后重试')
                    time.sleep(2 ** attempt)
    return result


def fetch_image_fill_urls(fk: str, token: str) -> dict:
    for attempt in range(3):
        try:
            data = figma_get(f'https://api.figma.com/v1/files/{fk}/images', token)
            return data.get('meta', {}).get('images', {})
        except Exception as e:
            if attempt == 2:
                return {}
            time.sleep(2 ** attempt)
    return {}


def collect_asset_nodes(ir: dict, acc: Optional[dict] = None) -> dict:
    if acc is None:
        acc = {'vectors': [], 'images': [], 'bg_nodes': []}
    if ir.get('isVectorNode'):
        acc['vectors'].append(ir)
    elif ir.get('isImageNode'):
        acc['images'].append(ir)
    elif ir.get('fillImageRef'):
        acc['bg_nodes'].append(ir)
    for c in ir.get('children', []):
        collect_asset_nodes(c, acc)
    return acc



def fetch_and_embed_assets(ir: dict, fk: str, comp_name: str, public_dir: Path, token: str):
    assets_dir = public_dir / comp_name
    url_prefix = f'/assets/{comp_name}'
    acc = collect_asset_nodes(ir)

    if not acc['vectors'] and not acc['images'] and not acc['bg_nodes']:
        return

    print(f"       → 资源: {len(acc['vectors'])} SVG图标 + {len(acc['images'])} 图片 + {len(acc['bg_nodes'])} 背景图")
    assets_dir.mkdir(parents=True, exist_ok=True)

    # SVG vectors
    if acc['vectors']:
        ids = list({v['figmaId'] for v in acc['vectors']})
        url_map = fetch_export_urls(fk, ids, 'svg', token)
        for fid, url in url_map.items():
            if not url:
                continue
            safe = re.sub(r'[:/;]', '-', fid)
            dest = assets_dir / f'{safe}.svg'
            try:
                if not dest.exists():
                    download_file(url, dest)
                for n in acc['vectors']:
                    if n['figmaId'] == fid:
                        n['localAssetPath'] = f'{url_prefix}/{safe}.svg'
            except Exception as e:
                print(f'       ⚠  SVG 下载失败 {fid}: {e}')

    # PNG images
    if acc['images']:
        ids = list({n['figmaId'] for n in acc['images']})
        url_map = fetch_export_urls(fk, ids, 'png', token)
        for fid, url in url_map.items():
            if not url:
                continue
            safe = re.sub(r'[:/;]', '-', fid)
            dest = assets_dir / f'{safe}.png'
            try:
                if not dest.exists():
                    download_file(url, dest)
                for n in acc['images']:
                    if n['figmaId'] == fid:
                        n['localAssetPath'] = f'{url_prefix}/{safe}.png'
            except Exception as e:
                print(f'       ⚠  图片下载失败 {fid}: {e}')

    # Background images via image fill refs
    if acc['bg_nodes']:
        fill_urls = fetch_image_fill_urls(fk, token)
        for n in acc['bg_nodes']:
            url = fill_urls.get(n.get('fillImageRef', ''))
            if not url:
                continue
            safe = re.sub(r'[^a-zA-Z0-9]', '-', n['fillImageRef'])
            dest = assets_dir / f'{safe}.png'
            try:
                if not dest.exists():
                    download_file(url, dest)
                public_url = f'{url_prefix}/{safe}.png'
                css = n.get('css', {})
                if css.get('background', '').find("url('')") != -1:
                    css['background'] = css['background'].replace("url('')", f"url('{public_url}')")
                if css.get('_before:background', '').find("url('')") != -1:
                    css['_before:background'] = css['_before:background'].replace("url('')", f"url('{public_url}')")
            except Exception as e:
                print(f'       ⚠  背景图下载失败: {e}')


def resolve_asset_paths(node: dict, comp_name: str, public_dir: Path):
    assets_dir = public_dir / comp_name
    url_prefix = f'/assets/{comp_name}'

    def walk(n):
        if not n.get('localAssetPath'):
            safe = re.sub(r'[:/;]', '-', n.get('figmaId', ''))
            if n.get('isVectorNode'):
                # Always assign path regardless of file existence:
                # path is deterministic (nodeId-based); whether asset exists is the asset
                # pipeline's concern. Without this, --skip-assets causes <div> fallback.
                n['localAssetPath'] = f'{url_prefix}/{safe}.svg'
            elif n.get('isImageNode'):
                n['localAssetPath'] = f'{url_prefix}/{safe}.png'
            elif n.get('fillImageRef'):
                safe_fill = re.sub(r'[^a-zA-Z0-9]', '-', n['fillImageRef'])
                p = assets_dir / f'{safe_fill}.png'
                css = n.get('css', {})
                if p.exists() and "url('')" in css.get('background', ''):
                    css['background'] = css['background'].replace(
                        "url('')", f"url('{url_prefix}/{safe_fill}.png')"
                    )
        for c in n.get('children', []):
            walk(c)

    walk(node)



def apply_fallback_semantics(ir: dict) -> dict:
    return {
        **ir,
        'semantic': _fallback(ir),
        'children': [apply_fallback_semantics(c) for c in ir.get('children', [])],
    }


def _fallback(ir: dict) -> dict:
    name = re.sub(r'[^a-zA-Z0-9-_]', '', ir.get('figmaName', '').replace(' ', '-')).lower()
    return {
        'htmlTag': 'span' if ir.get('isTextNode') else 'img' if ir.get('isImageNode') else 'div',
        'componentName': to_pascal(name) if ir.get('figmaType') == 'COMPONENT' else None,
        'className': name or f'node-{(ir.get("figmaId") or "").replace(":", "-")}',
        'isExtractedComponent': ir.get('figmaType') == 'COMPONENT',
        'props': [],
    }


# ─── patch_flex_shrink ────────────────────────────────────────────────
# Full port of the JS patchFlexShrink function (patches 1–22)

def patch_flex_shrink(node: dict):
    css = node.get('css') or {}
    is_flex_row = css.get('display') == 'flex' and css.get('flex-direction') == 'row'
    is_flex_col = css.get('display') == 'flex' and (
        css.get('flex-direction') == 'column' or not css.get('flex-direction')
    )
    # We need is_flex_col to be strictly 'column' for patch 2 to match JS behaviour
    is_flex_col_strict = css.get('display') == 'flex' and css.get('flex-direction') == 'column'

    for child in node.get('children') or []:
        ccss = child.get('css') or {}

        if ccss.get('position') != 'absolute':
            # Patch 1: FIXED width in flex-row → flex-shrink:0
            if is_flex_row and (ccss.get('width') or '').endswith('px') and \
                    not ccss.get('flex') and not ccss.get('flex-grow'):
                ccss['flex-shrink'] = '0'

            # Patch 1: FIXED height in flex-col → flex-shrink:0
            if is_flex_col_strict and (ccss.get('height') or '').endswith('px') and \
                    not ccss.get('flex') and not ccss.get('flex-grow'):
                ccss['flex-shrink'] = '0'

            # Patch 2: column + flex:1 + height:px → remove flex:1, set align-self:stretch
            if is_flex_col_strict and ccss.get('flex') == '1' and (ccss.get('height') or '').endswith('px'):
                ccss.pop('flex', None)
                ccss['align-self'] = 'stretch'

        # Patch 4: TEXT WIDTH_AND_HEIGHT → white-space:nowrap
        if child.get('isTextNode') and child.get('textAutoResize') == 'WIDTH_AND_HEIGHT' and \
                not ccss.get('white-space'):
            ccss['white-space'] = 'nowrap'

        # Patch 5: isVectorNode + background-color → remove background-color.
        # All isVectorNode=True nodes get exported as SVG assets (resolve_asset_paths
        # always assigns a localAssetPath for them). The SVG embeds the fill color, so
        # CSS background-color would fill the transparent bounding box of the <img>
        # element and produce a wrong visual (fills rect, not the vector path shape).
        # Note: resolve_asset_paths runs AFTER this patch, so localAssetPath is not
        # yet set here — we use isVectorNode as the reliable indicator instead.
        if (child.get('isVectorNode') and 'background-color' in ccss):
            del ccss['background-color']

        # Patch 8: isDecorativeElement + isImageNode + not explicitly positioned → restore
        # A node with position:absolute was intentionally positioned (even if top/left=0
        # got omitted by css_extractor); only restore nodes that have no position at all.
        if child.get('isDecorativeElement') and child.get('isImageNode') and \
                not ccss.get('position') and \
                'top' not in ccss and 'left' not in ccss:
            child.pop('isDecorativeElement', None)
            ccss.pop('pointer-events', None)

        # Patch 6: decorative in flex parent → position:absolute (except inline vectors)
        if child.get('isDecorativeElement') and not ccss.get('position'):
            parent_is_flex = css.get('display') == 'flex'
            if not (parent_is_flex and child.get('isVectorNode')):
                ccss['position'] = 'absolute'
                if css.get('display') == 'flex' and not css.get('position'):
                    css['position'] = 'relative'

        # Patch 3: decorative/image child > 1.5x parent → overflow:hidden on parent
        # Skip for __ow wrappers: they're created by ir_builder._wrap_overflow_children
        # specifically to let decorative images extend outside a clipsContent=False frame.
        # Adding overflow:hidden here would undo that design and clip the overflow image.
        if (not css.get('overflow')
                and not node.get('figmaId', '').endswith('__ow')
                and (child.get('isDecorativeElement') or child.get('isImageNode'))):
            pw = _px(css.get('width'))
            ph = _px(css.get('height') or css.get('min-height'))
            if ph > 0 or pw > 0:
                cw = _px(ccss.get('width'))
                ch = _px(ccss.get('height'))
                if (ph > 0 and ch > ph * 1.5) or (pw > 0 and cw > pw * 1.5):
                    css['overflow'] = 'hidden'
            # Large absolute image (> 800px) → parent overflow:hidden
            if child.get('isImageNode') and ccss.get('position') == 'absolute':
                img_w = _px(ccss.get('width'))
                img_h = _px(ccss.get('height'))
                if (img_w > 800 or img_h > 800) and not css.get('overflow'):
                    css['overflow'] = 'hidden'

        # CENTER constraint large background image (left:50%/calc(50%+N)+translateX, > 800px)
        # Root component uses width:100% (responsive), so pixel-exact Figma coords break
        # at non-1440px viewports. Any CENTER-constrained large bg image → full-width CSS.
        if child.get('isImageNode') and ccss.get('position') == 'absolute':
            img_w = _px(ccss.get('width'))
            img_h = _px(ccss.get('height'))
            parent_w = _px(css.get('width'))
            parent_has_no_width = not css.get('width') or parent_w >= 1000
            if (img_w > 800 or img_h > 800) and parent_has_no_width:
                left_val = ccss.get('left') or ''
                # calc(50% + N) is "near-centered" only when the offset is small relative
                # to the image width (< 15%). A larger offset means intentional positioning,
                # not a background image meant to fill the parent.
                _calc_match = re.match(r'calc\(50% \+ (-?[\d.]+)px\)', left_val)
                _calc_offset = abs(float(_calc_match.group(1))) if _calc_match else 0
                # "near-center" means design-tool alignment noise only (≤10px absolute).
                # A relative threshold (e.g. 15% of image width) is wrong: on a 1376px
                # image it tolerates 206px of intentional offset as if it were rounding.
                _near_center_calc = (
                    _calc_match is not None and
                    _calc_offset <= 10
                )
                is_centered = (
                    (left_val == '50%' or _near_center_calc)
                    and 'translateX(-50%)' in (ccss.get('transform') or '')
                )
                if is_centered and (ccss.get('width') or '').endswith('px'):
                    aspect_ratio = img_w / (img_h or 1)
                    if aspect_ratio > 0.5:
                        # For absolutely-positioned <img> (replaced element), width:auto
                        # uses the intrinsic image width rather than the containing block
                        # width. width:100% on an abs-pos element resolves against the
                        # padding-box of the containing block, so it DOES fill the full
                        # parent width even when the parent has horizontal padding.
                        ccss['width'] = '100%'
                        ccss['left'] = '0'
                        ccss.pop('right', None)
                        ccss['object-fit'] = 'cover'
                        existing_t = ccss.get('transform', '')
                        new_t = re.sub(r'\s*translateX\(-50%\)', '', existing_t).strip()
                        if new_t:
                            ccss['transform'] = new_t
                        else:
                            ccss.pop('transform', None)

        # Patch 23: flow child width corrections based on bb vs. parent geometry.
        # Parses parent padding once and handles two sub-cases:
        #   A) content-area fill: child_bb_w ≈ parent_content_w → width:100%
        #   B) full-width breakout: child_bb_w ≈ parent_bb_w AND parent has LR padding
        #      → calc(100% + padLR) + margin-left:-padLeft  (child breaks out of padding)
        # Root cause examples:
        #   A) 285:43825 (bb=353) inside 285:43787 (bb=393, pad=20px LR, content=353)
        #   B) 285:44342 (bb=393) inside 285:43787 (bb=393, pad=20px LR) needs breakout
        _child_bb_w = (child.get('bb') or {}).get('width', 0)
        _parent_bb_w = (node.get('bb') or {}).get('width', 0)
        if _child_bb_w > 0 and _parent_bb_w > 0:
            _pad_str = css.get('padding', '')
            _pad_parts = [_px(p) for p in (_pad_str or '').split() if p]
            if len(_pad_parts) == 4:
                _pl, _pr = _pad_parts[3], _pad_parts[1]
            elif len(_pad_parts) == 2:
                _pl, _pr = _pad_parts[1], _pad_parts[1]
            elif len(_pad_parts) == 1:
                _pl, _pr = _pad_parts[0], _pad_parts[0]
            else:
                _pl, _pr = 0.0, 0.0
            _parent_content_w = _parent_bb_w - _pl - _pr

            # Sub-case A: fill parent content area (no existing width, no align-self)
            if (ccss.get('position') != 'absolute' and
                    not ccss.get('width') and
                    not ccss.get('align-self') and
                    _parent_content_w > 0 and
                    abs(_child_bb_w - _parent_content_w) <= 1):
                ccss['width'] = '100%'

            # Sub-case B: full-width breakout — child spans full parent bb (past padding)
            # Use explicit bb.width in px (clean, matches design spec directly).
            # With align-items:center on the flex column parent, a 393px child inside a
            # 353px content area centers to: (353-393)/2 = -20px → starts at x=0 from
            # the parent's border edge, naturally breaking out of the 20px padding.
            # No negative margins needed; explicit px is cleaner than calc hacks.
            elif (ccss.get('position') != 'absolute' and
                    ccss.get('width') == '100%' and
                    (_pl + _pr) > 0 and
                    abs(_child_bb_w - _parent_bb_w) <= 1):
                ccss['width'] = f'{int(round(_child_bb_w))}px'

        # Patch 25: flex-row flow child with flex-shrink:0 but no explicit width/height
        # → add bb.width / bb.height as explicit CSS dimensions.
        # Root cause: Pagination items (bb=32x32) in a flex-row have no CSS width;
        # normalize_class_names deduplicates their identical CSS under one class name,
        # so the class CSS needs the right dimensions BEFORE deduplication runs.
        # Guard: only for flex-row parents, only flow children, only when flex-shrink:0
        # is explicitly set (indicating a fixed designed size), not already sized.
        # Skip: child is a flex container with visible content children (HUG content) —
        # its size comes from its children, not from bb dimensions. But leaf flex
        # containers (no children) keep explicit dimensions (e.g. pagination 32x32).
        _child_is_hug_flex = (
            ccss.get('display') == 'flex' and
            any(c.get('visible') is not False for c in (child.get('children') or []))
        )
        _child_is_text = child.get('figmaType') == 'TEXT' or child.get('isTextNode')
        if (css.get('display') == 'flex' and css.get('flex-direction') == 'row' and
                ccss.get('position') != 'absolute' and
                ccss.get('flex-shrink') == '0' and
                not ccss.get('width') and
                not ccss.get('flex') and not ccss.get('flex-grow') and
                not _child_is_hug_flex and
                not _child_is_text):
            _bb_w25 = (child.get('bb') or {}).get('width', 0)
            _bb_h25 = (child.get('bb') or {}).get('height', 0)
            if _bb_w25 > 0:
                ccss['width'] = f'{int(round(_bb_w25))}px'
            if _bb_h25 > 0 and not ccss.get('height') and not ccss.get('min-height'):
                ccss['height'] = f'{int(round(_bb_h25))}px'

        # Patch 24: absolute child with bottom < 0 (extends below parent) + large
        # bottom border-radius → clear bottom corners to prevent visual "露底" gaps.
        # Root cause: 286:66129 (VECTOR rect, h=405, bottom:-1px) has 100px corners
        # all around; the bottom 100px curve is visible within the parent's overflow
        # clip (parent h=397), creating concave notches that expose the background.
        # Figma design intent: the card extends far off-screen, so bottom corners are
        # never visible. In CSS the parent clips at 397px, making them visible.
        if (ccss.get('position') == 'absolute' and
                ccss.get('border-radius') and
                ccss.get('bottom')):
            _bottom_val = _px(ccss.get('bottom'))
            if _bottom_val < 0:  # child extends below parent's bottom edge
                _br = ccss.get('border-radius', '')
                _br_parts = _br.split()
                if len(_br_parts) == 4:  # tl tr br bl
                    ccss['border-radius'] = f'{_br_parts[0]} {_br_parts[1]} 0px 0px'
                elif len(_br_parts) == 1 and _px(_br_parts[0]) > 0:
                    ccss['border-radius'] = f'{_br_parts[0]} {_br_parts[0]} 0px 0px'

        patch_flex_shrink(child)

    # Patch 7: absolute child → parent needs position:relative
    if not css.get('position'):
        if any(c.get('css', {}).get('position') == 'absolute' for c in (node.get('children') or [])):
            css['position'] = 'relative'

    # Patch 13: LINE or thin VECTOR → unset isVectorNode
    is_line_like_vector = node.get('figmaType') == 'VECTOR' and (
        lambda: (
            _px(css.get('width'), 999) <= 1 or
            _px(css.get('height'), 999) <= 1
        )
    )()
    if node.get('figmaType') == 'LINE' or is_line_like_vector:
        if node.get('isVectorNode'):
            node['isVectorNode'] = False

    # Patch 14: clear micro-rotations
    if css.get('transform'):
        rot_match = re.search(r'rotate\((-?[\d.]+)deg\)', css['transform'])
        if rot_match:
            deg = abs(float(rot_match.group(1)))
            is_absolute = css.get('position') == 'absolute'
            is_line = node.get('figmaType') == 'LINE'
            threshold = 0.3 if (not is_line and is_absolute) else 3
            if deg < threshold:
                cleaned = re.sub(r'\s*rotate\(-?[\d.]+deg\)', '', css['transform']).strip()
                if cleaned:
                    css['transform'] = cleaned
                else:
                    del css['transform']

    # Patch 12: remove z-index — except for overflow-wrapper promoted children (_isOWChild)
    # which need z-index:1 to appear above the parent card that follows in DOM order.
    if not node.get('_isOWChild'):
        css.pop('z-index', None)

    # Patch 15: REMOVED — height→min-height logic unified in ir_builder._post_process_frame()
    # which correctly subtracts vertical padding from the min-height value.

    # Patch 22: isVectorNode → remove all border properties
    if node.get('isVectorNode'):
        for prop in ('border', 'border-top', 'border-right', 'border-bottom', 'border-left', 'outline'):
            css.pop(prop, None)

    # Patch 21: small square container (≤64px, border-radius, min-height) → restore height
    if css.get('min-height') and css.get('border-radius'):
        mh = _px(css['min-height'])
        w = _px(css.get('width'))
        if mh > 0 and mh <= 64 and w > 0 and abs(w - mh) < 2:
            css['height'] = css['min-height']
            del css['min-height']
            if not css.get('overflow'):
                css['overflow'] = 'hidden'

    # Patch 9: border-radius + no overflow → overflow:hidden for FRAME/COMPONENT/INSTANCE
    # Skip when a child deliberately extends outside parent bounds (e.g. arrow/caret
    # indicators with bottom:-Xpx) to avoid clipping intentional decorative overflow.
    def _p9_child_overflows():
        for c in (node.get('children') or []):
            ccss = c.get('css') or {}
            for prop in ('bottom', 'top', 'left', 'right'):
                v = ccss.get(prop, '')
                if isinstance(v, str) and v.endswith('px'):
                    try:
                        if float(v[:-2]) < -1:
                            return True
                    except ValueError:
                        pass
        return False
    if not css.get('overflow') and css.get('border-radius') and (node.get('children') or []) and \
            node.get('figmaType') in ('FRAME', 'COMPONENT', 'INSTANCE', 'COMPONENT_SET') and \
            not _p9_child_overflows():
        css['overflow'] = 'hidden'

    # Patch 11: ELLIPSE → border-radius:50%
    if node.get('figmaType') == 'ELLIPSE' and not css.get('border-radius'):
        css['border-radius'] = '50%'

    # Patch 17: absolute child with top far above parent
    if css.get('position'):
        for child in (node.get('children') or []):
            ccss = child.get('css') or {}
            if ccss.get('position') == 'absolute':
                top_val = _px(ccss.get('top'))
                if top_val < -200 and 'gradient' in (ccss.get('background') or ''):
                    ccss['display'] = 'none'
                    if not css.get('overflow'):
                        css['overflow'] = 'hidden'
                elif top_val < -100 and not css.get('overflow'):
                    css['overflow'] = 'hidden'

    # Patch 20: absolute child top > maxBottom * 1.5 + top > 1500 → display:none
    if not css.get('display') or css.get('position') == 'relative':
        abs_kids = [
            c for c in (node.get('children') or [])
            if c.get('css', {}).get('position') == 'absolute' and
            (c.get('css', {}).get('top') or '').endswith('px')
        ]
        if len(abs_kids) >= 2:
            bottoms = []
            for c in abs_kids:
                ccss = c.get('css') or {}
                t = _px(ccss.get('top'))
                h = _px(ccss.get('height') or ccss.get('min-height'))
                bottoms.append(t + h)
            for i, c in enumerate(abs_kids):
                top = _px(c['css'].get('top'))
                others_max = max((b for j, b in enumerate(bottoms) if j != i), default=0)
                if top > others_max * 1.5 and top > 1500:
                    c['css']['display'] = 'none'

    # Patch 18a: flex-row icon+text → align-items:flex-start
    # Guard: only override when Figma EXPLICITLY set counterAxisAlignItems to non-CENTER.
    # None means designer never touched it — respect the geometry-inferred align-items.
    if css.get('display') == 'flex' and css.get('flex-direction') == 'row' and \
            css.get('align-items') == 'center' and \
            node.get('figmaCounterAxisAlign') is not None and \
            node.get('figmaCounterAxisAlign') != 'CENTER':
        flow_kids_18a = [c for c in (node.get('children') or []) if c.get('css', {}).get('position') != 'absolute']
        if len(flow_kids_18a) == 2:
            a, b = flow_kids_18a
            a_css = a.get('css') or {}
            b_css = b.get('css') or {}
            has_img_text = (
                (a_css.get('flex-shrink') == '0' and b_css.get('flex-grow') == '1') or
                (b_css.get('flex-shrink') == '0' and a_css.get('flex-grow') == '1')
            )
            if has_img_text:
                css['align-items'] = 'flex-start'

    # Patch 18b: all flex children with flex-grow:1 → align-items:stretch
    if css.get('display') == 'flex' and css.get('flex-direction') == 'row' and \
            css.get('align-items') == 'center':
        flow_kids_19 = [c for c in (node.get('children') or []) if c.get('css', {}).get('position') != 'absolute']
        if len(flow_kids_19) >= 2 and all(
            c.get('css', {}).get('flex-grow') == '1' and c.get('css', {}).get('display') == 'flex'
            for c in flow_kids_19
        ):
            css['align-items'] = 'stretch'

    # Patch 18: symmetric decorative first/last column-flex siblings
    if css.get('display') == 'flex' and css.get('flex-direction') == 'row':
        flow_kids = [c for c in (node.get('children') or []) if c.get('css', {}).get('position') != 'absolute']
        if len(flow_kids) >= 2:
            first = flow_kids[0]
            last = flow_kids[-1]
            first_css = first.get('css') or {}
            first_w = _px(first_css.get('width'))
            # Fix: require an explicit px width on the first sibling.
            # flex:1 / flex-grow items have no explicit width, so _px returns 0,
            # which would incorrectly satisfy first_w <= 64 and corrupt align-items
            # on the last sibling (e.g. turning center → flex-end on card containers).
            first_has_explicit_px_width = (first_css.get('width') or '').endswith('px')
            if first is not last and \
                    first_css.get('flex-direction') == 'column' and \
                    (last.get('css') or {}).get('flex-direction') == 'column' and \
                    first.get('figmaName') == last.get('figmaName') and \
                    first_has_explicit_px_width and \
                    first_w <= 64:
                (last.get('css') or {})['align-items'] = 'flex-end'

    # Patch 10: absolute-only children → infer parent width/height from child bounds
    children = node.get('children') or []
    if children:
        abs_children = [c for c in children if c.get('css', {}).get('position') == 'absolute']
        flow_children = [c for c in children if c.get('css', {}).get('position') != 'absolute']
        is_all_absolute = len(abs_children) == len(children) and abs_children
        is_flex_with_abs = css.get('display') == 'flex' and abs_children and not css.get('width')
        should_infer = css.get('position') == 'absolute' or is_all_absolute or is_flex_with_abs

        if should_infer:
            all_abs_w = [c for c in abs_children if (c.get('css', {}).get('width') or '').endswith('px')]
            all_abs_h = [c for c in abs_children if (c.get('css', {}).get('height') or '').endswith('px')]
            non_dec_abs   = [c for c in all_abs_w if not c.get('isDecorativeElement')]
            non_dec_abs_h = [c for c in all_abs_h if not c.get('isDecorativeElement')]
            src_abs_w = non_dec_abs if non_dec_abs else ([] if flow_children else all_abs_w)
            src_abs_h = non_dec_abs_h if non_dec_abs_h else ([] if flow_children else all_abs_h)

            flow_widths  = [_px(c['css']['width'])  for c in flow_children if (c.get('css', {}).get('width') or '').endswith('px')]
            flow_heights = [_px(c['css']['height']) for c in flow_children if (c.get('css', {}).get('height') or '').endswith('px')]
            max_flow_w = max(flow_widths,  default=0)
            max_flow_h = max(flow_heights, default=0)

            if is_flex_with_abs:
                abs_widths = []
                for c in src_abs_w:
                    w = _px(c['css']['width'])
                    l_val = c['css'].get('left', '')
                    l = _px(l_val) if (l_val or '').endswith('px') else 0
                    abs_widths.append(l + w)
            else:
                abs_widths = [_px(c['css']['width']) for c in src_abs_w]

            abs_heights = [_px(c['css']['height']) for c in src_abs_h]
            max_abs_w = max(abs_widths,  default=0)
            max_abs_h = max(abs_heights, default=0)

            w_cap = 2000 if is_flex_with_abs else 600
            has_stretch = css.get('align-self') == 'stretch'

            # When is_flex_with_abs=True AND flow children exist, abs children are
            # decorative overlays (e.g. halo rings) that may extend beyond the visible
            # container boundary. Using their l+w inflates the container beyond the
            # design spec. Prefer the node's own bb.width (Figma's ground truth).
            # Example: 285:43827 光圈 rings are 378-389px but container bb is 375px.
            if is_flex_with_abs and flow_children and not css.get('width'):
                bb_w = (node.get('bb') or {}).get('width', 0)
                if bb_w > 0:
                    css['width'] = f'{int(round(bb_w))}px'
            elif not css.get('width') and not has_stretch and max_abs_w > max_flow_w and max_abs_w <= w_cap:
                css['width'] = f'{int(round(max_abs_w))}px'
            # Skip height inference for ANY flex container that has flow children:
            # flow children grow the container naturally; an absolute decorative child
            # (e.g. a large blur Ellipse) must not cap the parent height.
            # The old guard tied this to `is_flex_with_abs` (requires no width), which
            # missed flex containers that DO have an explicit width.
            height_infer_ok = not (css.get('display') == 'flex' and flow_children)
            if not css.get('height') and max_abs_h > max_flow_h and max_abs_h <= 600 and height_infer_ok:
                if css.get('min-height') and len(all_abs_h) > 1:
                    css['height'] = css['min-height']
                    del css['min-height']
                else:
                    css['height'] = f'{int(round(max_abs_h))}px'

    # Ensure css dict is written back (it's already a reference, but be explicit)
    node['css'] = css


# ─── patch_top_gradient_reversal ──────────────────────────────────────
# A gradient overlay at top:0 with transparent→opaque direction creates a
# solid-color band at the overlay's bottom edge, which becomes a visible
# boundary line where the overlay ends and the full image shows below.
# Fix: reverse to opaque→transparent so the overlay bottom blends seamlessly.
_GRAD_T2O_RE = re.compile(
    r'^linear-gradient\(\s*(-?[0-9.]+)deg\s*,\s*'
    r'(rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0(?:\.\d+)?\s*\))\s+(-?[0-9.]+)%\s*,\s*'
    r'(#[0-9a-fA-F]{6})\s+(-?[0-9.]+)%\s*\)$'
)



def patch_top_gradient_reversal(node: dict):
    css = node.get('css') or {}
    bg = css.get('background', '')
    # Skip rotated elements: a side-edge overlay with rotate(-90deg) uses the same
    # transparent→opaque gradient but the rotation means the opaque end is already
    # correctly pointing toward the page background edge.
    if css.get('top') == '0px' and bg.startswith('linear-gradient') and 'transform' not in css:
        m = _GRAD_T2O_RE.match(bg.strip())
        if m:
            deg = m.group(1)
            transparent_color = m.group(2)
            t_pct = float(m.group(3))
            opaque_color = m.group(4)
            o_pct = float(m.group(5))
            new_o_pct = round(100 - o_pct, 2)
            new_t_pct = round(100 - t_pct, 2)
            css['background'] = (
                f'linear-gradient({deg}deg, {opaque_color} {new_o_pct:g}%, '
                f'{transparent_color} {new_t_pct:g}%)'
            )
    for child in node.get('children') or []:
        patch_top_gradient_reversal(child)


# ─── patch_filter_blend_isolation ─────────────────────────────────────
# CSS filter:drop-shadow on a container creates an isolated stacking context,
# causing sibling mix-blend-mode children to blend against transparent instead
# of the actual page background. This breaks additive blend modes like
# plus-lighter that are designed to be invisible on light backgrounds.
# Fix: move filter to non-blend-mode children (typically images) so blend-mode
# siblings blend against the true page background.


def patch_filter_blend_isolation(node: dict):
    css = node.get('css') or {}
    filter_val = css.get('filter', '')
    if filter_val and 'drop-shadow' in filter_val:
        children = node.get('children') or []
        blend_children = [c for c in children if (c.get('css') or {}).get('mix-blend-mode')]
        non_blend_children = [c for c in children if not (c.get('css') or {}).get('mix-blend-mode')]
        if blend_children and non_blend_children:
            del css['filter']
            for child in non_blend_children:
                child_css = child.setdefault('css', {})
                existing = child_css.get('filter', '')
                child_css['filter'] = (f'{existing} {filter_val}'.strip() if existing else filter_val)
    for child in node.get('children') or []:
        patch_filter_blend_isolation(child)


# ─── patch_center_flex_padding_normalize ──────────────────────────────
# When a Figma button component is designed with an icon slot on one side
# (e.g., padding:12px 2px 12px 28px for left-icon), but only text is rendered,
# the asymmetric padding makes text appear off-center despite justify-content:center.
# Fix: for isComponentInstance + justify-content:center + only-text-children +
# highly asymmetric horizontal padding (|left-right| > 12px), normalize to symmetric.
#
# Real case: By_Primary Buttons-Dark (177:13510) from ByAiHub20 (169-33787).
# Figma button designed with [github-icon | text] layout, only text rendered.


def patch_center_flex_padding_normalize(node: dict):
    css = node.get('css') or {}
    if (node.get('isComponentInstance')
            and css.get('display') == 'flex'
            and css.get('justify-content') == 'center'):
        padding = css.get('padding', '')
        parts = padding.split() if padding else []
        if len(parts) == 4:
            try:
                top, right_val, bottom, left_val = [float(p.rstrip('px')) for p in parts]
                if abs(left_val - right_val) > 12:
                    children = node.get('children') or []
                    if children and all(c.get('isTextNode') for c in children):
                        sym = max(left_val, right_val)
                        sym_s = f'{sym:.0f}px' if sym == int(sym) else f'{sym}px'
                        top_s = parts[0]
                        bot_s = parts[2]
                        css['padding'] = f'{top_s} {sym_s} {bot_s} {sym_s}'
            except (ValueError, IndexError):
                pass
    for child in node.get('children') or []:
        patch_center_flex_padding_normalize(child)


# ─── Progress logging ─────────────────────────────────────────────────

_t0 = None
_phase_start: dict = {}


def log(step: int, msg: str):
    global _t0
    now = time.time()
    if step > 1 and (step - 1) in _phase_start:
        elapsed = now - _phase_start[step - 1]
        print(f'       ⏱  {elapsed:.2f}s')
    _phase_start[step] = now
    print(f'  [{step}/4] {msg}')


def log_total():
    now = time.time()
    if _phase_start:
        last = max(_phase_start)
        elapsed = now - _phase_start[last]
        print(f'       ⏱  {elapsed:.2f}s')
    if _t0 is not None:
        print(f'  ⏱  总耗时: {now - _t0:.2f}s')


def _write_var_miss_report(ir_dir: Path, node_id_safe: str, miss_counts: dict, var_id_to_name: dict) -> None:
    """Write variable miss statistics to 2-figma-extract/{nodeId}-var-miss.json.

    Output format: {"total": N, "by_name": {"--figma-var-name": count, ...}}
    Sorted by count descending. Omitted when there are no misses.
    """
    if not miss_counts:
        return
    named = {}
    for var_id, count in miss_counts.items():
        name = var_id_to_name.get(var_id, var_id)
        named[name] = named.get(name, 0) + count
    sorted_named = dict(sorted(named.items(), key=lambda x: -x[1]))
    report = {'total': sum(sorted_named.values()), 'by_name': sorted_named}
    miss_path = var_miss_file(node_id_safe)
    miss_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f'       → 变量映射未命中 {len(sorted_named)} 种（共 {report["total"]} 次）: {miss_path}')


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    global _t0
    _t0 = time.time()

    args = parse_args()
    figma_url    = args['figma_url']
    parsed       = args['parsed']
    file_key     = args['file_key']
    node_id      = args['node_id']
    css_ext      = args['css_ext']
    skip_assets  = args['skip_assets']
    from_ir   = args['from_ir']
    public_dir_flag = args['public_dir_flag']

    # ── Validation ──────────────────────────────────────────────────────
    if not figma_url and not node_id:
        print('\n❌  请提供 Figma URL 或 --nodeId，例：')
        print("     python3 scripts/convert.py 'https://www.figma.com/design/AbCd/Name?node-id=42-100'")
        sys.exit(1)

    if figma_url and not parsed:
        print(f'\n❌  无法解析 Figma URL: {figma_url}')
        print('     URL 格式应为: https://www.figma.com/design/<fileKey>/...?node-id=<id>')
        sys.exit(1)

    if not node_id:
        print('\n❌  无法获取 node-id，请确认 URL 包含 ?node-id=xxx 参数')
        sys.exit(1)

    from_raw_data = args['from_raw_data']

    # Load token always: variables API needs it even in normal mode; assets download
    # needs it even in --from-ir mode (when skip_assets is False).
    figma_token = load_figma_token()

    if not from_ir and not from_raw_data:
        if not file_key:
            print('\n❌  无法获取 fileKey，请确认 URL 包含 /design/<fileKey>/ 部分')
            sys.exit(1)
        if not figma_token:
            print('\n❌  未找到 Figma token，请二选一：')
            print('     1. 将 token 写入文件：echo "figd_xxx" > ~/.claude/figma-token')
            print('     2. 设置环境变量：export FIGMA_ACCESS_TOKEN=figd_xxx')
            sys.exit(1)

    # ── Path setup ──────────────────────────────────────────────────────
    base_dir     = BASE_DIR
    raw_data_dir = RAW_DATA_DIR
    ir_dir       = IR_DIR
    public_dir   = Path(public_dir_flag).resolve() if public_dir_flag else ASSETS_STAGING_DIR.resolve()

    node_id_safe = node_id.replace(':', '-')
    ir_path = ir_file(node_id_safe)

    # When running --from-raw-data or --from-ir without a URL, file_key may be empty.
    # Recover it from cache file names: format is {nodeIdSafe}-{fileKey}-{type}.json.
    if not file_key and (from_raw_data or from_ir):
        _prefix = node_id_safe + '-'
        for _suffix, _type in [('-styles.json', 'styles'), ('-variables.json', 'variables')]:
            for _cache_f in raw_data_dir.glob(f'{node_id_safe}-*{_suffix}'):
                _stem = _cache_f.name  # e.g. "169-33787-5hamPnt...-styles.json"
                _inner = _stem[len(_prefix):]   # "5hamPnt...-styles.json"
                _candidate = _inner[: -(len(_suffix))]  # "5hamPnt..."
                if _candidate:
                    file_key = _candidate
                    break
            if file_key:
                break

    # Fetch Figma file variables in background (parallel with Figma node API)
    _var_id_to_bds: dict = {}
    _var_id_to_name: dict = {}
    def _fetch_vars():
        nonlocal _var_id_to_bds, _var_id_to_name
        _var_id_to_bds, _var_id_to_name = load_figma_var_map(
            file_key or '', figma_token, raw_data_dir, node_id_safe
        )
    vars_thread = threading.Thread(target=_fetch_vars, daemon=True)
    vars_thread.start()

    # Fetch Figma Color Styles in background (parallel — CDN-served, fast ~15s)
    # Covers older designs that use Styles instead of Variables for color semantics.
    _style_id_to_bds: dict = {}
    def _fetch_styles():
        nonlocal _style_id_to_bds
        _style_id_to_bds = load_figma_style_map(
            file_key or '', figma_token, raw_data_dir, node_id_safe
        )
    styles_thread = threading.Thread(target=_fetch_styles, daemon=True)
    styles_thread.start()

    mode_tag = '[from-ir]' if from_ir else ('[from-raw-data]' if from_raw_data else f'({file_key})')
    print(f'\n🔄  转换 {node_id} {mode_tag}\n')

    # ── Step 1: Load or fetch IR ─────────────────────────────────────────
    if from_ir:
        log(1, '从缓存加载 IR...')
        if not ir_path.exists():
            print(f'❌  IR 缓存不存在: {ir_path}')
            print(f'    请先不带 --from-ir 运行一次以生成缓存')
            sys.exit(1)
        ir = json.loads(ir_path.read_text())
        print(f'       → IR 已加载: {ir_path}')
        print(f'       → 节点: {ir.get("figmaName")} ({ir.get("figmaType")})')
    else:
        if from_raw_data:
            log(1, '从 1-raw-data 加载原始数据...')
            raw_path = raw_data_file(node_id_safe)
            if not raw_path.exists():
                print(f'❌  raw-data 缓存不存在: {raw_path}')
                print(f'    请先不带 --from-raw-data 运行一次以下载原始数据')
                sys.exit(1)
            raw = json.loads(raw_path.read_text())
            doc = raw.get('nodes', {})
            node_entry = doc.get(node_id) or doc.get(node_id_safe) or {}
            figma_node = node_entry.get('document')
            if not figma_node:
                print(f'❌  raw-data 中未找到节点 {node_id}')
                sys.exit(1)
            print(f'       → raw-data 已加载: {raw_path}')
        else:
            log(1, '读取 Figma 节点数据...')
            figma_node = fetch_figma_node(file_key, node_id, figma_token, raw_data_dir)
            if not figma_node:
                print(f'❌  节点 {node_id} 未找到')
                sys.exit(1)

        log(2, 'Script Layer：提取 CSS...')
        sass_vars = load_sass_var_map()
        # Wait for variables before building IR — from-raw-data loads in ~0.01s so the
        # background thread may not have finished yet. In normal mode the Figma nodes API
        # call (~3-5s) already provides enough overlap, but joining here is always correct.
        # _LOCAL_TIMEOUT(60s) × _LOCAL_RETRIES(1) + _PUBLISHED_TIMEOUT(15s) + buffer
        vars_thread.join(timeout=82)
        styles_thread.join(timeout=20)  # styles is CDN-served, fast

        # ── Adaptive mode detection (before IR build so ctx propagates) ─────
        _is_adaptive = should_adaptive(figma_node)
        _abb_detect = figma_node.get('absoluteBoundingBox') or {}
        _root_w_detect = figma_node.get('width') or _abb_detect.get('width') or 1440
        _adaptive_clamp = _is_adaptive and _root_w_detect > 768 and not args.get('no_clamp')

        clear_var_miss()
        ir_list = build_ir([figma_node], sass_vars, _var_id_to_bds, _style_id_to_bds, adaptive=_is_adaptive, adaptive_clamp=_adaptive_clamp)
        if not ir_list:
            print('❌  IR 构建失败，节点可能不可见')
            sys.exit(1)
        ir = ir_list[0]
        if file_key:
            ir['fileKey'] = file_key

        ir['_adaptive'] = _is_adaptive
        ir.setdefault('css', {})
        if _is_adaptive:
            _abb = figma_node.get('absoluteBoundingBox') or {}
            root_width = figma_node.get('width') or _abb.get('width') or 1440
            _is_mobile = root_width <= 768
            if _is_mobile:
                # Mobile adaptive: layout only (FILL→100%, FIXED→max-width+center)
                # No clamp on text/spacing; max-width bumped to 768px for tablet
                ir['css']['width'] = '100%'
                ir['css']['max-width'] = '768px'
                ir['css']['margin-left'] = 'auto'
                ir['css']['margin-right'] = 'auto'
                log(2, f'       → 自适应模式：已启用-移动端（design={root_width}px, max-width=768px, clamp=off）')
            else:
                # PC adaptive: full clamp + layout
                ir['css']['width'] = '100%'
                ir['css']['max-width'] = px(root_width)
                ir['css']['margin-left'] = 'auto'
                ir['css']['margin-right'] = 'auto'
                log(2, f'       → 自适应模式：已启用（root width={root_width}px）')
        else:
            # Fixed root: constrain to viewport without expanding.
            ir['css']['max-width'] = '100%'
            ir['css']['margin-left'] = 'auto'
            ir['css']['margin-right'] = 'auto'

        ir_dir.mkdir(parents=True, exist_ok=True)
        _write_var_miss_report(ir_dir, node_id_safe, get_var_miss(), _var_id_to_name)
        print(f'       → 节点: {ir.get("figmaName")} ({ir.get("figmaType")})')
        print(f'       → CSS 属性数: {len(ir.get("css", {}))}')

    # ── 页面主题判断 & 反色标记 ─────────────────────────────────────────────
    vars_thread.join(timeout=5)
    _page_theme = detect_page_theme(ir)
    tag_mixed_theme_nodes(ir, page_theme=_page_theme)

    # ── Patch: flex shrink ───────────────────────────────────────────────
    patch_flex_shrink(ir)

    # Root page frame: remove overflow:hidden that would clip bottom content
    # when browser text-reflow makes sections taller than the design frame.
    # patch_flex_shrink Patch 9 may have re-added overflow via border-radius heuristic.
    # NOTE: Do NOT pop overflow-x — css_extractor intentionally sets overflow-x:hidden
    # when clipsContent=True so that decorative elements (e.g. off-edge butterflies)
    # don't widen the document and break pixel-diff comparisons.
    ir['css'].pop('overflow', None)

    # ── Patch: top-edge gradient reversal ────────────────────────────────
    patch_top_gradient_reversal(ir)

    # ── Patch: filter isolation breaks blend mode ─────────────────────────
    patch_filter_blend_isolation(ir)

    # ── Patch: center-flex button with asymmetric icon-slot padding ────────
    patch_center_flex_padding_normalize(ir)

    # ── Brand replace：在保存 IR 之前替换品牌词，确保磁盘 IR 与产物一致。
    # split_components.py 直接读磁盘 IR，必须在此处替换，否则 split 产物仍含原始品牌词。
    _ir_root_name = (ir.get('figmaName') or '').replace(' ', '-')
    _ir_root_name = re.sub(r'[^a-zA-Z0-9-_]', '', _ir_root_name).lower()
    _old_comp = to_pascal(_ir_root_name) if _ir_root_name else 'Component'
    brand_replace_ir(ir)
    _ir_root_name2 = (ir.get('figmaName') or '').replace(' ', '-')
    _ir_root_name2 = re.sub(r'[^a-zA-Z0-9-_]', '', _ir_root_name2).lower()
    _new_comp = to_pascal(_ir_root_name2) if _ir_root_name2 else _old_comp
    rename_staging_dirs(base_dir, _old_comp, _new_comp, node_id_safe)

    # ── 保存 IR（已含 brand replace，在所有 patch + theme 标记之后）─────
    ir_dir.mkdir(parents=True, exist_ok=True)
    ir_path.write_text(json.dumps(ir, indent=2))
    print(f'       → IR 已保存: {ir_path}')

    # ── Step 3: Semantics (fallback) ─────────────────────────────────────
    log(3, '语义推断...')
    resolved_ir = apply_fallback_semantics(ir)
    print('       → 语义已推断')

    # ── Step 4: Code Gen ─────────────────────────────────────────────────
    log(4, 'Code Gen：生成组件文件...')
    normalize_class_names_in_place(resolved_ir)

    sem = resolved_ir.get('semantic') or {}
    name = sem.get('componentName') or to_pascal(sem.get('className', 'Component'))

    out_dir = page_code_subdir(name, node_id_safe)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Download assets
    if not skip_assets:
        fk = resolved_ir.get('fileKey') if from_ir else file_key
        if fk:
            fetch_and_embed_assets(resolved_ir, fk, page_code_subdir(name, node_id_safe).name, public_dir, figma_token)

    # Resolve asset paths from disk
    resolve_asset_paths(resolved_ir, page_code_subdir(name, node_id_safe).name, public_dir)

    # i18n: collect text tokens and build texts_map (opt-in via --i18n)
    _i18n_tokens = collect_text_tokens(resolved_ir) if args.get('enable_i18n') else []
    _texts_map: dict = (
        {t['figmaId']: t['tokenId'] for t in _i18n_tokens if t['figmaId']}
        if _i18n_tokens else {}
    )

    # ── Split mode: detect boundaries → skip_subtrees ─────────────────────
    skip_subtrees = set()
    if args.get('split_mode'):
        from lib.boundary_detector import detect_boundaries
        from lib.code_connect import load_code_connect_json, parse_mcp_response
        _cc_path = code_connect_file(node_id_safe)
        _cc_raw = load_code_connect_json(_cc_path) if _cc_path.exists() else {}
        _sample = next(iter(_cc_raw.values()), {}) if _cc_raw else {}
        _cc_map = parse_mcp_response(_cc_raw) if _cc_raw and 'ccImport' not in _sample else _cc_raw
        _sem_path = semantic_file(node_id_safe)
        if _sem_path.exists():
            _semantic = json.loads(_sem_path.read_text())
        else:
            _semantic = {'componentInstances': {}, 'interactiveNodes': {}, 'readyFrames': []}
        _boundaries = detect_boundaries(resolved_ir, _semantic, code_connect_map=_cc_map)
        for s in _boundaries.get('sections', []):
            fid = s['node'].get('figmaId', '')
            if fid:
                skip_subtrees.add(fid)
        for lc in _boundaries.get('leafComponents', []):
            for inst in lc.get('instances', []):
                fid = inst.get('figmaId', '')
                if fid:
                    skip_subtrees.add(fid)
        if skip_subtrees:
            print(f'       → Split mode: {len(skip_subtrees)} subtrees will be skipped')

    # Write output files
    (out_dir / f'{name}.tsx').write_text(
        generate_tsx(resolved_ir, css_ext=css_ext,
                     texts_map=_texts_map or None,
                     component_name_for_texts=name,
                     page_theme=_page_theme,
                     skip_subtrees=skip_subtrees or None)
    )
    (out_dir / f'{name}.module.{css_ext}').write_text(generate_scss(resolved_ir))
    # Sidecar: className + assetPath mappings for split_codegen
    (out_dir / 'figma-maps.json').write_text(json.dumps({
        'fidToClass': _collect_fid_to_class(resolved_ir),
        'fidToSrc': _collect_fid_to_src(resolved_ir),
    }, indent=2, ensure_ascii=False))
    (out_dir / INDEX_TS_FILENAME).write_text(generate_index(name))
    if _i18n_tokens:
        # Preserve already-filled keys on re-run
        _existing_keys = parse_existing_texts_keys(out_dir / texts_ts_filename(name))
        # Figma source for defaults file header
        _figma_source = (
            f'https://www.figma.com/design/{file_key}/?node-id={node_id}'
            if file_key else f'node-id={node_id}'
        ) if not from_ir else f'node-id={node_id} (from cached IR)'
        (out_dir / texts_ts_filename(name)).write_text(
            generate_texts_file(_i18n_tokens, name, existing_keys=_existing_keys)
        )
        (out_dir / texts_defaults_ts_filename(name)).write_text(
            generate_texts_defaults_file(_i18n_tokens, name, figma_source=_figma_source)
        )

    log_total()
    print(f'\n✅  完成！\n')
    print(f'     {out_dir}/{name}.tsx')
    print(f'     {out_dir}/{name}.module.{css_ext}')
    print(f'     {out_dir}/index.ts')
    if _i18n_tokens:
        print(f'     {out_dir}/{texts_ts_filename(name)}')
        print(f'     {out_dir}/{texts_defaults_ts_filename(name)}')
    print(f'\n下一步校验：')
    print(f'     python3 scripts/validate.py --nodeId={node_id}\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'\n❌  {e}')
        sys.exit(1)
