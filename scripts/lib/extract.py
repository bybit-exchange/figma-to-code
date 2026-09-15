"""
extract.py — orchestrate a one-shot Figma extraction into the cache dir.

Given a Figma URL + a cache dir, does:
  1. Parse fileKey + nodeId from URL
  2. Fetch raw Figma node via Figma REST API
  3. (Optional) Load Figma variables + styles for design token resolution
  4. Build IR via ir_builder.build_ir()
  5. Walk IR, collect asset node figmaIds, batch-fetch export URLs, download PNG/SVG
  6. Emit small thumbnails (via screenshot.get) for the root frame's direct children
  7. Write ir.json + denoised.json under cache_dir

Public API:
  extract(figma_url: str, output_dir: Path) -> dict
    returns {"fileKey": ..., "rootNodeId": ..., "nodeCount": int}
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib import request as urllib_request, parse as urllib_parse

from .ir_builder import build_ir
from .figma_vars import load_figma_var_map, load_figma_style_map
from . import denoise as _denoise
from . import screenshot as _screenshot


# ─── Figma URL parser ────────────────────────────────────────────────────

def parse_figma_url(url: str) -> Optional[dict]:
    """Parse a Figma URL into {file_key, node_id}. Returns None on failure."""
    if not url:
        return None
    try:
        u = urllib_parse.urlparse(url)
        m = re.search(r'/(?:design|file|proto)/([^/]+)', u.path)
        if not m:
            return None
        file_key = m.group(1)
        qs = urllib_parse.parse_qs(u.query)
        # focus-id preferred over node-id (matches figma-to-code behavior)
        node_id_raw = qs.get('focus-id', [''])[0] or qs.get('node-id', [''])[0]
        node_id = node_id_raw.replace('-', ':')
        return {'file_key': file_key, 'node_id': node_id or None}
    except Exception:
        return None


# ─── Token loading ────────────────────────────────────────────────────────

def load_figma_token() -> Optional[str]:
    """Read Figma token from ~/.claude/figma-token then FIGMA_ACCESS_TOKEN env."""
    token_file = Path.home() / '.claude' / 'figma-token'
    if token_file.exists():
        t = token_file.read_text().strip()
        if t:
            return t
    return os.environ.get('FIGMA_ACCESS_TOKEN')


# ─── HTTP helpers ─────────────────────────────────────────────────────────

def figma_get(url: str, token: str, timeout: int = 30) -> dict:
    req = urllib_request.Request(url, headers={'X-Figma-Token': token})
    with urllib_request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


# ─── Figma node fetch ─────────────────────────────────────────────────────

def fetch_figma_node(file_key: str, node_id: str, token: str, raw_dir: Path) -> Optional[dict]:
    encoded = urllib_parse.quote(node_id)
    url = f'https://api.figma.com/v1/files/{file_key}/nodes?ids={encoded}'
    data = figma_get(url, token)

    raw_dir.mkdir(parents=True, exist_ok=True)
    safe = node_id.replace(':', '-')
    (raw_dir / f'{safe}-raw-data.json').write_text(json.dumps(data, indent=2))

    doc = data.get('nodes', {})
    node = doc.get(node_id) or doc.get(node_id.replace(':', '-')) or {}
    return node.get('document')


# ─── Asset URL fetch / download ───────────────────────────────────────────

def fetch_export_urls(file_key: str, ids: list, fmt: str, token: str, scale: int = 2) -> dict:
    result = {}
    for batch in _chunk(ids, 100):
        encoded = urllib_parse.quote(','.join(batch))
        url = (
            f'https://api.figma.com/v1/images/{file_key}?ids={encoded}'
            f'&format={fmt}&scale={scale}'
        )
        try:
            data = figma_get(url, token)
            result.update(data.get('images', {}))
        except Exception as e:
            print(f'   [warn] Figma images API error ({fmt}): {e}')
    return result


def fetch_image_fill_urls(file_key: str, token: str) -> dict:
    try:
        data = figma_get(f'https://api.figma.com/v1/files/{file_key}/images', token)
        return data.get('meta', {}).get('images', {})
    except Exception:
        return {}


def download_file(url: str, dest: Path, retries: int = 3):
    for attempt in range(retries):
        try:
            subprocess.run(
                ['curl', '-sSL', '--max-time', '30', '-o', str(dest), url],
                check=True,
            )
            return
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1 * (attempt + 1))


def _collect_asset_nodes(ir: dict, acc=None) -> dict:
    if acc is None:
        acc = {'vectors': [], 'images': [], 'bg_nodes': []}
    if ir.get('isVectorNode'):
        acc['vectors'].append(ir)
    elif ir.get('isImageNode'):
        acc['images'].append(ir)
    elif ir.get('fillImageRef'):
        acc['bg_nodes'].append(ir)
    for c in ir.get('children', []):
        _collect_asset_nodes(c, acc)
    return acc


def _download_assets(ir: dict, file_key: str, assets_dir: Path, token: str):
    acc = _collect_asset_nodes(ir)
    if not (acc['vectors'] or acc['images'] or acc['bg_nodes']):
        return
    assets_dir.mkdir(parents=True, exist_ok=True)

    # SVG vectors
    if acc['vectors']:
        ids = list({v['figmaId'] for v in acc['vectors']})
        url_map = fetch_export_urls(file_key, ids, 'svg', token)
        for fid, url in url_map.items():
            if not url:
                continue
            safe = re.sub(r'[:/;]', '-', fid)
            dest = assets_dir / f'{safe}.svg'
            if dest.exists():
                continue
            try:
                download_file(url, dest)
            except Exception as e:
                print(f'   [warn] SVG download failed {fid}: {e}')

    # PNG images
    if acc['images']:
        ids = list({n['figmaId'] for n in acc['images']})
        url_map = fetch_export_urls(file_key, ids, 'png', token)
        for fid, url in url_map.items():
            if not url:
                continue
            safe = re.sub(r'[:/;]', '-', fid)
            dest = assets_dir / f'{safe}.png'
            if dest.exists():
                continue
            try:
                download_file(url, dest)
            except Exception as e:
                print(f'   [warn] PNG download failed {fid}: {e}')

    # Background image fills
    if acc['bg_nodes']:
        fill_urls = fetch_image_fill_urls(file_key, token)
        for n in acc['bg_nodes']:
            url = fill_urls.get(n.get('fillImageRef', ''))
            if not url:
                continue
            safe = re.sub(r'[^a-zA-Z0-9]', '-', n['fillImageRef'])
            dest = assets_dir / f'{safe}.png'
            if dest.exists():
                continue
            try:
                download_file(url, dest)
            except Exception as e:
                print(f'   [warn] fill image download failed: {e}')


# ─── Thumbnail helper ─────────────────────────────────────────────────────

def _emit_thumbnails(ir: dict, file_key: str, thumbs_dir: Path, token: str, width: int = 160):
    """Emit ~160px thumbnails for the root node + its direct children.

    Uses screenshot.get() which caches under a screenshot subdir; we then symlink
    or copy into thumbs_dir. To keep dependencies minimal we just re-fetch through
    the shared screenshot module (writes into its own cache).
    """
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    node_ids: list = []
    root_id = ir.get('figmaId')
    if root_id:
        node_ids.append(root_id)
    for c in ir.get('children', [])[:20]:
        cid = c.get('figmaId')
        if cid:
            node_ids.append(cid)

    if not node_ids:
        return
    # Batch fetch the export URLs, then download to thumbs_dir/{safeId}.png
    url_map = fetch_export_urls(file_key, node_ids, 'png', token, scale=1)
    for fid, url in url_map.items():
        if not url:
            continue
        safe = re.sub(r'[:/;]', '-', fid)
        dest = thumbs_dir / f'{safe}.png'
        if dest.exists():
            continue
        try:
            download_file(url, dest)
        except Exception as e:
            print(f'   [warn] thumbnail download failed {fid}: {e}')


# ─── Public entry ─────────────────────────────────────────────────────────

def extract(figma_url: str, output_dir: Path) -> dict:
    """One-shot extraction. Populates output_dir with:

    output_dir/
      ir.json               — full IR tree (build_ir output)
      denoised.json         — IR with per-node `tags` field
      assets/               — downloaded SVGs + PNGs (by figmaId)
      thumbnails/           — small 1x PNGs for overview endpoint
      raw/                  — raw Figma API JSON responses (for debugging)
      .meta.json            — {fileKey, rootNodeId, nodeCount, extractedAt}

    Returns the .meta.json dict.
    """
    parsed = parse_figma_url(figma_url)
    if not parsed or not parsed.get('file_key') or not parsed.get('node_id'):
        raise ValueError(f'Invalid Figma URL: {figma_url}')

    token = load_figma_token()
    if not token:
        raise RuntimeError(
            'Figma token not found. Put your token in ~/.claude/figma-token '
            'or set FIGMA_ACCESS_TOKEN env var.'
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'raw').mkdir(exist_ok=True)
    (output_dir / 'assets').mkdir(exist_ok=True)
    (output_dir / 'thumbnails').mkdir(exist_ok=True)

    file_key = parsed['file_key']
    node_id = parsed['node_id']

    print(f'[extract] fetching node {node_id} from file {file_key}...')
    figma_node = fetch_figma_node(file_key, node_id, token, output_dir / 'raw')
    if not figma_node:
        raise RuntimeError(f'Figma node {node_id} not found (permissions? deleted?)')

    # Load design token variable maps. figma_vars caches under a raw-data dir alongside
    # the raw Figma responses (it looks for {nodeId}-{fileKey}-variables.json).
    node_id_safe = node_id.replace(':', '-')
    raw_dir = output_dir / 'raw'
    try:
        var_id_to_bds, _var_id_to_name = load_figma_var_map(
            file_key, token, raw_dir, node_id_safe
        )
    except Exception as e:
        print(f'   [warn] figma variables fetch failed: {e}')
        var_id_to_bds = {}
    try:
        style_id_to_bds = load_figma_style_map(
            file_key, token, raw_dir, node_id_safe
        )
    except Exception as e:
        print(f'   [warn] figma styles fetch failed: {e}')
        style_id_to_bds = {}

    print('[extract] building IR...')
    ir_list = build_ir([figma_node], {}, var_id_to_bds, style_id_to_bds)
    if not ir_list:
        raise RuntimeError('build_ir returned empty list')
    ir = ir_list[0]

    print('[extract] downloading assets...')
    _download_assets(ir, file_key, output_dir / 'assets', token)

    print('[extract] emitting thumbnails...')
    _emit_thumbnails(ir, file_key, output_dir / 'thumbnails', token)

    # Write ir.json
    (output_dir / 'ir.json').write_text(json.dumps(ir, indent=2, ensure_ascii=False))

    # Denoise: tag nodes
    print('[extract] denoising...')
    denoised = _denoise.tag_ir(ir)
    (output_dir / 'denoised.json').write_text(json.dumps(denoised, indent=2, ensure_ascii=False))

    # Count nodes
    def _count(n):
        return 1 + sum(_count(c) for c in n.get('children', []))
    node_count = _count(ir)

    meta = {
        'fileKey': file_key,
        'rootNodeId': node_id,
        'nodeCount': node_count,
        'figmaUrl': figma_url,
        'extractedAt': int(time.time()),
    }
    (output_dir / '.meta.json').write_text(json.dumps(meta, indent=2))
    print(f'[extract] done: {node_count} nodes in {output_dir}')
    return meta
