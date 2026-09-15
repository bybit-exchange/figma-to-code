from __future__ import annotations
#!/usr/bin/env python3
"""
从 Figma raw-data 生成 semantic.json（P4 前置步骤）。

用法:
  python3 scripts/semantic_context.py 'https://figma.com/...?node-id=42-100'
  python3 scripts/semantic_context.py --node-id=42-100

输出: .figma-to-code/2-figma-extract/{nodeId}.semantic.json
已存在时直接复用（缓存）。
"""

import sys
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))
from lib.semantic_extractor import extract_semantic_context
from lib.paths import raw_data_file, semantic_file


def _parse_node_id(arg: str) -> str | None:
    """从 URL 或 --node-id= 参数中提取 nodeId（连字符格式）。"""
    if arg.startswith('--node-id='):
        return arg.split('=', 1)[1].replace(':', '-')
    try:
        u = urlparse(arg)
        m = re.search(r'/(?:design|file)/([^/]+)', u.path)
        if m:
            qs = parse_qs(u.query)
            raw = qs.get('node-id', [''])[0]
            return raw.replace(':', '-') if raw else None
    except Exception:
        pass
    return None


def main() -> None:
    node_id_safe = None
    for arg in sys.argv[1:]:
        result = _parse_node_id(arg)
        if result:
            node_id_safe = result
            break

    if not node_id_safe:
        print('用法: python3 scripts/semantic_context.py <Figma URL> | --node-id=42-100')
        sys.exit(1)

    raw_path = raw_data_file(node_id_safe)
    out_path = semantic_file(node_id_safe)

    if out_path.exists():
        print(f'✓  semantic.json 已存在（复用缓存）: {out_path}')
        return

    if not raw_path.exists():
        print(f'❌  raw-data 不存在: {raw_path}')
        print('    请先运行 P1（convert.py）生成缓存，或确认 nodeId 正确')
        sys.exit(1)

    raw_data = json.loads(raw_path.read_text())
    ctx = extract_semantic_context(raw_data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False))

    print(f'✅  semantic.json 已生成: {out_path}')
    print(f'   组件实例组: {len(ctx["componentInstances"])} 个')
    print(f'   交互节点:   {len(ctx["interactiveNodes"])} 个')
    print(f'   已定稿 Frame: {len(ctx["readyFrames"])} 个')


if __name__ == '__main__':
    main()
