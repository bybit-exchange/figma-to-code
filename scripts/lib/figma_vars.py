from __future__ import annotations
"""
figma_vars.py — Fetch, parse, cache Figma file variables.

Fetch strategy (in order):
  1. Cache hit (raw_data_dir/{file_key}-variables.json, 24h TTL) → instant
  2. GET /variables/local  — 2 attempts × 60s timeout
  3. GET /variables/published — fallback when /local hangs (some files have server-side
     computation that never completes; /published is served from a faster code path)

Background:
  /variables/local is a real-time computation endpoint; certain large files cause
  Figma's server to hang indefinitely (starttransfer never arrives). /variables/published
  is CDN-served and almost always fast, but only contains library-published variables
  (may be partial). Together they maximise coverage.

Network failure or missing token → returns ({}, {}) silently.
"""

import json
import re
import time
import urllib.request
from pathlib import Path

_VARS_LOCAL_URL = "https://api.figma.com/v1/files/{file_key}/variables/local"
_VARS_PUBLISHED_URL = "https://api.figma.com/v1/files/{file_key}/variables/published"
_STYLES_URL = "https://api.figma.com/v1/files/{file_key}/styles"
_CACHE_TTL = 86400  # 24h in seconds
_LOCAL_TIMEOUT = 60    # seconds — covers slow files (~25-35s observed)
_LOCAL_RETRIES = 1     # single attempt; prefetch_vars.py handles multi-retry
_PUBLISHED_TIMEOUT = 15  # seconds — /published is CDN-served, fast
_STYLES_TIMEOUT = 15   # seconds — styles endpoint is CDN-served, fast


def normalize_var_name(figma_name: str) -> str:
    """Normalize a Figma variable name to CSS custom property format.

    Examples:
      "Gray/tt-1/title"   → "--gray-tt-1-title"
      "--gray-tt-1-title" → "--gray-tt-1-title"  (idempotent)
      "Brand/700/normal"  → "--brand-700-normal"
      "static White"      → "--static-white"
    """
    name = figma_name.lower()
    name = re.sub(r'[/ _]+', '-', name)  # treat _ same as / and space
    name = re.sub(r'-+', '-', name).strip('-')
    return name if name.startswith('--') else f'--{name}'


def load_var_token_map() -> dict:
    """Load the static Figma variable name → design token mapping table."""
    map_path = Path(__file__).parent / 'var_token_map.json'
    if not map_path.exists():
        return {}
    try:
        return json.loads(map_path.read_text())
    except Exception:
        return {}


def load_var_id_token_direct() -> dict:
    """Load direct VariableID → design token mappings for remote library variables.

    These cover variables that /variables/local doesn't return (from external
    shared libraries). Keys are full VariableID strings.
    """
    map_path = Path(__file__).parent / 'var_id_token_direct.json'
    if not map_path.exists():
        return {}
    try:
        return json.loads(map_path.read_text())
    except Exception:
        return {}


def resolve_cache_path(raw_data_dir: Path, file_key: str, node_id_safe: str = '') -> Path:
    """Return the cache path for a file's variables.

    Cache file format: {nodeId}-{fileKey}-variables.json
    If a cache already exists for this fileKey (from any node), reuse it —
    variables are file-level and shared by all nodes in the same file.
    """
    existing = sorted(raw_data_dir.glob(f'*-{file_key}-variables.json'))
    if existing:
        return existing[0]
    prefix = f'{node_id_safe}-' if node_id_safe else ''
    return raw_data_dir / f'{prefix}{file_key}-variables.json'


def load_figma_var_map(
    file_key: str,
    token: str | None,
    raw_data_dir: Path,
    node_id_safe: str = '',
) -> tuple[dict, dict]:
    """Return (var_id_to_token, var_id_to_name) for all variables in the file.

    var_id_to_token: {variableId: "--design-token-xxx"}  — only mapped variables
    var_id_to_name:  {variableId: "--figma-name"}        — all variables (for miss logging)

    Cache file: {nodeId}-{fileKey}-variables.json (24h TTL).
    Variables are file-level — all nodes in the same file share one cache file.
    Returns ({}, {}) on any failure (no token, network error, API error).
    """
    if not file_key or not token:
        return {}, {}

    var_token_map = load_var_token_map()
    cache_path = resolve_cache_path(raw_data_dir, file_key, node_id_safe)

    raw_variables = _load_or_fetch(file_key, token, cache_path)
    var_id_to_token, var_id_to_name = _build_maps(raw_variables, var_token_map) if raw_variables else ({}, {})

    # Merge direct VariableID → design token mappings (covers remote library variables
    # that /variables/local doesn't return)
    direct = load_var_id_token_direct()
    var_id_to_token.update(direct)

    return var_id_to_token, var_id_to_name


def _write_cache(cache_path: Path, variables: dict, status: str) -> None:
    """Write variables cache with explicit status marker.

    Cache format:
      {"_status": "ok",     "_ts": <epoch>, "data": {variableId: {...}, ...}}
      {"_status": "failed", "_ts": <epoch>}
    """
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'_status': status, '_ts': time.time()}
        if status == 'ok':
            payload['data'] = variables
        cache_path.write_text(json.dumps(payload))
    except Exception:
        pass


def _read_cache(cache_path: Path, force: bool = False) -> dict | None:
    """Read variables from cache.

    Returns:
      dict  — valid variables data (age < _CACHE_TTL)
      None  — cache miss, expired, invalid, or force=True bypass
    """
    if force or not cache_path.exists():
        return None
    try:
        age = time.time() - cache_path.stat().st_mtime
        payload = json.loads(cache_path.read_text())
        status = payload.get('_status')

        if status == 'ok':
            if age < _CACHE_TTL:
                return payload.get('data', {})
        elif status == 'failed':
            # No negative cache — always retry on next run.
            # Failed marker exists only as a record; we ignore it here.
            return None
        else:
            # Old format (plain variables dict, no _status)
            if age < _CACHE_TTL and isinstance(payload, dict):
                return payload
    except Exception:
        pass
    return None


def _load_or_fetch(file_key: str, token: str, cache_path: Path,
                   force: bool = False) -> dict:
    """Return cached variables dict or fetch fresh from API.

    Fetch order:
      1. Cache (_status=ok within 24h, or _status=failed within 1h)
      2. /variables/local  — _LOCAL_RETRIES attempts × _LOCAL_TIMEOUT each
      3. /variables/published — fallback if /local never responds

    On total failure writes {"_status":"failed"} so subsequent runs skip
    re-fetching for _FAILED_TTL (1h). Pass force=True to bypass.
    """
    cached = _read_cache(cache_path, force=force)
    if cached is not None:
        return cached

    headers = {'X-Figma-Token': token}

    # Attempt /variables/local (may be slow or hang on large files)
    for attempt in range(1, _LOCAL_RETRIES + 1):
        try:
            url = _VARS_LOCAL_URL.format(file_key=file_key)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_LOCAL_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
            variables = data.get('meta', {}).get('variables', {})
            _write_cache(cache_path, variables, 'ok')
            return variables
        except Exception:
            if attempt < _LOCAL_RETRIES:
                time.sleep(2)

    # Fallback: /variables/published (CDN-served, covers library-published vars)
    try:
        url = _VARS_PUBLISHED_URL.format(file_key=file_key)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_PUBLISHED_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        variables = data.get('meta', {}).get('variables', {})
        _write_cache(cache_path, variables, 'ok')
        return variables
    except Exception:
        pass

    # Both failed — write negative cache to skip on next run
    _write_cache(cache_path, {}, 'failed')
    return {}


def load_figma_style_map(
    file_key: str,
    token: str | None,
    raw_data_dir: Path,
    node_id_safe: str = '',
) -> dict:
    """Return {styleId: "--design-token-xxx"} for all FILL Color Styles in the file.

    Color Styles are the older Figma styling system (pre-Variables). They are
    referenced via node.styles.fill = "2:317" rather than fill.boundVariables.

    Resolution strategy:
      1. /files/{key}/styles  — locally-published styles (fast, often empty for
         files that only use remote library styles)
      2. /files/{key}/nodes?ids={all_style_ids}  — batch-fetch style swatch nodes;
         each swatch is a hidden RECTANGLE whose `name` field IS the style name.
         Works for remote library styles that /styles doesn't return.
      3. Direct overrides from var_id_token_direct.json (STYLE:{fileKey}:{id} entries)

    Cache: raw_data_dir/{nodeId}-{fileKey}-styles.json (24h TTL, _status marker).
    Returns {} on any failure.
    """
    if not file_key or not token:
        return {}

    cache_path = _resolve_style_cache_path(raw_data_dir, file_key, node_id_safe)
    cached = _read_cache(cache_path)

    var_token_map = load_var_token_map()
    style_id_to_token: dict = cached if cached is not None else {}

    # On cache hit: still check if this page has styleIds not yet in the cache.
    # Multiple pages share one file's style cache — a cache built from page A may
    # miss styleIds that only appear in page B. Detect and fill gaps incrementally.
    if cached is not None:
        raw_style_ids = _collect_style_ids_from_raw(raw_data_dir, node_id_safe)
        missing = raw_style_ids - set(style_id_to_token.keys())
        if missing:
            swatch_names = _fetch_style_swatch_names(file_key, token, missing)
            updated = False
            for sid, name in swatch_names.items():
                token_val = var_token_map.get(normalize_var_name(name))
                if token_val:
                    style_id_to_token[sid] = token_val
                    updated = True
            if updated:
                _write_cache(cache_path, style_id_to_token, 'ok')
        _apply_style_direct_overrides(style_id_to_token, file_key)
        return style_id_to_token

    # Step 1: /files/{key}/styles — locally-published styles
    try:
        url = _STYLES_URL.format(file_key=file_key)
        req = urllib.request.Request(url, headers={'X-Figma-Token': token})
        with urllib.request.urlopen(req, timeout=_STYLES_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        for style in data.get('meta', {}).get('styles', []):
            if style.get('styleType') != 'FILL':
                continue
            node_id = style.get('node_id', '')
            name = style.get('name', '')
            if not node_id or not name:
                continue
            token_val = var_token_map.get(normalize_var_name(name))
            if token_val:
                style_id_to_token[node_id] = token_val
    except Exception:
        pass

    # Step 2: batch-fetch style swatch nodes for remote library styles
    # Each style subscription creates a hidden RECTANGLE in the file whose
    # `name` field is the Color Style name — accessible via /nodes even for
    # remote styles that /styles doesn't return.
    raw_style_ids = _collect_style_ids_from_raw(raw_data_dir, node_id_safe)
    missing = raw_style_ids - set(style_id_to_token.keys())
    if missing and token:
        swatch_names = _fetch_style_swatch_names(file_key, token, missing)
        for sid, name in swatch_names.items():
            token_val = var_token_map.get(normalize_var_name(name))
            if token_val:
                style_id_to_token[sid] = token_val

    _apply_style_direct_overrides(style_id_to_token, file_key)
    _write_cache(cache_path, style_id_to_token, 'ok')
    return style_id_to_token


def _collect_style_ids_from_raw(raw_data_dir: Path, node_id_safe: str) -> set:
    """Walk raw-data file and collect all node.styles.fill IDs."""
    raw_path = raw_data_dir / f'{node_id_safe}-raw-data.json'
    if not raw_path.exists():
        return set()
    try:
        data = json.loads(raw_path.read_text())
    except Exception:
        return set()

    ids: set = set()

    def walk(node: dict) -> None:
        sid = (node.get('styles') or {}).get('fill', '')
        if sid:
            ids.add(sid)
        for child in (node.get('children') or []):
            walk(child)

    for ndata in (data.get('nodes') or {}).values():
        walk(ndata.get('document') or {})
    return ids


def _fetch_style_swatch_names(file_key: str, token: str, style_ids: set,
                              timeout: int = 20) -> dict:
    """Batch-fetch style swatch nodes; return {styleId: styleName}.

    Figma creates a hidden RECTANGLE node for each subscribed Color Style.
    Its `name` field is the Color Style name (e.g. "Gray [T]/T1_Title").
    This works for remote library styles that /files/{key}/styles omits.
    """
    if not style_ids:
        return {}
    ids_param = ','.join(style_ids)
    url = f'https://api.figma.com/v1/files/{file_key}/nodes?ids={ids_param}'
    try:
        req = urllib.request.Request(url, headers={'X-Figma-Token': token})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return {}

    result: dict = {}
    for sid, ndata in (data.get('nodes') or {}).items():
        doc = (ndata or {}).get('document') or {}
        name = doc.get('name', '')
        if name and doc.get('type') == 'RECTANGLE' and not doc.get('visible', True):
            result[sid] = name
    return result


def get_style_cache_path(raw_data_dir: Path, file_key: str, node_id_safe: str = '') -> Path:
    """Return cache path for Color Styles: {nodeId}-{fileKey}-styles.json.

    Scans for any existing cache for this fileKey (all nodes share one cache).
    Public alias used by enrich_styles.py and other tooling.
    """
    existing = sorted(raw_data_dir.glob(f'*-{file_key}-styles.json'))
    if existing:
        return existing[0]
    prefix = f'{node_id_safe}-' if node_id_safe else ''
    return raw_data_dir / f'{prefix}{file_key}-styles.json'


# Private alias for internal use
_resolve_style_cache_path = get_style_cache_path


def _apply_style_direct_overrides(style_id_to_token: dict, file_key: str) -> dict:
    """Merge STYLE:{fileKey}:{styleId} entries from var_id_token_direct.json in-place."""
    direct = load_var_id_token_direct()
    prefix = f'STYLE:{file_key}:'
    for k, v in direct.items():
        if k.startswith(prefix):
            style_id_to_token[k[len(prefix):]] = v
    return style_id_to_token


def _build_maps(raw_variables: dict, var_token_map: dict) -> tuple[dict, dict]:
    """Build (var_id_to_token, var_id_to_name) from raw API variables dict."""
    var_id_to_token: dict = {}
    var_id_to_name: dict = {}

    for var_id, var_data in raw_variables.items():
        name = var_data.get('name', '')
        if not name:
            continue
        css_name = normalize_var_name(name)
        var_id_to_name[var_id] = css_name
        token_val = var_token_map.get(css_name)
        if token_val:
            var_id_to_token[var_id] = token_val

    return var_id_to_token, var_id_to_name
