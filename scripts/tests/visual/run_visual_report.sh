#!/usr/bin/env bash
# run_visual_report.sh — figma-to-code 视觉回归报告（支持 fast / full 两种模式）
#
# 用法：
#   bash scripts/tests/visual/run_visual_report.sh [URLs...] [选项]
#
# 模式（二选一）：
#   --fast（默认） 快速模式：跳过 Code Connect，不需要预先获取 CC 数据
#                  split 使用 --skip-cc，全流程可全自动运行
#
#   --full         完整模式：包含 Code Connect Moly 组件映射
#                  运行前需由 Claude 先调 MCP 获取 CC 数据：
#                    get_code_connect_map(fileKey=..., nodeId=..., codeConnectLabel="Web")
#                  CC 文件路径：.figma-to-code/2-figma-extract/{nodeId_safe}.code-connect.json
#
# 其他选项：
#   --viewport=1440   截图宽度（默认 1440；cases.json 中可按页面单独设置）
#   --out=report.html 报告输出路径（默认 visual-report.html）
#   --from-ir         跳过 Figma API，从缓存 IR 重建（大版本迭代后推荐）
#   --keep            保留临时目录供调试
#
# 流程（每个 Figma URL）：
#   Step 1  test_all.py              — 单元测试 pre-flight（只跑一次）
#   Step 2  convert.py <url>         — 生成 IR + 初始代码
#   Step 3  split_components --auto  — 生成拆分计划（full 模式含 CC；fast 加 --skip-cc）
#   Step 3.5 split_components --apply-renames — 复用已有 renames.json（无则跳过并警告）
#   Step 4  split_components --apply — 生成 stage/ 产物（含语义化名称）
#   Step 5  test_pipeline.py         — 中间产物完整性
#   Step 6  validate_split.py        — 拆分结构校验
#   Step 7  validate.py              — Layer 0~2.6
#   Step 8  test_product.py          — 产物契约
#   Step 9  Playwright 截图 + Figma API 截图 + diff → visual_report.py
#
# 依赖：
#   - node / npm（sandbox）
#   - python3 playwright（可选，截图用）：pip3 install playwright && playwright install chromium
#   - python3 pillow（可选，diff 用）：pip3 install pillow
#   - FIGMA_TOKEN 或 FIGMA_ACCESS_TOKEN 环境变量（或 ~/.claude/figma-token）

set -uo pipefail

# ── 路径初始化 ────────────────────────────────────────────────────────────────

VISUAL_DIR="$(cd "$(dirname "$0")" && pwd)"
TESTS_DIR="$(cd "$VISUAL_DIR/.." && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
SKILL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
SANDBOX_TEMPLATE="$VISUAL_DIR/sandbox-template"

# ── 参数解析 ──────────────────────────────────────────────────────────────────

FIGMA_URLS=()
VIEWPORT=1440
OUT_FILE=""   # 默认在 arg 解析后按 MODE+时间戳设置
FROM_IR=""
FROM_RAW_DATA=""
KEEP_TMPDIR=0
MODE="fast"   # fast（默认，--skip-cc）| full（需要预先有 CC 数据）
LOG_FILE=""   # 默认在 arg 解析后自动生成
FIXED_WORK_DIR="$SKILL_DIR/.figma-to-code/visual-test"  # 固定工作目录，产物持久化（覆盖传 --work-dir=）

for arg in "$@"; do
  case "$arg" in
    --viewport=*)      VIEWPORT="${arg#*=}" ;;
    --out=*)           OUT_FILE="${arg#*=}" ;;
    --log=*)           LOG_FILE="${arg#*=}" ;;
    --from-ir)         FROM_IR="--from-ir" ;;
    --from-raw-data)   FROM_RAW_DATA="--from-raw-data" ;;
    --keep)            KEEP_TMPDIR=1 ;;
    --fast)            MODE="fast" ;;
    --full)            MODE="full" ;;
    --work-dir=*)      FIXED_WORK_DIR="${arg#*=}" ;;
    --*)               echo "⚠  未知选项: $arg"; exit 1 ;;
    *)              FIGMA_URLS+=("$arg") ;;
  esac
done

# 命名格式：YYYYMMDD-HHmm-{mode}-{type}.{ext}
_TS="$(date +%Y%m%d-%H%M)"
_OUT_DIR="$SKILL_DIR/.figma-to-code/4-test-output"
mkdir -p "$_OUT_DIR"
[ -z "$OUT_FILE" ] && OUT_FILE="${_TS}-${MODE}-visual-report.html"
# --log= 未指定时自动生成；指定了绝对路径则直接用，否则放到 4-test-output/
if [ -z "$LOG_FILE" ]; then
  LOG_FILE="$_OUT_DIR/${_TS}-${MODE}-run.log"
elif [[ "$LOG_FILE" != /* ]]; then
  LOG_FILE="$_OUT_DIR/$LOG_FILE"
fi

# 所有输出同时写入日志文件（exec 内部重定向，不依赖外部管道）
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== START $(date) ==="

# 未提供 URL 时，从 cases.json 读取
CASES_FILE="$VISUAL_DIR/cases.json"
CASE_VIEWPORTS=()   # 与 FIGMA_URLS 平行，存每个 case 的 viewport
if [ ${#FIGMA_URLS[@]} -eq 0 ]; then
  if [ ! -f "$CASES_FILE" ]; then
    echo "❌  未提供 Figma URL，也找不到 cases.json: $CASES_FILE"
    echo "用法: bash scripts/tests/visual/run_visual_report.sh 'FIGMA_URL ...' [--viewport=1440] [--out=report.html] [--from-ir] [--keep]"
    exit 1
  fi
  echo "📋  从 cases.json 读取设计稿列表..."
  while IFS='|' read -r url vp; do
    if [ -n "$url" ]; then
      FIGMA_URLS+=("$url")
      CASE_VIEWPORTS+=("${vp:-1440}")
    fi
  done < <(python3 -c "
import json, sys
data = json.loads(open('$CASES_FILE').read())
for c in (data.get('cases') or []):
    u = (c.get('url') or '').strip()
    vp = c.get('viewport', 1440)
    if u:
        print(f'{u}|{vp}')
" 2>/dev/null)

  if [ ${#FIGMA_URLS[@]} -eq 0 ]; then
    echo "❌  cases.json 中没有配置任何 URL（url 字段为空）"
    echo "   请编辑: $CASES_FILE"
    exit 1
  fi
  echo "   找到 ${#FIGMA_URLS[@]} 个设计稿"
else
  # CLI 传入时，viewport 全部用全局 VIEWPORT
  for _ in "${FIGMA_URLS[@]}"; do CASE_VIEWPORTS+=("$VIEWPORT"); done
fi

# ── Figma token ───────────────────────────────────────────────────────────────

FIGMA_TOKEN="${FIGMA_TOKEN:-${FIGMA_ACCESS_TOKEN:-}}"
if [ -z "$FIGMA_TOKEN" ] && [ -f "$HOME/.claude/figma-token" ]; then
  FIGMA_TOKEN="$(cat "$HOME/.claude/figma-token")"
fi
if [ -z "$FIGMA_TOKEN" ]; then
  echo "⚠  未设置 FIGMA_TOKEN（Figma 截图将跳过）"
  echo "   设置方式：export FIGMA_TOKEN=xxx  或写入 ~/.claude/figma-token"
fi

# ── 依赖检测 ──────────────────────────────────────────────────────────────────

HAS_PLAYWRIGHT=0
python3 -c "from playwright.sync_api import sync_playwright" 2>/dev/null && HAS_PLAYWRIGHT=1
[ "$HAS_PLAYWRIGHT" -eq 0 ] && echo "⚠  playwright 未安装，跳过截图。安装: pip3 install playwright && playwright install chromium"

HAS_PILLOW=0
python3 -c "from PIL import Image" 2>/dev/null && HAS_PILLOW=1
[ "$HAS_PILLOW" -eq 0 ] && echo "⚠  pillow 未安装，跳过 diff。安装: pip3 install pillow"

# ── 临时目录（每次运行全新创建）──────────────────────────────────────────────

if [ -n "$FIXED_WORK_DIR" ]; then
  # 固定工作目录模式：创建（或复用）指定路径，自动保留所有产物
  WORK_DIR="$(python3 -c "import os; p=os.path.realpath('$FIXED_WORK_DIR'); os.makedirs(p, exist_ok=True); print(p)")"
  KEEP_TMPDIR=1
else
  WORK_DIR="$(python3 -c "import tempfile; print(tempfile.mkdtemp(prefix='figma-visual-'))")"
fi
echo ""
if [ "$MODE" = "full" ]; then
  echo "模式: FULL（含 Code Connect，Moly 组件精确映射）"
else
  echo "模式: FAST（跳过 Code Connect，快速算法验证）"
fi
if [ -n "$FIXED_WORK_DIR" ]; then
  echo "工作目录（固定）: ${WORK_DIR}"
else
  echo "临时工作目录: ${WORK_DIR}"
fi

SCREENSHOTS_DIR="${WORK_DIR}/screenshots"
mkdir -p "$SCREENSHOTS_DIR"

RESULTS_JSON="${WORK_DIR}/results.json"
echo "[]" > "$RESULTS_JSON"

# 进入 skill 根目录（convert.py 等脚本需要从 skill 根目录运行）
cd "$SKILL_DIR"

# ── EXIT 清理（kill 所有后台 dev server + 删除临时目录）─────────────────────

DEV_SERVER_PIDS=()
cleanup() {
  echo ""
  echo "── 清理后台进程..."
  for pid in "${DEV_SERVER_PIDS[@]+"${DEV_SERVER_PIDS[@]}"}"; do
    kill "$pid" 2>/dev/null || true
  done
  lsof -ti:5273 | xargs kill 2>/dev/null || true

  if [ "$KEEP_TMPDIR" -eq 1 ]; then
    echo ""
    if [ -n "$FIXED_WORK_DIR" ]; then
      echo "╔══════════════════════════════════════════════════╗"
      echo "║  固定工作目录模式：所有产物已保留               ║"
      echo "╠══════════════════════════════════════════════════╣"
      printf "║  工作目录: %-38s ║\n" "${WORK_DIR}"
      echo "╠══════════════════════════════════════════════════╣"
      echo "║  目录结构：                                      ║"
      echo "║    sandbox/          — React 调试项目            ║"
      echo "║    screenshots/      — 渲染截图                  ║"
      echo "║    results.json      — 所有测试结果              ║"
      echo "║    vite_batch.log    — dev server 日志           ║"
      echo "║    entry_*.json      — 每个 URL 的详细结果       ║"
      echo "╠══════════════════════════════════════════════════╣"
      echo "║  启动调试：                                      ║"
      printf "║    cd %-44s ║\n" "${WORK_DIR}/sandbox"
      echo "║    pnpm run dev   →  http://localhost:5273        ║"
      echo "║    # ?page=<PAGE_KEY> 切换页面                   ║"
      echo "╚══════════════════════════════════════════════════╝"
    else
      echo "╔══════════════════════════════════════════════════╗"
      echo "║  --keep 模式：临时项目已保留，可直接调试        ║"
      echo "╠══════════════════════════════════════════════════╣"
      printf "║  目录: %-42s ║\n" "${WORK_DIR}"
      echo "╠══════════════════════════════════════════════════╣"
      echo "║  调试步骤：                                      ║"
      echo "║    cd <目录>/sandbox                             ║"
      echo "║    # 手动修改 src/App.tsx 切换组件               ║"
      echo "║    pnpm run dev   →  http://localhost:5273        ║"
      echo "║    # 截图保存在: <目录>/screenshots/             ║"
      echo "╚══════════════════════════════════════════════════╝"
    fi
  else
    if [ -d "${WORK_DIR}" ]; then
      rm -rf "${WORK_DIR}"
      echo "   已删除临时目录: ${WORK_DIR}"
    fi
  fi
}
trap cleanup EXIT

# ── 工具函数 ──────────────────────────────────────────────────────────────────

# 运行一个命令，收集输出和退出码
run_step() {
  local label="$1"; shift
  local out
  out="$(set +e; "$@" 2>&1; echo "EXIT:$?")"
  local exit_code
  exit_code="$(echo "$out" | grep -o 'EXIT:[0-9]*$' | cut -d: -f2)"
  local output
  output="$(echo "$out" | sed '$d')"  # 去掉最后一行 EXIT:N
  local passed=false
  [ "$exit_code" = "0" ] && passed=true
  echo "$passed|$exit_code|$output"
}

# 将步骤结果追加到 entry 的 validation_results
append_validation() {
  local json_file="$1"  # 当前 entry json 文件
  local step_key="$2"
  local passed="$3"
  local output="$4"
  python3 -c "
import json, sys
path = sys.argv[1]
key  = sys.argv[2]
passed_str = sys.argv[3]
output = sys.argv[4]
data = json.loads(open(path).read())
data.setdefault('validation_results', {})[key] = {
    'passed': passed_str == 'true',
    'output': output
}
open(path, 'w').write(json.dumps(data, ensure_ascii=False))
" "$json_file" "$step_key" "$passed" "$output"
}

# 将 entry json 文件追加到 results.json 数组
append_to_results() {
  local entry_file="$1"
  python3 -c "
import json
results_path = '$RESULTS_JSON'
entry_path   = '$entry_file'
results = json.loads(open(results_path).read())
entry   = json.loads(open(entry_path).read())
results.append(entry)
open(results_path, 'w').write(json.dumps(results, ensure_ascii=False, indent=2))
"
}

# ── Sandbox 初始化（含 403 降版本兜底）────────────────────────────────────────

echo ""
echo "▶  初始化 sandbox（临时目录: ${WORK_DIR}）..."
mkdir -p "${WORK_DIR}/sandbox"
# 排除 node_modules 和 pnpm-lock.yaml（仅复制模板源文件）
rsync -a --exclude='node_modules' --exclude='pnpm-lock.yaml' \
  "$SANDBOX_TEMPLATE/" "${WORK_DIR}/sandbox/" 2>/dev/null || \
  cp -r "$SANDBOX_TEMPLATE/." "${WORK_DIR}/sandbox/"
cd "${WORK_DIR}/sandbox"

# 尝试 pnpm install，如果遇到 403 则对失败包降一个 patch 版本重试（最多 8 轮）
_npm_install_with_fallback() {
  local attempt=0
  local max_attempts=8
  while [ $attempt -lt $max_attempts ]; do
    attempt=$((attempt + 1))
    echo "   pnpm install（第 $attempt 轮）..."
    local install_out
    install_out="$(pnpm install 2>&1)"
    local install_exit=$?
    if [ $install_exit -eq 0 ]; then
      echo "   ✅ 依赖安装成功"
      return 0
    fi
    echo "$install_out" | tail -5 | sed 's/^/   /'
    # 检测是否为 403/registry 错误（48h 内发布的包 jfrog 未同步）
    if echo "$install_out" | grep -qiE 'ERR_PNPM_FETCH_403|E403|Forbidden'; then
      echo "   ⚠  检测到 403，精准降版（只降 403 的包）..."
      # 从错误输出提取 403 包名和版本，降一个 patch；
      # 直接依赖降 devDependencies，传递依赖写入 pnpm.overrides
      INSTALL_LOG="$install_out" python3 - << 'DOWNGRADE'
import json, subprocess, re, sys, os
from pathlib import Path

install_log = os.environ.get('INSTALL_LOG', '')
pkg_path = Path('package.json')
pkg = json.loads(pkg_path.read_text())

# 从 403 行提取所有失败的 "pkgname/-/pkgname-version.tgz"
failing = {}
for m in re.finditer(r'/([^/]+)/-/\1-([0-9]+\.[0-9]+\.[0-9]+)\.tgz.*403', install_log):
    name, ver = m.group(1), m.group(2)
    failing[name] = ver  # 只保留最后一个版本

if not failing:
    print('   未能从日志提取 403 包，无变更')
    sys.exit(0)

def prev_patch(name, current):
    """返回比 current 低一个 patch 的版本。"""
    try:
        out = subprocess.check_output(
            ['npm', 'view', name, 'versions', '--json'], text=True, timeout=15)
        stable = [v for v in json.loads(out) if re.fullmatch(r'\d+\.\d+\.\d+', v)]
        if current in stable:
            idx = stable.index(current)
            return stable[idx - 1] if idx > 0 else None
    except Exception:
        pass
    return None

# 收集所有直接依赖包名
direct = set()
for section in ('dependencies', 'devDependencies'):
    direct.update(pkg.get(section, {}).keys())

changed = False
pkg.setdefault('pnpm', {}).setdefault('overrides', {})

for name, ver in failing.items():
    down = prev_patch(name, ver)
    if not down:
        print(f'   ⚠  {name}@{ver}: 找不到上一个版本，跳过')
        continue
    if name in direct:
        for section in ('dependencies', 'devDependencies'):
            if name in pkg.get(section, {}):
                print(f'   降直接依赖: {name} {ver} → {down}')
                pkg[section][name] = down
                changed = True
    else:
        print(f'   加 override（传递依赖）: {name} {ver} → {down}')
        pkg['pnpm']['overrides'][name] = down
        changed = True

if changed:
    pkg_path.write_text(json.dumps(pkg, indent=2))
else:
    print('   无变更')
DOWNGRADE
    else
      echo "   ❌ pnpm install 失败（非 403 错误），退出"
      echo "$install_out"
      return 1
    fi
  done
  echo "   ❌ pnpm install 失败，已重试 $max_attempts 次"
  return 1
}

_npm_install_with_fallback
cd "$SKILL_DIR"

# ── Step 1: 单元测试（pre-flight，只跑一次）───────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  Step 1: 单元测试 pre-flight（test_all.py）      ║"
echo "╚══════════════════════════════════════════════════╝"

TEST_ALL_OUTPUT=""
TEST_ALL_PASSED=false
set +e
TEST_ALL_OUTPUT="$(python3 "$SCRIPTS_DIR/tests/test_all.py" 2>&1)"
TEST_ALL_EXIT=$?
set -e
[ "$TEST_ALL_EXIT" -eq 0 ] && TEST_ALL_PASSED=true || true
echo "$TEST_ALL_OUTPUT" | tail -5 | sed 's/^/   /'
if [ "$TEST_ALL_PASSED" = "true" ]; then
  echo "   ✅ 单元测试通过"
else
  echo "   ❌ 单元测试失败（继续执行，报告中记录失败）"
fi

# ── 逐个处理 Figma URL ────────────────────────────────────────────────────────

URL_INDEX=0
for FIGMA_URL in "${FIGMA_URLS[@]}"; do
  URL_INDEX=$((URL_INDEX + 1))
  CASE_VIEWPORT="${CASE_VIEWPORTS[$((URL_INDEX-1))]:-$VIEWPORT}"
  echo ""
  echo "╔══════════════════════════════════════════════════╗"
  printf "║  URL %d/%d: %-38s ║\n" "$URL_INDEX" "${#FIGMA_URLS[@]}" "$(echo "$FIGMA_URL" | cut -c1-38)"
  echo "╚══════════════════════════════════════════════════╝"

  # 初始化 entry json
  ENTRY_FILE="${WORK_DIR}/entry_${URL_INDEX}.json"

  # 解析 file_key 和 node_id
  PARSED=$(python3 -c "
import re, sys
url = sys.argv[1]
# file_key: /design/<key>/ 或 /file/<key>/
m_key = re.search(r'/(?:design|file)/([^/?#]+)', url)
file_key = m_key.group(1) if m_key else ''
# node_id: node-id=42-100 → 42:100
m_nid = re.search(r'node[-_]id=([0-9]+)[-:]([0-9]+)', url)
if m_nid:
    node_id = f'{m_nid.group(1)}:{m_nid.group(2)}'
    node_id_safe = f'{m_nid.group(1)}-{m_nid.group(2)}'
else:
    node_id = ''
    node_id_safe = ''
print(f'{file_key}|{node_id}|{node_id_safe}')
" "$FIGMA_URL" 2>/dev/null)

  FILE_KEY="$(echo "$PARSED" | cut -d'|' -f1)"
  NODE_ID="$(echo "$PARSED" | cut -d'|' -f2)"      # 42:100
  NODE_ID_SAFE="$(echo "$PARSED" | cut -d'|' -f3)"  # 42-100

  echo "   file_key=$FILE_KEY  node_id=$NODE_ID  safe=$NODE_ID_SAFE"

  # 初始化 entry
  python3 -c "
import json
entry = {
    'name': '',
    'figma_url': '$FIGMA_URL',
    'file_key': '$FILE_KEY',
    'node_id': '$NODE_ID',
    'node_id_safe': '$NODE_ID_SAFE',
    'viewport': $CASE_VIEWPORT,
    'rendered_screenshot': None,
    'validation_results': {}
}
open('$ENTRY_FILE', 'w').write(json.dumps(entry, ensure_ascii=False))
"
  # 写入 test_all 结果（已跑完）
  append_validation "$ENTRY_FILE" "test_all" "$TEST_ALL_PASSED" "$TEST_ALL_OUTPUT"

  set +e  # 以下步骤失败不中止

  # ── Step 2: convert.py ────────────────────────────────────────────────────
  echo ""
  echo "   ▶  Step 2: convert.py..."
  CONVERT_ARGS=("$FIGMA_URL")
  [ -n "$FROM_IR" ]       && CONVERT_ARGS+=("$FROM_IR")
  [ -n "$FROM_RAW_DATA" ] && CONVERT_ARGS+=("$FROM_RAW_DATA")
  S2_OUTPUT="$(python3 "$SCRIPTS_DIR/convert.py" "${CONVERT_ARGS[@]}" 2>&1)"
  S2_EXIT=$?
  S2_PASSED=false; [ "$S2_EXIT" -eq 0 ] && S2_PASSED=true
  echo "$S2_OUTPUT" | tail -3 | sed 's/^/     /'
  echo "     $([ "$S2_PASSED" = "true" ] && echo '✅' || echo '❌') convert.py"
  append_validation "$ENTRY_FILE" "convert" "$S2_PASSED" "$S2_OUTPUT"

  # ── 推断 page 目录 ─────────────────────────────────────────────────────────
  PAGE_DIR=""
  PAGE_NAME=""
  if [ -n "$NODE_ID_SAFE" ]; then
    PAGE_DIR_RESULT="$(python3 -c "
import sys
from pathlib import Path
nid = sys.argv[1]
base = Path('.figma-to-code/3-page-code')
if not base.exists():
    print('')
    sys.exit(0)
candidates = list(base.glob(f'*-{nid}')) + list(base.glob(f'{nid}-*'))
dirs = [d for d in candidates if d.is_dir()]
print(str(dirs[0]) if dirs else '')
" "$NODE_ID_SAFE" 2>/dev/null)"
    PAGE_DIR="$PAGE_DIR_RESULT"

    if [ -n "$PAGE_DIR" ]; then
      PAGE_NAME="$(python3 -c "
from pathlib import Path
d = Path('$PAGE_DIR')
tsx_files = list(d.glob('*.tsx'))
print(tsx_files[0].stem if tsx_files else '')
" 2>/dev/null)"
      # 写入 name
      python3 -c "
import json
path = '$ENTRY_FILE'
data = json.loads(open(path).read())
data['name'] = '$PAGE_NAME'
open(path, 'w').write(json.dumps(data, ensure_ascii=False))
"
      echo "   page_dir: $PAGE_DIR  page_name: $PAGE_NAME"
    else
      echo "   ⚠  找不到 page 目录（node_id_safe=${NODE_ID_SAFE}）"
      PAGE_NAME="page_${URL_INDEX}"
      python3 -c "
import json
path = '$ENTRY_FILE'
data = json.loads(open(path).read())
data['name'] = 'page_${URL_INDEX}'
open(path, 'w').write(json.dumps(data, ensure_ascii=False))
"
    fi
  fi

  # ── Step 3: split_components --auto ───────────────────────────────────────
  if [ -n "$NODE_ID_SAFE" ]; then
    echo ""
    if [ "$MODE" = "fast" ]; then
      echo "   ▶  Step 3: split_components --auto（fast 模式，--skip-cc）..."
      S3_OUTPUT="$(python3 "$SCRIPTS_DIR/split_components.py" --node-id="$NODE_ID_SAFE" --auto --skip-cc 2>&1)"
    else
      CC_FILE=".figma-to-code/2-figma-extract/${NODE_ID_SAFE}.code-connect.json"
      if [ -f "$CC_FILE" ]; then
        echo "   ▶  Step 3: split_components --auto（full 模式，含 CC）..."
        S3_OUTPUT="$(python3 "$SCRIPTS_DIR/split_components.py" --node-id="$NODE_ID_SAFE" --auto 2>&1)"
      else
        echo "   ▶  Step 3: split_components --auto（full 模式，CC 缺失 → --skip-cc 降级）..."
        echo "   ⚠  full 模式缺少 CC 数据: ${CC_FILE}（需 Claude 先调 MCP 获取）"
        S3_OUTPUT="$(python3 "$SCRIPTS_DIR/split_components.py" --node-id="$NODE_ID_SAFE" --auto --skip-cc 2>&1)"
      fi
    fi
    S3_EXIT=$?
    S3_PASSED=false; [ "$S3_EXIT" -eq 0 ] && S3_PASSED=true
    echo "     $([ "$S3_PASSED" = "true" ] && echo '✅' || echo '❌') split --auto"
    append_validation "$ENTRY_FILE" "split_auto" "$S3_PASSED" "$S3_OUTPUT"
  else
    append_validation "$ENTRY_FILE" "split_auto" "false" "缺少 node_id，跳过"
    S3_PASSED=false
  fi

  # ── Step 3.5: split_components --apply-renames（若有 renames.json）────────
  if [ "$S3_PASSED" = "true" ] && [ -n "$NODE_ID_SAFE" ]; then
    echo ""
    RENAMES_FILE="$(find .figma-to-code/3-page-code -path "*${NODE_ID_SAFE}/split-a/renames.json" 2>/dev/null | head -1)"
    if [ -n "$RENAMES_FILE" ]; then
      echo "   ▶  Step 3.5: apply-renames（复用 $(basename "$RENAMES_FILE")）..."
      S35_OUTPUT="$(python3 "$SCRIPTS_DIR/split_components.py" \
        --node-id="$NODE_ID_SAFE" --apply-renames="$RENAMES_FILE" 2>&1)"
      S35_EXIT=$?
      S35_PASSED=false; [ "$S35_EXIT" -eq 0 ] && S35_PASSED=true
      echo "     $([ "$S35_PASSED" = "true" ] && echo '✅' || echo '❌') apply-renames"
      append_validation "$ENTRY_FILE" "apply_renames" "$S35_PASSED" "$S35_OUTPUT"
    else
      echo "   ⚠  Step 3.5: 未找到 renames.json，跳过 AI rename pass"
      echo "      首次运行需通过 /figma-to-code 完整流程生成 renames.json"
      append_validation "$ENTRY_FILE" "apply_renames" "false" "renames.json 不存在，跳过"
    fi
  fi

  # ── Step 4: split_components --apply ──────────────────────────────────────
  if [ "$S3_PASSED" = "true" ] && [ -n "$NODE_ID_SAFE" ]; then
    echo ""
    echo "   ▶  Step 4: split_components --apply..."
    S4_OUTPUT="$(python3 "$SCRIPTS_DIR/split_components.py" --node-id="$NODE_ID_SAFE" --apply \
      --dest="${WORK_DIR}/sandbox/src/pages/${URL_INDEX}_${PAGE_NAME}" 2>&1)"
    S4_EXIT=$?
    S4_PASSED=false; [ "$S4_EXIT" -eq 0 ] && S4_PASSED=true
    echo "     $([ "$S4_PASSED" = "true" ] && echo '✅' || echo '❌') split --apply"
    append_validation "$ENTRY_FILE" "split_apply" "$S4_PASSED" "$S4_OUTPUT"
  else
    append_validation "$ENTRY_FILE" "split_apply" "false" "split --auto 未通过，跳过"
    S4_PASSED=false
  fi

  # ── Step 5: test_pipeline.py ───────────────────────────────────────────────
  if [ -n "$NODE_ID_SAFE" ]; then
    echo ""
    echo "   ▶  Step 5: test_pipeline.py..."
    S5_OUTPUT="$(python3 "$SCRIPTS_DIR/tests/test_pipeline.py" --node-id="$NODE_ID_SAFE" 2>&1)"
    S5_EXIT=$?
    S5_PASSED=false; [ "$S5_EXIT" -eq 0 ] && S5_PASSED=true
    echo "     $([ "$S5_PASSED" = "true" ] && echo '✅' || echo '❌') test_pipeline"
    append_validation "$ENTRY_FILE" "pipeline" "$S5_PASSED" "$S5_OUTPUT"
  else
    append_validation "$ENTRY_FILE" "pipeline" "false" "缺少 node_id，跳过"
  fi

  # ── Step 6: validate_split.py ─────────────────────────────────────────────
  if [ -n "$NODE_ID_SAFE" ]; then
    echo ""
    echo "   ▶  Step 6: validate_split.py..."
    S6_OUTPUT="$(python3 "$SCRIPTS_DIR/validate_split.py" --node-id="$NODE_ID_SAFE" 2>&1)"
    S6_EXIT=$?
    S6_PASSED=false; [ "$S6_EXIT" -eq 0 ] && S6_PASSED=true
    echo "     $([ "$S6_PASSED" = "true" ] && echo '✅' || echo '❌') validate_split"
    append_validation "$ENTRY_FILE" "validate_split" "$S6_PASSED" "$S6_OUTPUT"
  else
    append_validation "$ENTRY_FILE" "validate_split" "false" "缺少 node_id，跳过"
  fi

  # ── Step 7: validate.py（含 P3 阶段一自动重试）─────────────────────────
  echo ""
  echo "   ▶  Step 7: validate.py --layers=assets,static,compile,texts,colors..."
  S7_OUTPUT="$(python3 "$SCRIPTS_DIR/validate.py" "$FIGMA_URL" \
    --layers=assets,static,compile,texts,colors 2>&1)"
  S7_EXIT=$?
  S7_PASSED=false; [ "$S7_EXIT" -eq 0 ] && S7_PASSED=true

  # P3 阶段一：Layer 0 有缺失资源时自动重试下载（与 SKILL.md P3 Step 2 阶段一一致）
  if [ "$S7_PASSED" = "false" ] && echo "$S7_OUTPUT" | grep -q "缺失且无法修复"; then
    echo "   ⟳  Layer 0 有缺失资源，执行 P3 阶段一：convert.py --from-ir 重试下载..."
    RETRY_OUTPUT="$(FIGMA_TOKEN="$FIGMA_TOKEN" python3 "$SCRIPTS_DIR/convert.py" \
      "$FIGMA_URL" --from-ir 2>&1)"
    RETRY_EXIT=$?
    echo "     $([ "$RETRY_EXIT" -eq 0 ] && echo '✅' || echo '⚠️ ') convert --from-ir (exit ${RETRY_EXIT})"
    # 重跑 validate --layers=assets 验证补全情况
    echo "   ▶  Step 7 (retry): validate.py --layers=assets..."
    S7_OUTPUT="$(python3 "$SCRIPTS_DIR/validate.py" "$FIGMA_URL" \
      --layers=assets,static,compile,texts,colors 2>&1)"
    S7_EXIT=$?
    S7_PASSED=false; [ "$S7_EXIT" -eq 0 ] && S7_PASSED=true
    # 统计最终仍缺失数（阶段二：报告不可访问资源）
    STILL_MISSING="$(echo "$S7_OUTPUT" | grep -c "缺失且无法修复" || true)"
    if [ "$STILL_MISSING" -gt 0 ]; then
      echo "   ⚠️  阶段二：仍有 ${STILL_MISSING} 个资源在 Figma 端不可访问（权限受限或已删除）"
    fi
  fi

  echo "     $([ "$S7_PASSED" = "true" ] && echo '✅' || echo '❌') validate"
  append_validation "$ENTRY_FILE" "validate" "$S7_PASSED" "$S7_OUTPUT"

  # ── Step 8: test_product.py ───────────────────────────────────────────────
  echo ""
  echo "   ▶  Step 8: test_product.py --contract-only..."
  S8_OUTPUT="$(python3 "$SCRIPTS_DIR/tests/test_product.py" \
    --out=".figma-to-code/3-page-code" --contract-only 2>&1)"
  S8_EXIT=$?
  S8_PASSED=false; [ "$S8_EXIT" -eq 0 ] && S8_PASSED=true
  echo "     $([ "$S8_PASSED" = "true" ] && echo '✅' || echo '❌') test_product"
  append_validation "$ENTRY_FILE" "test_product" "$S8_PASSED" "$S8_OUTPUT"

  # ── Step 9: split --apply --dest 落地到 sandbox（与真实项目保持一致）──────
  echo ""
  echo "   ▶  Step 9: split --apply --dest 落地到 sandbox..."
  if [ -n "$NODE_ID_SAFE" ] && [ -n "$PAGE_NAME" ]; then
    PAGE_KEY="${URL_INDEX}_${PAGE_NAME}"
    SANDBOX_PAGES="${WORK_DIR}/sandbox/src/pages"
    mkdir -p "$SANDBOX_PAGES/$PAGE_KEY"

    # 用与真实项目完全一致的落地命令，生成正确的 components/ 子目录结构和 import 路径
    set +e
    DEST_OUT="$(python3 "$SCRIPTS_DIR/split_components.py" \
      --node-id="$NODE_ID_SAFE" --apply \
      --dest="$SANDBOX_PAGES/$PAGE_KEY" 2>&1)"
    DEST_EXIT=$?
    set -e
    if [ "$DEST_EXIT" -eq 0 ]; then
      echo "     ✅ 落地成功: $SANDBOX_PAGES/$PAGE_KEY"

      # 先探测 split --apply --dest 实际创建的子目录名（split 用 plan.pageComponent 命名，
      # 可能与 PAGE_NAME 大小写不同，img src 路径以 ACTUAL_PAGE_NAME 为准）
      ACTUAL_PAGE_NAME="$(python3 -c "
from pathlib import Path
dest = Path('$SANDBOX_PAGES/$PAGE_KEY')
subdirs = [d.name for d in dest.iterdir() if d.is_dir() and (d / 'index.tsx').exists()]
print(subdirs[0] if subdirs else '$PAGE_NAME')
" 2>/dev/null || echo "$PAGE_NAME")"

      # 复制 assets：覆盖三种可能的 img src 前缀格式
      #   /assets/{PAGE_NAME}/...              新格式（与 PAGE_NAME 相同大小写）
      #   /assets/{ACTUAL_PAGE_NAME}/...       实际组件名（可能大小写不同）
      #   /assets/{PAGE_NAME}-{nodeId}/...     旧格式（带 nodeId）
      ASSETS_SRC_DIR=".figma-to-code/1-assets/${PAGE_NAME}-${NODE_ID_SAFE}"
      if [ -d "$ASSETS_SRC_DIR" ]; then
        for ASSET_DEST_NAME in "${PAGE_NAME}" "${ACTUAL_PAGE_NAME}" "${PAGE_NAME}-${NODE_ID_SAFE}"; do
          if [ -n "$ASSET_DEST_NAME" ]; then
            mkdir -p "${WORK_DIR}/sandbox/public/assets/${ASSET_DEST_NAME}"
            cp -r "$ASSETS_SRC_DIR/." "${WORK_DIR}/sandbox/public/assets/${ASSET_DEST_NAME}/"
          fi
        done
      elif [ -d ".figma-to-code/1-assets" ]; then
        mkdir -p "${WORK_DIR}/sandbox/public/assets"
        cp -r ".figma-to-code/1-assets/." "${WORK_DIR}/sandbox/public/assets/"
      fi

      # 注册页面到 results（只有落地成功才注册，避免 Vite import 错误导致所有页面崩溃）
      python3 -c "
import json
path = '$ENTRY_FILE'
data = json.loads(open(path).read())
data['page_key']  = '$PAGE_KEY'
data['page_name'] = '$ACTUAL_PAGE_NAME'
data['rendered_screenshot'] = None
open(path, 'w').write(json.dumps(data, ensure_ascii=False))
"
      echo "     ✅ 已注册: ${PAGE_KEY} (component: ${ACTUAL_PAGE_NAME})"
    else
      echo "     ❌ split --dest 失败（exit ${DEST_EXIT}），跳过截图（不注册，避免 Vite import 错误）"
      echo "$DEST_OUT" | tail -3 | sed 's/^/     /'
    fi
  else
    echo "     ── 无 node_id / page 产物，跳过注册"
  fi

  append_to_results "$ENTRY_FILE"

  set -e  # 恢复 pipefail 模式
  echo ""
  echo "   ── URL $URL_INDEX 处理完成 ──"

done  # end for each URL

# ── 批量截图（单 dev server + query param 路由，一个 browser session）────────

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  批量截图（单 dev server + ?page= 路由）         ║"
echo "╚══════════════════════════════════════════════════╝"

if [ "$HAS_PLAYWRIGHT" -eq 1 ]; then
  # 生成 App.tsx：所有页面用 React.lazy + query param 切换
  python3 - "$RESULTS_JSON" "${WORK_DIR}/sandbox/src/App.tsx" << 'GENAPP'
import json, sys
from pathlib import Path

results = json.loads(Path(sys.argv[1]).read_text())
app_out  = Path(sys.argv[2])

lazy_lines  = []
pages_lines = []
vp_lines    = []

for e in results:
    key       = e.get('page_key', '')
    name      = e.get('name', '') or e.get('page_name', '')
    page_name = e.get('page_name', '') or name   # split --dest 在 pages/{key}/ 下创建的子目录
    vp        = e.get('viewport', 1440)
    if not key or not page_name:
        continue
    var = 'C_' + key.replace('-', '_')   # valid JS identifier
    # split --apply --dest=pages/{key}/ 生成 pages/{key}/{page_name}/index.tsx
    lazy_lines.append(
        f"const {var} = React.lazy(() => import('./pages/{key}/{page_name}'))"
    )
    pages_lines.append(f"  '{key}': {var},")
    vp_lines.append(f"  '{key}': {vp},")

app = (
    "import React from 'react'\n\n"
    + "\n".join(lazy_lines)
    + "\n\nconst PAGES: Record<string, React.ComponentType<any>> = {\n"
    + "\n".join(pages_lines)
    + "\n}\n\nconst VIEWPORTS: Record<string, number> = {\n"
    + "\n".join(vp_lines)
    + "\n}\n\n"
    + "export default function App() {\n"
    + "  const key = new URLSearchParams(window.location.search).get('page') || ''\n"
    + "  const Comp = PAGES[key]\n"
    + "  const vp   = VIEWPORTS[key] || 1440\n"
    + "  if (!Comp) return <div style={{ padding: 40, color: '#999' }}>No page: {key}</div>\n"
    + "  return (\n"
    + "    <React.Suspense fallback={<div>Loading…</div>}>\n"
    + "      <div style={{ width: `${vp}px` }}><Comp /></div>\n"
    + "    </React.Suspense>\n"
    + "  )\n"
    + "}\n"
)
app_out.write_text(app)
print(f'App.tsx 生成（{len(lazy_lines)} 个页面）')
GENAPP

  # 启动一个 dev server
  cd "${WORK_DIR}/sandbox"
  lsof -ti:5273 | xargs kill 2>/dev/null || true
  sleep 0.5
  pnpm run dev > "${WORK_DIR}/vite_batch.log" 2>&1 &
  BATCH_DEV_PID=$!
  DEV_SERVER_PIDS+=("$BATCH_DEV_PID")
  cd "$SKILL_DIR"

  # 等待就绪（最多 30s）
  DEV_READY=false
  for i in $(seq 1 30); do
    sleep 1
    if curl -s http://localhost:5273 > /dev/null 2>&1; then
      DEV_READY=true
      echo "   ✅ dev server 就绪（${i}s）"
      break
    fi
  done

  if [ "$DEV_READY" = "true" ]; then
    # 单 browser session 截所有页面，截完后更新 results.json
    python3 - "$RESULTS_JSON" "$SCREENSHOTS_DIR" << 'SCREENSHOT_ALL'
from playwright.sync_api import sync_playwright
import json, sys
from pathlib import Path

results_path   = Path(sys.argv[1])
screenshots_dir = Path(sys.argv[2])
entries = json.loads(results_path.read_text())

with sync_playwright() as p:
    browser = p.chromium.launch()
    for e in entries:
        key  = e.get('page_key', '')
        name = e.get('name', '') or key
        vp   = e.get('viewport', 1440)
        if not key:
            continue
        try:
            pg = browser.new_page(viewport={'width': vp, 'height': 900})
            pg.goto(
                f'http://localhost:5273?page={key}',
                wait_until='networkidle', timeout=30000,
            )
            pg.wait_for_timeout(2000)
            out = screenshots_dir / f'{name}_rendered.png'
            pg.screenshot(path=str(out), full_page=True)
            pg.close()
            print(f'  ✅ {name}: {out.name}')
        except Exception as ex:
            print(f'  ❌ {name}: {ex}')
    browser.close()

# 回写 rendered_screenshot 路径
for e in entries:
    name = e.get('name', '') or e.get('page_key', '')
    p = screenshots_dir / f'{name}_rendered.png'
    e['rendered_screenshot'] = str(p) if (name and p.exists()) else None
results_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2))
print('results.json 已更新')
SCREENSHOT_ALL
  else
    echo "   ❌ dev server 启动超时（查看: ${WORK_DIR}/vite_batch.log）"
  fi

  kill "$BATCH_DEV_PID" 2>/dev/null || true
  lsof -ti:5273 | xargs kill 2>/dev/null || true
else
  echo "   ── 跳过截图（playwright 未安装）"
fi

# ── 生成报告 ─────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  生成 HTML 报告                                  ║"
echo "╚══════════════════════════════════════════════════╝"

mkdir -p "$SKILL_DIR/.figma-to-code/4-test-output"
REPORT_PATH="$SKILL_DIR/.figma-to-code/4-test-output/$OUT_FILE"
# 导出 FIGMA_TOKEN 给 visual_report.py
export FIGMA_TOKEN

python3 "$VISUAL_DIR/visual_report.py" \
  --results-json="$RESULTS_JSON" \
  --out="$REPORT_PATH" \
  --mode="$MODE"

echo ""
echo "══════════════════════════════════════════════════"
echo "  报告已生成: $REPORT_PATH"
echo "  位置: .figma-to-code/4-test-output/"
echo "══════════════════════════════════════════════════"
[ -n "$LOG_FILE" ] && echo "=== DONE $(date) ==="
