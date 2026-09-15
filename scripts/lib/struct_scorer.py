"""struct_scorer.py — 节点结构相似度评分，用于 merge_responsive.py Phase 3 分支判断。"""
from __future__ import annotations

STRUCTURAL_SPLIT_THRESHOLD = 0.55


def _tree_depth(node: dict, _d: int = 0) -> int:
    """递归计算节点树最大深度（叶节点深度为 0）。"""
    children = [c for c in (node.get('children') or []) if c.get('visible', True)]
    if not children:
        return _d
    return max(_tree_depth(c, _d + 1) for c in children)


def _extract_visible_leaf_texts(node: dict) -> set[str]:
    """提取节点树中所有可见叶节点文本（跳过 visible=False 的节点）。"""
    if not node.get('visible', True):
        return set()
    texts: set[str] = set()
    if node.get('isTextNode') and node.get('textContent'):
        texts.add(node['textContent'].strip())
    for child in node.get('children') or []:
        texts |= _extract_visible_leaf_texts(child)
    return texts


def structural_similarity(base_node: dict, supp_node: dict) -> float:
    """
    计算两个 IR 节点的结构相似度，返回 0.0-1.0。
    低于 STRUCTURAL_SPLIT_THRESHOLD 时，调用方应走 structuralSplit 路径（双组件）。

    核心约束：内容相同的元素必须共享 DOM，structuralSplit 只在"内容 AND 结构都有显著差异"时才触发。

    三个维度：
      count_sim    (0.25) — 可见子节点数量接近程度
      depth_sim    (0.15) — 树深度接近程度
      text_coverage (0.60) — 双向文本覆盖率（Dice 系数），内容相同时权重主导分数
    """
    base_vis = [c for c in (base_node.get('children') or []) if c.get('visible', True)]
    supp_vis = [c for c in (supp_node.get('children') or []) if c.get('visible', True)]

    base_count = len(base_vis)
    supp_count = len(supp_vis)
    count_sim = 1.0 - abs(base_count - supp_count) / max(base_count, supp_count, 1)

    base_depth = _tree_depth(base_node)
    supp_depth = _tree_depth(supp_node)
    depth_sim = 1.0 - abs(base_depth - supp_depth) / max(base_depth, supp_depth, 1)

    base_texts = _extract_visible_leaf_texts(base_node)
    supp_texts = _extract_visible_leaf_texts(supp_node)
    total = len(base_texts) + len(supp_texts)
    if total > 0:
        text_coverage = 2 * len(base_texts & supp_texts) / total  # Dice 系数
    else:
        text_coverage = 1.0  # 两端都没文本，不扣分

    return 0.25 * count_sim + 0.15 * depth_sim + 0.60 * text_coverage
