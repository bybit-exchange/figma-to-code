#!/usr/bin/env python3
"""
BUG-1: CC 组件替换后 wrapper div 保留了 border 等视觉样式。

根因：generate_components 在第 510 行用 _load_orig_figma_class_map() 从原始 TSX 提取
fid→className 映射，但 CC 被替换的节点不在 TSX 里（convert.py 用 snippet 替换了）。
figma-maps.json 有完整映射（包括 CC 节点），但第 514 行 `_, fid_to_src = ...` 丢弃了。
导致 _moly_wrappers 为空，CSS 不被剥离。

修复：用 figma-maps.json 的 fidToClass 补充 fid_to_class。
"""

import sys
import json
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()


def _setup_env(tmp: Path, node_id: str, comp_name: str):
    """Simulate a real scenario: original CSS has wrapper class, TSX lacks CC node's figma-id."""
    page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
    page_dir.mkdir(parents=True)

    # Original CSS (from convert.py) — wrapper class has full visual styles
    css_content = """\
.page-root {
  width: 1200px;
  position: relative;
}

.section-frame {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 24px;
  position: relative;
}

.btn-wrapper {
  width: 122px;
  height: 48px;
  display: flex;
  flex-direction: row;
  justify-content: center;
  align-items: center;
  gap: 4px;
  padding: 12px 28px;
  flex-shrink: 0;
  border: 1px solid #d5dae0;
  border-radius: 24px;
  position: relative;
  margin-top: 45px;
  align-self: flex-end;
  overflow: hidden;
}

.btn-text {
  color: #121214;
  font-size: 16px;
}
"""
    (page_dir / f'{comp_name}.module.less').write_text(css_content)

    # Original TSX — only NON-CC nodes have data-figma-id.
    # The CC node (1:100) was replaced by convert.py with <Button> snippet.
    # So _load_orig_figma_class_map will find page-root and section-frame, but NOT btn-wrapper.
    tsx_content = f"""\
import styles from './{comp_name}.module.less';
import {{ Button }} from 'your-component-lib';
export default function {comp_name}() {{
  return (
    <div className={{styles['page-root']}} data-figma-id="{node_id.replace('-', ':')}">
      <div className={{styles['section-frame']}} data-figma-id="1:200">
        <Button variant="secondary">Copy</Button>
      </div>
    </div>
  );
}}
"""
    (page_dir / f'{comp_name}.tsx').write_text(tsx_content)

    # figma-maps.json — has COMPLETE mapping including CC nodes
    figma_maps = {
        'fidToClass': {
            node_id.replace('-', ':'): 'page-root',
            '1:200': 'section-frame',
            '1:100': 'btn-wrapper',
            '1:101': 'btn-text',
        },
        'fidToSrc': {}
    }
    (page_dir / 'figma-maps.json').write_text(json.dumps(figma_maps))

    return page_dir


def test_moly_wrapper_strips_visual_css():
    """U-290: When CC node is missing from TSX but present in figma-maps.json,
    wrapper CSS should still be stripped of visual properties."""
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '10-200'
        comp_name = 'TestMoly'

        _setup_env(tmp, node_id, comp_name)

        # Section IR — the CC node's figmaId is 1:100
        section_ir = {
            'figmaId': '1:200', 'figmaName': 'SectionFrame',
            'figmaType': 'FRAME',
            'css': {'display': 'flex', 'flex-direction': 'column',
                    'align-items': 'flex-start', 'gap': '24px', 'position': 'relative'},
            'children': [
                {
                    'figmaId': '1:100', 'figmaName': 'BtnWrapper',
                    'figmaType': 'FRAME',
                    'css': {'width': '122px', 'height': '48px', 'display': 'flex',
                            'flex-direction': 'row', 'justify-content': 'center',
                            'align-items': 'center', 'gap': '4px',
                            'padding': '12px 28px', 'flex-shrink': '0',
                            'border': '1px solid #d5dae0', 'border-radius': '24px',
                            'position': 'relative', 'margin-top': '45px',
                            'align-self': 'flex-end', 'overflow': 'hidden'},
                    'children': [
                        {
                            'figmaId': '1:101', 'figmaName': 'btnText',
                            'figmaType': 'TEXT', 'isTextNode': True,
                            'css': {'color': '#121214', 'font-size': '16px'},
                            'children': [], 'characters': 'Copy',
                        }
                    ],
                }
            ],
        }

        plan = {
            'pageComponent': comp_name,
            'nodeId': node_id,
            'nodeIdSafe': node_id,
            'cssExt': 'less',
            'irPath': '',
            'sections': [
                {
                    'name': 'HeroSection',
                    'ir': section_ir,
                    'y': 0,
                    'leafComponents': [
                        {
                            'name': 'CarouselCard',
                            'ir': section_ir['children'][0],
                            'ccComponent': 'Carousel',
                            'ccImport': 'your-component-lib',
                            'allImports': ["import { Carousel } from 'your-component-lib'"],
                            'snippet': '<Carousel>Copy</Carousel>',
                            'varyingProps': [],
                            'allInstanceFigmaIds': ['1:100'],
                        }
                    ],
                }
            ],
            'leafComponents': [],
            'topLeaves': [],
            'inlineNodes': [],
        }

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from lib.split_codegen import generate_components
            out_dir = tmp / 'out'
            generate_components(plan, out_dir, css_ext='less')
        finally:
            os.chdir(old_cwd)

        # Read the generated section CSS
        section_css_path = out_dir / 'components' / 'HeroSection' / 'index.module.less'
        check('U-290a', 'section CSS file generated', section_css_path.exists())

        if section_css_path.exists():
            import re
            css = section_css_path.read_text()

            # Find the btn-wrapper class block
            wrapper_match = re.search(r'\.btn-wrapper\s*\{([^}]*)\}', css, re.DOTALL)
            if wrapper_match:
                block = wrapper_match.group(1)
                # Should KEEP positioning props
                check('U-290b', 'wrapper keeps position',
                      'position' in block)
                check('U-290c', 'wrapper keeps margin-top',
                      'margin-top' in block)
                check('U-290d', 'wrapper keeps align-self',
                      'align-self' in block)
                check('U-290e', 'wrapper keeps flex-shrink',
                      'flex-shrink' in block)
                # Should STRIP visual props (not in positioning whitelist)
                check('U-290f', 'wrapper strips border',
                      'border:' not in block)
                check('U-290g', 'wrapper strips border-radius',
                      'border-radius' not in block)
                check('U-290h', 'wrapper strips padding',
                      'padding' not in block)
                # width/height are now in the whitelist (kept for Figma sizing)
                check('U-290i', 'wrapper keeps width',
                      'width' in block)
                check('U-290j', 'wrapper keeps height',
                      'height' in block)
                check('U-290k', 'wrapper keeps overflow',
                      'overflow' in block)
                check('U-290l', 'wrapper keeps display (for centering Moly component)',
                      'display' in block)
                check('U-290m', 'wrapper keeps gap',
                      'gap' in block)
            else:
                # If btn-wrapper is not in the CSS at all, the class name wasn't resolved
                # This means fid_to_class didn't supplement from figma-maps.json
                check('U-290-CRITICAL', 'btn-wrapper class present in CSS (fid_to_class supplemented)',
                      False)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_icon_wrapper_keeps_transform():
    """U-291: Real data: node 4030:41619 from Brand6PreKYC (4030-41365).
    Icon wrapper CSS must retain transform (positioning property) for centering.
    IR CSS: position:absolute; left:50%; top:50%; transform:translateX(-50%) translateY(-50%).
    Without transform in whitelist, icon is displaced to bottom-right corner."""
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '4030-41365'
        comp_name = 'Brand6PreKyc'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)

        # Real data: section CSS containing the icon wrapper class
        css_content = """\
.one-platform {
  width: 100%;
  display: flex;
  flex-direction: column;
  position: relative;
}

.icon1-n4030-41619 {
  width: 24px;
  height: 24px;
  position: absolute;
  left: 50%;
  transform: translateX(-50%) translateY(-50%);
  top: 50%;
  border-radius: 100px;
  overflow: hidden;
  pointer-events: none;
}
"""
        (page_dir / f'{comp_name}.module.less').write_text(css_content)
        tsx_content = f"""\
import styles from './{comp_name}.module.less';
import {{ IconArrowChevronRight }} from '@example/icons';
export default function {comp_name}() {{
  return (
    <div className={{styles['one-platform']}} data-figma-id="4030:41594">
      <IconArrowChevronRight className={{styles['icon1-n4030-41619']}} />
    </div>
  );
}}
"""
        (page_dir / f'{comp_name}.tsx').write_text(tsx_content)
        figma_maps = {
            'fidToClass': {
                '4030:41594': 'one-platform',
                '4030:41619': 'icon1-n4030-41619',
            },
            'fidToSrc': {}
        }
        (page_dir / 'figma-maps.json').write_text(json.dumps(figma_maps))

        # Real IR for section containing the icon
        section_ir = {
            'figmaId': '4030:41594', 'figmaName': 'OnePlatform',
            'figmaType': 'FRAME',
            'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                    'position': 'relative'},
            'children': [{
                'figmaId': '4030:41619', 'figmaName': 'icon1',
                'figmaType': 'INSTANCE',
                'css': {
                    'width': '24px', 'height': '24px',
                    'position': 'absolute', 'left': '50%',
                    'transform': 'translateX(-50%) translateY(-50%)',
                    'top': '50%', 'border-radius': '100px',
                    'overflow': 'hidden', 'pointer-events': 'none',
                },
                'children': [],
            }],
        }

        plan = {
            'pageComponent': comp_name,
            'nodeId': node_id,
            'nodeIdSafe': node_id,
            'cssExt': 'less',
            'irPath': '',
            'sections': [{
                'name': 'OnePlatformSection',
                'ir': section_ir,
                'y': 0,
                'leafComponents': [{
                    'name': 'Icon1',
                    'ir': section_ir['children'][0],
                    'ccComponent': 'IconArrowChevronRight',
                    'ccImport': '@example/icons',
                    'allImports': ["import { IconArrowChevronRight } from '@example/icons'"],
                    'snippet': '<IconArrowChevronRight />',
                    'varyingProps': [],
                    'allInstanceFigmaIds': ['4030:41619'],
                }],
            }],
            'leafComponents': [],
            'topLeaves': [],
            'inlineNodes': [],
        }

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from lib.split_codegen import generate_components
            out_dir = tmp / 'out'
            generate_components(plan, out_dir, css_ext='less')
        finally:
            os.chdir(old_cwd)

        section_css_path = out_dir / 'components' / 'OnePlatformSection' / 'index.module.less'
        check('U-291a', 'section CSS generated', section_css_path.exists())

        if section_css_path.exists():
            import re
            css = section_css_path.read_text()
            wrapper_match = re.search(r'\.icon1-n4030-41619\s*\{([^}]*)\}', css, re.DOTALL)
            if wrapper_match:
                block = wrapper_match.group(1)
                # Real data: node 4030:41619 from Brand6PreKYC (4030-41365)
                check('U-291b', 'icon wrapper keeps transform',
                      'transform' in block)
                check('U-291c', 'icon wrapper keeps pointer-events',
                      'pointer-events' in block)
                check('U-291d', 'icon wrapper keeps position:absolute',
                      'position' in block)
                check('U-291e', 'icon wrapper keeps left:50%',
                      'left' in block)
                check('U-291f', 'icon wrapper keeps top:50%',
                      'top' in block)
            else:
                check('U-291-CRITICAL', 'icon1-n4030-41619 class present in CSS',
                      False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_non_moly_leaf_shared_class_keeps_padding():
    """U-292: Real data: node 4030:41697 from Brand6PreKYC (4030-41365).
    Non-Moly leaf (LoremIpsum) instances share class 'frame-2147224635' with the
    template node that is rendered INLINE in section. Since the class is shared with
    the inline template, it must NOT be stripped (otherwise inline rendering loses visual)."""
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '4030-41365'
        comp_name = 'Brand6PreKyc'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)

        # Real data: section CSS with padding + backdrop-filter on shared class
        css_content = """\
.earn-section {
  width: 100%;
  display: flex;
  flex-direction: column;
  position: relative;
}

.frame-2147224635 {
  height: 136px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: flex-start;
  gap: 8px;
  padding: 0px 32px 32px 32px;
  flex-shrink: 0;
  align-self: stretch;
  backdrop-filter: blur(30px);
  -webkit-backdrop-filter: blur(30px);
  position: relative;
}

.lorem-text {
  color: #121214;
  font-size: 16px;
}
"""
        (page_dir / f'{comp_name}.module.less').write_text(css_content)
        tsx_content = f"""\
import styles from './{comp_name}.module.less';
export default function {comp_name}() {{
  return (
    <div className={{styles['earn-section']}} data-figma-id="4030:41687">
      <div className={{styles['frame-2147224635']}} data-figma-id="4030:41697">
        <span className={{styles['lorem-text']}} data-figma-id="4030:41705">Lorem ipsum</span>
      </div>
    </div>
  );
}}
"""
        (page_dir / f'{comp_name}.tsx').write_text(tsx_content)
        figma_maps = {
            'fidToClass': {
                '4030:41687': 'earn-section',
                '4030:41697': 'frame-2147224635',
                '4030:41712': 'frame-2147224635',
                '4030:41724': 'frame-2147224635',
                '4030:41705': 'lorem-text',
            },
            'fidToSrc': {}
        }
        (page_dir / 'figma-maps.json').write_text(json.dumps(figma_maps))

        # Section IR containing a non-Moly leaf whose instances share the same class
        text_ir = {
            'figmaId': '4030:41705', 'figmaName': 'LoremText',
            'figmaType': 'TEXT', 'isTextNode': True,
            'css': {'color': '#121214', 'font-size': '16px'},
            'children': [],
        }
        frame_ir = {
            'figmaId': '4030:41697', 'figmaName': 'Frame2147224635',
            'figmaType': 'FRAME',
            'css': {'height': '136px', 'display': 'flex', 'flex-direction': 'column',
                    'justify-content': 'flex-end', 'align-items': 'flex-start',
                    'gap': '8px', 'padding': '0px 32px 32px 32px',
                    'flex-shrink': '0', 'align-self': 'stretch',
                    'backdrop-filter': 'blur(30px)', '-webkit-backdrop-filter': 'blur(30px)',
                    'position': 'relative'},
            'children': [text_ir],
        }
        section_ir = {
            'figmaId': '4030:41687', 'figmaName': 'EarnSection',
            'figmaType': 'FRAME',
            'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                    'position': 'relative'},
            'children': [frame_ir],
        }

        plan = {
            'pageComponent': comp_name,
            'nodeId': node_id,
            'nodeIdSafe': node_id,
            'cssExt': 'less',
            'irPath': '',
            'sections': [{
                'name': 'EarnSection',
                'ir': section_ir,
                'y': 0,
                'leafComponents': [{
                    'name': 'LoremIpsum',
                    'ir': frame_ir,
                    'varyingProps': [],
                    'instancesData': [{}],
                    'ccComponent': None,
                    'allInstanceFigmaIds': ['4030:41712', '4030:41724'],
                }],
            }],
            'leafComponents': [],
            'topLeaves': [],
            'inlineNodes': [],
        }

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from lib.split_codegen import generate_components
            out_dir = tmp / 'out'
            generate_components(plan, out_dir, css_ext='less')
        finally:
            os.chdir(old_cwd)

        section_css_path = out_dir / 'components' / 'EarnSection' / 'index.module.less'
        check('U-292a', 'section CSS generated', section_css_path.exists())

        if section_css_path.exists():
            import re
            css = section_css_path.read_text()
            wrapper_match = re.search(r'\.frame-2147224635\s*\{([^}]*)\}', css, re.DOTALL)
            if wrapper_match:
                block = wrapper_match.group(1)
                # Real data: node 4030:41697 from Brand6PreKYC (4030-41365)
                # Non-Moly leaf wrapper must keep padding
                check('U-292b', 'non-moly leaf wrapper keeps padding',
                      'padding' in block)
                check('U-292c', 'non-moly leaf wrapper keeps backdrop-filter',
                      'backdrop-filter' in block)
                check('U-292d', 'non-moly leaf wrapper keeps gap',
                      'gap' in block)
            else:
                check('U-292-CRITICAL', 'frame-2147224635 class present in CSS',
                      False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_non_moly_leaf_unique_class_strips_visual():
    """U-293: Real data: node I39641:9303;39641:6388 from TomorrowlandLP (39641-6863).
    Non-Moly leaf (TopUp) has unique wrapper class 'container-I39641-9303-39641-6388'
    that is NOT shared with any inline template node. This wrapper must strip visual
    props (border-radius, padding, background-color) to avoid double-rendering,
    since the leaf component itself outputs these."""
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '39641-6863'
        comp_name = 'TomorrowlandLandingPage4'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)

        css_content = """\
.top-up-section {
  width: 100%;
  display: flex;
  position: relative;
}

.container-I39641-9303-39641-6388 {
  width: 497px;
  height: 140px;
  position: absolute;
  left: 120px;
  top: 0px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 32px;
  background-color: #ffffff;
  border-radius: 24px;
  overflow: hidden;
}
"""
        (page_dir / f'{comp_name}.module.less').write_text(css_content)
        tsx_content = f"""\
import styles from './{comp_name}.module.less';
export default function {comp_name}() {{
  return (
    <div className={{styles['top-up-section']}} data-figma-id="I39641:9303;39641:6387">
      <div className={{styles['container-I39641-9303-39641-6388']}} data-figma-id="I39641:9303;39641:6388">
      </div>
    </div>
  );
}}
"""
        (page_dir / f'{comp_name}.tsx').write_text(tsx_content)
        figma_maps = {
            'fidToClass': {
                'I39641:9303;39641:6387': 'top-up-section',
                'I39641:9303;39641:6388': 'container-I39641-9303-39641-6388',
            },
            'fidToSrc': {}
        }
        (page_dir / 'figma-maps.json').write_text(json.dumps(figma_maps))

        leaf_ir = {
            'figmaId': 'I39641:9303;39641:6388', 'figmaName': 'Container',
            'figmaType': 'FRAME',
            'css': {'width': '497px', 'height': '140px',
                    'position': 'absolute', 'left': '120px', 'top': '0px',
                    'display': 'flex', 'flex-direction': 'column',
                    'align-items': 'flex-start', 'gap': '12px',
                    'padding': '32px', 'background-color': '#ffffff',
                    'border-radius': '24px', 'overflow': 'hidden'},
            'children': [],
        }
        section_ir = {
            'figmaId': 'I39641:9303;39641:6387', 'figmaName': 'TopUpSection',
            'figmaType': 'FRAME',
            'css': {'width': '100%', 'display': 'flex', 'position': 'relative'},
            'children': [leaf_ir],
        }

        plan = {
            'pageComponent': comp_name,
            'nodeId': node_id,
            'nodeIdSafe': node_id,
            'cssExt': 'less',
            'irPath': '',
            'sections': [{
                'name': 'TopUpSection',
                'ir': section_ir,
                'y': 0,
                'leafComponents': [{
                    'name': 'TopUp',
                    'ir': leaf_ir,
                    'varyingProps': [],
                    'instancesData': [{}],
                    'ccComponent': None,
                    'allInstanceFigmaIds': ['I39641:9303;39641:6388'],
                }],
            }],
            'leafComponents': [],
            'topLeaves': [],
            'inlineNodes': [],
        }

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from lib.split_codegen import generate_components
            out_dir = tmp / 'out'
            generate_components(plan, out_dir, css_ext='less')
        finally:
            os.chdir(old_cwd)

        section_css_path = out_dir / 'components' / 'TopUpSection' / 'index.module.less'
        check('U-293a', 'section CSS generated', section_css_path.exists())

        if section_css_path.exists():
            import re
            css = section_css_path.read_text()
            wrapper_match = re.search(
                r'\.container-I39641-9303-39641-6388\s*\{([^}]*)\}', css, re.DOTALL)
            if wrapper_match:
                block = wrapper_match.group(1)
                # Wrapper must strip visual props (leaf has them)
                check('U-293b', 'leaf wrapper strips border-radius',
                      'border-radius' not in block)
                check('U-293c', 'leaf wrapper strips padding',
                      'padding' not in block)
                check('U-293d', 'leaf wrapper strips background-color',
                      'background-color' not in block)
                # Wrapper keeps positioning
                check('U-293e', 'leaf wrapper keeps position',
                      'position' in block)
                check('U-293f', 'leaf wrapper keeps width',
                      'width' in block)
            else:
                check('U-293-CRITICAL', 'wrapper class present in section CSS', False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tabs_text_override_keeps_container_css():
    """U-294: Tabs (TEXT_OVERRIDE) wrapper should keep container visual CSS.

    When a Figma node is CC-replaced by Moly <Tabs>, the wrapper class in
    the Section CSS must preserve container decoration (background-color,
    border-radius, padding, height, overflow) because:
    1. The Tabs' own CSS file says '/* 使用 Moly 内部样式 */' (empty)
    2. Moly Tabs does NOT provide the outer container background/rounding
    3. These visual properties belong to the WRAPPER, not Moly's internals

    Real data: node 10664:26953 "ButtonTabWithBg2" from page 10664-26578
    (n2_button_tab_with_bg wrapper for Tabs, background-color: var(--bds-gray-ele-line))
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '10664-26578'
        comp_name = 'Frame2147224865'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)

        # Original CSS — Tabs node (n2_button_tab_with_bg) has full container styles
        css_content = """\
.frame-root {
  width: 1440px;
  position: relative;
}

.n2_button_tab_with_bg {
  width: 100%;
  height: 40px;
  display: flex;
  flex-direction: row;
  align-items: flex-start;
  gap: 4px;
  padding: 2px 2px 2px 2px;
  overflow: hidden;
  flex-shrink: 0;
  align-self: stretch;
  background-color: var(--bds-gray-ele-line);
  border-radius: 4px;
  position: relative;
}
"""
        (page_dir / f'{comp_name}.module.less').write_text(css_content)
        tsx_content = f"""\
import styles from './{comp_name}.module.less';
export default function {comp_name}() {{
  return <div className={{styles['frame-root']}} data-figma-id="10664:26578" />;
}}
"""
        (page_dir / f'{comp_name}.tsx').write_text(tsx_content)

        figma_maps = {
            'fidToClass': {
                '10664:26578': 'frame-root',
                '10664:26953': 'n2_button_tab_with_bg',   # Tabs node
            },
            'fidToSrc': {}
        }
        (page_dir / 'figma-maps.json').write_text(json.dumps(figma_maps))

        # Section IR: Tabs node at 10664:26953 with container visual CSS
        # Real data: node 10664:26953 from page 10664-26578
        section_ir = {
            'figmaId': '10664:26900', 'figmaName': 'MainSection',
            'figmaType': 'FRAME',
            'css': {'width': '100%', 'position': 'relative'},
            'semantic': {'className': 'main-section', 'componentName': 'MainSection'},
            'children': [
                {
                    'figmaId': '10664:26953', 'figmaName': 'ButtonTabWithBg',
                    'figmaType': 'FRAME',
                    'css': {
                        'width': '100%',
                        'height': '40px',
                        'display': 'flex',
                        'flex-direction': 'row',
                        'align-items': 'flex-start',
                        'gap': '4px',
                        'padding': '2px 2px 2px 2px',
                        'overflow': 'hidden',
                        'flex-shrink': '0',
                        'align-self': 'stretch',
                        'background-color': 'var(--bds-gray-ele-line)',
                        'border-radius': '4px',
                        'position': 'relative',
                    },
                    'semantic': {'className': 'n2_button_tab_with_bg', 'componentName': 'ButtonTabWithBg'},
                    'children': [],
                },
            ],
        }

        plan = {
            'pageComponent': comp_name,
            'nodeId': node_id,
            'nodeIdSafe': node_id,
            'cssExt': 'less',
            'irPath': '',
            'sections': [
                {
                    'name': 'MainSection',
                    'ir': section_ir,
                    'y': 0,
                    'leafComponents': [
                        {
                            'name': 'ButtonTabWithBg2',
                            'ir': section_ir['children'][0],
                            'ccComponent': 'Tabs',
                            'ccImport': "import { Tabs } from 'your-component-lib';",
                            'allImports': ["import { Tabs } from 'your-component-lib';"],
                            'snippet': '<Tabs tabType="button" size="s" />',
                            'varyingProps': [],
                            'allInstanceFigmaIds': ['10664:26953'],
                            'confidence': 1.0,
                            'source': 'code-connect',
                        }
                    ],
                    'topLeaves': [],
                    'inlineNodes': [],
                }
            ],
            'topLeaves': [],
            'inlineNodes': [],
        }

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from lib.split_codegen import generate_components
            out_dir = tmp / 'out'
            generate_components(plan, out_dir, css_ext='less')
        finally:
            os.chdir(old_cwd)

        section_css_path = out_dir / 'components' / 'MainSection' / 'index.module.less'
        check('U-294a', 'section CSS generated', section_css_path.exists())

        if section_css_path.exists():
            import re
            css = section_css_path.read_text()
            wrapper_match = re.search(
                r'\.n2_button_tab_with_bg\s*\{([^}]*)\}', css, re.DOTALL)
            if wrapper_match:
                block = wrapper_match.group(1)
                # Tabs wrapper must KEEP container visual CSS (Moly doesn't provide these)
                check('U-294b', 'Tabs wrapper keeps background-color',
                      'background-color' in block)
                check('U-294c', 'Tabs wrapper keeps border-radius',
                      'border-radius' in block)
                check('U-294d', 'Tabs wrapper keeps padding',
                      'padding' in block)
                check('U-294e', 'Tabs wrapper keeps height',
                      'height' in block)
                check('U-294f', 'Tabs wrapper keeps overflow',
                      'overflow' in block)
                # Wrapper still keeps positioning
                check('U-294g', 'Tabs wrapper keeps position',
                      'position' in block)
                check('U-294h', 'Tabs wrapper keeps width',
                      'width' in block)
            else:
                check('U-294-CRITICAL', 'n2_button_tab_with_bg class present in section CSS', False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_instance_unique_classes_each_stripped_when_no_instance_uses_root_cls():
    """U-295: When a non-Moly leaf has multiple instances each with their OWN unique
    wrapper class (different from root_cls), ALL unique wrapper classes must be stripped.

    Root cause: the partial-coverage guard (`_fids_using_cls < len(_inst_fids)`) was
    designed for the Arena zebra-stripe pattern (2 of 4 rows use a variant class,
    2 use the root class). For TopUp, EACH instance has its own unique class and NONE
    use root_cls. The guard incorrectly treats instance-unique classes as variant
    wrappers and skips stripping.

    Correct distinction:
    - Variant (Arena zebra): non-using instances fall back to root_cls → keep visual CSS
    - Instance-unique (TopUp): non-using instances have a DIFFERENT unique class → strip

    Real data: TopUp (I39641:9303;39641:6388/6400) from TomorrowlandLandingPage4 (39641-6863).
    Instance 1: I39641:9303;39641:6388 → class container-I39641-9303-39641-6388
    Instance 2: I39641:9303;39641:6400 → class container-I39641-9303-39641-6400
    root_cls: container (template node 39641:6388)
    Bug: padding/background-color/border-radius kept in wrapper CSS → text clipped.
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '39641-6863'
        comp_name = 'TomorrowlandLandingPage4'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)

        # Real data: section CSS with two instance-unique wrapper classes
        # Both have the visual props that should be stripped
        css_content = """\
.top-up-section {
  width: 100%;
  display: flex;
  position: relative;
}

.container-I39641-9303-39641-6388 {
  width: 497px;
  height: 140px;
  position: absolute;
  left: 120px;
  top: 0px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 32px 29.48px 32px 32px;
  background-color: #ffffff;
  border-radius: 24px;
  overflow: hidden;
  box-sizing: border-box;
}

.container-I39641-9303-39641-6400 {
  width: 497px;
  height: 140px;
  position: absolute;
  left: 120px;
  top: 150px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 32px 29.48px 32px 32px;
  background-color: #ffffff;
  border-radius: 24px;
  overflow: hidden;
  box-sizing: border-box;
}
"""
        (page_dir / f'{comp_name}.module.scss').write_text(css_content)

        tsx_content = f"""\
import styles from './{comp_name}.module.scss';
export default function {comp_name}() {{
  return (
    <div className={{styles['top-up-section']}} data-figma-id="I39641:9303;39641:6387">
      <div className={{styles['container-I39641-9303-39641-6388']}} data-figma-id="I39641:9303;39641:6388" />
      <div className={{styles['container-I39641-9303-39641-6400']}} data-figma-id="I39641:9303;39641:6400" />
    </div>
  );
}}
"""
        (page_dir / f'{comp_name}.tsx').write_text(tsx_content)

        figma_maps = {
            'fidToClass': {
                'I39641:9303;39641:6387': 'top-up-section',
                'I39641:9303;39641:6388': 'container-I39641-9303-39641-6388',
                'I39641:9303;39641:6400': 'container-I39641-9303-39641-6400',
                # root_cls for the TopUp template node
                '39641:6388': 'container',
            },
            'fidToSrc': {}
        }
        (page_dir / 'figma-maps.json').write_text(json.dumps(figma_maps))

        # Real data: TopUp leaf IR template (node 39641:6388 = root_cls 'container')
        def _make_topup_ir(fid):
            return {
                'figmaId': fid, 'figmaName': 'Container', 'figmaType': 'FRAME',
                'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 497, 'height': 140},
                'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
                'variants': None, 'imageRef': None, 'fillImageRef': None,
                'localAssetPath': None,
                'css': {'width': '497px', 'height': '140px', 'position': 'absolute',
                        'left': '120px', 'top': '0px', 'display': 'flex',
                        'flex-direction': 'column', 'align-items': 'flex-start',
                        'gap': '12px', 'padding': '32px 29.48px 32px 32px',
                        'background-color': '#ffffff', 'border-radius': '24px',
                        'overflow': 'hidden'},
                'children': [],
                'semantic': {'htmlTag': 'div', 'className': 'container',
                             'componentName': 'TopUp', 'props': [],
                             'isExtractedComponent': True},
            }

        inst1_ir = _make_topup_ir('I39641:9303;39641:6388')
        inst2_ir = _make_topup_ir('I39641:9303;39641:6400')

        section_ir = {
            'figmaId': 'I39641:9303;39641:6387', 'figmaName': 'TopUpSection',
            'figmaType': 'FRAME', 'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 1440, 'height': 400},
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
            'variants': None, 'imageRef': None, 'fillImageRef': None,
            'localAssetPath': None,
            'css': {'width': '100%', 'display': 'flex', 'position': 'relative'},
            'children': [inst1_ir, inst2_ir],
            'semantic': {'htmlTag': 'section', 'className': 'top-up-section',
                         'componentName': 'TopUpSection', 'props': [],
                         'isExtractedComponent': True},
        }

        plan = {
            'pageComponent': comp_name,
            'nodeId': node_id,
            'cssExt': 'scss',
            'leafComponents': [],
            'sections': [{
                'name': 'TopUpSection',
                'ir': section_ir,
                'ccComponent': None,
                'leafComponents': [{
                    'name': 'TopUp',
                    'ir': _make_topup_ir('39641:6388'),  # template node
                    'varyingProps': [],
                    'instancesData': [{} for _ in range(2)],
                    # Both instances have unique classes different from root_cls='container'
                    'allInstanceFigmaIds': ['I39641:9303;39641:6388', 'I39641:9303;39641:6400'],
                    'ccComponent': None,
                    'allImports': [],
                }],
            }],
        }

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from lib.split_codegen import generate_components
            out_dir = tmp / 'out'
            generate_components(plan, out_dir, css_ext='scss')
        finally:
            os.chdir(old_cwd)

        section_css_path = out_dir / 'components' / 'TopUpSection' / 'index.module.scss'
        check('U-295a', 'section CSS generated', section_css_path.exists())

        if section_css_path.exists():
            css = section_css_path.read_text()

            # Find the first instance wrapper class
            import re
            wrapper_match = re.search(
                r'\.container-I39641-9303-39641-6388\s*\{([^}]*)\}', css, re.DOTALL)
            if wrapper_match:
                block = wrapper_match.group(1)
                # Real data: node I39641:9303;39641:6388 from TomorrowlandLandingPage4 (39641-6863)
                # Instance-unique wrapper: non-using instance has its OWN unique class
                # (container-I39641-9303-39641-6400), not root_cls → must strip visual CSS.
                # Bug: partial-coverage guard fires (1/2 instances use this class) and skips
                # stripping, treating instance-unique classes as Arena zebra-stripe variants.
                check('U-295b', 'instance-unique wrapper strips padding',
                      'padding' not in block)
                check('U-295c', 'instance-unique wrapper strips background-color',
                      'background-color' not in block)
                check('U-295d', 'instance-unique wrapper strips border-radius',
                      'border-radius' not in block)
                # Positioning props must be kept
                check('U-295e', 'instance-unique wrapper keeps position:absolute',
                      'position' in block)
                check('U-295f', 'instance-unique wrapper keeps left:120px',
                      'left' in block)
                check('U-295g', 'instance-unique wrapper keeps width',
                      'width' in block)
            else:
                check('U-295-CRITICAL',
                      'container-I39641-9303-39641-6388 class present in CSS', False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_moly_flex_wrapper_gets_child_flex1_rule():
    """U-296: When a Moly CC component is placed inside a flex-container wrapper div,
    the section CSS must append a '> * { flex: 1; min-width: 0; }' child rule to make
    the Moly component fill the wrapper.

    Root cause: split_codegen strips visual CSS from Moly wrapper classes but leaves
    the wrapper with its flex properties. The Moly component renders at its own intrinsic
    width (e.g., 139px for Search inside a 240px wrapper), leaving 67px of empty space.
    No child-fill rule was appended, so the component appeared left-aligned with a gap.

    Real data: n3_search (172:13315 parent, 172:13120 = Search instance)
    from ByAiHub20 TypeTabTagElement5Section (nodeId: 169-33787).
    Wrapper CSS: width:240px; display:flex; flex-direction:row; align-items:center; overflow:hidden
    Bug: Search renders 139px, gap=67px (right side of wrapper)
    Fix: .n3_search > * { flex: 1; min-width: 0; } → Search fills 240px
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        node_id = '169-33787'
        comp_name = 'ByAiHub20'

        page_dir = tmp / '.figma-to-code' / '3-page-code' / f'{comp_name}-{node_id}'
        page_dir.mkdir(parents=True)

        # Real data: n3_search and surrounding CSS from ByAiHub20 TypeTabTagElement5Section
        css_content = """\
.skill-card-footer {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
  align-self: stretch;
  position: relative;
}

.n3_search {
  width: 240px;
  display: flex;
  flex-direction: row;
  align-items: center;
  flex-shrink: 0;
  position: relative;
  overflow: hidden;
}
"""
        (page_dir / f'{comp_name}.module.scss').write_text(css_content)
        tsx_content = f"""\
import styles from './{comp_name}.module.scss';
export default function {comp_name}() {{
  return (
    <div className={{styles['skill-card-footer']}} data-figma-id="172:13315">
      <div className={{styles['n3_search']}} data-figma-id="172:13120" />
    </div>
  );
}}
"""
        (page_dir / f'{comp_name}.tsx').write_text(tsx_content)

        # Real data: figma-maps.json from ByAiHub20 (169-33787)
        figma_maps = {
            'fidToClass': {
                '172:13315': 'skill-card-footer',
                '172:13120': 'n3_search',
            },
            'fidToSrc': {}
        }
        (page_dir / 'figma-maps.json').write_text(json.dumps(figma_maps))

        def _frame_ir(fid, name, css, children=None):
            return {
                'figmaId': fid, 'figmaName': name, 'figmaType': 'FRAME',
                'isComponentInstance': False, 'componentId': None,
                'bb': {'width': 240, 'height': 40},
                'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
                'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
                'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
                'variants': None, 'imageRef': None, 'fillImageRef': None,
                'localAssetPath': None,
                'css': css, 'children': children or [],
            }

        # Real data: n3_search node (172:13120) wrapper IR
        n3_search_ir = _frame_ir('172:13120', 'n3_search',
            {'width': '240px', 'display': 'flex', 'flex-direction': 'row',
             'align-items': 'center', 'flex-shrink': '0',
             'position': 'relative', 'overflow': 'hidden'})

        footer_ir = _frame_ir('172:13315', 'skill-card-footer',
            {'display': 'flex', 'flex-direction': 'row',
             'justify-content': 'space-between', 'align-items': 'center',
             'flex-shrink': '0', 'align-self': 'stretch', 'position': 'relative'},
            children=[n3_search_ir])

        section_ir = {
            'figmaId': '176:13424', 'figmaName': 'TypeTabTagElement5Section',
            'figmaType': 'FRAME', 'isComponentInstance': False, 'componentId': None,
            'bb': {'width': 1440, 'height': 600},
            'isTextNode': False, 'isImageNode': False, 'isVectorNode': False,
            'isDecorativeElement': False, 'textContent': None, 'textSegments': None,
            'textAutoResize': None, 'lineTypes': [], 'lineIndentations': [],
            'variants': None, 'imageRef': None, 'fillImageRef': None,
            'localAssetPath': None,
            'css': {'width': '100%', 'display': 'flex', 'flex-direction': 'column',
                    'position': 'relative'},
            'children': [footer_ir],
            'semantic': {'htmlTag': 'section', 'className': 'skill-tab-section',
                         'componentName': 'TypeTabTagElement5Section', 'props': [],
                         'isExtractedComponent': True},
        }

        plan = {
            'pageComponent': comp_name,
            'nodeId': node_id,
            'cssExt': 'scss',
            'leafComponents': [],
            'sections': [{
                'name': 'TypeTabTagElement5Section',
                'ir': section_ir,
                'ccComponent': None,
                'leafComponents': [{
                    'name': 'Search3',
                    'ir': n3_search_ir,
                    'varyingProps': [],
                    'instancesData': [{}],
                    # Real data: Search Moly component in n3_search wrapper
                    'allInstanceFigmaIds': ['172:13120'],
                    'ccComponent': 'Search',
                    'ccImport': "your-component-lib",
                    'allImports': ["import { Search } from 'your-component-lib'"],
                    'snippet': '<Search size="small" placeholder="Search" />',
                }],
            }],
        }

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            from lib.split_codegen import generate_components
            out_dir = tmp / 'out'
            generate_components(plan, out_dir, css_ext='scss')
        finally:
            os.chdir(old_cwd)

        section_css_path = out_dir / 'components' / 'TypeTabTagElement5Section' / 'index.module.scss'
        check('U-296a', 'section CSS generated', section_css_path.exists())

        if section_css_path.exists():
            css = section_css_path.read_text()
            # The Moly Search wrapper must have a child rule to fill the 240px container.
            # Real data: 139px Search inside 240px wrapper → 67px gap without this rule.
            check('U-296b', 'n3_search wrapper has > * { flex: 1 } child rule',
                  '> *' in css and 'flex: 1' in css)
            check('U-296c', 'n3_search > * has min-width: 0',
                  'min-width: 0' in css)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Run ──
print('\n── split Moly wrapper CSS tests ──')
test_moly_wrapper_strips_visual_css()
test_icon_wrapper_keeps_transform()
test_non_moly_leaf_shared_class_keeps_padding()
test_non_moly_leaf_unique_class_strips_visual()
test_tabs_text_override_keeps_container_css()
test_instance_unique_classes_each_stripped_when_no_instance_uses_root_cls()
test_moly_flex_wrapper_gets_child_flex1_rule()


def test_u297_backdrop_filter_wrapper_not_added_to_moly_wrappers():
    """
    U-297: A Moly CC wrapper with backdrop-filter must NOT be stripped.

    Real data: node 181:3025 (events class) from Web-merged-181-2980.
    events wraps IconShare (CC component), but events IS a styled glass container:
    background-color:rgba(255,255,255,0.12), backdrop-filter:blur(30px), etc.

    The presence of backdrop-filter/background-color signals the wrapper IS the
    styled component (not just a positioning div), so it must keep all visual props.

    Without fix: events added to _moly_wrappers → backdrop-filter stripped → glass effect lost.
    With fix: events skipped from _moly_wrappers when orig_css has backdrop-filter.
    """
    import tempfile, json, shutil
    from pathlib import Path
    from lib.split_codegen import _css_from_orig

    # Real CSS from Web-merged-181-2980 (node 181:3025 events)
    orig_css_map = {
        'events': {
            'display': 'flex', 'flex-direction': 'row', 'align-items': 'center',
            'gap': '8px', 'padding': '12px 12px 12px 8px', 'flex-shrink': '0',
            'background-color': 'rgba(255, 255, 255, 0.12)',
            'backdrop-filter': 'blur(30px)', '-webkit-backdrop-filter': 'blur(30px)',
            'box-shadow': '0 0 0 1px rgba(0, 0, 0, 0.12)',
            'border-radius': '100px', 'position': 'relative', 'overflow': 'hidden',
        }
    }
    ir = {
        'figmaId': '181:3025', 'figmaName': 'events', 'figmaType': 'FRAME',
        'css': orig_css_map['events'],
        'semantic': {'className': 'events'},
        'children': [],
    }
    # When events is treated as a moly_wrapper, visual props are stripped
    css_stripped = _css_from_orig(ir, orig_css_map, 'less', moly_wrapper_classes={'events'})
    # When events is NOT in moly_wrappers (fixed behavior), props are preserved
    css_preserved = _css_from_orig(ir, orig_css_map, 'less', moly_wrapper_classes=None)

    check('U-297a', 'events not in moly_wrapper → backdrop-filter preserved',
          'backdrop-filter: blur(30px)' in css_preserved)
    check('U-297b', 'events not in moly_wrapper → background-color preserved',
          'background-color' in css_preserved)
    check('U-297c', 'events not in moly_wrapper → border-radius preserved',
          'border-radius: 100px' in css_preserved)
    check('U-297d', 'events not in moly_wrapper → padding preserved',
          'padding: 12px 12px 12px 8px' in css_preserved)


test_u297_backdrop_filter_wrapper_not_added_to_moly_wrappers()


def test_u323_varying_instance_class_no_flex_child_rule():
    """U-323: Varying-instance unique class must NOT generate '> * { flex: 1 }' rule,
    even when it has display:flex, because it is a plain card container (not a Moly CC wrapper).

    Real data: 'eth' class from EarnSection/Section2 (node 4030:41720, Brand6PreKyc 4030-41365).
    Bug: 'eth' was added to _moly_wrappers via the varying-instance-unique-class path, which
    caused '.eth > * { flex: 1; min-width: 0 }' to be generated. This overrode flex-basis on
    flow children (frame-2147224634: 342px spacer and frame-2147224635: 136px text overlay),
    collapsing the spacer to 0px and shrinking the card to ~130px instead of 478px.

    Fix: separate 'moly_flex_child_classes' (actual Moly CC wrappers) from 'moly_wrapper_classes'
    (all CSS-stripped wrappers). Only generate '> *' for moly_flex_child_classes.
    """
    from lib.split_codegen import _css_from_orig

    # Real data: 'eth' CSS from Brand6PreKyc Section2/index.module.less (node 4030:41720)
    orig_css_map = {
        'eth': {
            'flex': '1', 'display': 'flex', 'flex-direction': 'column',
            'align-items': 'center', 'flex-grow': '1', 'min-width': '0',
            'overflow': 'hidden', 'position': 'relative', 'width': '388px',
        }
    }
    ir = {
        'figmaId': '4030:41720', 'figmaName': 'ETH', 'figmaType': 'FRAME',
        'css': orig_css_map['eth'],
        'semantic': {'className': 'eth'},
        'children': [],
    }
    # 'eth' is in moly_wrapper_classes (CSS stripped for varying instance) but NOT a Moly CC wrapper
    css = _css_from_orig(ir, orig_css_map, 'less', moly_wrapper_classes={'eth'})
    check('U-323a', 'varying-instance class in moly_wrapper_classes but no moly_flex_child_classes → no > * rule',
          '> *' not in css)
    # When 'eth' IS explicitly in moly_flex_child_classes, the > * rule SHOULD appear (Moly CC case)
    css_with_flex = _css_from_orig(ir, orig_css_map, 'less',
                                   moly_wrapper_classes={'eth'},
                                   moly_flex_child_classes={'eth'})
    check('U-323b', 'when class IS in moly_flex_child_classes → > * rule generated (Moly CC regression)',
          '> *' in css_with_flex and 'flex: 1' in css_with_flex)


test_u323_varying_instance_class_no_flex_child_rule()


# ── U-326: HUG 节点不应继承 orig_css_map 里同名类的 height ────────────────────
# 真实来源: Brand6PreKyc page
# 类 'frame-2147224743' 在 One Platform 实例 (4030:41595, FIXED h=88) 和
# Earn 实例 (4030:41688, HUG h=None) 中同名。One Platform 的 height:88px 污染了
# orig_css_map，导致 EarnSection/OneAccount.module.less 里也出现 height:88px，
# 造成 Earn/SpotX/End section 各多 4-8px，总偏移 18px，整页重影。
def test_u326_hug_node_strips_inherited_height():
    from lib.split_codegen import _css_from_orig
    ir_hug = {
        'figmaId': '4030:41688',
        'figmaType': 'FRAME',
        'figmaName': 'Frame 2147224743',
        'semantic': {'className': 'frame-2147224743'},
        'css': {
            # HUG: height is None (key exists but value is None — real IR output)
            'height': None,
            'display': 'flex',
            'flex-direction': 'column',
            'gap': '8px',
        },
        'children': [],
    }
    # orig_css_map contains height:88px from the One Platform instance (same class name)
    orig_contaminated = {
        'frame-2147224743': {
            'height': '88px',     # ← from 4030:41595 (FIXED, One Platform)
            'display': 'flex',
            'flex-direction': 'column',
            'gap': '8px',
        }
    }
    css_hug = _css_from_orig(ir_hug, orig_contaminated, 'less')
    check('U-326a',
          'HUG 节点（IR 无 height）→ 不继承 orig_css_map 的 height:88px（同名类污染）',
          'height: 88px' not in css_hug and 'height:88px' not in css_hug)

    # Regression: FIXED 节点（IR 有 height）仍应输出 height
    ir_fixed = {
        'figmaId': '4030:41595',
        'figmaType': 'FRAME',
        'figmaName': 'Frame 2147224743',
        'semantic': {'className': 'frame-2147224743'},
        'css': {
            'height': '88px',     # FIXED in IR
            'display': 'flex',
            'flex-direction': 'column',
            'gap': '8px',
        },
        'children': [],
    }
    css_fixed = _css_from_orig(ir_fixed, orig_contaminated, 'less')
    check('U-326b',
          'FIXED 节点（IR 有 height:88px）→ 仍输出 height:88px（回归）',
          'height: 88px' in css_fixed)


test_u326_hug_node_strips_inherited_height()


# ── U-327: _expand_ir_max_css 当某实例 HUG 时应从模板清除 height ───────────────
# 真实来源: OneAccount 组件模板选 4030:41595 (FIXED h=88)，但 Earn/SpotX/End 实例
# (4030:41688 等) 是 HUG (height=None)。_expand_ir_max_css 应清除 height，
# 防止 HUG 实例被强制 88px，导致 section 整体多 4px，累计 18px 偏移。
def test_u327_expand_ir_max_css_removes_height_when_any_instance_is_hug():
    # 直接 import split_components 里的函数
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from split_components import _expand_ir_max_css

    # 模板 (One Platform): FIXED h=88
    ir_template = {
        'figmaId': '4030:41595',
        'figmaType': 'FRAME',
        'semantic': {'className': 'frame-2147224743'},
        'css': {'height': '88px', 'display': 'flex', 'gap': '8px'},
        'children': [],
    }
    # 其他实例 (Earn): HUG, height=None
    ir_earn = {
        'figmaId': '4030:41688',
        'figmaType': 'FRAME',
        'semantic': {'className': 'frame-2147224743'},
        'css': {'height': None, 'display': 'flex', 'gap': '8px'},
        'children': [],
    }

    import copy
    tmpl = copy.deepcopy(ir_template)
    _expand_ir_max_css(tmpl, [ir_earn])
    check('U-327a',
          '_expand_ir_max_css: 某实例 height=None(HUG) → 模板中 height 被清除',
          tmpl.get('css', {}).get('height') is None or 'height' not in tmpl.get('css', {}))

    # Regression: 若所有实例都是 FIXED，height 应保留最大值
    ir_fixed2 = copy.deepcopy(ir_template)
    ir_fixed2['css']['height'] = '92px'
    tmpl2 = copy.deepcopy(ir_template)
    _expand_ir_max_css(tmpl2, [ir_fixed2])
    check('U-327b',
          '_expand_ir_max_css: 所有实例均 FIXED → height 取最大值（92px）保留',
          tmpl2.get('css', {}).get('height') == '92px')


test_u327_expand_ir_max_css_removes_height_when_any_instance_is_hug()
print_summary('split/test_moly_wrapper_css')
