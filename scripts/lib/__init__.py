"""
Python utility library for the figma-to-code converter.

Modules:
  paths             — Centralised directory constants (.figma-to-code/* paths)
  color             — Figma color → CSS string helpers
  math_utils        — Statistical helpers (median, deviation, etc.)
  normalize_classes — In-place CSS class-name normalisation for IR trees
  layout_inference  — Flex-layout detection from Figma children geometry
  scss_generator    — SCSS text generation from an IR tree
  tsx_generator     — TSX component text generation from an IR tree
"""
