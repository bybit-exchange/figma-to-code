import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.struct_scorer import structural_similarity, STRUCTURAL_SPLIT_THRESHOLD, _tree_depth


def _text_node(text: str) -> dict:
    return {'figmaType': 'TEXT', 'isTextNode': True, 'textContent': text,
            'visible': True, 'children': [], 'css': {}}


def _frame(children: list, name: str = 'Frame') -> dict:
    return {'figmaType': 'FRAME', 'figmaName': name, 'visible': True,
            'children': children, 'css': {}}


# ── _tree_depth ──────────────────────────────────────────────────────────────

def test_tree_depth_leaf_returns_0():
    assert _tree_depth(_text_node('hello')) == 0


def test_tree_depth_one_level():
    assert _tree_depth(_frame([_text_node('a'), _text_node('b')])) == 1


def test_tree_depth_nested():
    inner = _frame([_text_node('x')])
    outer = _frame([inner])
    assert _tree_depth(outer) == 2


# ── structural_similarity ────────────────────────────────────────────────────

def test_identical_node_returns_1():
    node = _frame([_text_node('A'), _text_node('B')])
    assert structural_similarity(node, node) == 1.0


def test_different_children_count_triggers_split():
    # PC: 4 children, H5: 1 child → should be below threshold
    base = _frame([_text_node(f't{i}') for i in range(4)])
    supp = _frame([_text_node('t0')])
    score = structural_similarity(base, supp)
    assert score < STRUCTURAL_SPLIT_THRESHOLD, f"Expected < {STRUCTURAL_SPLIT_THRESHOLD}, got {score}"


def test_same_texts_depth_diff_stays_above_threshold():
    # H5 wraps same texts in extra div — depth differs but content same
    texts = [_text_node(t) for t in ['Sign up', 'Earn rewards', 'Get paid', 'Top up', 'Cashback']]
    base = _frame(texts)
    supp = _frame([_frame(texts)])   # one extra wrapper level
    score = structural_similarity(base, supp)
    assert score >= STRUCTURAL_SPLIT_THRESHOLD, f"Expected >= {STRUCTURAL_SPLIT_THRESHOLD}, got {score}"


def test_no_text_both_sides_returns_1():
    # Image-only sections: text_coverage = 1.0 (empty set)
    base = _frame([_frame([])])
    supp = _frame([_frame([])])
    assert structural_similarity(base, supp) == 1.0


def test_same_content_completely_different_structure_no_split():
    """最极端情况：内容完全相同但结构完全不同，也不应该触发 split"""
    texts = [_text_node(t) for t in ['Sign up', 'Earn rewards', 'Get paid', 'Top up', 'Cashback']]
    # PC: 5个平级子节点
    base = _frame(texts)
    # H5: 1个子节点（完全不同结构）
    supp = _frame([_frame(texts)])
    score = structural_similarity(base, supp)
    assert score >= STRUCTURAL_SPLIT_THRESHOLD, \
        f"Same content should NOT split even with structural difference, got {score}"


def test_hidden_children_excluded():
    visible = _text_node('hello')
    hidden = {**_text_node('hidden'), 'visible': False}
    base = _frame([visible, hidden])  # 2 children but 1 visible
    supp = _frame([visible])          # 1 child
    # Should treat both as having 1 visible child → count_sim == 1.0
    score = structural_similarity(base, supp)
    assert score >= 0.9
