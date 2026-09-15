from __future__ import annotations
#!/usr/bin/env python3
"""
prefetch_vars.py — Pre-warm the Figma variables cache for one or more files.

Use this for files whose /variables/local endpoint is slow or intermittent.
The script retries until success (or --max-retries is reached), giving the
Figma API more chances to respond than the inline convert.py timeout allows.

Usage:
  python3 scripts/prefetch_vars.py 'https://www.figma.com/design/<fileKey>/...'
  python3 scripts/prefetch_vars.py <url1> <url2> ...           # multiple files
  python3 scripts/prefetch_vars.py --max-retries=10 <url>      # more retries
  python3 scripts/prefetch_vars.py --clear <url>               # clear cache first

After successful prefetch, subsequent convert.py runs use the cache (~0.01s).
"""

import sys
import os
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))
from lib.figma_vars import _load_or_fetch, resolve_cache_path
from lib.paths import RAW_DATA_DIR


def parse_url(url: str) -> tuple[str | None, str]:
    """Return (file_key, node_id_safe) from a Figma URL."""
    try:
        u = urlparse(url)
        m = re.search(r'/(?:design|file)/([^/]+)', u.path)
        file_key = m.group(1) if m else None
        qs = parse_qs(u.query)
        node_id_raw = qs.get('node-id', [''])[0]
        node_id_safe = node_id_raw.replace('-', ':').replace(':', '-')
        return file_key, node_id_safe
    except Exception:
        return None, ''


def load_token() -> str | None:
    token_file = Path.home() / '.claude' / 'figma-token'
    if token_file.exists():
        t = token_file.read_text().strip()
        if t:
            return t
    return os.environ.get('FIGMA_ACCESS_TOKEN')


def prefetch_one(file_key: str, node_id_safe: str, token: str, raw_data_dir: Path,
                 max_retries: int = 5, clear: bool = False) -> bool:
    cache_path = resolve_cache_path(raw_data_dir, file_key, node_id_safe)

    if clear and cache_path.exists():
        cache_path.unlink()
        print(f'  🗑️  已清除缓存: {cache_path.name}')
        # Re-resolve after deletion
        cache_path = resolve_cache_path(raw_data_dir, file_key, node_id_safe)

    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        print(f'  [{attempt}/{max_retries}] 拉取中...', end='', flush=True)
        variables = _load_or_fetch(file_key, token, cache_path, force=True)
        elapsed = time.time() - t0

        if variables and not variables.get('_figma_vars_fetch_failed'):
            print(f' ✅ {len(variables)} 个变量 ({elapsed:.1f}s)')
            return True
        else:
            print(f' ❌ 失败 ({elapsed:.1f}s)', end='')
            if attempt < max_retries:
                wait = min(5 * attempt, 30)
                print(f' — {wait}s 后重试...')
                time.sleep(wait)
            else:
                print()

    return False


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    max_retries = 5
    clear = False
    urls = []

    for a in args:
        if a.startswith('--max-retries='):
            max_retries = int(a.split('=')[1])
        elif a == '--clear':
            clear = True
        else:
            urls.append(a)

    if not urls:
        print('❌  请提供至少一个 Figma URL')
        sys.exit(1)

    token = load_token()
    if not token:
        print('❌  未找到 Figma token（~/.claude/figma-token 或 FIGMA_ACCESS_TOKEN）')
        sys.exit(1)

    raw_data_dir = RAW_DATA_DIR
    raw_data_dir.mkdir(parents=True, exist_ok=True)

    # Deduplicate file keys (first URL per fileKey wins for nodeId naming)
    seen = {}  # {file_key: node_id_safe}
    for url in urls:
        fk, node_id_safe = parse_url(url)
        if fk:
            if fk not in seen:
                seen[fk] = node_id_safe
        else:
            print(f'⚠️  无法解析 fileKey: {url}')

    if not seen:
        print('❌  没有有效的 fileKey')
        sys.exit(1)

    print(f'\n🔑  预热 {len(seen)} 个 Figma 文件的变量缓存\n')
    results = {}
    for fk, node_id_safe in seen.items():
        print(f'📁  fileKey={fk}  nodeId={node_id_safe or "(未指定)"}')
        ok = prefetch_one(fk, node_id_safe, token, raw_data_dir,
                          max_retries=max_retries, clear=clear)
        results[fk] = ok
        print()

    # Summary
    succeeded = [k for k, v in results.items() if v]
    failed = [k for k, v in results.items() if not v]

    print('=' * 50)
    if not failed:
        print(f'✅  全部 {len(succeeded)} 个文件预热成功')
        print('   下次 convert.py 运行时将直接使用缓存，无需等待 API')
    else:
        if succeeded:
            print(f'✅  成功: {len(succeeded)} 个')
        print(f'❌  失败: {len(failed)} 个')
        for fk in failed:
            print(f'     {fk}')
        print()
        print('   失败的文件可能是 Figma 服务端持续挂起')
        print('   建议稍后重试，或在 Figma 里手动查看变量名后补充到 var_token_map.json')
        sys.exit(1)


if __name__ == '__main__':
    main()
