#!/usr/bin/env python3
"""
merge_responsive.py — PC + H5 双端 IR 合并，产出响应式扩展 IR。

用法：
  python3 merge_responsive.py --base-node-id=181-2980 --supplement-node-id=6-1998 --base=pc
  python3 merge_responsive.py --base-node-id=181-2980 --supplement-url='<H5 URL>' --base=pc --resume
  python3 merge_responsive.py ... --dry-run
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.node_matcher import get_top_sections, classify_matches, LowMatchRateError, extract_leaf_texts, normalize_tree
from lib.css_differ import css_diff
from lib.struct_scorer import structural_similarity, STRUCTURAL_SPLIT_THRESHOLD
from lib.paths import (
    BASE_DIR, IR_DIR as EXTRACT_DIR, ASSETS_STAGING_DIR,
    ir_file, merged_ir_file, match_report_file, match_confirmed_file,
)
DEFAULT_BREAKPOINT = 768


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Merge PC + H5 IR into responsive IR')
    p.add_argument('--base-node-id', help='Already-fetched base IR node-id (dash format)')
    p.add_argument('--supplement-node-id', help='Already-fetched supplement IR node-id (dash format)')
    p.add_argument('--base-url', help='Figma URL for base platform (fetches IR if not cached)')
    p.add_argument('--supplement-url', help='Figma URL for supplement platform')
    p.add_argument('--pc-url', help='Shortcut: PC Figma URL (implies --base=pc)')
    p.add_argument('--h5-url', help='Shortcut: H5 Figma URL')
    p.add_argument('--base', choices=['pc', 'h5'], default='pc')
    p.add_argument('--css-ext', default='less')
    p.add_argument('--breakpoint', type=int, default=DEFAULT_BREAKPOINT)
    p.add_argument('--dry-run', action='store_true', help='Preview changes, do not write files')
    p.add_argument('--resume', action='store_true',
                   help='Read match-confirmed.json and continue from Phase 3')
    return p.parse_args()


def _node_id_from_url(url: str) -> str:
    """Extract node-id in dash format from Figma URL."""
    m = re.search(r'node-id=([0-9]+-[0-9]+)', url)
    if not m:
        raise ValueError(f'Cannot extract node-id from URL: {url}')
    return m.group(1)


def _ensure_ir(node_id_safe: str, url: str | None, css_ext: str) -> Path:
    """Return IR path, fetching via convert.py if not cached."""
    ir_path = ir_file(node_id_safe)
    if ir_path.exists():
        return ir_path
    if not url:
        print(f'❌  IR 不存在 ({ir_path})，且未提供 URL，无法重建。')
        print(f'   请提供原始 Figma URL 重新生成 IR。')
        sys.exit(1)
    print(f'⟳  IR 不存在，重新拉取: {url}')
    convert = Path(__file__).parent / 'convert.py'
    result = subprocess.run(
        [sys.executable, str(convert), url, f'--css-ext={css_ext}'],
        capture_output=False,
    )
    if result.returncode != 0:
        print(f'❌  convert.py 失败，退出码 {result.returncode}')
        sys.exit(1)
    if not ir_path.exists():
        print(f'❌  convert.py 完成但 IR 仍不存在: {ir_path}')
        sys.exit(1)
    return ir_path


def _page_height(ir: dict) -> float:
    bb = ir.get('absoluteBoundingBox') or {}
    return float(bb.get('height', 8000))


# ── Phase 3: CSS diff ──────────────────────────────────────────────────────

def _find_matching_text_node(node: dict, text_content: str) -> dict | None:
    """Return the first TEXT leaf node in *node*'s subtree whose textContent
    matches *text_content* (case-sensitive, stripped).
    Used by U-433 to find the H5 text counterpart of a PC text node that
    structurally-splits with an H5 container.
    """
    if node.get('isTextNode') and (node.get('textContent') or '').strip() == text_content.strip():
        return node
    for ch in (node.get('children') or []):
        found = _find_matching_text_node(ch, text_content)
        if found:
            return found
    return None


def _merge_node_pair(base_node: dict, supp_node: dict, breakpoint: int) -> dict:
    """
    递归合并一对匹配节点。
    - 结构相似（sim >= STRUCTURAL_SPLIT_THRESHOLD）→ CSS diff + 递归子节点
    - 结构差异大 → structuralSplit + supplementNode（不做 CSS diff）
    """
    # Leaf text nodes always share DOM; text divergence is captured via supplementText
    is_text_leaf = base_node.get('isTextNode') and supp_node.get('isTextNode')
    if not is_text_leaf:
        sim = structural_similarity(base_node, supp_node)
        if sim < STRUCTURAL_SPLIT_THRESHOLD:
            result = {**base_node, 'structuralSplit': True, 'supplementNode': supp_node}
            # U-433: when a PC TEXT node structurally-splits with an H5 non-TEXT container,
            # look for the matching text node inside the H5 supplement subtree and apply
            # its CSS as responsive overrides. Without this, the PC text keeps its original
            # fixed-px width (e.g. width:250px) in H5, appearing misaligned inside the
            # H5 flex-start container instead of spanning the full container width.
            # Real case: EU deposit EarnFromSection 250:2649 ('To be eligible for the
            # rewards', 250px) splits with H5 250:3860 (full step container). The H5
            # counterpart text 250:3867 has width:100%; applying css_diff gives
            # responsive={max-width:none, white-space:normal}, fixing the visual alignment.
            if base_node.get('isTextNode') and not supp_node.get('isTextNode'):
                base_text = (base_node.get('textContent') or '').strip()
                if base_text:
                    h5_match = _find_matching_text_node(supp_node, base_text)
                    if h5_match:
                        h5_css = h5_match.get('css') or {}
                        pc_css = base_node.get('css') or {}
                        _, resp = css_diff(pc_css, h5_css)
                        # In structural splits, base CSS is NOT updated (pc width stays fixed).
                        # U-431 generates max-width:none + drops width:100% from responsive,
                        # but here we need width:100% in responsive to override the PC fixed
                        # width — the base never got the width:100% conversion that U-431
                        # relies on. Replace max-width:none with explicit width:100%.
                        if resp.get('max-width') == 'none':
                            h5_width = h5_css.get('width', '')
                            if h5_width and not re.match(r'^\d+(\.\d+)?px$', str(h5_width)):
                                resp.pop('max-width', None)
                                resp['width'] = h5_width
                        if resp:
                            result['responsive'] = (base_node.get('responsive') or []) + [{
                                'breakpoint': breakpoint,
                                'css': resp,
                                'confidence': 0.7,
                            }]
            return result

    # CSS diff（保留现有逻辑）
    base_css = base_node.get('css') or {}
    supp_css = supp_node.get('css') or {}
    base_styles, responsive_overrides = css_diff(base_css, supp_css)

    merged = {**base_node, 'css': base_styles}
    if responsive_overrides:
        existing = merged.get('responsive') or []
        merged['responsive'] = existing + [{
            'breakpoint': breakpoint,
            'css': responsive_overrides,
            'confidence': 1.0,
        }]

    # 文字内容不同 → 记录 supplementText
    base_text = base_node.get('textContent')
    supp_text = supp_node.get('textContent')
    if base_text and supp_text and base_text != supp_text:
        merged['supplementText'] = supp_text

    # 递归处理子节点
    base_children = [c for c in (base_node.get('children') or []) if c.get('visible', True)]
    supp_children = [c for c in (supp_node.get('children') or []) if c.get('visible', True)]
    if base_children and supp_children:
        merged['children'] = _merge_children(base_children, supp_children, breakpoint)

    # U-435: when parent is flex-centered and a TEXT child has text-align:left width:100%,
    # add text-align:center to the child's H5 responsive so it appears centered in H5.
    # In PC, such buttons are narrow — the text naturally fills the narrow width and
    # appears centered. In H5, the parent expands to full-width; without this fix the
    # text sits at the left of a wide span. The @media override wins over the base
    # .class{text-align:left} at the same CSS specificity because it comes later.
    # Real case: EU deposit hero CTA 250:2631 "Register Now" button text (250:2631;2:8053).
    if (base_styles.get('display') == 'flex' and
            base_styles.get('justify-content') == 'center' and
            'children' in merged):
        _children_out = list(merged['children'])
        for _ci, _child in enumerate(_children_out):
            _ccss = _child.get('css') or {}
            if (_child.get('isTextNode') and
                    _ccss.get('text-align') == 'left' and
                    _ccss.get('width') == '100%'):
                _existing = _child.get('responsive') or []
                _has_ta = any('text-align' in _r.get('css', {}) for _r in _existing)
                if not _has_ta:
                    _children_out[_ci] = {**_child, 'responsive': _existing + [{
                        'breakpoint': breakpoint,
                        # Use !important to override [dir="ltr"] .class { text-align:left }
                        # which the project's RTL PostCSS plugin generates at specificity 11
                        # (attribute + class) — higher than our @media class-only rule (10).
                        'css': {'text-align': 'center !important'},
                        'confidence': 0.7,
                    }]}
        merged['children'] = _children_out

    return merged


def _has_text_content(node: dict) -> bool:
    """Return True if node or any descendant has text/textContent."""
    if node.get('isTextNode') or node.get('textContent'):
        return True
    return any(_has_text_content(c) for c in node.get('children', []))


def _collect_node_texts(node: dict) -> set:
    """Collect all text strings from node and its descendants.

    Used by Fix B to check whether an unmatched PC child's text content
    is fully covered by the matched H5 subtree. If all PC text exists in H5,
    the nodes represent the same content in a different structure, so the
    PC node should be SHARED rather than pcOnly.
    """
    result: set = set()
    tc = node.get('textContent')
    if tc:
        result.add(tc.strip())
    for ch in node.get('children', []):
        result.update(_collect_node_texts(ch))
    return result


def _collect_node_image_refs(node: dict) -> set:
    """Collect all imageRef values from node and its descendants.

    Used by Fix D to detect icon/image nodes whose visual content is identical
    across PC and H5 platforms (same imageRef → same image asset). When an
    unmatched PC icon has all its imageRefs present in matched H5 subtrees,
    the icon represents shared content and should NOT be marked pcOnly.

    Real case: EU campaign EarnFromSection Trade icons (250:2637, 250:2644).
    PC step items have flat [icon, title, desc] while H5 wraps [icon, text] in
    an extra container. The icons share the same imageRef hash, confirming they
    are the same image. Fix B can't rescue them (no text), but Fix D can.
    """
    result: set = set()
    ref = node.get('imageRef')
    if ref:
        result.add(ref)
    for ch in (node.get('children') or []):
        result.update(_collect_node_image_refs(ch))
    return result


def _collect_node_image_refs_non_h5only(node: dict) -> set:
    """Like _collect_node_image_refs but skips h5Only subtrees.

    Used by Fix D2: if a PC icon's imageRef only appears inside h5Only nodes
    within matched H5 subtrees, the H5 uses a SEPARATE version of that icon.
    The PC icon should be pcOnly, not shared.

    Real case: EU campaign EarnFromSection step 1 — 250:2637 (PC icon, shared
    imageRef fa1fc450) and 250:3851 (H5 icon, h5Only, same imageRef fa1fc450).
    Fix D (original) incorrectly marks 250:2637 as SHARED because fa1fc450 IS
    in matched_h5_image_refs. Fix D2 adds a check: fa1fc450 only appears in
    h5Only subtrees → 250:2637 should be pcOnly (H5 has its own sized version).
    """
    if node.get('h5Only'):
        return set()  # Skip h5Only subtrees entirely
    result: set = set()
    ref = node.get('imageRef')
    if ref:
        result.add(ref)
    for ch in (node.get('children') or []):
        result.update(_collect_node_image_refs_non_h5only(ch))
    return result


def _has_wide_descendant(node: dict, breakpoint: int) -> bool:
    """Return True if node or any descendant has a fixed CSS width wider than breakpoint.

    Used by Fix C to detect when a h5Only sibling contains content that overflows the
    mobile viewport. When True, overflow-x: auto is added to the sibling's responsive
    CSS so the content scrolls horizontally instead of overflowing the page body.
    """
    css = node.get('css') or {}
    w = str(css.get('width', '')).strip()
    try:
        if w.endswith('px') and float(w[:-2]) > breakpoint:
            return True
    except ValueError:
        pass
    return any(_has_wide_descendant(c, breakpoint) for c in node.get('children', []))


def _remove_h5only_descendants(node: dict) -> dict:
    """Recursively strip h5Only-marked children from a node tree.

    Used when the h5_only_structural check promotes a merged node to pcOnly:
    the H5 supplement is added as a separate h5Only sibling, so h5Only descendants
    inside the now-pcOnly node are redundant (they'd be invisible anyway under a
    pcOnly parent) and create clutter in the IR.
    """
    children = node.get('children') or []
    if not children:
        return node
    cleaned = [_remove_h5only_descendants(c) for c in children if not c.get('h5Only')]
    return {**node, 'children': cleaned}


def _abs_leaf(node: dict) -> bool:
    """Return True when node is position:absolute with no children (decorative overlay)."""
    return ((node.get('css') or {}).get('position') == 'absolute'
            and not node.get('children'))


def _fix_class_collisions(nodes: list) -> list:
    """Rename h5Only nodes' cssClass with '-h5' suffix when they share a cssClass with
    a pcOnly sibling.

    split_codegen._collect_inline_responsive_flags keys on cssClass. When pcOnly and
    h5Only siblings share the same class, it detects a conflict (flag=None) and falls
    back to [data-figma-id] selectors for both. Adding '-h5' gives each node a unique
    class so split_codegen can handle them independently via normal class selectors.

    Only top-level nodes in `nodes` are checked — children keep their own classes because
    they're nested under a platform-specific parent and visibility is inherited.
    """
    pc_classes = {n.get('cssClass') for n in nodes if n.get('pcOnly') and n.get('cssClass')}
    if not pc_classes:
        return nodes
    result = []
    for node in nodes:
        cls = node.get('cssClass', '')
        if node.get('h5Only') and cls and cls in pc_classes:
            node = {**node, 'cssClass': cls + '-h5'}
        result.append(node)
    return result


def _merge_children(
    base_children: list,
    supp_children: list,
    breakpoint: int,
    auto_threshold: float = 0.55,  # 子节点文本少，阈值宽松
) -> list:
    """贪心配对子节点列表并递归合并，未配对的打 h5Only/pcOnly 标记。"""
    from lib.node_matcher import match_score
    dummy_height = 10000.0

    # 评分所有对
    all_scores = []
    for i, b in enumerate(base_children):
        for j, s in enumerate(supp_children):
            score = match_score(s, b, dummy_height, dummy_height)
            all_scores.append((i, j, score))
    all_scores.sort(key=lambda x: -x[2])

    used_base: set = set()
    used_supp: set = set()
    pairs: list = []

    for bi, si, score in all_scores:
        if bi in used_base or si in used_supp:
            continue
        if score >= auto_threshold:
            pairs.append((bi, si))
            used_base.add(bi)
            used_supp.add(si)

    # Fix B: collect all text content from matched H5 supp_children (at all depths).
    # Used below to detect unmatched PC children that are SHARED content — i.e., PC nodes
    # whose text appears in the matched H5 subtree but couldn't be structurally paired
    # because the H5 uses a different nesting structure.
    # Real case: EU campaign EarnFromSection step item — PC has flat [icon, title, desc],
    # H5 has nested [wrapper → [icon, title+desc]]. The PC title "Sign up for By EU" pairs
    # with the H5 wrapper's desc container, leaving the PC title unmatched → pcOnly.
    # Fix B detects that the PC title text IS in the matched H5 subtree and marks it SHARED.
    _matched_h5_texts: set = set()
    for _, si_p in pairs:
        _matched_h5_texts.update(_collect_node_texts(supp_children[si_p]))

    # Fix D: collect imageRefs from matched H5 subtrees AND matched PC subtrees.
    # Used to detect icon/image nodes that are shared content despite structural differences.
    # Real case: EarnFromSection Trade icons (250:2637/250:2644) — text-free icon frames
    # with the same imageRef as their H5 counterparts. Fix B skips them (no text).
    # Fix D: if unmatched PC icon's imageRef(s) exist in matched H5 subtrees → SHARED.
    # Fix D-sym: if unmatched H5 icon's imageRef(s) all exist in matched PC subtrees →
    # skip adding as h5Only (already represented by the shared PC icon above).
    _matched_h5_image_refs: set = set()
    _matched_pc_image_refs: set = set()
    for bi_p, si_p in pairs:
        _matched_h5_image_refs.update(_collect_node_image_refs(supp_children[si_p]))
        _matched_pc_image_refs.update(_collect_node_image_refs(base_children[bi_p]))

    # 构建结果列表（保持 base 顺序）
    # result_supp_idx: tracks which supp_child index each result item corresponds to (-1 = none)
    result: list = []
    result_supp_idx: list = []

    for i, b in enumerate(base_children):
        if i in used_base:
            si = next(si for bi, si in pairs if bi == i)
            s = supp_children[si]
            # Both absolute-positioned leaf nodes are platform-specific decoratives
            # (e.g. background images). Don't merge — emit both with pcOnly/h5Only so
            # CSS can show/hide each on its platform.
            # Real case: EU campaign hero 250:2621 (PC dark bg) paired with 250:3834
            # (H5 orange bg). Merging discards the H5 image; splitting preserves it.
            if _abs_leaf(b) and _abs_leaf(s):
                result.append({**b, 'pcOnly': True})
                result_supp_idx.append(-1)
                # H5 abs-leaf: add responsive reset for right/bottom/left/top positioning
                # to prevent viewport overflow on mobile. Absolute backgrounds often use
                # negative right/bottom to extend beyond the container edge (e.g. 250:3834
                # EU hero bg has right: -345px). Without a reset, the element extends
                # beyond the viewport even when the container has overflow:hidden, causing
                # a horizontal scrollbar.
                h5_css_vals = s.get('css') or {}
                resp_overrides = {}
                for _pos in ('right', 'bottom', 'left', 'top'):
                    if _pos not in h5_css_vals:
                        continue
                    val_str = str(h5_css_vals[_pos]).strip()
                    # Only reset plain px values that are significantly negative (> 50px
                    # outside the container edge) — these cause actual viewport overflow.
                    # Skip: small values (< 50px, contained by overflow:hidden),
                    #        calc()/% values (internal layout, must not be disturbed).
                    if not val_str.endswith('px'):
                        continue
                    try:
                        if float(val_str[:-2]) < -50:
                            resp_overrides[_pos] = '0px'
                    except ValueError:
                        pass
                if resp_overrides:
                    existing_resp = list(s.get('responsive') or [])
                    h5_node = {**s, 'h5Only': True,
                               'responsive': existing_resp + [{'breakpoint': breakpoint, 'css': resp_overrides}]}
                else:
                    h5_node = {**s, 'h5Only': True}
                result.append(h5_node)
                result_supp_idx.append(si)
            else:
                result.append(_merge_node_pair(b, s, breakpoint))
                result_supp_idx.append(si)
        else:
            # Fix B: if ALL of this PC node's text content already appears in the matched
            # H5 subtrees, the node represents the same content in a different structure.
            # Keep it SHARED so it renders on both platforms (the H5 wrapper shows the same
            # text, so the PC version won't be missing on mobile).
            # Only applies when the node HAS text (purely structural nodes with no text,
            # like icon frames, stay pcOnly as intended).
            b_texts = _collect_node_texts(b)
            b_image_refs = _collect_node_image_refs(b)
            if b_texts and b_texts.issubset(_matched_h5_texts):
                result.append(dict(b))  # SHARED — Fix B: same text content exists in matched H5
            elif (not b_texts and b_image_refs
                  and b_image_refs.issubset(_matched_h5_image_refs)):
                # Fix D2: if H5 matched node has ≥2 direct children, one of which
                # contains this imageRef, the icon will become h5Only in the inner
                # merge (text siblings match, leaving icon as unmatched h5Only).
                # Marking PC icon SHARED would then create a duplicate on H5.
                # Only applies when the icon is at a shallow depth inside the H5 match.
                _h5_will_produce_duplicate = any(
                    len([_c for _c in (supp_children[_si].get('children') or [])
                         if _c.get('visible', True)]) >= 2
                    and any(
                        _collect_node_image_refs(_c).intersection(b_image_refs)
                        for _c in (supp_children[_si].get('children') or [])
                        if _c.get('visible', True)
                    )
                    for _, _si in pairs
                )
                if _h5_will_produce_duplicate:
                    marked = dict(b)
                    marked['pcOnly'] = True
                    result.append(marked)  # pcOnly — Fix D2: h5Only backup will exist in inner merge
                else:
                    result.append(dict(b))  # SHARED — Fix D: H5 icon won't be h5Only, must share PC node
            else:
                marked = dict(b)
                marked['pcOnly'] = True
                result.append(marked)
            result_supp_idx.append(-1)

    # Insert supp-only nodes at the correct position relative to matched supp children.
    # A supp-only node at index j should appear BEFORE the first result whose supp_idx > j.
    # This preserves H5 reading order (e.g. FAQ header j=0 before FAQ content j=1).
    # Without ordering: h5Only nodes always appended at end, causing H5 header to render
    # AFTER the T&C section on mobile (EU campaign Component10Section FaqHeader).
    for j in sorted(jj for jj in range(len(supp_children)) if jj not in used_supp):
        s = supp_children[j]
        # Fix D-sym: if this H5-only node is a text-free icon whose imageRefs are ALL
        # present in matched PC subtrees, it's already covered by the shared PC icon above.
        # Adding it as h5Only would create a DUPLICATE icon on mobile.
        # U-464: also require imageRefs ⊆ _matched_h5_image_refs — if the imageRefs only
        # exist in matched PC subtrees but NOT in matched H5 subtrees, the PC node may be
        # pcOnly (hidden on mobile) and the H5-only card/image must be preserved.
        # Real case: Tomorrowland VIP Card section — H5 card images (39603:19899) share
        # the same imageRefs as PC card (39641:5115, inside matched pc_main 39641:5102).
        # Without this check, Fix D-sym skips the H5 cards → blank card area on mobile.
        s_texts = _collect_node_texts(s)
        s_image_refs = _collect_node_image_refs(s)
        if (not s_texts and s_image_refs
                and s_image_refs.issubset(_matched_pc_image_refs)
                and s_image_refs.issubset(_matched_h5_image_refs)):  # U-464
            continue  # Fix D-sym: icon already covered by SHARED node on both platforms
        marked = {**s, 'h5Only': True}
        insert_at = len(result)
        for k, si in enumerate(result_supp_idx):
            if si != -1 and si > j:
                insert_at = k
                break
        result.insert(insert_at, marked)
        result_supp_idx.insert(insert_at, j)

    # Post-process: when h5Only siblings outnumber or equal paired nodes, the H5 section
    # uses a fundamentally different layout. Mark paired PC nodes as pcOnly so the renderer
    # wraps them in a desktop-only div (hidden via @media max-width: 768px).
    # This fixes cases like EU campaign TieredRewardsSection where the PC table (1200px) is
    # incorrectly paired with H5 accordion content (same tier text, score=0.948), while the
    # H5 accordion wrapper (250:4576) stays h5Only — the PC table must be pcOnly to hide on mobile.
    #
    # EXCLUDE h5Only nodes with position:absolute — they are decorative overlays (e.g. gradient
    # fades, badges), NOT structural mobile replacements. Without this exclusion, a simple
    # gradient overlay in the H5 hero would trigger pcOnly on the hero text column, hiding
    # ALL hero content on mobile (EU campaign 250:3836 Rectangle vs 250:2624 hero-text-col).
    #
    # EXCLUDE h5Only TEXT nodes that are title variants (different wording of the same title).
    # Title variants occur when BOTH PC and H5 have unmatched TEXT title siblings: the PC title
    # becomes pcOnly and the H5 title becomes h5Only. In this pattern the h5Only TEXT is
    # additive (not a structural replacement), so it should not trigger pcOnly on paired nodes.
    # Real case: EU campaign JoinSection — 250:4894 (h5Only "Join 70M+") paired alongside
    # 250:3770 (pcOnly "Join 70+ million"), causing trust-cards (250:3771) to wrongly become
    # pcOnly and disappear on mobile.
    # DISTINCTION: h5Only TEXT nodes that have NO corresponding pcOnly TEXT sibling ARE
    # structural (e.g., H5-specific subtitle text "Earn up to €170..." in OriginalCopySection
    # has no PC equivalent at the same level), so they DO count and must trigger pcOnly.
    _has_pconly_text_sibling = any(r.get('pcOnly') and r.get('isTextNode') for r in result)
    h5_only_structural_count = sum(
        1 for r in result
        if r.get('h5Only')
        and (r.get('css') or {}).get('position') != 'absolute'
        # isTextNode h5Only nodes are title variants ONLY when a pcOnly TEXT sibling exists
        and not (r.get('isTextNode') and _has_pconly_text_sibling)
        and _has_text_content(r)      # icon-only frames are additive, not structural replacements
    )
    if h5_only_structural_count >= len(pairs) > 0:
        # Split each merged pair into PC (pcOnly) + H5 (h5Only sibling) so the H5 content
        # can be rendered independently on mobile. Without the split, the H5 supp content
        # is absorbed inside a pcOnly parent and becomes invisible (display:none wins over
        # the h5Only children's display:block). The split ensures the H5 version is a
        # separate sibling at the same level as the pcOnly PC node.
        #
        # Real case: EU campaign CalculateYourCashbackSection — H5 calculator content
        # (250:4861) pairs with PC calculator (250:3768). The h5_only check fires because
        # H5 sticky bar (250:4859) is structural. Without the split, 250:3768 becomes pcOnly
        # and 250:4861 is absorbed inside it, hidden on mobile. With the split, 250:4861 is
        # extracted as h5Only sibling and renders the full H5 calculator on mobile.
        new_result: list = []
        new_supp_idx: list = []
        for k, r in enumerate(result):
            si = result_supp_idx[k]
            if not r.get('h5Only') and not r.get('pcOnly'):
                # Merged pair → emit PC as pcOnly (strip its h5Only descendants, they're
                # now represented by the sibling h5Only node below)
                new_result.append(_remove_h5only_descendants({**r, 'pcOnly': True}))
                new_supp_idx.append(-1)
                # Re-emit the H5 supplement as h5Only sibling (using the ORIGINAL supp
                # node, not the merge result) so the H5 layout is preserved intact.
                # If the H5 content has descendants wider than the mobile breakpoint,
                # add overflow-x: auto so wide tables/grids scroll instead of overflowing
                # the page body (e.g., EU campaign TieredRewardsSection tier table 908px).
                if si != -1 and si < len(supp_children):
                    h5_supp = supp_children[si]
                    if _has_wide_descendant(h5_supp, breakpoint):
                        existing_resp = list(h5_supp.get('responsive') or [])
                        h5_supp = {
                            **h5_supp,
                            'responsive': existing_resp + [
                                {'breakpoint': breakpoint, 'css': {'overflow-x': 'auto'}}
                            ],
                        }
                    new_result.append({**h5_supp, 'h5Only': True})
                    new_supp_idx.append(si)
            else:
                new_result.append(r)
                new_supp_idx.append(result_supp_idx[k])
        result = new_result
        result_supp_idx = new_supp_idx

    result = _fix_class_collisions(result)
    return result


# ── Phase 4: build merged IR ───────────────────────────────────────────────

def _build_merged_ir(
    base_ir: dict,
    supp_ir: dict,
    auto_matches: list[tuple[int, int, float]],
    h5_only: list[int],
    pc_only: list[int],
    base_platform: str,
    breakpoint: int,
) -> dict:
    base_sections = get_top_sections(base_ir)
    supp_sections = get_top_sections(supp_ir)

    # Build lookup: base_idx → merged node
    base_idx_to_merged: dict[int, dict] = {}
    for h5_i, pc_i, score in auto_matches:
        if base_platform == 'pc':
            base_node = base_sections[pc_i]
            supp_node = supp_sections[h5_i]
        else:
            base_node = base_sections[h5_i]
            supp_node = supp_sections[pc_i]
        base_idx_to_merged[pc_i if base_platform == 'pc' else h5_i] = \
            _merge_node_pair(base_node, supp_node, breakpoint)

    # Supplement-only nodes
    supp_only_indices = h5_only if base_platform == 'pc' else pc_only
    supp_only_nodes = []
    for idx in supp_only_indices:
        node = dict(supp_sections[idx])
        if base_platform == 'pc':
            node['h5Only'] = True
        else:
            node['pcOnly'] = True
        supp_only_nodes.append((idx, node))

    # Rebuild children list in base (PC/H5) section order — U-441.
    # Previously the list was built in H5 supplement-index order to preserve z-index
    # stacking for absolute backgrounds, but that caused sections to render out of
    # sequence when PC and H5 matched at cross-indices (e.g. DemoTrading 202:33464).
    #
    # Algorithm: iterate base sections in order; for supplement-only (h5Only/pcOnly)
    # nodes, insert each one immediately BEFORE the first base section whose matched
    # supplement index is higher than the supplement-only node's own index.
    # This preserves both (1) base section order and (2) z-index stacking of H5
    # absolute backgrounds (U-436: BgRect at supp_idx=0 inserts before the matched
    # section at supp_idx=2, keeping BgRect below the content layer).
    from bisect import bisect_right as _bisect_right

    # Build sorted list of (supp_idx, base_idx) for matched pairs
    _sorted_supp_idxs: list = []
    _supp_to_base_idx: dict = {}
    for _h5_i, _pc_i, _score in auto_matches:
        _s = _h5_i if base_platform == 'pc' else _pc_i
        _b = _pc_i if base_platform == 'pc' else _h5_i
        _sorted_supp_idxs.append(_s)
        _supp_to_base_idx[_s] = _b
    _sorted_supp_idxs.sort()

    # For each supplement-only node, determine which base section it should precede
    _h5only_before_base: dict = {}  # base_idx → [supp_nodes to insert before it]
    _h5only_tail: list = []
    for _supp_idx, _supp_node in supp_only_nodes:
        _pos = _bisect_right(_sorted_supp_idxs, _supp_idx)
        if _pos < len(_sorted_supp_idxs):
            _ins_base = _supp_to_base_idx[_sorted_supp_idxs[_pos]]
            _h5only_before_base.setdefault(_ins_base, []).append(_supp_node)
        else:
            _h5only_tail.append(_supp_node)

    new_children = []
    for i, base_child in enumerate(base_sections):
        new_children.extend(_h5only_before_base.get(i, []))
        if i in base_idx_to_merged:
            new_children.append(base_idx_to_merged[i])
        else:
            marked = {**base_child}
            if base_platform == 'pc':
                marked['pcOnly'] = True
            else:
                marked['h5Only'] = True
            new_children.append(marked)
    new_children.extend(_h5only_tail)

    # Return merged IR with updated children
    root = normalize_tree(base_ir)
    merged_root = {**root, 'children': new_children}
    # h5SectionOrder records H5 section name sequence for tsx_generator
    merged_root['h5SectionOrder'] = [s.get('figmaName', '') for s in supp_sections]
    # h5RootHeight: H5 page height used by scss_generator to inject mobile
    # min-height on the merged root container, preventing height collapse when
    # the PC block child is hidden at mobile and all H5 nodes are absolute.
    # Use bb.height (from IR extraction) as absoluteBoundingBox may be absent.
    supp_bb = supp_ir.get('bb') or supp_ir.get('absoluteBoundingBox') or {}
    supp_h = supp_bb.get('height')
    if supp_h:
        merged_root['h5RootHeight'] = float(supp_h)
    return merged_root


# ── Output ─────────────────────────────────────────────────────────────────

def _write_report(base_node_id: str, auto: list, pending: list,
                  base_sections: list, supp_sections: list,
                  h5_only: list, pc_only: list, base_platform: str) -> Path:
    def _section_info(sections, idx, score=None):
        node = sections[idx]
        texts = list(extract_leaf_texts(node))[:3]
        d = {'node_id': node.get('figmaId', ''), 'name': node.get('figmaName', '')}
        if score is not None:
            d['score'] = round(score, 3)
        if texts:
            d['preview_texts'] = texts
        return d

    report = {
        'auto': [
            {
                'base_idx': pc_i if base_platform == 'pc' else h5_i,
                'supplement_idx': h5_i if base_platform == 'pc' else pc_i,
                'base': _section_info(base_sections, pc_i if base_platform == 'pc' else h5_i),
                'supplement': _section_info(supp_sections, h5_i if base_platform == 'pc' else pc_i, score),
                'score': round(score, 3),
            }
            for h5_i, pc_i, score in auto
        ],
        'pending_confirm': [
            {
                'base_idx': pc_i if base_platform == 'pc' else h5_i,
                'supplement_idx': h5_i if base_platform == 'pc' else pc_i,
                'base': _section_info(base_sections, pc_i if base_platform == 'pc' else h5_i),
                'supplement': _section_info(supp_sections, h5_i if base_platform == 'pc' else pc_i, score),
                'score': round(score, 3),
            }
            for h5_i, pc_i, score in pending
        ],
        'base_only': [_section_info(base_sections, i) for i in (pc_only if base_platform == 'pc' else h5_only)],
        'supplement_only': [_section_info(supp_sections, i) for i in (h5_only if base_platform == 'pc' else pc_only)],
    }
    path = match_report_file(base_node_id)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f'✓  匹配报告: {path}')
    return path


def _apply_confirmed(auto_matches, pending_matches, confirmed_path: Path,
                     h5_only, pc_only, base_platform) -> tuple[list, list, list]:
    """Read match-confirmed.json and merge with auto matches.

    match-confirmed.json format expected:
      {"confirmed": [{"base_idx": N, "supplement_idx": M, "score": 1.0}, ...]}
    Older format without indices falls back to positional lookup in pending_matches.
    """
    if not confirmed_path.exists():
        print(f'❌  {confirmed_path} 不存在，请先完成半自动确认。')
        sys.exit(1)
    confirmed_data = json.loads(confirmed_path.read_text())

    new_auto = list(auto_matches)
    new_h5_only = list(h5_only)

    for i, item in enumerate(confirmed_data.get('confirmed', [])):
        if 'base_idx' in item and 'supplement_idx' in item:
            b_idx = item['base_idx']
            s_idx = item['supplement_idx']
            score = item.get('score', 1.0)
        elif i < len(pending_matches):
            # Fall back to positional match in pending list (pre-index-storage format)
            h5_i, pc_i, score = pending_matches[i]
            if base_platform == 'pc':
                b_idx, s_idx = pc_i, h5_i
            else:
                b_idx, s_idx = h5_i, pc_i
        else:
            continue

        if base_platform == 'pc':
            new_auto.append((s_idx, b_idx, score))
        else:
            new_auto.append((b_idx, s_idx, score))

        # Remove from h5_only if it was there
        if s_idx in new_h5_only:
            new_h5_only.remove(s_idx)

    # Unconfirmed pending pairs: supplement (H5) sections were excluded from
    # h5_only_idxs because they were "tentatively matched". When skipped, they
    # must be added back so they appear as h5Only nodes in the merged IR.
    confirmed_s_idxs = {item['supplement_idx']
                        for item in confirmed_data.get('confirmed', [])
                        if 'supplement_idx' in item}
    for h5_i, pc_i, _score in pending_matches:
        s_idx = h5_i if base_platform == 'pc' else pc_i
        if s_idx not in confirmed_s_idxs and s_idx not in new_h5_only:
            new_h5_only.append(s_idx)

    # Restore original H5 section order (sort by index so the merged IR
    # preserves the Figma visual top-to-bottom sequence).
    new_h5_only.sort()

    return new_auto, [], new_h5_only


def _merge_staging_assets(base_ir: dict, supp_ir: dict,
                          base_node_id: str, supp_node_id: str,
                          staging_base: Path | None = None) -> None:
    """Merge PC+H5 staging asset dirs into a single merged-* staging dir.

    After merge_responsive.py writes the merged IR, the per-platform asset dirs
    (e.g. 1-assets/Web-250-2618/ and 1-assets/H5-250-3798/) must be combined so
    that split_components.py --apply can find assets under the merged node ID
    (e.g. 1-assets/Web-merged-250-2618/).  PC files take priority on name conflict.
    """
    _base = staging_base if staging_base is not None else ASSETS_STAGING_DIR

    base_name = base_ir.get('name', 'Component')
    supp_name = supp_ir.get('name', 'Component')

    src_pc = _base / f'{base_name}-{base_node_id}'
    src_h5 = _base / f'{supp_name}-{supp_node_id}'
    dest   = _base / f'{base_name}-merged-{base_node_id}'

    sources = [s for s in [src_pc, src_h5] if s.is_dir()]
    if not sources:
        return

    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sources:
        for f in sorted(src.iterdir()):
            if f.is_file() and not (dest / f.name).exists():
                shutil.copy2(str(f), str(dest / f.name))
                count += 1

    if count:
        print(f'   ✓  资源暂存区已合并：{src_pc.name}+{src_h5.name} → {dest.name}/ ({count} 个文件)')


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    # Resolve URLs and node IDs
    if args.pc_url and args.h5_url:
        if args.base == 'pc':
            base_url, supp_url = args.pc_url, args.h5_url
        else:
            base_url, supp_url = args.h5_url, args.pc_url
        base_node_id = _node_id_from_url(base_url)
        supp_node_id = _node_id_from_url(supp_url)
    else:
        base_node_id = args.base_node_id
        supp_node_id = args.supplement_node_id
        base_url = args.base_url
        supp_url = args.supplement_url
        if not base_node_id:
            print('❌  必须提供 --base-node-id 或 --pc-url + --h5-url')
            sys.exit(1)

    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure IRs exist (fetch if missing)
    base_ir_path = _ensure_ir(base_node_id, base_url, args.css_ext)
    if supp_node_id:
        supp_ir_path = _ensure_ir(supp_node_id, supp_url, args.css_ext)
    elif supp_url:
        supp_node_id = _node_id_from_url(supp_url)
        supp_ir_path = _ensure_ir(supp_node_id, supp_url, args.css_ext)
    else:
        print('❌  必须提供 --supplement-node-id 或 --supplement-url')
        sys.exit(1)

    base_ir = json.loads(base_ir_path.read_text())
    supp_ir = json.loads(supp_ir_path.read_text())

    base_sections = get_top_sections(base_ir)
    supp_sections = get_top_sections(supp_ir)
    base_h = _page_height(base_ir)
    supp_h = _page_height(supp_ir)

    confirmed_path = match_confirmed_file(base_node_id)

    if args.resume:
        # --resume: skip Phase 1+2, apply confirmed matches
        report_path = match_report_file(base_node_id)
        if not report_path.exists():
            print(f'❌  match-report.json 不存在，请先运行一次不带 --resume 的命令')
            sys.exit(1)
        report = json.loads(report_path.read_text())
        # Reconstruct (h5_idx, pc_idx, score) from stored indices.
        # Older reports without base_idx/supplement_idx fall back to node_id lookup.
        def _idx_from_entry(entry, sections, key):
            if key in entry:
                return entry[key]
            node_id = (entry.get('base') or entry.get('supplement') or {}).get('node_id', '')
            for i, s in enumerate(sections):
                if s.get('figmaId') == node_id:
                    return i
            return 0

        def _rebuild_matches(entries, base_secs, supp_secs):
            pairs = []
            for e in entries:
                b_idx = e.get('base_idx', _idx_from_entry(e, base_secs, 'base_idx'))
                s_idx = e.get('supplement_idx', _idx_from_entry(e, supp_secs, 'supplement_idx'))
                score = e.get('score', 1.0)
                # Convert to (h5_idx, pc_idx) based on base_platform
                if args.base == 'pc':
                    pairs.append((s_idx, b_idx, score))
                else:
                    pairs.append((b_idx, s_idx, score))
            return pairs

        base_secs_for_resume = base_sections
        supp_secs_for_resume = supp_sections
        auto_matches = _rebuild_matches(report.get('auto', []), base_secs_for_resume, supp_secs_for_resume)
        pending_matches = _rebuild_matches(report.get('pending_confirm', []), base_secs_for_resume, supp_secs_for_resume)

        # Rebuild only-indices by matching stored node_ids to actual section arrays
        matched_base = {b_i for _, b_i, _ in auto_matches} | {b_i for _, b_i, _ in pending_matches} if args.base == 'pc' else \
                       {h_i for h_i, _, _ in auto_matches} | {h_i for h_i, _, _ in pending_matches}
        matched_supp = {h_i for h_i, _, _ in auto_matches} | {h_i for h_i, _, _ in pending_matches} if args.base == 'pc' else \
                       {s_i for _, s_i, _ in auto_matches} | {s_i for _, s_i, _ in pending_matches}
        pc_only_idxs = [i for i in range(len(base_sections)) if i not in matched_base]
        h5_only_idxs = [i for i in range(len(supp_sections)) if i not in matched_supp]
        pc_only = pc_only_idxs
        final_auto, _, h5_only = _apply_confirmed(
            auto_matches, pending_matches, confirmed_path, h5_only_idxs, pc_only_idxs, args.base
        )
    else:
        # Phase 1+2: classify matches
        try:
            if args.base == 'pc':
                result = classify_matches(supp_sections, base_sections, supp_h, base_h)
            else:
                result = classify_matches(base_sections, supp_sections, base_h, supp_h)
        except LowMatchRateError as e:
            print(f'\n⚠️  {e}')
            print('  [A] 仍然继续合并  [B] 放弃，保留两份独立产物')
            choice = input('请选择 (A/B): ').strip().upper()
            if choice != 'A':
                print('已放弃合并。')
                sys.exit(0)
            result = classify_matches(
                supp_sections if args.base == 'pc' else base_sections,
                base_sections if args.base == 'pc' else supp_sections,
                supp_h if args.base == 'pc' else base_h,
                base_h if args.base == 'pc' else supp_h,
                auto_threshold=0.0,          # force all to auto after user confirmation
                semi_threshold=0.0,
                low_match_rate_threshold=0.0,  # skip rate check — user already confirmed
            )

        auto = result['auto']
        pending = result['pending']
        h5_only_idxs = result['h5_only']
        pc_only_idxs = result['pc_only']

        _write_report(base_node_id, auto, pending, base_sections, supp_sections,
                      h5_only_idxs, pc_only_idxs, args.base)

        if pending:
            print(f'\n⚠️  {len(pending)} 对匹配需要确认，请查看 match-report.json')
            print(f'   确认后将结果写入 {confirmed_path.name}，然后运行 --resume 继续')
            if not args.dry_run:
                sys.exit(0)   # pause for human confirmation

        final_auto = auto
        h5_only = h5_only_idxs
        pc_only = pc_only_idxs

    # Phase 4: build merged IR
    merged_ir = _build_merged_ir(
        base_ir, supp_ir, final_auto, h5_only, pc_only, args.base, args.breakpoint
    )

    # Phase 5: output
    out_path = merged_ir_file(base_node_id)
    if args.dry_run:
        auto_count = len(final_auto)
        media_count = sum(1 for s in get_top_sections(merged_ir) if s.get('responsive'))
        print(f'\n[dry-run] 将写入: {out_path}')
        print(f'  自动匹配: {auto_count} 对')
        print(f'  @media 节点: {media_count} 个')
        print(f'  h5Only 节点: {sum(1 for s in get_top_sections(merged_ir) if s.get("h5Only"))} 个')
        print(f'  pcOnly 节点: {sum(1 for s in get_top_sections(merged_ir) if s.get("pcOnly"))} 个')
    else:
        # Apply theme-override tagging before saving: nodes with dark H5 responsive
        # backgrounds (e.g. Hero section in EU LP) get themeOverride='dark' so that
        # split_codegen generates theme-override-dark on those section roots.
        try:
            from lib.token_resolver import tag_mixed_theme_nodes as _tmt
            from lib.tsx_generator import detect_page_theme as _dpt
            _page_theme_merged = _dpt(merged_ir)
            _tmt(merged_ir, page_theme=_page_theme_merged)
        except Exception:
            pass
        out_path.write_text(json.dumps(merged_ir, indent=2, ensure_ascii=False))
        print(f'✓  合并 IR 已写入: {out_path}')
        _merge_staging_assets(base_ir, supp_ir, base_node_id, supp_node_id)
        print(f'   下一步: python3 split_components.py --node-id=merged-{base_node_id} --auto --css-ext={args.css_ext}')


if __name__ == '__main__':
    main()
