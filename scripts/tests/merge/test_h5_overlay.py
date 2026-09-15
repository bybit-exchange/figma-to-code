import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.paths import INDEX_TSX_FILENAME


def _write_fake_component(comp_dir: Path, name: str) -> None:
    comp_dir.mkdir(parents=True, exist_ok=True)
    (comp_dir / INDEX_TSX_FILENAME).write_text(f'export default function {name}() {{ return <div /> }}\n')
    (comp_dir / 'index.module.less').write_text(f'.{name.lower()} {{\n  display: flex;\n}}\n')


def test_overlay_appends_import_to_existing_less(tmp_path):
    """index.module.less 末尾追加 @import，且不重复追加"""
    from h5_overlay import append_h5_import
    less_path = tmp_path / 'index.module.less'
    less_path.write_text('.hero {\n  display: flex;\n}\n')
    append_h5_import(less_path)
    content = less_path.read_text()
    assert "@import './index.h5.module.less'" in content
    # 幂等：再追加一次不重复
    append_h5_import(less_path)
    assert content.count("@import './index.h5.module.less'") == 1


def test_overlay_does_not_modify_original_tsx(tmp_path):
    """original index.tsx 内容不被修改（只有结构相似时）"""
    from h5_overlay import write_css_overlay
    comp_dir = tmp_path / 'HeroSection'
    _write_fake_component(comp_dir, 'HeroSection')
    original_tsx = (comp_dir / INDEX_TSX_FILENAME).read_text()
    h5_css = {'padding': '16px', 'font-size': '14px'}
    write_css_overlay(comp_dir, h5_css, breakpoint=768, css_ext='less')
    assert (comp_dir / INDEX_TSX_FILENAME).read_text() == original_tsx
    assert (comp_dir / 'index.h5.module.less').exists()


def test_overlay_less_contains_media_query(tmp_path):
    """生成的 .h5.module.less 包含正确的 @media 规则"""
    from h5_overlay import write_css_overlay
    comp_dir = tmp_path / 'EarnSection'
    _write_fake_component(comp_dir, 'EarnSection')
    h5_css = {'padding': '12px 16px', 'flex-direction': 'column'}
    write_css_overlay(comp_dir, h5_css, breakpoint=768, css_ext='less')
    content = (comp_dir / 'index.h5.module.less').read_text()
    assert '@media (max-width: 768px)' in content
    assert 'padding: 12px 16px' in content
    assert 'flex-direction: column' in content


def test_match_h5_sections_to_components():
    """H5 section 按文本相似度匹配到既有组件名"""
    from h5_overlay import match_h5_to_components
    h5_sections = [
        {'figmaName': 'Section 1', 'figmaType': 'FRAME', 'visible': True,
         'children': [{'figmaType': 'TEXT', 'isTextNode': True, 'textContent': 'Earn cashback',
                       'visible': True, 'children': []}],
         'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 375, 'height': 300},
         'css': {}, 'fills': []},
    ]
    component_names = ['EarnSection', 'HeroSection', 'FooterSection']
    component_texts = {
        'EarnSection': {'Earn cashback', 'Register now', 'Get rewards'},
        'HeroSection': {'Welcome to ByEU', 'Start trading'},
        'FooterSection': {'Privacy Policy', 'Terms'},
    }
    matches = match_h5_to_components(h5_sections, component_names, component_texts)
    assert matches[0] == 'EarnSection'
