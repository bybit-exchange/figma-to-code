import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.tsx_generator import collect_text_tokens


def _text_node(fid, text, cls='menu', prop_name=None):
    node = {
        'figmaId': fid, 'isTextNode': True, 'textContent': text,
        'lineTypes': [], 'textSegments': None,
        'semantic': {'className': cls, 'htmlTag': 'span', 'props': []},
        'children': [],
    }
    if prop_name:
        node['_prop_name'] = prop_name
    return node


def _container(fid, children, cls='root'):
    return {
        'figmaId': fid, 'isTextNode': False,
        'semantic': {'className': cls, 'htmlTag': 'div', 'props': []},
        'children': children,
    }


def test_collect_basic():
    ir = _container('1:1', [_text_node('1:2', 'Buy Crypto', 'menu')])
    tokens = collect_text_tokens(ir)
    assert len(tokens) == 1
    assert tokens[0]['tokenId'] == 'menu'
    assert tokens[0]['default'] == 'Buy Crypto'
    assert tokens[0]['figmaId'] == '1:2'


def test_collect_dedup():
    ir = _container('1:1', [
        _text_node('1:2', 'Buy Crypto', 'menu'),
        _text_node('1:3', 'Markets',    'menu'),
        _text_node('1:4', 'Trade',      'menu'),
    ])
    tokens = collect_text_tokens(ir)
    assert [t['tokenId'] for t in tokens] == ['menu', 'menu_1', 'menu_2']


def test_collect_skips_prop_marked():
    ir = _container('1:1', [_text_node('1:2', 'Dynamic', 'title', prop_name='title')])
    assert collect_text_tokens(ir) == []


def test_collect_skips_multiline():
    ir = _container('1:1', [_text_node('1:2', 'line1\nline2', 'body')])
    assert collect_text_tokens(ir) == []


def test_collect_skips_list_node():
    node = _text_node('1:2', 'item', 'list')
    node['lineTypes'] = ['UNORDERED']
    ir = _container('1:1', [node])
    assert collect_text_tokens(ir) == []


def test_collect_shared_used_ids():
    """Two separate IRs share used_ids → global dedup."""
    used = set()
    ir1 = _container('1:1', [_text_node('1:2', 'A', 'menu')])
    ir2 = _container('2:1', [_text_node('2:2', 'B', 'menu')])
    t1 = collect_text_tokens(ir1, used)
    t2 = collect_text_tokens(ir2, used)
    assert t1[0]['tokenId'] == 'menu'
    assert t2[0]['tokenId'] == 'menu_1'


from lib.tsx_generator import (
    generate_texts_file, generate_texts_defaults_file,
    generate_tsx, render_jsx_body,
    parse_existing_texts_keys,
)


def _full_text_ir(fid, text, cls):
    return {
        'figmaId': fid, 'figmaName': cls,
        'isTextNode': True, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': text, 'textSegments': None,
        'lineTypes': [], 'localAssetPath': None,
        'css': {}, 'children': [],
        'semantic': {'htmlTag': 'span', 'className': cls,
                     'componentName': None, 'props': []},
    }


def test_render_jsx_body_with_texts_map():
    ir = {
        'figmaId': '1:1', 'figmaName': 'root',
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None,
        'lineTypes': [], 'localAssetPath': None,
        'css': {}, 'children': [_full_text_ir('1:2', 'Buy Crypto', 'menu')],
        'semantic': {'htmlTag': 'div', 'className': 'root',
                     'componentName': 'Root', 'props': []},
    }
    texts_map = {'1:2': 'menu'}
    jsx = render_jsx_body(ir, texts_map=texts_map)
    assert "{t('menu')}" in jsx
    assert 'Buy Crypto' not in jsx


def test_generate_tsx_with_texts_map_includes_hook():
    ir = {
        'figmaId': '1:1', 'figmaName': 'BtcPizzaDay',
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None,
        'lineTypes': [], 'localAssetPath': None,
        'css': {}, 'children': [_full_text_ir('1:2', 'Buy Crypto', 'menu')],
        'semantic': {'htmlTag': 'div', 'className': 'page',
                     'componentName': 'BtcPizzaDay', 'props': []},
    }
    texts_map = {'1:2': 'menu'}
    tsx = generate_tsx(ir, texts_map=texts_map, component_name_for_texts='BtcPizzaDay')
    assert 'usePageTexts' in tsx
    assert 'PAGE_TEXTS' in tsx
    assert 'TEXT_DEFAULTS' in tsx
    assert "const { t } = usePageTexts(TEXT_DEFAULTS)" in tsx
    assert "{t('menu')}" in tsx
    assert 'Buy Crypto' not in tsx


def test_generate_tsx_without_texts_map_unchanged():
    """No texts_map → original behavior, no hook injected."""
    ir = {
        'figmaId': '1:1', 'figmaName': 'Hero',
        'isTextNode': False, 'isImageNode': False,
        'isVectorNode': False, 'isDecorativeElement': False,
        'textContent': None, 'textSegments': None,
        'lineTypes': [], 'localAssetPath': None,
        'css': {}, 'children': [_full_text_ir('1:2', 'Hello', 'title')],
        'semantic': {'htmlTag': 'div', 'className': 'hero',
                     'componentName': 'Hero', 'props': []},
    }
    tsx = generate_tsx(ir)
    assert 'usePageTexts' not in tsx
    assert 'Hello' in tsx


def test_generate_texts_file_structure():
    tokens = [
        {'tokenId': 'menu', 'default': 'Buy Crypto', 'figmaId': '1:2'},
        {'tokenId': 'title', 'default': 'Hello World', 'figmaId': '1:3'},
    ]
    out = generate_texts_file(tokens, 'MyPage')
    assert "export const PAGE_TEXTS = {" in out
    assert "  menu: ''," in out
    assert "  title: ''," in out
    assert "export type PageTextToken = keyof typeof PAGE_TEXTS" in out
    assert "export type PageT =" in out


def test_generate_texts_defaults_file_structure():
    tokens = [
        {'tokenId': 'menu', 'default': 'Buy Crypto', 'figmaId': '1:2'},
        {'tokenId': 'msg',  'default': "It's fine",  'figmaId': '1:3'},
    ]
    out = generate_texts_defaults_file(tokens, 'MyPage')
    assert "import type { PageTextToken } from './MyPage.texts'" in out
    assert "export const TEXT_DEFAULTS" in out
    assert "  menu: 'Buy Crypto'," in out
    assert r"  msg: 'It\'s fine'," in out


def test_generate_texts_defaults_escapes_quotes():
    tokens = [{'tokenId': 'q', 'default': "it's a 'test'", 'figmaId': '1:1'}]
    out = generate_texts_defaults_file(tokens, 'P')
    assert r"it\'s a \'test\'" in out


import tempfile, os

def test_parse_existing_texts_keys_empty_when_no_file():
    assert parse_existing_texts_keys('/nonexistent/path.ts') == {}


def test_parse_existing_texts_keys_returns_filled_only():
    content = (
        "export const PAGE_TEXTS = {\n"
        "  menu: 'landing.nav.buy',\n"
        "  menu_1: '',\n"
        "  title: 'landing.hero_title',\n"
        "} as const\n"
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ts', delete=False) as f:
        f.write(content)
        fname = f.name
    try:
        result = parse_existing_texts_keys(fname)
        assert result == {'menu': 'landing.nav.buy', 'title': 'landing.hero_title'}
        assert 'menu_1' not in result  # empty string not included
    finally:
        os.unlink(fname)


def test_generate_texts_file_preserves_existing_keys():
    tokens = [
        {'tokenId': 'menu',  'default': 'Buy Crypto', 'figmaId': '1:2'},
        {'tokenId': 'title', 'default': 'Hello',      'figmaId': '1:3'},
        {'tokenId': 'new',   'default': 'New Text',    'figmaId': '1:4'},
    ]
    existing = {'menu': 'landing.nav.buy', 'title': 'landing.hero'}
    out = generate_texts_file(tokens, 'P', existing_keys=existing)
    assert "  menu: 'landing.nav.buy'," in out
    assert "  title: 'landing.hero'," in out
    assert "  new: ''," in out  # new token stays empty


def test_generate_texts_defaults_with_figma_source():
    tokens = [{'tokenId': 'menu', 'default': 'Buy', 'figmaId': '235:14256'}]
    out = generate_texts_defaults_file(tokens, 'P', figma_source='https://figma.com/?node-id=235-14237')
    assert '// 设计稿来源: https://figma.com/?node-id=235-14237' in out


def test_generate_texts_defaults_figma_id_comment():
    tokens = [{'tokenId': 'menu', 'default': 'Buy', 'figmaId': '235:14256'}]
    out = generate_texts_defaults_file(tokens, 'P')
    assert '// figmaId: 235:14256' in out
    lines = out.splitlines()
    id_idx = next(i for i, l in enumerate(lines) if '// figmaId:' in l)
    assert "menu: 'Buy'," in lines[id_idx + 1]  # comment directly above entry


if __name__ == '__main__':
    tests = [
        test_collect_basic, test_collect_dedup, test_collect_skips_prop_marked,
        test_collect_skips_multiline, test_collect_skips_list_node,
        test_collect_shared_used_ids,
        test_generate_texts_file_structure, test_generate_texts_defaults_file_structure,
        test_generate_texts_defaults_escapes_quotes,
        test_render_jsx_body_with_texts_map,
        test_generate_tsx_with_texts_map_includes_hook,
        test_generate_tsx_without_texts_map_unchanged,
        test_parse_existing_texts_keys_empty_when_no_file,
        test_parse_existing_texts_keys_returns_filled_only,
        test_generate_texts_file_preserves_existing_keys,
        test_generate_texts_defaults_with_figma_source,
        test_generate_texts_defaults_figma_id_comment,
    ]
    passed = 0
    for t in tests:
        try:
            t(); print(f'  ✓ {t.__name__}'); passed += 1
        except Exception as e:
            print(f'  ✗ {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')
