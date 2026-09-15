"""
Centralised directory constants for the figma-to-code pipeline.

All scripts that need .figma-to-code/* paths should import from here so that
a single edit propagates everywhere.
"""
from pathlib import Path
import re


def to_pascal(s: str) -> str:
    """Convert kebab-case / snake_case / space-separated string to PascalCase."""
    return ''.join(w.capitalize() for w in re.split(r'[-_\s]+', s) if w)


def figma_name_to_pascal(figma_name: str, figma_id: str = '') -> str:
    """Figma 帧名 → PascalCase 组件名（页面命名唯一真理来源）。

    与 convert.py 的 _fallback + to_pascal 完全一致的两步算法：

      Step 1  spaces → hyphens, strip non-ASCII-alphanum-hyphen-underscore, lowercase
              "BY AI HUB 2.0"        → "by-ai-hub-20"
              "portal revised跑测"   → "portal-revised"
              "模拟盘落地页"           → ""  (all Chinese stripped)
      Step 2  if empty → "node-{figma_id_safe}"  e.g. "node-202-33464"
      Step 3  to_pascal  → "ByAiHub20", "PortalRevised", "Node20233464"
    """
    class_name = re.sub(r'[^a-zA-Z0-9-_]', '', figma_name.replace(' ', '-')).lower()
    if not class_name and figma_id:
        class_name = f'node-{figma_id.replace(":", "-")}'
    return to_pascal(class_name)

# ── .figma-to-code/ workspace ─────────────────────────────────────────────
BASE_DIR           = Path('.figma-to-code')
RAW_DATA_DIR       = BASE_DIR / '1-raw-data'
ASSETS_STAGING_DIR = BASE_DIR / '1-assets'
DESIGN_TOKEN_DIR   = BASE_DIR / '1-design-token'
IR_DIR             = BASE_DIR / '2-figma-extract'
PAGE_CODE_DIR      = BASE_DIR / '3-page-code'

# ── project-level fixed paths ─────────────────────────────────────────────
PUBLIC_ASSETS_DIR  = Path('public') / 'assets'
SCSS_VARS_PATH     = Path('src') / 'styles' / '_variables.scss'
PAGES_SRC_DIR      = Path('src') / 'pages'

# ── fixed artifact file paths ─────────────────────────────────────────────
DESIGN_CSS_PATH = DESIGN_TOKEN_DIR / 'design-css.txt'

# ── page-code working sub-directory names ─────────────────────────────────
SPLIT_A_SUBDIR    = 'split-a'   # 程序化自动拆分组件
SPLIT_B_SUBDIR    = 'split-b'   # 待实现：用 AI 拆分组件
STAGE_SUBDIR      = 'stage'
COMPONENTS_SUBDIR = 'components'
HOOKS_SUBDIR      = 'hooks'

# ── generated component file names ─────────────────────────────────────────
INDEX_TS_FILENAME      = 'index.ts'
INDEX_TSX_FILENAME     = 'index.tsx'
PAGE_TSX_FILENAME      = 'Page.tsx'
RENAMES_FILENAME       = 'renames.json'
USE_PAGE_ENV_FILENAME  = 'usePageEnv.ts'

def texts_ts_filename(name: str) -> str:
    """Return the texts.ts filename for a component, e.g. 'ProductCard.texts.ts'."""
    return f'{name}.texts.ts'

def texts_defaults_ts_filename(name: str) -> str:
    """Return the texts.defaults.ts filename for a component, e.g. 'ProductCard.texts.defaults.ts'."""
    return f'{name}.texts.defaults.ts'

# ── page-code sub-paths (relative to page_dir) ────────────────────────────
PLAN_SUBPATH        = Path(SPLIT_A_SUBDIR) / 'plan.json'
PLAN_NAMING_SUBPATH = Path(SPLIT_A_SUBDIR) / 'plan.naming.json'
ASSET_MAPS_FILENAME = 'asset-path-maps.json'

# ── parameterized IR artifact helpers ─────────────────────────────────────

def ir_file(node_id_safe: str) -> Path:
    return IR_DIR / f'{node_id_safe}.ir.json'

def raw_data_file(node_id_safe: str) -> Path:
    return RAW_DATA_DIR / f'{node_id_safe}-raw-data.json'

def semantic_file(node_id_safe: str) -> Path:
    return IR_DIR / f'{node_id_safe}.semantic.json'

def code_connect_file(node_id_safe: str) -> Path:
    return IR_DIR / f'{node_id_safe}.code-connect.json'

def var_miss_file(node_id_safe: str) -> Path:
    return IR_DIR / f'{node_id_safe}-var-miss.json'

def merged_ir_file(node_id_safe: str) -> Path:
    return IR_DIR / f'merged-{node_id_safe}.ir.json'

def match_report_file(node_id_safe: str) -> Path:
    return IR_DIR / f'merged-{node_id_safe}.match-report.json'

def match_confirmed_file(node_id_safe: str) -> Path:
    return IR_DIR / f'merged-{node_id_safe}.match-confirmed.json'

# ── parameterized page-code directory helpers ──────────────────────────────

def page_code_subdir(name: str, node_id_safe: str) -> Path:
    """产物暂存目录：PAGE_CODE_DIR/{name}-{node_id_safe}"""
    return PAGE_CODE_DIR / f'{name}-{node_id_safe}'

def assets_staging_subdir(name: str, node_id_safe: str) -> Path:
    """资源暂存目录：ASSETS_STAGING_DIR/{name}-{node_id_safe}"""
    return ASSETS_STAGING_DIR / f'{name}-{node_id_safe}'
