import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.node_matcher import (
    extract_leaf_texts, is_pure_wrapper, get_top_sections,
    match_score, classify_matches, LowMatchRateError,
)


def _text_node(text: str, fid: str = '1:1') -> dict:
    return {'figmaId': fid, 'figmaName': text, 'figmaType': 'TEXT',
            'isTextNode': True, 'textContent': text,
            'css': {}, 'children': [], 'visible': True}


def _section(name: str, texts: list[str], fid: str = '1:1',
             y: float = 0.0, height: float = 100.0) -> dict:
    return {
        'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
        'css': {}, 'visible': True,
        'absoluteBoundingBox': {'x': 0, 'y': y, 'width': 393, 'height': height},
        'children': [_text_node(t, f'{fid}:{i}') for i, t in enumerate(texts)],
    }


def _wrapper(child: dict, name: str = 'Frame 2147') -> dict:
    return {'figmaId': '0:1', 'figmaName': name, 'figmaType': 'FRAME',
            'css': {'width': '1440px'}, 'visible': True,
            'children': [child]}


# ── extract_leaf_texts ──────────────────────────────────────────────────────

def test_extract_leaf_texts_collects_nested():
    node = _section('Hero', ['Title', 'Subtitle'])
    assert extract_leaf_texts(node) == {'Title', 'Subtitle'}


def test_extract_leaf_texts_empty_for_no_text():
    node = {'figmaId': '1:1', 'figmaName': 'Frame', 'figmaType': 'FRAME',
            'isTextNode': False, 'textContent': None, 'css': {}, 'children': []}
    assert extract_leaf_texts(node) == set()


# ── is_pure_wrapper ─────────────────────────────────────────────────────────

def test_is_pure_wrapper_single_child():
    child = _section('Hero', ['Title'])
    node = {'figmaId': '0:1', 'figmaName': 'Page', 'figmaType': 'FRAME',
            'css': {}, 'children': [child], 'visible': True}
    assert is_pure_wrapper(node) is True


def test_is_pure_wrapper_multiple_children_not_wrapper():
    node = {'figmaId': '0:1', 'figmaName': 'Page', 'figmaType': 'FRAME',
            'css': {}, 'children': [_section('A', []), _section('B', [])], 'visible': True}
    assert is_pure_wrapper(node) is False


def test_is_pure_wrapper_auto_named_layout_only():
    node = {'figmaId': '0:1', 'figmaName': 'Frame 2147224550', 'figmaType': 'FRAME',
            'css': {'width': '1440px', 'maxWidth': '1200px'},
            'children': [_section('A', []), _section('B', [])], 'visible': True}
    assert is_pure_wrapper(node) is True


def test_is_pure_wrapper_semantic_name_not_wrapper():
    node = {'figmaId': '0:1', 'figmaName': 'HeroSection', 'figmaType': 'FRAME',
            'css': {'width': '1440px'},
            'children': [_section('A', []), _section('B', [])], 'visible': True}
    assert is_pure_wrapper(node) is False


# ── get_top_sections ────────────────────────────────────────────────────────

def test_get_top_sections_penetrates_single_wrapper():
    hero = _section('Hero', ['Title'])
    inner = {'figmaId': '0:2', 'figmaName': 'Frame 2147', 'figmaType': 'FRAME',
             'css': {'width': '1440px'}, 'children': [hero], 'visible': True}
    root = {'figmaId': '0:1', 'figmaName': 'Web', 'figmaType': 'FRAME',
            'css': {}, 'children': [inner], 'visible': True}
    sections = get_top_sections(root)
    assert len(sections) == 1
    assert sections[0]['figmaName'] == 'Hero'


def test_get_top_sections_filters_hidden():
    hero = _section('Hero', ['Title'])
    nav = {**_section('Nav', []), 'visible': False}
    root = {'figmaId': '0:1', 'figmaName': 'Page', 'figmaType': 'FRAME',
            'css': {}, 'children': [nav, hero], 'visible': True}
    sections = get_top_sections(root)
    assert len(sections) == 1
    assert sections[0]['figmaName'] == 'Hero'


# ── match_score ─────────────────────────────────────────────────────────────

def test_match_score_identical_text_gives_high_score():
    h5 = _section('Hero', ['Move Your Funds', 'Get Rewarded'], y=0, height=750)
    pc = _section('2606-T90590', ['Move Your Funds', 'Get Rewarded'], y=0, height=750)
    score = match_score(h5, pc, 8484, 7718)
    assert score >= 0.70


def test_match_score_no_text_overlap_gives_low_score():
    h5 = _section('Hero', ['Alpha', 'Beta'], y=0, height=100)
    pc = _section('Footer', ['Gamma', 'Delta'], y=5000, height=100)
    score = match_score(h5, pc, 8484, 7718)
    assert score < 0.40


def test_match_score_same_name_boosts_score():
    h5 = _section('Join', [], y=3000, height=200)
    pc = _section('Join', [], y=4000, height=200)
    score_same = match_score(h5, pc, 8484, 7718)
    pc_diff = _section('Something Else', [], y=4000, height=200)
    score_diff = match_score(h5, pc_diff, 8484, 7718)
    assert score_same > score_diff


# ── classify_matches ────────────────────────────────────────────────────────

def test_classify_matches_auto_high_confidence():
    h5_secs = [_section('Hero', ['Title', 'Sub'], y=0, height=750)]
    pc_secs = [_section('Hero', ['Title', 'Sub'], y=0, height=750)]
    result = classify_matches(h5_secs, pc_secs, 8484, 7718)
    assert len(result['auto']) == 1
    assert result['pending'] == []


def test_classify_matches_low_rate_raises():
    h5_secs = [_section('A', ['x'], y=0, height=100)]
    pc_secs = [_section('B', ['y'], y=0, height=100),
               _section('C', ['z'], y=100, height=100),
               _section('D', ['w'], y=200, height=100),
               _section('E', ['v'], y=300, height=100)]
    try:
        classify_matches(h5_secs, pc_secs, 400, 400)
        assert False, "should raise LowMatchRateError"
    except LowMatchRateError:
        pass


def test_classify_matches_unmatched_marked_as_only():
    shared_text = ['Prize Pool', '50,000 USDT']
    h5_only_text = ['Mobile CTA']
    h5_secs = [
        _section('PrizePool', shared_text, y=0, height=500),
        _section('MobileCTA', h5_only_text, y=500, height=100),
    ]
    pc_secs = [_section('PrizePool', shared_text, y=0, height=500)]
    result = classify_matches(h5_secs, pc_secs, 600, 500)
    assert len(result['h5_only']) == 1


# ── extract_asset_ids ────────────────────────────────────────────────────────

def test_extract_asset_ids_finds_image_fills():
    from lib.node_matcher import extract_asset_ids
    node = {
        'figmaType': 'FRAME', 'visible': True,
        'fills': [{'type': 'IMAGE', 'imageRef': 'abc123'}],
        'children': [{
            'figmaType': 'RECTANGLE', 'visible': True,
            'fills': [{'type': 'IMAGE', 'imageRef': 'def456'}],
            'children': [],
        }],
    }
    assert extract_asset_ids(node) == {'abc123', 'def456'}


def test_extract_asset_ids_ignores_non_image():
    from lib.node_matcher import extract_asset_ids
    node = {
        'fills': [{'type': 'SOLID', 'color': '#fff'}],
        'children': [],
    }
    assert extract_asset_ids(node) == set()


# ── asset_sim ────────────────────────────────────────────────────────────────

def test_asset_sim_same_images_returns_1():
    from lib.node_matcher import asset_sim
    node = {'fills': [{'type': 'IMAGE', 'imageRef': 'abc'}], 'children': []}
    assert asset_sim(node, node) == 1.0


def test_asset_sim_no_images_returns_1():
    from lib.node_matcher import asset_sim
    node = {'fills': [], 'children': []}
    assert asset_sim(node, node) == 1.0


def test_asset_sim_disjoint_images_returns_0():
    from lib.node_matcher import asset_sim
    h5 = {'fills': [{'type': 'IMAGE', 'imageRef': 'aaa'}], 'children': []}
    pc = {'fills': [{'type': 'IMAGE', 'imageRef': 'bbb'}], 'children': []}
    assert asset_sim(h5, pc) == 0.0


# ── match_score 权重验证 ──────────────────────────────────────────────────────

def test_match_score_naming_difference_does_not_block_high_text_sim():
    """命名完全不同但文本内容相同，分数应 >= 0.65（auto_threshold）"""
    texts = ['Earn cashback', 'Register now', 'Top up funds', 'Get rewards', 'Verify identity']
    h5 = _section('Section 3', texts, fid='2:1', y=300)
    pc = _section('EarnFromSection', texts, fid='1:1', y=400)
    score = match_score(h5, pc, 3000, 4000)
    assert score >= 0.65, f"Expected >= 0.65, got {score}"


def test_match_score_shared_image_ref_boosts_score():
    """共享图片资源应提升分数"""
    from lib.node_matcher import match_score as _ms
    def _img_section(name, image_ref, y):
        return {
            'figmaId': '1:1', 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'css': {},
            'absoluteBoundingBox': {'x': 0, 'y': y, 'width': 375, 'height': 200},
            'fills': [{'type': 'IMAGE', 'imageRef': image_ref}],
            'children': [],
        }
    h5 = _img_section('banner', 'img-abc', 100)
    pc_same = _img_section('HeroSection', 'img-abc', 200)
    pc_diff = _img_section('HeroSection', 'img-xyz', 200)
    score_same = _ms(h5, pc_same, 1000, 2000)
    score_diff = _ms(h5, pc_diff, 1000, 2000)
    assert score_same > score_diff


# ── classify_matches 阈值验证 ─────────────────────────────────────────────────

def test_classify_matches_uses_new_thresholds():
    """auto_threshold=0.65, semi_threshold=0.45：中等相似度进 pending 而非 auto"""
    texts_a = ['Hello world']
    texts_b = ['Different text entirely']
    h5_secs = [_section('S1', texts_a, fid='2:1', y=0)]
    pc_secs = [_section('SectionOne', texts_b, fid='1:1', y=0)]
    # 文本完全不同 → 应进 h5_only 或 pending，不进 auto
    # LowMatchRateError is also acceptable: it implies zero auto matches
    try:
        result = classify_matches(h5_secs, pc_secs, 1000, 1000)
        assert len(result['auto']) == 0
    except LowMatchRateError:
        pass  # zero auto matches confirmed via exception


# ── Bug fix: pending 应计入匹配率检查 ──────────────────────────────────────────

def test_classify_matches_pending_counts_toward_match_rate():
    """Fix: pending 匹配应计入 LowMatchRateError 阈值检查，避免误报。
    Old: only len(auto)/total checked → false error when pending sufficient.
    New: (len(auto)+len(pending))/total checked.

    Real data: Trump活动 node 181:2980 (PC) + 6:1998 (H5)，两端主内容区
    text_sim≈0.33 (10 shared / 30 union) → score≈0.48，进 pending 不进 auto。
    """
    # Real data: node 181:2980 PC + 6:1998 H5, Trump活动页面
    shared = [
        '+ 500 USDT', '+ 1,200 USDT', '+ 2,500 USDT', '4.5K', '1.2B',
        '10K', '1,200 USDT + Trump 纪念品', '123,23200 Trump', '3 晚双人房含早', '奖励介绍',
    ]
    h5_main = _section('1', shared + [
        'Terms and Condition', 'Can i follow more than one Master Trader?',
        '12,323,200 USD', '215,000', 'Earn 567% APR on 50 USDT',
        '专属出行服务', 'Trump 最高持仓', '56,823,320 USD',
    ], fid='6:3', y=690, height=2484)
    pc_main = _section('Frame 2147229891', shared + [
        'Cras vel orci', '-', '--', '1,000', '1,100', '1,200',
        '1-10 of 100 items', '100', '100K', '12,500 USDT + Trump 纪念品',
        '17-50', 'Username',
    ], fid='181:3', y=800, height=4149)

    # 1 H5 section vs 1 PC section → (0 auto + 1 pending) / max(1,1) = 100% ≥ 30% → no raise
    result = classify_matches([h5_main], [pc_main], 2815, 5079)
    assert len(result['auto']) == 0
    assert len(result['pending']) == 1


def test_classify_matches_trump_page_five_h5_two_pc_no_raise():
    """Trump活动页真实场景：H5 5个section，PC 2个section，0 auto + 2 pending → 不报 LowMatchRateError。
    Old: 0/5=0%<30% → raises.  New: 2/5=40%≥30% → no raise.

    Real data: node 6:1998 (H5, 393px) + 181:2980 (PC, 1440px), Trump活动页面。
    H5 section names均为自动 ID（"2"/"1"/"Navigation Bar"），PC sections也是自动 ID，
    name_sim几乎为 0，但 text_sim 足以产生 pending 配对。
    """
    shared_main = [
        '+ 500 USDT', '+ 1,200 USDT', '+ 2,500 USDT', '4.5K', '1.2B',
        '10K', '1,200 USDT + Trump 纪念品', '123,23200 Trump', '3 晚双人房含早', '奖励介绍',
    ]
    shared_hero = ['23', '交易现货 & 合约', '59', 'D', 'H', 'M', 'S']

    h5_secs = [
        _section('2', ['KV 图片'], fid='6:2', y=0, height=359),
        _section('Navigation Bar', ['9:41'], fid='6:11', y=359, height=103),
        _section('Frame 2147224436', shared_hero + ['60'], fid='6:12', y=462, height=228),
        _section('1', shared_main + [
            'Terms and Condition', 'Can i follow more than one Master Trader?',
            '12,323,200 USD', '215,000', 'Earn 567% APR on 50 USDT',
            '专属出行服务', 'Trump 最高持仓', '56,823,320 USD',
        ], fid='6:3', y=690, height=2484),
        _section('Sheet', ['Trade Trump'], fid='6:99', y=3174, height=106),
    ]
    pc_secs = [
        _section('Group 2007673545', shared_hero + [
            'Login', 'Buy Crypto', 'Trade Trump', 'Register',
            'Finance', 'Markets', 'Derivatives', 'Tools',
        ], fid='181:2', y=0, height=800),
        _section('Frame 2147229891', shared_main + [
            'Cras vel orci', '-', '--', '1,000', '1,100', '1,200',
            '1-10 of 100 items', '100', '100K', '12,500 USDT + Trump 纪念品',
            '17-50', 'Username',
        ], fid='181:3', y=800, height=4149),
    ]

    # After fix: (0+2)/max(5,2) = 2/5 = 40% ≥ 30% → no raise
    result = classify_matches(h5_secs, pc_secs, 3280, 4949)
    assert len(result['auto']) == 0
    assert len(result['pending']) >= 2  # H5[2]↔PC[0] + H5[3]↔PC[1]


# ── U-430: text-free + asset-free leaf vs container should not force-match ────

def _icon_leaf(name: str, fid: str) -> dict:
    """Leaf image node: 0 children, no text, no image fills (e.g. Amazon_Prime_Logo SVG import)."""
    return {
        'figmaId': fid, 'figmaName': name, 'figmaType': 'VECTOR',
        'isTextNode': False, 'textContent': None,
        'css': {'overflow': 'hidden'}, 'fills': [],
        'children': [], 'visible': True,
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 75, 'height': 47},
    }


def _icon_container(name: str, fid: str, child_name: str) -> dict:
    """Container icon tile: 1 child (vector group), no text, no image fills."""
    child = {
        'figmaId': fid + ':1', 'figmaName': child_name, 'figmaType': 'GROUP',
        'isTextNode': False, 'textContent': None,
        'css': {}, 'fills': [], 'children': [], 'visible': True,
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 32, 'height': 32},
    }
    return {
        'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
        'isTextNode': False, 'textContent': None,
        'css': {'background-color': 'var(--bds-gray-t1-title)'},
        'fills': [], 'children': [child], 'visible': True,
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 48, 'height': 48},
    }


def test_u430_leaf_image_vs_icon_container_scores_below_threshold():
    """U-430: match_score must return < 0.55 when PC is a leaf image node (0 children)
    paired with an H5 container icon tile (1+ children), both text-free and asset-free.

    Real data: EU deposit campaign EarnFromSection2 (250:3308).
    PC node 250:3344 'Amazon_Prime_Logo 1' (SVG import, 0 children, no text, no image fills)
    was matched to H5 node 250:4519 'openai-logomark' container (1 child), because
    both have no text → text_sim = 1.0 and no image fills → asset_sim = 1.0.
    This produced a bogus match score of ~0.95 and assigned Amazon logo to OpenAI's
    dark background tile → Amazon SVG invisible (dark paths on dark background).

    Fix: when both nodes are text-free AND asset-free, replace the neutral text_sim=1.0
    with child-count similarity, so leaf (0 children) vs container (1+ children) scores low.
    """
    pc_leaf = _icon_leaf('Amazon_Prime_Logo 1', '250:3344')
    h5_openai = _icon_container('6', '250:4519', 'openai-logomark 1')

    score = match_score(h5_openai, pc_leaf, 10000.0, 10000.0)

    assert score < 0.55, (
        f'U-430 FAIL: leaf PC node (0 children) vs container H5 node (1 child) should score '
        f'< 0.55 (auto_threshold) so they are NOT force-matched. '
        f'Bug: text_sim=1.0 + asset_sim=1.0 when both nodes are text-free & asset-free → '
        f'fake score 0.95 forces Amazon_Prime_Logo onto OpenAI dark bg → invisible. '
        f'Got score: {score:.3f}'
    )


def test_u430_two_containers_same_child_count_scores_above_threshold():
    """U-430 contrast: two icon container tiles with same child count SHOULD still match.

    PC 250:3328 'Clip path group' (1 child: Group) paired with H5 250:4524 'Icon' (1 child:
    Clip path group) → both non-text, non-image-fill, same child count → should score ≥ 0.55.
    """
    pc_container = _icon_container('Clip path group', '250:3328', 'Group')
    h5_container = _icon_container('Icon', '250:4524', 'Clip path group')

    score = match_score(h5_container, pc_container, 10000.0, 10000.0)

    assert score >= 0.55, (
        f'U-430 contrast FAIL: two icon containers with same child count (both=1) should '
        f'score >= 0.55 so they ARE auto-matched. Got score: {score:.3f}'
    )
