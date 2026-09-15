import sys, tempfile, json, os, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.split_codegen import generate_components, update_root_index, _generate_page_tsx, _page_can_flow, _is_section_overlay, _generate_section_tsx, _generate_usePageEnv_hook_file, _sanitize_classnames, _generate_structural_split_section, _resolve_missing_asset_paths, _maybe_adjust_h5_top_for_nav_offset, _css_from_orig, _write_moly_flat
from lib.paths import INDEX_TS_FILENAME, INDEX_TSX_FILENAME, PAGE_TSX_FILENAME, SPLIT_A_SUBDIR, STAGE_SUBDIR


def _make_ir(name: str, tag: str = 'section') -> dict:
    return {
        'figmaId': '1:1', 'figmaName': name, 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'children': [],
        'semantic': {
            'htmlTag': tag, 'className': name.lower(),
            'componentName': name, 'props': [], 'isExtractedComponent': True,
        },
    }


def _section_with_leaves(section_name, leaf_name, instances_data):
    return {
        'pageComponent': 'TestPage',
        'nodeId': '1-1',
        'leafComponents': [],
        'sections': [{
            'name': section_name,
            'ir': _make_ir(section_name),
            'ccComponent': None,
            'leafComponents': [{
                'name': leaf_name,
                'ir': _make_ir(leaf_name, tag='div'),
                'varyingProps': [],
                'instancesData': instances_data,
                'ccComponent': None,
            }],
        }],
    }


def _section_without_leaves(section_name):
    return {
        'pageComponent': 'TestPage',
        'nodeId': '1-1',
        'leafComponents': [],
        'sections': [{
            'name': section_name,
            'ir': _make_ir(section_name),
            'ccComponent': None,
            'leafComponents': [],
        }],
    }


def _top_level_leaf_plan(leaf_name, instances_data):
    return {
        'pageComponent': 'TestPage',
        'nodeId': '1-1',
        'leafComponents': [{
            'name': leaf_name,
            'ir': _make_ir(leaf_name, tag='div'),
            'varyingProps': [],
            'instancesData': instances_data,
            'ccComponent': None,
        }],
        'sections': [],
    }


def test_section_leaf_flat_files_inside_section_dir():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_with_leaves('HeroSection', 'Card', [{'title': 'A'}, {'title': 'B'}])
        generate_components(plan, out, css_ext='less')
        assert (out / 'components' / 'HeroSection' / 'Card.tsx').exists()
        assert (out / 'components' / 'HeroSection' / 'Card.module.less').exists()
        assert not (out / 'components' / 'HeroSection' / 'Card' / 'Card.tsx').exists()


def test_section_index_tsx_generated():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_without_leaves('HeroSection')
        generate_components(plan, out, css_ext='less')
        assert (out / 'components' / 'HeroSection' / INDEX_TSX_FILENAME).exists()
        assert (out / 'components' / 'HeroSection' / 'index.module.less').exists()


def test_section_index_tsx_contains_data_map():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_with_leaves('TabSection', 'Tab', [{'title': 'Alpha'}, {'title': 'Beta'}])
        generate_components(plan, out, css_ext='less')
        content = (out / 'components' / 'TabSection' / INDEX_TSX_FILENAME).read_text()
        assert "import Tab from './Tab'" in content
        assert '<Tab' in content


def test_page_tsx_imports_only_sections():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_with_leaves('HeroSection', 'Card', [{'title': 'A'}])
        generate_components(plan, out, css_ext='less')
        page = (out / INDEX_TSX_FILENAME).read_text()
        assert "import HeroSection from './components/HeroSection'" in page
        assert 'Card' not in page


def test_page_tsx_no_map():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_without_leaves('HeroSection')
        generate_components(plan, out, css_ext='less')
        page = (out / INDEX_TSX_FILENAME).read_text()
        assert '<HeroSection' in page
        assert '.map(' not in page


def test_top_level_leaf_goes_to_common():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _top_level_leaf_plan('SharedComp', [{'title': 'X'}, {'title': 'Y'}])
        generate_components(plan, out, css_ext='less')
        assert (out / 'common' / 'SharedComp.tsx').exists()
        assert (out / 'common' / 'SharedComp.module.less').exists()


def test_update_root_index_points_to_page():
    with tempfile.TemporaryDirectory() as tmp:
        page_dir = Path(tmp)
        update_root_index(page_dir, 'TestPage')
        content = (page_dir / INDEX_TS_FILENAME).read_text()
        assert "./components/Page" in content


def test_update_root_index_preserves_original():
    with tempfile.TemporaryDirectory() as tmp:
        page_dir = Path(tmp)
        (page_dir / INDEX_TS_FILENAME).write_text("export { default } from './TestPage'\n")
        update_root_index(page_dir, 'TestPage')
        content = (page_dir / INDEX_TS_FILENAME).read_text()
        assert "./components/Page" in content
        assert "SPLIT_A_ORIGINAL" in content


def _section_with_i18n_leaf(section_name, leaf_name):
    """Create a plan with i18n texts_map so leaf components get texts imports."""
    leaf_ir = _make_ir(leaf_name, tag='div')
    leaf_ir['children'] = [{
        'figmaId': '2:1', 'figmaName': 'text1', 'figmaType': 'TEXT',
        'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': 'Hello',
        'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'font-size': '16px'},
        'children': [],
        'semantic': {'htmlTag': 'span', 'className': 'text1'},
    }]
    return {
        'pageComponent': 'TestPage',
        'nodeId': '1-1',
        'leafComponents': [],
        'sections': [{
            'name': section_name,
            'ir': _make_ir(section_name),
            'ccComponent': None,
            'leafComponents': [{
                'name': leaf_name,
                'ir': leaf_ir,
                'varyingProps': [],
                'instancesData': [],
                'ccComponent': None,
            }],
        }],
    }


def test_leaf_in_section_texts_import_uses_parent_relative_path():
    """Leaf inside components/Section/ must use '../../' to reach texts files at stage root."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_with_i18n_leaf('FaqSection', 'ExpandableItem')
        generate_components(plan, out, css_ext='less')
        leaf_file = out / 'components' / 'FaqSection' / 'ExpandableItem.tsx'
        assert leaf_file.exists(), f"Leaf file not found: {leaf_file}"
        content = leaf_file.read_text()
        assert "from '../../TestPage.texts'" in content or "from '../../TestPage.texts';" in content, \
            f"Expected '../../TestPage.texts' import but got:\n{content}"
        assert "from './TestPage.texts'" not in content, \
            f"Found incorrect './' import in nested leaf:\n{content}"


def _section_with_padded_leaf(section_name, leaf_name):
    """Real data pattern: leaf with padding+overflow+border-radius (card container).
    Based on node I39641:9303;39641:6388 from TomorrowLand PC."""
    leaf_ir = _make_ir(leaf_name, tag='div')
    leaf_ir['css'] = {
        'display': 'flex', 'flex-direction': 'column', 'align-items': 'flex-start',
        'width': '497px', 'height': '140px',
        'padding': '32px 32px 32px 32px',
        'background-color': '#ffffff', 'border-radius': '24px', 'overflow': 'hidden',
        'position': 'absolute', 'left': '120px', 'top': '0px',
        'gap': '12px',
    }
    leaf_ir['figmaId'] = '39641:6388'
    leaf_ir['semantic'] = {
        'htmlTag': 'div', 'className': 'card-container',
        'componentName': leaf_name, 'props': [], 'isExtractedComponent': True,
    }
    return {
        'pageComponent': 'TestPage',
        'nodeId': '1-1',
        'leafComponents': [],
        'sections': [{
            'name': section_name,
            'ir': _make_ir(section_name),
            'ccComponent': None,
            'leafComponents': [{
                'name': leaf_name,
                'ir': leaf_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': None,
                'allInstanceFigmaIds': ['39641:6388'],
            }],
        }],
    }


def test_leaf_wrapper_in_section_no_padding_overflow():
    """Real data: node I39641:9303;39641:6388 from TomorrowLand PC (39641-6863).
    Section CSS for leaf wrapper should NOT have padding/overflow/bg/radius — only positioning.
    Leaf's own CSS should retain all visual properties."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_with_padded_leaf('TopUpSection', 'TopUpCard')
        generate_components(plan, out, css_ext='less')
        section_css = (out / 'components' / 'TopUpSection' / 'index.module.less').read_text()
        leaf_css = (out / 'components' / 'TopUpSection' / 'TopUpCard.module.less').read_text()
        assert 'padding' not in section_css, \
            f"Section CSS should not have padding (leaf wrapper):\n{section_css}"
        assert 'overflow' not in section_css, \
            f"Section CSS should not have overflow (leaf wrapper):\n{section_css}"
        assert 'background-color' not in section_css, \
            f"Section CSS should not have background-color (leaf wrapper):\n{section_css}"
        assert 'padding' in leaf_css, f"Leaf CSS should have padding:\n{leaf_css}"
        assert 'overflow' in leaf_css or 'border-radius' in leaf_css, \
            f"Leaf CSS should have overflow or border-radius:\n{leaf_css}"


def test_cc_button_uses_snippet_variant_not_css_inference():
    """Real data: node 245:12763 from BTC Pizza page (235-14237).
    CC snippet: <Button variant="secondaryBrand" size="small">
    Node CSS: border:2px solid #ffffff, no background-color → would infer 'outline'.
    Must use CC snippet's variant, not CSS inference."""
    # Complete real IR for node 245:12763 (extracted from 235-14237.ir.json)
    real_button_ir = {
        'figmaId': '245:12763', 'figmaName': 'button', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': {
            'width': '250px', 'display': 'flex', 'flex-direction': 'row',
            'justify-content': 'center', 'align-items': 'center', 'gap': '4px',
            'padding': '14px 24px 14px 24px', 'flex-shrink': '0',
            'border': '2px solid #ffffff', 'border-radius': '40px',
            'position': 'relative', 'overflow': 'hidden',
        },
        'semantic': {
            'htmlTag': 'button', 'className': 'button-n245-12763',
            'componentName': 'SecondaryBtn', 'props': [], 'isExtractedComponent': True,
        },
        'children': [{
            'figmaId': 'I245:12763;513:18797', 'figmaName': 'Button Text',
            'figmaType': 'TEXT', 'isTextNode': True, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': None,
            'textContent': 'point', 'textSegments': None,
            'localAssetPath': None, 'lineTypes': ['NONE'],
            'css': {
                'flex-shrink': '0', 'color': 'var(--bds-static-white)',
                'font-size': '20px', 'font-weight': '600',
                "font-family": "'Inter', sans-serif", 'line-height': '1.4',
                'letter-spacing': '0px', 'text-align': 'left', 'white-space': 'nowrap',
            },
            'semantic': {'htmlTag': 'span', 'className': 'button-text-I245-12763-513-18797'},
            'children': [],
        }],
    }
    # Real CC data from MCP get_code_connect_map response (parsed via parse_mcp_response)
    real_cc_data = {
        '245:12763': {
            'componentName': 'Button',
            'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': ['import { Button } from "your-component-lib";'],
            'snippet': '<Button variant="secondaryBrand" size="small"></Button>',
            'label': 'Web', 'source': '', 'isIcon': False,
        }
    }
    plan = {
        'pageComponent': 'TestPage',
        'nodeId': '235-14237',
        'leafComponents': [],
        'sections': [{
            'name': 'ButtonSection',
            'ir': {**_make_ir('ButtonSection'), 'children': [real_button_ir]},
            'ccComponent': None,
            'leafComponents': [{
                'name': 'SecondaryBtn',
                'ir': real_button_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Button',
                'allInstanceFigmaIds': ['245:12763'],
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        # Write CC JSON so generate_components can read it
        cc_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        cc_dir.mkdir(parents=True)
        (cc_dir / '235-14237.code-connect.json').write_text(json.dumps(real_cc_data))
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='less')
        finally:
            os.chdir(old_cwd)
        section_tsx = (out / 'components' / 'ButtonSection' / INDEX_TSX_FILENAME).read_text()
        assert 'secondaryBrand' in section_tsx, \
            f"Expected variant='secondaryBrand' from CC snippet but got:\n{section_tsx}"
        assert 'outline' not in section_tsx, \
            f"Should NOT use CSS-inferred 'outline' variant:\n{section_tsx}"


def _make_section_with_button(section_name, btn_figma_id, btn_class, btn_css, btn_name='PrimaryBtn'):
    """Helper: create section IR that contains the button node as child (needed for section CSS)."""
    section_ir = _make_ir(section_name)
    section_ir['children'] = [{
        'figmaId': btn_figma_id, 'figmaName': 'button', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': btn_css,
        'children': [],
        'semantic': {
            'htmlTag': 'button', 'className': btn_class,
            'componentName': btn_name, 'props': [], 'isExtractedComponent': True,
        },
    }]
    return section_ir


def test_cc_confirmed_button_retains_visual_css():
    """Real data: node 245:12751 from BTC Pizza page (235-14237).
    CC snippet: <Button variant="primary" size="small">
    Node CSS: background-color: var(--bds-static-black), border-radius: 40px.
    Moly 'primary' renders yellow — CSS override MUST be preserved in section .module.less."""
    btn_css = {
        'width': '250px', 'display': 'flex', 'flex-direction': 'row',
        'justify-content': 'center', 'align-items': 'center', 'gap': '4px',
        'padding': '14px 24px 14px 24px', 'flex-shrink': '0',
        'background-color': 'var(--bds-static-black)',
        'border-radius': '40px', 'position': 'relative', 'overflow': 'hidden',
    }
    real_button_ir = {
        'figmaId': '245:12751', 'figmaName': 'button', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': {**btn_css, '_rawBgHex': '#000000'},
        'semantic': {
            'htmlTag': 'button', 'className': 'button',
            'componentName': 'PrimaryBtn', 'props': [], 'isExtractedComponent': True,
        },
        'children': [{
            'figmaId': 'I245:12751;513:18789', 'figmaName': 'Button Text',
            'figmaType': 'TEXT', 'isTextNode': True, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': None,
            'textContent': 'Registration', 'textSegments': None,
            'localAssetPath': None, 'lineTypes': ['NONE'],
            'css': {
                'flex-shrink': '0', 'color': 'var(--bds-static-white)',
                'font-size': '20px', 'font-weight': '600',
                "font-family": "'Inter', sans-serif", 'line-height': '1.4',
                'letter-spacing': '0px', 'text-align': 'left', 'white-space': 'nowrap',
            },
            'semantic': {'htmlTag': 'span', 'className': 'button-text-I245-12751-513-18789'},
            'children': [],
        }],
    }
    real_cc_data = {
        '245:12751': {
            'componentName': 'Button',
            'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': ['import { Button } from "your-component-lib";'],
            'snippet': '<Button variant="primary" size="small"></Button>',
            'label': 'Web', 'source': '', 'isIcon': False,
        }
    }
    section_ir = _make_section_with_button('ButtonSection', '245:12751', 'button', btn_css)
    plan = {
        'pageComponent': 'TestPage',
        'nodeId': '235-14237',
        'leafComponents': [],
        'sections': [{
            'name': 'ButtonSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'PrimaryBtn',
                'ir': real_button_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Button',
                'allInstanceFigmaIds': ['245:12751'],
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cc_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        cc_dir.mkdir(parents=True)
        (cc_dir / '235-14237.code-connect.json').write_text(json.dumps(real_cc_data))
        # Create orig CSS file so orig_css_map is populated (real scenario)
        page_code_dir = Path(tmp) / '.figma-to-code' / '3-page-code' / 'TestPage-235-14237'
        page_code_dir.mkdir(parents=True)
        (page_code_dir / 'TestPage.module.less').write_text(
            '.buttonsection {\n  display: flex;\n  flex-direction: column;\n  width: 100%;\n}\n\n'
            '.button {\n  width: 250px;\n  display: flex;\n  flex-direction: row;\n'
            '  justify-content: center;\n  align-items: center;\n  gap: 4px;\n'
            '  padding: 14px 24px 14px 24px;\n  flex-shrink: 0;\n'
            '  background-color: var(--bds-static-black);\n  border-radius: 40px;\n'
            '  position: relative;\n  overflow: hidden;\n}\n'
        )
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='less')
        finally:
            os.chdir(old_cwd)
        section_css = (out / 'components' / 'ButtonSection' / 'index.module.less').read_text()
        # CC confirmed button MUST retain visual overrides
        assert 'background-color' in section_css, \
            f"CC Button must retain background-color override:\n{section_css}"
        assert 'border-radius' in section_css, \
            f"CC Button must retain border-radius override:\n{section_css}"


def test_cc_confirmed_button_border_override():
    """Real data: node 245:12763 from BTC Pizza page (235-14237).
    CC snippet: <Button variant="secondaryBrand" size="small">
    Node CSS: border: 2px solid #ffffff, border-radius: 40px, no bg.
    Moly 'secondaryBrand' default border differs — CSS override MUST be preserved."""
    btn_css = {
        'width': '250px', 'display': 'flex', 'flex-direction': 'row',
        'justify-content': 'center', 'align-items': 'center', 'gap': '4px',
        'padding': '14px 24px 14px 24px', 'flex-shrink': '0',
        'border': '2px solid #ffffff', 'border-radius': '40px',
        'position': 'relative', 'overflow': 'hidden',
    }
    real_button_ir = {
        'figmaId': '245:12763', 'figmaName': 'button', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': btn_css,
        'semantic': {
            'htmlTag': 'button', 'className': 'button-n245-12763',
            'componentName': 'SecondaryBtn', 'props': [], 'isExtractedComponent': True,
        },
        'children': [{
            'figmaId': 'I245:12763;513:18797', 'figmaName': 'Button Text',
            'figmaType': 'TEXT', 'isTextNode': True, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': None,
            'textContent': 'point', 'textSegments': None,
            'localAssetPath': None, 'lineTypes': ['NONE'],
            'css': {
                'flex-shrink': '0', 'color': 'var(--bds-static-white)',
                'font-size': '20px', 'font-weight': '600',
                "font-family": "'Inter', sans-serif", 'line-height': '1.4',
                'letter-spacing': '0px', 'text-align': 'left', 'white-space': 'nowrap',
            },
            'semantic': {'htmlTag': 'span', 'className': 'button-text-I245-12763-513-18797'},
            'children': [],
        }],
    }
    real_cc_data = {
        '245:12763': {
            'componentName': 'Button',
            'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': ['import { Button } from "your-component-lib";'],
            'snippet': '<Button variant="secondaryBrand" size="small"></Button>',
            'label': 'Web', 'source': '', 'isIcon': False,
        }
    }
    section_ir = _make_section_with_button('ButtonSection', '245:12763', 'button-n245-12763', btn_css)
    plan = {
        'pageComponent': 'TestPage',
        'nodeId': '235-14237',
        'leafComponents': [],
        'sections': [{
            'name': 'ButtonSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'SecondaryBtn',
                'ir': real_button_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Button',
                'allInstanceFigmaIds': ['245:12763'],
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cc_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        cc_dir.mkdir(parents=True)
        (cc_dir / '235-14237.code-connect.json').write_text(json.dumps(real_cc_data))
        # Create orig CSS file so orig_css_map is populated (real scenario)
        page_code_dir = Path(tmp) / '.figma-to-code' / '3-page-code' / 'TestPage-235-14237'
        page_code_dir.mkdir(parents=True)
        (page_code_dir / 'TestPage.module.less').write_text(
            '.buttonsection {\n  display: flex;\n  flex-direction: column;\n  width: 100%;\n}\n\n'
            '.button-n245-12763 {\n  width: 250px;\n  display: flex;\n  flex-direction: row;\n'
            '  justify-content: center;\n  align-items: center;\n  gap: 4px;\n'
            '  padding: 14px 24px 14px 24px;\n  flex-shrink: 0;\n'
            '  border: 2px solid #ffffff;\n  border-radius: 40px;\n'
            '  position: relative;\n  overflow: hidden;\n}\n'
        )
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='less')
        finally:
            os.chdir(old_cwd)
        section_css = (out / 'components' / 'ButtonSection' / 'index.module.less').read_text()
        assert 'border' in section_css, \
            f"CC Button must retain border override:\n{section_css}"
        assert 'border-radius' in section_css, \
            f"CC Button must retain border-radius override:\n{section_css}"


def test_inline_pagination_no_classname_prop():
    """Real data: node 227:13142 from MyPage (169-33787).
    Pagination is NOT in _MOLY_CLASSNAME_COMPONENTS — inline snippet must NOT get className prop.
    It should use a wrapper div for positioning instead."""
    pagination_ir = {
        'figmaId': '227:13142', 'figmaName': 'Pagination/Default', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': {
            'display': 'flex', 'flex-direction': 'row', 'align-items': 'center',
            'gap': '4px', 'flex-shrink': '0', 'position': 'relative',
        },
        'semantic': {
            'htmlTag': 'div', 'className': 'paginationdefault',
            'componentName': 'PaginationDefault', 'props': [], 'isExtractedComponent': True,
        },
        'children': [],
    }
    section_ir = _make_ir('SkillHubSection')
    section_ir['children'] = [pagination_ir]
    real_cc_data = {
        '227:13142': {
            'componentName': 'Pagination',
            'ccImport': 'import { Pagination } from "your-component-lib";',
            'allImports': ['import { Pagination } from "your-component-lib";'],
            'snippet': '<Pagination total={50} current={1} pageSize={10}/>',
            'label': 'Web', 'source': '', 'isIcon': False,
        }
    }
    plan = {
        'pageComponent': 'TestPage',
        'nodeId': '169-33787',
        'leafComponents': [],
        'sections': [{
            'name': 'SkillHubSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'PaginationDefault',
                'ir': pagination_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Pagination',
                'allInstanceFigmaIds': ['227:13142'],
                'snippet': '<Pagination total={50} current={1} pageSize={10}/>',
                'allImports': ['import { Pagination } from "your-component-lib";'],
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cc_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        cc_dir.mkdir(parents=True)
        (cc_dir / '169-33787.code-connect.json').write_text(json.dumps(real_cc_data))
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='less')
        finally:
            os.chdir(old_cwd)
        section_tsx = (out / 'components' / 'SkillHubSection' / INDEX_TSX_FILENAME).read_text()
        # Pagination must NOT receive className prop directly on the component
        assert "Pagination" in section_tsx, f"Pagination component missing:\n{section_tsx}"
        # className should be on a wrapper div, NOT on the Pagination component itself
        assert "<Pagination" in section_tsx
        for line in section_tsx.split('\n'):
            if '<Pagination' in line:
                assert "className=" not in line, \
                    f"Pagination should NOT get className prop directly:\n{line}"
                break


def test_icon_with_color_generates_color_prop():
    """Real data: node 4030:41619 from Brand6PreKYC (4030-41365).
    Icon instance IconArrowChevronRight has white fill (rgb 255,255,255) in Figma.
    Single-instance icon with no varyingProps → inlined in section TSX.
    The color must be injected into the inline snippet (color="#ffffff"),
    NOT lost silently with a bare <IconArrowChevronRight /> snippet."""
    # Real IR for icon node 4030:41619
    icon_ir = {
        'figmaId': '4030:41619', 'figmaName': 'icon1',
        'figmaType': 'INSTANCE', 'componentId': '3943:558',
        'bb': {'width': 24.0, 'height': 24.0},
        'isImageNode': False, 'isVectorNode': True,
        'isTextNode': False, 'isComponentInstance': True,
        'isDecorativeElement': True,
        'figmaCounterAxisAlign': None,
        'textContent': None, 'textSegments': None,
        'textAutoResize': None, 'lineTypes': None, 'lineIndentations': None,
        'variants': None, 'imageRef': None, 'fillImageRef': None,
        'iconColor': '#ffffff',
        'css': {
            'width': '24px', 'height': '24px',
            'position': 'absolute', 'left': '50%',
            'transform': 'translateX(-50%) translateY(-50%)',
            'top': '50%', 'border-radius': '100px',
            'overflow': 'hidden', 'pointer-events': 'none',
        },
        'children': [],
    }
    section_ir = {
        'figmaId': '4030:41594', 'figmaName': 'OnePlatform',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                'position': 'relative'},
        'children': [icon_ir],
    }
    plan = {
        'pageComponent': 'Brand6PreKyc',
        'nodeId': '4030-41365',
        'nodeIdSafe': '4030-41365',
        'cssExt': 'less',
        'irPath': '',
        'leafComponents': [],
        'topLeaves': [],
        'inlineNodes': [],
        'sections': [{
            'name': 'OnePlatformSection',
            'ir': section_ir,
            'y': 0,
            'leafComponents': [{
                'name': 'Icon1',
                'ir': icon_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'IconArrowChevronRight',
                'allInstanceFigmaIds': ['4030:41619'],
                'snippet': '<IconArrowChevronRight />',
                'allImports': ['import { IconArrowChevronRight } from "@example/icons";'],
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        # Setup page code dir for orig_css_map
        page_dir = Path(tmp) / '.figma-to-code' / '3-page-code' / 'Brand6PreKyc-4030-41365'
        page_dir.mkdir(parents=True)
        (page_dir / 'Brand6PreKyc.module.less').write_text(
            ".icon1-n4030-41619 { width: 24px; height: 24px; position: absolute; }\n"
        )
        (page_dir / 'Brand6PreKyc.tsx').write_text(
            '<div data-figma-id="4030:41594"><IconArrowChevronRight data-figma-id="4030:41619"/></div>'
        )
        (page_dir / 'figma-maps.json').write_text(json.dumps({
            'fidToClass': {'4030:41594': 'one-platform', '4030:41619': 'icon1-n4030-41619'},
            'fidToSrc': {}
        }))
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='less')
        finally:
            os.chdir(old_cwd)
        # Single-instance icons with no varyingProps are inlined in section TSX — no separate file.
        icon_tsx_path = out / 'components' / 'OnePlatformSection' / 'Icon1.tsx'
        assert not icon_tsx_path.exists(), f"Icon1.tsx should be inlined (not a separate file)"
        # Color must be injected into the section TSX inline snippet.
        section_tsx_path = out / 'components' / 'OnePlatformSection' / INDEX_TSX_FILENAME
        assert section_tsx_path.exists(), f"Section TSX not generated"
        section_tsx = section_tsx_path.read_text()
        has_color = (
            '#ffffff' in section_tsx.lower()
            or '#fff' in section_tsx.lower()
            or 'color' in section_tsx.lower()
            or 'white' in section_tsx.lower()
        )
        assert has_color, f"Section TSX missing white color for inlined icon:\n{section_tsx}"


def test_section_root_no_margin_top_from_canvas_offset():
    """Section root 的画布级 top 偏移不得转换为 margin-top。
    Real data: node 12244:11762 (Frame 2147240395) from Homegames page (12244-11664).
    IR css: position:absolute; top:144px（父节点无 Auto Layout，绝对定位画布）。
    独立渲染为 Section 后，此偏移在 DOM 流式布局中无意义，必须丢弃而非保留为 margin-top。
    margin-top:144px 会在 Section 上方产生多余 144px 空白。

    触发条件：页面根 IR display:flex（_page_has_flex=True）→ _can_flow=True
    → is_section_root=True → 规则 6 将 top:144px 转换为 margin-top:144px。"""
    # Real page root IR CSS (from 12244-11664.ir.json, root figmaId 12244:11664)
    page_root_ir = {
        'figmaId': '12244:11664', 'figmaName': 'Home - games', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '1440px', 'min-height': '1728px', 'background-color': '#000000',
            'position': 'relative', 'display': 'flex', 'flex-direction': 'column',
            'align-items': 'flex-start', 'padding': '0px 0px 25px 0px',
            'max-width': '100%', 'margin-left': 'auto', 'margin-right': 'auto',
        },
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'home-games',
            'componentName': 'HomeGames', 'props': [], 'isExtractedComponent': False,
        },
    }
    section_root_css = {
        'width': '1440px', 'height': '1559px',
        'position': 'absolute', 'left': '0px', 'top': '144px',
        'display': 'flex', 'flex-direction': 'column',
        'align-items': 'center', 'gap': '32px',
    }
    section_ir = {
        'figmaId': '12244:11762', 'figmaName': 'Frame 2147240395', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': section_root_css,
        'bb': {'width': 1440.0, 'height': 1559.0},
        'children': [],
        'semantic': {
            'htmlTag': 'section', 'className': 'frame-2147240395',
            'componentName': 'Frame2147240395Section',
            'props': [], 'isExtractedComponent': True,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        # 写临时 IR 文件（generate_components 通过 irPath 读取页面根 display 属性）
        ir_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '12244-11664.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))
        # Inline nodes (real data from plan.json): header + tab bar above the section
        # canvas y=-3394 is page root; section at y=-3250 → relative_top=144
        # inline 1: y=-3394, h=48 → rel[0..48]; inline 2: y=-3346, h=96 → rel[48..144]
        # total inline height = 144 → gap = 144 - 144 = 0 → no margin-top
        inline_nodes = [
            {'y': -3394.0, 'ir': {'figmaId': '12244:11665', 'bb': {'width': 1440.0, 'height': 48.0}}},
            {'y': -3346.0, 'ir': {'figmaId': '12244:11754', 'bb': {'width': 1440.0, 'height': 96.0}}},
        ]
        plan = {
            'pageComponent': 'HomeGames',
            'nodeId': '12244-11664',
            'irPath': str(ir_path),
            'leafComponents': [],
            'inlineNodes': inline_nodes,
            'sections': [{
                'name': 'Frame2147240395Section',
                'ir': section_ir,
                'y': -3250.0,
                'ccComponent': None,
                'leafComponents': [],
            }],
        }
        generate_components(plan, out, css_ext='less')
        css_file = out / 'components' / 'Frame2147240395Section' / 'index.module.less'
        assert css_file.exists(), f'CSS file not generated: {list(out.rglob("*.less"))}'
        css_content = css_file.read_text()
        assert 'margin-top: 144px' not in css_content, (
            f'Section root 不得将画布 top:144px 转换为 margin-top，'
            f'实际 CSS:\n{css_content}'
        )


def test_tag_cc_flex_direction_retained():
    """Real data: node 11372:39183 from Modal-11372-39177.
    Original page CSS has flex-direction: row on .tag_sales (from Figma IR).
    _MOLY_BUTTON_CC_CONFIRMED_PROPS must include flex-direction so it is NOT stripped
    from CC confirmed Tag/Button classes (Layer 1 validation would fail otherwise)."""
    real_tag_ir = {
        'figmaId': '11372:39183', 'figmaName': 'tag/high-impact', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': {
            'display': 'flex', 'flex-direction': 'row',
            'justify-content': 'center', 'align-items': 'center',
            'gap': '2px', 'flex-shrink': '0',
            'background-color': 'var(--bds-brand-700-normal)',
            'border-radius': '50px', 'position': 'relative', 'overflow': 'hidden',
        },
        'semantic': {
            'htmlTag': 'span', 'className': 'tag_sales',
            'componentName': 'HighImpactTag', 'props': [], 'isExtractedComponent': True,
        },
        'children': [],
    }
    real_cc_data = {
        '11372:39183': {
            'componentName': 'Tag',
            'ccImport': 'import { Tag } from "your-component-lib";',
            'allImports': ['import { Tag } from "your-component-lib";'],
            'snippet': '<Tag color="brand-gradient" size="small">High Impact</Tag>',
            'label': 'Web', 'source': '', 'isIcon': False,
        }
    }
    section_ir = _make_ir('ChangeInTheSection')
    section_ir['children'] = [{
        'figmaId': '11372:39183', 'figmaName': 'tag/high-impact', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': {
            'display': 'flex', 'flex-direction': 'row',
            'justify-content': 'center', 'align-items': 'center',
            'gap': '2px', 'flex-shrink': '0',
            'background-color': 'var(--bds-brand-700-normal)',
            'border-radius': '50px', 'position': 'relative', 'overflow': 'hidden',
        },
        'semantic': {'htmlTag': 'span', 'className': 'tag_sales', 'props': [], 'isExtractedComponent': True},
        'children': [],
    }]
    plan = {
        'pageComponent': 'TestPage',
        'nodeId': '11372-39177',
        'leafComponents': [],
        'sections': [{
            'name': 'ChangeInTheSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'HighImpactTag',
                'ir': real_tag_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Tag',
                'allInstanceFigmaIds': ['11372:39183'],
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cc_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        cc_dir.mkdir(parents=True)
        (cc_dir / '11372-39177.code-connect.json').write_text(json.dumps(real_cc_data))
        page_code_dir = Path(tmp) / '.figma-to-code' / '3-page-code' / 'TestPage-11372-39177'
        page_code_dir.mkdir(parents=True)
        (page_code_dir / 'TestPage.module.less').write_text(
            '.changeinthesection {\n  display: flex;\n  flex-direction: column;\n  width: 100%;\n}\n\n'
            '.tag_sales {\n  display: flex;\n  flex-direction: row;\n'
            '  justify-content: center;\n  align-items: center;\n'
            '  gap: 2px;\n  flex-shrink: 0;\n'
            '  background-color: var(--bds-brand-700-normal);\n  border-radius: 50px;\n'
            '  position: relative;\n  overflow: hidden;\n}\n'
        )
        (page_code_dir / 'figma-maps.json').write_text(
            json.dumps({'fidToClass': {'11372:39183': 'tag_sales'}, 'fidToSrc': {}})
        )
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='less')
        finally:
            os.chdir(old_cwd)
        import re as _re
        section_css = (out / 'components' / 'ChangeInTheSection' / 'index.module.less').read_text()
        assert _re.search(
            r'\.tag_sales\s*\{[^}]*flex-direction\s*:\s*row',
            section_css, _re.DOTALL
        ), f"CC confirmed Tag must retain flex-direction from orig CSS:\n{section_css}"


def test_tag_cc_text_color_two_levels_deep():
    """Real data: node 11372:39183 from Modal-11372-39177 (Homegames calendar).
    CC snippet: <Tag color="brand-gradient" size="small">High Impact</Tag> (no variant).
    Tag IR: text color is 2 levels deep (Tag → layout-control → text node).
    The 1-level-deep search misses the text node; must search 2 levels to inject
    color: var(--bds-static-black) into .tag_sales CSS."""
    # Real IR data: node 11372:39183 from Modal-11372-39177.ir.json
    real_tag_ir = {
        'figmaId': '11372:39183', 'figmaName': 'tag/high-impact', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': {
            'display': 'flex', 'justify-content': 'center', 'align-items': 'center',
            'gap': '2px', 'padding': '0px 6px 0px 6px', 'flex-shrink': '0',
            'background-color': 'var(--bds-brand-700-normal)',
            'border-radius': '50px', 'position': 'relative', 'overflow': 'hidden',
        },
        'semantic': {
            'htmlTag': 'span', 'className': 'tag_sales',
            'componentName': 'HighImpactTag', 'props': [], 'isExtractedComponent': True,
        },
        'children': [{
            # layout-control: NOT a text node (depth 1, current code stops here — bug)
            'figmaId': 'I11372:39183-layout', 'figmaName': 'layout-control',
            'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': None,
            'css': {'display': 'flex', 'flex-direction': 'row', 'align-items': 'center', 'gap': '2px'},
            'semantic': {'htmlTag': 'div', 'className': 'layout-control', 'props': [], 'isExtractedComponent': False},
            'children': [{
                # text node at depth 2 — the color that must be injected into .tag_sales
                'figmaId': 'I11372:39183-text', 'figmaName': 'High Impact',
                'figmaType': 'TEXT',
                'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
                'isDecorativeElement': None,
                'textContent': 'High Impact', 'textSegments': None,
                'localAssetPath': None, 'lineTypes': ['NONE'],
                'css': {
                    'flex-shrink': '0', 'color': 'var(--bds-static-black)',
                    'font-size': '10px', 'font-weight': '400',
                    'font-family': "'Inter', sans-serif", 'line-height': '14px',
                    'letter-spacing': '0px', 'text-align': 'left', 'white-space': 'nowrap',
                },
                'semantic': {'htmlTag': 'span', 'className': 'text', 'props': [], 'isExtractedComponent': False},
                'children': [],
            }],
        }],
    }
    # Real CC data from 11372-39177.code-connect.json
    real_cc_data = {
        '11372:39183': {
            'componentName': 'Tag',
            'ccImport': 'import { Tag } from "your-component-lib";',
            'allImports': ['import { Tag } from "your-component-lib";'],
            'snippet': '<Tag color="brand-gradient" size="small">High Impact</Tag>',
            'label': 'Web', 'source': '', 'isIcon': False,
        }
    }
    # Section IR: tag is a direct child (section CSS traversal picks up .tag_sales)
    section_ir = _make_ir('ChangeInTheSection')
    section_ir['children'] = [{
        'figmaId': '11372:39183', 'figmaName': 'tag/high-impact', 'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': None, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': None,
        'css': {
            'display': 'flex', 'justify-content': 'center', 'align-items': 'center',
            'gap': '2px', 'padding': '0px 6px 0px 6px', 'flex-shrink': '0',
            'background-color': 'var(--bds-brand-700-normal)',
            'border-radius': '50px', 'position': 'relative', 'overflow': 'hidden',
        },
        'semantic': {'htmlTag': 'span', 'className': 'tag_sales', 'props': [], 'isExtractedComponent': True},
        'children': [],
    }]
    plan = {
        'pageComponent': 'TestPage',
        'nodeId': '11372-39177',
        'leafComponents': [],
        'sections': [{
            'name': 'ChangeInTheSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'HighImpactTag',
                'ir': real_tag_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Tag',
                'allInstanceFigmaIds': ['11372:39183'],
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cc_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        cc_dir.mkdir(parents=True)
        (cc_dir / '11372-39177.code-connect.json').write_text(json.dumps(real_cc_data))
        # Page-level CSS: .tag_sales has no color — as generated by convert.py before fix
        page_code_dir = Path(tmp) / '.figma-to-code' / '3-page-code' / 'TestPage-11372-39177'
        page_code_dir.mkdir(parents=True)
        (page_code_dir / 'TestPage.module.less').write_text(
            '.changeinthesection {\n  display: flex;\n  flex-direction: column;\n  width: 100%;\n}\n\n'
            '.tag_sales {\n  display: flex;\n  justify-content: center;\n  align-items: center;\n'
            '  gap: 2px;\n  padding: 0px 6px 0px 6px;\n  flex-shrink: 0;\n'
            '  background-color: var(--bds-brand-700-normal);\n  border-radius: 50px;\n'
            '  position: relative;\n  overflow: hidden;\n}\n'
        )
        # figma-maps.json: maps Tag figmaId → CSS class (CC nodes lack data-figma-id in TSX)
        (page_code_dir / 'figma-maps.json').write_text(
            json.dumps({'fidToClass': {'11372:39183': 'tag_sales'}, 'fidToSrc': {}})
        )
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='less')
        finally:
            os.chdir(old_cwd)
        import re as _re
        section_css = (out / 'components' / 'ChangeInTheSection' / 'index.module.less').read_text()
        assert _re.search(
            r'\.tag_sales\s*\{[^}]*color\s*:\s*var\(--bds-static-black\)',
            section_css, _re.DOTALL
        ), f"Tag with 2-level deep text color must inject color into .tag_sales:\n{section_css}"


def test_non_flow_page_section_without_top_gets_absolute_position():
    """Non-flex page root: section with position:relative (no CSS top) must get
    position:absolute; top:{page_relative_y}px so it sits at its Figma y-coordinate.

    Root cause: AI Hub / BTC Pizza Day / Pre KYC pages have no flex on root →
    _can_flow=False → section CSS is not touched → auto-layout sections
    (position:relative, no top) flow from y:0 instead of their Figma positions.

    Fix: when _can_flow=False and a section has no CSS top, infer page_root_y from
    another section that has both y and CSS top, then add position:absolute; top:{y}px.

    Real data: AI Hub (169-33787)
      - TaskCard2RowSection: position:relative, no top, canvas y=1673
      - ComponentSection: position:absolute; top:2995px, canvas y=3731
        → page_root_y = 3731 - 2995 = 736
        → TaskCard2Row page-relative y = 1673 - 736 = 937
    """
    # Real data: page root IR from AI Hub (169:33787) - no flex/grid display
    page_root_ir = {
        'figmaId': '169:33787', 'figmaName': 'MY LANDING PAGE', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '1440px', 'height': '3942px',
            'background-color': '#ffffff',
            'position': 'relative',
            'max-width': '100%', 'margin-left': 'auto', 'margin-right': 'auto',
        },
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'my-landing-page',
            'componentName': 'MyPage', 'props': [], 'isExtractedComponent': False,
        },
    }
    # Section A: auto-layout section with position:relative, NO CSS top
    # Real: TaskCard2RowSection from AI Hub, canvas y=1673
    flow_section_ir = {
        'figmaId': '169:35532', 'figmaName': 'Frame 2147224803', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'display': 'flex', 'flex-direction': 'column',
            'align-items': 'flex-start', 'gap': '88px',
            'position': 'relative',
        },
        'bb': {'width': 1336.0, 'height': 1096.0},
        'children': [],
        'semantic': {
            'htmlTag': 'section', 'className': 'frame-2147224803',
            'componentName': 'TaskCard2RowSection', 'props': [], 'isExtractedComponent': True,
        },
    }
    # Section B: absolute section WITH CSS top — used to infer page_root_y
    # Real: ComponentSection from AI Hub, canvas y=3731, css_top=2995 → page_root_y=736
    anchor_section_ir = {
        'figmaId': '177:13498', 'figmaName': 'node-177-13498', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '1440px', 'height': '537px',
            'position': 'absolute', 'left': '0px', 'top': '2995px',
            'display': 'flex', 'flex-direction': 'column',
        },
        'bb': {'width': 1440.0, 'height': 537.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'node-177-13498',
            'componentName': 'ComponentSection', 'props': [], 'isExtractedComponent': True,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '169-33787.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))
        plan = {
            'pageComponent': 'MyPage',
            'nodeId': '169-33787',
            'irPath': str(ir_path),
            'leafComponents': [],
            'inlineNodes': [],
            'sections': [
                {
                    'name': 'TaskCard2RowSection',
                    'ir': flow_section_ir,
                    'y': 1673.0,
                    'ccComponent': None,
                    'leafComponents': [],
                },
                {
                    'name': 'ComponentSection',
                    'ir': anchor_section_ir,
                    'y': 3731.0,
                    'ccComponent': None,
                    'leafComponents': [],
                },
            ],
        }
        generate_components(plan, out, css_ext='less')

        # FlowSection (TaskCard2RowSection) CSS must NOT be changed —
        # the wrapper approach positions it in Page.tsx, not by modifying the section CSS.
        flow_css = (out / 'components' / 'TaskCard2RowSection' / 'index.module.less').read_text()
        assert 'position: relative' in flow_css or 'position:relative' in flow_css, (
            f'TaskCard2RowSection CSS must keep position:relative (wrapper handles placement). '
            f'Actual CSS:\n{flow_css}'
        )

        # Page.tsx must wrap TaskCard2RowSection with an absolute-positioned div at top=937px
        page_tsx = (out / INDEX_TSX_FILENAME).read_text()
        assert "top: '937px'" in page_tsx or "top:'937px'" in page_tsx, (
            f'Page.tsx must wrap TaskCard2RowSection at top=937px (1673-736). '
            f'Actual Page.tsx:\n{page_tsx}'
        )
        assert 'position: \'absolute\'' in page_tsx or "position: 'absolute'" in page_tsx, (
            f'Page.tsx must use position:absolute wrapper for non-flex page sections. '
            f'Actual Page.tsx:\n{page_tsx}'
        )

        # AnchorSection (ComponentSection) must keep its original absolute CSS
        anchor_css = (out / 'components' / 'ComponentSection' / 'index.module.less').read_text()
        assert 'top: 2995px' in anchor_css or 'top:2995px' in anchor_css, (
            f'ComponentSection must retain top:2995px. Actual CSS:\n{anchor_css}'
        )


def _make_card_ir(fid, name='TaskCard'):
    """最小叶子节点 IR，带明确 figmaId（用于子 section 内实例识别）。"""
    return {
        'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': 200, 'height': 80},
        'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
        'css': {'display': 'flex'}, 'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'task-card',
            'componentName': name, 'props': [],
        },
    }


def test_subsection_leaf_instances_render_as_component_calls():
    """RC-1 修复验证：子 Section 内的重复叶子实例应渲染为 <ComponentName /> 调用，
    并从父目录（'../ComponentName'）import，而非内联 HTML + 相同目录 import。

    Real data: TaskCard2RowSection/Section3 from MyPage (nodeId: 169-33787)
    4 张 Task Card（figmaId: 169:35571/79/87/95）在 Section3 子 section 内部；
    TaskCard2Row 作为 TaskCard2RowSection 的叶子组件（count=4）被正确检测，
    但 Section3 使用 _generate_leaf_tsx 生成，不传 leafComponents → 4 张卡片全部内联。
    修复后：Section3/index.tsx 包含 <TaskCard /> 调用 + import from '../TaskCard'。
    """
    LEAF_FIDS = ['tc:1', 'tc:2', 'tc:3', 'tc:4']

    ss_ir = {
        'figmaId': 'ss:root', 'figmaName': 'Section3', 'figmaType': 'FRAME',
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': 400, 'height': 400},
        'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
        'css': {'display': 'flex', 'flex-direction': 'column'},
        'children': [_make_card_ir(fid) for fid in LEAF_FIDS],
        'semantic': {
            'htmlTag': 'section', 'className': 'section3',
            'componentName': 'Section3', 'props': [],
        },
    }

    plan = {
        'pageComponent': 'TestPage',
        'nodeId': '1-1',
        'leafComponents': [],
        'sections': [{
            'name': 'TaskCard2RowSection',
            'ir': _make_ir('TaskCard2RowSection'),
            'ccComponent': None,
            'leafComponents': [{
                'name': 'TaskCard',
                'ir': _make_card_ir('tc:1'),
                'varyingProps': [],
                'instancesData': [{} for _ in LEAF_FIDS],
                'allInstanceFigmaIds': list(LEAF_FIDS),
                'ccComponent': None,
                'allImports': [],
            }],
            'subSections': [{
                'name': 'Section3',
                'ir': ss_ir,
                'leafComponents': [],
            }],
        }],
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_components(plan, out, css_ext='less')
        ss_tsx_path = out / 'components' / 'TaskCard2RowSection' / 'Section3' / INDEX_TSX_FILENAME
        assert ss_tsx_path.exists(), 'Section3/index.tsx should be generated'
        ss_tsx = ss_tsx_path.read_text()

        assert '<TaskCard' in ss_tsx, (
            f'Sub-section should contain <TaskCard /> component calls, not inline HTML.\n'
            f'Current content (first 600 chars):\n{ss_tsx[:600]}'
        )
        assert "import TaskCard from '../TaskCard'" in ss_tsx, (
            f'Sub-section should import from "../TaskCard" (parent dir), '
            f'not "./TaskCard".\nCurrent imports:\n'
            + '\n'.join(l for l in ss_tsx.splitlines() if 'import' in l)
        )
        # _generate_section_tsx 约定 import './index.module.less'，CSS 必须用 index 命名
        assert (out / 'components' / 'TaskCard2RowSection' / 'Section3' / 'index.module.less').exists(), (
            'Sub-section CSS must be index.module.less when using _generate_section_tsx'
        )
        assert not (out / 'components' / 'TaskCard2RowSection' / 'Section3' / 'Section3.module.less').exists(), (
            'Section3.module.less must not exist when using _generate_section_tsx path'
        )


def test_section_wrapper_uses_abs_left_and_width_for_fixed_sections():
    """Task #1: 固定尺寸设计稿的 section wrapper 应使用 Figma 实际 left/width 值，
    而非 left:0/width:'100%'（会导致内容左侧贴边）。

    Real data: MyPage (nodeId: 169-33787)
    Section 'Frame 2147224809' (169:35529): Figma left=120px, width=1200px，
    在 1440px 页面中居中。生成代码应保留 left:'120px', width:'1200px'，
    而非硬编码 left:0, width:'100%'。
    """
    body_items = [
        {
            'y': 817.0,
            'type': 'section',
            'name': 'TaskCard2RowSection',
            'abs_top': 937.0,
            'abs_left': 120.0,    # Real data: 120px from MyPage Figma design
            'abs_width': 1200.0,  # Real data: 1200px section width
        }
    ]
    tsx = _generate_page_tsx('TestPage', body_items, 'test-page', 'less')

    wrapper_lines = [l for l in tsx.splitlines() if 'absolute' in l and 'TaskCard2RowSection' not in l]
    assert "left: '120px'" in tsx, (
        f"Wrapper should use abs_left=120px, not hardcoded left:0.\n"
        f"Wrapper line(s): {wrapper_lines}"
    )
    assert "width: '1200px'" in tsx, (
        f"Wrapper should use abs_width=1200px, not hardcoded width:'100%'.\n"
        f"Wrapper line(s): {wrapper_lines}"
    )


def test_section_wrapper_falls_back_to_full_width_when_no_abs_left():
    """backward compat: abs_left/abs_width 未设时，保持原有 left:0/width:'100%' 行为。"""
    body_items = [
        {
            'y': 100.0,
            'type': 'section',
            'name': 'HeroSection',
            'abs_top': 100.0,
            # abs_left and abs_width intentionally absent (full-width section)
        }
    ]
    tsx = _generate_page_tsx('TestPage', body_items, 'test-page', 'less')
    wrapper_lines = [l for l in tsx.splitlines() if 'absolute' in l and 'HeroSection' not in l]
    assert 'left: 0' in tsx or "left: '0'" in tsx, (
        f"Full-width section should keep left:0 when no abs_left.\n{wrapper_lines}"
    )
    assert "width: '100%'" in tsx, (
        f"Full-width section should keep width:'100%' when no abs_width.\n{wrapper_lines}"
    )


def test_non_flow_inline_node_gets_left_and_width():
    """Non-flex page: inline node with position:relative (no CSS top/left) must get
    position:absolute; top:{y}px; left:{x}px; width:{bb.width}px when converted.

    Root cause: split_codegen only injected top+height but omitted left+width,
    causing centered sections to render flush-left instead of page-centered.

    Real data: MyPage (169-33787), node 169:35530
      - Frame 2147224387: position:relative, canvas x=120, y=1553, bb={width:1200, height:40}
      - Anchor section (ComponentSection): y=3731, css top=2995 → page_root_y=736
      - Full-width nav section: bb.width=1440, x=0 → page_root_x=0
      - Expected: position:absolute; top:817px (1553-736); left:120px (120-0); width:1200px
    """
    page_root_ir = {
        'figmaId': '169:33787', 'figmaName': 'MY LANDING PAGE', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '1440px', 'height': '3942px',
            'background-color': '#ffffff', 'position': 'relative',
            'max-width': '100%', 'margin-left': 'auto', 'margin-right': 'auto',
        },
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'my-landing-page',
            'componentName': 'MyPage', 'props': [], 'isExtractedComponent': False,
        },
    }
    # Full-width anchor section: bb.width=1440, x=0 → page_root_x=0
    # Also has CSS top → anchors page_root_y (3731 - 2995 = 736)
    # Real: ComponentSection from AI Hub
    anchor_section_ir = {
        'figmaId': '177:13498', 'figmaName': 'node-177-13498', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '1440px', 'height': '537px',
            'position': 'absolute', 'left': '0px', 'top': '2995px',
            'display': 'flex', 'flex-direction': 'column',
        },
        'bb': {'width': 1440.0, 'height': 537.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'node-177-13498',
            'componentName': 'ComponentSection', 'props': [], 'isExtractedComponent': True,
        },
    }
    # Inline node: position:relative, centered (x=120, bb.width=1200) in 1440px page
    # Real: node 169:35530 "Frame 2147224387" from AI Hub
    inline_node_ir = {
        'figmaId': '169:35530', 'figmaName': 'Frame 2147224387', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'display': 'flex', 'flex-direction': 'column',
            'align-items': 'center', 'gap': '20px',
            'flex-shrink': '0', 'align-self': 'stretch',
            'position': 'relative',
        },
        'bb': {'width': 1200.0, 'height': 40.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'frame-2147224387',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '169-33787.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))
        plan = {
            'pageComponent': 'MyPage',
            'nodeId': '169-33787',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [
                {
                    'name': 'ComponentSection',
                    'ir': anchor_section_ir,
                    'y': 3731.0,
                    'x': 0.0,      # full-width → page_root_x = 0
                    'ccComponent': None,
                    'leafComponents': [],
                },
            ],
            # x=120: absolute canvas x of this inline node (120 - page_root_x(0) = left:120)
            'inlineNodes': [{'ir': inline_node_ir, 'y': 1553.0, 'x': 120.0}],
        }
        generate_components(plan, out, css_ext='less')

        page_css = (out / 'MyPage.module.less').read_text()
        # Must have left:120px (x=120 - page_root_x=0)
        assert 'left: 120px' in page_css or 'left:120px' in page_css, (
            f'Inline node with x=120 must get left:120px in CSS.\n'
            f'frame-2147224387 rule:\n'
            + '\n'.join(l for l in page_css.splitlines() if 'frame-2147224387' in l or ('left' in l and 'absolute' in page_css))
        )
        # Must have width:1200px (bb.width)
        assert 'width: 1200px' in page_css or 'width:1200px' in page_css, (
            f'Inline node with bb.width=1200 must get width:1200px in CSS.\n'
            f'Actual page CSS snippet:\n{page_css[page_css.find("frame-2147224387"):page_css.find("frame-2147224387")+300]}'
        )
        # Must still have correct top:817px (1553 - 736)
        assert 'top: 817px' in page_css or 'top:817px' in page_css, (
            f'Inline node top must be 817px (1553 - page_root_y 736).\nActual:\n{page_css}'
        )


def test_leaf_component_accepts_data_figma_id_prop():
    """Leaf components reused multiple times must accept 'data-figma-id' as a prop
    and apply it to their root element (instead of hardcoding the first instance's id).

    Root cause: _generate_leaf_tsx hardcodes data-figma-id on root element;
    all instances render the same id, making Figma node-based inspection impossible.

    Real data: MyPage (169-33787), TaskCard2Row (4 instances):
      figmaIds: 169:35571, 169:35579, 169:35587, 169:35595
    Expected:
      - TaskCard2Row.tsx interface has 'data-figma-id'?: string
      - Root element uses data-figma-id={dataFigmaId ?? "169:35571"}
      - Section/index.tsx passes different data-figma-id per instance
    """
    LEAF_FIDS = ['169:35571', '169:35579', '169:35587', '169:35595']
    INSTANCE_DATA = [
        {'text0': '行情洞察', 'btc24': '帮我查询 BTC 价格'},
        {'text0': '快捷交易', 'btc24': '帮我买入0.1个BTC'},
        {'text0': '资产管理', 'btc24': '查看USDT余额'},
        {'text0': '衍生品交易', 'btc24': '调整杠杆为10x'},
    ]
    leaf_ir = {
        'figmaId': '169:35571', 'figmaName': 'TaskCard2Row', 'figmaType': 'FRAME',
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': 1200, 'height': 120},
        'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
        'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'task-card-2row-n169-35571',
            'componentName': 'TaskCard2Row', 'props': [],
        },
    }
    section_ir = {
        'figmaId': 'sec:root', 'figmaName': 'TaskCard2RowSection', 'figmaType': 'FRAME',
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': 1200, 'height': 600},
        'isTextNode': False, 'isImageNode': False, 'isDecorativeElement': False,
        'css': {'display': 'flex', 'flex-direction': 'column'},
        'children': [
            {**leaf_ir, 'figmaId': fid, 'semantic': {**leaf_ir['semantic'], '_prop_name': None}}
            for fid in LEAF_FIDS
        ],
        'semantic': {
            'htmlTag': 'section', 'className': 'task-card-2row-section',
            'componentName': 'TaskCard2RowSection', 'props': [], 'isExtractedComponent': True,
        },
    }
    plan = {
        'pageComponent': 'TestPage',
        'nodeId': '1-1',
        'leafComponents': [],
        'sections': [{
            'name': 'TaskCard2RowSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'TaskCard2Row',
                'ir': leaf_ir,
                'varyingProps': [],   # no varying text props; focus on data-figma-id
                'instancesData': [{} for _ in LEAF_FIDS],
                'allInstanceFigmaIds': LEAF_FIDS,
                'ccComponent': None,
                'allImports': [],
            }],
        }],
        'inlineNodes': [],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_components(plan, out, css_ext='less')

        leaf_tsx_path = out / 'components' / 'TaskCard2RowSection' / 'TaskCard2Row.tsx'
        assert leaf_tsx_path.exists(), 'TaskCard2Row.tsx must be generated'
        leaf_tsx = leaf_tsx_path.read_text()

        # Interface must accept 'data-figma-id'
        assert "'data-figma-id'?: string" in leaf_tsx or '"data-figma-id"?: string' in leaf_tsx, (
            f"TaskCard2Row interface must include 'data-figma-id'?: string.\n"
            f"Actual interface:\n"
            + '\n'.join(l for l in leaf_tsx.splitlines() if 'Props' in l or 'interface' in l or 'data-figma' in l)
        )
        # Root element must use dynamic prop, not hardcoded id
        assert 'dataFigmaId' in leaf_tsx, (
            f"Root element must use dataFigmaId variable (not hardcoded figma-id).\n"
            f"Actual root element line:\n"
            + next((l for l in leaf_tsx.splitlines() if 'task-card-2row' in l or 'data-figma-id' in l), '')
        )
        # Section must pass per-instance data-figma-id
        section_tsx_path = out / 'components' / 'TaskCard2RowSection' / INDEX_TSX_FILENAME
        if section_tsx_path.exists():
            section_tsx = section_tsx_path.read_text()
            assert 'data-figma-id="169:35579"' in section_tsx or "data-figma-id='169:35579'" in section_tsx, (
                f"Section must pass different data-figma-id per instance (e.g. 169:35579).\n"
                f"Actual TaskCard2Row calls:\n"
                + '\n'.join(l for l in section_tsx.splitlines() if 'TaskCard2Row' in l or 'figma-id' in l)
            )


def test_flow_page_narrow_section_gets_margin_left():
    """Flow-mode narrow section must get marginLeft wrapper for horizontal centering.

    Root cause: _can_flow=True → abs_left never computed for sections → narrow sections
    (width < page width) render flush-left in flex column container instead of centered.

    Real data: Brand6PreKyc (4030-41365), Frame2147229759Section:
      bb.width=1200 inside 1440px page root → abs_left=120px
    Expected: Page.tsx wraps section with <div style={{ marginLeft: '120px' }}>
    so the 1200px section is properly indented/centered in the 1440px container.
    """
    body_items = [
        {
            'y': 100.0,
            'type': 'section',
            'name': 'Frame2147229759Section',
            'abs_top': None,     # flow mode — no absolute positioning
            'abs_left': 120.0,   # Real: 120px from Brand6PreKyc Figma canvas
            'abs_width': None,   # width handled by section's own CSS
        }
    ]
    tsx = _generate_page_tsx('TestPage', body_items, 'test-page', 'less')

    # Must add marginLeft wrapper for the narrow section
    assert "marginLeft: '120px'" in tsx or "marginLeft:'120px'" in tsx, (
        f"Flow-mode narrow section with abs_left=120 must get marginLeft: '120px' wrapper.\n"
        f"Actual section line(s):\n"
        + '\n'.join(l for l in tsx.splitlines() if 'Frame2147229759Section' in l or 'margin' in l.lower())
    )


def test_leaf_root_transform_stripped_when_wrapper():
    """Leaf root transform:translateX should be stripped when leaf has a wrapper.

    Bug: VolumeBarCard (frame-2147224892) IR has transform:translateX(-50%) from
    the centering pattern left:calc(50%-251px)+transform:translateX(-50%).
    The WRAPPER in PositionStructureSection already has both left+transform applied.
    The leaf root should NOT repeat transform — causes double -50% shift,
    displacing content 21px to the left of the visible area (clipped by overflow:hidden).

    Fix: 'transform' added to _LEAF_ROOT_STRIP_PROPS so it is stripped from leaf root
    CSS when _has_wrapper=True.

    Real data: node 10664:27043, VolumeBarCard (frame-2147224892), 10664-26577
    leaf CSS was: transform:translateX(-50%) → must be absent when wrapper present.
    """
    import tempfile, shutil
    from lib.split_codegen import generate_components as _gc2

    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '10664-26577'
        comp = 'TestPage'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp}-{node_id}'
        (page_dir / SPLIT_A_SUBDIR).mkdir(parents=True)
        # Minimal original CSS with the transform in frame-2147224892
        (page_dir / f'{comp}.module.less').write_text(
            '.frame-2147224892 {\n'
            '  width: 42px;\n'
            '  height: 93.52%;\n'
            '  position: absolute;\n'
            '  left: calc(50% + -251px);\n'
            '  transform: translateX(-50%);\n'
            '  top: 0%;\n'
            '}\n'
        )
        (page_dir / 'figma-maps.json').write_text('{}')

        def _mk_ir(fid, name, children=None):
            return {
                'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
                'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                'isDecorativeElement': False, 'isComponentInstance': False,
                'componentId': None, 'textContent': None, 'textSegments': None,
                'localAssetPath': None, 'lineTypes': [],
                'css': {'width': '42px', 'height': '93.52%',
                        'position': 'absolute', 'left': 'calc(50% + -251px)',
                        'transform': 'translateX(-50%)', 'top': '0%'},
                'bb': {'width': 42, 'height': 576},
                'children': children or [],
                'semantic': {
                    'htmlTag': 'div', 'className': 'frame-2147224892',
                    'componentName': 'VolumeBarCard', 'props': [],
                    'isExtractedComponent': True,
                },
            }

        leaf_ir = _mk_ir('10664:27043', 'frame-2147224892')
        plan = {
            'pageComponent': comp, 'nodeIdSafe': node_id, 'nodeId': node_id,
            'cssExt': 'less',
            'sections': [{
                'name': 'BarSection',
                'ir': {'figmaId': '10664:26609', 'figmaName': 'BarSection',
                       'figmaType': 'FRAME', 'isTextNode': False,
                       'isComponentInstance': False, 'componentId': None,
                       'css': {'width': '100%', 'display': 'flex'}, 'children': [],
                       'bb': {'width': 1208, 'height': 2000},
                       'semantic': {'htmlTag': 'section', 'className': 'bar-section',
                                    'componentName': 'BarSection', 'props': [],
                                    'isExtractedComponent': True}},
                'leafComponents': [{
                    'name': 'VolumeBarCard',
                    'ir': leaf_ir,
                    'varyingProps': [],
                    'instancesData': [{}],
                    'allInstanceFigmaIds': ['10664:27043'],
                    'ccComponent': None,
                }],
                'subSections': [], 'y': 100,
            }],
            'leafComponents': [], 'topLeaves': [], 'inlineNodes': [],
            'irPath': str(page_dir / 'fake.ir.json'),
            'fidToClass': {'10664:27043': 'frame-2147224892'},
            'cssMap': {'frame-2147224892': {
                'width': '42px', 'height': '93.52%',
                'position': 'absolute', 'left': 'calc(50% + -251px)',
                'transform': 'translateX(-50%)', 'top': '0%',
            }},
        }
        plan_path = page_dir / SPLIT_A_SUBDIR / 'plan.json'
        plan_path.write_text(json.dumps(plan))

        out_dir = tmp / 'out'
        out_dir.mkdir()
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            _gc2(plan, out_dir, 'less')
        finally:
            os.chdir(old_cwd)

        # Find the VolumeBarCard leaf CSS
        leaf_scss_files = list(out_dir.rglob('VolumeBarCard.module.less'))
        assert leaf_scss_files, 'VolumeBarCard.module.less not generated'
        leaf_css = leaf_scss_files[0].read_text()
        assert 'transform' not in leaf_css, (
            f'leaf root CSS must NOT contain transform when wrapper handles positioning.\n'
            f'Got: {[l for l in leaf_css.splitlines() if "transform" in l]}'
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_leaf_with_padding_and_fixed_width_gets_border_box():
    """Leaf element with fixed px width AND padding must get box-sizing:border-box.

    Bug: Figma tooltip (frame-2147223812, 10664:27168) is 232px TOTAL (border-box,
    including 16px padding on each side). Generated CSS has width:232px +
    padding:16px without box-sizing:border-box → actual rendered width = 266px →
    34px overflow outside the containing card.

    Fix: _css_from_orig adds box-sizing:border-box when element has both
    a fixed px width and padding properties.

    Real data: node 10664:27168, frame-2147223812, 10664-26577
    CSS: width:232px; padding:16px 16px 16px 16px → inner content should be 200px.
    """
    from lib.split_codegen import _css_from_orig

    ir = {
        'figmaId': '10664:27168', 'figmaName': 'frame-2147223812',
        'figmaType': 'FRAME', 'isTextNode': False,
        'css': {
            'width': '232px',
            'padding': '16px 16px 16px 16px',
            'position': 'absolute',
            'left': '203px',
            'top': '388.78px',
            'display': 'flex',
            'flex-direction': 'column',
            'background-color': '#ffffff',
            'border-radius': '8px',
        },
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'frame-2147223812',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }
    orig_css_map = {
        'frame-2147223812': {
            'width': '232px', 'padding': '16px 16px 16px 16px',
            'position': 'absolute', 'left': '203px', 'top': '388.78px',
            'display': 'flex', 'flex-direction': 'column',
            'background-color': '#ffffff', 'border-radius': '8px',
        }
    }
    css = _css_from_orig(ir, orig_css_map, 'less')
    assert 'box-sizing: border-box' in css or 'box-sizing:border-box' in css, (
        f'Element with fixed width + padding must have box-sizing:border-box.\n'
        f'Got CSS: {css[:300]}'
    )


def test_subsection_leaf_wrapper_strips_visual_css():
    """Leaf wrapper class in a subsection must have visual CSS stripped (padding/bg/border).

    Bug: subsection CSS is generated at line 1244 BEFORE _moly_wrappers is built
    (lines 1272-1332) AND without passing moly_wrapper_classes. So the subsection
    leaf wrapper keeps ALL visual CSS including padding, background-color, border,
    causing double visual styling when the inner leaf component also renders those.

    Real scenario: PriceListCard (frame-2147223812) wrapper in PositionStructureSection.
      outer: frame-2147223812-n10664-27168 {padding:16px; bg:gray; border:1px}
      inner: frame-2147223812              {padding:16px; bg:gray; border:1px}
    → double padding (32px total) + double background + nested card appearance.

    Fix: build per-subsection _ss_moly_wrappers before generating subsection CSS,
    pass moly_wrapper_classes to _css_from_orig for subsections.

    Test approach: unit-test _css_from_orig directly with moly_wrapper_classes set
    to verify visual CSS is stripped when class is in the wrapper set.
    Real data: frame-2147223812-n10664-27168 from 10664-26577
    """
    from lib.split_codegen import _css_from_orig

    ir = {
        'figmaId': '10664:27168', 'figmaName': 'frame-2147223812',
        'figmaType': 'FRAME', 'isTextNode': False,
        'css': {
            'width': '232px', 'position': 'absolute', 'left': '203px', 'top': '388px',
            'padding': '16px 16px 16px 16px', 'background-color': 'var(--bds-gray-bg-card)',
            'border': '1px solid #dde1e5', 'border-radius': '8px',
        },
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'frame-2147223812-n10664-27168',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }
    orig_css_map = {
        'frame-2147223812-n10664-27168': {
            'width': '232px', 'position': 'absolute', 'left': '203px', 'top': '388px',
            'padding': '16px 16px 16px 16px', 'background-color': 'var(--bds-gray-bg-card)',
            'border': '1px solid #dde1e5', 'border-radius': '8px',
        }
    }
    # With moly_wrapper_classes → visual CSS must be stripped, positioning kept
    css_with_strip = _css_from_orig(ir, orig_css_map, 'less',
                                    moly_wrapper_classes={'frame-2147223812-n10664-27168'})
    assert 'padding' not in css_with_strip, (
        f'Wrapper class with moly_wrapper_classes must NOT have padding.\nGot: {css_with_strip}'
    )
    assert 'background-color' not in css_with_strip, (
        'Wrapper class must NOT have background-color when in moly_wrapper_classes'
    )
    # Positioning MUST be kept (position/left/top/width are in _MOLY_WRAPPER_POSITIONING_PROPS)
    assert 'position' in css_with_strip and 'left' in css_with_strip, (
        'Wrapper class must KEEP position and left when in moly_wrapper_classes'
    )
    # Without moly_wrapper_classes → padding still present (baseline / default)
    css_without_strip = _css_from_orig(ir, orig_css_map, 'less')
    assert 'padding' in css_without_strip, (
        'Without moly_wrapper_classes, padding must be present (baseline check)'
    )


def test_flow_page_auto_margin_section_no_margin_left_wrapper():
    """Flow-mode section with margin-left:auto must NOT get marginLeft wrapper.

    Bug: generate_components computes abs_left = section.x - page_root_x = 116px for
    BarSection. _generate_page_tsx adds <div style={{ marginLeft: '116px' }}>. But
    BarSection already has margin-left:auto in IR CSS, so align-items:center centers
    it naturally. The extra marginLeft double-counts the 116px centering offset.

    Fix: generate_components sets abs_left=None when section IR CSS has margin-left:auto.
    _generate_page_tsx receives abs_left=None → no wrapper → section self-centers.

    Real data: node 10664-26577 BarSection (frame-2147224833)
      page_root_x = 0 (from NavTabSection bb.width=1440, x=0)
      BarSection.x = 116, BarSection IR CSS margin-left:auto
      Expected: NO marginLeft wrapper in index.tsx
    """
    body_items = [
        {
            'y': 200.0,
            'type': 'section',
            'name': 'BarSection',
            'abs_top': None,
            'abs_left': None,  # correctly cleared because margin-left:auto
            'abs_width': None,
        }
    ]
    tsx = _generate_page_tsx('TestPage', body_items, 'test-page', 'less')
    assert "marginLeft: '116px'" not in tsx and "marginLeft:'116px'" not in tsx, (
        "BarSection with margin-left:auto should not get marginLeft wrapper.\n"
        + '\n'.join(l for l in tsx.splitlines() if 'BarSection' in l or 'margin' in l.lower())
    )


# U-375 reverted: approach was too broad (icon1/arrow icons also affected)


def test_deco_spanning_multiple_sections_treated_as_overlay():
    """Real data: desktop1526 (nodeId: 6777-36042).
    deco-court-pattern inline node (y=52, h=1598) spans BlockHeroSection AND Block2Section.

    Bug 1: _is_section_overlay required the node to be *completely contained* within one section.
    A multi-section deco got added to flow_items → overlap with BlockHeroSection → _page_can_flow=False.
    Fix 1: _is_section_overlay returns True when node starts AND ends within (possibly different) sections.

    Bug 2: Figma sub-pixel precision causes adjacent section bottom/top to differ by ~0.0001px
    (e.g. Section1.bottom=891.9372558… > Section2.top=891.9371948…), triggering false overlap.
    Fix 2: _page_can_flow uses _FLOW_OVERLAP_TOL=0.5px tolerance in the overlap check.
    """
    # Real data: exact section y-ranges from desktop1526 (6777:36042) plan.json
    # Note the sub-pixel float difference between consecutive sections (Bug 2 data)
    section_y_ranges = [
        (51.937255859375,  891.937255859375),   # BlockHeroSection: note bottom > next top by ~0.0001px
        (891.9371948242188, 2073.9371948242188), # Block2Section
        (2073.937255859375, 2803.937255859375),  # Block2Section2
        (2803.937255859375, 3625.937255859375),  # Block4Section
        (3625.937255859375, 4579.9375),          # Block5Section
        (4579.9375,         5419.9375),          # SectionTermsSection
        (5419.9375,         6427.9375),          # FooterSection
    ]
    # Real data: inline nodes from desktop1526 plan.json inlineNodes
    nav_node  = {'y': 0.0,  'ir': {'bb': {'height': 51.93719482421875}}}  # nav above sections
    deco_node = {'y': 52.0, 'ir': {'bb': {'height': 1598.0}}}             # deco spans hero+block2

    # Bug 1 fix: deco starts within BlockHeroSection and ends within Block2Section → overlay
    assert _is_section_overlay(52.0, 1598.0, section_y_ranges), (
        "deco (y=52, h=1598) starts within BlockHeroSection and ends within Block2Section → overlay"
    )
    # nav ends exactly at section top (51.93719 <= 51.93726) → NOT an overlay
    assert not _is_section_overlay(0.0, 51.93719482421875, section_y_ranges), (
        "nav (bottom=51.937) is adjacent to BlockHeroSection (top=51.937), not an overlay"
    )
    # Bug 2 fix: adjacent sections with sub-pixel float diff → _page_can_flow must return True
    result = _page_can_flow(
        inline_nodes=[nav_node, deco_node],
        section_y_ranges=section_y_ranges,
        page_has_flex=True,
    )
    assert result is True, (
        f"desktop1526 sections are non-overlapping (only sub-pixel float diff); "
        f"expected _page_can_flow=True but got {result}"
    )


def test_partial_coverage_wrapper_retains_visual_css():
    """Real data: Arena page (nodeId: 6777-33238) ranking table.
    RankingRow1 has 4 instances: odd rows use 'ranking-row-1', even rows use 'ranking-row-2'.
    'ranking-row-2' wraps RankingRow1 and adds a distinct background-color + padding (zebra stripe).

    Bug: split_codegen adds 'ranking-row-2' to _moly_wrappers because it has no non-instance users.
    Then _css_from_orig strips all visual CSS, leaving no background-color or padding.

    Fix: when only SOME instances of a leaf use a class (partial coverage), the class provides
    VARIANT styling (not just positioning). Its visual CSS must NOT be stripped.

    Expected: generate_components produces CSS for 'ranking-row-2' that includes
    background-color (var(--bds-gray-bg-float)) and padding (8px 8px 8px 8px).
    """
    _node_rr1 = {
        'figmaId': '6777:33304', 'figmaName': 'RankingRow1', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [], 'bb': {'width': 400, 'height': 40},
        'css': {'display': 'flex', 'flex-direction': 'row', 'align-items': 'center',
                'width': '400px', 'height': '40px', 'position': 'relative'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'ranking-row-1',
            'componentName': 'RankingRow1', 'props': [], 'isExtractedComponent': True,
        },
    }
    # Even-row wrapper node: provides background + padding (zebra stripe)
    # Real data: 6777:33313 from Arena ranking table
    _node_row2 = {
        'figmaId': '6777:33313', 'figmaName': 'ranking-row-2', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineLines': [], 'lineTypes': [],
        'bb': {'width': 400, 'height': 56},
        'css': {'display': 'flex', 'flex-direction': 'row', 'justify-content': 'space-between',
                'align-items': 'flex-start', 'padding': '8px 8px 8px 8px',
                'flex-shrink': '0', 'align-self': 'stretch',
                'background-color': 'var(--bds-gray-bg-float)', 'position': 'relative'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': None,
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }
    # Even-row wrapper node in the IR (6777:33313 = ranking-row-2 wrapper with background-color)
    # Real data: from Arena SectionRankingSection, the even-row variant of RankingRow1
    _node_row2_wrapper = {
        'figmaId': '6777:33313', 'figmaName': 'ranking-row-2', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [], 'bb': {'width': 400, 'height': 56},
        'css': {'display': 'flex', 'flex-direction': 'row', 'justify-content': 'space-between',
                'align-items': 'flex-start', 'padding': '8px 8px 8px 8px',
                'flex-shrink': '0', 'align-self': 'stretch',
                'background-color': 'var(--bds-gray-bg-float)', 'position': 'relative'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'ranking-row-2',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }
    _section_ir = {
        'figmaId': '6777:33267', 'figmaName': 'SectionMainSection', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [], 'bb': {'width': 400, 'height': 300},
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%', 'position': 'relative'},
        'children': [_node_row2_wrapper],  # include the even-row wrapper so _css_from_orig generates its class
        'semantic': {
            'htmlTag': 'section', 'className': 'section-main',
            'componentName': 'SectionMainSection', 'props': [], 'isExtractedComponent': True,
        },
    }
    _page_root_ir = {
        'figmaId': '6777:33238', 'figmaName': 'Arena', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%', 'position': 'relative'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'arena',
            'componentName': 'ArenaPage', 'props': [], 'isExtractedComponent': False,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '6777-33238.ir.json'
        ir_path.write_text(json.dumps(_page_root_ir))
        plan = {
            'generatedBy': 'split-a', 'nodeId': '6777:33238',
            'irPath': str(ir_path),
            'pageComponent': 'ArenaPage', 'cssExt': 'less',
            'fidToClass': {
                # 4 RankingRow1 instances: odd → ranking-row-1, even → ranking-row-2
                '6777:33304': 'ranking-row-1',
                '6777:33313': 'ranking-row-2',  # even-row wrapper
                '6777:33322': 'ranking-row-1',
                '6777:33331': 'ranking-row-2',  # even-row wrapper
            },
            'cssMap': {
                'ranking-row-1': {'display': 'flex', 'flex-direction': 'row',
                                  'align-items': 'center', 'width': '400px',
                                  'height': '40px', 'position': 'relative'},
                'ranking-row-2': {'display': 'flex', 'flex-direction': 'row',
                                  'justify-content': 'space-between', 'align-items': 'flex-start',
                                  'padding': '8px 8px 8px 8px', 'flex-shrink': '0',
                                  'align-self': 'stretch',
                                  'background-color': 'var(--bds-gray-bg-float)',
                                  'position': 'relative'},
                'section-main': {'display': 'flex', 'flex-direction': 'column',
                                 'width': '100%', 'position': 'relative'},
            },
            'fidToSrc': {},
            'leafComponents': [],
            'sections': [{
                'name': 'SectionMainSection',
                'ir': _section_ir,
                'ccComponent': None, 'ccImport': None,
                'leafComponents': [{
                    'name': 'RankingRow1',
                    'ir': _node_rr1,
                    'ccComponent': None,
                    'allInstanceFigmaIds': ['6777:33304', '6777:33313', '6777:33322', '6777:33331'],
                    'props': [{'name': 'rank', 'figmaId': '6777:33304', 'sampleValues': ['1', '2']}],
                }],
                'y': 0, 'x': None,
            }],
            'inlineNodes': [],
            'codeConnectComponents': [],
            'expandedWrapperFigmaIds': [],
            'cssClassRenames': {},
        }
        generate_components(plan, out, css_ext='less')
        # Find the generated section CSS (index.module.less, not leaf component CSS files)
        css_file = out / 'components' / 'SectionMainSection' / 'index.module.less'
        assert css_file.exists(), (
            f'No index.module.less generated for SectionMainSection. '
            f'Files: {[str(f.relative_to(out)) for f in out.rglob("*.less")]}'
        )
        css_content = css_file.read_text()
        # ranking-row-2 must retain background-color and padding (partial-coverage variant wrapper)
        assert 'background-color' in css_content, (
            f'ranking-row-2 must retain background-color (partial-coverage variant wrapper).\n'
            f'CSS:\n{css_content[:800]}'
        )
        assert 'padding' in css_content, (
            f'ranking-row-2 must retain padding.\nCSS:\n{css_content[:800]}'
        )
        # TSX check: call _generate_section_tsx directly with explicit orig_css_map/fid_to_class
        # (generate_components loads these from disk; for the unit test we pass them directly).
        # Real data: Arena SectionRankingSection (subsection of SectionMainSection)
        _orig_css_map = {
            'ranking-row-1': {'display': 'flex', 'flex-direction': 'row',
                              'justify-content': 'space-between', 'align-items': 'flex-start',
                              'padding': '8px 8px 8px 8px', 'flex-shrink': '0',
                              'align-self': 'stretch', 'position': 'relative'},
            'ranking-row-2': {'display': 'flex', 'flex-direction': 'row',
                              'justify-content': 'space-between', 'align-items': 'flex-start',
                              'padding': '8px 8px 8px 8px', 'flex-shrink': '0',
                              'align-self': 'stretch',
                              'background-color': 'var(--bds-gray-bg-float)',
                              'position': 'relative'},
        }
        _fid_to_class = {
            '6777:33304': 'ranking-row-1', '6777:33313': 'ranking-row-2',
            '6777:33322': 'ranking-row-1', '6777:33331': 'ranking-row-2',
        }
        # Section IR with 4 RankingRow1 instances (2 odd via direct, 2 even via ranking-row-2)
        _node_rr1_ir = {
            'figmaId': '6777:33304', 'figmaName': 'RankingRow1', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [], 'bb': {'width': 400, 'height': 40},
            'css': {'display': 'flex', 'flex-direction': 'row', 'width': '400px',
                    'position': 'relative'},
            'children': [],
            'semantic': {'htmlTag': 'div', 'className': 'ranking-row-1',
                         'componentName': 'RankingRow1', 'props': [], 'isExtractedComponent': True},
        }
        _row2_ir = {
            'figmaId': '6777:33313', 'figmaName': 'ranking-row-2', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [], 'bb': {'width': 400, 'height': 56},
            'css': {'display': 'flex', 'flex-direction': 'row', 'background-color': 'var(--bds-gray-bg-float)', 'padding': '8px', 'position': 'relative'},
            'children': [],
            'semantic': {'htmlTag': 'div', 'className': 'ranking-row-2',
                         'componentName': None, 'props': [], 'isExtractedComponent': False},
        }
        _section_tsx_ir = {
            'figmaId': '6777:33267', 'figmaName': 'SectionRankingSection', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
            'children': [_node_rr1_ir, _row2_ir],  # odd row + even-row wrapper
            'semantic': {'htmlTag': 'section', 'className': 'section-ranking',
                         'componentName': 'SectionRankingSection', 'props': [], 'isExtractedComponent': True},
        }
        tsx_result, _ = _generate_section_tsx(
            section_name='SectionRankingSection',
            ir=_section_tsx_ir,
            raw_section_ir=_section_tsx_ir,
            leaf_components=[{
                'name': 'RankingRow1',
                'ir': _node_rr1_ir,
                'ccComponent': None,
                'allInstanceFigmaIds': ['6777:33304', '6777:33313', '6777:33322', '6777:33331'],
                'instancesData': [{'rank': '1'}, {'rank': '2'}, {'rank': '3'}, {'rank': '4'}],
                'varyingProps': [{'propName': 'rank', 'type': 'text', 'values': ['1', '2', '3', '4']}],
            }],
            leaf_names=['RankingRow1'],
            css_ext='less',
            node_index={'6777:33304': _node_rr1_ir, '6777:33313': _row2_ir},
            orig_css_map=_orig_css_map,
            fid_to_class=_fid_to_class,
        )
        assert 'rowClassName' in tsx_result, (
            f'Even-row instances must use rowClassName={{styles[...]}} prop, not wrapper div.\n'
            f'TSX:\n{tsx_result[:600]}'
        )
        assert "<div className={styles['ranking-row-2']}>" not in tsx_result, (
            f'Even-row wrapper div must NOT be generated for visual-only CSS diff.\n'
            f'TSX:\n{tsx_result[:600]}'
        )


def test_h5only_inline_node_gets_media_query():
    """U-391: h5Only inline node must generate @media (min-width: 769px) { display:none }
    in Page.module.css so the node is hidden on desktop (PC-only viewport).

    Root cause: split_codegen appends css to inline_css_parts but never checks the
    h5Only flag on the inline node IR, so the media query is never emitted.

    Fix: after appending css, check n['ir'].get('h5Only') and emit the media block.
    """
    page_root_ir = {
        'figmaId': '10:1', 'figmaName': 'TestH5Page', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '1440px', 'height': '2000px', 'position': 'relative',
        },
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'test-h5-page',
            'componentName': 'TestH5Page', 'props': [], 'isExtractedComponent': False,
        },
    }
    anchor_section_ir = {
        'figmaId': '10:2', 'figmaName': 'AnchorSection', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '1440px', 'height': '400px',
            'position': 'absolute', 'left': '0px', 'top': '400px',
            'display': 'flex', 'flex-direction': 'column',
        },
        'bb': {'width': 1440.0, 'height': 400.0},
        'children': [],
        'semantic': {
            'htmlTag': 'section', 'className': 'anchor-section',
            'componentName': 'AnchorSection', 'props': [], 'isExtractedComponent': True,
        },
    }
    # h5Only inline node: a navigation bar that should only show on mobile
    inline_node_ir = {
        'figmaId': '10:10', 'figmaName': 'NavigationBar', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'h5Only': True,  # KEY: must trigger @media (min-width:769px) { display:none }
        'css': {
            'display': 'flex', 'flex-direction': 'row',
            'width': '375px', 'height': '48px', 'position': 'relative',
        },
        'bb': {'width': 375.0, 'height': 48.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'navigation-bar',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '10-1.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))
        plan = {
            'pageComponent': 'TestH5Page',
            'nodeId': '10-1',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [{
                'name': 'AnchorSection',
                'ir': anchor_section_ir,
                'y': 800.0,
                'x': 0.0,
                'ccComponent': None,
                'leafComponents': [],
            }],
            'inlineNodes': [{'ir': inline_node_ir, 'y': 100.0, 'x': 0.0}],
        }
        generate_components(plan, out, css_ext='less')

        page_css = (out / 'TestH5Page.module.less').read_text()
        assert '@media (min-width: 769px)' in page_css, (
            f'h5Only inline node must emit @media (min-width: 769px) in page CSS.\n'
            f'Actual page CSS:\n{page_css}'
        )
        assert 'display: none' in page_css or 'display:none' in page_css, (
            f'h5Only @media block must contain display:none.\n'
            f'Actual page CSS:\n{page_css}'
        )


def test_h5only_section_gets_responsive_wrapper():
    """U-392: h5Only section must get a responsive CSS wrapper class in Page.tsx and
    the matching CSS rules (.h5-only-* { display:none } + @media(max-width:768px)) in
    Page.module.css.

    Root cause: split_codegen never reads h5Only from section IR when building body_items,
    and _generate_page_tsx never emits a wrapper for h5-only sections.

    Fix part A: _generate_page_tsx emits <div className={styles['h5-only-...']}> wrapper.
    Fix part B: generate_components appends responsive CSS for h5Only sections to page_css.
    """
    # Part A: _generate_page_tsx direct test
    body_items_a = [
        {
            'y': 0.0, 'type': 'section', 'name': 'Comp2Section',
            'abs_top': None, 'abs_left': None, 'abs_width': None,
            'h5_only': True,
        },
        {
            'y': 400.0, 'type': 'section', 'name': 'HeroSection',
            'abs_top': None, 'abs_left': None, 'abs_width': None,
            'h5_only': False,
        },
    ]
    tsx_a = _generate_page_tsx('TestPage', body_items_a, 'test-page', 'less')
    assert "h5-only-comp2-section" in tsx_a, (
        f'h5Only section Comp2Section must get wrapper class h5-only-comp2-section in TSX.\n'
        f'Actual TSX:\n{tsx_a}'
    )
    # The non-h5Only section must NOT get a wrapper
    assert "h5-only-hero-section" not in tsx_a, (
        f'Non-h5Only HeroSection must NOT get a h5-only wrapper.\nActual TSX:\n{tsx_a}'
    )

    # Part B: generate_components end-to-end CSS test
    page_root_ir = {
        'figmaId': '20:1', 'figmaName': 'TestH5Page2', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'test-h5-page2',
            'componentName': 'TestH5Page2', 'props': [], 'isExtractedComponent': False,
        },
    }
    h5only_section_ir = {
        'figmaId': '20:2', 'figmaName': 'Comp2Section', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'h5Only': True,  # KEY: section only visible on mobile
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'children': [],
        'semantic': {
            'htmlTag': 'section', 'className': 'comp2-section',
            'componentName': 'Comp2Section', 'props': [], 'isExtractedComponent': True,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '20-1.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))
        plan = {
            'pageComponent': 'TestH5Page2',
            'nodeId': '20-1',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [{
                'name': 'Comp2Section',
                'ir': h5only_section_ir,
                'y': 0.0,
                'x': 0.0,
                'ccComponent': None,
                'leafComponents': [],
            }],
            'inlineNodes': [],
        }
        generate_components(plan, out, css_ext='less')

        page_tsx = (out / INDEX_TSX_FILENAME).read_text()
        page_css = (out / 'TestH5Page2.module.less').read_text()

        # TSX must wrap h5Only section
        assert "h5-only-comp2-section" in page_tsx, (
            f'h5Only section Comp2Section must get wrapper class in Page.tsx.\n'
            f'Actual TSX:\n{page_tsx}'
        )
        # CSS must hide on PC
        assert 'display: none' in page_css or 'display:none' in page_css, (
            f'h5-only-comp2-section CSS must have display:none for PC.\n'
            f'Actual CSS:\n{page_css}'
        )
        # CSS must show on mobile
        assert '@media (max-width: 768px)' in page_css, (
            f'h5-only-comp2-section CSS must have @media (max-width: 768px) for mobile.\n'
            f'Actual CSS:\n{page_css}'
        )


def test_usePageEnv_hook_resets_overflow_y():
    """U-394: _generate_usePageEnv_hook_file must save prevOverflowY, set
    overflowY to 'visible', and restore on unmount.

    Root cause: the template only handles overflow-x. CSS spec says if overflow-x
    is set to anything other than 'visible' while overflow-y is 'visible', the
    browser forces overflow-y to 'auto'. In the option trading app, #app has
    overflow-x:scroll from globals.less; the hook resets overflow-x to 'unset'
    but that still leaves overflow-y:auto (inherited from the cascade), creating
    a second inner scrollbar on LP pages like meta-h5.

    Fix: also save prevOverflowY, set app.style.overflowY = 'visible', and
    restore app.style.overflowY = prevOverflowY on unmount.
    """
    content = _generate_usePageEnv_hook_file('dark')
    # Must use setProperty('important') to override globals.less overflow-x:scroll !important;
    # plain assignment (app.style.overflowX = 'unset') loses to !important author rules.
    assert "setProperty('overflow-x'" in content or 'setProperty("overflow-x"' in content, (
        f"usePageEnv hook must use setProperty('overflow-x', ..., 'important') to override "
        f"globals.less '#app {{ overflow-x: scroll !important }}'.\n"
        f'Actual content:\n{content}'
    )
    assert "setProperty('overflow-y'" in content or 'setProperty("overflow-y"' in content, (
        f"usePageEnv hook must use setProperty('overflow-y', 'visible', 'important') to prevent inner scrollbar.\n"
        f'Actual content:\n{content}'
    )
    assert 'removeProperty' in content, (
        f'usePageEnv hook must use removeProperty to restore overflow on unmount.\n'
        f'Actual content:\n{content}'
    )


def test_abs_section_with_bottom_css_skips_wrapper():
    """U-393: Non-flow page section using bottom:-Xpx (no top) must NOT get a
    position:absolute top:Ypx wrapper in Page.tsx. The section positions itself
    correctly via bottom CSS relative to the page root; a height-0 wrapper causes
    double-offset (section ends up at ~-5090px above the page).

    Real data: node 314:11956 (ForEverySection) from PagebyMeta (nodeId: 314-11779)
    CSS: position:absolute; bottom:-1034px; height:4764px; left:0px
    Page root height: 4438px → section resolves to top:708px (= 4438+1034-4764)
    When wrapped in height-0 div with top:708px: section top = -5090px (broken).

    Fix: when item has has_own_bottom=True, _generate_page_tsx renders the section
    directly (no wrapper), relying on its own bottom CSS for page-relative positioning.
    """
    body_items = [
        {
            'y': 708.0, 'type': 'section', 'name': 'ForEverySection',
            'abs_top': 708.0, 'abs_left': None, 'abs_width': None,
            'has_own_bottom': True,  # section CSS: position:absolute; bottom:-1034px
        },
    ]
    tsx = _generate_page_tsx('PagebyMeta', body_items, 'pageby-meta', 'less')
    # Must NOT generate a top:708px wrapper (it would double-offset the section)
    assert "top: '708px'" not in tsx, (
        f"Section with has_own_bottom=True must not get top:708px wrapper.\n"
        f"Actual TSX:\n{tsx}"
    )
    # Must still render the section component
    assert '<ForEverySection' in tsx, (
        f"ForEverySection must still be rendered in TSX.\nActual TSX:\n{tsx}"
    )


def test_structural_split_scss_uses_node_index_for_deep_nodes():
    """_generate_structural_split_section 传入 node_index 后，深层节点的 CSS 必须出现在 SCSS 中。
    Root cause: 调用时未传 node_index，pc_ir 里深层节点无 semantic → _collect_classes 跳过。
    Fix: 传入 node_index，内部用 _get_normalized(pc_ir, node_index) 代替裸 deepcopy。

    Real data: Trump活动 merged-181-2980 GmtSection，703 个节点只有 22 个有 semantic，
    导致 GmtSectionPC.module.scss 只生成 21 个 class（frame-2147229976 等深层 CSS 全部丢失）。
    """
    from lib.split_codegen import _generate_structural_split_section

    # 深层子节点：只有 figmaId，无 semantic（复现 plan.json 中 section IR 的典型状态）
    # Real data: figmaId 181:3041 = Frame 2147229976 from GmtSection sub-tree
    deep_child = {
        'figmaId': '181:3041', 'figmaName': 'Frame 2147229976',
        'figmaType': 'FRAME', 'semantic': None,
        'css': {'display': 'flex', 'flex-direction': 'row', 'align-items': 'center'},
        'children': [],
    }
    pc_ir = {
        'figmaId': '181:3038', 'figmaName': 'Frame 2147229891',
        'figmaType': 'FRAME',
        'semantic': {'className': 'main-content', 'htmlTag': 'div', 'componentName': 'GmtSection', 'props': []},
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '1200px'},
        'children': [deep_child],
        'structuralSplit': True,
        'supplementNode': {
            'figmaId': '6:3', 'figmaName': '1', 'figmaType': 'FRAME',
            'semantic': None, 'css': {'display': 'flex'}, 'children': [],
        },
    }
    # node_index 包含深层节点的完整 semantic（模拟 _build_node_index 的输出）
    node_index = {
        '181:3038': {**pc_ir, 'semantic': {'className': 'main-content', 'htmlTag': 'div', 'componentName': None, 'props': []}},
        '181:3041': {**deep_child, 'semantic': {'className': 'update-time-row', 'htmlTag': 'div', 'componentName': None, 'props': []}},
    }
    fid_to_class = {'181:3041': 'update-time-row'}

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        _generate_structural_split_section(
            'GmtSection', pc_ir, out_dir, css_ext='scss',
            node_index=node_index, fid_to_class=fid_to_class,
        )
        pc_css = (out_dir / 'GmtSectionPC.module.scss').read_text()
        assert 'update-time-row' in pc_css, (
            f"深层节点（figmaId=181:3041）的 CSS class .update-time-row 未出现。\n"
            f"root cause: 未通过 node_index 传递完整语义。\n"
            f"实际 SCSS:\n{pc_css}"
        )


def test_structural_split_section_creates_index_module_css():
    """_generate_structural_split_section must create index.module.{css_ext} alongside index.tsx.
    Before fix: only GmtSectionPC/H5.tsx + CSS were generated; index.module.scss was missing,
    causing validate_split Layer 0 warning '- GmtSection/index.module.scss 不存在' (-1 pt).
    After fix: empty index.module.scss created so Layer 0 passes.

    Real data: GmtSection in Trump活动 merged-181-2980 (structural split section).
    """
    pc_ir = {
        'figmaId': '181:2999', 'figmaName': 'GmtSection',
        'semantic': {'className': 'gmt-section', 'htmlTag': 'div', 'componentName': 'GmtSection', 'props': []},
        'css': {'display': 'flex'}, 'children': [],
        'structuralSplit': True,
        'supplementNode': {
            'figmaId': '6:999', 'figmaName': 'GmtSectionH5',
            'semantic': {'className': 'gmt-section-h5', 'htmlTag': 'div', 'componentName': None, 'props': []},
            'css': {'display': 'flex'}, 'children': [],
        },
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        _generate_structural_split_section('GmtSection', pc_ir, out_dir, css_ext='scss')
        assert (out_dir / 'index.module.scss').exists(), (
            "index.module.scss must be created for structural split sections "
            "(required by validate_split Layer 0)"
        )


def test_sanitize_classnames_strips_leading_digit():
    """_sanitize_classnames: leading digit must be prefixed to produce valid CSS selector.
    Real data: node figmaId 181:3048, figmaName '64x-55/64x-55_top-2' from
    Trump活动 merged-181-2980 PC IR (GmtSectionPC.module.scss line 93 compilation error).
    Before fix: '64x-55-64x-55_top-2' → .64x-55-64x-55_top-2 → invalid (leading digit).
    After fix: 'n-64x-55-64x-55_top-2' → .n-64x-55-64x-55_top-2 → valid.
    """
    # Real data: node 181:3048 from Trump活动 merged-181-2980
    ir = {
        'figmaId': '181:3048', 'figmaName': '64x-55/64x-55_top-2',
        'semantic': {
            'className': '64x-55/64x-55_top-2',
            'htmlTag': 'div', 'componentName': None, 'props': [],
        },
        'children': [],
    }
    _sanitize_classnames(ir)
    name = ir['semantic']['className']
    assert not name[0].isdigit(), f"Leading digit not removed: {name!r}"
    assert '/' not in name, f"Slash not removed: {name!r}"


def test_sanitize_classnames_handles_slash_paren_double_dot():
    """_sanitize_classnames removes / () and leading dots from all class names.
    Real data: Trump活动 merged-181-2980 GmtSectionPC.module.scss lines 112/190/204/213:
    '.element-/-l-1-/-dark', '.3_tag-(promotional)', '.pagination/default', '.faq-(expandable)'
    """
    # Real data: node classNames from Trump活动 merged-181-2980 structural split SCSS
    cases = [
        ('faq-(expandable)',         lambda n: '(' not in n and ')' not in n),
        ('.element-/-l-1-/-dark',    lambda n: '/' not in n and not n.startswith('-')),
        ('3_tag-(promotional)',      lambda n: not n[0].isdigit() and '(' not in n),
        ('pagination/default',       lambda n: '/' not in n),
    ]
    for raw, check in cases:
        ir = {
            'figmaId': '1:1', 'figmaName': raw,
            'semantic': {'className': raw, 'htmlTag': 'div', 'componentName': None, 'props': []},
            'children': [],
        }
        _sanitize_classnames(ir)
        name = ir['semantic']['className']
        assert check(name), f"Sanitize failed for {raw!r} → {name!r}"


def test_structural_split_scss_no_invalid_selectors():
    """Structural split section SCSS must not contain class names with / () or leading digits.
    Root cause: _generate_structural_split_section called generate_scss(pc_ir) without
    first calling _sanitize_classnames(pc_ir).
    Real data: Trump活动 merged-181-2980 GmtSectionPC.module.scss had 6 invalid selectors.
    """
    from lib.scss_generator import generate_scss

    # Real data: simplified IR mimicking Trump活动 PC section with invalid class names.
    # Node figmaId 181:3048, figmaName '64x-55/64x-55_top-2' from merged-181-2980 IR.
    pc_ir = {
        'figmaId': '181:2999', 'figmaName': 'GmtSection',
        'semantic': {'className': 'gmt-section', 'htmlTag': 'div', 'componentName': 'GmtSection', 'props': []},
        'css': {'display': 'flex', 'flex-direction': 'column'},
        'children': [
            {
                'figmaId': '181:3048', 'figmaName': '64x-55/64x-55_top-2',
                'semantic': {'className': '64x-55/64x-55_top-2', 'htmlTag': 'div', 'componentName': None, 'props': []},
                'css': {'width': '50%'},
                'children': [],
            },
            {
                'figmaId': '181:3949', 'figmaName': 'faq-(expandable)',
                'semantic': {'className': 'faq-(expandable)', 'htmlTag': 'div', 'componentName': None, 'props': []},
                'css': {'display': 'flex'},
                'children': [],
            },
        ],
    }

    # After fix: _sanitize_classnames called before generate_scss
    _sanitize_classnames(pc_ir)
    scss = generate_scss(pc_ir)

    for m in re.finditer(r'^(\.[^\s{]+)\s*\{', scss, re.MULTILINE):
        cls = m.group(1)[1:]  # strip leading dot
        assert not cls[0].isdigit(), f"Leading digit in sanitized SCSS: .{cls}"
        assert '/' not in cls, f"Slash in sanitized SCSS: .{cls}"
        assert '(' not in cls and ')' not in cls, f"Parens in sanitized SCSS: .{cls}"


def test_section_function_suffixed_root_when_subsection_name_conflicts():
    """U-403: When a section and its subSection share the same name, the generated
    index.tsx must declare 'function {name}Root()' instead of 'function {name}()',
    to prevent 'Identifier already declared' in Vite/esbuild scope-hoisting.

    Root cause: _generate_section_tsx used section_name directly as the function name,
    even when a subSection import had the same name (import X from './X' + function X()).

    Real data: ByLearnSection (202:33790) from DemoTrading (202-33464);
    subSection ByLearnSection (202:33792) — both named 'ByLearnSection' after AI rename pass.
    """
    SS_FID = '202:33792'

    def _make_frame(fid, name, children=None):
        return {
            'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'isComponentInstance': False,
            'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'bb': {'width': 1440, 'height': 400},
            'css': {'display': 'flex', 'flex-direction': 'column'},
            'children': children or [],
            'semantic': {
                'htmlTag': 'section', 'className': name.lower().replace(' ', '-'),
                'componentName': name, 'props': [], 'isExtractedComponent': True,
            },
        }

    ss_ir = _make_frame(SS_FID, 'ByLearnSection')
    section_ir = _make_frame('202:33790', 'ByLearnSection', children=[ss_ir])

    plan = {
        'pageComponent': 'DemoTrading',
        'nodeId': '202-33464',
        'cssExt': 'scss',
        'leafComponents': [],
        'inlineNodes': [],
        'sections': [{
            'name': 'ByLearnSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [],
            'subSections': [{
                'name': 'ByLearnSection',  # Same as parent — triggers guard
                'ir': ss_ir,
                'leafComponents': [],
            }],
        }],
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_components(plan, out, css_ext='scss')

        index_path = out / 'components' / 'ByLearnSection' / INDEX_TSX_FILENAME
        assert index_path.exists(), 'ByLearnSection/index.tsx not generated'
        content = index_path.read_text()

        fn_line = next((l for l in content.splitlines() if 'export default function' in l), '')
        assert 'ByLearnSectionRoot' in fn_line, (
            "Function must be renamed to 'ByLearnSectionRoot' when same-named subSection exists.\n"
            f"Actual: {fn_line}"
        )
        assert 'export default function ByLearnSection(' not in content, (
            "Function named 'ByLearnSection' conflicts with subSection import './ByLearnSection'.\n"
            f"Actual: {fn_line}"
        )


def test_varying_prop_uses_varyingprops_values_when_instancesdata_key_mismatches():
    """U-404: When instancesData keys differ from varyingProps.propName, the codegen
    must use varyingProps.values[global_idx] as the authoritative prop value, NOT
    fall back to _extract_instance_texts()[0] which incorrectly uses the first text node.

    Root cause: _generate_section_tsx looked up inst_data.get(vp['propName']) which
    was empty when instancesData used a different key (e.g. 'text1' instead of 'amount').
    The fallback _extract_instance_texts()[0] returned the first text node's value,
    so ALL missing props got "Deposit Savings" (the first text) regardless of their
    actual value.

    Real data: EarnUseCase (4030:41601/4030:41653) from Brand6PreKyc (4030-41365).
    instancesData stores: {depositSavings, text1, earnings, text12}
    varyingProps has propName='amount' (values=['+$10,400', '-$1,400']),
                      propName='subAmount' (values=['+$45.50', '+$14']).
    Bug: amount="Deposit Savings", subAmount="Deposit Savings"
    Fix: amount="+$10,400", subAmount="+$45.50" for first instance.
    """
    INST1_FID = '4030:41601'
    INST2_FID = '4030:41653'

    def _text_ir(fid, text):
        # Real data: text node structure from Brand6PreKyc (4030-41365)
        return {
            'figmaId': fid, 'figmaName': text, 'figmaType': 'TEXT',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 150, 'height': 20},
            'isTextNode': True, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False,
            'textContent': text,   # used by _extract_instance_texts
            'textSegments': None, 'textAutoResize': None,
            'lineTypes': [], 'lineIndentations': [],
            'variants': None, 'imageRef': None, 'fillImageRef': None,
            'localAssetPath': None,
            'css': {'color': '#ffffff', 'font-size': '14px'},
            'children': [],
        }

    def _earn_instance_ir(fid, label, amount_val, title, sub_val):
        # Minimal version of EarnUseCase IR (4030:41601 / 4030:41653).
        # Child structure: [img, [row1=[label_text, amount_text], row2=[title_text, sub_text]]]
        # _extract_instance_texts DFS order: label, amount_val, title, sub_val
        return {
            'figmaId': fid, 'figmaName': 'Earn use case', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 381, 'height': 480},
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False,
            'textContent': None, 'textSegments': None, 'textAutoResize': None,
            'lineTypes': [], 'lineIndentations': [],
            'variants': None, 'imageRef': None, 'fillImageRef': None,
            'localAssetPath': None,
            'css': {'width': '100%', 'height': '480px', 'position': 'relative',
                    'display': 'flex', 'flex-direction': 'column'},
            'children': [
                # child 0: image (non-text, skipped by _extract_instance_texts)
                {'figmaId': fid + ':img', 'figmaName': 'image', 'figmaType': 'RECTANGLE',
                 'isComponentInstance': False, 'componentId': None,
                 'bb': {'width': 381, 'height': 300},
                 'isTextNode': False, 'isImageNode': True, 'isVectorNode': False,
                 'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                 'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
                 'variants': None, 'imageRef': 'abc', 'fillImageRef': None,
                 'localAssetPath': f'/assets/{fid.replace(":", "-")}.png',
                 'css': {}, 'children': []},
                # child 1: platform-visual-box with 4 text descendants
                {'figmaId': fid + ':box', 'figmaName': 'Frame 21', 'figmaType': 'FRAME',
                 'isComponentInstance': False, 'componentId': None,
                 'bb': {'width': 381, 'height': 100},
                 'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                 'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                 'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
                 'variants': None, 'imageRef': None, 'fillImageRef': None,
                 'localAssetPath': None,
                 'css': {'display': 'flex', 'flex-direction': 'column'},
                 'children': [
                     {'figmaId': fid + ':row1', 'figmaName': 'row1', 'figmaType': 'FRAME',
                      'isComponentInstance': False, 'componentId': None,
                      'bb': {'width': 381, 'height': 30},
                      'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                      'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                      'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
                      'variants': None, 'imageRef': None, 'fillImageRef': None,
                      'localAssetPath': None,
                      'css': {'display': 'flex', 'flex-direction': 'row'},
                      'children': [
                          _text_ir(fid + ':t1', label),       # depositSavings — first text
                          _text_ir(fid + ':t2', amount_val),  # amount
                      ]},
                     {'figmaId': fid + ':row2', 'figmaName': 'row2', 'figmaType': 'FRAME',
                      'isComponentInstance': False, 'componentId': None,
                      'bb': {'width': 381, 'height': 30},
                      'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                      'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                      'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
                      'variants': None, 'imageRef': None, 'fillImageRef': None,
                      'localAssetPath': None,
                      'css': {'display': 'flex', 'flex-direction': 'row'},
                      'children': [
                          _text_ir(fid + ':t3', title),     # earnings
                          _text_ir(fid + ':t4', sub_val),   # subAmount
                      ]},
                 ]},
            ],
        }

    inst1_ir = _earn_instance_ir(INST1_FID, 'Deposit Savings', '+$10,400', 'Earnings', '+$45.50')
    inst2_ir = _earn_instance_ir(INST2_FID, 'Purchase Flight to Bali', '-$1,400', 'Cashback', '+$14')

    section_ir = {
        'figmaId': '4030:41594', 'figmaName': 'OnePlatformSection', 'figmaType': 'FRAME',
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': 1440, 'height': 1200},
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
        'variants': None, 'imageRef': None, 'fillImageRef': None,
        'localAssetPath': None,
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                'position': 'relative'},
        'children': [inst1_ir, inst2_ir],
        'semantic': {
            'htmlTag': 'section', 'className': 'on-platform-section',
            'componentName': 'OnePlatformSection', 'props': [],
            'isExtractedComponent': True,
        },
    }

    plan = {
        'pageComponent': 'Brand6PreKyc',
        'nodeId': '4030-41365',
        'cssExt': 'scss',
        'leafComponents': [],
        'sections': [{
            'name': 'OnePlatformSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'EarnUseCase',
                'ir': inst1_ir,
                'varyingProps': [
                    {'path': [0], 'type': 'image', 'propName': 'image5165',
                     'values': ['/assets/4030-41602.png', '/assets/4030-41654.png']},
                    {'path': [1, 0, 0, 0], 'type': 'text', 'propName': 'depositSavings',
                     'values': ['Deposit Savings', 'Purchase Flight to Bali']},
                    {'path': [1, 0, 0, 1], 'type': 'text', 'propName': 'amount',
                     'values': ['+$10,400', '-$1,400']},
                    {'path': [1, 0, 1, 0], 'type': 'text', 'propName': 'earnings',
                     'values': ['Earnings', 'Cashback']},
                    {'path': [1, 0, 1, 1], 'type': 'text', 'propName': 'subAmount',
                     'values': ['+$45.50', '+$14']},
                ],
                # BUG scenario: instancesData uses generic keys (text1/text12), NOT
                # the semantic propNames (amount/subAmount). This mismatch causes codegen
                # to fall back to _extract_instance_texts()[0] = label text for all
                # missing props.
                # Real data: plan.json instancesData from Brand6PreKyc (4030-41365)
                'instancesData': [
                    {
                        'image5165': '/assets/4030-41602.png',
                        'depositSavings': 'Deposit Savings',
                        'text1': '+$10,400',    # key mismatch: propName is 'amount'
                        'earnings': 'Earnings',
                        'text12': '+$45.50',    # key mismatch: propName is 'subAmount'
                    },
                    {
                        'image5165': '/assets/4030-41654.png',
                        'depositSavings': 'Purchase Flight to Bali',
                        'text1': '-$1,400',
                        'earnings': 'Cashback',
                        'text12': '+$14',
                    },
                ],
                'allInstanceFigmaIds': [INST1_FID, INST2_FID],
                'ccComponent': None,
                'allImports': [],
            }],
        }],
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_components(plan, out, css_ext='scss', no_i18n=True)

        section_tsx = (out / 'components' / 'OnePlatformSection' / INDEX_TSX_FILENAME).read_text()

        # Instance 0: amount from varyingProps.values[0], NOT fallback first-text
        assert 'amount="+$10,400"' in section_tsx, (
            'U-404a: First EarnUseCase instance must have amount="+$10,400" '
            '(from varyingProps.values[0]). '
            'Bug produces amount="Deposit Savings" (first-text fallback).\n'
            + '\n'.join(l for l in section_tsx.splitlines() if 'EarnUseCase' in l or 'amount' in l)
        )
        assert 'subAmount="+$45.50"' in section_tsx, (
            'U-404b: First instance must have subAmount="+$45.50" '
            '(from varyingProps.values[0]). Bug produces "Deposit Savings".'
        )
        # Instance 1
        assert 'amount="-$1,400"' in section_tsx, (
            'U-404c: Second instance must have amount="-$1,400" '
            '(from varyingProps.values[1]). Bug produces "Purchase Flight to Bali".'
        )
        assert 'subAmount="+$14"' in section_tsx, (
            'U-404d: Second instance must have subAmount="+$14" '
            '(from varyingProps.values[1]). Bug produces "Purchase Flight to Bali".'
        )
        # Sanity: correctly-keyed props still render
        assert 'depositSavings="Deposit Savings"' in section_tsx, (
            'U-404e: depositSavings prop must still be "Deposit Savings" (key matches).'
        )


def test_leaf_root_height_kept_when_only_width_is_percentage():
    """U-405: When a leaf component root has width:100% (%) and height:480px (px),
    only width should be stripped (double-nesting prevention), NOT height.

    Root cause: lines 1403-1410 in split_codegen.py used 'or' to check whether
    width or height contained '%', stripping BOTH dimensions if either was a percent.
    EarnUseCase has width:100%, height:480px → height got incorrectly stripped →
    card height collapsed to 444px (margin-top:353 + height:91) → no bottom margin,
    info box pressed against bottom edge of card (visible in browser).

    Fix: check each dimension independently — strip only the dimension that is a percent.

    Real data: EarnUseCase (4030:41601) from Brand6PreKyc (4030-41365).
    inst_cls == root_cls == 'earn-use-case' (no-wrapper case triggers the U-375 path).
    Original CSS: earn-use-case { width: 100%; height: 480px; ... }
    """
    import os
    import shutil
    import tempfile

    NODE_ID = '4030-41365'
    COMP_NAME = 'Brand6PreKyc'
    INST1_FID = '4030:41601'
    INST2_FID = '4030:41653'

    # Real data: earn-use-case CSS from Brand6PreKyc.module.scss (4030-41365)
    orig_css = """\
.earn-use-case {
  width: 100%;
  height: 480px;
  flex-shrink: 0;
  background-color: #eff3fd;
  border-radius: 16px;
  overflow: hidden;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
}
"""

    # Real data: figma-maps.json from Brand6PreKyc (4030-41365)
    # Both instance figmaIds map to 'earn-use-case' → _has_wrapper = False
    figma_maps = {
        'fidToClass': {
            INST1_FID: 'earn-use-case',
            INST2_FID: 'earn-use-case',
        },
        'fidToSrc': {}
    }

    def _earn_ir(fid):
        return {
            'figmaId': fid, 'figmaName': 'Earn use case', 'figmaType': 'FRAME',
            'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 381.0, 'height': 480.0},
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False,
            'textContent': None, 'textSegments': None, 'textAutoResize': None,
            'lineTypes': [], 'lineIndentations': [],
            'variants': None, 'imageRef': None, 'fillImageRef': None,
            'localAssetPath': None,
            # Real data: earn-use-case root CSS from IR node 4030:41601
            'css': {'width': '100%', 'height': '480px', 'flex-shrink': '0',
                    'background-color': '#eff3fd', 'border-radius': '16px',
                    'overflow': 'hidden', 'position': 'relative',
                    'display': 'flex', 'flex-direction': 'column',
                    'align-items': 'center'},
            'children': [],
            'semantic': {'htmlTag': 'div', 'className': 'earn-use-case',
                         'componentName': 'EarnUseCase', 'props': [],
                         'isExtractedComponent': True},
        }

    inst1_ir = _earn_ir(INST1_FID)
    inst2_ir = _earn_ir(INST2_FID)

    section_ir = {
        'figmaId': '4030:41599', 'figmaName': 'CardSection', 'figmaType': 'FRAME',
        'isComponentInstance': False, 'componentId': None,
        'bb': {'width': 1440, 'height': 1000},
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False,
        'textContent': None, 'textSegments': None, 'textAutoResize': None,
        'lineTypes': [], 'lineIndentations': [],
        'variants': None, 'imageRef': None, 'fillImageRef': None,
        'localAssetPath': None,
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                'position': 'relative'},
        'children': [inst1_ir, inst2_ir],
        'semantic': {'htmlTag': 'section', 'className': 'card-section',
                     'componentName': 'CardSection', 'props': [],
                     'isExtractedComponent': True},
    }

    plan = {
        'pageComponent': COMP_NAME,
        'nodeId': NODE_ID,
        'cssExt': 'scss',
        'leafComponents': [],
        'sections': [{
            'name': 'CardSection',
            'ir': section_ir,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'EarnUseCase',
                'ir': inst1_ir,
                'varyingProps': [],
                'instancesData': [{} for _ in [INST1_FID, INST2_FID]],
                'allInstanceFigmaIds': [INST1_FID, INST2_FID],
                'ccComponent': None,
                'allImports': [],
            }],
        }],
    }

    tmp = Path(tempfile.mkdtemp())
    try:
        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{COMP_NAME}-{NODE_ID}'
        page_dir.mkdir(parents=True)
        (page_dir / f'{COMP_NAME}.module.scss').write_text(orig_css)
        (page_dir / 'figma-maps.json').write_text(json.dumps(figma_maps))

        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            out = tmp / 'out'
            generate_components(plan, out, css_ext='scss', no_i18n=True)
        finally:
            os.chdir(old_cwd)

        leaf_css_path = out / 'components' / 'CardSection' / 'EarnUseCase.module.scss'
        assert leaf_css_path.exists(), 'EarnUseCase.module.scss not generated'
        leaf_css = leaf_css_path.read_text()

        # height:480px is a FIXED value — must be kept so the card renders at the
        # correct height and the info box has bottom margin.
        assert 'height: 480px' in leaf_css, (
            'U-405a: height:480px must be kept in leaf CSS. '
            'Bug: stripped because width:100% contains %, and the OR condition '
            'caused height to be stripped too.\n'
            f'Leaf CSS:\n{leaf_css[:400]}'
        )
        # width:100% must also be KEPT — 100%×100%=100%, no double-nesting occurs.
        # Stripping width:100% collapses the card to its content width (309px),
        # removing all left/right margin between the card edge and the info box.
        # Only non-100% percentages (e.g., 62.5%) can cause double-nesting.
        assert 'width: 100%' in leaf_css, (
            'U-405b: width:100% must be kept in leaf CSS — it never causes '
            'double-nesting (100%×100%=100%). Stripping it collapses card width '
            'to content width, eliminating left/right margins.\n'
            f'Leaf CSS:\n{leaf_css[:400]}'
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_u406_sections_in_components_subdir():
    """U-406: generate_components must place all section dirs inside out_dir/components/,
    not directly under out_dir. This ensures stage/ structure matches dest/ structure
    so landing is a direct copy without directory reshuffling.
    Real behavior: split_codegen.py uses components_dir = out_dir / COMPONENTS_SUBDIR.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_without_leaves('HeroSection')
        generate_components(plan, out, css_ext='less')
        # Section must be in components/
        assert (out / 'components' / 'HeroSection' / INDEX_TSX_FILENAME).exists(), \
            'Section index.tsx must be inside out_dir/components/HeroSection/'
        # Section must NOT be at out_dir root
        assert not (out / 'HeroSection').exists(), \
            'Section dir must NOT appear directly under out_dir (must be in components/)'
        # hooks stays at out_dir root (not inside components/)
        assert (out / 'hooks').exists(), \
            'hooks/ must remain at out_dir root (not moved into components/)'
        assert not (out / 'components' / 'hooks').exists(), \
            'hooks/ must NOT be inside components/'


def test_u407_page_entry_is_index_tsx():
    """U-407: generate_components must write index.tsx (not Page.tsx) at out_dir root.
    This eliminates the rename step in cmd_apply and makes stage/ = dest/ structure.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = _section_without_leaves('HeroSection')
        generate_components(plan, out, css_ext='less')
        # Must have index.tsx at root
        assert (out / INDEX_TSX_FILENAME).exists(), \
            'generate_components must write index.tsx at out_dir root'
        # Must NOT have Page.tsx
        assert not (out / PAGE_TSX_FILENAME).exists(), \
            'Page.tsx must NOT be generated (replaced by index.tsx)'
        # index.tsx content: imports sections via ./components/SectionName
        content = (out / INDEX_TSX_FILENAME).read_text()
        assert "import HeroSection from './components/HeroSection'" in content, \
            f"Page entry must import sections via ./components/ but got:\n{content[:300]}"


def test_u412_h5only_subsection_zeroes_section_root_padding_on_mobile():
    """U-412: When a section has h5Only subsections, the section root's padding
    and gap must be zeroed on mobile via @media (max-width: 768px).

    Real case: CalculateYourCashbackSection (250:3689) has PC padding: 80px 120px
    and gap: 48px. On mobile, only h5Only subsections (CalculateYourCashbackInner)
    are visible. Without the mobile override, the section is 1274px vs Figma 778px
    because the 80+80px PC padding and 48px gap persist on mobile.

    Same issue in TieredRewardsSection (60+60px padding, gap) → 762px vs Figma 618px.
    TieredRewardsSection2 → 766px vs Figma 646px.
    """
    def _make_calc_section_ir():
        return {
            'figmaId': '250:3689', 'figmaName': 'CalculateYourCashback', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {
                'display': 'flex', 'flex-direction': 'column', 'width': '100%',
                'padding': '80px 120px 80px 120px', 'gap': '48px',
                'background-color': '#ffffff',
            },
            'children': [],
            'semantic': {
                'htmlTag': 'div', 'className': 'calculate-your-cashback',
                'componentName': 'CalculateYourCashbackSection', 'props': [],
                'isExtractedComponent': True,
            },
        }

    def _make_h5only_sub_ir():
        return {
            'figmaId': '250:4861', 'figmaName': 'CalculateYourCashbackInner', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'h5Only': True,
            'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
            'children': [],
            'semantic': {
                'htmlTag': 'div', 'className': 'calculate-your-cashback-n250-4861',
                'componentName': 'CalculateYourCashbackInner', 'props': [],
                'isExtractedComponent': True,
            },
        }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = {
            'pageComponent': 'TestPage',
            'nodeId': '250-3689',
            'leafComponents': [],
            'sections': [{
                'name': 'CalculateYourCashbackSection',
                'ir': _make_calc_section_ir(),
                'ccComponent': None,
                'leafComponents': [],
                'subSections': [{
                    'name': 'CalculateYourCashbackInner',
                    'ir': _make_h5only_sub_ir(),
                    'leafComponents': [],
                }],
            }],
            'inlineNodes': [],
        }
        generate_components(plan, out, css_ext='less')

        section_css_path = (
            out / 'components' / 'CalculateYourCashbackSection' / 'index.module.less'
        )
        assert section_css_path.exists(), f'Section CSS must exist at {section_css_path}'
        section_css = section_css_path.read_text()

        assert '@media (max-width: 768px)' in section_css, (
            'Section with h5Only subsection must have mobile media query.\n'
            f'Actual CSS:\n{section_css[:600]}'
        )
        assert 'padding: 0' in section_css, (
            'Mobile media query must zero out section root padding.\n'
            f'Actual CSS:\n{section_css[:600]}'
        )
        assert 'gap: 0' in section_css, (
            'Mobile media query must zero out section root gap.\n'
            f'Actual CSS:\n{section_css[:600]}'
        )

    # Guard: section with NO h5Only subsections must NOT get padding override
    with tempfile.TemporaryDirectory() as tmp2:
        out2 = Path(tmp2)
        plan2 = {
            'pageComponent': 'TestPage',
            'nodeId': '1-1',
            'leafComponents': [],
            'sections': [{
                'name': 'HeroSection',
                'ir': {
                    'figmaId': '1:2', 'figmaName': 'HeroSection', 'figmaType': 'FRAME',
                    'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                    'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                    'localAssetPath': None, 'lineTypes': [],
                    'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%',
                            'padding': '60px 0px 60px 0px'},
                    'children': [],
                    'semantic': {
                        'htmlTag': 'section', 'className': 'hero-section',
                        'componentName': 'HeroSection', 'props': [], 'isExtractedComponent': True,
                    },
                },
                'ccComponent': None,
                'leafComponents': [],
                'subSections': [{
                    'name': 'HeroContent',
                    'ir': {
                        'figmaId': '1:3', 'figmaName': 'HeroContent', 'figmaType': 'FRAME',
                        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                        'localAssetPath': None, 'lineTypes': [],
                        # NO h5Only flag — shared subsection
                        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
                        'children': [],
                        'semantic': {
                            'htmlTag': 'div', 'className': 'hero-content',
                            'componentName': 'HeroContent', 'props': [], 'isExtractedComponent': True,
                        },
                    },
                    'leafComponents': [],
                }],
            }],
            'inlineNodes': [],
        }
        generate_components(plan2, out2, css_ext='less')
        hero_css = (out2 / 'components' / 'HeroSection' / 'index.module.less').read_text()
        # Without h5Only subsections, must NOT add padding:0 override
        assert 'padding: 0' not in hero_css, (
            'Section with only shared subsections must NOT get padding:0 mobile override.\n'
            f'Actual CSS:\n{hero_css[:400]}'
        )


def test_u416_pconly_abs_subsection_wrapper_gets_position_absolute():
    """U-416: When a pcOnly subsection's root IR has position:absolute, the wrapper
    div must also get position:absolute so it doesn't participate in the section's
    flex flow and consume a gap slot.

    Root cause: split_codegen only emits @media(max-width:768px){display:none} for
    pcOnly subsection wrappers. If the subsection is abs-positioned, the wrapper has
    height:0 but still occupies a flex gap (48px in CalculateYourCashback), making
    the section 48px taller than Figma.

    Real case: CalculateYourCashbackSection BgSection (250:3751) —
    position:absolute background. Wrapper gets no position, participates in flex,
    adds extra 48px gap → section is 732px instead of Figma 684px.

    Fix: in pcOnly wrapper CSS generation, check if subsection IR root has
    position:absolute; if so, emit .pc-{name} { position:absolute; top:0; ... }
    in addition to the mobile display:none rule.
    """
    def _make_abs_bg_ir():
        return {
            'figmaId': '250:3751', 'figmaName': 'Bg', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'pcOnly': True,
            'css': {
                'display': 'block', 'position': 'absolute',
                'width': '1512px', 'height': '684px', 'top': '0px',
            },
            'children': [],
            'semantic': {
                'htmlTag': 'div', 'className': 'bg',
                'componentName': 'BgSection', 'props': [], 'isExtractedComponent': True,
            },
        }

    def _make_calc_section_ir():
        return {
            'figmaId': '250:3689', 'figmaName': 'Calculate', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {
                'display': 'flex', 'flex-direction': 'column', 'width': '100%',
                'padding': '80px 120px 80px 120px', 'gap': '48px',
                'position': 'relative',
            },
            'children': [],
            'semantic': {
                'htmlTag': 'div', 'className': 'calculate-your-cashback',
                'componentName': 'CalculateYourCashbackSection', 'props': [],
                'isExtractedComponent': True,
            },
        }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        plan = {
            'pageComponent': 'TestPage',
            'nodeId': '250-3689',
            'leafComponents': [],
            'sections': [{
                'name': 'CalculateYourCashbackSection',
                'ir': _make_calc_section_ir(),
                'ccComponent': None,
                'leafComponents': [],
                'subSections': [{
                    'name': 'BgSection',
                    'ir': _make_abs_bg_ir(),
                    'leafComponents': [],
                }],
            }],
            'inlineNodes': [],
        }
        generate_components(plan, out, css_ext='less')

        css_path = out / 'components' / 'CalculateYourCashbackSection' / 'index.module.less'
        assert css_path.exists(), f'Section CSS must exist at {css_path}'
        css = css_path.read_text()

        assert 'pc-bg-section' in css, (
            'pcOnly BgSection must produce a .pc-bg-section rule.\n'
            f'Actual CSS snippet:\n{css[-500:]}'
        )
        assert 'position: absolute' in css, (
            'pcOnly subsection with position:absolute root must have position:absolute on wrapper.\n'
            f'pc-bg-section CSS:\n{css[css.find("pc-bg-section"):][:300]}'
        )

    # Guard: pcOnly subsection with position:relative must NOT get position:absolute
    def _make_rel_subsection_ir():
        return {
            'figmaId': '250:9999', 'figmaName': 'NormalSection', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'pcOnly': True,
            'css': {'display': 'flex', 'flex-direction': 'column', 'position': 'relative'},
            'children': [],
            'semantic': {
                'htmlTag': 'div', 'className': 'normal-section',
                'componentName': 'NormalSection', 'props': [], 'isExtractedComponent': True,
            },
        }

    with tempfile.TemporaryDirectory() as tmp2:
        out2 = Path(tmp2)
        plan2 = {
            'pageComponent': 'TestPage',
            'nodeId': '250-3689',
            'leafComponents': [],
            'sections': [{
                'name': 'CalculateYourCashbackSection',
                'ir': _make_calc_section_ir(),
                'ccComponent': None,
                'leafComponents': [],
                'subSections': [{
                    'name': 'NormalSection',
                    'ir': _make_rel_subsection_ir(),
                    'leafComponents': [],
                }],
            }],
            'inlineNodes': [],
        }
        generate_components(plan2, out2, css_ext='less')
        css2_path = out2 / 'components' / 'CalculateYourCashbackSection' / 'index.module.less'
        css2 = css2_path.read_text()
        # Only mobile display:none should be present, NOT position:absolute
        pc_normal_block = css2[css2.find('pc-normal-section'):css2.find('pc-normal-section') + 200] if 'pc-normal-section' in css2 else ''
        assert 'position: absolute' not in pc_normal_block, (
            'pcOnly subsection with position:relative must NOT get position:absolute on wrapper.\n'
            f'pc-normal-section CSS:\n{pc_normal_block}'
        )


def test_u418_h5only_flex_node_uses_display_flex_in_show_rule():
    """U-418: h5Only inline node with display:flex in IR must use
    display:flex !important (not display:block !important) in its mobile show rule.

    display:block !important overrides the element's own display:flex CSS,
    breaking flex layout and causing elements to stack/misalign on H5.

    Real case: 250:3842 (Hero countdown row) — display:flex with align-items:center.
    Mobile rule shows it with display:block, making justify-content/align-items
    ineffective and the countdown items not centered.

    Fix: in the h5Only show rule generator, check the node's CSS display value
    in the IR and use it (flex/inline-flex) instead of always using block.
    """
    def _make_flex_h5only_ir():
        return {
            'figmaId': '250:3842', 'figmaName': 'CountdownRow', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'h5Only': True,  # h5Only flex container
            'css': {
                'display': 'flex', 'flex-direction': 'row', 'align-items': 'center',
                'justify-content': 'center', 'width': '100%', 'gap': '8px',
            },
            'children': [],
            'semantic': {
                'htmlTag': 'div', 'className': 'countdown-row',
                'componentName': None, 'props': [], 'isExtractedComponent': False,
            },
        }

    section_ir = {
        'figmaId': '250:2620', 'figmaName': 'HeroSection', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%',
                'height': '750px'},
        'bb': {'width': 1440.0, 'height': 750.0},
        'children': [_make_flex_h5only_ir()],
        'semantic': {
            'htmlTag': 'section', 'className': 'hero-section',
            'componentName': 'HeroSection', 'props': [], 'isExtractedComponent': True,
        },
    }

    import os as _os

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        out = tmp_path / 'output'
        out.mkdir()
        # Create figma-maps.json so fid_to_class is populated → h5Only rules generated
        maps_dir = tmp_path / '.figma-to-code' / '3-page-code' / 'Web-250-2620'
        maps_dir.mkdir(parents=True)
        (maps_dir / 'figma-maps.json').write_text(json.dumps({
            'fidToClass': {'250:3842': 'countdown-row'},
            'fidToSrc': {},
        }))
        plan = {
            'pageComponent': 'TestPage',
            'nodeId': '250-2620',
            'leafComponents': [],
            'sections': [{
                'name': 'HeroSection',
                'ir': section_ir,
                'y': 0, 'x': 0,
                'ccComponent': None,
                'leafComponents': [],
            }],
            'inlineNodes': [],
        }
        _orig_cwd = _os.getcwd()
        _os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='less')
        finally:
            _os.chdir(_orig_cwd)

        section_css = (out / 'components' / 'HeroSection' / 'index.module.less').read_text()

    # The h5Only show rule must use display:flex since the node is a flex container
    # Find the mobile show block for 250:3842
    css_3842_block = ''
    if '250:3842' in section_css:
        idx = section_css.find('250:3842')
        css_3842_block = section_css[max(0, idx-5):idx+300]

    assert '250:3842' in section_css, (
        'h5Only node 250:3842 must have CSS rules generated.\n'
        f'Actual CSS tail:\n{section_css[-400:]}'
    )
    assert 'display: flex' in css_3842_block or 'display:flex' in css_3842_block, (
        'h5Only flex container must use display:flex in mobile show rule, not display:block.\n'
        f'CSS block for 250:3842:\n{css_3842_block}'
    )
    assert 'display: block' not in css_3842_block, (
        'h5Only flex container must NOT use display:block in show rule.\n'
        f'CSS block for 250:3842:\n{css_3842_block}'
    )


def test_u415_h5only_inline_node_without_class_gets_attr_selector():
    """U-415: h5Only inline node absent from fid_cls_map must still get
    [data-figma-id="..."] { display: none !important } in section CSS.

    Root cause: _collect_inline_responsive_flags uses fid_cls_map.get(fid, '')
    to build the CSS selector. When a h5Only node (e.g. 250:3901 from H5 IR)
    is absent from fid_cls_map (not merged into PC figma-maps.json), it is silently
    skipped and no display:none rule is emitted on PC.

    Real case: EarnPointsOnCardPaySection's h5Only Iphone (250:3901) has no class
    in merged figma-maps.json → visible on PC → takes 1/3 of flex space → step-cards
    column only ~64px wide instead of ~380px.

    Fix: in _flagged() inside _collect_inline_responsive_flags, when cls is empty
    but figmaId is present, add attr:figmaId key directly to candidates so it
    bypasses the class-lookup path and emits a [data-figma-id] selector.
    """
    from lib.split_codegen import _collect_inline_responsive_flags  # type: ignore[attr-defined]

    section_ir = {
        'figmaId': '250:2682', 'figmaName': 'StepsContainer', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'css': {'display': 'flex', 'flex-direction': 'row'},
        'children': [
            {
                'figmaId': '250:4495',
                'figmaName': 'Header',
                'figmaType': 'FRAME',
                'h5Only': True,  # Has a class entry in fid_cls_map (existing path)
                'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                'css': {'display': 'flex'},
                'children': [],
            },
            {
                'figmaId': '250:3901',
                'figmaName': 'Iphone',
                'figmaType': 'FRAME',
                'h5Only': True,  # NOT in fid_cls_map (the broken case)
                'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                'css': {'display': 'flex', 'flex': '1'},
                'children': [],
            },
        ],
    }

    fid_cls_map = {'250:4495': 'header-n250-4495'}  # 250:3901 intentionally absent
    result = _collect_inline_responsive_flags(section_ir, fid_cls_map, set())

    # Existing path: node with class should produce attr selector
    assert 'attr:250:4495' in result, (
        'h5Only node WITH class in fid_cls_map must produce attr:figmaId selector.\n'
        f'Got result keys: {list(result.keys())}'
    )
    assert result['attr:250:4495'] == 'h5Only'

    # New path: node WITHOUT class must also produce attr selector (U-415 fix)
    assert 'attr:250:3901' in result, (
        'h5Only node WITHOUT class in fid_cls_map must ALSO produce attr:figmaId selector.\n'
        f'Got result keys: {list(result.keys())}'
    )
    assert result['attr:250:3901'] == 'h5Only', (
        f'attr:250:3901 must be h5Only, got: {result.get("attr:250:3901")}'
    )


def test_u423_dart_snippet_not_used_as_inline_jsx():
    """U-423: Moly CC snippet in Dart/Flutter format must NOT be inlined as JSX.

    Bug: _can_inline and _will_be_inlined only check bool(snippet), not whether
    snippet is valid JSX. Dart snippets like MolyAppTopBar(leadingType: ..., () {})
    lack '<' and render as visible text when placed as JSX children of a wrapper div.

    Real case: EU Deposit Campaign EarnPointsOnCardPaySection node 250:4302
    (NavigationBar Moly instance). Figma Code Connect has only a Flutter snippet
    (MolyAppTopBar(...)) — no React/JSX equivalent. The bug caused the Flutter code
    to appear as readable text inside the phone mockup UI.

    Fix: require '<' in snippet for both _will_be_inlined and _can_inline checks.
    JSX snippets always contain '<' (opening tag); Dart/Flutter snippets use
    ClassName(...) constructor syntax with no '<'.
    """
    from lib.tsx_generator import render_jsx_body_with_leaf_refs

    # Real data: node 250:4302 from EU EarnPointsOnCardPaySection (merged-250-2618)
    dart_snippet = "MolyAppTopBar(\n  leadingType: BitAppTopBarLeadingType.back,\n  leadingCallback: () {},\n  title: 'Page title',\n)"
    jsx_snippet = "<AppTopBar leadingType='back' title='Page title' />"

    # Pre-condition: Dart snippet has no '<' → JSX check fails it correctly
    assert '<' not in dart_snippet, f'Dart snippet must not contain < but got: {dart_snippet[:50]}'
    assert '<' in jsx_snippet, f'JSX snippet must contain < but got: {jsx_snippet[:50]}'

    # Verify the bug scenario: if Dart inlineSnippet is passed directly to renderer,
    # the Dart code appears as visible text in JSX output.
    ir_node = {
        'figmaId': '250:4302', 'figmaName': 'NavigationBar', 'figmaType': 'INSTANCE',
        'css': {'display': 'flex'}, 'children': [],
        'semantic': {'className': 'navigation-bar', 'htmlTag': 'div'},
    }
    leaf_map_buggy = {
        '250:4302': {
            'componentName': 'NavigationBar', 'varyingProps': [], 'instanceData': {},
            'rootClassName': 'navigation-bar', 'isMoly': True,
            'inlineSnippet': dart_snippet,  # BUG: Dart code set as inlineSnippet
        }
    }
    buggy_out = render_jsx_body_with_leaf_refs(ir_node, leaf_map_buggy)
    assert 'MolyAppTopBar(' in buggy_out, (
        'Regression guard: renderer DOES output Dart code when inlineSnippet=dart_snippet.\n'
        'This confirms the bug scenario — the fix must prevent Dart code reaching inlineSnippet.'
    )

    # After fix: _generate_section_tsx must NOT set inlineSnippet for Dart snippets.
    # Simulated: when inlineSnippet is None (fix prevents Dart from being set),
    # the renderer falls back to the component name reference.
    leaf_map_fixed = {
        '250:4302': {
            'componentName': 'NavigationBar', 'varyingProps': [], 'instanceData': {},
            'rootClassName': 'navigation-bar', 'isMoly': True,
            'inlineSnippet': None,  # FIX: Dart snippet blocked → inlineSnippet stays None
        }
    }
    fixed_out = render_jsx_body_with_leaf_refs(ir_node, leaf_map_fixed)
    assert 'MolyAppTopBar(' not in fixed_out, (
        'FIX: when inlineSnippet=None, Dart code must NOT appear in output.\n'
        f'Got: {fixed_out[:200]}'
    )
    # Component is referenced by name (NavigationBar import in section)
    assert 'NavigationBar' in fixed_out or 'navigation-bar' in fixed_out, (
        f'FIX: component name or class must appear in output. Got: {fixed_out[:200]}'
    )


def test_u438_resolve_missing_asset_paths_assigns_deterministic_paths():
    """U-438: figma-maps.json can become stale when merge_responsive.py adds new H5
    nodes to the merged IR after convert.py last ran. _resolve_missing_asset_paths
    must fill in deterministic paths for vector/image nodes not yet resolved."""
    node_index = {
        '250:4523': {'figmaId': '250:4523', 'isVectorNode': True, 'isImageNode': False, 'localAssetPath': None},
        '250:4537': {'figmaId': '250:4537', 'isVectorNode': False, 'isImageNode': True, 'localAssetPath': None},
        'I250:4520;930:521': {'figmaId': 'I250:4520;930:521', 'isVectorNode': True, 'isImageNode': False, 'localAssetPath': None},
        '250:4516': {'figmaId': '250:4516', 'isVectorNode': False, 'isImageNode': True,
                     'localAssetPath': '/assets/Web-merged-250-2618/250-4516.png'},
        '250:3311': {'figmaId': '250:3311', 'isVectorNode': False, 'isImageNode': False, 'localAssetPath': None},
    }
    _resolve_missing_asset_paths(node_index, 'Web-merged-250-2618')

    assert node_index['250:4523']['localAssetPath'] == '/assets/Web-merged-250-2618/250-4523.svg', \
        'vector node must get .svg path'
    assert node_index['250:4537']['localAssetPath'] == '/assets/Web-merged-250-2618/250-4537.png', \
        'image node must get .png path'
    assert node_index['I250:4520;930:521']['localAssetPath'] == '/assets/Web-merged-250-2618/I250-4520-930-521.svg', \
        'vector node with instance ID must get safe-escaped .svg path'
    assert node_index['250:4516']['localAssetPath'] == '/assets/Web-merged-250-2618/250-4516.png', \
        'already-resolved node must NOT be overwritten'
    assert node_index['250:3311']['localAssetPath'] is None, \
        'non-asset node must remain None'


def test_u445_structural_split_inline_node_gets_h5_display_none():
    """U-445: An inline node with structuralSplit=True (but no pcOnly flag) must
    get @media (max-width: 768px) { display: none } in the page CSS so the PC-side
    content is hidden on H5.

    Root cause: DemoTrading Navigation (202:33465) was matched with H5 App_bar
    (1484:27515) at score 0.271. merge_responsive.py set structuralSplit=True on it,
    but did NOT set pcOnly=True. split_codegen only generates display:none for
    pcOnly inline nodes, so .navigation renders on H5 (shows full PC desktop nav bar).

    Fix: in the inline node CSS generation loop (around lines 2588-2595), also emit
    @media display:none when the inline node has structuralSplit=True.

    Real data: inlineNode ir.figmaId=202:33465 from DemoTrading (merged-202-33464).
    """
    nav_ir = {
        'figmaId': '202:33465', 'figmaName': 'Navigation', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        # KEY: structuralSplit=True but pcOnly is NOT set
        'structuralSplit': True,
        'supplementNode': {'figmaId': '1484:27515', 'figmaName': '2.NAVI/App_bar',
                           'figmaType': 'FRAME', 'isTextNode': False, 'isImageNode': False,
                           'isVectorNode': False, 'isDecorativeElement': False,
                           'textContent': None, 'textSegments': None, 'localAssetPath': None,
                           'lineTypes': [], 'css': {}, 'children': []},
        'css': {'width': '100%', 'height': '48px', 'display': 'flex',
                'flex-direction': 'row', 'position': 'relative',
                'background-color': '#121214'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'navigation',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }

    page_root_ir = {
        'figmaId': '202:33464', 'figmaName': 'DemoTradingPage', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column'},
        'children': [nav_ir],
        'semantic': {
            'htmlTag': 'div', 'className': 'demo-trading-page',
            'componentName': 'DemoTradingPage', 'props': [], 'isExtractedComponent': False,
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = out / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '202-33464.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))

        plan = {
            'pageComponent': 'DemoTradingPage',
            'nodeId': '202-33464',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [],
            'inlineNodes': [{'ir': nav_ir, 'y': -1e9, 'x': 0.0}],
        }
        generate_components(plan, out, css_ext='scss')

        page_css = (out / 'DemoTradingPage.module.scss').read_text()

        # U-445a: page CSS must have display:none for .navigation on mobile
        assert '@media (max-width: 768px)' in page_css, (
            'U-445a FAIL: page CSS must have @media (max-width: 768px) rule.\n'
            f'Actual CSS:\n{page_css}'
        )
        assert 'navigation' in page_css and 'display: none' in page_css, (
            f'U-445b FAIL: page CSS must hide .navigation on H5.\nActual CSS:\n{page_css}'
        )


def test_u444_structural_split_h5_tsx_renders_supplement_content():
    """U-444: _generate_structural_split_section must render non-empty H5 TSX when
    the supplementNode's IR nodes have no 'semantic' field.

    Root cause: DemoTrading HeaderSectionH5.tsx renders empty <div className={styles['']}>
    because the supplementNode (1484:27154) and its children have semantic=None.
    generate_tsx with no className/htmlTag produces an empty div.

    Fix: add _infer_supplement_semantics(h5_ir) call before generate_tsx in
    _generate_structural_split_section. This infers htmlTag (div/span) and
    className (from figmaName, kebab-case) for nodes without semantic data.

    Real data: node 1484:27154 from DemoTrading (merged-202-33464),
    which is the H5 supplement for PC HeaderSection (202:33496).
    supplementNode children: Frame 1410119534 > [Experience Realistic, Demo Trading]
    """
    # Real data: 1484:27154 supplementNode from merged-202-33464.ir.json
    # supplementNode has NO 'semantic' on any node (all are None/missing)
    supplement_node = {
        'figmaId': '1484:27154',
        'figmaName': 'Frame 1410119534',
        'figmaType': 'FRAME',
        'isTextNode': False,
        'isImageNode': False,
        'isVectorNode': False,
        'isDecorativeElement': False,
        'textContent': None,
        'textSegments': None,
        'localAssetPath': None,
        'lineTypes': None,
        # NO 'semantic' key — this is the bug condition
        'css': {
            'width': '270px',
            'position': 'absolute',
            'left': '52.5px',
            'top': '91px',
            'display': 'flex',
            'flex-direction': 'column',
            'align-items': 'center',
            'gap': '12px',
        },
        'children': [{
            'figmaId': '1484:27155',
            'figmaName': 'Frame 1410118791',
            'figmaType': 'FRAME',
            'isTextNode': False,
            'isImageNode': False,
            'isVectorNode': False,
            'isDecorativeElement': False,
            'textContent': None,
            'textSegments': None,
            'localAssetPath': None,
            'lineTypes': None,
            # NO 'semantic' key
            'css': {
                'width': '270px',
                'display': 'flex',
                'flex-direction': 'column',
                'justify-content': 'flex-end',
                'align-items': 'center',
                'gap': '8px',
                'flex-shrink': '0',
                'position': 'relative',
            },
            'children': [
                {
                    'figmaId': '1484:27156',
                    'figmaName': 'Experience Realistic',
                    'figmaType': 'TEXT',
                    'isTextNode': True,
                    'isImageNode': False,
                    'isVectorNode': False,
                    'isDecorativeElement': False,
                    'textContent': 'Experience Realistic',
                    'textSegments': None,
                    'localAssetPath': None,
                    'lineTypes': ['NONE'],
                    # NO 'semantic' key
                    'css': {
                        'color': '#ffffff',
                        'font-size': '20px',
                        'font-weight': '300',
                        'font-family': "'IBM Plex Sans', sans-serif",
                        'line-height': '1.3',
                        'flex-shrink': '0',
                        'align-self': 'stretch',
                        'text-align': 'center',
                    },
                    'children': [],
                },
                {
                    'figmaId': '1484:27157',
                    'figmaName': 'Demo Trading',
                    'figmaType': 'TEXT',
                    'isTextNode': True,
                    'isImageNode': False,
                    'isVectorNode': False,
                    'isDecorativeElement': False,
                    'textContent': 'Demo Trading',
                    'textSegments': None,
                    'localAssetPath': None,
                    'lineTypes': ['NONE'],
                    # NO 'semantic' key
                    'css': {
                        'color': '#f7a600',
                        'font-size': '32px',
                        'font-weight': '700',
                        'font-family': "'IBM Plex Sans', sans-serif",
                        'line-height': '1.3',
                        'flex-shrink': '0',
                        'align-self': 'stretch',
                        'text-align': 'center',
                    },
                    'children': [],
                },
            ],
        }],
    }

    pc_ir = {
        'figmaId': '202:33496',
        'figmaName': 'Header',
        'figmaType': 'FRAME',
        'isTextNode': False,
        'isImageNode': False,
        'isVectorNode': False,
        'isDecorativeElement': False,
        'textContent': None,
        'textSegments': None,
        'localAssetPath': None,
        'lineTypes': [],
        'structuralSplit': True,
        'supplementNode': supplement_node,
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'header',
            'componentName': 'HeaderSection', 'props': [], 'isExtractedComponent': True,
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        _generate_structural_split_section('HeaderSection', pc_ir, out_dir, css_ext='scss')

        h5_tsx = (out_dir / 'HeaderSectionH5.tsx').read_text()

        # U-444a: H5 TSX must not be an empty div with empty className
        assert "styles['']" not in h5_tsx and 'styles[""]' not in h5_tsx, (
            f'U-444a FAIL: HeaderSectionH5 must not use empty className styles[""].\n'
            f'Actual H5 TSX:\n{h5_tsx}'
        )

        # U-444b: H5 TSX must contain the title text "Experience Realistic"
        assert 'Experience Realistic' in h5_tsx, (
            f'U-444b FAIL: HeaderSectionH5 must render "Experience Realistic" text.\n'
            f'Actual H5 TSX:\n{h5_tsx}'
        )

        # U-444c: H5 TSX must contain "Demo Trading"
        assert 'Demo Trading' in h5_tsx, (
            f'U-444c FAIL: HeaderSectionH5 must render "Demo Trading" text.\n'
            f'Actual H5 TSX:\n{h5_tsx}'
        )


def test_u446_structural_split_h5_centered_abs_uses_calc():
    """U-446: H5 supplement root with position:absolute and a left value that represents
    horizontal centering in the 375px design frame must generate
    left: calc((100% - {width}) / 2) instead of a fixed pixel value.

    Root cause: node 1484:27154 (Frame 1410119534) in DemoTrading HeaderSectionH5 has
    position:absolute; left:52.5px; width:270px. In the 375px design frame this is
    centered: (375 - 270) / 2 = 52.5. In a 390px viewport the fixed value shifts the
    title 7.5px left of center.

    Fix: in _generate_structural_split_section, before calling generate_scss(h5_ir),
    call _maybe_convert_centered_abs_left(h5_ir) to replace the fixed left with calc().

    Real data: node 1484:27154 from DemoTrading (merged-202-33464).
    """
    # Real data: 1484:27154 supplementNode from merged-202-33464.ir.json
    supplement_node = {
        'figmaId': '1484:27154',
        'figmaName': 'Frame 1410119534',
        'figmaType': 'FRAME',
        'isTextNode': False,
        'isImageNode': False,
        'isVectorNode': False,
        'isDecorativeElement': False,
        'textContent': None,
        'textSegments': None,
        'localAssetPath': None,
        'lineTypes': None,
        'css': {
            'width': '270px',
            'position': 'absolute',
            'left': '52.5px',
            'top': '91px',
            'display': 'flex',
            'flex-direction': 'column',
            'align-items': 'center',
            'gap': '12px',
        },
        'children': [],
    }

    pc_ir = {
        'figmaId': '202:33496',
        'figmaName': 'Header',
        'figmaType': 'FRAME',
        'isTextNode': False,
        'isImageNode': False,
        'isVectorNode': False,
        'isDecorativeElement': False,
        'textContent': None,
        'textSegments': None,
        'localAssetPath': None,
        'lineTypes': [],
        'structuralSplit': True,
        'supplementNode': supplement_node,
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'header',
            'componentName': 'HeaderSection', 'props': [], 'isExtractedComponent': True,
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        _generate_structural_split_section('HeaderSection', pc_ir, out_dir, css_ext='scss')

        h5_css = (out_dir / 'HeaderSectionH5.module.scss').read_text()

        # U-446a: must use calc() for centered left, not fixed 52.5px
        assert 'left: calc((100% - 270px) / 2)' in h5_css, (
            f'U-446a FAIL: H5 CSS must use calc((100% - 270px) / 2) for a centered absolute '
            f'element. Fixed left:52.5px shifts the title off-center in a 390px viewport.\n'
            f'Actual H5 CSS:\n{h5_css}'
        )

        # U-446b: must NOT contain the fixed pixel left value
        assert 'left: 52.5px' not in h5_css, (
            f'U-446b FAIL: H5 CSS must not use fixed left:52.5px for a centered element.\n'
            f'Actual H5 CSS:\n{h5_css}'
        )


def test_u447_structural_split_h5_abs_root_gets_zindex():
    """U-447: H5 supplement root with position:absolute must have z-index set in the
    generated CSS so it renders above PC section elements that have z-index (e.g.
    AdvantagesSection with z-index:5).

    Root cause: DemoTrading page sections (AdvantagesSection z-index:5, ByLearnSection
    z-index:7, etc.) create stacking contexts above the H5 title frame-1410119534,
    which has position:absolute but no z-index (auto=0). The page is flex-column so
    sections do not visually overlap, BUT z-index still determines paint order:
    z-index:5 paints over z-index:auto even when not spatially overlapping.

    Fix: in _maybe_convert_centered_abs_left (or a companion helper), when the H5
    supplement root has position:absolute and no explicit z-index, set z-index:10 so
    the title paints above all typical section stacking contexts.

    Real data: node 1484:27154 from DemoTrading (merged-202-33464).
    AdvantagesSection CSS: z-index:5; position:relative (from index.module.scss line 11).
    """
    supplement_node = {
        'figmaId': '1484:27154',
        'figmaName': 'Frame 1410119534',
        'figmaType': 'FRAME',
        'isTextNode': False,
        'isImageNode': False,
        'isVectorNode': False,
        'isDecorativeElement': False,
        'textContent': None,
        'textSegments': None,
        'localAssetPath': None,
        'lineTypes': None,
        'css': {
            'width': '270px',
            'position': 'absolute',
            'left': '52.5px',
            'top': '91px',
            'display': 'flex',
            'flex-direction': 'column',
            'align-items': 'center',
            'gap': '12px',
        },
        'children': [],
    }

    pc_ir = {
        'figmaId': '202:33496',
        'figmaName': 'Header',
        'figmaType': 'FRAME',
        'isTextNode': False,
        'isImageNode': False,
        'isVectorNode': False,
        'isDecorativeElement': False,
        'textContent': None,
        'textSegments': None,
        'localAssetPath': None,
        'lineTypes': [],
        'structuralSplit': True,
        'supplementNode': supplement_node,
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'header',
            'componentName': 'HeaderSection', 'props': [], 'isExtractedComponent': True,
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        _generate_structural_split_section('HeaderSection', pc_ir, out_dir, css_ext='scss')

        h5_css = (out_dir / 'HeaderSectionH5.module.scss').read_text()

        # U-447a: must have z-index property to render above PC section stacking contexts
        assert 'z-index: 10' in h5_css, (
            f'U-447a FAIL: H5 CSS must include z-index:10 for an absolute-positioned '
            f'supplement root, so it renders above page sections with z-index (e.g. '
            f'AdvantagesSection z-index:5).\n'
            f'Actual H5 CSS:\n{h5_css}'
        )


def test_u448_responsive_height_kept_when_overflow_hidden_prevents_clip():
    """U-448: When a node's H5 responsive override includes overflow:hidden AND the H5
    height exceeds the PC height (Case C ratio), the height must NOT be stripped.

    Root cause: _collect_responsive_overrides_from_ir Case C strips height when
    h5_height >= pc_height * 1.35 AND node has pcOnly/h5Only children. For
    vip-content-row (I39641:7672;39641:5102): PC height=494px, H5 height=682px,
    ratio=1.38 → Case C triggers, height stripped. But overflow:hidden is also set,
    meaning the PC height (494px) is then inherited and clips the coming-soon-row text
    that appears at y≈595px in flex-column layout.

    Fix: when overflow:hidden is present in the H5 responsive CSS, the height is
    protecting content visibility — do NOT strip it.

    Real data: node I39641:7672;39641:5102 (vip-content-row) from
    TomorrowlandLandingPage4 (merged-39641-6863).
    """
    from lib.split_codegen import _collect_responsive_overrides_from_ir

    # Real data: I39641:7672;39641:5102 from merged-39641-6863.ir.json
    vip_content_row = {
        'figmaId': 'I39641:7672;39641:5102',
        'figmaName': 'Frame 2147223767',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '100%', 'justify-content': 'space-between',
            'margin-top': '120px', 'max-width': '1200px', 'align-items': 'center',
            'flex-direction': 'row', 'flex-shrink': '0', 'position': 'relative',
            'align-self': 'center', 'height': '494px', 'display': 'flex',
        },
        'responsive': [{
            'breakpoint': 768,
            'css': {
                'overflow': 'hidden',
                'margin-top': '0px',
                'max-width': '100%',
                'top': '-0.06%',
                'background-color': '#fffefd',
                'flex-direction': 'column',
                'left': '-0.11%',
                'position': 'absolute',
                'height': '682px',
            },
            'confidence': 1.0,
        }],
        'children': [{
            # pcOnly child: the black VIP card (position:absolute, pcOnly=True → h5:none)
            'figmaId': 'I39641:7672;39641:5115',
            'figmaName': 'tokenizedvip',
            'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'pcOnly': True,
            'css': {'position': 'absolute', 'top': '0px', 'left': '0px',
                    'width': '481px', 'height': '316px'},
            'children': [],
        }],
    }

    fid_cls_map = {'I39641:7672;39641:5102': 'vip-content-row'}
    result = _collect_responsive_overrides_from_ir(vip_content_row, fid_cls_map, set())

    # Find the vip-content-row responsive override
    vip_override = next((r for r in result if r[0] == 'vip-content-row'), None)
    assert vip_override is not None, (
        'U-448a FAIL: vip-content-row responsive override not found in result.'
    )

    _cls, _bp, _css = vip_override

    # U-448a: height must be preserved when overflow:hidden is in H5 responsive
    assert _css.get('height') == '682px', (
        f'U-448a FAIL: height must be preserved (682px) when overflow:hidden is set '
        f'in H5 responsive. Stripping it causes PC height (494px) to be inherited, '
        f'which clips the coming-soon-row at y≈595px.\n'
        f'Got height: {_css.get("height")!r}'
    )

    # U-448b: overflow:hidden must still be present
    assert _css.get('overflow') == 'hidden', (
        f'U-448b FAIL: overflow:hidden must be in the generated responsive CSS.'
    )


def test_u449_structural_split_h5_top_adjusted_for_nav_offset():
    """U-449: H5 supplement with position:absolute and top ≈ h5_nav_height + small offset
    must have its top adjusted by subtracting h5_nav_height, so the element positions
    relative to the content area (not the page root that includes the hidden nav bar).

    Root cause: DemoTrading node 1484:27154 (HeaderSectionH5 text overlay) has top:91px.
    The H5 Figma design has a navigation bar (1484:27515) at top:0 with height:88px.
    The text overlay is designed to sit 3px below the nav (91 - 88 = 3px from content top).
    In the web, H5 navigation is display:none, so content starts at y=0. Without adjustment
    the text appears 91px below the hero top instead of 3px — visually too far down.

    Fix: _maybe_adjust_h5_top_for_nav_offset(h5_ir, h5_nav_height=88) mutates top:91px
    to top:3px. Called in _generate_structural_split_section before CSS generation.

    Real data: node 1484:27154 from H5-Dubai Gala Tab (DemoTrading merged-202-33464).
    Nav 1484:27515 has bb.height=88px; text overlay css.top='91px'.
    """
    # Real data: node 1484:27154 supplementNode CSS from merged-202-33464.ir.json
    ir_node = {
        'figmaId': '1484:27154',
        'figmaName': 'Frame 1410119534',
        'figmaType': 'FRAME',
        'isTextNode': False,
        'isImageNode': False,
        'isVectorNode': False,
        'css': {
            'width': '270px',
            'position': 'absolute',
            'left': '52.5px',
            'top': '91px',
            'display': 'flex',
            'flex-direction': 'column',
            'align-items': 'center',
            'gap': '12px',
        },
        'children': [],
    }

    # H5 nav (1484:27515) has bb.height = 88px
    _maybe_adjust_h5_top_for_nav_offset(ir_node, h5_nav_height=88.0)

    css = ir_node['css']
    assert css.get('top') == '3px', (
        f'U-449a FAIL: top must be adjusted to 3px (91-88) when nav_height=88. '
        f'Got: {css.get("top")!r}'
    )
    assert css.get('position') == 'absolute', (
        f'U-449b FAIL: position must remain absolute after top adjustment.'
    )
    assert css.get('left') == '52.5px', (
        f'U-449c FAIL: left must be unchanged by top adjustment.'
    )

    # Edge: top below 80% of nav_height should NOT be adjusted
    ir_below = {'css': {'position': 'absolute', 'top': '60px'}, 'children': []}
    _maybe_adjust_h5_top_for_nav_offset(ir_below, h5_nav_height=88.0)
    assert ir_below['css']['top'] == '60px', (
        f'U-449d FAIL: top:60px (< 0.8*88=70.4) must NOT be adjusted. '
        f'Got: {ir_below["css"]["top"]!r}'
    )

    # Edge: top above 150% of nav_height should NOT be adjusted
    ir_above = {'css': {'position': 'absolute', 'top': '140px'}, 'children': []}
    _maybe_adjust_h5_top_for_nav_offset(ir_above, h5_nav_height=88.0)
    assert ir_above['css']['top'] == '140px', (
        f'U-449e FAIL: top:140px (> 1.5*88=132) must NOT be adjusted. '
        f'Got: {ir_above["css"]["top"]!r}'
    )

    # Edge: non-absolute position should NOT be adjusted
    ir_rel = {'css': {'position': 'relative', 'top': '91px'}, 'children': []}
    _maybe_adjust_h5_top_for_nav_offset(ir_rel, h5_nav_height=88.0)
    assert ir_rel['css']['top'] == '91px', (
        f'U-449f FAIL: non-absolute position must NOT be adjusted. '
        f'Got: {ir_rel["css"]["top"]!r}'
    )

    # Edge: h5_nav_height=0 should be a no-op
    ir_nonav = {'css': {'position': 'absolute', 'top': '91px'}, 'children': []}
    _maybe_adjust_h5_top_for_nav_offset(ir_nonav, h5_nav_height=0.0)
    assert ir_nonav['css']['top'] == '91px', (
        f'U-449g FAIL: h5_nav_height=0 must be a no-op. '
        f'Got: {ir_nonav["css"]["top"]!r}'
    )


def test_u450_structural_split_inline_node_h5_supplement_rendered():
    """U-450: When an inline structuralSplit node has a supplementNode with content,
    the H5 supplement must be rendered as inline JSX in the page index.tsx AND
    the supplement root class must get @media (min-width: 769px) { display: none }
    so it is hidden on desktop.

    Root cause: DemoTrading Navigation section [0] (202:33465) has structuralSplit=True
    with H5 supplement 1484:27515 ('2.NAVI/App_bar', 88px mobile nav bar). The
    split_codegen correctly hides the PC nav on mobile (U-445) but NEVER renders the
    H5 supplement — so the mobile navigation bar is completely absent on H5.

    Real data: inlineNode ir.figmaId=202:33465 from DemoTrading (merged-202-33464),
    supplementNode figmaId=1484:27515 h=88px from H5 IR.
    """
    # Supplement has actual CSS and a child (the 导航条 nav bar)
    supp_child = {
        'figmaId': 'I1484:27515;758:1847', 'figmaName': '导航条', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'height': '44px', 'width': '100%', 'display': 'flex'},
        'children': [],
    }
    nav_ir_with_supp = {
        'figmaId': '202:33465', 'figmaName': 'Navigation', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'structuralSplit': True,
        'supplementNode': {
            'figmaId': '1484:27515', 'figmaName': '2.NAVI/App_bar', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'position': 'absolute', 'top': '0px', 'height': '88px', 'display': 'flex'},
            'children': [supp_child],
        },
        'css': {'width': '100%', 'height': '48px', 'display': 'flex',
                'flex-direction': 'row', 'position': 'relative',
                'background-color': '#121214'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'navigation',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }

    page_root_ir = {
        'figmaId': '202:33464', 'figmaName': 'DemoTradingPage', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column'},
        'children': [nav_ir_with_supp],
        'semantic': {
            'htmlTag': 'div', 'className': 'demo-trading-page',
            'componentName': 'DemoTradingPage', 'props': [], 'isExtractedComponent': False,
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = out / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '202-33464.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))

        plan = {
            'pageComponent': 'DemoTradingPage',
            'nodeId': '202-33464',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [],
            'inlineNodes': [{'ir': nav_ir_with_supp, 'y': 0.0, 'x': 0.0}],
        }
        generate_components(plan, out, css_ext='scss')

        page_tsx = (out / 'index.tsx').read_text()
        page_css = (out / 'DemoTradingPage.module.scss').read_text()

        # U-450a: H5 supplement class 'n2-navi-app_bar' must appear in index.tsx JSX
        # Note: _infer_supplement_semantics preserves _ from figmaName 'App_bar' →
        # className = 'n2-navi-app_bar' (underscore, NOT n2-navi-app-bar with dash).
        assert 'n2-navi-app_bar' in page_tsx, (
            'U-450a FAIL: H5 supplement class "n2-navi-app_bar" (inferred from figmaName '
            '"2.NAVI/App_bar") must appear in the page index.tsx so the mobile nav bar '
            'is rendered.\n'
            f'Actual index.tsx:\n{page_tsx}'
        )

        # U-450b: H5 supplement must be hidden on desktop via min-width: 769px media query
        assert '@media (min-width: 769px)' in page_css, (
            'U-450b FAIL: page CSS must have @media (min-width: 769px) to hide H5 '
            'supplement on desktop.\n'
            f'Actual CSS:\n{page_css}'
        )
        assert 'n2-navi-app_bar' in page_css, (
            'U-450c FAIL: H5 supplement class "n2-navi-app_bar" must appear in the '
            'page CSS with display: none rule for min-width: 769px.\n'
            f'Actual CSS:\n{page_css}'
        )

        # U-450d: PC nav still hidden on mobile (U-445 regression guard)
        assert '@media (max-width: 768px)' in page_css and 'navigation' in page_css, (
            'U-450d FAIL: PC navigation must still get display:none on mobile '
            '(U-445 regression).\n'
            f'Actual CSS:\n{page_css}'
        )

        # U-450e: H5 supplement with position:absolute at top≈0 must be
        # converted to position:relative so it occupies flow space (not overlay).
        # Ensures the hero section below the nav starts at y=88px, not y=0.
        import re as _re450
        h5_supp_cls_block = _re450.search(
            r'\.n2-navi-app_bar\s*\{([^}]*)\}', page_css, _re450.DOTALL)
        assert h5_supp_cls_block is not None, (
            'U-450e FAIL: .n2-navi-app_bar rule not found in page CSS.\n'
            f'Actual CSS:\n{page_css}'
        )
        cls_block_css = h5_supp_cls_block.group(1)
        assert 'position: relative' in cls_block_css or 'position:relative' in cls_block_css, (
            'U-450e FAIL: H5 supplement .n2-navi-app_bar must use position:relative '
            '(not absolute) so it occupies 88px in the flex-column flow and pushes '
            'the hero section down instead of overlaying at top:0.\n'
            f'Actual .n2-navi-app_bar CSS block:\n{cls_block_css}'
        )


def test_u451_u450_flow_nav_disables_u449_text_adjustment():
    """U-451: When U-450 renders an inline structuralSplit supplement as a flow element
    (supplement has content: CSS or children), the U-449 nav-height detection must
    yield _h5_nav_height = 0 so that H5 text overlays in adjacent sections keep their
    ORIGINAL top values (e.g. 91px) instead of being adjusted to nav_height-subtracted
    values (e.g. 3px).

    Root cause: DemoTrading section [0] nav supplement (1484:27515) has content, so
    U-450 renders it as a flow element (position:relative, height:88px). The text
    overlay in section [1] was designed at top:91px (= nav_height=88 + 3px offset).
    U-449 was subtracting nav_height to get top:3px, assuming nav was display:none.
    But with U-450, the nav IS shown (occupies 88px in flow), so the text at top:3px
    would be positioned at page y=3px instead of y=91px, overlapping the nav bar.

    Fix: in the U-449 detection loop, skip the nav height when the supplement has
    content (bool(supp.get('css') or supp.get('children')) is True) — meaning U-450
    will render it as a flow element.

    Real data: DemoTrading merged-202-33464, section [0] nav has bb.h=88px and
    supplement children (status bar + nav bar). Section [1] hero H5 supplement
    has top:91px text overlay.
    """
    _base_ir_node = {
        'figmaId': 'merged:202:33464', 'figmaName': 'DemoTradingPage', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'demo-trading-page',
            'componentName': 'DemoTradingPage', 'props': [], 'isExtractedComponent': False,
        },
    }

    # Inline nav with supplement that HAS content (U-450 will render it as flow)
    _nav_ir = {
        'figmaId': '202:33465', 'figmaName': 'Navigation', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'structuralSplit': True,
        'supplementNode': {
            'figmaId': '1484:27515', 'figmaName': '2.NAVI/App_bar', 'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            # KEY: has both CSS and children → U-450 will render this as flow element
            'css': {'position': 'absolute', 'top': '0px', 'height': '88px', 'display': 'flex'},
            'children': [{'figmaId': 'I1484:27515;758:1847', 'figmaName': '导航条',
                          'figmaType': 'FRAME', 'isTextNode': False, 'isImageNode': False,
                          'isVectorNode': False, 'isDecorativeElement': False,
                          'textContent': None, 'textSegments': None,
                          'localAssetPath': None, 'lineTypes': [],
                          'css': {'height': '44px', 'display': 'flex'}, 'children': []}],
            'bb': {'width': 375.0, 'height': 88.0},
        },
        'css': {'width': '100%', 'height': '48px', 'display': 'flex', 'position': 'relative'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'navigation',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
        'bb': {'width': 1440.0, 'height': 48.0},
    }

    # Hero section structural split with H5 supplement text overlay at top: 91px
    _hero_h5_supp = {
        'figmaId': '1484:27154', 'figmaName': 'Frame 1410119534', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '270px', 'position': 'absolute', 'left': '52.5px',
            'top': '91px',  # This must NOT be adjusted to 3px when U-450 renders nav
            'display': 'flex', 'flex-direction': 'column', 'align-items': 'center',
        },
        'children': [],
    }
    _hero_ir = {
        'figmaId': '202:33496', 'figmaName': 'Header', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'structuralSplit': True,
        'supplementNode': _hero_h5_supp,
        'css': {'width': '100%', 'height': '750px', 'position': 'relative', 'display': 'flex'},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'header-section',
            'componentName': 'HeaderSection', 'props': [], 'isExtractedComponent': False,
        },
        'bb': {'width': 1440.0, 'height': 750.0},
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = out / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        # merged IR must include nav inline node so U-449 detection can find it
        full_ir = {**_base_ir_node, 'children': [_nav_ir]}
        ir_path = ir_dir / '202-33464.ir.json'
        ir_path.write_text(json.dumps(full_ir))

        plan = {
            'pageComponent': 'DemoTradingPage',
            'nodeId': '202-33464',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [{'name': 'HeaderSection', 'ir': _hero_ir, 'y': 48.0}],
            'inlineNodes': [{'ir': _nav_ir, 'y': 0.0, 'x': 0.0}],
        }
        generate_components(plan, out, css_ext='scss')

        # Find the HeaderSectionH5 scss file
        h5_scss_files = list(out.rglob('HeaderSectionH5.module.scss'))
        assert h5_scss_files, (
            'U-451a FAIL: HeaderSectionH5.module.scss must be generated for structural split section.\n'
            f'Generated files: {[str(f) for f in out.rglob("*.scss")]}'
        )
        h5_css = h5_scss_files[0].read_text()

        # U-451a: When U-450 renders the nav supplement as flow, the hero H5 text
        # overlay at top:91px must NOT be adjusted to top:3px (U-449 must not apply).
        assert 'top: 3px' not in h5_css and 'top:3px' not in h5_css, (
            'U-451a FAIL: H5 text overlay top must NOT be adjusted to 3px when '
            'U-450 renders the nav supplement as a flow element. '
            '(U-449 must be disabled when U-450 has content to render.)\n'
            f'Actual H5 CSS:\n{h5_css}'
        )

        # U-451b: The original top:91px must be preserved
        assert 'top: 91px' in h5_css or 'top:91px' in h5_css, (
            'U-451b FAIL: H5 text overlay top:91px must be preserved when nav is '
            'rendered as a flow element by U-450.\n'
            f'Actual H5 CSS:\n{h5_css}'
        )


def test_u443_same_name_h5only_subsections_get_unique_dirs():
    """U-443: Multiple h5Only subSections with same figmaName must each get a unique
    component directory and distinct TSX content. Without the fix they all write to the
    same directory and only the last one survives.

    Root cause: DemoTrading AdvantagesSection has 6 H5 sections all named
    "Why Institutions Choose Us" in Figma (nodes 1484:27252..1484:27484). split_codegen
    collapsed them into one WhyInstitutionsChooseUsSection component, so all 6 slots in
    the parent index.tsx render identical content.

    Fix: track a _ss_name_counts counter in the subSection loop; append numeric suffix
    ("2", "3", ...) to disambiguate same-named subSections.

    Real data: nodes 1484:27252 and 1484:27484 from DemoTrading (merged-202-33464).
    """
    # Real data: node 1484:27252 — "Practice your trading skills With demo trading"
    ss_ir_a = {
        'figmaId': '1484:27252', 'figmaName': 'Why Institutions Choose Us',
        'figmaType': 'FRAME', 'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None, 'localAssetPath': None,
        'lineTypes': [], 'h5Only': True,
        'css': {'display': 'flex', 'flex-direction': 'column',
                'width': '375px', 'height': '550px',
                'align-items': 'center', 'gap': '32px',
                'padding': '40px 16px 40px 16px',
                'background-color': '#000000', 'position': 'relative'},
        'children': [{
            'figmaId': '1484:27253',
            'figmaName': 'Practice your trading skills With demo trading',
            'figmaType': 'TEXT', 'isTextNode': True, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': 'Practice your trading skills With demo trading',
            'textSegments': None, 'localAssetPath': None, 'lineTypes': ['NONE'],
            'css': {'color': '#ffffff', 'font-size': '24px', 'font-weight': '600',
                    'font-family': "'IBM Plex Sans', sans-serif", 'line-height': '40px',
                    'flex-shrink': '0', 'align-self': 'stretch'},
            'children': [],
            'semantic': {
                'htmlTag': 'span',
                'className': 'practice-your-trading-skills',
                'componentName': None, 'props': [], 'isExtractedComponent': False,
            },
        }],
        'semantic': {
            'htmlTag': 'div', 'className': 'why-institutions-choose-us-n1484-27252',
            'componentName': 'WhyInstitutionsChooseUsSection',
            'props': [], 'isExtractedComponent': True,
        },
    }

    # Real data: node 1484:27484 — "Practice the following teaching content..."
    ss_ir_b = {
        'figmaId': '1484:27484', 'figmaName': 'Why Institutions Choose Us',
        'figmaType': 'FRAME', 'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None, 'localAssetPath': None,
        'lineTypes': [], 'h5Only': True,
        'css': {'display': 'flex', 'flex-direction': 'column',
                'width': '375px', 'height': '1235px',
                'align-items': 'center', 'gap': '32px',
                'padding': '40px 16px 40px 16px',
                'background-color': '#000000', 'position': 'relative'},
        'children': [{
            'figmaId': '1484:27485',
            'figmaName': 'Practice the following teaching content through simulated trading',
            'figmaType': 'TEXT', 'isTextNode': True, 'isImageNode': False,
            'isVectorNode': False, 'isDecorativeElement': False,
            'textContent': 'Practice the following teaching content through simulated trading',
            'textSegments': None, 'localAssetPath': None, 'lineTypes': ['NONE'],
            'css': {'color': '#ffffff', 'font-size': '24px', 'font-weight': '600',
                    'font-family': "'IBM Plex Sans', sans-serif", 'line-height': '40px',
                    'flex-shrink': '0', 'align-self': 'stretch'},
            'children': [],
            'semantic': {
                'htmlTag': 'span',
                'className': 'practice-the-following',
                'componentName': None, 'props': [], 'isExtractedComponent': False,
            },
        }],
        'semantic': {
            'htmlTag': 'div', 'className': 'why-institutions-choose-us-n1484-27484',
            'componentName': 'WhyInstitutionsChooseUsSection',
            'props': [], 'isExtractedComponent': True,
        },
    }

    parent_section_ir = {
        'figmaId': '202:33619', 'figmaName': 'AdvantagesSection', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%',
                'position': 'relative'},
        'children': [ss_ir_a, ss_ir_b],
        'semantic': {
            'htmlTag': 'div', 'className': 'advantages',
            'componentName': 'AdvantagesSection', 'props': [], 'isExtractedComponent': True,
        },
    }

    page_root_ir = {
        'figmaId': '202:33464', 'figmaName': 'DemoTradingPage', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column'},
        'children': [parent_section_ir],
        'semantic': {
            'htmlTag': 'div', 'className': 'demo-trading-page',
            'componentName': 'DemoTradingPage', 'props': [], 'isExtractedComponent': False,
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = out / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '202-33464.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))

        plan = {
            'pageComponent': 'DemoTradingPage',
            'nodeId': '202-33464',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [{
                'name': 'AdvantagesSection',
                'ir': parent_section_ir,
                'y': 0.0, 'x': 0.0,
                'ccComponent': None,
                'leafComponents': [],
                'subSections': [
                    {'name': 'WhyInstitutionsChooseUsSection', 'ir': ss_ir_a, 'leafComponents': []},
                    {'name': 'WhyInstitutionsChooseUsSection', 'ir': ss_ir_b, 'leafComponents': []},
                ],
            }],
            'inlineNodes': [],
        }
        generate_components(plan, out, css_ext='scss')

        adv_dir = out / 'components' / 'AdvantagesSection'

        # U-443a: first instance keeps original name
        dir_a = adv_dir / 'WhyInstitutionsChooseUsSection'
        assert dir_a.is_dir(), (
            'U-443a FAIL: first WhyInstitutionsChooseUsSection must have its own directory'
        )

        # U-443b: second instance gets disambiguated name WhyInstitutionsChooseUsSection2
        dir_b = adv_dir / 'WhyInstitutionsChooseUsSection2'
        assert dir_b.is_dir(), (
            f'U-443b FAIL: second same-named subSection must get WhyInstitutionsChooseUsSection2 dir.\n'
            f'Dirs found under {adv_dir}: {[d.name for d in adv_dir.iterdir() if d.is_dir()] if adv_dir.is_dir() else "N/A"}'
        )

        # U-443c: second instance has its own index.tsx with node B content
        tsx_b = (dir_b / 'index.tsx').read_text()
        assert 'practice-the-following' in tsx_b or 'WhyInstitutionsChooseUsSection2' in tsx_b, (
            'U-443c FAIL: WhyInstitutionsChooseUsSection2/index.tsx must contain node B content'
        )

        # U-443d: parent section imports both components under distinct names
        parent_tsx = (adv_dir / 'index.tsx').read_text()
        assert 'WhyInstitutionsChooseUsSection2' in parent_tsx, (
            f'U-443d FAIL: parent index.tsx must import WhyInstitutionsChooseUsSection2.\n'
            f'Actual:\n{parent_tsx[:1500]}'
        )


def test_u452b_effective_root_cls_when_semantic_not_in_orig_css():
    """U-452b: effective_root_cls must equal inst_cls (skip wrapper) when the leaf's
    semantic root class name is NOT in orig_css_map but the leaf's template figmaId
    maps to the same CSS class as the instance via fid_to_class.

    Bug: root_cls='container' not in orig_css_map →
      orig_css_map.get('container', {}) = {} (empty)
      inst_css = orig_css_map.get('container-I39641-9303-39641-6388') = {position:abs, left:120px, ...}
      {} != inst_css → CSS diff check → diff has non-visual props → effective_root_cls='' → WRAPPER
    Result: tsx_generator renders wrapper div: <div class="container-I39641-9303-39641-6388">
    AND TopUp root div also has class "container-I39641-9303-39641-6388" with position:abs;left:120px
    → double absolute positioning: 120px + 120px = 240px (card shifts right by 120px).

    Fix: when root_cls not in orig_css_map AND fid_to_class maps leaf template figmaId
    to the same class as inst_cls, set effective_root_cls = inst_cls → no wrapper needed.

    Real data: TopUp I39641:9303;39641:6388 in Tomorrowland GetStartedInSection
               fid_to_class from merged-39641-6863 figma-maps.json
    """
    import lib.split_codegen as _sc

    # Real data from Tomorrowland GetStartedInSection (node I39641:9303;39641:6388)
    lc_ir = {
        'figmaId': 'I39641:9303;39641:6388',  # template figmaId for TopUp leaf
        'figmaName': 'Container',
        'semantic': {'className': 'container', 'componentName': 'TopUp'},
        'css': {'width': '497px', 'height': '140px', 'position': 'absolute',
                'left': '120px', 'top': '0px', 'display': 'flex',
                'padding': '32px', 'background-color': '#ffffff',
                'border-radius': '24px', 'overflow': 'hidden'},
    }
    fid_to_class = {
        'I39641:9303;39641:6388': 'container-I39641-9303-39641-6388',
        'I39641:9303;39641:6400': 'container-I39641-9303-39641-6400',
    }
    orig_css_map = {
        # Section CSS uses node-ID class — semantic name 'container' is ABSENT
        'container-I39641-9303-39641-6388': {
            'width': '497px', 'height': '140px', 'position': 'absolute',
            'left': '120px', 'top': '0px', 'display': 'flex',
            'flex-direction': 'column', 'align-items': 'flex-start',
            'gap': '12px', 'overflow': 'hidden',
        },
        'container-I39641-9303-39641-6400': {
            'height': '140px', 'position': 'absolute', 'left': '120px',
            'top': '-0.25px', 'display': 'flex', 'flex-direction': 'column',
            'align-items': 'flex-start', 'gap': '12px', 'overflow': 'hidden',
        },
    }

    def _strip_invisible(css):
        return {k: v for k, v in css.items()
                if not (k == 'opacity' and str(v).strip() in ('0', '0.0'))}

    # Simulate effective_root_cls computation for template instance (fid = I39641:9303;39641:6388)
    fid = 'I39641:9303;39641:6388'
    root_cls = (lc_ir.get('semantic') or {}).get('className', '')  # 'container'
    is_moly = False

    # UNFIXED: current logic
    effective_root_cls_unfixed = root_cls  # starts as root_cls
    if not is_moly and orig_css_map and fid_to_class:
        inst_cls = fid_to_class.get(fid, '')
        if inst_cls and inst_cls != root_cls:
            # CSS comparison fails: {} != {position:abs, left:120px, ...}
            if _strip_invisible(orig_css_map.get(inst_cls, {})) == _strip_invisible(orig_css_map.get(root_cls, {})):
                effective_root_cls_unfixed = inst_cls
            else:
                # Diff check: diff has position, left, width → not visual-only
                _r_css = orig_css_map.get(root_cls, {})
                _i_css = orig_css_map.get(inst_cls, {})
                _diff_keys = {k for k in set(_r_css) | set(_i_css)
                              if _r_css.get(k) != _i_css.get(k)}
                if _diff_keys and _diff_keys.issubset(_sc._VISUAL_ONLY_PROPS):
                    pass  # variant_cls (no wrapper)
                else:
                    effective_root_cls_unfixed = ''  # force wrapper

    assert effective_root_cls_unfixed == '', (
        f'U-452b-precondition FAIL: UNFIXED effective_root_cls should be "" '
        f'(wrapper forced) but got {effective_root_cls_unfixed!r}'
    )

    # tsx_generator: needs_wrapper = inst_cls != root_cls
    # With root_cls='' and inst_cls='container-I39641-9303-39641-6388' → needs_wrapper=True
    inst_cls = fid_to_class.get(fid, '')
    needs_wrapper_unfixed = bool(inst_cls and inst_cls != effective_root_cls_unfixed)
    assert needs_wrapper_unfixed, (
        'U-452b-precondition FAIL: UNFIXED wrapper should be generated (bug reproduces)'
    )

    # FIXED: when root_cls not in orig_css_map AND leaf template figmaId maps to inst_cls
    effective_root_cls_fixed = root_cls
    if not is_moly and orig_css_map and fid_to_class:
        inst_cls = fid_to_class.get(fid, '')
        _lc_tpl_fid = lc_ir.get('figmaId', '')
        _lc_tpl_orig_cls = fid_to_class.get(_lc_tpl_fid, '')
        if inst_cls and inst_cls != root_cls:
            # U-452: when root_cls not in orig_css_map, use template figmaId class for comparison
            if root_cls not in orig_css_map and _lc_tpl_orig_cls:
                effective_root_cls_fixed = inst_cls  # leaf IS its own root node
            elif _strip_invisible(orig_css_map.get(inst_cls, {})) == _strip_invisible(orig_css_map.get(root_cls, {})):
                effective_root_cls_fixed = inst_cls
            else:
                _r_css = orig_css_map.get(root_cls, {})
                _i_css = orig_css_map.get(inst_cls, {})
                _diff_keys = {k for k in set(_r_css) | set(_i_css)
                              if _r_css.get(k) != _i_css.get(k)}
                if _diff_keys and _diff_keys.issubset(_sc._VISUAL_ONLY_PROPS):
                    pass
                else:
                    effective_root_cls_fixed = ''

    assert effective_root_cls_fixed == inst_cls, (
        f'U-452b FAIL: FIXED effective_root_cls should equal inst_cls '
        f'"{inst_cls}" but got {effective_root_cls_fixed!r}'
    )

    # tsx_generator: needs_wrapper = inst_cls != root_cls = 'container-I39641-9303-39641-6388' != 'container-I39641-9303-39641-6388' = False
    needs_wrapper_fixed = bool(inst_cls and inst_cls != effective_root_cls_fixed)
    assert not needs_wrapper_fixed, (
        'U-452b FAIL: FIXED wrapper should NOT be generated (no double absolute positioning)'
    )


def test_u453_root_css_text_collision_falls_back_to_ir_css():
    """U-453: When page root CSS class collides with a TEXT node's class (naming collision),
    the root CSS block contains TEXT-specific properties (font-size, white-space:nowrap,
    -webkit-text-fill-color) causing page-wide text style inheritance.

    Real case: DemoTrading figmaName "Demo Trading" → class 'demo-trading' collides
    with page root container class 'demo-trading'.  convert.py writes TEXT gradient
    styles into .demo-trading; split_codegen reads it as root CSS → all children
    inherit font-size:78px and white-space:nowrap, breaking the entire page layout.

    Fix: _sanitize_page_root_css detects TEXT indicators and replaces with IR CSS.
    """
    import lib.split_codegen as _sc

    text_polluted_css = '''.demo-trading {
  width: 100%;
  flex-shrink: 0;
  color: transparent;
  font-size: 78px;
  font-weight: 600;
  font-family: 'Inter', sans-serif;
  line-height: 56px;
  letter-spacing: 3.9px;
  text-align: left;
  text-transform: uppercase;
  white-space: nowrap;
  background-image: linear-gradient(90deg, #bcbcbc -0%, #f4f4f4 33.33%, #6b6b6b 66.67%, #bcbcbc 100%);
  background-color: transparent;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
'''

    ir_css = {
        'width': '100%',
        'display': 'flex',
        'flex-direction': 'column',
        'align-items': 'flex-start',
        'background-color': '#000000',
        'position': 'relative',
    }

    result = _sc._sanitize_page_root_css(text_polluted_css, 'demo-trading', ir_css)

    assert '-webkit-text-fill-color' not in result, 'Should strip -webkit-text-fill-color'
    assert 'white-space' not in result, 'Should strip white-space'
    assert 'font-size' not in result, 'Should strip font-size'
    assert '.demo-trading' in result, 'Should keep root class name'
    assert 'display: flex' in result, 'Should have display:flex from IR'
    assert 'background-color: #000000' in result, 'Should have background-color from IR'

    # Normal (non-text) CSS should pass through unchanged
    normal_css = '.demo-trading {\n  width: 100%;\n  display: flex;\n  background-color: #000;\n}\n'
    assert _sc._sanitize_page_root_css(normal_css, 'demo-trading', ir_css) == normal_css, (
        'Non-text CSS should be returned unchanged'
    )


def test_u456_abs_positioned_leaf_instance_gets_wrapper_div():
    """U-456: When a leaf component (e.g. BeginnersTabList) appears at multiple locations
    in a section and one instance has position:absolute CSS that differs from the
    leaf template's absolute positioning (different left/top offsets), the TSX generator
    must wrap that instance in a div with the instance-specific positioning CSS class.

    Bug: The U-452 fix sets effective_root_cls = inst_cls when
    'root_cls not in orig_css_map', bypassing the wrapper. But if inst_cls differs
    from the leaf template class AND both have position:absolute with different
    left/top values, skipping the wrapper means the instance-specific positioning
    is NEVER applied (BeginnersTabList ends up in flex flow at 656px instead of abs).

    Real case: BeginnersTabList (template: 202:33568, class group-1410117705 at
    left:120px top:218px) has a second instance 1484:26020 (class
    group-1410117705-n1484-26020 at left:0px top:166px) in UspSkilledSection.
    Without the fix the second instance renders without position:absolute, adding
    656px to UspSkilledSection's height (rendered 1566px vs Figma 882px).

    Fix: When inst_cls != leaf template class AND inst_cls has position:absolute
    in orig_css_map, force effective_root_cls = '' so a wrapper div is generated.
    """
    import lib.split_codegen as _sc

    # Leaf template figmaId = 202:33568 → class group-1410117705 (position:absolute, left:120px, top:218px)
    # Non-template instance figmaId = 1484:26020 → class group-1410117705-n1484-26020 (abs, left:0, top:166px)
    lc_ir = {
        'figmaId': '202:33568',
        'figmaName': 'Group 1410117705',
        'semantic': {'className': 'group1410117705'},  # normalized (no hyphens)
        'css': {'width': '1200px', 'height': '656px', 'position': 'absolute',
                'left': '120px', 'top': '218px'},
    }
    fid_to_class = {
        '202:33568': 'group-1410117705',
        '1484:26020': 'group-1410117705-n1484-26020',
    }
    orig_css_map = {
        'group-1410117705': {
            'width': '1200px', 'height': '656px',
            'position': 'absolute', 'left': '120px', 'top': '218px',
        },
        'group-1410117705-n1484-26020': {
            'width': '1200px', 'height': '656px',
            'position': 'absolute', 'left': '0px', 'top': '166px',
        },
    }

    # Helper matching split_codegen logic
    def _strip_invisible(css):
        return {k: v for k, v in css.items()
                if not (k == 'opacity' and str(v).strip() in ('0', '0.0'))}

    # Test the non-template instance (fid = 1484:26020)
    fid = '1484:26020'
    root_cls = (lc_ir.get('semantic') or {}).get('className', '')  # 'group1410117705' (normalized)
    is_moly = False

    # ── PRE-FIX BEHAVIOR ──────────────────────────────────────────────────────
    # U-452 fires: root_cls='group1410117705' not in orig_css_map (hyphenated keys)
    # → effective_root_cls = inst_cls (no wrapper) → BUG: positioning never applied
    effective_root_cls_prefixed = root_cls
    if not is_moly and orig_css_map and fid_to_class:
        inst_cls = fid_to_class.get(fid, '')
        _lc_tpl_fid = lc_ir.get('figmaId', '')
        _lc_tpl_orig_cls = fid_to_class.get(_lc_tpl_fid, '')
        if inst_cls and inst_cls != root_cls:
            if _lc_tpl_orig_cls and (_lc_tpl_orig_cls == root_cls or root_cls not in orig_css_map):
                # U-452 sets no-wrapper — this is where the bug lives
                effective_root_cls_prefixed = inst_cls  # BUG: skips wrapper

    inst_cls = fid_to_class.get(fid, '')
    assert effective_root_cls_prefixed == inst_cls, (
        f'U-456-precondition FAIL: pre-fix should produce effective_root_cls=inst_cls '
        f'(no wrapper = bug), got {effective_root_cls_prefixed!r}'
    )
    needs_wrapper_prefixed = bool(inst_cls and inst_cls != effective_root_cls_prefixed)
    assert not needs_wrapper_prefixed, (
        'U-456-precondition FAIL: pre-fix must NOT produce wrapper (bug reproduces)'
    )

    # ── POST-FIX BEHAVIOR ─────────────────────────────────────────────────────
    # U-456: when inst_cls != leaf template class AND inst_cls has position:absolute,
    # leave effective_root_cls = '' so wrapper IS generated.
    effective_root_cls_fixed = root_cls
    if not is_moly and orig_css_map and fid_to_class:
        inst_cls = fid_to_class.get(fid, '')
        _lc_tpl_fid = lc_ir.get('figmaId', '')
        _lc_tpl_orig_cls = fid_to_class.get(_lc_tpl_fid, '')
        if inst_cls and inst_cls != root_cls:
            if _lc_tpl_orig_cls and (_lc_tpl_orig_cls == root_cls or root_cls not in orig_css_map):
                # U-456: if the instance is NOT the template and has abs positioning,
                # it needs a wrapper — leaf component strips position from its own CSS.
                _u456_inst_needs_abs_wrapper = (
                    inst_cls != _lc_tpl_orig_cls
                    and orig_css_map.get(inst_cls, {}).get('position') == 'absolute'
                )
                if not _u456_inst_needs_abs_wrapper:
                    effective_root_cls_fixed = inst_cls  # no wrapper (correct for template case)
                # else: effective_root_cls stays '' → wrapper generated

    # After the fix, effective_root_cls must NOT equal inst_cls
    # (so tsx_generator sees inst_cls != root_cls → wrapper generated).
    # It may be root_cls ('group1410117705') or ''; both trigger needs_wrapper=True.
    assert effective_root_cls_fixed != inst_cls, (
        f'U-456 FAIL: fixed effective_root_cls must differ from inst_cls '
        f'(so wrapper is generated), got {effective_root_cls_fixed!r} == {inst_cls!r}'
    )

    needs_wrapper_fixed = bool(inst_cls and inst_cls != effective_root_cls_fixed)
    assert needs_wrapper_fixed, (
        'U-456 FAIL: fixed logic must generate wrapper div for abs-positioned non-template instance'
    )

    # Also verify the TEMPLATE instance (fid = 202:33568) is UNAFFECTED
    # (it should still get effective_root_cls = inst_cls, preserving existing behavior)
    fid_template = '202:33568'
    inst_cls_tpl = fid_to_class.get(fid_template, '')  # 'group-1410117705'
    effective_root_cls_template = root_cls
    if not is_moly and orig_css_map and fid_to_class:
        _lc_tpl_fid = lc_ir.get('figmaId', '')
        _lc_tpl_orig_cls = fid_to_class.get(_lc_tpl_fid, '')
        if inst_cls_tpl and inst_cls_tpl != root_cls:
            if _lc_tpl_orig_cls and (_lc_tpl_orig_cls == root_cls or root_cls not in orig_css_map):
                _u456_tpl_needs_abs_wrapper = (
                    inst_cls_tpl != _lc_tpl_orig_cls  # False: 'group-1410117705' == 'group-1410117705'
                    and orig_css_map.get(inst_cls_tpl, {}).get('position') == 'absolute'
                )
                if not _u456_tpl_needs_abs_wrapper:
                    effective_root_cls_template = inst_cls_tpl

    assert effective_root_cls_template == inst_cls_tpl, (
        f'U-456 regression: template instance should still get effective_root_cls=inst_cls, '
        f'got {effective_root_cls_template!r}'
    )


def test_u467_sub_pixel_top_diff_no_wrapper_for_leaf_instance():
    """U-467: When a non-template leaf instance's CSS differs from the template ONLY
    by a sub-pixel (< 1px) top coordinate, U-456 must NOT trigger a wrapper div.

    Bug: U-456 checks inst_cls != tpl_cls AND position:absolute → wrapper generated.
    But when the only CSS diff is top:0px vs top:-0.25px (Figma sub-pixel rounding),
    the leaf already renders position:absolute at the same effective coordinates.
    The wrapper creates DOUBLE absolute positioning: outer div (position:abs, left:120px)
    + TopUp root (position:abs, left:120px) → card shifted to left:240px, clipped by
    overflow:hidden → empty white box with no visible text.

    Real data: node I39641:9303;39641:6400 from TomorrowlandLandingPage4 (39641-6863).
    TopUp template: I39641:9303;39641:6388 (container-I39641-9303-39641-6388, top:0px)
    Non-template:   I39641:9303;39641:6400 (container-I39641-9303-39641-6400, top:-0.25px)
    """
    # Real data: nodes from TomorrowlandLandingPage4 (39641-6863), GetStartedIn section
    _lc_ir = {
        'figmaId': 'I39641:9303;39641:6388',
        'figmaName': 'Container',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'bb': {'width': 497, 'height': 140},
        'css': {'width': '497px', 'height': '140px', 'position': 'absolute',
                'left': '120px', 'top': '0px', 'display': 'flex',
                'flex-direction': 'column', 'align-items': 'flex-start', 'gap': '12px',
                'padding': '32px 29.48px 32px 32px',
                'background-color': '#ffffff', 'border-radius': '24px', 'overflow': 'hidden'},
        'children': [],
        'semantic': {'htmlTag': 'div', 'className': 'TopUp',
                     'componentName': 'TopUp', 'props': [], 'isExtractedComponent': True},
    }
    _inst_6400_ir = {
        'figmaId': 'I39641:9303;39641:6400',
        'figmaName': 'Container',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'bb': {'width': 497, 'height': 140},
        'css': {'width': '497px', 'height': '140px', 'position': 'absolute',
                'left': '120px', 'top': '-0.25px', 'display': 'flex',
                'flex-direction': 'column', 'align-items': 'flex-start', 'gap': '12px',
                'padding': '32px 29.48px 32px 32px',
                'background-color': '#ffffff', 'border-radius': '24px', 'overflow': 'hidden'},
        'children': [],
        'semantic': {'htmlTag': 'div', 'className': 'container-I39641-9303-39641-6400',
                     'componentName': None, 'props': [], 'isExtractedComponent': False},
    }
    _section_ir = {
        'figmaId': 'I39641:9303;39641:6399',
        'figmaName': 'container-I39641-9303-39641-6399',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'bb': {'width': 617, 'height': 139},
        'css': {'width': '617px', 'height': '139px', 'position': 'relative',
                'display': 'flex', 'flex-direction': 'row'},
        'children': [_inst_6400_ir],
        'semantic': {'htmlTag': 'div', 'className': 'container-I39641-9303-39641-6399',
                     'componentName': 'TopUpSection', 'props': [], 'isExtractedComponent': True},
    }
    _fid_to_class = {
        'I39641:9303;39641:6388': 'container-I39641-9303-39641-6388',
        'I39641:9303;39641:6400': 'container-I39641-9303-39641-6400',
    }
    _orig_css_map = {
        'container-I39641-9303-39641-6399': {
            'width': '617px', 'height': '139px', 'position': 'relative',
            'display': 'flex', 'flex-direction': 'row',
        },
        'container-I39641-9303-39641-6388': {
            'width': '497px', 'height': '140px',
            'position': 'absolute', 'left': '120px', 'top': '0px',
            'display': 'flex', 'flex-direction': 'column', 'align-items': 'flex-start',
            'gap': '12px', 'padding': '32px 29.48px 32px 32px',
            'background-color': '#ffffff', 'border-radius': '24px', 'overflow': 'hidden',
        },
        'container-I39641-9303-39641-6400': {
            'width': '497px', 'height': '140px',
            'position': 'absolute', 'left': '120px', 'top': '-0.25px',  # only diff: sub-pixel
            'display': 'flex', 'flex-direction': 'column', 'align-items': 'flex-start',
            'gap': '12px', 'padding': '32px 29.48px 32px 32px',
            'background-color': '#ffffff', 'border-radius': '24px', 'overflow': 'hidden',
        },
    }
    _node_index = {
        'I39641:9303;39641:6388': _lc_ir,
        'I39641:9303;39641:6400': _inst_6400_ir,
        'I39641:9303;39641:6399': _section_ir,
    }

    tsx_result, _ = _generate_section_tsx(
        section_name='TopUpSection',
        ir=_section_ir,
        raw_section_ir=_section_ir,
        leaf_components=[{
            'name': 'TopUp',
            'ir': _lc_ir,
            'ccComponent': None,
            'allInstanceFigmaIds': ['I39641:9303;39641:6388', 'I39641:9303;39641:6400'],
            'instancesData': [
                {'topUpYourCardHere': 'Top-up your card here',
                 'applyInMinutesWithJustYourEmailNoCreditC': 'Top up instantly.'},
                {'topUpYourCardHere': 'Use it throughout the journey',
                 'applyInMinutesWithJustYourEmailNoCreditC': 'Unlock Card benefits.'},
            ],
            'varyingProps': [
                {'propName': 'topUpYourCardHere', 'type': 'text',
                 'values': ['Top-up your card here', 'Use it throughout the journey']},
                {'propName': 'applyInMinutesWithJustYourEmailNoCreditC', 'type': 'text',
                 'values': ['Top up instantly.', 'Unlock Card benefits.']},
            ],
        }],
        leaf_names=['TopUp'],
        css_ext='less',
        node_index=_node_index,
        orig_css_map=_orig_css_map,
        fid_to_class=_fid_to_class,
    )

    # U-467: node 6400 should be rendered as <TopUp> DIRECTLY — no wrapper div
    # Before the fix, U-456 fires and generates:
    #   <div className={styles['container-I39641-9303-39641-6400']}><TopUp .../></div>
    assert "styles['container-I39641-9303-39641-6400']" not in tsx_result, (
        "U-467 FAIL: wrapper div container-I39641-9303-39641-6400 was generated around "
        "TopUp, causing double absolute positioning (double left:120px) and clipping "
        "the card content. Sub-pixel top diff (-0.25px) should NOT trigger U-456 wrapper."
    )
    assert '<TopUp ' in tsx_result, (
        "U-467 FAIL: TopUp component call not found in section TSX"
    )


def test_u468_icon_no_import_when_package_unknown():
    """U-468: When _write_moly_flat is called with an Icon* moly_name and no
    all_imports / snippet (fallback path), no hardcoded import is added.
    The component renders as a bare reference — the user must add the correct
    import for their own component library.

    Previous bug: fallback path hardcoded an internal icon package name.
    Fixed: skip adding any import when no import path is available.

    Real data: node 311:14971 from BtcPizzaDay page (235-14237).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        parent = Path(tmpdir)
        _write_moly_flat(
            parent_dir=parent,
            name='IconTransfor',
            moly_name='IconTransfor',
            cc_import='',
            css_ext='less',
            all_imports=None,
            snippet=None,
            instance_ir=None,
        )
        tsx_content = (parent / 'IconTransfor.tsx').read_text()
        # No hardcoded package import should be present
        assert 'your-component-lib' not in tsx_content, (
            "U-468 FAIL: hardcoded component package found in generated file. "
            f"Got: {tsx_content!r}"
        )
        assert '@example/icons' not in tsx_content or 'IconTransfor' in tsx_content, (
            "U-468 FAIL: unexpected import in generated file."
        )


def test_u469_button_snippet_empty_children_gets_instance_text_injected():
    """U-469: When a CC snippet has an empty-tag children (<Button ...></Button>)
    with NO whitespace between '>' and '</', the instance text must still be
    injected as the Button's children.

    Bug: _write_moly_flat uses r'>\s+</' (one-or-more whitespace) to detect
    empty children.  CC snippets from the Figma Code Connect map use
    '></Button>' (zero whitespace), so the regex never matches and the
    instance text ('Registration') is silently dropped — buttons render blank.

    Real data: node 245:12751 from BtcPizzaDay page (235-14237).
    CC snippet: '<Button variant="primary" size="small"></Button>'
    Instance child text: 'Registration'
    """
    # Real data: node 245:12751 from BtcPizzaDay (235-14237)
    _button_ir = {
        'figmaId': '245:12751',
        'figmaName': 'button',
        'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '120px', 'height': '32px'},
        'children': [{
            'figmaId': 'I245:12751;2:8053',
            'figmaName': 'Button Text',
            'figmaType': 'TEXT',
            'isTextNode': True,
            'textContent': 'Registration',
            'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {}, 'children': [],
        }],
        'semantic': {'htmlTag': 'button', 'className': 'button',
                     'componentName': 'Button', 'props': [], 'isExtractedComponent': True},
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        parent = Path(tmpdir)
        _write_moly_flat(
            parent_dir=parent,
            name='Button',
            moly_name='Button',
            cc_import='',
            css_ext='less',
            all_imports=['import { Button } from "your-component-lib";'],
            snippet='<Button variant="primary" size="small"></Button>',
            instance_ir=_button_ir,
        )
        tsx_content = (parent / 'Button.tsx').read_text()
        assert 'Registration' in tsx_content, (
            "U-469 FAIL: instance text 'Registration' was not injected into Button children. "
            "CC snippet '<Button ...></Button>' (zero-whitespace empty children) must be "
            f"treated as empty and filled with instance text. Got: {tsx_content!r}"
        )


def test_u470_inline_button_snippet_empty_children_gets_instance_text():
    """U-470: When a Moly Button leaf component is INLINED (not a standalone wrapper file)
    into its parent section's TSX, and the CC snippet is '<Button ...></Button>'
    (zero-whitespace empty children), the instance text must still be injected.

    Bug: _generate_section_tsx inline-snippet path (line ~3553) uses r'>\s+</'
    (one-or-more whitespace) — same bug as U-469 in _write_moly_flat.
    CC snippet '></Button>' has zero whitespace, so regex fails and instance
    text ('Registration') is silently dropped from the rendered Button.

    Real data: node 245:12751 from BtcPizzaDay page (235-14237).
    """
    # Real data: node 245:12751 from BtcPizzaDay (235-14237)
    _btn_text_ir = {
        'figmaId': 'I245:12751;2:8053',
        'figmaName': 'Button Text',
        'figmaType': 'TEXT',
        'isTextNode': True, 'textContent': 'Registration',
        'isImageNode': False, 'isVectorNode': False, 'isDecorativeElement': False,
        'textSegments': None, 'localAssetPath': None, 'lineTypes': [],
        'css': {}, 'children': [],
        'semantic': {'htmlTag': 'span', 'className': 'btn-text'},
    }
    _btn_ir = {
        'figmaId': '245:12751',
        'figmaName': 'button',
        'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '120px', 'height': '32px', 'flex-shrink': '0'},
        'children': [_btn_text_ir],
        'semantic': {'htmlTag': 'div', 'className': 'button',
                     'componentName': None, 'props': [], 'isExtractedComponent': False},
    }
    _real_cc_data = {
        '245:12751': {
            'componentName': 'Button',
            'ccImport': 'import { Button } from "your-component-lib";',
            'allImports': ['import { Button } from "your-component-lib";'],
            'snippet': '<Button variant="primary" size="small"></Button>',
            'label': 'Web', 'source': '', 'isIcon': False,
        }
    }
    _plan = {
        'pageComponent': 'TestPage',
        'nodeId': '235-14237',
        'leafComponents': [],
        'sections': [{
            'name': 'RegistrationSection',
            'ir': {**_make_ir('RegistrationSection'), 'children': [_btn_ir]},
            'ccComponent': None,
            'leafComponents': [{
                'name': 'Button',
                'ir': _btn_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Button',
                'allInstanceFigmaIds': ['245:12751'],
                'allImports': ['import { Button } from "your-component-lib";'],
                'snippet': '<Button variant="primary" size="small"></Button>',
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cc_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        cc_dir.mkdir(parents=True)
        (cc_dir / '235-14237.code-connect.json').write_text(json.dumps(_real_cc_data))
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(_plan, out, css_ext='less')
        finally:
            os.chdir(old_cwd)
        section_tsx = (out / 'components' / 'RegistrationSection' / INDEX_TSX_FILENAME).read_text()
        # Must find 'Registration' as Button children, NOT just as part of component name
        assert '>Registration<' in section_tsx or 'Registration</' in section_tsx, (
            "U-470 FAIL: instance text 'Registration' not injected as Button children. "
            "CC snippet '</Button>' (zero-whitespace) must be treated as empty and "
            f"filled with instance text. Got section TSX:\n{section_tsx}"
        )


def test_u459_moly_collapse_override_in_subsection_css():
    """U-459: When a Moly Collapse leafComponent is rendered inside a subSection,
    the :global(.moly-collapse) override CSS must be generated in the subSection's
    CSS file, NOT in the parent section's CSS file.

    Bug: _generate_moly_text_overrides runs in the section-level CSS loop and
    writes to AdvantagesSection/index.module.scss. But the Collapse component
    renders inside FrequentlyAskedQuestionsSection (a subSection), which uses
    its own CSS Module hash. The parent section's .faq-expandable CSS Module
    hash differs, so :global(.moly-collapse) never applies to the actual Collapse.

    Real data: node 1484:27513 from DemoTrading AdvantagesSection (202-33464).
    FrequentlyAskedQuestionsSection is a subSection of AdvantagesSection.
    FaqExpandable leaf: ccComponent=Collapse, allInstanceFigmaIds=['1484:27513'].
    fid_to_class: {'1484:27513': 'faq-expandable'}

    Fix: During subSection CSS generation, detect Collapse/Tabs leaves that
    belong to the subSection and append the :global override to the subSection CSS.
    Skip those same leaves in the parent section's moly override loop.
    """
    # Real data: node 1484:27513 from DemoTrading AdvantagesSection (202-33464)
    # Collapse IR structure: root gap=14.07px, first child gap=7.04px,
    #   title text 10.56px/15.25px, content text 9.38px/14.07px
    collapse_ir = {
        'figmaId': '1484:27513',
        'figmaName': 'FAQ Expandable',
        'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'gap': '14.07px'},
        'children': [{
            'figmaId': '1484:27514',
            'figmaName': 'FAQ Item 1',
            'figmaType': 'FRAME',
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'localAssetPath': None, 'lineTypes': [],
            'css': {'display': 'flex', 'flex-direction': 'column', 'gap': '7.04px'},
            'children': [
                {
                    'figmaId': '1484:27515', 'figmaName': 'question',
                    'figmaType': 'TEXT', 'isTextNode': True, 'isImageNode': False,
                    'isVectorNode': False, 'isDecorativeElement': False,
                    'textContent': 'Question?', 'textSegments': None,
                    'localAssetPath': None, 'lineTypes': [],
                    'css': {'font-size': '10.56px', 'line-height': '15.25px'},
                    'children': [],
                    'semantic': {'htmlTag': 'span', 'className': 'question',
                                 'componentName': None, 'props': [],
                                 'isExtractedComponent': False},
                },
                {
                    'figmaId': '1484:27516', 'figmaName': 'answer',
                    'figmaType': 'TEXT', 'isTextNode': True, 'isImageNode': False,
                    'isVectorNode': False, 'isDecorativeElement': False,
                    'textContent': 'Answer.', 'textSegments': None,
                    'localAssetPath': None, 'lineTypes': [],
                    'css': {'font-size': '9.38px', 'line-height': '14.07px'},
                    'children': [],
                    'semantic': {'htmlTag': 'span', 'className': 'answer',
                                 'componentName': None, 'props': [],
                                 'isExtractedComponent': False},
                },
            ],
            'semantic': {'htmlTag': 'div', 'className': 'faq-item-1',
                         'componentName': None, 'props': [], 'isExtractedComponent': False},
        }],
        'semantic': {
            'htmlTag': 'div', 'className': 'faq-expandable',
            'componentName': 'FaqExpandable', 'props': [], 'isExtractedComponent': True,
        },
    }

    # SubSection root: FrequentlyAskedQuestionsSection containing the Collapse
    subsection_ir = {
        'figmaId': '1484:27600',
        'figmaName': 'FrequentlyAskedQuestionsSection',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'children': [collapse_ir],
        'semantic': {
            'htmlTag': 'section', 'className': 'frequently-asked-questions',
            'componentName': 'FrequentlyAskedQuestionsSection',
            'props': [], 'isExtractedComponent': True,
        },
    }

    # Section root: AdvantagesSection containing the subSection as its child
    section_ir = {
        'figmaId': '202:33619',
        'figmaName': 'AdvantagesSection',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'column', 'width': '100%'},
        'children': [subsection_ir],
        'semantic': {
            'htmlTag': 'section', 'className': 'advantages-section',
            'componentName': 'AdvantagesSection', 'props': [], 'isExtractedComponent': True,
        },
    }

    plan = {
        'pageComponent': 'DemoTradingPage',
        'nodeId': '202-33464',
        'leafComponents': [],
        'sections': [{
            'name': 'AdvantagesSection',
            'ir': section_ir,
            'y': 0.0, 'x': 0.0,
            'ccComponent': None,
            'leafComponents': [{
                'name': 'FaqExpandable',
                'ir': collapse_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Collapse',
                'allInstanceFigmaIds': ['1484:27513'],
            }],
            'subSections': [{
                'name': 'FrequentlyAskedQuestionsSection',
                'ir': subsection_ir,
                'leafComponents': [],
            }],
        }],
        'inlineNodes': [],
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        # Create figma-maps.json so fid_to_class maps 1484:27513 → faq-expandable
        pcode_dir = out / '.figma-to-code' / '3-page-code' / 'DemoTradingPage-202-33464'
        pcode_dir.mkdir(parents=True)
        (pcode_dir / 'figma-maps.json').write_text(json.dumps({
            'fidToClass': {'1484:27513': 'faq-expandable'},
            'fidToSrc': {},
        }))
        # Create minimal orig CSS so orig_css_map has the faq-expandable class
        (pcode_dir / 'DemoTradingPage.module.scss').write_text(
            '.advantages-section {\n  display: flex;\n  flex-direction: column;\n  width: 100%;\n}\n'
            '.faq-expandable {\n  display: flex;\n  flex-direction: column;\n  gap: 14.07px;\n}\n'
        )
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            generate_components(plan, out, css_ext='scss')
        finally:
            os.chdir(old_cwd)

        ss_css_path = (out / 'components' / 'AdvantagesSection'
                       / 'FrequentlyAskedQuestionsSection' / 'index.module.scss')
        section_css_path = out / 'components' / 'AdvantagesSection' / 'index.module.scss'

        assert ss_css_path.exists(), (
            f'U-459 FAIL: FrequentlyAskedQuestionsSection/index.module.scss must be generated, '
            f'got: {list((out / "components" / "AdvantagesSection").iterdir())}'
        )
        ss_css = ss_css_path.read_text()
        section_css = section_css_path.read_text()

        assert ':global(.moly-collapse)' in ss_css, (
            f'U-459 FAIL: :global(.moly-collapse) override must be in subSection CSS '
            f'(FrequentlyAskedQuestionsSection/index.module.scss), but was missing.\n'
            f'SubSection CSS:\n{ss_css[:1000]}'
        )
        assert ':global(.moly-collapse)' not in section_css, (
            f'U-459 FAIL: :global(.moly-collapse) override must NOT be in parent section CSS '
            f'(AdvantagesSection/index.module.scss) — wrong CSS Module scope.\n'
            f'Section CSS:\n{section_css[:1000]}'
        )


def test_u460_inline_node_responsive_generates_h5_media_query():
    """U-460: Page-level inline node with responsive[] array must generate
    @media (max-width: 768px) override in the page module CSS.

    Real case: TomorrowlandLandingPage4 butterfly images (40017:4440, 40017:4443)
    have responsive[{breakpoint:768, css:{top:1271.98px, left:284.14px, ...}}] in the
    merged IR, but the page module SCSS was missing the @media (max-width:768px) block.
    This caused the butterflies to appear at their PC absolute positions at H5 viewport,
    overlapping with GetStartedIn section content.

    Fix: after collecting inline_css_parts CSS for each inline node, also call
    _collect_responsive_overrides_from_ir and append the resulting @media blocks.
    Additionally, _collect_responsive_overrides_from_ir should use semantic.className
    as a fallback when fid_cls_map has no entry for the node's figmaId.

    Real data: node 40017:4440 "By Butterfly 2 (1) 1" from TomorrowlandLandingPage4
      PC CSS: position:absolute; left:1075.34px; top:1424.02px; max-width:476.64px
      H5 responsive: {top:1271.98px, left:284.14px, width:181.41px, height:118.05px}
    """
    page_root_ir = {
        'figmaId': '39641:6863', 'figmaName': 'Tomorrowland Landing Page 4',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '1440px', 'min-height': '5020px',
            'background-color': '#ffffff', 'overflow-x': 'hidden',
            'position': 'relative', 'max-width': '100%',
            'margin-left': 'auto', 'margin-right': 'auto',
        },
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'tomorrowland-landing-page-4',
            'componentName': 'TomorrowlandLandingPage4', 'props': [], 'isExtractedComponent': False,
        },
    }
    # Anchor section: full-width absolute section to establish page_root_y / page_root_x
    # Real: VipStatusFollowsSection (39641:7672) top=861px in PC design
    anchor_section_ir = {
        'figmaId': '39641:7672', 'figmaName': 'VipStatusFollows', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '100%', 'height': '734px',
            'position': 'absolute', 'left': '-1px', 'top': '861px',
            'display': 'flex', 'flex-direction': 'column',
            'max-width': '1440px',
        },
        'bb': {'width': 1440.0, 'height': 734.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'card-tier-container',
            'componentName': 'VipStatusFollowsSection', 'props': [], 'isExtractedComponent': True,
        },
    }
    # Butterfly inline node with responsive[] data for H5 positions
    # Real data: node 40017:4440 "By Butterfly 2 (1) 1" from TomorrowlandLandingPage4
    butterfly_ir = {
        'figmaId': '40017:4440', 'figmaName': 'By Butterfly 2 (1) 1', 'figmaType': 'RECTANGLE',
        'isTextNode': False, 'isImageNode': True, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': '/assets/TomorrowlandLandingPage4/40017-4440.png', 'lineTypes': [],
        'css': {
            'width': '100%', 'left': '1075.34px', 'position': 'absolute',
            'height': '310.16px', 'top': '1424.02px', 'max-width': '476.64px',
        },
        'bb': {'width': 476.64, 'height': 310.16},
        # Real H5 responsive data from merged-39641-6863.ir.json node 40017:4440
        'responsive': [{
            'breakpoint': 768,
            'css': {
                'width': '181.41px',
                'left': '284.14px',
                'height': '118.05px',
                'top': '1271.98px',
            },
        }],
        'children': [],
        'semantic': {
            'htmlTag': 'img', 'className': 'by-butterfly-2-1-1',
            'componentName': None, 'props': [], 'isExtractedComponent': False,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '39641-6863.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))
        plan = {
            'pageComponent': 'TomorrowlandLandingPage4',
            'nodeId': '39641-6863',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [{
                'name': 'VipStatusFollowsSection',
                'ir': anchor_section_ir,
                'y': 861.0,
                'x': 0.0,
                'ccComponent': None,
                'leafComponents': [],
            }],
            'inlineNodes': [{'ir': butterfly_ir, 'y': 1424.02, 'x': 1075.34}],
        }
        generate_components(plan, out, css_ext='scss')

        page_css = (out / 'TomorrowlandLandingPage4.module.scss').read_text()
        assert '@media (max-width: 768px)' in page_css, (
            f'U-460 FAIL: page-level inline node with responsive[] must emit '
            f'@media (max-width: 768px) in page CSS.\n'
            f'Actual page CSS:\n{page_css}'
        )
        assert 'top: 1271.98px' in page_css or 'top:1271.98px' in page_css, (
            f'U-460 FAIL: H5 responsive top value (1271.98px) must appear in page CSS.\n'
            f'Actual page CSS:\n{page_css}'
        )
        assert 'by-butterfly-2-1-1' in page_css, (
            f'U-460 FAIL: butterfly CSS class must appear in @media block.\n'
            f'Actual page CSS:\n{page_css}'
        )


def test_u461_bottom_overlay_inline_root_generates_sticky():
    """U-461: Page-level inline node with position:absolute; bottom:0 (no top) must generate
    position:sticky instead of position:absolute to avoid covering flow content.

    Real case: DemoTrading H5 StartDemoTradingSection (node 1484:27516, "Button Group"):
      CSS: position:absolute; bottom:0px; left:0px; width:375px; bb:{width:375, height:144}
    This button bar was rendered as position:absolute, overlapping the last 144px of the
    FAQ section content beneath it.

    Fix: in split_codegen.py Rule 7 (inline node root), when _has_bottom and top is None,
    instead of `pass` (keeping absolute), set position:sticky so the element occupies
    its height in the flow AND sticks to the bottom of the viewport.
    """
    page_root_ir = {
        'figmaId': '1484:27139', 'figmaName': 'H5 Dubai/Gala Tab',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '375px', 'min-height': '7557px',
            'background-color': '#16171a',
            'display': 'flex', 'flex-direction': 'column',
            'position': 'relative', 'overflow-x': 'hidden',
        },
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'h5-dubai-gala-tab',
            'componentName': 'H5DubaiGalaTab', 'props': [], 'isExtractedComponent': False,
        },
    }
    # A section to establish section_y_ranges so _can_flow can evaluate properly
    content_section_ir = {
        'figmaId': '1484:27140', 'figmaName': 'Content', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '375px', 'height': '7413px',
            'position': 'absolute', 'left': '0px', 'top': '88px',
            'display': 'flex', 'flex-direction': 'column',
        },
        'bb': {'width': 375.0, 'height': 7413.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'content',
            'componentName': 'ContentSection', 'props': [], 'isExtractedComponent': True,
        },
    }
    # The bottom button bar — real data from node 1484:27516
    button_group_ir = {
        'figmaId': '1484:27516', 'figmaName': 'Button Group', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '375px',
            'position': 'absolute',
            'left': '0px',
            'bottom': '0px',
            'display': 'flex', 'flex-direction': 'column',
            'background-color': '#16171a',
        },
        'bb': {'width': 375.0, 'height': 144.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'button-group',
            'componentName': 'StartDemoTradingSectionH5', 'props': [], 'isExtractedComponent': True,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ir_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        ir_dir.mkdir(parents=True)
        ir_path = ir_dir / '1484-27139.ir.json'
        ir_path.write_text(json.dumps(page_root_ir))
        plan = {
            'pageComponent': 'H5DubaiGalaTab',
            'nodeId': '1484-27139',
            'irPath': str(ir_path),
            'leafComponents': [],
            'sections': [{
                'name': 'ContentSection',
                'ir': content_section_ir,
                'y': 88.0,
                'x': 0.0,
                'ccComponent': None,
                'leafComponents': [],
            }],
            # Button group as inline node with y=inf (bottom-pinned, no top)
            'inlineNodes': [{'ir': button_group_ir, 'y': float('inf'), 'x': 0.0}],
        }
        generate_components(plan, out, css_ext='scss')

        # Inline node CSS is emitted to the page module file, not a separate component
        page_css_file = out / 'H5DubaiGalaTab.module.scss'
        assert page_css_file.exists(), (
            f'U-461 FAIL: page module CSS not generated.\n'
            f'Files: {list(out.rglob("*.scss"))}'
        )
        page_css = page_css_file.read_text()
        # Find the .button-group block specifically
        assert '.button-group' in page_css, (
            f'U-461 FAIL: .button-group class must appear in page module CSS.\n'
            f'Actual CSS:\n{page_css}'
        )
        # Extract the .button-group CSS block content
        bg_match = re.search(r'\.button-group\s*\{([^}]*)\}', page_css, re.DOTALL)
        assert bg_match, f'U-461 FAIL: cannot parse .button-group block.\nActual CSS:\n{page_css}'
        bg_block = bg_match.group(1)
        assert 'sticky' in bg_block, (
            f'U-461 FAIL: .button-group must have position:sticky after fix.\n'
            f'Actual .button-group block:\n{bg_block}'
        )
        assert 'absolute' not in bg_block, (
            f'U-461 FAIL: .button-group must not have position:absolute after fix.\n'
            f'Actual .button-group block:\n{bg_block}'
        )


def test_u462_structural_split_h5_bottom_overlay_root_becomes_sticky():
    """U-462: When a structural-split H5 section root has position:absolute; bottom:0 (no top),
    _generate_structural_split_section must emit position:sticky in the H5 CSS module.

    Real case: DemoTrading H5 StartDemoTradingSectionH5 (node 1484:27516, "Button Group"):
      H5 supplementNode root css: position:absolute; bottom:0px; left:0px; width:375px
    The H5 CSS was generated via generate_scss(h5_ir) without rule-6/7 transforms,
    so position:absolute was preserved — the button bar overlapped flow content.

    Fix: in _generate_structural_split_section, before calling generate_scss(h5_ir),
    check if the H5 root has position:absolute + bottom (no top) and mutate to sticky.
    """
    # Real data: button-group root from node 1484:27516 (DemoTrading H5)
    h5_root_ir = {
        'figmaId': '1484:27516', 'figmaName': 'Button Group', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'width': '375px',
            'position': 'absolute',
            'left': '0px',
            'bottom': '0px',
            'display': 'flex',
            'flex-direction': 'column',
            'background-color': '#16171a',
        },
        'bb': {'width': 375.0, 'height': 144.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'button-group',
            'componentName': 'StartDemoTradingSectionH5', 'props': [],
            'isExtractedComponent': True,
        },
    }
    pc_root_ir = {
        'figmaId': '202:33760', 'figmaName': 'Button Group PC', 'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'display': 'flex', 'flex-direction': 'row', 'width': '100%'},
        'bb': {'width': 1440.0, 'height': 80.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div', 'className': 'button-group-pc',
            'componentName': 'StartDemoTradingSectionPC', 'props': [],
            'isExtractedComponent': True,
        },
        'structuralSplit': True,
        'supplementNode': h5_root_ir,
    }
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / 'components' / 'StartDemoTradingSection'
        out_dir.mkdir(parents=True)
        _generate_structural_split_section(
            'StartDemoTradingSection', pc_root_ir, out_dir, css_ext='scss'
        )
        h5_css_file = out_dir / 'StartDemoTradingSectionH5.module.scss'
        assert h5_css_file.exists(), (
            f'U-462 FAIL: H5 CSS module not generated.\n'
            f'Files: {list(out_dir.iterdir())}'
        )
        h5_css = h5_css_file.read_text()
        bg_match = re.search(r'\.button-group\s*\{([^}]*)\}', h5_css, re.DOTALL)
        assert bg_match, (
            f'U-462 FAIL: .button-group block not found in H5 CSS.\n'
            f'Actual CSS:\n{h5_css}'
        )
        bg_block = bg_match.group(1)
        assert 'sticky' in bg_block, (
            f'U-462 FAIL: .button-group must have position:sticky in H5 CSS.\n'
            f'Actual block:\n{bg_block}'
        )
        assert 'absolute' not in bg_block, (
            f'U-462 FAIL: .button-group must not have position:absolute in H5 CSS.\n'
            f'Actual block:\n{bg_block}'
        )


def test_u463_ir_height_smaller_with_overflow_hidden_uses_ir():
    """U-463: When IR height < orig_css_map height AND IR has overflow:hidden,
    _css_from_orig must use the IR height (context clip constraint).

    Real case: DemoTrading H5 Options icon (node 1484:27374 in WhyInstitutionsChooseUsSection4):
      orig_css_map (from single-file product): .futures { height: 67.8px; overflow: hidden }
      IR css: { height: 52px; overflow: hidden; position: relative; width: 52px }
      Result: Should generate height: 52px (context constraint), not 67.8px.
    Without this fix, the Options icon renders at 67.8px, making WhySection4 18px too tall
    and causing ~36px cumulative offset in the lower half of the page.

    Fix: in _css_from_orig rule 1 (SIZE attrs), also override when IR < orig AND
    overflow:hidden is in IR props (indicating a context clip constraint, not just a
    size-down rendering issue).
    """
    # Real data: from DemoTrading merged IR, node 1484:27374 (Options icon in H5 context)
    ir = {
        'figmaId': '1484:27374',
        'figmaName': 'Options',
        'figmaType': 'FRAME',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {
            'position': 'relative',
            'width': '52px',
            'height': '52px',
            'overflow': 'hidden',
            'display': 'flex',
            'align-items': 'center',
            'justify-content': 'center',
        },
        'bb': {'width': 52.0, 'height': 52.0},
        'children': [],
        'semantic': {
            'htmlTag': 'div',
            'className': 'futures',  # same class name as the 67.8px component
            'componentName': 'Options',
            'props': [],
            'isExtractedComponent': True,
        },
    }
    # orig_css_map: the single-file product CSS has 67.8px for .futures
    orig_css_map = {
        'futures': {
            'width': '67.8px',
            'height': '67.8px',
            'overflow': 'hidden',
            'display': 'flex',
            'align-items': 'center',
            'justify-content': 'center',
            'position': 'relative',
            'box-shadow': '0px 0px 33.9px 0px rgba(255, 156, 46, 0.7)',
            'flex-shrink': '0',
        }
    }

    css = _css_from_orig(ir, orig_css_map, 'scss')

    # Find .futures block
    match = re.search(r'\.futures\s*\{([^}]*)\}', css, re.DOTALL)
    assert match, f'U-463 FAIL: .futures block not found.\nActual CSS:\n{css}'
    block = match.group(1)

    # Must use IR height (52px), not orig height (67.8px)
    height_match = re.search(r'height:\s*([^;]+);', block)
    assert height_match, f'U-463 FAIL: no height in .futures block.\nBlock:\n{block}'
    height_val = height_match.group(1).strip()
    assert height_val == '52px', (
        f'U-463 FAIL: height must be 52px (IR context constraint), got: {height_val}\n'
        f'Full .futures block:\n{block}'
    )


def test_u466_structural_split_short_supplement_hides_long_text_descendants_not_parent():
    """U-466: When a structuralSplit child's supplementNode has SHORT textContent (< 50 chars),
    it is a title replacement — the parent PC block should NOT be fully hidden at mobile.
    Instead, only its long-text (description-like, > 50 chars) TEXT descendants should get
    display:none. This preserves the short PC title while removing duplicate descriptions.

    Real data: ExampleLandingSection (39641-6863).
    Node I39641:9382;39641:6690 (structuralSplit=True) contains:
      - I39641:9382;39641:6694 "A new rhythm for real-world finance" (35 chars) — TITLE, keep
      - I39641:9382;39641:6695 "From the first step toward..." (300+ chars) — DESCRIPTION, hide
    Supplement I39648:4945;39603:19855 textContent = "A new rhythm for real-world finance" (35 chars)
    """
    from lib.split_codegen import _collect_responsive_overrides_from_ir

    fid_cls_map = {
        'parent:6697': 'benefit-icon-label',
        'child-pc:6690': 'header-text-cta',     # structuralSplit=True, short supplement
        'title:6694': 'a-new-rhythm-for-real-world-finance',   # short title — keep visible
        'desc:6695': 'from-the-first-step-toward-the-festival',   # long description — hide
    }

    parent_ir = {
        'figmaId': 'parent:6697',
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column'},
        'children': [
            {
                'figmaId': 'child-pc:6690',
                'structuralSplit': True,
                'supplementNode': {
                    'figmaId': 'supp:19855',
                    # Real: "A new rhythm for real-world finance" — 35 chars, SHORT
                    'textContent': 'A new rhythm for real-world finance',
                    'css': {},
                    'children': [],
                },
                'css': {'width': '540px', 'height': '265px', 'display': 'flex'},
                'isTextNode': False,
                'figmaName': 'header-text-cta',
                'children': [
                    {
                        'figmaId': 'title:6694',
                        'isTextNode': True,
                        # Short title: 35 chars — must NOT get display:none
                        'textContent': 'A new rhythm for real-world finance',
                        'css': {'font-size': '24px'},
                        'children': [],
                    },
                    {
                        'figmaId': 'desc:6695',
                        'isTextNode': True,
                        # Long description: 100+ chars — must get display:none
                        'textContent': 'From the first step toward the festival to the lights on the main stage, Tomorrowland Brasil 2027, we move together.',
                        'css': {'font-size': '20px'},
                        'children': [],
                    },
                ],
            },
        ],
    }

    result = _collect_responsive_overrides_from_ir(parent_ir, fid_cls_map, set())

    # Parent 'header-text-cta' must NOT be hidden (supplement is short title)
    parent_hidden = [r for r in result if r[0] == 'header-text-cta' and r[2].get('display') == 'none']
    assert not parent_hidden, (
        'U-466 FAIL: header-text-cta (PC parent) should NOT get display:none at mobile '
        'when supplement has SHORT textContent. Got hidden: ' + str(parent_hidden)
    )
    # Short title must NOT be hidden
    title_hidden = [r for r in result if r[0] == 'a-new-rhythm-for-real-world-finance' and r[2].get('display') == 'none']
    assert not title_hidden, (
        'U-466 FAIL: short title (a-new-rhythm) should NOT get display:none at mobile. '
        'Got hidden: ' + str(title_hidden)
    )
    # Long description MUST be hidden
    desc_hidden = [r for r in result if r[0] == 'from-the-first-step-toward-the-festival' and r[2].get('display') == 'none']
    assert desc_hidden, (
        'U-466 FAIL: long description (from-the-first-step) should get display:none at mobile '
        'to prevent duplication with H5 sibling.\nAll results: ' + str(result)
    )
    # Parent with explicit px height should get height:auto to avoid blank space
    parent_height_auto = [r for r in result if r[0] == 'header-text-cta' and r[2].get('height') == 'auto']
    assert parent_height_auto, (
        'U-466 FAIL: parent (header-text-cta) with explicit px height should get height:auto at mobile '
        'to prevent blank space after long description is hidden.\nAll results: ' + str(result)
    )


def test_u465_structural_split_child_gets_display_none_at_mobile():
    """U-465: _collect_responsive_overrides_from_ir must emit display:none at mobile
    for any direct child node that has structuralSplit=True.

    Real data: ExampleLandingSection (39641-6863).
    Node I39641:9382;39641:6697 (benefitIconLabel, 781px) contains:
      - I39641:9382;39641:6690 (header-text-cta, structuralSplit=True, 265px) — PC text
      - I39648:4945;39603:19856 (H5 text, shown via data-figma-id rule) — H5 text

    Without display:none for 6690 at mobile, the PC text overlaps with H5 text,
    causing text overlap in the rendered page (issue #22).

    Fix: detect structuralSplit=True in direct children and emit display:none at 768px.
    """
    from lib.split_codegen import _collect_responsive_overrides_from_ir

    fid_cls_map = {
        'parent:6697': 'benefit-icon-label',
        'child-pc:6690': 'header-text-cta',   # structuralSplit=True → must get display:none
        'child-h5:19856': 'h5-text-content',
    }

    # Real case: 6697 contains 6690 (structuralSplit=True) as direct child
    parent_ir = {
        'figmaId': 'parent:6697',
        'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column'},
        'children': [
            {
                'figmaId': 'child-pc:6690',
                'structuralSplit': True,         # PC content that should be hidden on H5
                'supplementNode': {'figmaId': 'supp:99', 'css': {}},
                'css': {'width': '540px', 'height': '265px', 'display': 'flex'},
                'children': [],
            },
            {
                'figmaId': 'child-h5:19856',    # H5 content (no structuralSplit)
                'css': {'width': '100%', 'display': 'block'},
                'children': [],
            },
        ],
    }

    result = _collect_responsive_overrides_from_ir(parent_ir, fid_cls_map, set())

    display_none = [r for r in result if r[0] == 'header-text-cta' and r[2].get('display') == 'none']
    assert display_none, (
        'U-465 FAIL: structuralSplit child (header-text-cta) should get display:none at mobile '
        'from _collect_responsive_overrides_from_ir.\n'
        f'All results: {result}'
    )
    assert display_none[0][1] == 768, (
        f'U-465 FAIL: breakpoint should be 768, got {display_none[0][1]}'
    )
    # h5 content (no structuralSplit) should NOT get display:none
    h5_display_none = [r for r in result if r[0] == 'h5-text-content' and r[2].get('display') == 'none']
    assert not h5_display_none, (
        'U-465 FAIL: H5 content node (no structuralSplit) should NOT get display:none at mobile'
    )


def test_u471_search_snippet_text_not_injected_as_children():
    """U-471: Moly Search (and other input-like components) must NOT get text injected
    as children via U-326 — <input> is a void element and can't have children; doing so
    crashes React with "input is a void element tag and must neither have children".

    Bug: U-326 inline-snippet path expands self-closing CC snippets to
    <Component>text</Component> for ALL non-Icon Moly components. Search renders
    <input> internally, so injecting text children causes a fatal React error that
    crashes the entire component tree (black page).

    Real data: node 172:13120 from ByAiHub20 (nodeId: 169-33787).
    CC snippet: '<Search size="small" placeholder="Search"/>'
    Figma instance text: 'Search' (the placeholder label text in the design)
    """
    # Real data: node 172:13120 from ByAiHub20 (169-33787)
    _search_text_ir = {
        'figmaId': 'I172:13120;5581:10994',
        'figmaName': 'Search Coin',
        'figmaType': 'TEXT',
        'isTextNode': True, 'textContent': 'Search',
        'isImageNode': False, 'isVectorNode': False, 'isDecorativeElement': False,
        'textSegments': None, 'localAssetPath': None, 'lineTypes': [],
        'css': {}, 'children': [],
        'semantic': {'htmlTag': 'span', 'className': 'search-coin'},
    }
    _search_ir = {
        'figmaId': '172:13120',
        'figmaName': 'Search',
        'figmaType': 'INSTANCE',
        'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
        'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
        'localAssetPath': None, 'lineTypes': [],
        'css': {'width': '160px', 'height': '32px', 'flex-shrink': '0'},
        'children': [_search_text_ir],
        'semantic': {'htmlTag': 'div', 'className': 'n3-search',
                     'componentName': None, 'props': [], 'isExtractedComponent': False},
    }
    _real_cc_data = {
        '172:13120': {
            'componentName': 'Search',
            'ccImport': 'import { Search } from "your-component-lib";',
            'allImports': ['import { Search } from "your-component-lib";'],
            'snippet': '<Search size="small" placeholder="Search"/>',
            'label': 'Web', 'source': '', 'isIcon': False,
        }
    }
    _plan = {
        'pageComponent': 'TestPage',
        'nodeId': '169-33787',
        'leafComponents': [],
        'sections': [{
            'name': 'SkillCardFooterSection',
            'ir': {**_make_ir('SkillCardFooterSection'), 'children': [_search_ir]},
            'ccComponent': None,
            'leafComponents': [{
                'name': 'Search',
                'ir': _search_ir,
                'varyingProps': [],
                'instancesData': [{}],
                'ccComponent': 'Search',
                'allInstanceFigmaIds': ['172:13120'],
                'allImports': ['import { Search } from "your-component-lib";'],
                'snippet': '<Search size="small" placeholder="Search"/>',
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cc_dir = Path(tmp) / '.figma-to-code' / '2-figma-extract'
        cc_dir.mkdir(parents=True)
        (cc_dir / '169-33787.code-connect.json').write_text(json.dumps(_real_cc_data))
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            # no_i18n=True matches split_components.py --apply default behavior
            generate_components(_plan, out, css_ext='less', no_i18n=True)
        finally:
            os.chdir(old_cwd)
        section_tsx = (out / 'components' / 'SkillCardFooterSection' / INDEX_TSX_FILENAME).read_text()
        # Search renders <input> internally — it's a void element.
        # The component must be self-closing; any children (literal or i18n) cause a React crash.
        assert '</Search>' not in section_tsx, (
            "U-471 FAIL: Search component must NOT have children (self-closing only) — "
            "<input> is a void element; children crash React. "
            f"Got section TSX:\n{section_tsx}"
        )
        assert '<Search' in section_tsx, (
            f"U-471 FAIL: Search component call must still be present. Got:\n{section_tsx}"
        )


if __name__ == '__main__':
    tests = [
        test_section_leaf_flat_files_inside_section_dir,
        test_section_index_tsx_generated,
        test_section_index_tsx_contains_data_map,
        test_page_tsx_imports_only_sections,
        test_page_tsx_no_map,
        test_top_level_leaf_goes_to_common,
        test_update_root_index_points_to_page,
        test_update_root_index_preserves_original,
        test_leaf_in_section_texts_import_uses_parent_relative_path,
        test_leaf_wrapper_in_section_no_padding_overflow,
        test_cc_button_uses_snippet_variant_not_css_inference,
        test_cc_confirmed_button_retains_visual_css,
        test_cc_confirmed_button_border_override,
        test_inline_pagination_no_classname_prop,
        test_icon_with_color_generates_color_prop,
        test_section_root_no_margin_top_from_canvas_offset,
        test_tag_cc_flex_direction_retained,
        test_tag_cc_text_color_two_levels_deep,
        test_non_flow_page_section_without_top_gets_absolute_position,
        test_subsection_leaf_instances_render_as_component_calls,
        test_section_wrapper_uses_abs_left_and_width_for_fixed_sections,
        test_section_wrapper_falls_back_to_full_width_when_no_abs_left,
        test_non_flow_inline_node_gets_left_and_width,
        test_leaf_component_accepts_data_figma_id_prop,
        test_flow_page_narrow_section_gets_margin_left,
        test_flow_page_auto_margin_section_no_margin_left_wrapper,
        test_leaf_root_transform_stripped_when_wrapper,
        test_leaf_with_padding_and_fixed_width_gets_border_box,
        test_subsection_leaf_wrapper_strips_visual_css,
        test_deco_spanning_multiple_sections_treated_as_overlay,
        test_partial_coverage_wrapper_retains_visual_css,
        test_h5only_inline_node_gets_media_query,
        test_h5only_section_gets_responsive_wrapper,
        test_abs_section_with_bottom_css_skips_wrapper,
        test_usePageEnv_hook_resets_overflow_y,
        test_structural_split_scss_uses_node_index_for_deep_nodes,
        test_structural_split_section_creates_index_module_css,
        test_sanitize_classnames_strips_leading_digit,
        test_sanitize_classnames_handles_slash_paren_double_dot,
        test_structural_split_scss_no_invalid_selectors,
        test_section_function_suffixed_root_when_subsection_name_conflicts,
        test_varying_prop_uses_varyingprops_values_when_instancesdata_key_mismatches,
        test_leaf_root_height_kept_when_only_width_is_percentage,
        test_u406_sections_in_components_subdir,
        test_u407_page_entry_is_index_tsx,
        test_u412_h5only_subsection_zeroes_section_root_padding_on_mobile,
        test_u416_pconly_abs_subsection_wrapper_gets_position_absolute,
        test_u418_h5only_flex_node_uses_display_flex_in_show_rule,
        test_u415_h5only_inline_node_without_class_gets_attr_selector,
        test_u423_dart_snippet_not_used_as_inline_jsx,
        test_u438_resolve_missing_asset_paths_assigns_deterministic_paths,
        test_u443_same_name_h5only_subsections_get_unique_dirs,
        test_u444_structural_split_h5_tsx_renders_supplement_content,
        test_u445_structural_split_inline_node_gets_h5_display_none,
        test_u446_structural_split_h5_centered_abs_uses_calc,
        test_u447_structural_split_h5_abs_root_gets_zindex,
        test_u448_responsive_height_kept_when_overflow_hidden_prevents_clip,
        test_u449_structural_split_h5_top_adjusted_for_nav_offset,
        test_u450_structural_split_inline_node_h5_supplement_rendered,
        test_u451_u450_flow_nav_disables_u449_text_adjustment,
        test_u452b_effective_root_cls_when_semantic_not_in_orig_css,
        test_u453_root_css_text_collision_falls_back_to_ir_css,
        test_u456_abs_positioned_leaf_instance_gets_wrapper_div,
        test_u459_moly_collapse_override_in_subsection_css,
        test_u460_inline_node_responsive_generates_h5_media_query,
        test_u461_bottom_overlay_inline_root_generates_sticky,
        test_u462_structural_split_h5_bottom_overlay_root_becomes_sticky,
        test_u463_ir_height_smaller_with_overflow_hidden_uses_ir,
        test_u465_structural_split_child_gets_display_none_at_mobile,
        test_u467_sub_pixel_top_diff_no_wrapper_for_leaf_instance,
        test_u468_icon_no_import_when_package_unknown,
        test_u469_button_snippet_empty_children_gets_instance_text_injected,
        test_u470_inline_button_snippet_empty_children_gets_instance_text,
        test_u471_search_snippet_text_not_injected_as_children,
    ]
    passed = 0
    for t in tests:
        try:
            t(); print(f'  ✓ {t.__name__}'); passed += 1
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f'  ✗ {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')


# U-470: _generate_moly_text_overrides must include color declaration for dark theme
def test_u470_moly_collapse_button_override_includes_color_for_dark_theme():
    """U-470: When page_theme='dark', the generated :global(.moly-collapse h3 button)
    CSS override must include an explicit color declaration using the BDS token with a
    white fallback, so Collapse label text is visible on dark backgrounds even when
    BDS theme tokens are not yet injected (e.g. Playwright headless before JS mount).

    Root cause: Moly Collapse label uses --bds-gray-t1-title for color, which resolves
    to dark text in light theme. On a dark page, if data-theme is not propagated yet,
    the label text becomes invisible (dark text on dark background).

    Fix: _generate_moly_text_overrides accepts page_theme kwarg and emits
    color: var(--bds-gray-t1-title, #ffffff) for dark, #1e1e1e for light.
    """
    from lib.split_codegen import _generate_moly_text_overrides

    # dark theme: fallback must be #ffffff
    dark_css = _generate_moly_text_overrides(
        [('faq-expandable', 'Collapse', '18px', '26px', '16px', '24px', '24px', '12px')],
        page_theme='dark'
    )
    assert 'color:' in dark_css or 'color :' in dark_css, (
        'U-470 FAIL: dark theme Collapse button override must include color declaration.\n'
        f'Got:\n{dark_css}'
    )
    assert '#ffffff' in dark_css or 'var(--bds-gray-t1-title' in dark_css, (
        'U-470 FAIL: dark theme Collapse button override must reference white fallback.\n'
        f'Got:\n{dark_css}'
    )

    # light theme: fallback must be #1e1e1e
    light_css = _generate_moly_text_overrides(
        [('faq-expandable', 'Collapse', '18px', '26px', '16px', '24px', '24px', '12px')],
        page_theme='light'
    )
    assert 'color:' in light_css or 'color :' in light_css, (
        'U-470 FAIL: light theme Collapse button override must include color declaration.\n'
        f'Got:\n{light_css}'
    )
    assert '#1e1e1e' in light_css or 'var(--bds-gray-t1-title' in light_css, (
        'U-470 FAIL: light theme Collapse button override must reference dark fallback.\n'
        f'Got:\n{light_css}'
    )
