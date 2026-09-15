#!/usr/bin/env python3
"""
figma-context start — one-shot extract + background server.

Usage:
  python3 scripts/start.py '<Figma URL>'

Steps:
  1. Validate $FIGMA_TOKEN (via ~/.claude/figma-token or FIGMA_ACCESS_TOKEN env).
  2. Parse fileKey + nodeId from URL.
  3. Extract IR + assets + thumbnails to .figma-to-code/{fileKey}-{nodeIdSafe}/.
  4. Spawn scripts/server.py as background subprocess. Write PID and cache_dir
     to .figma-to-code/.server.pid / .figma-to-code/.current.
  5. Poll GET /health until 200 or 15s timeout. Print "Server ready at ..."
  6. Handle port conflict: try 7181..7191, then fail with clear message.

Ports:
  Default 7181. Override with $FIGMA_CONTEXT_PORT.

Cache layout:
  .figma-to-code/
    {fileKey}-{nodeIdSafe}/
      ir.json, denoised.json, .meta.json, raw/, assets/, thumbnails/
    .server.pid                — background server PID
    .current                   — absolute path to the current cache dir
    .server.port               — the actual port the server bound to
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib import request as urllib_request

# Make sibling `lib` importable
sys.path.insert(0, str(Path(__file__).parent))

from lib.extract import extract, load_figma_token, parse_figma_url  # noqa: E402


CACHE_ROOT = Path.cwd() / '.figma-to-code' / '5-context-server'
PORT_RANGE = range(7181, 7192)  # 7181..7191 inclusive


def _die(msg: str, code: int = 1):
    print(f'error: {msg}', file=sys.stderr)
    sys.exit(code)


def _find_free_port(preferred: int) -> int:
    """Return first free port from preferred..7191, else raise."""
    candidates = [preferred] + [p for p in PORT_RANGE if p != preferred]
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f'no free port available in {PORT_RANGE.start}..{PORT_RANGE.stop - 1}'
    )


def _poll_health(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    url = f'http://127.0.0.1:{port}/health'
    while time.time() < deadline:
        try:
            with urllib_request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _stop_existing_server(instance_id: str):
    """If this instance's server is running, best-effort SIGTERM it before starting a new one."""
    pid_file = CACHE_ROOT / f'.server-{instance_id}.pid'
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return
    try:
        os.kill(pid, 15)  # SIGTERM
        for _ in range(15):
            time.sleep(0.2)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    except OSError:
        pass
    finally:
        try:
            pid_file.unlink()
        except OSError:
            pass


def main():
    if len(sys.argv) < 2:
        _die('usage: start.py <Figma URL>')

    figma_url = sys.argv[1]

    # Token check — extract.load_figma_token consults ~/.claude/figma-token + env
    if not load_figma_token():
        _die(
            'FIGMA_TOKEN not found. Put your token in ~/.claude/figma-token '
            'or set FIGMA_ACCESS_TOKEN env var.'
        )

    parsed = parse_figma_url(figma_url)
    if not parsed or not parsed.get('file_key') or not parsed.get('node_id'):
        _die(f'invalid Figma URL (no file_key or node-id): {figma_url}')

    file_key = parsed['file_key']
    node_id = parsed['node_id']
    node_id_safe = node_id.replace(':', '-')

    CACHE_ROOT.mkdir(exist_ok=True)
    cache_dir = CACHE_ROOT / f'{file_key}-{node_id_safe}'

    # Extract (skips heavy work if already done, but always refreshes ir.json)
    print(f'[start] extracting to {cache_dir}...')
    meta = extract(figma_url, cache_dir)

    # Instance ID for per-instance pid/port files (supports multiple concurrent servers)
    instance_id = f'{file_key}-{node_id_safe}'

    # Stop any stale server for THIS instance before spawning a new one
    _stop_existing_server(instance_id)

    # Pick a port
    preferred = int(os.environ.get('FIGMA_CONTEXT_PORT') or '7181')
    try:
        port = _find_free_port(preferred)
    except RuntimeError as e:
        _die(str(e))

    # Spawn context_server.py
    server_script = Path(__file__).parent / 'context_server.py'
    env = os.environ.copy()
    env['FIGMA_CONTEXT_CACHE_DIR'] = str(cache_dir.resolve())
    env['FIGMA_CONTEXT_PORT'] = str(port)

    log_path = CACHE_ROOT / f'server-{instance_id}.log'
    log_fh = open(log_path, 'ab')  # noqa: SIM115 — kept open for detached subprocess

    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        env=env,
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    (CACHE_ROOT / f'.server-{instance_id}.pid').write_text(str(proc.pid))
    (CACHE_ROOT / f'.server-{instance_id}.port').write_text(str(port))

    if not _poll_health(port, timeout=15.0):
        # Server failed — clean up and surface log tail
        try:
            proc.terminate()
        except Exception:
            pass
        tail = ''
        try:
            with open(log_path, 'rb') as f:
                data = f.read()
                tail = data[-2000:].decode('utf-8', errors='replace')
        except Exception:
            pass
        _die(f'server did not become healthy in 15s. Log tail:\n{tail}')

    summary = {
        'url': f'http://127.0.0.1:{port}',
        'port': port,
        'pid': proc.pid,
        'cacheDir': str(cache_dir),
        'fileKey': meta['fileKey'],
        'rootNodeId': meta['rootNodeId'],
        'nodeCount': meta['nodeCount'],
    }
    print()
    print(f"Server ready at {summary['url']} (pid {summary['pid']})")
    print(f"Root node: {summary['rootNodeId']} in file {summary['fileKey']}")
    print(f"Nodes extracted: {summary['nodeCount']}")
    print(f"Cache dir: {summary['cacheDir']}")
    print()
    print('Try:')
    print(f"  curl {summary['url']}/overview | jq")
    print(f"  curl '{summary['url']}/context/{summary['rootNodeId']}' | jq")
    print()
    print('When done: python3 scripts/stop.py')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
