"""tests for structuralSplit / supplementText / h5SectionOrder code generation"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.paths import INDEX_TSX_FILENAME


def _make_split_section_ir(name: str) -> dict:
    """IR node with structuralSplit=True and supplementNode"""
    pc_child = {'figmaId': '1:2', 'figmaName': 'Card', 'figmaType': 'FRAME',
                'visible': True, 'children': [], 'css': {'width': '300px'},
                'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 300, 'height': 200},
                'fills': [], 'semantic': {'className': 'card'}}
    h5_child = {'figmaId': '2:2', 'figmaName': 'Card', 'figmaType': 'FRAME',
                'visible': True, 'children': [], 'css': {'width': '100%'},
                'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 300},
                'fills': [], 'semantic': {'className': 'card'}}
    node = {
        'figmaId': '1:1', 'figmaName': name, 'figmaType': 'FRAME',
        'visible': True, 'children': [pc_child],
        'css': {'display': 'flex', 'flex-direction': 'row'},
        'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 1440, 'height': 400},
        'fills': [], 'semantic': {'className': name.lower()},
        'structuralSplit': True,
        'supplementNode': {
            'figmaId': '2:1', 'figmaName': name, 'figmaType': 'FRAME',
            'visible': True, 'children': [h5_child],
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 600},
            'fills': [], 'semantic': {'className': name.lower()},
        },
    }
    return node


def test_structural_split_section_generates_three_files(tmp_path):
    """structuralSplit section → SectionPC.tsx + SectionH5.tsx + index.tsx"""
    from lib.split_codegen import _generate_structural_split_section
    ir = _make_split_section_ir('EarnSection')
    _generate_structural_split_section('EarnSection', ir, tmp_path, css_ext='less')
    assert (tmp_path / 'EarnSectionPC.tsx').exists()
    assert (tmp_path / 'EarnSectionH5.tsx').exists()
    assert (tmp_path / INDEX_TSX_FILENAME).exists()


def test_structural_split_index_uses_is_mobile(tmp_path):
    from lib.split_codegen import _generate_structural_split_section
    ir = _make_split_section_ir('HeroSection')
    _generate_structural_split_section('HeroSection', ir, tmp_path, css_ext='less')
    content = (tmp_path / INDEX_TSX_FILENAME).read_text()
    assert 'useMobileSize' in content
    assert 'HeroSectionPC' in content
    assert 'HeroSectionH5' in content
    assert 'isMobile' in content


def test_supplement_text_generates_conditional(tmp_path):
    """supplementText node → isMobile ? H5text : PCtext"""
    from lib.split_codegen import _render_supplement_text_node
    node = {
        'figmaId': '1:1', 'figmaName': 'CTA', 'figmaType': 'TEXT',
        'visible': True, 'isTextNode': True, 'textContent': 'Sign up for ByEU',
        'supplementText': 'Sign up',
        'children': [], 'css': {'font-size': '16px'}, 'fills': [],
        'semantic': {'className': 'cta-text'},
    }
    jsx = _render_supplement_text_node(node, css_ext='less')
    assert 'isMobile' in jsx
    assert 'Sign up for ByEU' in jsx
    assert 'Sign up' in jsx
    assert 'useMobileSize' in jsx or 'isMobile' in jsx


def test_h5_section_order_dual_branch_in_page_tsx(tmp_path):
    """h5SectionOrder τ距离>0.3 → Page.tsx 含 isMobile 双分支"""
    from lib.split_codegen import _generate_page_tsx_with_order
    # PC order: [A, B, C], H5 order: [C, A, B] — significant reorder
    body_items = [
        {'y': 0,   'type': 'section', 'name': 'SectionA', 'h5_only': False, 'pc_only': False},
        {'y': 300, 'type': 'section', 'name': 'SectionB', 'h5_only': False, 'pc_only': False},
        {'y': 600, 'type': 'section', 'name': 'SectionC', 'h5_only': False, 'pc_only': False},
    ]
    h5_order = ['SectionC', 'SectionA', 'SectionB']
    tsx = _generate_page_tsx_with_order('Page', body_items, 'page', 'less', h5_order=h5_order)
    assert 'isMobile' in tsx
    # Both orderings should appear
    a_pos_pc = tsx.find('<SectionA')
    b_pos_pc = tsx.find('<SectionB')
    assert a_pos_pc < b_pos_pc  # PC order preserved somewhere


def test_collect_inline_responsive_flags_pconly():
    """_collect_inline_responsive_flags: pcOnly node → attr:[figmaId] selector.
    Uses attr:[figmaId] instead of class selector to work across CSS module boundaries.
    Leaf components (Trade, SignUp, etc.) live in their own CSS modules — section-level
    class rules don't affect them. [data-figma-id="..."] selectors are global and work
    regardless of which CSS module defines the class.
    Real data: 250:3768 (Calculate Mode, pcOnly=True) in CalculateYourCashbackSection.
    """
    from lib.split_codegen import _collect_inline_responsive_flags
    ir_node = {
        'figmaId': '250:3768', 'figmaName': 'Calculate Mode',
        'pcOnly': True, 'children': [],
    }
    result = _collect_inline_responsive_flags(ir_node, {'250:3768': 'calculate-mode'}, set())
    assert result == {'attr:250:3768': 'pcOnly'}, (
        f'pcOnly node should use attr:[figmaId] selector for cross-module safety. Got: {result}'
    )


def test_collect_inline_responsive_flags_h5only():
    """_collect_inline_responsive_flags: h5Only node → attr:[figmaId] selector.
    Uses attr:[figmaId] instead of class selector for cross-module compatibility.
    Real data: 250:4862 (bg-n250-4862, h5Only=True) — H5 calculator background.
    """
    from lib.split_codegen import _collect_inline_responsive_flags
    ir_node = {
        'figmaId': '250:4862', 'figmaName': 'bg',
        'h5Only': True, 'children': [],
    }
    result = _collect_inline_responsive_flags(ir_node, {'250:4862': 'bg-n250-4862'}, set())
    assert result == {'attr:250:4862': 'h5Only'}, (
        f'h5Only node should use attr:[figmaId] selector. Got: {result}'
    )


def test_collect_inline_responsive_flags_skips_subsections():
    """_collect_inline_responsive_flags: nodes in skip_fids are excluded.
    subSection nodes (like 250:4576 RewardsTiersSection) are already wrapped by
    _subsection_responsive, so they must be excluded from inline responsive collection.
    """
    from lib.split_codegen import _collect_inline_responsive_flags
    ir_node = {
        'figmaId': '250:4576', 'figmaName': 'Rewards tiers',
        'h5Only': True, 'children': [],
    }
    result = _collect_inline_responsive_flags(
        ir_node, {'250:4576': 'rewards-tiers-section'}, {'250:4576'}
    )
    assert result == {}, (
        f'subSection node should be skipped (in skip_fids). Got: {result}'
    )


def test_find_same_name_pc_for_h5only_subsections():
    """_find_same_name_pc_for_h5only_subsections: when a PC subSection shares a name with
    an h5Only subSection, the PC one should be marked for pcOnly wrapping.

    Real case: Component10Section (250:3796 EU deposit campaign) has two 'FaqSection'
    subSections — one PC (I250:3796;14668:69867, pc=None) and one H5 (I250:4920;14668:58717,
    h5Only=True). The h5Only_count(1) < paired_count(2) rule in _merge_children doesn't
    fire because TermsConditionsSection is also paired. This helper detects the same-name
    pattern and marks the PC FaqSection for pcOnly wrapping.
    """
    from lib.split_codegen import _find_same_name_pc_for_h5only_subsections
    subsections = [
        {'name': 'FaqSection', 'ir': {'figmaId': 'I250:3796;14668:69867', 'h5Only': None, 'pcOnly': None}},
        {'name': 'TermsConditionsSection', 'ir': {'figmaId': 'I250:3796;14668:69946', 'h5Only': None, 'pcOnly': None}},
        {'name': 'FaqSection', 'ir': {'figmaId': 'I250:4920;14668:58717', 'h5Only': True, 'pcOnly': None}},
    ]
    result = _find_same_name_pc_for_h5only_subsections(subsections)
    assert 'FaqSection' in result, (
        f'PC FaqSection should be detected as needing pcOnly (same name as h5Only sibling). Got: {result}'
    )
    assert 'TermsConditionsSection' not in result, (
        f'TermsConditionsSection has no h5Only namesake, should not be in result. Got: {result}'
    )


def test_collect_inline_responsive_flags_recursive():
    """_collect_inline_responsive_flags: recursively collects pcOnly/h5Only from children
    using attr:[figmaId] selectors for cross-module compatibility.
    """
    from lib.split_codegen import _collect_inline_responsive_flags
    ir = {
        'figmaId': 'root:1', 'figmaName': 'Section',
        'children': [
            {'figmaId': 'c:1', 'figmaName': 'Calculate Mode', 'pcOnly': True, 'children': []},
            {'figmaId': 'c:2', 'figmaName': 'H5 Panel', 'h5Only': True, 'children': []},
        ],
    }
    fid_cls = {'c:1': 'calculate-mode', 'c:2': 'h5-panel'}
    result = _collect_inline_responsive_flags(ir, fid_cls, set())
    assert result == {'attr:c:1': 'pcOnly', 'attr:c:2': 'h5Only'}, (
        f'Both pcOnly and h5Only children should use attr: selectors. Got: {result}'
    )


def test_collect_responsive_overrides_from_ir():
    """_collect_responsive_overrides_from_ir: nodes with responsive[] should generate
    @media blocks in the section CSS. Real data: EU deposit hero-content-wrapper (250:2623)
    has responsive=[{breakpoint: 768, css: {margin-left: 0px, margin-top: 0px}}]
    after css_diff fix. Without collecting these, the PC canvas margins remain on mobile.
    """
    from lib.split_codegen import _collect_responsive_overrides_from_ir
    ir = {
        'figmaId': '250:2623', 'figmaName': 'hero-content-wrapper',
        'css': {'margin-left': '120px', 'display': 'flex'},
        'responsive': [{'breakpoint': 768, 'css': {'margin-left': '0px', 'margin-top': '0px'}}],
        'children': [],
    }
    fid_cls = {'250:2623': 'hero-content-wrapper'}
    result = _collect_responsive_overrides_from_ir(ir, fid_cls, set())
    assert len(result) == 1
    cls, bp, css = result[0]
    assert cls == 'hero-content-wrapper'
    assert bp == 768
    assert css.get('margin-left') == '0px'
    assert css.get('margin-top') == '0px'


def test_collect_inline_responsive_flags_paired_pc_h5_same_class():
    """When a CSS class is shared by BOTH a pcOnly and h5Only node (platform pair),
    generate TWO attr:[figmaId] entries instead of dropping both as a conflict.

    Real case: OriginalCopySection task-hover-bg-556.
    250:2665 (pcOnly=True) and 250:3881 (h5Only=True) share the same class.
    Without fix: conflict sets flag=None → both dropped → pcOnly node stays visible
    on mobile → 556px-wide background causes horizontal scrollbar.
    """
    from lib.split_codegen import _collect_inline_responsive_flags
    ir = {
        'figmaId': 'root:1', 'figmaName': 'Section',
        'children': [
            {'figmaId': '250:2665', 'figmaName': 'TaskHoverBg', 'pcOnly': True, 'h5Only': None, 'children': []},
            {'figmaId': '250:3881', 'figmaName': 'TaskHoverBg', 'pcOnly': None, 'h5Only': True, 'children': []},
        ],
    }
    fid_cls = {'250:2665': 'task-hover-bg-556', '250:3881': 'task-hover-bg-556'}
    result = _collect_inline_responsive_flags(ir, fid_cls, set())
    assert 'attr:250:2665' in result, (
        f'pcOnly node should get attr:[figmaId] selector. Got: {result}'
    )
    assert result['attr:250:2665'] == 'pcOnly', (
        f'250:2665 should be pcOnly. Got: {result}'
    )
    assert 'attr:250:3881' in result, (
        f'h5Only node should get attr:[figmaId] selector. Got: {result}'
    )
    assert result['attr:250:3881'] == 'h5Only', (
        f'250:3881 should be h5Only. Got: {result}'
    )


def test_find_same_name_pc_skips_if_pc_has_h5only_children():
    """_find_same_name_pc_for_h5only_subsections should NOT mark a PC subSection as
    pcOnly if the PC subSection's IR contains h5Only descendant nodes.

    Real case: Component10Section PC FaqSection (I250:3796;14668:69867) has h5Only
    children (I250:4920;14668:58720, I250:4920;14668:58733 — H5 FAQ expandable items).
    The H5 FaqSection header (I250:4920;14668:58717, h5Only=True) is the same-name
    trigger. Without this guard, wrapping PC FaqSection in pc-faq-section (display:none
    on mobile) also hides the h5Only FAQ items inside it — FAQ content disappears.
    """
    from lib.split_codegen import _find_same_name_pc_for_h5only_subsections

    # PC FaqSection contains h5Only children (H5 FAQ expandable items)
    # Real data: I250:3796;14668:69867 from EU deposit Component10Section
    pc_faq_ir = {
        'figmaId': 'I250:3796;14668:69867',
        'h5Only': None, 'pcOnly': None,
        'children': [
            {
                'figmaId': 'I250:3796;14668:69870', 'figmaName': 'FAQ (Expandable)',
                'pcOnly': True, 'h5Only': None, 'children': [],
            },
            {
                'figmaId': 'I250:4920;14668:58720',
                'figmaName': '.component_breakdown (expandable only)',
                'pcOnly': None, 'h5Only': True, 'children': [],
            },
        ],
    }
    subsections = [
        {'name': 'FaqSection', 'ir': pc_faq_ir},
        {'name': 'TermsConditionsSection',
         'ir': {'figmaId': 'I250:3796;14668:69946', 'h5Only': None, 'pcOnly': None, 'children': []}},
        {'name': 'FaqSection',
         'ir': {'figmaId': 'I250:4920;14668:58717', 'h5Only': True, 'pcOnly': None, 'children': []}},
    ]
    result = _find_same_name_pc_for_h5only_subsections(subsections)
    assert 'FaqSection' not in result, (
        f'PC FaqSection has h5Only children — must NOT be marked pcOnly (would hide them). '
        f'Got: {result}'
    )


def test_subsection_inline_faq_pattern_pconly_and_h5only_flagged():
    """_collect_inline_responsive_flags correctly processes FaqSection-like subSection IR.

    Real case: EU deposit campaign FaqSection (I250:3796;14668:69867) is a subSection of
    Component10Section. Its children include:
      - I250:3796;14668:69868 (pcOnly=True) → 'frame-1410118813' (renamed to 'faq-content')
      - I250:3796;14668:69870 (pcOnly=True) → 'faq-expandable'
      - I250:4920;14668:58720 (h5Only=True) → 'component-h5-expandable'
      - I250:4920;14668:58733 (h5Only=True) → 'component-h5-expandable' (same class)
      - I250:4920;14668:58746 (h5Only=True) → 'view_more_dark' (also used by PC non-flagged node)

    Bug: generate_components only calls _collect_inline_responsive_flags for top-level
    sections (lines 2084-2103). The subSection loop (lines 1772-1872) never calls it.
    Result: FaqSection CSS has NO responsive display rules — PC FAQ header and expandable
    items show on mobile; H5 expandable items show on desktop. Both platforms broken.

    Fix: after generating ss_css (line 1860-1864), add the same inline responsive CSS
    collection/append logic as top-level sections.
    """
    from lib.split_codegen import _collect_inline_responsive_flags

    ss_ir_raw = {
        'figmaId': 'I250:3796;14668:69867', 'figmaName': 'FaqSection',
        'children': [
            {'figmaId': 'I250:3796;14668:69868', 'figmaName': 'PC FAQ header',
             'pcOnly': True, 'h5Only': None, 'children': []},
            {'figmaId': 'I250:3796;14668:69870', 'figmaName': 'FAQ (Expandable)',
             'pcOnly': True, 'h5Only': None, 'children': [
                 {'figmaId': 'I250:3796;14668:69936', 'figmaName': 'view_more_dark',
                  'pcOnly': None, 'h5Only': None, 'children': []},
             ]},
            {'figmaId': 'I250:4920;14668:58720', 'figmaName': 'H5 expandable 1',
             'h5Only': True, 'pcOnly': None, 'children': []},
            {'figmaId': 'I250:4920;14668:58733', 'figmaName': 'H5 expandable 2',
             'h5Only': True, 'pcOnly': None, 'children': []},
            {'figmaId': 'I250:4920;14668:58746', 'figmaName': 'H5 View more',
             'h5Only': True, 'pcOnly': None, 'children': []},
        ],
    }
    fid_to_class = {
        'I250:3796;14668:69868': 'frame-1410118813',
        'I250:3796;14668:69870': 'faq-expandable',
        'I250:3796;14668:69936': 'view_more_dark',
        'I250:4920;14668:58720': 'component-h5-expandable',
        'I250:4920;14668:58733': 'component-h5-expandable',
        'I250:4920;14668:58746': 'view_more_dark',
    }

    result = _collect_inline_responsive_flags(ss_ir_raw, fid_to_class, set())

    # Unique-class pcOnly nodes use attr:[figmaId] selector (cross-module safe)
    assert 'attr:I250:3796;14668:69868' in result, (
        f'PC FAQ header should use attr: selector (cross-module). Got: {result}'
    )
    assert result['attr:I250:3796;14668:69868'] == 'pcOnly'

    assert 'attr:I250:3796;14668:69870' in result, (
        f'faq-expandable should use attr: selector. Got: {result}'
    )
    assert result['attr:I250:3796;14668:69870'] == 'pcOnly'

    # 58720 and 58733 share 'component-h5-expandable' and are both h5Only
    # count=2, all h5Only → class selector (after fix #2) OR attr: selectors (current)
    has_class_rule = 'component-h5-expandable' in result and result['component-h5-expandable'] == 'h5Only'
    has_attr_rules = (
        'attr:I250:4920;14668:58720' in result and 'attr:I250:4920;14668:58733' in result
    )
    assert has_class_rule or has_attr_rules, (
        f'Both H5 expandable items need responsive rules (either class or attr). Got: {result}'
    )

    # 58746 uses view_more_dark which is also used by non-flagged PC node → attr: selector
    assert 'attr:I250:4920;14668:58746' in result, (
        f'H5 view_more_dark should use attr: selector (shared with unflagged PC node). Got: {result}'
    )


def test_collect_inline_responsive_flags_all_same_flag_same_class_uses_attr_selectors():
    """When ALL occurrences of a CSS class share the same flag (all h5Only or all pcOnly),
    emit individual attr:[figmaId] selectors for EACH node (not a class selector).

    CSS module scoping: class selectors in section CSS don't apply to leaf component
    elements (different module hash). attr:[figmaId] selectors work globally.

    Real case: EarnFromSection Trade nodes 250:2637 and 250:2644 both map to class
    'trade' and both are pcOnly. Using class selector '.trade { display:none }' in
    index.module.less doesn't hide the Trade leaf component because Trade.module.less
    compiles '.trade' to a different hash.

    Fix: always use attr:[figmaId] selectors, even when all uses share the same flag.
    Both 250:2637 and 250:2644 must each get their own [data-figma-id] rule.
    """
    from lib.split_codegen import _collect_inline_responsive_flags

    ir = {
        'figmaId': 'root:1', 'figmaName': 'Section',
        'children': [
            {'figmaId': 'h5:1', 'h5Only': True, 'pcOnly': None, 'children': []},
            {'figmaId': 'h5:2', 'h5Only': True, 'pcOnly': None, 'children': []},
        ],
    }
    fid_cls = {'h5:1': 'h5-item', 'h5:2': 'h5-item'}

    result = _collect_inline_responsive_flags(ir, fid_cls, set())

    assert 'attr:h5:1' in result and 'attr:h5:2' in result, (
        f'Both h5Only nodes must get individual attr: selectors. Got: {result}'
    )
    assert result['attr:h5:1'] == 'h5Only' and result['attr:h5:2'] == 'h5Only', (
        f'Both must be h5Only. Got: {result}'
    )
    assert 'h5-item' not in result, (
        f'Class selector must NOT be used (CSS module boundary issue). Got: {result}'
    )


def test_collect_inline_responsive_flags_partial_flagged_uses_attr_selectors():
    """When only SOME occurrences of a class are flagged (others unflagged),
    emit per-node [data-figma-id] selectors for the flagged nodes only.

    Real case: view_more_dark is used by H5 view-more button (h5Only=True) AND
    by an unflagged PC node inside the pcOnly subtree. Since not all uses are flagged,
    a class selector would incorrectly affect the unflagged uses.
    """
    from lib.split_codegen import _collect_inline_responsive_flags

    ir = {
        'figmaId': 'root:1', 'figmaName': 'Section',
        'children': [
            {'figmaId': 'h5:1', 'h5Only': True, 'pcOnly': None, 'children': []},
            {'figmaId': 'pc:1', 'h5Only': None, 'pcOnly': None, 'children': []},
        ],
    }
    fid_cls = {'h5:1': 'shared-item', 'pc:1': 'shared-item'}

    result = _collect_inline_responsive_flags(ir, fid_cls, set())

    assert 'attr:h5:1' in result, f'Flagged node should get attr: selector. Got: {result}'
    assert result['attr:h5:1'] == 'h5Only', f'Expected h5Only for h5:1. Got: {result}'
    assert 'shared-item' not in result, (
        f'Class selector must NOT be used when unflagged uses exist. Got: {result}'
    )


def test_disambiguate_h5only_subsection_name_when_pc_sibling_exists():
    """_disambiguate_h5only_subsection_names returns a mapping of h5Only subsection
    names → disambiguated names (appended 'H5') when a same-name PC sibling exists.

    Real case: Component10Section has two FaqSection subsections:
      - FaqSection (PC, h5Only=None): the main FAQ with expandable items
      - FaqSection (H5, h5Only=True): just the H5 FAQ header title

    Both are rendered as <FaqSection> → the h5-faq-section wrapper duplicates H5 content.
    Fix: rename the h5Only one to 'FaqSectionH5', generate a SEPARATE component file,
    so the wrapper renders <FaqSectionH5> (just the header) instead of <FaqSection>.

    Without this fix, on mobile:
      - h5-faq-section shows <FaqSection> with H5 items (one copy)
      - main <FaqSection> also shows H5 items (second copy) → duplicate!
    """
    from lib.split_codegen import _disambiguate_h5only_subsection_names

    subsections = [
        {'name': 'FaqSection',
         'ir': {'figmaId': 'I250:4920;14668:58717', 'h5Only': True, 'pcOnly': None}},
        {'name': 'FaqSection',
         'ir': {'figmaId': 'I250:3796;14668:69867', 'h5Only': None, 'pcOnly': None}},
        {'name': 'TermsConditionsSection',
         'ir': {'figmaId': 'I250:3796;14668:69946', 'h5Only': None, 'pcOnly': None}},
    ]

    result = _disambiguate_h5only_subsection_names(subsections)

    assert result.get('I250:4920;14668:58717') == 'FaqSectionH5', (
        f'h5Only FaqSection should be renamed to FaqSectionH5. Got: {result}'
    )
    assert 'I250:3796;14668:69867' not in result, (
        f'PC FaqSection should NOT be renamed. Got: {result}'
    )
    assert 'I250:3796;14668:69946' not in result, (
        f'TermsConditionsSection has no same-name h5Only sibling. Got: {result}'
    )


def test_structural_split_section_hooks_rel_path_depth2(tmp_path):
    """U-439: structuralSplit H5/PC files must import usePageEnv with ../../ (depth-2).

    Real data: node 202:33464 (模拟盘落地页, StartDemoTradingSection)
    Bug: _generate_structural_split_section called generate_tsx without hooks_rel_path,
    using tsx_generator default '../hooks/usePageEnv' (depth-1), which is wrong for
    files living at components/{SectionName}/ (depth-2 from page root).
    """
    from lib.split_codegen import _generate_structural_split_section
    ir = _make_split_section_ir('StartDemoTradingSection')
    _generate_structural_split_section('StartDemoTradingSection', ir, tmp_path, css_ext='scss')

    for fname in ('StartDemoTradingSectionPC.tsx', 'StartDemoTradingSectionH5.tsx'):
        content = (tmp_path / fname).read_text()
        assert '../../hooks/usePageEnv' in content, (
            f'{fname}: expected ../../hooks/usePageEnv, got:\n'
            + '\n'.join(l for l in content.split('\n') if 'usePageEnv' in l)
        )
        assert '../hooks/usePageEnv' not in content.replace('../../hooks/usePageEnv', ''), (
            f'{fname}: still contains wrong ../hooks/usePageEnv path'
        )


def test_structural_split_index_uses_default_imports(tmp_path):
    """U-440: structuralSplit index.tsx must use default imports for H5/PC files.

    Real data: node 202:33464 (模拟盘落地页, HeaderSection / StartDemoTradingSection)
    Bug: index.tsx template used named imports { HeaderSectionH5 } but the H5/PC
    files generated by generate_tsx only have 'export default function X()', causing
    SyntaxError: module does not provide an export named 'X'.
    """
    from lib.split_codegen import _generate_structural_split_section
    ir = _make_split_section_ir('HeaderSection')
    _generate_structural_split_section('HeaderSection', ir, tmp_path, css_ext='scss')

    content = (tmp_path / 'index.tsx').read_text()
    # must use default imports
    assert "import HeaderSectionPC from './HeaderSectionPC'" in content, (
        f"index.tsx must use default import for HeaderSectionPC, got:\n"
        + '\n'.join(l for l in content.split('\n') if 'HeaderSectionPC' in l)
    )
    assert "import HeaderSectionH5 from './HeaderSectionH5'" in content, (
        f"index.tsx must use default import for HeaderSectionH5, got:\n"
        + '\n'.join(l for l in content.split('\n') if 'HeaderSectionH5' in l)
    )
    # must NOT use named imports
    assert "import { HeaderSectionPC }" not in content
    assert "import { HeaderSectionH5 }" not in content
