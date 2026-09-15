"""
denoise.py — walk an IR tree and add per-node `tags` classification.

Tags:
  - "likely-decorative"    — pure vector/gradient/shape, no TEXT descendants
  - "keep-text"            — TEXT node, or contains TEXT descendants
  - "component-instance"   — Figma INSTANCE type (or IR flag isComponentInstance)
  - "mask-container"       — has a masked child (or is itself a mask)

Also emits `zIndex` per child (index within parent's children array), which
approximates Figma's paint order (later children paint on top).

Public API:
  tag_ir(ir) -> ir_with_tags   (returns a new dict; does not mutate input)
  has_text_descendant(node) -> bool
  is_pure_decorative(node) -> bool
"""

from __future__ import annotations

import copy
from typing import Any


_DECORATIVE_FIGMA_TYPES = frozenset({
    'LINE', 'ELLIPSE', 'VECTOR', 'RECTANGLE',
    'POLYGON', 'STAR', 'BOOLEAN_OPERATION',
})


def has_text_descendant(node: dict) -> bool:
    if node.get('figmaType') == 'TEXT' or node.get('isTextNode'):
        return True
    for c in node.get('children') or []:
        if has_text_descendant(c):
            return True
    return False


def _is_masked_container(node: dict) -> bool:
    """True if any child is marked as a Figma mask."""
    for c in node.get('children') or []:
        if c.get('isMask') or c.get('figmaIsMask'):
            return True
    return False


def is_pure_decorative(node: dict) -> bool:
    """A node is pure decorative when:
      - No TEXT descendants, AND
      - Either a decorative Figma primitive, OR flagged isVectorNode / isDecorativeElement.
    """
    if has_text_descendant(node):
        return False
    if node.get('isDecorativeElement'):
        return True
    if node.get('isVectorNode'):
        return True
    figma_type = node.get('figmaType') or ''
    if figma_type in _DECORATIVE_FIGMA_TYPES:
        return True
    return False


def _tags_for(node: dict) -> list[str]:
    tags: list[str] = []
    figma_type = node.get('figmaType') or ''

    if figma_type == 'TEXT' or node.get('isTextNode') or has_text_descendant(node):
        tags.append('keep-text')

    if is_pure_decorative(node):
        tags.append('likely-decorative')

    if figma_type == 'INSTANCE' or node.get('isComponentInstance'):
        tags.append('component-instance')

    if _is_masked_container(node) or node.get('isMask') or node.get('figmaIsMask'):
        tags.append('mask-container')

    return tags


def tag_ir(ir: dict) -> dict:
    """Return a copy of the IR tree with `tags: list[str]` + `zIndex: int` set on every node."""
    out = copy.deepcopy(ir)
    _walk(out)
    return out


def _walk(node: dict, z: int = 0):
    node['tags'] = _tags_for(node)
    node['zIndex'] = z
    children = node.get('children') or []
    for i, c in enumerate(children):
        _walk(c, i)


# ─── Slim projection helpers (used by server context endpoint) ────────────

def project_child(node: dict) -> dict:
    """Return a shallow projection of a child node suitable for /context response.

    Keeps: id, name, type, layerKind, bounds, tags, css (only text-y css if keep-text).
    Drops: children (agent should re-query), heavy metadata.
    """
    figma_type = node.get('figmaType') or ''
    tags = node.get('tags') or _tags_for(node)
    css = node.get('css') or {}
    keep_css_keys = (
        {'color', 'fontSize', 'font-size', 'fontWeight', 'font-weight',
         'lineHeight', 'line-height', 'textAlign', 'text-align',
         'letterSpacing', 'letter-spacing', 'fontFamily', 'font-family'}
        if 'keep-text' in tags else set()
    )
    slim_css = {k: v for k, v in css.items() if k in keep_css_keys} if keep_css_keys else None

    layer_kind = _layer_kind(figma_type, node)
    projected: dict[str, Any] = {
        'id': node.get('figmaId'),
        'name': node.get('figmaName'),
        'type': figma_type,
        'layerKind': layer_kind,
        'tags': tags,
    }
    bb = node.get('bb') or node.get('absoluteBoundingBox')
    if bb:
        projected['bounds'] = {
            'x': bb.get('x'), 'y': bb.get('y'),
            'w': bb.get('width') or bb.get('w'),
            'h': bb.get('height') or bb.get('h'),
        }
    if slim_css:
        projected['css'] = slim_css
    return projected


def _layer_kind(figma_type: str, node: dict) -> str:
    if figma_type == 'TEXT':
        return 'text'
    if figma_type == 'INSTANCE' or node.get('isComponentInstance'):
        return 'component'
    if node.get('isVectorNode') or figma_type in _DECORATIVE_FIGMA_TYPES:
        return 'vector'
    if node.get('isImageNode') or node.get('fillImageRef'):
        return 'image'
    return 'frame'
