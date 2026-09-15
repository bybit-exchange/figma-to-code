#!/usr/bin/env python3
"""
helpers.py — Shared test infrastructure for figma-to-code tests.

Provides:
  - check(test_id, desc, condition) — record pass/fail
  - make_node(**overrides) — create mock Figma node
  - make_ctx(**overrides) — create mock context
  - get_results() — return (_pass, _fail, _failures)
  - reset() — reset counters
  - print_summary() — print final results
"""

import sys
import re
import math
from pathlib import Path

# Ensure scripts/ is importable
_SCRIPTS_DIR = str(Path(__file__).parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ─── Test runner state ───────────────────────────────────────────────────────

_pass = 0
_fail = 0
_failures = []


def reset():
    """Reset pass/fail counters."""
    global _pass, _fail, _failures
    _pass = _fail = 0
    _failures = []


def check(test_id, desc, condition):
    """Record a test result."""
    global _pass, _fail
    if condition:
        _pass += 1
        print(f'  ✓  [{test_id}] {desc}')
    else:
        _fail += 1
        _failures.append(f'[{test_id}] {desc}')
        print(f'  ✗  [{test_id}] {desc}')


def get_results():
    """Return (pass_count, fail_count, failures_list)."""
    return _pass, _fail, _failures


def print_summary(label=None):
    """Print final pass/fail summary."""
    total = _pass + _fail
    prefix = f' ({label})' if label else ''
    print(f'\n{"=" * 44}')
    if _fail == 0:
        print(f'  ✅  全部通过{prefix} ({_pass}/{total})')
    else:
        print(f'  ❌  {_fail} 项失败，{_pass} 项通过{prefix} ({_pass}/{total})')
        for f in _failures:
            print(f'    · {f}')
    print('=' * 44)
    if _fail:
        sys.exit(1)
    return True


# ─── Mock helpers ────────────────────────────────────────────────────────────

def make_node(**overrides):
    """Create a mock Figma node dict."""
    base = {
        'type': 'FRAME', 'width': 100, 'height': 100,
        'layoutMode': None, 'layoutPositioning': 'ABSOLUTE',
        'constraints': {'horizontal': 'LEFT', 'vertical': 'TOP'},
    }
    base.update(overrides)
    return base


def make_ctx(**overrides):
    """Create a mock context dict."""
    base = {'layoutMode': 'NONE', 'width': 400, 'height': 400,
            'absX': 0, 'absY': 0, 'isRoot': False}
    base.update(overrides)
    return base


# ─── Product regression helpers ──────────────────────────────────────────────

_SOLID_BLACK = {'type': 'SOLID', 'color': {'r': 0, 'g': 0, 'b': 0, 'a': 1}, 'visible': True}
_SOLID_DARK = {'type': 'SOLID', 'color': {'r': 0.169, 'g': 0.169, 'b': 0.169, 'a': 1}, 'visible': True}


def node_has_css(scss, tsx, figma_id, css_prop, css_value=None):
    """Check that the node with given figma_id has css_prop in its CSS block."""
    lines = tsx.split('\n')
    line = next((l for l in lines if f'data-figma-id="{figma_id}"' in l), None)
    if not line:
        return None
    m = re.search(r"styles\['([^']+)'\]", line)
    if not m:
        return None
    cls = re.escape(m.group(1))
    block_m = re.search(rf'\.{cls}\s*\{{([^}}]+)\}}', scss, re.DOTALL)
    if not block_m:
        return None
    block = block_m.group(1)
    if css_value:
        return css_prop + ':' in block and css_value in block
    return css_prop + ':' in block


def class_lacks_property(scss, class_pattern, css_prop):
    """Return True if no block matching .class_pattern contains css_prop."""
    blocks = re.findall(rf'\.{class_pattern}[^{{]*\{{([^}}]+)\}}', scss, re.DOTALL)
    if not blocks:
        return None
    return all(css_prop + ':' not in b for b in blocks)


def read_page(out_dir, page_name, css_ext='less'):
    """Read TSX and Less files for a page."""
    out = Path(out_dir)
    if not out.exists():
        return None
    page_dir = None
    for d in out.iterdir():
        if not d.is_dir():
            continue
        if d.name == page_name or d.name.endswith(f'-{page_name}'):
            page_dir = d
            break
        if (d / f'{page_name}.tsx').exists():
            page_dir = d
            break
    if not page_dir:
        return None
    tsx_path = page_dir / f'{page_name}.tsx'
    style_path = page_dir / f'{page_name}.module.{css_ext}'
    if not tsx_path.exists() or not style_path.exists():
        return None
    return {'tsx': tsx_path.read_text(), 'scss': style_path.read_text(), 'dir': page_dir}
