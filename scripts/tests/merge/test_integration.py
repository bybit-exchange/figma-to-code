"""
集成测试：用 fixture IR 数据验证端到端合并结果。
离线运行，不依赖 Figma API。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.node_matcher import get_top_sections, classify_matches
from lib.css_differ import css_diff
from lib.scss_generator import generate_scss

FIXTURES = Path(__file__).parent / 'fixtures'


def _load(case: str, platform: str) -> dict:
    return json.loads((FIXTURES / case / f'{platform}.ir.json').read_text())


# ── EU Deposit: 命名差异场景（H5 语义名 vs PC Jira 票号）──────────────────────

def test_eu_deposit_hero_matched_by_text():
    """Hero section matched despite totally different figmaNames."""
    h5 = _load('eu-deposit', 'h5')
    pc = _load('eu-deposit', 'pc')
    h5_secs = get_top_sections(h5)
    pc_secs = get_top_sections(pc)
    result = classify_matches(h5_secs, pc_secs, 8484, 7718)
    assert len(result['auto']) >= 1, "Hero should be auto-matched via text Jaccard"
    # Verify Hero ↔ 2606-T90590 is somewhere in auto matches
    hero_match = next(
        (m for m in result['auto'] if h5_secs[m[0]]['figmaName'] == 'Hero'), None
    )
    assert hero_match is not None, "Hero should be in auto matches"
    h5_i, pc_i, score = hero_match
    assert '2606-T90590' in pc_secs[pc_i]['figmaName']
    assert score >= 0.70


def test_eu_deposit_join_matched_by_name_and_text():
    h5 = _load('eu-deposit', 'h5')
    pc = _load('eu-deposit', 'pc')
    h5_secs = get_top_sections(h5)
    pc_secs = get_top_sections(pc)
    result = classify_matches(h5_secs, pc_secs, 8484, 7718)
    matched_names = {
        (h5_secs[h5_i]['figmaName'], pc_secs[pc_i]['figmaName'])
        for h5_i, pc_i, _ in result['auto']
    }
    assert ('Join', 'Join') in matched_names


def test_eu_deposit_css_diff_direction():
    """H5 column layout overrides PC row layout in @media."""
    h5 = _load('eu-deposit', 'h5')
    pc = _load('eu-deposit', 'pc')
    hero_h5 = get_top_sections(h5)[0]  # Hero
    hero_pc = get_top_sections(pc)[0]  # 2606-T90590...
    base_styles, responsive = css_diff(hero_pc['css'], hero_h5['css'])
    assert base_styles.get('flexDirection') == 'row'   # PC default
    assert responsive.get('flexDirection') == 'column'  # H5 override


def test_eu_deposit_scss_has_media_block():
    """Merged section emits @media block in CSS output."""
    h5 = _load('eu-deposit', 'h5')
    pc = _load('eu-deposit', 'pc')
    hero_h5 = get_top_sections(h5)[0]
    hero_pc = get_top_sections(pc)[0]
    _, responsive_overrides = css_diff(hero_pc['css'], hero_h5['css'])
    # Inject responsive field into PC hero node (simulating what merge_responsive.py does)
    merged_node = {
        **hero_pc,
        'responsive': [{'breakpoint': 768, 'css': responsive_overrides, 'confidence': 0.92}],
    }
    css_output = generate_scss(merged_node)
    assert '@media (max-width: 768px)' in css_output
    assert 'flex-direction: column' in css_output


# ── Copy Trading: 规范命名场景（Section/Name 一致）────────────────────────────

def test_copy_trading_all_three_sections_auto_matched():
    h5 = _load('copy-trading', 'h5')
    pc = _load('copy-trading', 'pc')
    h5_secs = get_top_sections(h5)
    pc_secs = get_top_sections(pc)
    result = classify_matches(h5_secs, pc_secs, 3000, 3000)
    assert len(result['auto']) == 3, f"Expected 3 auto matches, got {result['auto']}"
    assert result['pending'] == []


def test_copy_trading_hero_height_diff_in_responsive():
    """H5 hero height (400px) overrides PC height (600px) in @media."""
    h5 = _load('copy-trading', 'h5')
    pc = _load('copy-trading', 'pc')
    hero_h5 = get_top_sections(h5)[0]
    hero_pc = get_top_sections(pc)[0]
    base_styles, responsive = css_diff(hero_pc['css'], hero_h5['css'])
    assert base_styles.get('height') == '600px'
    assert responsive.get('height') == '400px'


def test_copy_trading_flex_direction_diff():
    """H5 column layout overrides PC row layout."""
    h5 = _load('copy-trading', 'h5')
    pc = _load('copy-trading', 'pc')
    main_h5 = get_top_sections(h5)[1]   # Section/Main-Content
    main_pc = get_top_sections(pc)[1]
    _, responsive = css_diff(main_pc['css'], main_h5['css'])
    assert responsive.get('flexDirection') == 'column'


# ── 递归子节点合并 ─────────────────────────────────────────────────────────────

def test_merge_node_pair_propagates_child_css():
    """子节点的 H5 CSS 差异应被记录在 responsive[] 中，不再丢失"""
    from merge_responsive import _merge_node_pair
    child_pc = {'figmaId': '1:2', 'figmaName': 'Card', 'figmaType': 'FRAME',
                'visible': True, 'children': [],
                'css': {'width': '80%', 'gap': '32px'},
                'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 300, 'height': 200},
                'fills': []}
    child_h5 = {'figmaId': '2:2', 'figmaName': 'Card', 'figmaType': 'FRAME',
                'visible': True, 'children': [],
                'css': {'width': '100%', 'gap': '16px'},
                'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 300},
                'fills': []}
    base = {'figmaId': '1:1', 'figmaName': 'EarnSection', 'figmaType': 'FRAME',
            'visible': True, 'children': [child_pc],
            'css': {'display': 'flex'}, 'fills': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 400}}
    supp = {'figmaId': '2:1', 'figmaName': 'Section 2', 'figmaType': 'FRAME',
            'visible': True, 'children': [child_h5],
            'css': {'display': 'flex'}, 'fills': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 500}}
    merged = _merge_node_pair(base, supp, 768)
    # 子节点应有 responsive[] 字段，且 width 应被覆盖
    assert not merged.get('structuralSplit'), "Structure is similar, should NOT split"
    merged_children = merged.get('children', [])
    assert len(merged_children) == 1
    child_responsive = merged_children[0].get('responsive', [])
    assert len(child_responsive) > 0, "Child's H5 CSS overrides should be in responsive[]"
    assert merged_children[0]['responsive'][0]['css'].get('width') == '100%'


def test_merge_node_pair_triggers_structural_split():
    """children 数量差异大时应打 structuralSplit 标记"""
    from merge_responsive import _merge_node_pair
    def _card(i):
        return {'figmaId': f'1:{i}', 'figmaName': f'Card{i}', 'figmaType': 'FRAME',
                'visible': True,
                'children': [{'figmaType': 'TEXT', 'isTextNode': True,
                               'textContent': f'Feature {i}', 'visible': True,
                               'children': [], 'css': {}}],
                'css': {}, 'fills': [],
                'absoluteBoundingBox': {'x': i*300, 'y': 0, 'width': 280, 'height': 200}}
    base = {'figmaId': '1:0', 'figmaName': 'GridSection', 'figmaType': 'FRAME',
            'visible': True, 'children': [_card(i) for i in range(4)],
            'css': {'display': 'flex'}, 'fills': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 300}}
    supp = {'figmaId': '2:0', 'figmaName': 'Section', 'figmaType': 'FRAME',
            'visible': True, 'children': [_card(0)],  # only 1 child vs 4
            'css': {'display': 'flex'}, 'fills': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 400}}
    merged = _merge_node_pair(base, supp, 768)
    assert merged.get('structuralSplit') is True
    assert 'supplementNode' in merged


def test_merge_node_pair_records_supplement_text():
    """PC 和 H5 文本不同时应记录 supplementText"""
    from merge_responsive import _merge_node_pair
    base = {'figmaId': '1:1', 'figmaName': 'CTA', 'figmaType': 'TEXT',
            'visible': True, 'isTextNode': True,
            'textContent': 'Sign up for ByEU', 'children': [],
            'css': {'font-size': '16px'}, 'fills': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 200, 'height': 40}}
    supp = {'figmaId': '2:1', 'figmaName': 'CTA', 'figmaType': 'TEXT',
            'visible': True, 'isTextNode': True,
            'textContent': 'Sign up', 'children': [],
            'css': {'font-size': '14px'}, 'fills': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 120, 'height': 32}}
    merged = _merge_node_pair(base, supp, 768)
    assert merged.get('supplementText') == 'Sign up'
    assert merged['textContent'] == 'Sign up for ByEU'  # PC text preserved


def test_build_merged_ir_records_h5_section_order():
    """merged IR 根节点应包含 h5SectionOrder 字段"""
    from merge_responsive import _build_merged_ir
    def _sec(name, y, texts):
        return {'figmaId': f'1:{y}', 'figmaName': name, 'figmaType': 'FRAME',
                'visible': True, 'children': [
                    {'figmaType': 'TEXT', 'isTextNode': True, 'textContent': t,
                     'visible': True, 'children': [], 'css': {}}
                    for t in texts
                ],
                'css': {}, 'fills': [],
                'absoluteBoundingBox': {'x': 0, 'y': y, 'width': 1440, 'height': 300}}
    pc_ir = {'figmaId': 'page:1', 'figmaName': 'Page', 'figmaType': 'FRAME',
             'visible': True, 'css': {},
             'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
             'children': [
                 _sec('Hero', 0, ['Welcome', 'Get started', 'Trade now', 'Earn more', 'Join us']),
                 _sec('Earn', 300, ['Earn cashback', 'Register', 'Top up', 'Verify', 'Start earning']),
             ]}
    h5_ir = {'figmaId': 'page:2', 'figmaName': 'Page', 'figmaType': 'FRAME',
             'visible': True, 'css': {},
             'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 1200},
             'children': [
                 _sec('Section2', 0, ['Earn cashback', 'Register', 'Top up', 'Verify', 'Start earning']),
                 _sec('Section1', 400, ['Welcome', 'Get started', 'Trade now', 'Earn more', 'Join us']),
             ]}
    auto_matches = [(0, 0, 0.9), (1, 1, 0.9)]  # H5[0]↔PC[0], H5[1]↔PC[1]
    merged = _build_merged_ir(pc_ir, h5_ir, auto_matches, [], [], 'pc', 768)
    assert 'h5SectionOrder' in merged
    assert isinstance(merged['h5SectionOrder'], list)


# ── Bug 修复验证：资源暂存区合并 ───────────────────────────────────────────────

def test_merge_staging_assets_creates_merged_dir():
    """_merge_staging_assets 应将 PC+H5 资源合并到 merged-* 暂存目录。
    Real data: nodes 250-2618 (PC/Web) + 250-3798 (H5) from EU deposit campaign LP.
    """
    import tempfile
    from merge_responsive import _merge_staging_assets

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Real data: node 250:2618 (PC) → Web-250-2618, node 250:3798 (H5) → H5-250-3798
        pc_dir = tmp_path / 'Web-250-2618'
        h5_dir = tmp_path / 'H5-250-3798'
        pc_dir.mkdir()
        h5_dir.mkdir()
        (pc_dir / 'logo.svg').write_text('<svg/>')
        (pc_dir / 'hero-bg.png').write_bytes(b'\x89PNG')
        (h5_dir / 'hero-mobile.png').write_bytes(b'\x89PNG')
        (h5_dir / 'logo.svg').write_text('<svg alt/>')  # duplicate, PC takes priority

        base_ir = {'name': 'Web'}
        supp_ir = {'name': 'H5'}
        _merge_staging_assets(base_ir, supp_ir, '250-2618', '250-3798',
                              staging_base=tmp_path)

        merged_dir = tmp_path / 'Web-merged-250-2618'
        assert merged_dir.is_dir(), 'Merged assets dir should be created'
        files = {f.name for f in merged_dir.iterdir()}
        assert 'logo.svg' in files, 'PC logo.svg should be present'
        assert 'hero-bg.png' in files, 'PC hero-bg.png should be present'
        assert 'hero-mobile.png' in files, 'H5 hero-mobile.png should be present'
        assert len(files) == 3, 'Duplicate logo.svg must not be copied twice (PC wins)'
        # PC logo.svg wins (written first, H5 skipped via exist check)
        assert (merged_dir / 'logo.svg').read_text() == '<svg/>'


def test_merge_children_marks_pc_node_pconly_when_h5only_outnumber_pairs():
    """_merge_children: PC child paired with H5 content should get pcOnly=True
    when the number of h5Only siblings >= number of pairs.

    Real data: node 250:3405 (Tiered Table / 1, 1200x686) from TieredRewardsSection
    in EU deposit campaign (VxzOGHRHeBHKhJtDLz1PP5, nodeId=250-2618).
    Problem: 250:3405 pairs with 250:4578 (tiers, score=0.948 due to shared text),
    but 250:4576 (Rewards tiers accordion header, score=0.264) is unmatched → h5Only.
    Since h5Only_count(1) >= paired_count(1), 250:3405 should be marked pcOnly=True
    so _render_node wraps it in a pc-tiered-table1-section div hidden on mobile.
    Without this fix: PC table shows on mobile alongside the h5Only accordion.
    """
    from merge_responsive import _merge_children

    def _node(fid, name, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 300},
            'fills': [], 'semantic': {'className': name.lower().replace(' ', '-')},
            'textContent': texts,
        }

    # Real text data from EU campaign tier table/accordion content (shared text causes high score)
    tier_texts = '50,000\n125,000\n250,000\n500,000\n1,000,000'
    pc_table = _node('250:3405', 'Tiered Table / 1', tier_texts)
    h5_accordion_header = _node('250:4576', 'Rewards tiers', 'Vip快速通道')  # accordion header with text
    h5_accordion_content = _node('250:4578', 'tiers', tier_texts)  # same text → high match score

    base_children = [pc_table]
    supp_children = [h5_accordion_header, h5_accordion_content]

    result = _merge_children(base_children, supp_children, breakpoint=768)

    # 250:4576 (accordion header) should be h5Only — it can't match PC table
    h5_only_nodes = [r for r in result if r.get('h5Only')]
    assert len(h5_only_nodes) >= 1, (
        'Expected 250:4576 to be h5Only since score(250:4576 vs 250:3405)=0.264 < threshold'
    )

    # 250:3405 equivalent should be pcOnly — h5Only siblings >= paired nodes
    pc_nodes = [r for r in result if not r.get('h5Only')]
    assert all(r.get('pcOnly') for r in pc_nodes), (
        f'PC node should be pcOnly=True when h5Only_count >= paired_count. '
        f'Got: {[{"pcOnly": r.get("pcOnly"), "h5Only": r.get("h5Only"), "fid": r.get("figmaId")} for r in result]}'
    )


def test_merge_children_excludes_absolute_h5only_from_pconly_trigger():
    """_merge_children: h5Only nodes with position:absolute are decorative overlays,
    NOT structural mobile replacements. They should NOT count toward the h5Only >= pairs
    threshold that triggers pcOnly marking on PC nodes.

    Real data: EU deposit campaign Hero section (250:2620 PC ↔ 250:3833 H5).
    250:3836 (Rectangle gradient overlay, h5Only=True, position:absolute) triggered the
    pcOnly rule and marked 250:2624 (hero-text-col) as pcOnly=True.
    This hid ALL hero text on mobile (title, subtitle, countdown, CTA button).
    Fix: only count h5Only siblings WITHOUT position:absolute as structural replacements.

    Test scenario: PC has one content node that PAIRS with H5 content node (score >= 0.55).
    H5 also has an absolute-positioned gradient decoration that stays h5Only.
    h5Only_count(1) == paired_count(1), but since the h5Only node is position:absolute,
    it should NOT trigger pcOnly on the paired PC node.
    """
    from merge_responsive import _merge_children

    shared_text = 'Move Your Funds Get Rewarded'

    def _node(fid, name, css=None, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': css or {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 300},
            'fills': [], 'semantic': {'className': name.lower().replace(' ', '-')},
            'textContent': texts,
        }

    # PC hero content column — will PAIR with H5 hero content column (same text → high score)
    pc_content = _node('250:2624', 'hero-text-col', texts=shared_text)
    # H5 equivalent content that pairs with PC content
    h5_content = _node('h5:content', 'hero-text-col-h5', texts=shared_text)
    # H5 gradient overlay — absolute-positioned decoration, stays h5Only
    h5_gradient = _node('250:3836', 'gradient-overlay',
                        css={'position': 'absolute', 'width': '393px', 'height': '254px'})

    result = _merge_children([pc_content], [h5_content, h5_gradient], breakpoint=768)

    # h5_gradient should be h5Only (unmatched since h5_content paired with pc_content)
    h5_only_nodes = [r for r in result if r.get('h5Only')]
    assert len(h5_only_nodes) >= 1, (
        'Gradient overlay should be h5Only after content nodes are paired'
    )

    # The absolute-positioned h5Only gradient should NOT trigger pcOnly on pc_content
    # h5Only_count(1) == paired_count(1) but all h5Only nodes are position:absolute → no pcOnly
    pc_nodes = [r for r in result if not r.get('h5Only')]
    assert not any(r.get('pcOnly') for r in pc_nodes), (
        f'Hero text column should NOT be pcOnly when the only h5Only sibling is '
        f'position:absolute (decoration). Got: {[{"fid": r.get("figmaId"), "pcOnly": r.get("pcOnly")} for r in result]}'
    )


def test_merge_children_keeps_shared_nodes_when_h5only_is_minority():
    """_merge_children: paired PC nodes should NOT get pcOnly when h5Only siblings
    are outnumbered by pairs (extra H5 decoration, not a full replacement).

    Scenario: PC [A, B], H5 [A', B', extra_decoration(h5Only)]
    h5Only_count(1) < paired_count(2) → A and B stay non-pcOnly (show on both).
    """
    from merge_responsive import _merge_children

    def _node(fid, name, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 100},
            'fills': [], 'semantic': {'className': name.lower().replace(' ', '-')},
            'textContent': texts,
        }

    shared_text = 'Title'
    pc_a = _node('1:1', 'Card A', shared_text)
    pc_b = _node('1:2', 'Card B', shared_text)
    h5_a = _node('2:1', 'Card A', shared_text)
    h5_b = _node('2:2', 'Card B', shared_text)
    h5_extra = _node('2:3', 'Extra Decoration')  # will be h5Only

    result = _merge_children([pc_a, pc_b], [h5_a, h5_b, h5_extra], breakpoint=768)

    non_h5 = [r for r in result if not r.get('h5Only')]
    # When h5Only_count(1) < paired_count(2), PC nodes should NOT be pcOnly
    assert not any(r.get('pcOnly') for r in non_h5), (
        f'Shared PC nodes should stay non-pcOnly when h5Only is a minority decoration. '
        f'Got: {[{"pcOnly": r.get("pcOnly"), "fid": r.get("figmaId")} for r in non_h5]}'
    )


def test_merge_children_abs_leaf_h5only_gets_positional_reset():
    """_merge_children: h5Only abs-leaf node from a split pair should carry responsive
    CSS that resets right/bottom positioning to prevent viewport overflow on mobile.

    Real case: EU deposit hero section. 250:3834 (H5 orange bg, position:absolute,
    right: -345.57px, bottom: 0.46px) is split as h5Only. The negative right value
    extends the element beyond the container's right edge, causing body scrollWidth
    to exceed the viewport even when the container has overflow:hidden.
    Fix: add responsive[] override with right:0px, bottom:0px for mobile.
    """
    from merge_responsive import _merge_children, _abs_leaf

    pc_node = {
        'figmaId': '250:2621', 'figmaName': 'BG20260611-172441 1',
        'figmaType': 'RECTANGLE', 'visible': True,
        'css': {'position': 'absolute', 'right': '-176px', 'top': '-170px',
                'width': '1712px', 'height': '1090px'},
        'children': [], 'fills': [],
    }
    h5_node = {
        'figmaId': '250:3834', 'figmaName': 'BG20260611-172441 1',
        'figmaType': 'RECTANGLE', 'visible': True,
        'css': {'position': 'absolute', 'right': '-345.57px', 'bottom': '0.46px',
                'width': '1931.57px', 'height': '1229.28px'},
        'children': [], 'fills': [],
    }

    result = _merge_children([pc_node], [h5_node], breakpoint=768)
    h5_result = next((r for r in result if r.get('figmaId') == '250:3834'), None)
    assert h5_result is not None, '250:3834 H5 node should be in result'
    assert h5_result.get('h5Only'), '250:3834 should be h5Only'

    resp = h5_result.get('responsive') or []
    assert len(resp) >= 1, (
        'h5Only abs-leaf should have responsive[] override to reset positioning. '
        f'Got responsive: {resp}'
    )
    resp_css = resp[0].get('css', {})
    assert resp_css.get('right') == '0px', (
        f'H5 abs-leaf should reset right to 0px on mobile (right:-345px >> -50px). Got: {resp_css}'
    )
    # bottom: 0.46px is NOT < -50px → should NOT be reset (already near bottom edge)
    assert resp_css.get('bottom') is None, (
        f'bottom: 0.46px is not a large negative overflow, should not be reset. Got: {resp_css}'
    )


def test_merge_children_abs_leaf_small_negative_right_no_reset():
    """_merge_children: h5Only abs-leaf with small right overflow (< 50px) should NOT
    get a positional reset — the container's overflow:hidden contains it without issue.

    Real case: EU deposit hero product image 250:3835 (H5 product render, abs-leaf,
    right: -11px). The element is 837px wide but inside the hero which has
    overflow:hidden. The -11px right value is minor — the hero clips it correctly.
    Resetting right to 0px AND top to 0px was incorrectly repositioning the image to
    the top-right corner, cutting off the phone+EU stars portion of the product image.

    Also: top: calc(50% + -145.5px) is a non-px value and should never be reset.
    """
    from merge_responsive import _merge_children

    pc_node = {
        'figmaId': '250:2622', 'figmaName': '20260611-172d441dd 1',
        'figmaType': 'RECTANGLE', 'visible': True,
        'css': {'position': 'absolute', 'left': '6px', 'top': '-104px',
                'width': '1434px', 'height': '912px'},
        'children': [], 'fills': [],
    }
    h5_node = {
        'figmaId': '250:3835', 'figmaName': '20260611-172d441dd 1',
        'figmaType': 'RECTANGLE', 'visible': True,
        'css': {'position': 'absolute', 'right': '-11px',
                'top': 'calc(50% + -145.5px)', 'transform': 'translateY(-50%)',
                'width': '837px', 'height': '532px'},
        'children': [], 'fills': [],
    }

    result = _merge_children([pc_node], [h5_node], breakpoint=768)
    h5_result = next((r for r in result if r.get('figmaId') == '250:3835'), None)
    assert h5_result is not None, '250:3835 should be in result'
    assert h5_result.get('h5Only'), '250:3835 should be h5Only'

    resp = h5_result.get('responsive') or []
    # Small right (-11px) and calc() top should NOT be reset
    resp_css = resp[0].get('css', {}) if resp else {}
    assert resp_css.get('right') is None, (
        f'Small right (-11px) should NOT be reset (contained by overflow:hidden). '
        f'Got responsive: {resp_css}'
    )
    assert resp_css.get('top') is None, (
        f'top: calc() should NOT be reset (non-px, internal positioning). '
        f'Got responsive: {resp_css}'
    )


def test_merge_staging_assets_skips_when_no_sources():
    """_merge_staging_assets 在 PC/H5 资源目录均不存在时不应创建合并目录。"""
    import tempfile
    from merge_responsive import _merge_staging_assets

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        base_ir = {'name': 'Web'}
        supp_ir = {'name': 'H5'}
        _merge_staging_assets(base_ir, supp_ir, '999-999', '888-888',
                              staging_base=tmp_path)

        merged_dir = tmp_path / 'Web-merged-999-999'
        assert not merged_dir.exists(), 'No dir should be created when both sources missing'


def test_merge_children_abs_leaf_pair_splits_pconly_h5only():
    """_merge_children: paired absolute-positioned leaf nodes (no children) must be
    split as pcOnly (PC) and h5Only (H5) instead of merged.

    Real data: EU deposit campaign hero section. 250:2621 (PC dark bg image,
    position:absolute, no children) paired with 250:3834 (H5 orange bg image,
    position:absolute, no children). Both have same figmaName 'BG20260611-172441 1'.
    Merging discards the H5 image; splitting lets CSS show each on its platform.
    Without this fix: H5 orange background never renders on mobile.
    """
    from merge_responsive import _merge_children

    pc_node = {
        'figmaId': '250:2621', 'figmaName': 'BG20260611-172441 1',
        'figmaType': 'RECTANGLE', 'visible': True,
        'css': {'position': 'absolute', 'width': '1931.57px', 'height': '1229.28px'},
        'children': [], 'fills': [],
    }
    h5_node = {
        'figmaId': '250:3834', 'figmaName': 'BG20260611-172441 1',
        'figmaType': 'RECTANGLE', 'visible': True,
        'css': {'position': 'absolute', 'width': '1931.57px', 'height': '1229.28px'},
        'children': [], 'fills': [],
    }

    result = _merge_children([pc_node], [h5_node], breakpoint=768)

    assert len(result) == 2, (
        f'Expected 2 nodes (PC pcOnly + H5 h5Only), got {len(result)}: '
        f'{[{"id": r.get("figmaId"), "pcOnly": r.get("pcOnly"), "h5Only": r.get("h5Only")} for r in result]}'
    )
    pc_result = next((r for r in result if r.get('figmaId') == '250:2621'), None)
    h5_result = next((r for r in result if r.get('figmaId') == '250:3834'), None)
    assert pc_result is not None, '250:2621 PC node should be present in result'
    assert h5_result is not None, '250:3834 H5 node should be present in result'
    assert pc_result.get('pcOnly'), '250:2621 should be pcOnly=True (hidden on mobile)'
    assert h5_result.get('h5Only'), '250:3834 should be h5Only=True (visible only on mobile)'


def test_merge_children_supp_only_inserted_in_order():
    """_merge_children: h5Only (supp-only) children should be inserted before the
    first matched supp child with a higher index, NOT appended at end.

    Real case: EU Component10Section. H5 children: [FaqHeader(j=0, unmatched),
    FaqExpandable(j=1, matched with PC FaqSection), T&C(j=2, matched)].
    FaqHeader should appear at position 0 (before FaqExpandable result), not at end.
    Without this fix: on mobile the FAQ header text renders AFTER the T&C section.
    """
    from merge_responsive import _merge_children

    def _frame(fid, name, text=None):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'css': {}, 'children': [], 'fills': [],
            'textContent': text,
        }

    # PC has 2 sections; H5 has 3: a header that has no PC equivalent (j=0),
    # plus two that match PC sections (j=1, j=2).
    pc_faq = _frame('1:1', 'FaqSection', 'Q1 Q2')
    pc_tc = _frame('1:2', 'TermsSection', 'Terms')
    h5_header = _frame('2:0', 'FaqHeader')        # unmatched → h5Only, should be j=0
    h5_faq = _frame('2:1', 'FaqSection', 'Q1 Q2')  # matches pc_faq (j=1)
    h5_tc = _frame('2:2', 'TermsSection', 'Terms')  # matches pc_tc (j=2)

    result = _merge_children([pc_faq, pc_tc], [h5_header, h5_faq, h5_tc], breakpoint=768)

    # h5_header (h5Only, j=0) should appear BEFORE the FaqSection result (j=1 match)
    h5_header_idx = next((i for i, r in enumerate(result) if r.get('figmaId') == '2:0'), -1)
    faq_idx = next((i for i, r in enumerate(result) if r.get('figmaId') in ('1:1', '2:1')), -1)
    assert h5_header_idx != -1, 'h5Only FaqHeader node should be present in result'
    assert faq_idx != -1, 'FaqSection node should be present in result'
    assert h5_header_idx < faq_idx, (
        f'h5Only FaqHeader (supp j=0) should appear before FaqSection (supp j=1 matched). '
        f'Got header at position {h5_header_idx}, faq at {faq_idx}. '
        f'Result order: {[r.get("figmaId") for r in result]}'
    )


def test_merge_children_text_title_h5only_does_not_trigger_pconly():
    """h5Only TEXT nodes (title variants like 'Join 70M+' vs 'Join 70+ million') are
    additive title rewording, NOT structural layout replacements. They should NOT count
    toward the h5Only >= pairs threshold that triggers pcOnly marking on paired PC nodes.

    Real case: EU campaign JoinSection (250:3769 PC ↔ 250:4893 H5).
    PC: [title_pc TEXT "Join 70+ million...", trust_cards FRAME]
    H5: [title_h5 TEXT "Join 70M+...", trust_cards_h5 FRAME]
    title_pc and title_h5 have different texts → title_pc unmatched → pcOnly,
    title_h5 unmatched → h5Only (isTextNode=True).
    trust_cards_pc PAIRS with trust_cards_h5 (same trust card texts).
    Before fix: h5Only(1, title_h5) >= pairs(1) → trust_cards becomes pcOnly → HIDDEN on mobile!
    After fix: isTextNode h5Only excluded → count=0 → trust_cards stays SHARED → visible on H5.
    """
    from merge_responsive import _merge_children

    def _frame(fid, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 200},
            'fills': [], 'semantic': {'className': fid},
            'textContent': texts,
        }

    def _text(fid, content):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'TEXT',
            'isTextNode': True, 'visible': True, 'children': [],
            'css': {'font-size': '20px'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 28},
            'fills': [], 'semantic': {'className': fid},
            'textContent': content,
        }

    trust_texts = 'Regulated & compliant\nHeadquartered in Austria\n1:1 reserve\nWorldwide support'

    title_pc = _text('pc:title', 'Join 70+ million customers worldwide')
    trust_pc  = _frame('pc:trust', trust_texts)

    title_h5 = _text('h5:title', 'Join 70M+ users worldwide')
    trust_h5  = _frame('h5:trust', trust_texts)

    result = _merge_children([title_pc, trust_pc], [title_h5, trust_h5], breakpoint=768)

    title_result = next((r for r in result if r.get('figmaId') == 'pc:title'), None)
    assert title_result is not None
    assert title_result.get('pcOnly'), 'PC title with different text should be pcOnly'

    h5_title_result = next((r for r in result if r.get('figmaId') == 'h5:title'), None)
    assert h5_title_result is not None
    assert h5_title_result.get('h5Only'), 'H5 title text should be h5Only'

    trust_result = next((r for r in result if r.get('figmaId') == 'pc:trust'), None)
    assert trust_result is not None, 'trust_pc should appear in result'
    assert not trust_result.get('pcOnly'), (
        'trust_pc should NOT be pcOnly — the h5Only title text is a title variant, '
        'not a structural layout replacement. Got: '
        + str({'pcOnly': trust_result.get('pcOnly'), 'h5Only': trust_result.get('h5Only')})
    )


def test_merge_children_icon_only_h5only_does_not_trigger_pconly():
    """h5Only FRAME nodes that contain ONLY images (no text) are additive icon
    replacements, NOT structural mobile replacements. They should NOT count toward
    the h5Only >= pairs threshold.

    Real case: EU campaign EarnFromSection step-desc (250:2641).
    PC: [desc_text TEXT "Verify your identity"]
    H5: [icon_frame FRAME (image only, no text), title_desc FRAME (has text)]
    icon_frame is the H5-specific icon, title_desc is the H5 title+desc content.
    Before fix: icon_frame (h5Only, non-abs) counted as structural → count=1, pairs=1 →
    triggers pcOnly on merged(desc_text, title_desc) → desc hidden on mobile.
    After fix: icon_frame has no text → excluded → count=0 → desc stays visible on H5.
    """
    from merge_responsive import _merge_children

    def _frame(fid, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 100, 'height': 80},
            'fills': [], 'semantic': {'className': fid},
            'textContent': texts,
        }

    def _text(fid, content):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'TEXT',
            'isTextNode': True, 'visible': True, 'children': [],
            'css': {'font-size': '16px'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 353, 'height': 22},
            'fills': [], 'semantic': {'className': fid},
            'textContent': content,
        }

    pc_desc = _frame('pc:desc', 'Verify your identity')  # FRAME so it can pair with h5 FRAMEs
    h5_icon       = _frame('h5:icon')  # no textContent → icon-only
    h5_title_desc = _frame('h5:title_desc', 'Sign up for By EU\nVerify your identity')

    result = _merge_children([pc_desc], [h5_icon, h5_title_desc], breakpoint=768)

    h5_icon_result = next((r for r in result if r.get('figmaId') == 'h5:icon'), None)
    assert h5_icon_result is not None and h5_icon_result.get('h5Only'), \
        'H5 icon frame should be h5Only'

    pc_desc_result = next((r for r in result if r.get('figmaId') == 'pc:desc'), None)
    assert pc_desc_result is not None, 'pc:desc should appear in result'
    assert not pc_desc_result.get('pcOnly'), (
        'pc:desc should NOT be pcOnly when the only h5Only sibling is an icon-only frame '
        '(no text content). Got: '
        + str({'pcOnly': pc_desc_result.get('pcOnly'), 'h5Only': pc_desc_result.get('h5Only')})
    )


def test_merge_children_text_frame_pair_becomes_platform_specific():
    """When a TEXT node (PC leaf) pairs with a FRAME node (H5 container), they represent
    fundamentally different renderings of the same content — treat as platform-specific
    pair (like abs-leaf), NOT a merged node.

    Real case: EU campaign EarnFromSection step 1 inside earn-step-desc (250:2641).
    PC: [desc TEXT "Verify your identity"]  (leaf, no children)
    H5: [icon FRAME (no text), title_desc FRAME ("Sign up for By EU" + "Verify your...")]
    desc TEXT pairs with title_desc FRAME (both contain "Verify your identity").
    Before fix: TEXT+FRAME merged → produces TEXT node with FRAME h5Only children →
    invalid HTML (span with div children), duplicate text on mobile.
    After fix: TEXT+FRAME pair → desc pcOnly + title_desc h5Only → clean platform separation.
    """
    from merge_responsive import _merge_children

    def _text(fid, content):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'TEXT',
            'isTextNode': True, 'visible': True, 'children': [],
            'css': {'font-size': '16px'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 353, 'height': 22},
            'fills': [], 'semantic': {'className': fid},
            'textContent': content,
        }

    def _frame(fid, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 353, 'height': 80},
            'fills': [], 'semantic': {'className': fid},
            'textContent': texts,
        }

    desc_text = 'Verify your identity'
    pc_desc       = _text('pc:desc', desc_text)
    # Same text to guarantee score >= 0.55 → pair forms → Fix B must split them
    h5_title_desc = _frame('h5:title_desc', desc_text)

    result = _merge_children([pc_desc], [h5_title_desc], breakpoint=768)

    pc_result = next((r for r in result if r.get('figmaId') == 'pc:desc'), None)
    assert pc_result is not None, 'pc:desc should appear in result'
    assert pc_result.get('pcOnly'), (
        'TEXT node should be pcOnly when paired with FRAME (platform-specific pair). '
        'Got: ' + str({'pcOnly': pc_result.get('pcOnly'), 'h5Only': pc_result.get('h5Only')})
    )

    h5_result = next((r for r in result if r.get('figmaId') == 'h5:title_desc'), None)
    assert h5_result is not None, 'h5:title_desc should appear in result'
    assert h5_result.get('h5Only'), (
        'FRAME node paired with TEXT should be h5Only (platform-specific). '
        'Got: ' + str({'pcOnly': h5_result.get('pcOnly'), 'h5Only': h5_result.get('h5Only')})
    )

    merged_nodes = [r for r in result if not r.get('pcOnly') and not r.get('h5Only')]
    assert len(merged_nodes) == 0, (
        'No merged nodes expected for TEXT+FRAME pair. '
        f'Got: {[r.get("figmaId") for r in merged_nodes]}'
    )


def test_merge_children_standalone_text_h5only_counts_as_structural():
    """h5Only TEXT nodes WITHOUT a corresponding pcOnly TEXT sibling ARE structural.
    They represent H5-specific content that isn't just a title variant.

    Real case: EU campaign OriginalCopySection (250:2659).
    PC: [welcome_container FRAME (has 48px PC title inside)]
    H5: [welcome_container FRAME (paired), subtitle TEXT "Earn up to €170..." (supp-only)]
    No pcOnly TEXT sibling exists → subtitle is H5-specific content → structural → fires.
    Before refined fix (Fix A): subtitle TEXT excluded (isTextNode) → count=0 → welcome SHARED
                                → 48px PC title shows on mobile → REGRESSION.
    After refined fix: no pcOnly TEXT sibling → subtitle counted → check fires → welcome pcOnly ✓
    """
    from merge_responsive import _merge_children

    def _frame(fid, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 200},
            'fills': [], 'semantic': {'className': fid},
            'textContent': texts,
        }

    def _text(fid, content):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'TEXT',
            'isTextNode': True, 'visible': True, 'children': [],
            'css': {'font-size': '16px'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 22},
            'fills': [], 'semantic': {'className': fid},
            'textContent': content,
        }

    shared_pkg_text = 'New User Welcome Package'
    # PC: welcome container with title text inside (PC-only 48px layout)
    welcome_pc = _frame('pc:welcome', children=[_frame('pc:inner', shared_pkg_text)])
    # H5: same container (will pair) + h5-specific subtitle (supp-only TEXT, no PC counterpart)
    welcome_h5 = _frame('h5:welcome', children=[_frame('h5:inner', shared_pkg_text)])
    subtitle_h5 = _text('h5:subtitle', 'Earn up to €170 & get subscription cashback on what you love')

    result = _merge_children([welcome_pc], [subtitle_h5, welcome_h5], breakpoint=768)

    # subtitle_h5 should be h5Only (supp-only)
    sub_result = next((r for r in result if r.get('figmaId') == 'h5:subtitle'), None)
    assert sub_result is not None and sub_result.get('h5Only'), \
        'H5-only subtitle should be h5Only'

    # CRITICAL: welcome_pc should be pcOnly
    # h5Only TEXT (subtitle) has NO pcOnly TEXT sibling → it's H5-specific content → structural
    # → h5_only_structural_count=1, pairs=1 → check fires → welcome_pc becomes pcOnly
    pc_result = next((r for r in result if r.get('figmaId') == 'pc:welcome'), None)
    assert pc_result is not None, 'pc:welcome should appear in result'
    assert pc_result.get('pcOnly'), (
        'pc:welcome should be pcOnly: the h5Only subtitle TEXT has no pcOnly TEXT sibling, '
        'meaning it is H5-specific structural content (not a title variant). '
        'Got: ' + str({'pcOnly': pc_result.get('pcOnly'), 'h5Only': pc_result.get('h5Only')})
    )


def test_merge_children_h5only_structural_extracts_h5_sibling():
    """When h5_only_structural check fires, the H5 supplement content that was merged
    into the now-pcOnly PC node should be re-extracted as an h5Only SIBLING so it can
    be shown on mobile.

    Without this fix: H5 content is absorbed inside a pcOnly parent → invisible on mobile.
    With fix: H5 content appears as h5Only sibling alongside the pcOnly PC node.

    Real case: EU campaign CalculateYourCashbackSection.
    PC: [bg, title_text(pcOnly), calculator_pc]
    H5: [sticky_bar(h5Only struct), calculator_h5]
    sticky_bar is structural → check fires → calculator_pc becomes pcOnly.
    Before: calculator_h5 absorbed into pcOnly calculator_pc → hidden on mobile.
    After: calculator_h5 extracted as h5Only sibling → visible on mobile (full H5 calc UI).
    """
    from merge_responsive import _merge_children

    def _frame(fid, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 300},
            'fills': [], 'semantic': {'className': fid},
            'textContent': texts,
        }

    # PC: calculator content (pairs with H5 calculator)
    pc_calc = _frame('pc:calc', 'Tier 1\n300,000\n125 USDC')
    # H5: sticky bar (structural, triggers check) + separate H5 calculator content
    h5_sticky  = _frame('h5:sticky', 'Cashback calculator')  # structural h5Only trigger
    h5_calc    = _frame('h5:calc', 'Calculate your monthly cashback\n125 USDC\nSign up to earn Rewards')

    result = _merge_children([pc_calc], [h5_sticky, h5_calc], breakpoint=768)

    # h5:sticky should be h5Only
    sticky_r = next((r for r in result if r.get('figmaId') == 'h5:sticky'), None)
    assert sticky_r and sticky_r.get('h5Only'), 'H5 sticky bar should be h5Only'

    # pc:calc should be pcOnly (h5_only check fires because h5_sticky is structural)
    pc_r = next((r for r in result if r.get('figmaId') == 'pc:calc'), None)
    assert pc_r and pc_r.get('pcOnly'), 'PC calculator should be pcOnly when structural h5Only present'

    # CRITICAL: h5:calc should ALSO appear as h5Only sibling (Fix C)
    h5_calc_r = next((r for r in result if r.get('figmaId') == 'h5:calc'), None)
    assert h5_calc_r is not None, (
        'H5 calculator content should appear as h5Only sibling — it was paired with pc:calc '
        'and then both became hidden when check fired (pc:calc pcOnly, h5:calc absorbed inside). '
        'Fix C must re-extract h5:calc as separate h5Only sibling so it shows on mobile.'
    )
    assert h5_calc_r.get('h5Only'), (
        'H5 calculator content should be h5Only. Got: '
        + str({'pcOnly': h5_calc_r.get('pcOnly'), 'h5Only': h5_calc_r.get('h5Only')})
    )


def test_merge_children_unmatched_pc_shared_when_text_in_h5_subtree():
    """Unmatched PC children whose text content appears in the matched H5 subtree
    should be SHARED (not pcOnly) — they represent the same content in different structures.

    Real case: EU campaign EarnFromSection step item (250:2636).
    PC: [icon FRAME (no text), title TEXT "Sign up for By EU", desc_container FRAME]
    H5: [wrapper FRAME containing icon + "Sign up for By EU" + "Verify your identity"]

    wrapper matches desc_container (both contain desc text) → pair.
    icon has no text → unmatched → pcOnly (correct).
    title "Sign up for By EU" → its text exists in matched H5 wrapper subtree → SHARED!

    Before fix: title unmatched → pcOnly → title hidden on mobile (only step icon+desc visible).
    After fix: title text found in H5 → SHARED → title visible on mobile.
    """
    from merge_responsive import _merge_children

    def _frame(fid, texts=None, children=None):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'FRAME',
            'visible': True, 'children': children or [],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 353, 'height': 80},
            'fills': [], 'semantic': {'className': fid},
            'textContent': texts,
        }

    def _text(fid, content):
        return {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'TEXT',
            'isTextNode': True, 'visible': True, 'children': [],
            'css': {'font-size': '24px'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 353, 'height': 28},
            'fills': [], 'semantic': {'className': fid},
            'textContent': content,
        }

    title_text = 'Sign up for By EU'
    desc_text  = 'Verify your identity'

    # PC step item: [icon frame (no text), title text, desc frame with TEXT child]
    # NOTE: desc frame must have isTextNode children so match_score can extract texts
    # and pair it with h5_wrapper (which also contains the same desc text).
    pc_icon  = _frame('pc:icon')  # no text
    pc_title = _text('pc:title', title_text)
    pc_desc  = _frame('pc:desc', children=[_text('pc:desc:leaf', desc_text)])

    # H5 step item: single wrapper with TEXT children (needed for match_score extraction)
    h5_wrapper = _frame('h5:wrapper', children=[
        _frame('h5:icon'),
        _frame('h5:title_desc', children=[
            _text('h5:title_leaf', title_text),
            _text('h5:desc_leaf', desc_text),
        ]),
    ])

    result = _merge_children([pc_icon, pc_title, pc_desc], [h5_wrapper], breakpoint=768)

    # pc:icon has no text → can't be found in H5 subtree → should be pcOnly
    icon_r = next((r for r in result if r.get('figmaId') == 'pc:icon'), None)
    assert icon_r and icon_r.get('pcOnly'), 'PC icon (no text) should be pcOnly'

    # CRITICAL: pc:title text exists in H5 subtree → should be SHARED (not pcOnly)
    title_r = next((r for r in result if r.get('figmaId') == 'pc:title'), None)
    assert title_r is not None, 'pc:title should appear in result'
    assert not title_r.get('pcOnly'), (
        f'PC title "{title_text}" should NOT be pcOnly — its text appears in the matched '
        f'H5 subtree, so it represents the same content and should be shared. '
        f'Got: ' + str({'pcOnly': title_r.get('pcOnly'), 'h5Only': title_r.get('h5Only')})
    )


def test_merge_children_h5only_gets_cssclass_suffix_on_collision():
    """_merge_children: when pcOnly and h5Only nodes share the same cssClass,
    the h5Only node's cssClass is renamed with '-h5' suffix to avoid selector collision.

    Root Cause B fix: PC and H5 counterparts sharing the same figmaName derive the same
    cssClass. split_codegen._collect_inline_responsive_flags detects this as a conflict
    (flag=None) and falls back to [data-figma-id] selectors for both. Adding '-h5' suffix
    gives each a unique class so they can be handled independently.

    Real case: EU campaign hero 250:2621 (PC dark bg) + 250:3834 (H5 orange bg).
    Both are abs-leaf RECTANGLE nodes with the same figmaName → same cssClass 'hero-bg'.
    """
    from merge_responsive import _merge_children

    def _abs_node(fid, css_class):
        return {
            'figmaId': fid, 'figmaName': css_class,
            'figmaType': 'RECTANGLE',
            'cssClass': css_class,
            'visible': True, 'children': [],
            'css': {'position': 'absolute', 'width': '100%', 'height': '100%'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 600},
            'fills': [{'type': 'IMAGE'}],
            'semantic': {'className': css_class},
            'textContent': None,
        }

    # Real data: EU campaign hero section — PC and H5 bg both named 'hero-bg'
    pc_bg = _abs_node('250:2621', 'hero-bg')
    h5_bg = _abs_node('250:3834', 'hero-bg')

    result = _merge_children([pc_bg], [h5_bg], breakpoint=768)

    pc_r = next((r for r in result if r.get('figmaId') == '250:2621'), None)
    h5_r = next((r for r in result if r.get('figmaId') == '250:3834'), None)

    assert pc_r is not None and pc_r.get('pcOnly'), 'PC abs-leaf should be pcOnly'
    assert h5_r is not None and h5_r.get('h5Only'), 'H5 abs-leaf should be h5Only'

    assert pc_r.get('cssClass') == 'hero-bg', (
        f'PC cssClass should be unchanged. Got: {pc_r.get("cssClass")}'
    )
    assert h5_r.get('cssClass') == 'hero-bg-h5', (
        f'h5Only node sharing cssClass with pcOnly counterpart should get "-h5" suffix. '
        f'Got: {h5_r.get("cssClass")}'
    )


def test_fix_d_icon_frame_single_child_h5_wrapper_shared():
    """Fix D: Unmatched PC icon must be SHARED when the imageRef appears in matched H5
    subtrees but the H5 matched node has only ONE direct child (a wrapper containing
    the icon). In this case the inner merge will match the wrapper as a whole, and the
    icon INSIDE will NOT become an h5Only node — so there is no h5Only duplicate.
    Marking the PC icon as SHARED is correct here.

    Fix D2 (contrast): if the H5 matched node has ≥2 direct children and one of them
    is the icon, the icon WILL become h5Only in the inner merge → marking PC icon
    as SHARED would create a duplicate → PC icon must be pcOnly instead.

    This test covers the "single child wrapper" case where SHARED is correct:
    PC: [icon (UNMATCHED), desc-text (MATCHED)]
    H5: [h5_outer → [icon → …]] (single child outer, matched with desc-text)
    The icon is nested inside a single wrapper — it will be consumed (not h5Only).
    """
    from merge_responsive import _merge_children

    ICON_IMAGE_REF = 'cc001122334455667788990011223344556677aa'

    def _icon_node(fid, image_ref):
        return {
            'figmaId': fid, 'figmaName': 'Icon', 'figmaType': 'FRAME',
            'visible': True, 'isImageNode': False, 'isVectorNode': False,
            'css': {'width': '80px', 'height': '80px'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 80, 'height': 80},
            'fills': [], 'children': [{
                'figmaId': f'{fid}:img', 'figmaName': 'img',
                'figmaType': 'RECTANGLE', 'visible': True,
                'imageRef': image_ref,
                'isImageNode': True, 'isVectorNode': False,
                'css': {}, 'children': [], 'fills': [],
            }],
        }

    def _text_node(fid, text):
        return {
            'figmaId': fid, 'figmaName': text, 'figmaType': 'TEXT',
            'visible': True, 'isTextNode': True, 'textContent': text,
            'css': {}, 'children': [], 'fills': [],
        }

    def _frame(fid, name, children):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'css': {'display': 'flex'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 100},
            'fills': [], 'children': children,
        }

    # PC: [icon (UNMATCHED), desc (MATCHED)]
    pc_icon = _icon_node('pc:icon', ICON_IMAGE_REF)
    pc_desc = _text_node('pc:desc', 'Earn rewards on purchases')
    base_children = [pc_icon, pc_desc]

    # H5: [outer_wrapper → [inner_icon, inner_text]] — only ONE direct child (outer_wrapper)
    # outer_wrapper will be matched with pc_desc (same text found inside)
    h5_inner_icon = _icon_node('h5:icon', ICON_IMAGE_REF)
    h5_inner_text = _text_node('h5:text', 'Earn rewards on purchases')
    h5_outer = _frame('h5:outer', 'outer', [h5_inner_icon, h5_inner_text])
    h5_single_wrap = _frame('h5:wrap', 'wrap', [h5_outer])  # single-child wrapper
    supp_children = [h5_single_wrap]

    result = _merge_children(base_children, supp_children, breakpoint=768)

    pc_icon_result = next((r for r in result if r.get('figmaId') == 'pc:icon'), None)
    assert pc_icon_result is not None, (
        'pc:icon must appear in merge result. '
        f'Got: {[r.get("figmaId") for r in result]}'
    )

    # H5 matched node (h5_single_wrap) has ONE direct child → inner icon won't be h5Only
    # → No duplicate risk → Fix D fires → pc:icon is SHARED
    assert not pc_icon_result.get('pcOnly'), (
        'PC icon where H5 matched subtree has one direct child (no direct icon sibling) must be SHARED.\n'
        'Fix D: H5 direct children < 2 with icon → no h5Only duplicate → mark SHARED.\n'
        f'Got result: pcOnly={pc_icon_result.get("pcOnly")}'
    )


def test_fix_d_icon_frame_with_imageref_only_in_h5_matched_marked_pconly():
    """Fix D2: PC icon must be pcOnly when imageRef only appears in matched H5 subtrees
    (not in matched PC subtrees). This indicates the H5 uses the icon as an h5Only
    sub-node bundled inside a wrapper — marking PC icon as SHARED would duplicate it.

    Real case: EU campaign EarnFromSection step 1.
    PC 250:2636: [250:2637 Trade icon (UNMATCHED), 250:2639 title, 250:2641 desc-wrapper]
    H5 250:3850: [250:3851 Trade icon (SAME imageRef), 250:3853 text wrapper]
    250:2641 (PC desc) is MATCHED with 250:3850 (H5 inner wrapper).
    → matched_pc_image_refs = refs(250:2641) = {} (no images in desc wrapper)
    → matched_h5_image_refs = refs(250:3850) = {fa1fc450} (from 250:3851 inside)
    Fix D: fa1fc450 IN h5 but NOT IN pc matched refs → 250:2637 pcOnly=True.
    250:3851 inside 250:3850 later becomes h5Only via inner _merge_children.
    """
    from merge_responsive import _merge_children

    TRADE_IMAGE_REF = 'fa1fc450f958563ec6f52a05aa0fb46b855fb320'

    def _icon_node(fid, image_ref):
        return {
            'figmaId': fid, 'figmaName': 'Trade', 'figmaType': 'FRAME',
            'visible': True, 'isImageNode': False, 'isVectorNode': False,
            'css': {'width': '100px', 'height': '80px', 'position': 'relative'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 100, 'height': 80},
            'fills': [], 'children': [{
                'figmaId': f'{fid}:child', 'figmaName': 'img',
                'figmaType': 'RECTANGLE', 'visible': True,
                'imageRef': image_ref,
                'isImageNode': True, 'isVectorNode': False,
                'css': {}, 'children': [], 'fills': [],
            }],
        }

    def _text_node(fid, text):
        return {
            'figmaId': fid, 'figmaName': text, 'figmaType': 'TEXT',
            'visible': True, 'isTextNode': True, 'textContent': text,
            'css': {}, 'children': [], 'fills': [],
        }

    def _frame(fid, name, children):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'css': {'display': 'flex'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 139},
            'fills': [], 'children': children,
        }

    # PC step 1: [icon(Trade, imageRef=fa1fc450), title, desc-wrapper (→ text only)]
    pc_icon = _icon_node('250:2637', TRADE_IMAGE_REF)  # UNMATCHED PC icon
    pc_title = _text_node('250:2640', 'Sign up for By EU')
    pc_desc_wrapper = _frame('250:2641', 'desc-wrapper', [
        _text_node('250:2642', 'Verify your identity')
    ])  # No imageRef in PC desc-wrapper
    base_children = [pc_icon, pc_title, pc_desc_wrapper]

    # H5 step 1: [h5_inner_wrapper → [h5_icon (SAME imageRef), h5_text_wrapper → [text1, text2]]]
    # Note: h5_icon is inside h5_inner_wrapper which will be MATCHED with pc_desc_wrapper or pc_title
    h5_icon = _icon_node('250:3851', TRADE_IMAGE_REF)  # Raw H5 icon — no h5Only flag yet
    h5_text = _frame('250:3853', 'text-wrapper', [
        _text_node('250:3854', 'Sign up for By EU'),
        _text_node('250:3857', 'Verify your identity'),
    ])
    h5_inner_wrapper = _frame('250:3850', 'inner-wrapper', [h5_icon, h5_text])
    supp_children = [h5_inner_wrapper]

    result = _merge_children(base_children, supp_children, breakpoint=768)

    pc_icon_result = next((r for r in result if r.get('figmaId') == '250:2637'), None)
    assert pc_icon_result is not None, (
        '250:2637 (PC icon) must appear in merge result. '
        f'Got: {[r.get("figmaId") for r in result]}'
    )

    # Fix D2: imageRef fa1fc450 is in matched_h5_image_refs (from h5_inner_wrapper)
    # BUT NOT in matched_pc_image_refs (pc matched subtree has no imageRef)
    # → condition fails → pc_icon must be pcOnly=True
    assert pc_icon_result.get('pcOnly'), (
        'PC icon whose imageRef is ONLY in matched H5 subtrees (not PC) must be pcOnly=True.\n'
        'Fix D2: b_image_refs not subset of _matched_pc_image_refs → Fix D does not fire.\n'
        f'Got result: pcOnly={pc_icon_result.get("pcOnly")}'
    )



def test_u435_text_child_gets_center_alignment_inside_flex_centered_parent():
    """U-435: when a TEXT child has text-align:left width:100% inside a flex container
    with justify-content:center, _merge_node_pair must add text-align:center to the
    TEXT child's responsive so that button text appears centered in H5.

    Real data: EU deposit campaign hero CTA button (250:2631 by_primary-buttons-dark).
    PC parent: display:flex; justify-content:center; height:48px (orange button).
    PC child 'I250:2631;2:8053' (Button Text): text-align:left; width:100%.
    H5 parent: text-only (height:unset, bg:transparent after U-425/427 resets).
    H5 child: same text-align:left; width:100%.

    Bug: css_diff(pc_text_child, h5_text_child) → responsive={} (Case 1: same value both
    sides). The .button-text class in H5 inherits text-align:left within a 100%-wide span
    inside the full-width hero-cta-row orange pill. "Register Now" appears at far left.

    Fix: after merging children, post-process TEXT children with text-align:left width:100%
    inside a flex-justified-center parent — add text-align:center to their responsive.
    The @media override wins over the base .button-text{text-align:left} at same specificity
    because the media rule comes later in the CSS cascade.
    """
    from merge_responsive import _merge_node_pair

    def _make_text_child(fid, text='Register Now'):
        return {
            'figmaId': fid, 'figmaName': 'Button Text', 'figmaType': 'TEXT',
            'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
            'isComponentInstance': False, 'textContent': text,
            'css': {'flex-shrink': '0', 'text-align': 'left', 'width': '100%',
                    'font-size': '16px', 'font-weight': '600', 'color': '#121214'},
            'children': [], 'visible': True,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 100, 'height': 24},
            'fills': [],
        }

    def _make_button(fid, name, css, children):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'INSTANCE',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isComponentInstance': True, 'textContent': None,
            'css': css, 'children': children, 'visible': True,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 160, 'height': 48},
            'fills': [],
        }

    pc_text = _make_text_child('I250:2631;2:8053')
    h5_text = _make_text_child('I250:3845;513:18871')

    pc_button = _make_button('250:2631', 'Example_Primary Buttons-Dark', {
        'display': 'flex', 'justify-content': 'center', 'align-items': 'center',
        'width': '100%', 'height': '48px', 'padding': '12px 28px',
        'background-color': '#ff9c2e', 'border-radius': '24px',
    }, [pc_text])
    h5_button = _make_button('250:3845', 'button', {
        'width': '100%', 'flex-shrink': '0',
    }, [h5_text])

    result = _merge_node_pair(pc_button, h5_button, 768)

    # Find the merged button text child
    merged_text = None
    for ch in (result.get('children') or []):
        if ch.get('figmaId') == 'I250:2631;2:8053':
            merged_text = ch
            break

    assert merged_text is not None, 'U-435 FAIL: button text child not found in merged result'

    text_resp = {}
    for r in (merged_text.get('responsive') or []):
        text_resp.update(r.get('css', {}))

    # text-align must use !important to overcome the [dir="ltr"] .class { text-align:left }
    # rule generated by the project's RTL PostCSS plugin (specificity 11 > @media rule 10).
    assert text_resp.get('text-align') in ('center', 'center !important'), (
        f'U-435 FAIL: TEXT child with text-align:left width:100% inside flex-centered parent '
        f'must get responsive text-align:center (with !important) to override the RTL plugin '
        f'[dir="ltr"] selector. '
        f'Bug: "Register Now" in hero CTA button appears left-aligned. '
        f'Got responsive: {text_resp}'
    )


def test_u433_structural_split_text_vs_frame_applies_h5_text_css():
    """U-433: when a PC TEXT node structurally-splits with an H5 FRAME container,
    _merge_node_pair should look for the matching text node in the H5 subtree and
    apply its CSS as responsive overrides on the PC text node.

    Real data: EU deposit campaign EarnFromSection step 2.
    PC node 250:2649: TEXT 'To be eligible for the rewards', width=250px, white-space=nowrap.
    H5 supplement 250:3860: FRAME container holding [icon, text-area → [title, desc]].
    The desc child 250:3867 has the same text and width=100%.

    Bug: _merge_node_pair returns {structuralSplit:True} with no responsive overrides.
    The PC text keeps width=250px in H5, appearing left-biased in a 353px flex-start parent.

    Fix: for PC TEXT + H5 non-TEXT structural splits, scan the H5 supplement subtree for
    a TEXT node with matching textContent, then apply css_diff(pc_css, h5_text_css) as
    responsive overrides — so the PC text gets max-width:none + white-space:normal in H5.
    """
    from merge_responsive import _merge_node_pair

    def _make_text_node(fid, text, css, children=None):
        return {
            'figmaId': fid, 'figmaName': text[:30], 'figmaType': 'TEXT',
            'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
            'isComponentInstance': False, 'textContent': text,
            'css': css, 'children': children or [], 'visible': True,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 250, 'height': 20},
            'fills': [],
        }

    def _make_frame(fid, name, css, children=None):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isComponentInstance': False, 'textContent': None,
            'css': css, 'children': children or [], 'visible': True,
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 353, 'height': 139},
            'fills': [],
        }

    pc_text = _make_text_node('250:2649', 'To be eligible for the rewards', {
        'width': '250px', 'text-align': 'center', 'white-space': 'nowrap',
        'flex-shrink': '0', 'color': '#7f838a', 'font-size': '16px',
    })
    h5_title = _make_text_node('250:3866', 'Register for this promotion', {
        'width': '100%', 'text-align': 'center', 'white-space': 'nowrap',
    })
    h5_desc = _make_text_node('250:3867', 'To be eligible for the rewards', {
        'width': '100%', 'text-align': 'center', 'white-space': 'nowrap',
        'flex-shrink': '0', 'color': '#6a6e73', 'font-size': '16px',
    })
    h5_text_area = _make_frame('250:3864', 'Frame 2147224422',
                               {'width': '100%', 'flex-direction': 'column', 'gap': '8px'},
                               children=[h5_title, h5_desc])
    h5_icon = _make_frame('250:3861', 'Trade', {'width': '100px', 'height': '80px'})
    h5_container = _make_frame('250:3860', 'Frame 2147224424',
                               {'width': '100%', 'flex-direction': 'column', 'gap': '16px'},
                               children=[h5_icon, h5_text_area])

    result = _merge_node_pair(pc_text, h5_container, 768)

    assert result.get('structuralSplit'), 'U-433: should be structuralSplit=True (sim < threshold)'

    resp_css = {}
    for r in (result.get('responsive') or []):
        resp_css.update(r.get('css', {}))

    assert resp_css.get('width') == '100%', (
        f'U-433 FAIL: PC text width:250px + H5 text width:100% should produce responsive '
        f"width:'100%' to override the PC fixed width in H5 (structural split: base CSS NOT "
        f'updated, so width:100% must be explicit in responsive). Got responsive: {resp_css}'
    )
    # white-space: both PC and H5 have 'nowrap' → Case 1 (same value) → no reset needed.
    # The description text is short enough to fit on one line even with nowrap.


def test_u436_merged_children_follow_h5_layer_order():
    """U-436: _build_merged_ir must order new_children by H5's original layer index.

    Bug (real data: merged-315-29892, by-meta H5 page):
    - H5 has 13 children: Rectangle 34627240 (index 0, background) ... Frame 2147229648 (index 7+, main content)
    - PC has 1 child: Homepage-Web (matched with H5 Frame 2147229648 at supp_idx=7)
    - Current logic puts PC section first → new_children = [Homepage-Web, Rectangle, ...]
      → Homepage-Web gets z-index:0, Rectangle gets z-index:1
      → Rectangle (position:absolute, z-index:1) covers Homepage-Web (position:relative, z-index:auto)
      → H5 content blocked on mobile

    Fix: new_children must follow H5 original layer order:
      [Rectangle(idx=0), ...(idx=1..6)..., Homepage-Web(idx=7), ...(idx=8+)...]
      → Rectangle z-index:0, Homepage-Web z-index:7 (above background)

    Concrete test scenario (simplified from real data):
    - H5: [bg_rect(idx=0), nav(idx=1), main_content(idx=2, matched), deco(idx=3)]
    - PC: [pc_section(idx=0, matched with H5 idx=2)]
    - Expected order: [bg_rect, nav, pc_merged, deco]
    - Background (z-index:0) < nav (z-index:1) < pc_section (z-index:2) < deco (z-index:3)
    """
    from merge_responsive import _build_merged_ir

    def _make_sec(fid, name, figma_type='FRAME'):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': figma_type,
            'visible': True, 'children': [], 'css': {},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 100},
            'fills': [],
        }

    # H5 IR: 4 sections, main content at index 2
    h5_ir = {
        'figmaId': 'h5:root', 'figmaName': 'H5Page', 'figmaType': 'FRAME',
        'visible': True, 'css': {}, 'fills': [],
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 393, 'height': 1200},
        'children': [
            _make_sec('h5:10', 'BgRect', 'RECTANGLE'),   # idx=0 background
            _make_sec('h5:11', 'NavBar'),                  # idx=1 navigation
            _make_sec('h5:12', 'MainContent'),             # idx=2 ← matched with PC
            _make_sec('h5:13', 'DecoOverlay'),             # idx=3 decoration on top
        ],
    }
    # PC IR: 1 section (matches H5 MainContent at supp_idx=2)
    pc_ir = {
        'figmaId': 'pc:root', 'figmaName': 'PCPage', 'figmaType': 'FRAME',
        'visible': True, 'css': {}, 'fills': [],
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 900},
        'children': [
            _make_sec('pc:20', 'PCSection'),  # idx=0, matches H5 idx=2
        ],
    }

    # auto_matches: (supp_idx=2, base_idx=0, score=0.9) — H5[2] ↔ PC[0]
    auto_matches = [(2, 0, 0.9)]
    h5_only = [0, 1, 3]   # H5 idx=0,1,3 are H5-only (background, nav, decoration)
    pc_only = []

    merged = _build_merged_ir(pc_ir, h5_ir, auto_matches, h5_only, pc_only, 'pc', 768)
    children = merged.get('children', [])

    assert len(children) == 4, f'U-436a: expected 4 children, got {len(children)}'

    names = [c.get('figmaName') for c in children]

    # BgRect should come BEFORE the matched PC section
    bg_idx = next((i for i, c in enumerate(children) if c.get('figmaId') == 'h5:10'), -1)
    nav_idx = next((i for i, c in enumerate(children) if c.get('figmaId') == 'h5:11'), -1)
    pc_idx = next((i for i, c in enumerate(children) if c.get('figmaId') == 'pc:20'), -1)
    deco_idx = next((i for i, c in enumerate(children) if c.get('figmaId') == 'h5:13'), -1)

    assert bg_idx != -1, 'U-436b: BgRect not found in merged children'
    assert pc_idx != -1, 'U-436c: PC section not found in merged children'
    assert deco_idx != -1, 'U-436d: DecoOverlay not found in merged children'

    assert bg_idx < pc_idx, (
        f'U-436e: BgRect(z-index:{bg_idx}) must come BEFORE PCSection(z-index:{pc_idx}). '
        f'Got order: {names}. '
        f'Bug: background rectangle with z-index:1 covers PC section (position:relative, z-index:auto)'
    )
    assert nav_idx < pc_idx, (
        f'U-436f: NavBar must come before PCSection. Got: {names}'
    )
    assert pc_idx < deco_idx, (
        f'U-436g: PCSection(z-index:{pc_idx}) must come BEFORE DecoOverlay(z-index:{deco_idx}). '
        f'Got: {names}'
    )


if __name__ == '__main__':
    import traceback
    tests = [(n, obj) for n, obj in sorted(globals().items())
             if n.startswith('test_') and callable(obj)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'  ✓  {name}')
            passed += 1
        except Exception as e:
            print(f'  ✗  {name}: {e}')
            traceback.print_exc()
            failed += 1
    total = passed + failed
    print(f'\n({passed}/{total}) integration tests')
    if failed:
        sys.exit(1)


def test_build_merged_ir_pc_base_preserves_pc_section_order():
    """U-441: _build_merged_ir with base_platform='pc' must order sections by PC
    (base) index, not H5 (supplement) index.

    Real data: node 202:33464 (模拟盘落地页) + 1484:27139 (H5).
    Bug: algorithm placed matched sections at H5 index positions (H5[1]=Advantages,
    H5[2]=Header), yielding order [Advantages, Header, ...] instead of
    [Header, ..., Advantages]. Root cause: h5_idx_to_child sorted by supp_idx
    even when base_platform='pc'.

    In this test: PC has 4 sections A(0), B(1), C(2), D(3).
    H5 has 2 sections matched cross-index: H5[0]↔PC[2]=C, H5[1]↔PC[0]=A.
    PC-only: B(1), D(3).
    Expected PC-base order: A(0), B(1,pcOnly), C(2), D(3,pcOnly).
    Bug order (H5-indexed): C(H5[0]), A(H5[1]), B(pcOnly), D(pcOnly).
    """
    from merge_responsive import _build_merged_ir

    def _sec(fid, name):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'css': {}, 'children': [], 'fills': [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 200},
        }

    pc_ir = {
        'figmaId': 'page:pc', 'figmaName': 'Page', 'figmaType': 'FRAME',
        'visible': True, 'css': {},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 800},
        'children': [
            _sec('pc:0', 'SecA'),
            _sec('pc:1', 'SecB'),
            _sec('pc:2', 'SecC'),
            _sec('pc:3', 'SecD'),
        ],
    }
    h5_ir = {
        'figmaId': 'page:h5', 'figmaName': 'Page', 'figmaType': 'FRAME',
        'visible': True, 'css': {},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 800},
        'children': [
            _sec('h5:0', 'SecC-H5'),   # matches PC[2]=SecC
            _sec('h5:1', 'SecA-H5'),   # matches PC[0]=SecA
        ],
    }
    # Cross-indexed: H5[0]↔PC[2], H5[1]↔PC[0]; PC-only: [1, 3]
    auto_matches = [(0, 2, 0.9), (1, 0, 0.9)]   # (h5_i, pc_i, score)
    pc_only = [1, 3]

    merged = _build_merged_ir(pc_ir, h5_ir, auto_matches, [], pc_only, 'pc', 768)

    figma_ids = [c['figmaId'] for c in merged['children']]
    # Expected PC-base order: SecA(pc:0), SecB(pc:1), SecC(pc:2), SecD(pc:3)
    assert figma_ids == ['pc:0', 'pc:1', 'pc:2', 'pc:3'], (
        f'Expected PC-base order [pc:0, pc:1, pc:2, pc:3], got {figma_ids}\n'
        f'(Bug would produce [pc:2, pc:0, pc:1, pc:3] — H5-indexed order)'
    )
    # B and D must be pcOnly
    children_by_id = {c['figmaId']: c for c in merged['children']}
    assert children_by_id['pc:1'].get('pcOnly'), 'SecB (unmatched) should be pcOnly'
    assert children_by_id['pc:3'].get('pcOnly'), 'SecD (unmatched) should be pcOnly'


def test_u464_fix_d_sym_not_skipped_when_imageref_only_in_pc_subtree():
    """U-464: Fix D-sym must NOT skip an H5-only node when its imageRefs exist in the
    matched PC subtrees but NOT in the matched H5 subtrees.

    Real case: Tomorrowland VIP Card section (39641-6863).
    PC section 39641:7672 matched with H5 section 39897:4550.
    Within the matched pair:
      - PC child 5102 (main content) matches H5 child 19895 (main content wrapper).
      - Inside PC 5102: card display node 5115 with imageRefs ['card_img_1', 'card_img_2'].
      - H5 child 19899 (card images container) is UNMATCHED at section level,
        has imageRefs ['card_img_1', 'card_img_2'] — the same as PC 5115.
      - H5 main content 19895 does NOT contain those imageRefs.

    Bug: Fix D-sym sees 19899.imageRefs.issubset(_matched_pc_image_refs) → True
    (because pc 5102 subtree contains 5115's imageRefs) → SKIPS 19899.
    Result: 19899 is missing from merged IR → blank card area on mobile.

    Fix: Fix D-sym must also require imageRefs.issubset(_matched_h5_image_refs).
    Since H5 matched subtree (19895) does NOT have those imageRefs,
    h5_refs.issubset(_matched_h5_image_refs) → False → 19899 is kept as h5Only.
    """
    from merge_responsive import _merge_children

    CARD_REF_1 = '6c98b448ed1d4a1eb33417d1e170ecbc6651f0cb'
    CARD_REF_2 = '7f5e22d9ace80db8da5fdf25087c85b47ad07c49'

    def _node(fid, w, h, children=None, image_ref=None, is_text=False):
        n = {
            'figmaId': fid, 'figmaName': fid, 'figmaType': 'FRAME',
            'visible': True,
            'css': {'width': w, 'height': h, 'position': 'relative'},
            'children': children or [],
            'fills': [{'type': 'IMAGE', 'imageRef': image_ref}] if image_ref else [],
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 100, 'height': 100},
        }
        if image_ref:
            n['imageRef'] = image_ref
        if is_text:
            n['isTextNode'] = True
            n['textContent'] = fid
        return n

    # PC card display node (inside pc_main → pc_card_display)
    pc_card_img_1 = _node('pc:card_img_1', '266px', '168px', image_ref=CARD_REF_1)
    pc_card_img_2 = _node('pc:card_img_2', '266px', '168px', image_ref=CARD_REF_2)
    pc_card_display = _node('pc:card_display', '481px', '316px',
                            children=[pc_card_img_1, pc_card_img_2])

    # PC main content (contains card_display + text)
    # Real data: node I39641:7672;39641:5102 from Tomorrowland PC
    pc_text = _node('pc:text', '100%', 'auto', is_text=True)
    pc_text['textContent'] = 'Your Pass to the Magic with Example Card'
    pc_main = _node('pc:main_content', '1200px', '494px',
                    children=[pc_text, pc_card_display])

    # H5 main content (NO card imageRefs inside — different structure)
    # Real data: node I39897:4550;39603:19895 from Tomorrowland H5
    h5_text = _node('h5:text', '100%', 'auto', is_text=True)
    h5_text['textContent'] = 'Your Pass to the Magic with Example Card'  # same text → triggers pairing
    h5_main = _node('h5:main_content', '100%', '682px', children=[h5_text])

    # H5 card images container (h5-only, same imageRefs as PC card)
    h5_card_img_1 = _node('h5:card_img_1', '266px', '168px', image_ref=CARD_REF_1)
    h5_card_img_2 = _node('h5:card_img_2', '266px', '168px', image_ref=CARD_REF_2)
    h5_card_container = _node('h5:card_container', '71.45%', '26.66%',
                               children=[h5_card_img_1, h5_card_img_2])

    # PC children: [pc_main]; H5 children: [h5_main (matched to pc_main), h5_card_container (unmatched)]
    base_children = [pc_main]
    supp_children = [h5_main, h5_card_container]

    result = _merge_children(base_children, supp_children, breakpoint=768)

    # h5_card_container must be present as h5Only
    h5_card_in_result = [n for n in result if 'card_container' in n.get('figmaId', '')]
    assert h5_card_in_result, (
        'U-464 FAIL: h5_card_container (H5-only card images) was incorrectly skipped by Fix D-sym. '
        'It should appear as h5Only in the merged result because its imageRefs are NOT in the '
        'matched H5 subtree — Fix D-sym must also require imageRefs ⊆ _matched_h5_image_refs.'
    )
    assert h5_card_in_result[0].get('h5Only'), (
        'U-464 FAIL: h5_card_container found but not marked h5Only'
    )
