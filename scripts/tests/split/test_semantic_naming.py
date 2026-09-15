"""Tests for semantic naming helpers: _find_first_text, _text_to_name, _infer_section_name."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from split_components import _find_first_text, _text_to_name, _infer_section_name, _deconflict_subsection_name
from lib.paths import SPLIT_A_SUBDIR
from helpers import check, print_summary, reset


# ── _find_first_text ──────────────────────────────────────────────────────────

class TestFindFirstText:
    def test_direct_text_node_by_figma_type(self):
        node = {'figmaType': 'TEXT', 'textContent': 'Hello World'}
        assert _find_first_text(node) == 'Hello World'

    def test_direct_text_node_by_is_text_node(self):
        node = {'isTextNode': True, 'textContent': 'Hello'}
        assert _find_first_text(node) == 'Hello'

    def test_text_node_uses_text_field_fallback(self):
        node = {'figmaType': 'TEXT', 'text': 'Fallback'}
        assert _find_first_text(node) == 'Fallback'

    def test_finds_child_text_within_depth(self):
        node = {
            'figmaType': 'FRAME',
            'children': [
                {'figmaType': 'TEXT', 'textContent': 'Child Text'}
            ]
        }
        assert _find_first_text(node) == 'Child Text'

    def test_depth_limit_respected(self):
        # text is at depth 3, max_depth=2 → should not find it
        node = {
            'figmaType': 'FRAME',
            'children': [{
                'figmaType': 'FRAME',
                'children': [{
                    'figmaType': 'FRAME',
                    'children': [
                        {'figmaType': 'TEXT', 'textContent': 'Deep'}
                    ]
                }]
            }]
        }
        assert _find_first_text(node, max_depth=2) is None

    def test_no_text_node_returns_none(self):
        node = {'figmaType': 'FRAME', 'children': []}
        assert _find_first_text(node) is None

    def test_returns_first_text_among_siblings(self):
        node = {
            'figmaType': 'FRAME',
            'children': [
                {'figmaType': 'RECT'},
                {'figmaType': 'TEXT', 'textContent': 'First'},
                {'figmaType': 'TEXT', 'textContent': 'Second'},
            ]
        }
        assert _find_first_text(node) == 'First'

    def test_empty_text_content_skipped(self):
        # empty string is falsy, should return None
        node = {'figmaType': 'TEXT', 'textContent': ''}
        assert _find_first_text(node) is None


# ── _text_to_name ─────────────────────────────────────────────────────────────

class TestTextToName:
    def test_single_english_word(self):
        assert _text_to_name('Hero') == 'Hero'

    def test_multiple_words_pascal_case(self):
        assert _text_to_name('learn more about us') == 'LearnMoreAbout'

    def test_respects_max_words(self):
        assert _text_to_name('one two three four five', max_words=2) == 'OneTwo'

    def test_chinese_text_returns_none(self):
        assert _text_to_name('这是中文文本') is None

    def test_mixed_chinese_english_extracts_english(self):
        result = _text_to_name('立即 Sign Up 注册')
        assert result == 'SignUp'

    def test_short_words_filtered(self):
        # single-char words should be excluded
        assert _text_to_name('a b c hello') == 'Hello'

    def test_empty_string_returns_none(self):
        assert _text_to_name('') is None

    def test_numbers_only_returns_none(self):
        assert _text_to_name('123 456') is None

    def test_preserves_capitalization_of_each_word(self):
        assert _text_to_name('GET STARTED NOW') == 'GetStartedNow'


# ── _infer_section_name ───────────────────────────────────────────────────────

class TestInferSectionName:
    def _make_node(self, figma_name='', children=None):
        node = {'figmaName': figma_name}
        if children is not None:
            node['children'] = children
        return node

    def test_level1_meaningful_figma_name(self):
        node = self._make_node('HeroArea')
        name = _infer_section_name(node, [], 0)
        assert 'Hero' in name
        assert name.endswith('Section')

    def test_level1_already_ends_with_section(self):
        node = self._make_node('HeroSection')
        name = _infer_section_name(node, [], 0)
        # should not double-append Section
        assert name.count('Section') == 1

    def test_level1_generic_name_falls_through(self):
        node = self._make_node('Frame1')
        # no text children, no leaves → fallback to Section{N}
        name = _infer_section_name(node, [], 0)
        assert name == 'Section1'

    def test_level2_extracts_text_from_child(self):
        node = self._make_node('Frame1', children=[
            {'figmaType': 'TEXT', 'textContent': 'Get Started'}
        ])
        name = _infer_section_name(node, [], 2)
        assert name == 'GetStartedSection'

    def test_level2_chinese_text_falls_through(self):
        node = self._make_node('Group1', children=[
            {'figmaType': 'TEXT', 'textContent': '开始使用'}
        ])
        leaves = [{'name': 'PriceCard'}]
        name = _infer_section_name(node, leaves, 0)
        assert name == 'PriceCardSection'

    def test_level3_leaf_component_name(self):
        node = self._make_node('Frame1')
        leaves = [{'name': 'FeatureCard'}]
        name = _infer_section_name(node, leaves, 0)
        assert name == 'FeatureCardSection'

    def test_fallback_section_n(self):
        node = self._make_node('Frame1')
        name = _infer_section_name(node, [], 4)
        assert name == 'Section5'

    def test_index_used_in_fallback(self):
        node = self._make_node('Frame1')
        assert _infer_section_name(node, [], 0) == 'Section1'
        assert _infer_section_name(node, [], 9) == 'Section10'


# ── _deconflict_subsection_name ───────────────────────────────────────────────

class TestDeconflictSubsectionName:
    def test_collision_with_section_suffix_replaced_by_inner(self):
        # Real data: subSection 'PickYourDeliverySection' collides with parent 'PickYourDeliverySection'
        # Both Frame 2147229830 (parent) and Frame 2147229827 (child) in BTC Pizza Day (235-14237)
        # contain "Pick Your Delivery" text → Level 2 generates identical names →
        # TypeScript: 'Identifier has already been declared'
        result = _deconflict_subsection_name('PickYourDeliverySection', 'PickYourDeliverySection')
        assert result == 'PickYourDeliveryInner'

    def test_no_collision_returns_unchanged(self):
        result = _deconflict_subsection_name('HeroSection', 'MainSection')
        assert result == 'HeroSection'

    def test_collision_without_section_suffix_appends_inner(self):
        result = _deconflict_subsection_name('Hero', 'Hero')
        assert result == 'HeroInner'

    def test_section_only_suffix_becomes_inner(self):
        # 'Section' with no prefix: unlikely but should not crash
        result = _deconflict_subsection_name('Section', 'Section')
        assert result == 'Inner'


# ── Task 2: Leaf Component Naming ──────────────────────────────────────────
from split_components import _infer_component_name


class TestInferComponentName:
    def test_meaningful_figma_name(self):
        node = {'figmaName': 'Task Card - 2/row', 'children': []}
        assert _infer_component_name(node) == 'TaskCard2Row'

    def test_generic_with_moly_component(self):
        node = {'figmaName': 'Frame 123', 'ccComponent': 'Button', 'children': []}
        assert _infer_component_name(node) == 'Button'

    def test_generic_with_text_child(self):
        node = {'figmaName': 'Frame 999', 'children': [
            {'figmaType': 'TEXT', 'isTextNode': True, 'textContent': 'Copy Link', 'children': []}
        ]}
        result = _infer_component_name(node)
        assert result == 'CopyLink'

    def test_generic_no_context_fallback(self):
        node = {'figmaName': 'Frame 777', 'children': []}
        result = _infer_component_name(node)
        # Frame777 → digits moved → "Frame777" since generic regex now matches "Frame 777" (with space) so it goes to text fallback → None → final fallback
        assert result and result[0].isalpha()

    def test_non_generic_digit_prefix(self):
        node = {'figmaName': '5_Type tab-Tag-Element', 'children': []}
        result = _infer_component_name(node)
        assert result[0].isalpha()  # Should not start with digit


# ── Task 3: Props Naming ───────────────────────────────────────────────────
from lib.instance_merger import extract_varying_props


class TestPropsNaming:
    def _make_instance(self, fid, text, figma_name='TextNode'):
        return {
            'figmaId': fid, 'figmaName': 'Card', 'figmaType': 'FRAME',
            'isComponentInstance': True, 'componentId': 'comp1',
            'bb': {'width': 200, 'height': 100}, 'css': {},
            'children': [{
                'figmaId': f'{fid}-t', 'figmaName': figma_name, 'figmaType': 'TEXT',
                'isTextNode': True, 'textContent': text,
                'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 100, 'height': 20}, 'css': {}, 'children': []
            }]
        }

    def test_prop_from_meaningful_figma_name(self):
        inst1 = self._make_instance('r1', 'Buy BTC', 'actionLabel')
        inst2 = self._make_instance('r2', 'Sell ETH', 'actionLabel')
        props = extract_varying_props([inst1, inst2])
        assert len(props) == 1
        assert props[0]['propName'] == 'actionLabel'

    def test_prop_from_text_when_generic_name(self):
        inst1 = self._make_instance('r1', 'Buy BTC', 'node-123')
        inst2 = self._make_instance('r2', 'Sell ETH', 'node-123')
        props = extract_varying_props([inst1, inst2])
        assert len(props) == 1
        # Should derive from text content: "Buy" + "BTC" → "buyBtc"
        assert props[0]['propName'] == 'buyBtc'

    def test_prop_fallback_when_chinese(self):
        inst1 = self._make_instance('r1', '行情洞察', 'node-456')
        inst2 = self._make_instance('r2', '快捷交易', 'node-456')
        props = extract_varying_props([inst1, inst2])
        assert len(props) == 1
        # Chinese text → no English words → fallback to text{N}
        assert props[0]['propName'] == 'text0'


# ── Task 4: i18n Token Enhancement ────────────────────────────────────────
from lib.split_codegen import _enhance_token_id


class TestEnhanceTokenId:
    def test_short_english_text(self):
        token = {'tokenId': 'button_text', 'default': 'copy', 'figmaId': '169:35551'}
        result = _enhance_token_id(token)
        assert result == 'copy'

    def test_multi_word_english(self):
        token = {'tokenId': 'button_text_xxx', 'default': 'Get Started', 'figmaId': ''}
        result = _enhance_token_id(token)
        assert result == 'get_started'

    def test_strip_figma_id_suffix(self):
        token = {'tokenId': 'button_text_i169_35115_2_8053', 'default': '极简配置即刻唤醒', 'figmaId': ''}
        result = _enhance_token_id(token)
        assert result == 'button_text'

    def test_long_text_no_change(self):
        token = {'tokenId': 'some_token', 'default': 'A very long text that exceeds five words and should not be renamed at all', 'figmaId': ''}
        result = _enhance_token_id(token)
        assert result is None

    def test_chinese_only_strips_figma_suffix(self):
        token = {'tokenId': 'node_169_35576', 'default': '行情洞察', 'figmaId': ''}
        result = _enhance_token_id(token)
        # No English words → Rule 1 fails. '_169_35576' matches Figma ID pattern → Rule 2 strips it.
        assert result == 'node'

    def test_same_as_current_returns_none(self):
        token = {'tokenId': 'copy', 'default': 'copy', 'figmaId': ''}
        result = _enhance_token_id(token)
        # enhanced == current → should return None (no change needed)
        assert result is None


# ── Task 6: plan.naming.json ──────────────────────────────────────────────
from split_components import _generate_naming_context


class TestGenerateNamingContext:
    def test_includes_generic_sections(self):
        plan = {
            'pageComponent': 'TestPage',
            'sections': [
                {
                    'name': 'Section1',
                    'ir': {'figmaName': 'Frame 123', 'children': [
                        {'figmaType': 'TEXT', 'isTextNode': True, 'textContent': 'Hello', 'children': []}
                    ]},
                    'leafComponents': [{'name': 'CardItem', 'ir': {}, 'varyingProps': []}]
                },
            ],
            'leafComponents': [],
        }
        ctx = _generate_naming_context(plan)
        assert len(ctx['sections']) == 1
        assert ctx['sections'][0]['currentName'] == 'Section1'
        assert 'Hello' in ctx['sections'][0]['firstTexts']
        assert ctx['sections'][0]['leafNames'] == ['CardItem']

    def test_subsection_rename_updates_subsection_name(self):
        """renames.json 的 subSections 字段应更新 plan.json 中 s['subSections'][i]['name']。

        Real data: VolDataSection 的 subSections Section1→MarketHeader 等，
        来自 page 10664-26578 重建时的 apply-renames 流程。
        """
        from pathlib import Path
        import tempfile, shutil, os
        tmp = Path(tempfile.mkdtemp())
        try:
            plan = {
                'pageComponent': 'TestPage',
                'nodeId': '10664-26578',
                'cssExt': 'less',
                'sections': [{
                    'name': 'VolDataSection',
                    'ir': {'figmaId': '10664:26609', 'figmaName': 'Frame 2147224833',
                            'figmaType': 'FRAME', 'css': {}, 'children': []},
                    'leafComponents': [],
                    'subSections': [
                        {'name': 'Section1', 'ir': {'figmaId': 'a1', 'figmaName': 'sub1',
                         'figmaType': 'FRAME', 'css': {}, 'children': []}, 'leafComponents': []},
                        {'name': 'Section2', 'ir': {'figmaId': 'a2', 'figmaName': 'sub2',
                         'figmaType': 'FRAME', 'css': {}, 'children': []}, 'leafComponents': []},
                    ],
                }],
                'leafComponents': [],
                'inlineNodes': [],
            }
            split_a = tmp / '.figma-to-code' / '3-page-code' / 'TestPage-10664-26578' / SPLIT_A_SUBDIR
            split_a.mkdir(parents=True)
            plan_path = split_a / 'plan.json'
            plan_path.write_text(__import__('json').dumps(plan))

            renames = {'subSections': {'Section1': 'MarketHeader', 'Section2': 'VolPanel1'}}
            renames_path = tmp / 'renames.json'
            renames_path.write_text(__import__('json').dumps(renames))

            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                from split_components import cmd_apply_renames
                cmd_apply_renames('10664-26578', str(renames_path))
            finally:
                os.chdir(old_cwd)

            updated = __import__('json').loads(plan_path.read_text())
            sub_names = [ss['name'] for ss in updated['sections'][0]['subSections']]
            assert 'MarketHeader' in sub_names, f"Expected MarketHeader in {sub_names}"
            assert 'VolPanel1' in sub_names, f"Expected VolPanel1 in {sub_names}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_page_rename_updates_page_component(self):
        """renames.json 的 page 字段应更新 plan.json 的 pageComponent，目录不重命名。

        Design: cmd_apply_renames 仅更新 plan.json，不重命名 3-page-code/ 目录。
        3-page-code/ 目录保持 Figma 原始帧名，由 --apply 在落地时使用 pageComponent。
        Real data: pageComponent='Frame2147224865' from page 10664-26578.
        """
        import tempfile, shutil, os
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        try:
            plan = {
                'pageComponent': 'Frame2147224865',
                'nodeId': '10664-26578',
                'cssExt': 'less',
                'sections': [],
                'leafComponents': [],
                'inlineNodes': [],
            }
            split_a = tmp / '.figma-to-code' / '3-page-code' / 'Frame2147224865-10664-26578' / SPLIT_A_SUBDIR
            split_a.mkdir(parents=True)
            plan_path = split_a / 'plan.json'
            plan_path.write_text(__import__('json').dumps(plan))

            renames = {'page': 'OptionExperience2026'}
            renames_path = tmp / 'renames.json'
            renames_path.write_text(__import__('json').dumps(renames))

            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                from split_components import cmd_apply_renames
                cmd_apply_renames('10664-26578', str(renames_path))
            finally:
                os.chdir(old_cwd)

            # plan.json 仍在原始帧名目录下（目录未重命名）
            updated = __import__('json').loads(plan_path.read_text())
            assert updated['pageComponent'] == 'OptionExperience2026', \
                f"Expected 'OptionExperience2026', got '{updated['pageComponent']}'"
            # 原始帧名目录仍存在
            assert split_a.exists(), '3-page-code 原始帧名目录应仍存在'
            # 不产生新名称目录
            new_dir = tmp / '.figma-to-code' / '3-page-code' / 'OptionExperience2026-10664-26578'
            assert not new_dir.exists(), '3-page-code 不应产生新名称目录'
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_includes_frame_number_sections(self):
        """Frame{N}Section (leaf-derived generic name) must be included for AI rename.

        Bug: _GENERIC_SECTION_RE was r'^Section\d+$' and did not match Frame2147223812Section.
        Fix: pattern extended to also match Frame/Group/Rectangle{N}Section.
        Real data: section 'Frame2147223812Section' from page 10664-26578 (node 10664-26578).
        """
        plan = {
            'pageComponent': 'TestPage',
            'sections': [
                {
                    'name': 'Frame2147223812Section',
                    'ir': {'figmaName': 'Frame 2147224833', 'children': []},
                    'leafComponents': []
                },
            ],
            'leafComponents': [],
        }
        ctx = _generate_naming_context(plan)
        assert len(ctx['sections']) == 1
        assert ctx['sections'][0]['currentName'] == 'Frame2147223812Section'

    def test_excludes_semantic_sections(self):
        plan = {
            'pageComponent': 'TestPage',
            'sections': [
                {'name': 'HeroSection', 'ir': {'figmaName': 'Hero', 'children': []}, 'leafComponents': []}
            ],
            'leafComponents': [],
        }
        ctx = _generate_naming_context(plan)
        assert len(ctx['sections']) == 0

    def test_includes_generic_leaves(self):
        plan = {
            'pageComponent': 'TestPage',
            'sections': [
                {
                    'name': 'Section1',
                    'ir': {'figmaName': 'F', 'children': []},
                    'leafComponents': [
                        {'name': 'Frame123', 'ir': {'figmaName': 'Frame 123', 'children': []}, 'varyingProps': []},
                        {'name': 'TaskCard', 'ir': {'figmaName': 'Task Card', 'children': []}, 'varyingProps': []},
                    ]
                }
            ],
            'leafComponents': [],
        }
        ctx = _generate_naming_context(plan)
        assert len(ctx['leaves']) == 1
        assert ctx['leaves'][0]['currentName'] == 'Frame123'

    def test_includes_generic_props(self):
        plan = {
            'pageComponent': 'TestPage',
            'sections': [
                {
                    'name': 'S1',
                    'ir': {'figmaName': 'F', 'children': []},
                    'leafComponents': [
                        {
                            'name': 'Card',
                            'ir': {'figmaName': 'Card', 'children': []},
                            'varyingProps': [
                                {'propName': 'text0', 'values': ['Hello', 'World']},
                                {'propName': 'title', 'values': ['A', 'B']},
                            ]
                        }
                    ]
                }
            ],
            'leafComponents': [],
        }
        ctx = _generate_naming_context(plan)
        assert len(ctx['props']) == 1
        assert ctx['props'][0]['currentName'] == 'text0'

    def test_subsections_with_generic_names_in_naming_context(self):
        """U-286: subSections 有通用名（Section{N}）时，应出现在 naming context 的 subSections 字段。

        Real data: VolDataSection 内 Frame 2147224775/2147224806/2147224832/2147224831
        在 page 2 (10664-26577) 中被命名为 Section1-4——AI rename pass 看不到它们，
        永远留在通用名。修复后，plan.naming.json 应包含 subSections 供 AI 命名。
        """
        plan = {
            'pageComponent': 'TestPage',
            'sections': [
                {
                    'name': 'VolDataSection',
                    'ir': {'figmaName': 'Frame 2147224833', 'children': []},
                    'leafComponents': [],
                    'subSections': [
                        {'name': 'Section1',
                         'ir': {'figmaName': 'Frame 2147224775',
                                'figmaId': '10664:26610',
                                'children': [{'figmaType': 'TEXT', 'textContent': 'Vol Tab', 'children': []}]}},
                        {'name': 'Section2',
                         'ir': {'figmaName': 'Frame 2147224806', 'figmaId': '10664:26660', 'children': []}},
                        # 语义化名不应出现
                        {'name': 'VolDataPanel',
                         'ir': {'figmaName': 'Frame 2147224806B', 'figmaId': '10664:26661', 'children': []}},
                    ],
                },
            ],
            'leafComponents': [],
        }
        ctx = _generate_naming_context(plan)
        ss = ctx.get('subSections', [])
        # 通用名 Section1/Section2 应在 subSections
        ss_names = [x['currentName'] for x in ss]
        assert 'Section1' in ss_names, f'Section1 should be in subSections, got {ss_names}'
        assert 'Section2' in ss_names, f'Section2 should be in subSections, got {ss_names}'
        # 语义化名 VolDataPanel 不应出现
        assert 'VolDataPanel' not in ss_names, f'Semantic name should not be in subSections'
        # 每条记录应有 sectionName（父 section 名）
        for entry in ss:
            if entry['currentName'] in ('Section1', 'Section2'):
                assert entry.get('sectionName') == 'VolDataSection'

    def test_empty_when_all_semantic(self):
        plan = {
            'pageComponent': 'TestPage',
            'sections': [{'name': 'HeroSection', 'ir': {'figmaName': 'Hero', 'children': []},
                         'leafComponents': [{'name': 'ActionButton', 'ir': {}, 'varyingProps': [
                             {'propName': 'label', 'values': ['OK']}
                         ]}]}],
            'leafComponents': [],
        }
        ctx = _generate_naming_context(plan)
        assert len(ctx['sections']) == 0
        assert len(ctx['leaves']) == 0
        assert len(ctx['props']) == 0


# ── Runner ───────────────────────────────────────────────────────────────────

def _run_class(cls):
    t = cls()
    for name in sorted(m for m in dir(t) if m.startswith('test_')):
        try:
            getattr(t, name)()
            check(name, f'{cls.__name__}.{name}', True)
        except AssertionError:
            check(name, f'{cls.__name__}.{name}', False)
        except Exception as e:
            check(name, f'{cls.__name__}.{name} EXCEPTION: {e}', False)


if __name__ == '__main__':
    reset()
    print('\n── split semantic naming tests ──')
    for cls in [TestFindFirstText, TestTextToName, TestInferSectionName,
                TestDeconflictSubsectionName, TestPropsNaming, TestGenerateNamingContext]:
        _run_class(cls)
    print_summary('split/test_semantic_naming')
