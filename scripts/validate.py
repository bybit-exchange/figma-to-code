#!/usr/bin/env python3
"""
Figma → React 校验（默认 Layer 0/1/2/2.5；Layer 3/4 可选）

用法：
  python3 scripts/validate.py 'https://www.figma.com/design/...?node-id=42-100'
  python3 scripts/validate.py --all
  python3 scripts/validate.py --layers=assets,static,compile,texts,colors  # 默认
  python3 scripts/validate.py --layers=assets,static,compile,texts,colors,computed,visual  # 完整

Layer 0:   资源落地校验（/assets/ 引用是否存在，自动 shutil.copy 复制，保留暂存区原件）
Layer 1:   静态规则（Sass 变量、flex/position 合法性）
Layer 2:   编译校验（less/scss 编译，无 NaN/undefined 值）
Layer 2.5: 文本 token 完整性（TSX t('key') 与 texts.ts 同步，defaults.ts 有非空兜底）
Layer 2.6: 色值 design token 覆盖率（design token vs hex 兜底比例，列出未映射的色值属性）
Layer 3:   计算样式对比（需 playwright：pip install playwright && playwright install chromium）
Layer 4:   视觉截图 diff（需 playwright + pillow）
"""

import sys
import os
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib import parse as urllib_parse, request as urllib_request

sys.path.insert(0, str(Path(__file__).parent))
from lib.paths import (
    ASSETS_STAGING_DIR, IR_DIR, PAGE_CODE_DIR,
    SCSS_VARS_PATH, PUBLIC_ASSETS_DIR,
    ir_file, ASSET_MAPS_FILENAME,
    texts_ts_filename, texts_defaults_ts_filename,
)

# ─── 参数解析 ─────────────────────────────────────────────────────────

raw_args = sys.argv[1:]
figma_url = next((a for a in raw_args if not a.startswith('--')), None)

flags = {}
for a in (a for a in raw_args if a.startswith('--')):
    k, _, v = a.lstrip('-').partition('=')
    flags[k] = v if v else 'true'

def parse_figma_url(url):
    try:
        u = urllib_parse.urlparse(url)
        m = re.search(r'/(?:design|file)/([^/]+)', u.path)
        if not m:
            return None
        qs = urllib_parse.parse_qs(u.query)
        node_id_raw = qs.get('node-id', [''])[0]
        return {'file_key': m.group(1), 'node_id': node_id_raw.replace('-', ':') or None}
    except Exception:
        return None

parsed   = parse_figma_url(figma_url) if figma_url else None
node_id  = (parsed or {}).get('node_id') or flags.get('nodeId') or flags.get('node-id')
file_key = (parsed or {}).get('file_key') or flags.get('fileKey') or flags.get('file-key')

layer_arg  = flags.get('layers', 'assets,static,compile,texts,colors')
layers     = [l.strip() for l in layer_arg.split(',')]
all_nodes  = flags.get('all') == 'true'

ir_cache_dir = IR_DIR
OUT_DIR      = Path(flags['out']) if 'out' in flags else PAGE_CODE_DIR
css_ext      = flags.get('css-ext', 'less')
DEV_URL      = flags.get('dev-url') or os.environ.get('DEV_SERVER_URL', 'http://localhost:5173')

# ─── 工具函数 ─────────────────────────────────────────────────────────

def load_figma_token():
    token_file = Path.home() / '.claude' / 'figma-token'
    if token_file.exists():
        t = token_file.read_text().strip()
        if t:
            return t
    return os.environ.get('FIGMA_ACCESS_TOKEN')

FIGMA_KEY = load_figma_token()

def get_component_name(ir):
    figma_name = ir.get('figmaName', '')
    cls = re.sub(r'[^a-zA-Z0-9-_]', '', figma_name.replace(' ', '-')).lower()
    if not cls:
        cls = f'node-{(ir.get("figmaId", "")).replace(":", "-")}'
    cls = re.sub(r'-{2,}', '-', cls).strip('-') or 'node'
    if re.match(r'^\d', cls):
        cls = 'n' + cls
    return ''.join(w.capitalize() for w in re.split(r'[-_\s]+', cls) if w)

def load_sass_var_set():
    var_path = SCSS_VARS_PATH
    if not var_path.exists():
        return set()
    result = set()
    for line in var_path.read_text().split('\n'):
        m = re.match(r'\s*(\$[\w-]+)\s*:', line)
        if m:
            result.add(m.group(1))
    return result

# ─── Layer 0：资源落地校验 + 自动修复 ──────────────────────────────────

def run_assets_validation(ir):
    """检查 TSX 中的 /assets/ 引用是否存在于 public/assets/；
    若文件仍在 .figma-to-code/1-assets/ 暂存区则自动复制落地（shutil.copytree/copy2，保留暂存区原件）。
    sev='fixed' 表示已自动修复，不扣分；sev='error' 表示无法修复，扣分且 pass=False。
    """
    issues     = []
    name       = get_component_name(ir)
    prefix     = (ir.get('figmaId') or '').replace(':', '-')

    # 定位 TSX：先找 3-page-code，再全局递归搜
    # convert.py 生成目录为 {name}-{nodeId}（name-prefix），须作为第一候选。
    # {prefix}-{name} 保留兼容旧格式产物。
    tsx_path = None
    for candidate in [
        OUT_DIR / f'{name}-{prefix}' / f'{name}.tsx',
        OUT_DIR / f'{prefix}-{name}' / f'{name}.tsx',
        OUT_DIR / name / f'{name}.tsx',
        *list(Path('.').rglob(f'{name}.tsx')),
    ]:
        if Path(candidate).exists():
            tsx_path = Path(candidate)
            break

    if tsx_path is None:
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': 'TSX 未找到，跳过资源校验'}],
                'autoFixed': 0, 'missing': 0}

    refs = set(re.findall(r'/assets/([^/"\']+)/([^"\'> \n]+)', tsx_path.read_text()))
    if not refs:
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': '组件无 /assets/ 引用'}],
                'autoFixed': 0, 'missing': 0}

    # 读 asset-path-maps.json：split --apply 已将 {name}-{nodeId} 目录重命名为 {name}。
    # staging TSX 仍含旧路径（如 /assets/MyPage-169-33787/）；若 maps 文件存在，
    # 将 refs 中的旧目录名替换为实际目录名，避免 Layer 0 在错误目录落地。
    _maps_file = OUT_DIR / f'{name}-{prefix}' / ASSET_MAPS_FILENAME
    if not _maps_file.exists():
        # 兼容两种目录命名顺序
        _maps_file = OUT_DIR / f'{prefix}-{name}' / ASSET_MAPS_FILENAME
    _assets_dir_name  = name  # public/assets/ 落地目录名（默认与组件名相同）
    _staging_dir_name = name  # 1-assets/ 暂存目录名（默认与组件名相同，可能含 nodeId 后缀）
    if _maps_file.exists():
        try:
            import json as _json
            _m = _json.loads(_maps_file.read_text())
            _assets_dir_name  = _m.get('assetsDir',  name)
            _staging_dir_name = _m.get('stagingDir', name)  # split 后暂存目录保留原 {name}-{nodeId} 命名
        except Exception:
            pass
    # 无 maps 文件时，从 1-assets/ 中前缀匹配 {name}-{nodeId} 格式的暂存目录
    if not _maps_file.exists() and ASSETS_STAGING_DIR.exists():
        candidates = [d for d in ASSETS_STAGING_DIR.iterdir()
                      if d.is_dir() and d.name.startswith(name)]
        if candidates:
            _staging_dir_name = candidates[0].name
    # 把所有引用中旧目录名（含 nodeId 后缀）替换为 asset-path-maps 指定的实际目录名。
    # 注意：_assets_dir_name 与 name 通常相同（均为 MyPage），但 staging TSX 的 ref
    # 目录是 MyPage-169-33787（带 nodeId），startswith(name) 可匹配并去掉 nodeId。
    if _maps_file.exists():
        refs = {(_assets_dir_name if d.startswith(name) else d, f) for d, f in refs}

    # 优先整体复制暂存目录到 public/assets（保留 1-assets/ 原件供对比）
    staging_dir = ASSETS_STAGING_DIR / _staging_dir_name
    public_dir  = PUBLIC_ASSETS_DIR / _assets_dir_name
    if not public_dir.exists() and staging_dir.exists():
        shutil.copytree(str(staging_dir), str(public_dir))
        count = sum(1 for _ in public_dir.iterdir())
        issues.append({'sev': 'fixed', 'field': 'assets',
                       'msg': f'整体复制 public/assets/{_assets_dir_name}/ ({count} 个文件)'})

    # 逐文件验证 + 单文件兜底复制
    auto_fixed = sum(1 for i in issues if i['sev'] == 'fixed')
    missing    = 0
    for asset_dir, filename in sorted(refs):
        target = PUBLIC_ASSETS_DIR / asset_dir / filename
        if target.exists():
            continue
        source = ASSETS_STAGING_DIR / _staging_dir_name / filename
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            auto_fixed += 1
            issues.append({'sev': 'fixed', 'field': 'assets',
                           'msg': f'单文件复制: {asset_dir}/{filename}'})
        else:
            missing += 1
            issues.append({'sev': 'error', 'field': 'assets',
                           'msg': f'缺失且无法修复: {asset_dir}/{filename}'})

    if not issues:
        issues.append({'sev': 'skip', 'msg': f'全部 {len(refs)} 个引用均已就绪'})

    return {'pass': missing == 0, 'issues': issues, 'autoFixed': auto_fixed, 'missing': missing}


# ─── Layer 1：静态校验 ────────────────────────────────────────────────

def run_static_validation(ir):
    issues = []
    sass_vars = load_sass_var_set()
    _check_node_static(ir, None, sass_vars, issues)
    return {'pass': not any(i['sev'] == 'error' for i in issues), 'issues': issues}

def _check_node_static(ir, parent, sass_vars, issues):
    for val in (ir.get('css') or {}).values():
        if isinstance(val, str) and val.startswith('$') and val not in sass_vars:
            issues.append({'sev': 'error', 'field': 'css', 'msg': f'未定义 Sass 变量 {val}'})

    if (ir.get('css') or {}).get('flex') == '1' and parent and not (parent.get('css') or {}).get('display'):
        issues.append({'sev': 'error', 'field': 'flex', 'msg': 'flex:1 但父节点非 flex 容器'})

    if (ir.get('css') or {}).get('position') == 'absolute' and parent and not (parent.get('css') or {}).get('position'):
        issues.append({'sev': 'warn', 'field': 'position', 'msg': 'absolute 但父节点缺 position:relative'})

    for child in (ir.get('children') or []):
        _check_node_static(child, ir, sass_vars, issues)

# ─── Layer 2：编译校验 ────────────────────────────────────────────────

def run_compile_validation(ir):
    issues = []
    name   = get_component_name(ir)
    prefix = (ir.get('figmaId') or '').replace(':', '-')

    prefixed = OUT_DIR / f'{prefix}-{name}'
    comp_dir = prefixed if prefixed.exists() else OUT_DIR / name
    style_p  = comp_dir / f'{name}.module.{css_ext}'

    if not style_p.exists():
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': f'样式文件未找到: {style_p}'}]}

    # 尝试 lessc (less) 或 sass (scss)
    try:
        if css_ext == 'less':
            result = subprocess.run(
                ['lessc', '--no-color', str(style_p), '/dev/null'],
                capture_output=True, text=True, timeout=15
            )
            output = result.stderr + result.stdout
        else:
            result = subprocess.run(
                ['sass', '--no-source-map', str(style_p), '/dev/null'],
                capture_output=True, text=True, timeout=15
            )
            output = result.stderr + result.stdout

        if result.returncode != 0:
            first_line = output.strip().split('\n')[0]
            issues.append({'sev': 'error', 'field': 'compile', 'msg': first_line})
        else:
            for line in output.split('\n'):
                if 'undefinedpx' in line or 'NaNpx' in line:
                    issues.append({'sev': 'error', 'field': 'css', 'msg': f'无效值: {line.strip()}'})

    except (FileNotFoundError, PermissionError, OSError):
        issues.append({'sev': 'skip', 'msg': f'{css_ext} 编译器未找到，Layer 2 跳过。安装：npm i -g less'})
    except subprocess.TimeoutExpired:
        issues.append({'sev': 'warn', 'field': 'compile', 'msg': '编译超时'})

    return {'pass': not any(i['sev'] == 'error' for i in issues), 'issues': issues}

# ─── Layer 2.5：文本 token 完整性校验 ─────────────────────────────────

def run_texts_validation(ir):
    """校验 TSX 中的 t('tokenId') 调用均有对应的 texts.ts 条目和非空 defaults 值。

    捕获两类问题：
    1. TSX 与 texts.ts 不同步（token key 不匹配 → 返回空字符串）
    2. defaults.ts 中的 fallback 值为空（i18n key 未填且 Figma 文案丢失）
    """
    issues = []
    name    = get_component_name(ir)
    prefix  = (ir.get('figmaId') or '').replace(':', '-')

    prefixed = OUT_DIR / f'{prefix}-{name}'
    comp_dir = prefixed if prefixed.exists() else OUT_DIR / name

    tsx_path      = comp_dir / f'{name}.tsx'
    texts_ts_path = comp_dir / texts_ts_filename(name)
    defaults_path = comp_dir / texts_defaults_ts_filename(name)

    if not tsx_path.exists():
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': f'TSX 文件未找到: {tsx_path}，跳过 texts 校验'}]}

    # 如果没有 texts 文件，该组件没有 i18n 文本，直接跳过
    if not texts_ts_path.exists():
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': 'texts.ts 不存在（无文本节点），跳过'}]}

    tsx_content      = tsx_path.read_text()
    texts_ts_content = texts_ts_path.read_text() if texts_ts_path.exists() else ''
    defaults_content = defaults_path.read_text() if defaults_path.exists() else ''

    # 1. 提取 TSX 中所有 t('tokenId') 调用
    tsx_tokens = set(re.findall(r"\bt\('(\w+)'\)", tsx_content))

    # 2. 提取 texts.ts 中声明的 token key
    declared_keys = set(re.findall(r'^\s+(\w+)\s*:', texts_ts_content, re.MULTILINE))

    # 3. 提取 defaults.ts 中有非空值的 token key
    nonempty_defaults = set(re.findall(r"^\s+(\w+):\s+'[^']+',", defaults_content, re.MULTILINE))

    for tok in sorted(tsx_tokens):
        if tok not in declared_keys:
            issues.append({
                'sev': 'error',
                'field': 'texts',
                'msg': f"t('{tok}') 在 texts.ts 中无对应声明（TSX 与 texts.ts 不同步，文案将为空）",
            })
        elif tok not in nonempty_defaults:
            issues.append({
                'sev': 'warn',
                'field': 'texts',
                'msg': f"t('{tok}') 在 texts.defaults.ts 中无兜底文案（i18n key 未填且无 Figma fallback）",
            })

    return {'pass': not any(i['sev'] == 'error' for i in issues), 'issues': issues}

# ─── Layer 2.6：色值 design token 覆盖率 ────────────────────────────────────

_COLOR_PROPS = re.compile(
    r'^\s*(color|background(?:-color)?|border(?:-color|-top-color|-right-color'
    r'|-bottom-color|-left-color)?|fill|stroke|outline(?:-color)?|'
    r'box-shadow|text-shadow)\s*:\s*(.+?)\s*;',
    re.MULTILINE
)
_HEX_COLOR   = re.compile(r'#[0-9a-fA-F]{3,8}')
_RGBA_COLOR  = re.compile(r'rgba?\([^)]+\)')
_DESIGN_TOKEN = re.compile(r'var\(--[\w-]+\)')
_COLOR_MIX   = re.compile(r'color-mix\(')
_SASS_VAR    = re.compile(r'\$[\w-]+')


def run_colors_validation(ir):
    """Layer 2.6: 统计色值属性中 design token 覆盖率 vs hex/rgba 兜底比例。

    扫描生成的 CSS 文件，逐条色值属性分类：
      token  — var(--xxx) 或 color-mix(…var(--xxx)…)  [主题感知]
      sass   — $sass-variable  [已有 sass 机制，不算 miss]
      static — #fff / rgba() 等固定色值  [miss，需关注]
    """
    issues  = []
    name    = get_component_name(ir)
    prefix  = (ir.get('figmaId') or '').replace(':', '-')

    prefixed  = OUT_DIR / f'{prefix}-{name}'
    comp_dir  = prefixed if prefixed.exists() else OUT_DIR / name
    style_p   = comp_dir / f'{name}.module.{css_ext}'

    if not style_p.exists():
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': f'样式文件未找到，跳过色值校验'}],
                'token': 0, 'static': 0, 'total': 0}

    content = style_p.read_text()
    token_count = static_count = sass_count = 0
    static_samples: list = []

    for m in _COLOR_PROPS.finditer(content):
        prop, val = m.group(1), m.group(2)
        if _DESIGN_TOKEN.search(val) or _COLOR_MIX.search(val):
            token_count += 1
        elif _SASS_VAR.search(val):
            sass_count += 1
        elif _HEX_COLOR.search(val) or _RGBA_COLOR.search(val):
            static_count += 1
            # 找到所属的 CSS class 名称（向上搜索最近的 .class-name {）
            preceding = content[:m.start()]
            cls_match = re.findall(r'\.([\w-]+)\s*\{', preceding)
            cls_name  = f'.{cls_match[-1]}' if cls_match else '?'
            if len(static_samples) < 8:
                static_samples.append(f'{cls_name} {prop}: {val.strip()[:50]}')

    total = token_count + static_count + sass_count
    pct   = round(token_count / total * 100) if total else 100

    if static_count > 0:
        sev = 'warn' if pct >= 80 else 'error'
        issues.append({
            'sev': sev, 'field': 'colors',
            'msg': f'{static_count} 处色值未映射到 design token（hex/rgba 兜底），覆盖率 {pct}%',
        })
        for s in static_samples:
            issues.append({'sev': 'info', 'field': 'sample', 'msg': s})

    return {
        'pass':   static_count == 0,
        'issues': issues,
        'token':  token_count,
        'static': static_count,
        'sass':   sass_count,
        'total':  total,
        'pct':    pct,
    }


# ─── Layer 3：计算样式（需要 playwright）────────────────────────────────

def run_computed_validation(ir):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': 'playwright 未安装，Layer 3 跳过。pip install playwright && playwright install chromium'}]}

    issues = []
    TOLERANT = {'width','height','top','left','right','bottom','gap','font-size','letter-spacing','border-radius'}
    EXACT    = {'display','flex-direction','justify-content','align-items','position','overflow','font-weight','text-align'}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page    = browser.new_page()
            page.goto(DEV_URL)
            _check_node_computed(page, ir, issues, TOLERANT, EXACT)
            browser.close()
    except Exception as e:
        issues.append({'sev': 'warn', 'field': 'browser', 'msg': f'浏览器错误: {e}'})

    return {'pass': not any(i['sev'] == 'error' for i in issues), 'issues': issues}

def _check_node_computed(page, ir, issues, TOLERANT, EXACT):
    figma_id = ir.get('figmaId', '')
    el = page.locator(f'[data-figma-id="{figma_id}"]')
    if not el.count():
        return

    comp = el.evaluate("""node => {
        const s = getComputedStyle(node), r = node.getBoundingClientRect();
        return { width: r.width, height: r.height, display: s.display,
                 'flex-direction': s.flexDirection, 'justify-content': s.justifyContent,
                 'align-items': s.alignItems, position: s.position, overflow: s.overflow,
                 gap: parseFloat(s.gap)||0, 'font-size': parseFloat(s.fontSize),
                 'font-weight': s.fontWeight, 'text-align': s.textAlign };
    }""")

    for prop, expected in (ir.get('css') or {}).items():
        actual = comp.get(prop)
        if actual is None:
            continue
        try:
            ev = float(str(expected).replace('px', ''))
            av = float(str(actual).replace('px', ''))
            if prop in TOLERANT:
                d = abs(ev - av)
                if d > 2:
                    issues.append({'sev': 'error' if d > 8 else 'warn', 'field': prop,
                                   'msg': f'期望 {ev}px 实际 {av}px (+{d:.1f}px)'})
            elif prop in EXACT:
                if str(expected).lower() != str(actual).lower():
                    issues.append({'sev': 'error', 'field': prop,
                                   'msg': f'期望 "{expected}" 实际 "{actual}"'})
        except (ValueError, TypeError):
            if prop in EXACT and str(expected).lower() != str(actual).lower():
                issues.append({'sev': 'error', 'field': prop,
                               'msg': f'期望 "{expected}" 实际 "{actual}"'})

    for child in (ir.get('children') or []):
        _check_node_computed(page, child, issues, TOLERANT, EXACT)

# ─── Layer 4：视觉截图（需要 playwright + pillow）───────────────────────

def run_visual_validation(ir, target_id):
    if not FIGMA_KEY:
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': 'Figma token 未设置，Layer 4 跳过'}]}

    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image, ImageChops
        import io
    except ImportError:
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': 'playwright/pillow 未安装，Layer 4 跳过'}]}

    if not file_key:
        return {'pass': True, 'issues': [{'sev': 'skip', 'msg': '无 fileKey，Layer 4 跳过'}]}

    issues = []
    try:
        url = f'https://api.figma.com/v1/images/{file_key}?ids={urllib_parse.quote(target_id)}&format=png&scale=2'
        req = urllib_request.Request(url, headers={'X-Figma-Token': FIGMA_KEY})
        with urllib_request.urlopen(req, timeout=30) as r:
            img_data = json.loads(r.read())

        img_url = (img_data.get('images') or {}).get(target_id) or \
                  (img_data.get('images') or {}).get(target_id.replace(':', '-'))
        if not img_url:
            raise ValueError('Figma 截图 URL 获取失败')

        with urllib_request.urlopen(img_url, timeout=30) as r:
            figma_bytes = r.read()

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page    = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.goto(DEV_URL)
            browser_bytes = page.locator(f'[data-figma-id="{ir["figmaId"]}"]').screenshot()
            browser.close()

        figma_img   = Image.open(io.BytesIO(figma_bytes)).convert('RGB')
        browser_img = Image.open(io.BytesIO(browser_bytes)).convert('RGB')

        if figma_img.size != browser_img.size:
            browser_img = browser_img.resize(figma_img.size, Image.LANCZOS)

        diff    = ImageChops.difference(figma_img, browser_img)
        pixels  = figma_img.width * figma_img.height
        diff_px = sum(1 for p in diff.getdata() if max(p) > 25)
        diff_pct = diff_px / pixels

        if diff_pct >= 0.02:
            issues.append({'sev': 'error', 'field': 'visual',
                           'msg': f'diff {diff_pct*100:.1f}%（阈值 2%）'})
        return {'pass': diff_pct < 0.02, 'issues': issues, 'diffPercent': diff_pct}

    except Exception as e:
        issues.append({'sev': 'warn', 'field': 'visual', 'msg': f'截图失败: {e}'})
        return {'pass': True, 'issues': issues}

# ─── Layer R: 响应式校验 ──────────────────────────────────────────────

def run_responsive_validation(ir):
    issues = []
    if ir.get('_adaptive'):
        _check_node_responsive(ir, None, issues)
    return {'pass': not any(i['sev'] == 'error' for i in issues), 'issues': issues}


def _check_node_responsive(ir, parent, issues):
    css = ir.get('css') or {}
    sizing_h = ir.get('_sizingH', '')
    node_name = ir.get('figmaName', '?')

    # R1: HUG nodes should not have percentage width
    if sizing_h == 'HUG':
        w = css.get('width', '')
        if '%' in w and w != '100%':
            issues.append({'sev': 'error', 'field': 'R1',
                           'msg': f'[{node_name}] HUG 节点不应输出百分比宽度: {w}'})

    # R2: FILL nodes must have width:100% or align-self:stretch
    if sizing_h == 'FILL':
        has_fill = css.get('width') == '100%' or css.get('flex') == '1' or css.get('align-self') == 'stretch'
        if not has_fill:
            issues.append({'sev': 'error', 'field': 'R2',
                           'msg': f'[{node_name}] FILL 节点缺少 width:100%/flex:1/align-self:stretch'})

    # R3: clamp() min should not exceed max
    for prop, val in css.items():
        if isinstance(val, str) and val.startswith('clamp('):
            parts = val[6:-1].split(',')
            if len(parts) == 3:
                try:
                    min_v = float(parts[0].strip().rstrip('px'))
                    max_v = float(parts[2].strip().rstrip('px'))
                    if min_v > max_v:
                        issues.append({'sev': 'error', 'field': 'R3',
                                       'msg': f'[{node_name}] clamp min > max: {val}'})
                except ValueError:
                    pass

    # R4: IMAGE FILL rectangles should have object-fit
    if ir.get('isImageNode') and not css.get('object-fit'):
        issues.append({'sev': 'warn', 'field': 'R4',
                       'msg': f'[{node_name}] IMAGE FILL 矩形缺少 object-fit'})

    # R5: FIXED content area (< parent width) should have max-width + centering
    if (sizing_h == 'FIXED' and parent
            and parent.get('_adaptive')
            and parent.get('css', {}).get('display') == 'flex'):
        parent_w_str = parent.get('css', {}).get('max-width', '') or parent.get('css', {}).get('width', '')
        if parent_w_str and 'px' in parent_w_str:
            try:
                parent_w = float(parent_w_str.rstrip('px'))
                node_w_str = css.get('width', '') or css.get('max-width', '')
                if node_w_str and 'px' in node_w_str:
                    node_w = float(node_w_str.rstrip('px'))
                    if node_w < parent_w * 0.97 and not css.get('max-width'):
                        issues.append({'sev': 'warn', 'field': 'R5',
                                       'msg': f'[{node_name}] 内容区缺少 max-width + 居中'})
            except ValueError:
                pass

    # Recurse into children
    for child in (ir.get('children') or []):
        _check_node_responsive(child, ir, issues)


# ─── 报告输出 ─────────────────────────────────────────────────────────

def print_layer(name, num, result):
    errors = sum(1 for i in result['issues'] if i['sev'] == 'error')
    warns  = sum(1 for i in result['issues'] if i['sev'] == 'warn')
    fixed  = sum(1 for i in result['issues'] if i['sev'] == 'fixed')
    skips  = sum(1 for i in result['issues'] if i['sev'] == 'skip')
    icon   = '⊘' if skips and not errors and not fixed else ('⟲' if fixed and not errors else ('✓' if result['pass'] else '✗'))
    detail = (f' — {errors} 错误' if errors else
              f' — {fixed} 项已自动落地' if fixed else
              f' — {warns} 警告'  if warns  else
              ' — 已跳过'         if skips  else '')
    print(f'  Layer {num}  {icon}  {name}{detail}')
    for i in (x for x in result['issues'] if x['sev'] != 'skip'):
        mark = '    ✘' if i['sev'] == 'error' else ('    ⟲' if i['sev'] == 'fixed' else '    ⚠')
        print(f'{mark}  [{i.get("field", "-")}] {i["msg"]}')

def _print_colors_layer(result):
    token  = result.get('token', 0)
    static = result.get('static', 0)
    sass   = result.get('sass', 0)
    total  = result.get('total', 0)
    pct    = result.get('pct', 100)
    issues = result.get('issues', [])

    errors = sum(1 for i in issues if i['sev'] == 'error')
    warns  = sum(1 for i in issues if i['sev'] == 'warn')
    icon   = '✓' if result['pass'] else ('⟲' if warns and not errors else '✗')

    if total == 0:
        print('  Layer 2.6  ⊘  色值覆盖率 — 无色值属性')
        return

    bar_filled = round(pct / 10)
    bar = '█' * bar_filled + '░' * (10 - bar_filled)
    print(f'  Layer 2.6  {icon}  色值覆盖率  [{bar}] {pct}%  '
          f'token:{token}  hex:{static}  sass:{sass}  共{total}处')

    for i in issues:
        if i['sev'] == 'error':
            print(f'    ✘  [{i.get("field","-")}] {i["msg"]}')
        elif i['sev'] == 'warn':
            print(f'    ⚠  [{i.get("field","-")}] {i["msg"]}')
        elif i['sev'] == 'info':
            print(f'       {i["msg"]}')


def score_deduction(result):
    return sum(5 if i['sev'] == 'error' else 1 if i['sev'] == 'warn' else 0
               for i in result['issues'])  # 'fixed' 和 'skip' 不扣分

# ─── 单节点校验 ───────────────────────────────────────────────────────

def run_validation(target_id):
    print(f'\n🔍  校验节点 {target_id}\n')
    ir_path = ir_file(target_id.replace(":", "-"))
    if not ir_path.exists():
        print(f'❌  IR 文件不存在：{ir_path}')
        print(f'    请先运行：python3 scripts/convert.py ...')
        return

    ir          = json.loads(ir_path.read_text())
    total_score = 100
    all_results = {}

    if 'assets' in layers:
        all_results['assets'] = run_assets_validation(ir)
        print_layer('资源落地', '0', all_results['assets'])
        total_score -= score_deduction(all_results['assets'])

    if 'static' in layers:
        all_results['static'] = run_static_validation(ir)
        print_layer('静态校验', '1', all_results['static'])
        total_score -= score_deduction(all_results['static'])

    if 'compile' in layers:
        all_results['compile'] = run_compile_validation(ir)
        print_layer('编译校验', '2', all_results['compile'])
        total_score -= score_deduction(all_results['compile'])

    if 'texts' in layers:
        all_results['texts'] = run_texts_validation(ir)
        print_layer('文本 token', '2.5', all_results['texts'])
        total_score -= score_deduction(all_results['texts'])

    if 'colors' in layers:
        all_results['colors'] = run_colors_validation(ir)
        r = all_results['colors']
        _print_colors_layer(r)
        total_score -= score_deduction(r)

    if 'responsive' in layers or ir.get('_adaptive'):
        all_results['responsive'] = run_responsive_validation(ir)
        print_layer('响应式校验', 'R', all_results['responsive'])
        total_score -= score_deduction(all_results['responsive'])

    if 'computed' in layers:
        all_results['computed'] = run_computed_validation(ir)
        print_layer('计算样式', '3', all_results['computed'])
        total_score -= score_deduction(all_results['computed'])

    if 'visual' in layers:
        all_results['visual'] = run_visual_validation(ir, target_id)
        print_layer('视觉截图', '4', all_results['visual'])
        diff_pct = all_results['visual'].get('diffPercent')
        if diff_pct is not None:
            total_score -= diff_pct * 100 * 0.3

    score = max(0, round(total_score))
    icon  = '✅' if score >= 90 else ('⚠️ ' if score >= 70 else '❌')
    print('\n' + '─' * 44)
    print(f'  {icon}  {ir.get("figmaName", "")}  综合评分: {score} / 100')
    print('─' * 44 + '\n')
    return score

# ─── 入口 ─────────────────────────────────────────────────────────────

def main():
    if all_nodes:
        if not ir_cache_dir.exists():
            print('❌  .figma-to-code/2-figma-extract/ 不存在，请先运行 convert')
            sys.exit(1)
        any_failed = False
        for f in ir_cache_dir.glob('*.ir.json'):
            nid = f.name.replace('.ir.json', '').replace('-', ':', 1)
            s = run_validation(nid)
            if s is None or s < 90:
                any_failed = True
        if any_failed:
            sys.exit(1)
        return

    if not node_id:
        print('\n❌  请提供 Figma URL 或 --nodeId，例：')
        print("     python3 scripts/validate.py 'https://www.figma.com/design/...?node-id=42-100'")
        sys.exit(1)

    score = run_validation(node_id)
    if score is None or score < 90:
        sys.exit(1)

if __name__ == '__main__':
    main()
