"""
screenshot.py — thin wrapper around Figma Images API for single-node exports.

Public API:
  get(cache_dir, file_key, node_id, token, width=None, fmt='png') -> Path

The response is cached under cache_dir/screenshot/{safeId}_{width}.png (or .svg).
If width is None, exports at scale=2.

Raises RuntimeError if the Figma API returns no URL for the node.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib import request as urllib_request, parse as urllib_parse


def _figma_get(url: str, token: str, timeout: int = 30) -> dict:
    req = urllib_request.Request(url, headers={'X-Figma-Token': token})
    with urllib_request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _download(url: str, dest: Path, retries: int = 3):
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


def _safe(node_id: str) -> str:
    return re.sub(r'[:/;]', '-', node_id)


def get(
    cache_dir: Path,
    file_key: str,
    node_id: str,
    token: str,
    width: Optional[int] = None,
    fmt: str = 'png',
) -> Path:
    """Return path to a cached PNG/SVG of the given Figma node.

    If width is provided, Figma Images API's `scale` param is derived (currently we
    pass scale=2 for high-density; width is used only to key the cache filename).
    A future improvement: query IR bounds and choose scale = width / native_width.
    """
    cache_dir = Path(cache_dir)
    shot_dir = cache_dir / 'screenshot'
    shot_dir.mkdir(parents=True, exist_ok=True)

    key = f'{_safe(node_id)}_{width or "native"}.{fmt}'
    dest = shot_dir / key
    if dest.exists():
        return dest

    encoded = urllib_parse.quote(node_id)
    scale = 2 if fmt == 'png' else 1
    url = (
        f'https://api.figma.com/v1/images/{file_key}?ids={encoded}'
        f'&format={fmt}&scale={scale}'
    )
    data = _figma_get(url, token)
    img_url = (data.get('images') or {}).get(node_id)
    if not img_url:
        raise RuntimeError(f'No image URL returned for node {node_id}')

    _download(img_url, dest)
    return dest
