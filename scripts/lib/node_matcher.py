"""node_matcher.py — 多信号匹配算法，用于 merge_responsive.py 的 Phase 1+2。"""
from __future__ import annotations
import re
from difflib import SequenceMatcher

LOW_MATCH_RATE_THRESHOLD = 0.30


class LowMatchRateError(Exception):
    """整体匹配率低于阈值，两端可能不是对应页面。"""


def extract_leaf_texts(node: dict) -> set[str]:
    texts: set[str] = set()
    if node.get('isTextNode') and node.get('textContent'):
        texts.add(node['textContent'].strip())
    for child in node.get('children') or []:
        texts |= extract_leaf_texts(child)
    return texts


def is_auto_named(name: str) -> bool:
    return bool(re.match(r'^(Frame|Group)\s+\d+', name))


def only_layout_css(css: dict) -> bool:
    return set(css.keys()) <= {'width', 'maxWidth', 'minWidth', 'margin', 'padding'}


def is_pure_wrapper(node: dict) -> bool:
    visible = [c for c in (node.get('children') or []) if c.get('visible', True)]
    if len(visible) == 1:
        return True
    if node.get('figmaType') in ('RECTANGLE', 'ELLIPSE') and not node.get('children'):
        return True
    if is_auto_named(node.get('figmaName', '')) and only_layout_css(node.get('css') or {}):
        return True
    return False


def normalize_tree(node: dict) -> dict:
    while True:
        visible = [c for c in (node.get('children') or []) if c.get('visible', True)]
        if len(visible) != 1:
            break
        child = visible[0]
        # Only penetrate through auto-named layout wrappers to avoid consuming semantic sections
        if is_auto_named(child.get('figmaName', '')) and only_layout_css(child.get('css') or {}):
            node = child
        else:
            break
    return node


def get_top_sections(node: dict) -> list[dict]:
    root = normalize_tree(node)
    return [c for c in (root.get('children') or []) if c.get('visible', True)]


def _y_ratio(node: dict, page_height: float) -> float:
    if page_height <= 0:
        return 0.0
    bb = node.get('absoluteBoundingBox') or {}
    y = bb.get('y', 0)
    h = bb.get('height', 0)
    return (y + h / 2) / page_height


def extract_asset_ids(node: dict) -> set[str]:
    """递归提取节点及其子节点所有 image fill 的 imageRef。"""
    ids: set[str] = set()
    for fill in node.get('fills') or []:
        if fill.get('type') == 'IMAGE' and fill.get('imageRef'):
            ids.add(fill['imageRef'])
    for child in node.get('children') or []:
        ids |= extract_asset_ids(child)
    return ids


def asset_sim(h5_node: dict, pc_node: dict) -> float:
    """图片资源 Jaccard 相似度。两端都无图片时返回 1.0（不扣分）。"""
    h5_ids = extract_asset_ids(h5_node)
    pc_ids = extract_asset_ids(pc_node)
    union = h5_ids | pc_ids
    if not union:
        return 1.0
    return len(h5_ids & pc_ids) / len(union)


def match_score(h5_node: dict, pc_node: dict,
                h5_page_height: float, pc_page_height: float) -> float:
    h5_t = extract_leaf_texts(h5_node)
    pc_t = extract_leaf_texts(pc_node)
    inter = len(h5_t & pc_t)
    union = len(h5_t | pc_t)
    text_sim = inter / union if union else (1.0 if not h5_t and not pc_t else 0.0)

    name_sim_val = SequenceMatcher(
        None,
        h5_node.get('figmaName', '').lower(),
        pc_node.get('figmaName', '').lower(),
    ).ratio()

    h5_y = _y_ratio(h5_node, h5_page_height)
    pc_y = _y_ratio(pc_node, pc_page_height)
    pos_sim_val = max(0.0, 1.0 - abs(h5_y - pc_y) * 2)

    asset_sim_val = asset_sim(h5_node, pc_node)

    # U-430: when both nodes are text-free AND have no image fills (pure vector icons),
    # text_sim=1.0 and asset_sim=1.0 produce an indiscriminate ~0.95 score for ALL pairs,
    # making it impossible to reject wrong matches (e.g. Amazon_Prime_Logo leaf vs OpenAI
    # container). Replace the neutral text_sim=1.0 with child-count similarity so a leaf
    # image node (0 children) won't be force-paired with a container icon tile (1+ children).
    if not h5_t and not pc_t and not extract_asset_ids(h5_node) and not extract_asset_ids(pc_node):
        h5_vis = [c for c in (h5_node.get('children') or []) if c.get('visible', True)]
        pc_vis = [c for c in (pc_node.get('children') or []) if c.get('visible', True)]
        h5_count = len(h5_vis)
        pc_count = len(pc_vis)
        text_sim = 1.0 - abs(h5_count - pc_count) / max(h5_count, pc_count, 1)

    return 0.70 * text_sim + 0.15 * asset_sim_val + 0.10 * pos_sim_val + 0.05 * name_sim_val


def classify_matches(
    h5_sections: list[dict],
    pc_sections: list[dict],
    h5_page_height: float,
    pc_page_height: float,
    auto_threshold: float = 0.65,   # 原 0.70
    semi_threshold: float = 0.45,   # 原 0.40
    low_match_rate_threshold: float = LOW_MATCH_RATE_THRESHOLD,
) -> dict:
    """
    Returns {
        'auto':    [(h5_idx, pc_idx, score), ...],  # score >= auto_threshold
        'pending': [(h5_idx, pc_idx, score), ...],  # semi_threshold <= score < auto_threshold
        'h5_only': [h5_idx, ...],
        'pc_only': [pc_idx, ...],
    }
    Raises LowMatchRateError if auto match rate < LOW_MATCH_RATE_THRESHOLD.
    """
    # Score all pairs
    all_scores = []
    for i, h5 in enumerate(h5_sections):
        for j, pc in enumerate(pc_sections):
            s = match_score(h5, pc, h5_page_height, pc_page_height)
            all_scores.append((i, j, s))
    all_scores.sort(key=lambda x: -x[2])

    # Greedy 1:1 assignment
    used_h5: set[int] = set()
    used_pc: set[int] = set()
    auto, pending = [], []

    for h5_i, pc_j, score in all_scores:
        if h5_i in used_h5 or pc_j in used_pc:
            continue
        if score >= auto_threshold:
            auto.append((h5_i, pc_j, score))
            used_h5.add(h5_i)
            used_pc.add(pc_j)
        elif score >= semi_threshold:
            pending.append((h5_i, pc_j, score))
            used_h5.add(h5_i)
            used_pc.add(pc_j)

    total = max(len(h5_sections), len(pc_sections), 1)
    if (len(auto) + len(pending)) / total < low_match_rate_threshold:
        raise LowMatchRateError(
            f"匹配率仅 {(len(auto)+len(pending))/total:.0%}（{len(auto)+len(pending)}/{total}），"
            f"两端可能不是对应页面，建议检查 URL 是否正确。"
        )

    h5_only = [i for i in range(len(h5_sections)) if i not in used_h5]
    pc_only = [j for j in range(len(pc_sections)) if j not in used_pc]
    return {'auto': auto, 'pending': pending, 'h5_only': h5_only, 'pc_only': pc_only}
