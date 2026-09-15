#!/usr/bin/env python3
"""
figma-context server — Python stdlib HTTP server exposing 4 REST endpoints.

Endpoints:
  GET  /health                — liveness probe
  GET  /overview              — root + direct-children summary + thumbnail URIs
  GET  /context/:nodeId?depth=1  — single node context (layout + css + slim children)
  GET  /screenshot/:nodeId?w=<n> — single-node PNG (Figma Images API, cached)
  POST /composite             — STUB in v0.1 (returns 501 with hint)

Server is stateless: each request re-reads $FIGMA_CONTEXT_CACHE_DIR/ir.json.
$FIGMA_CONTEXT_CACHE_DIR is set by start.py before spawning this process.

Env vars (all read at startup):
  FIGMA_CONTEXT_CACHE_DIR   — required, absolute path to the cache dir (contains ir.json)
  FIGMA_CONTEXT_PORT        — optional, default 7181
"""

from __future__ import annotations

import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Make sibling `lib` package importable when spawned directly by start.py.
sys.path.insert(0, str(Path(__file__).parent))

from lib import denoise as _denoise           # noqa: E402
from lib import screenshot as _screenshot     # noqa: E402
from lib import composite as _composite       # noqa: E402
from lib.extract import load_figma_token      # noqa: E402


# ─── State loaded at request time (stateless server) ─────────────────────

def _cache_dir() -> Path:
    p = os.environ.get('FIGMA_CONTEXT_CACHE_DIR')
    if not p:
        raise RuntimeError('FIGMA_CONTEXT_CACHE_DIR not set')
    return Path(p)


def _load_ir() -> dict:
    return json.loads((_cache_dir() / 'ir.json').read_text())


def _load_denoised() -> dict:
    p = _cache_dir() / 'denoised.json'
    if p.exists():
        return json.loads(p.read_text())
    # Fallback: tag on-the-fly (slower but keeps API contract).
    return _denoise.tag_ir(_load_ir())


def _load_meta() -> dict:
    p = _cache_dir() / '.meta.json'
    return json.loads(p.read_text()) if p.exists() else {}


def _find_node(root: dict, node_id: str) -> dict | None:
    if root.get('figmaId') == node_id:
        return root
    for c in root.get('children') or []:
        found = _find_node(c, node_id)
        if found:
            return found
    return None


# ─── Handlers ────────────────────────────────────────────────────────────

def h_health(_qs: dict) -> tuple[int, dict]:
    return 200, {'ok': True, 'service': 'figma-context', 'version': '0.1.0'}


def h_overview(_qs: dict) -> tuple[int, dict]:
    ir = _load_denoised()
    meta = _load_meta()
    root_id = ir.get('figmaId')

    def _node_summary(n, depth=0):
        bb = n.get('bb') or n.get('absoluteBoundingBox') or {}
        return {
            'id': n.get('figmaId'),
            'name': n.get('figmaName'),
            'type': n.get('figmaType'),
            'depth': depth,
            'tags': n.get('tags') or [],
            'bounds': {
                'x': bb.get('x'),
                'y': bb.get('y'),
                'w': bb.get('width') or bb.get('w'),
                'h': bb.get('height') or bb.get('h'),
            } if bb else None,
            'thumbnail': f'/screenshot/{n.get("figmaId")}?w=160',
        }

    # Enumerate root + up to 2 levels deep so agent can find modules without
    # pulling the entire tree.
    nodes: list = []

    def _walk(n, depth):
        nodes.append(_node_summary(n, depth))
        if depth >= 2:
            return
        for c in n.get('children') or []:
            _walk(c, depth + 1)

    _walk(ir, 0)

    return 200, {
        'fileKey': meta.get('fileKey'),
        'rootNodeId': root_id,
        'nodeCount': meta.get('nodeCount'),
        'nodes': nodes,
    }


def h_context(node_id: str, qs: dict) -> tuple[int, dict]:
    ir = _load_denoised()
    node = _find_node(ir, node_id)
    if not node:
        return 404, {'error': f'node {node_id} not found'}

    try:
        depth = int((qs.get('depth', ['1']) or ['1'])[0])
    except ValueError:
        depth = 1

    layout = node.get('inferredFlex') or node.get('layout') or None
    layout_str = None
    if isinstance(layout, dict):
        direction = layout.get('direction') or layout.get('flexDirection')
        gap = layout.get('gap')
        if direction:
            layout_str = f'flex {direction}'
            if gap:
                layout_str += f' gap:{gap}'

    children = node.get('children') or []
    projected_children: list = []
    for c in children:
        base = _denoise.project_child(c)
        if depth >= 2:
            base['children'] = [_denoise.project_child(gc) for gc in (c.get('children') or [])]
        projected_children.append(base)

    return 200, {
        'id': node.get('figmaId'),
        'name': node.get('figmaName'),
        'type': node.get('figmaType'),
        'tags': node.get('tags') or [],
        'layout': layout_str,
        'inferredFlex': node.get('inferredFlex'),
        'css': node.get('css') or {},
        'bounds': _bounds_of(node),
        'children': projected_children,
    }


def _bounds_of(node: dict) -> dict | None:
    bb = node.get('bb') or node.get('absoluteBoundingBox')
    if not bb:
        return None
    return {
        'x': bb.get('x'), 'y': bb.get('y'),
        'w': bb.get('width') or bb.get('w'),
        'h': bb.get('height') or bb.get('h'),
    }


def h_screenshot(node_id: str, qs: dict) -> tuple[int, dict | bytes, str]:
    """Returns (status, body, content_type). body is bytes for images or dict for errors."""
    meta = _load_meta()
    file_key = meta.get('fileKey')
    if not file_key:
        return 500, {'error': 'no fileKey in cache meta'}, 'application/json'

    token = load_figma_token()
    if not token:
        return 500, {'error': 'FIGMA_TOKEN missing on server'}, 'application/json'

    try:
        w_raw = (qs.get('w') or [None])[0]
        width = int(w_raw) if w_raw else None
    except ValueError:
        width = None

    try:
        path = _screenshot.get(_cache_dir(), file_key, node_id, token, width=width)
    except Exception as e:
        return 500, {'error': str(e)}, 'application/json'

    body = path.read_bytes()
    return 200, body, 'image/png'


def h_composite(body: dict) -> tuple[int, dict]:
    node_ids = body.get('nodeIds') or []
    fmt = body.get('format') or 'png'
    ir = _load_ir()
    result = _composite.render(_cache_dir(), ir, node_ids, fmt)
    status = result.get('status', 200) if 'error' in result else 200
    return status, result


# ─── HTTP dispatch ───────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = 'figma-context/0.1'

    def log_message(self, format, *args):  # noqa: A002 — override stdlib name
        sys.stderr.write(f'[figma-context] {self.address_string()} {format % args}\n')

    def _write_json(self, status: int, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_bytes(self, status: int, content_type: str, payload: bytes):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        path = u.path.rstrip('/') or '/'

        try:
            if path == '/health':
                s, p = h_health(qs)
                return self._write_json(s, p)
            if path == '/overview':
                s, p = h_overview(qs)
                return self._write_json(s, p)
            if path.startswith('/context/'):
                node_id = path[len('/context/'):]
                s, p = h_context(node_id, qs)
                return self._write_json(s, p)
            if path.startswith('/screenshot/'):
                node_id = path[len('/screenshot/'):]
                s, body, ctype = h_screenshot(node_id, qs)
                if ctype == 'application/json':
                    return self._write_json(s, body)
                return self._write_bytes(s, ctype, body)
            self._write_json(404, {'error': f'unknown path {path}'})
        except Exception as e:
            self._write_json(500, {'error': str(e)})

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        path = u.path.rstrip('/') or '/'
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length > 0 else b''
        try:
            body = json.loads(raw or b'{}')
        except json.JSONDecodeError as e:
            return self._write_json(400, {'error': f'invalid JSON: {e}'})

        try:
            if path == '/composite':
                s, p = h_composite(body)
                return self._write_json(s, p)
            self._write_json(404, {'error': f'unknown path {path}'})
        except Exception as e:
            self._write_json(500, {'error': str(e)})


# ─── Main ─────────────────────────────────────────────────────────────────

def _install_signal_handlers(httpd: HTTPServer):
    def _shutdown(_signum, _frame):
        sys.stderr.write('[figma-context] SIGTERM received, shutting down\n')
        # shutdown() must be called from a separate thread of the same process.
        import threading
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)


def main():
    if 'FIGMA_CONTEXT_CACHE_DIR' not in os.environ:
        print('FIGMA_CONTEXT_CACHE_DIR is required', file=sys.stderr)
        sys.exit(2)
    if not (Path(os.environ['FIGMA_CONTEXT_CACHE_DIR']) / 'ir.json').exists():
        print(
            f"ir.json missing in {os.environ['FIGMA_CONTEXT_CACHE_DIR']} — "
            'run start.py first',
            file=sys.stderr,
        )
        sys.exit(2)

    port = int(os.environ.get('FIGMA_CONTEXT_PORT') or '7181')
    httpd = HTTPServer(('127.0.0.1', port), Handler)
    _install_signal_handlers(httpd)
    sys.stderr.write(f'[figma-context] listening on http://127.0.0.1:{port}\n')
    httpd.serve_forever()


if __name__ == '__main__':
    main()
