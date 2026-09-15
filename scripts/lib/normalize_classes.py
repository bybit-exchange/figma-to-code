"""
normalize_classes.py — In-place CSS class-name normalisation for IR trees.


Problems solved:
  1. Class names containing illegal characters (';', ':', '.' etc. that come
     from Figma instance-node ID concatenation).
  2. Class names that start with a digit (invalid in CSS / JS identifiers).
  3. Nodes with identical CSS can share a single class name (reduces SCSS bloat).

The IR is a plain Python dict with at minimum:
  {
    "semantic": {"className": str, ...},  # optional
    "css":      dict,                     # CSS property → value
    "figmaId":  str,
    "children": list[dict],
  }
"""

from __future__ import annotations
import json
import re


def normalize_class_names_in_place(ir: dict) -> None:
    """Walk *ir* in-place, sanitising and de-duplicating semantic.className values."""
    css_to_class: dict[str, str] = {}   # cssJson → assignedClassName
    used_names: set[str] = set()
    _walk(ir, css_to_class, used_names)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    """
    Turn an arbitrary string into a valid, clean CSS class name segment.

    Rules (mirrors the JS version):
    - Replace any character that is not alphanumeric, '-', or '_' with '-'
    - Collapse consecutive '-' into one
    - Strip leading/trailing '-'
    - Empty result → 'node'
    - Starts with digit → prepend 'n'
    """
    s = re.sub(r"[^a-zA-Z0-9\-_]", "-", str(name))
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    if not s:
        s = "node"
    if re.match(r"^\d", s):
        s = "n" + s
    return s


def _walk(ir: dict, css_to_class: dict[str, str], used_names: set[str]) -> None:
    semantic = ir.get("semantic")
    if semantic and semantic.get("className"):
        base = _sanitize(semantic["className"])
        css_json = json.dumps(ir.get("css", {}), sort_keys=True)

        if css_json in css_to_class:
            # Identical CSS → reuse existing class name
            semantic["className"] = css_to_class[css_json]
        else:
            name = base
            if name in used_names:
                # Same base name but different CSS → disambiguate with figmaId
                suffix = _sanitize(ir.get("figmaId", ""))
                name = f"{base}-{suffix}"
                n = 2
                while name in used_names:
                    name = f"{base}-{suffix}-{n}"
                    n += 1
            semantic["className"] = name
            used_names.add(name)
            css_to_class[css_json] = name

    for child in ir.get("children", []):
        _walk(child, css_to_class, used_names)
