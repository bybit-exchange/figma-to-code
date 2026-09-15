"""
composite.py — POST /composite implementation.

⚠️ STUB (v0.1). Playwright integration is DEFERRED to v0.2.

See peppy-kindling-seal.md §Phase 4 for the intended implementation:
  1. For each nodeId, use screenshot.get() to fetch a single-node PNG.
  2. Read the IR to get each node's bounds (x, y, w, h) and zIndex.
  3. Build a transparent HTML page positioning each PNG with absolute + z-index.
  4. Launch Playwright chromium headless, page.screenshot() the page.
  5. Save the resulting composite PNG under cache_dir/composite/{hash}.png.

For v0.1 we return a structured error so the caller can gracefully degrade
by fetching individual /screenshot/:nodeId images and stacking them manually
in whatever tool they prefer (e.g. the coding agent's own imagination).

TODO(v0.2):
  - Add optional Playwright dependency (soft-import at call time so v0.1 still runs).
  - If Playwright missing, keep the same 501 fallback.
  - Compute output bounding box as union of all node bounds.
"""

from __future__ import annotations

from pathlib import Path


COMPOSITE_STUB_ERROR = {
    'error': 'composite API requires Playwright integration; not implemented in v0.1',
    'hint': (
        'call GET /screenshot/{nodeId} for each node individually and stack them '
        'manually. See references/coding-agent-guide.md.'
    ),
    'phase': 'v0.2',
    'status': 501,
}


def render(cache_dir: Path, ir_root: dict, node_ids: list[str], fmt: str = 'png') -> dict:
    """STUB. Returns COMPOSITE_STUB_ERROR verbatim.

    Signature is stable — v0.2 will populate {"path": ..., "size": {...}} on success.
    """
    # Even in the stub we validate inputs so the eventual real implementation has
    # a predictable failure mode.
    if not isinstance(node_ids, list) or not all(isinstance(n, str) for n in node_ids):
        return {
            'error': 'nodeIds must be a list of strings',
            'status': 400,
        }
    if fmt not in ('png', 'jpg', 'jpeg'):
        return {
            'error': f"unsupported format '{fmt}' (want png/jpg)",
            'status': 400,
        }
    return dict(COMPOSITE_STUB_ERROR)
