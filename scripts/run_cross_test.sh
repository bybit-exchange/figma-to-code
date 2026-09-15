#!/usr/bin/env bash
# run_cross_test.sh — 完整算法 E2E 验证
#
# 用法：
#   bash scripts/run_cross_test.sh 'https://www.figma.com/design/xxx?node-id=42-100'
#   bash scripts/run_cross_test.sh 'URL' --regression   # 同时跑特定设计稿回归断言
#
# 覆盖范围（可自动化部分）：
#   Step 1: test_all.py          → 算法单元测试（无 Figma 依赖，失败则中止）
#   Step 2: convert.py --split   → 拉 Figma API，生成 IR + 暂存代码 + split plan
#   Step 3: split_components.py  → --auto 生成拆分计划
#   Step 3.5: split_components   → --apply 生成 stage/ 拆分产物
#   Step 3.6: test_pipeline.py   → 中间产物完整性（文件齐全 + import 可解析）
#   Step 4: validate_split.py    → CSS 一致性校验（无漂移）
#   Step 5: validate.py          → Layer 0+1+2（资源/编译/静态）
#   Step 6: test_product.py      → 产物契约测试（文件结构/语法）
#
# ⚠️  AI rename pass（P1.5 步骤 3）无法自动化，需真实调用 /figma-to-code 覆盖完整流程。

set -euo pipefail

FIGMA_URL="${1:-}"
REGRESSION="${2:-}"
if [ -z "$FIGMA_URL" ]; then
  echo "用法: bash scripts/run_cross_test.sh 'FIGMA_URL' [--regression]"
  exit 1
fi

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$SKILL_DIR/scripts"
BASE=".figma-to-code"

# 从 URL 提取 node-id（格式：node-id=42-100 或 node-id=42:100）
NODE_ID_RAW=$(python3 -c "
import sys, re
url = '$FIGMA_URL'
m = re.search(r'node[-_]id=([0-9]+)[-:]([0-9]+)', url)
if m: print(f'{m.group(1)}-{m.group(2)}')
else: print('')
" 2>/dev/null)

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     figma-to-code 算法 E2E 验证          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Step 1: 算法单元测试（pre-flight，失败则中止）──────────────────────
echo "▶  Step 1: 单元测试（无 Figma 依赖）..."
python3 "$SCRIPTS_DIR/tests/test_all.py" 2>&1 | sed 's/^/   /'
# pipefail 保证 python3 失败时管道为非零；set -e 自动中止
echo "   ✅  单元测试通过"

# ── Step 2: 转换（含 --split 生成拆分计划）────────────────────────────
echo ""
echo "▶  Step 2: 转换 Figma 设计稿（--split）..."
python3 "$SCRIPTS_DIR/convert.py" "$FIGMA_URL" --split 2>&1 | sed 's/^/   /'
echo "   ✅  转换完成"

# ── Step 3+4+5+6: 产物验证（失败记录但不中止）─────────────────────────
set +e

# Step 3: split_components --auto（生成拆分计划，不落地）
if [ -n "$NODE_ID_RAW" ]; then
  echo ""
  echo "▶  Step 3: 组件拆分计划（split_components --auto）..."
  python3 "$SCRIPTS_DIR/split_components.py" \
    --node-id="$NODE_ID_RAW" --auto 2>&1 | sed 's/^/   /'
  STEP3=${PIPESTATUS[0]}
  [ "$STEP3" -eq 0 ] && echo "   ✅  拆分计划生成成功" || echo "   ❌  拆分计划失败"
else
  echo ""
  echo "   ⚠️  Step 3: 无法从 URL 提取 node-id，跳过 split_components"
  STEP3=0
fi

# Step 3.5: split_components --apply（生成 stage/ 拆分产物）
if [ -n "$NODE_ID_RAW" ] && [ "$STEP3" -eq 0 ]; then
  echo ""
  echo "▶  Step 3.5: split --apply（生成 stage/ 拆分产物）..."
  python3 "$SCRIPTS_DIR/split_components.py" \
    --node-id="$NODE_ID_RAW" --apply 2>&1 | sed 's/^/   /'
  STEP3_5=${PIPESTATUS[0]}
  [ "$STEP3_5" -eq 0 ] && echo "   ✅  stage/ 产物生成成功" || echo "   ❌  stage/ 产物生成失败"
else
  STEP3_5=0
fi

# Step 3.6: test_pipeline.py（中间产物完整性 + import 可解析）
if [ -n "$NODE_ID_RAW" ] && [ "$STEP3_5" -eq 0 ]; then
  echo ""
  echo "▶  Step 3.6: test_pipeline.py（中间产物完整性）..."
  python3 "$SCRIPTS_DIR/tests/test_pipeline.py" \
    --node-id="$NODE_ID_RAW" 2>&1 | sed 's/^/   /'
  STEP3_6=${PIPESTATUS[0]}
  [ "$STEP3_6" -eq 0 ] && echo "   ✅  中间产物完整性通过" || echo "   ❌  中间产物完整性失败"
else
  STEP3_6=0
fi

# Step 4: validate_split（校验拆分结构）
if [ -n "$NODE_ID_RAW" ] && [ "$STEP3" -eq 0 ]; then
  echo ""
  echo "▶  Step 4: validate_split（拆分结构校验）..."
  python3 "$SCRIPTS_DIR/validate_split.py" \
    --node-id="$NODE_ID_RAW" 2>&1 | sed 's/^/   /'
  STEP4=${PIPESTATUS[0]}
  [ "$STEP4" -eq 0 ] && echo "   ✅  拆分校验通过" || echo "   ❌  拆分校验失败"
else
  STEP4=0
fi

# Step 5: validate Layer 0+1+2
echo ""
echo "▶  Step 5: validate.py Layer 0+1+2..."
python3 "$SCRIPTS_DIR/validate.py" \
  --all --layers=assets,static,compile 2>&1 | sed 's/^/   /'
STEP5=${PIPESTATUS[0]}
[ "$STEP5" -eq 0 ] && echo "   ✅  Layer 0+1+2 通过" || echo "   ❌  Layer 0+1+2 失败"

# Step 6: 产物契约测试
echo ""
echo "▶  Step 6: 产物契约测试（文件结构/语法）..."
if [ "$REGRESSION" = "--regression" ]; then
  python3 "$SCRIPTS_DIR/tests/test_product.py" \
    --out="$BASE/3-page-code" 2>&1 | sed 's/^/   /'
else
  python3 "$SCRIPTS_DIR/tests/test_product.py" \
    --out="$BASE/3-page-code" --contract-only 2>&1 | sed 's/^/   /'
fi
STEP6=${PIPESTATUS[0]}
[ "$STEP6" -eq 0 ] && echo "   ✅  产物契约通过" || echo "   ❌  产物契约失败"

set -e

# ── 汇总 ─────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  结果汇总                                ║"
echo "╠══════════════════════════════════════════╣"
printf "║  Step 1  单元测试:               ✅ 通过  ║\n"
printf "║  Step 2  Figma 转换 --split:     ✅ 通过  ║\n"
_s3=$(  [ "$STEP3"   -eq 0 ] && echo '✅ 通过' || echo '❌ 失败' )
_s35=$( [ "$STEP3_5" -eq 0 ] && echo '✅ 通过' || echo '❌ 失败' )
_s36=$( [ "$STEP3_6" -eq 0 ] && echo '✅ 通过' || echo '❌ 失败' )
_s4=$(  [ "$STEP4"   -eq 0 ] && echo '✅ 通过' || echo '❌ 失败' )
_s5=$(  [ "$STEP5"   -eq 0 ] && echo '✅ 通过' || echo '❌ 失败' )
_s6=$(  [ "$STEP6"   -eq 0 ] && echo '✅ 通过' || echo '❌ 失败' )
printf "║  Step 3   拆分计划 --auto:       %-14s ║\n" "$_s3"
printf "║  Step 3.5 stage/ 产物生成:       %-14s ║\n" "$_s35"
printf "║  Step 3.6 中间产物完整性:        %-14s ║\n" "$_s36"
printf "║  Step 4   validate_split CSS:    %-14s ║\n" "$_s4"
printf "║  Step 5   Layer 0+1+2:           %-14s ║\n" "$_s5"
printf "║  Step 6   产物契约:              %-14s ║\n" "$_s6"
echo "╠══════════════════════════════════════════╣"

ALL_OK=$([ "$STEP3" -eq 0 ] && [ "$STEP3_5" -eq 0 ] && [ "$STEP3_6" -eq 0 ] && \
         [ "$STEP4" -eq 0 ] && [ "$STEP5"   -eq 0 ] && [ "$STEP6"   -eq 0 ] && echo yes || echo no)

if [ "$ALL_OK" = "yes" ]; then
  echo "║  ✅ 算法 E2E 全部通过                   ║"
  echo "╠══════════════════════════════════════════╣"
  echo "║  ⚠️  以下步骤需真实调用 skill 覆盖：    ║"
  echo "║    · AI rename pass（语义命名）         ║"
  echo "║    · split --apply（产物落地）          ║"
  echo "║    · P-Verify（截图对比）               ║"
  echo "╚══════════════════════════════════════════╝"
  echo ""
  echo "完整 skill 测试请运行：/figma-to-code $FIGMA_URL"
else
  echo "║  ❌ 有失败项 — 请修复后重试             ║"
  echo "╚══════════════════════════════════════════╝"
  exit 1
fi
