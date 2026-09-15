---
name: figma-to-code
description: "Figma 设计稿转像素级 React + CSS Modules 代码。当用户消息同时包含 figma.com URL 和明确实现意图词（还原/实现/按设计稿开发/按这个做/convert to code/implement this design）时自动触发。仅提到 Figma、仅分享链接讨论设计、设计评审/对比/检查等场景不触发。"
version: 4.20.0
author: Frontend Team
tags:
  - figma
  - design-to-code
  - pixel-perfect
  - react
  - css-modules
user-invocable: true
command: /figma-to-code
argument-hint: "<Figma URL>"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Agent
---

<ACTIVATION-GATE>
此 skill 仅在以下情况之一时执行：

1. **显式调用**：消息中包含 `/figma-to-code`（用户手动输入或 workflow 程序化调用均走此路径）
2. **智能匹配**（仅限用户直接输入）：用户消息**同时满足**以下两个条件：
   - 包含 `figma.com` URL（完整的设计稿链接，不是提到 "Figma" 这个词）
   - 包含明确的实现意图词/短语（至少命中一个）：
     还原 / 实现 / 开发 / 按设计稿 / 按这个做 / 转代码 / 写代码 / 落地 /
     implement / convert to code / build this / code this up / design to code

> **Workflow 调用方注意**：从 TRD/PRD 提取 URL 等非用户直接输入场景，必须用 `/figma-to-code <URL>` 显式调用，不要依赖智能匹配。

**以下场景不得触发（即使包含 Figma URL）：**
- 仅分享链接讨论/评审设计（"看看这个设计稿"、"这个设计怎么样"）
- 设计稿对比/检查（figma-page-diff / ui-review 场景）
- 提取样式/颜色/token（非代码生成意图）
- 用户在描述 bug 或已有页面问题时附带了 Figma 链接作为参考

不满足以上任一条件 → 立即停止，不执行任何操作。
</ACTIVATION-GATE>

# Figma to Code v4

技术栈：React + CSS Modules（默认 `.module.less`），Python 3（stdlib，零额外依赖）。

## 参考文件（按需读取）

| 文件 | 何时读 |
|------|--------|
| `references/css-mapping.md` | Layer 1/2 报布局/渐变/描边映射错误；不确定某 Figma 属性的 CSS 对应值时 |
| `references/layout-inference.md` | 无 Auto Layout 需从坐标推断布局；`inferredFlex.confidence === 'low'` 时 |
| `references/ir-schema.md` | IR 字段缺失/格式错误；不确定某字段含义时 |
| `references/validation-guide.md` | 任意 Layer 报错；需要查偏差修改位置时 |
| `references/parallel-guide.md` | 有多个 Figma URL 需要并行转换时 |
| `references/error-guide.md` | 脚本报错或异常退出时 |
| `references/dev-change-guide.md` | **变更 scripts/ 逻辑时必读**：写单测 → 改代码 → 跑单测 → 通过 → 验证产物 |
| `references/semantic-split.md` | 命令包含 `--split` 时；P3 完成后用户选择拆分时 |
| `references/p-verify-guide.md` | P3 完成后用户选择启动 Dev Server 视觉验证时 |
| `references/rename-inline-scripts.md` | P1.5 步骤 3b/4 执行分批重命名和覆盖率验证时 |
| `references/dual-independent-templates.md` | 双稿独立模式（P1.4-Ind）生成入口文件和写入 `.figma-source.json` 时 |
| `references/context-api-reference.md` | Engine 2 执行时需查看 REST API 的 req/resp schema |
| `references/context-coding-guide.md` | Engine 2 首次执行时必读：4 条硬规则 + 使用原则 |

---

## P0 — 初始化

1. 从命令参数提取所有 Figma URL；读取 Figma token → `$FIGMA_TOKEN`（优先 `~/.claude/figma-token`，其次 `FIGMA_ACCESS_TOKEN`；均无则停止） ，若URL中含 focus-id 取它作为目标node-id，否则取 node-id。
2. 检测 `$TARGET_DIR`（当前目录向上最多 3 级找 `package.json` + `vite.config.ts`/`next.config.*`）；检测 `$CSS_EXT`；检测 `$SKILL_DIR`：
   ```bash
   CSS_EXT=$(python3 -c "
import json; p=json.load(open('$TARGET_DIR/package.json'))
a={**p.get('dependencies',{}),**p.get('devDependencies',{})}
print('scss' if 'sass' in a or 'node-sass' in a else 'less')
" 2>/dev/null || echo less)
   ```

   **`$SKILL_DIR` 检测（按优先级）：**

   **① 优先：使用 skill 系统提供的完整路径**（系统提示中含 `Base directory for this skill: <path>`）

   若上下文中可见该行，直接取 `<path>` 作为 `$SKILL_DIR`，无需搜索。

   **② 兜底：从 `~/.claude` 搜索安装目录，取最新版本**

   ```bash
   SKILL_DIR=$(find ~/.claude -name "SKILL.md" \
     -path "*/figma-to-code/SKILL.md" -not -path "*/history-versions/*" 2>/dev/null | \
     while read f; do
       d=$(dirname "$f")
       [ -d "$d/scripts" ] && echo "$d" && break
     done)
   ```

   **初始变量**（后续阶段可覆盖）：
   - `DEST_DIR=src/pages`（P1 步骤③扫描目录后可能覆盖为实际路径）
   - `NO_SPLIT=false`（若命令含 `--no-split` 或用户明确表示"单文件"/"不要拆分"/"快速原型"则设为 `true`；P1 调用 `convert.py` 时据此决定是否带 `--split` flag）

3. **先判断引擎和模式（见下方转换引擎判断 + 模式矩阵），再决定执行路径**：
   - **Engine 2 + Mode S** → 启动 figma-context server → P1-AI → P2-AI → P3 → stop server
   - **Engine 2 + Mode D** → 启动 figma-context server(PC) + server(H5) → P1-AI(双端) → P2-AI → P3 → stop
   - **Engine 1 + Mode D（默认独立）** → 主 Agent 直接执行 P1(双端) → P1.4-Ind → P1.5 → P2 → P3，**不走 parallel-guide**
   - **Engine 1 + Mode D + `--merge`** → 主 Agent 直接执行 P1 → P1.4-Merge → P1.5 → P2 → P3，**不走 parallel-guide**
   - **Engine 1 + Mode S，1 个 URL** → 主 Agent 直接执行 P1 → P1.5 → P2 → P3
   - **Engine 1 + Mode S，多个 URL** → 读取 `references/parallel-guide.md` 并行派发 subagent（每个独立执行 Mode S）

### 转换引擎判断（P0 完成后立即执行）

两个正交维度：**输入模式**（S/D/A，由 URL 数量决定）× **转换引擎**（Engine 1 脚本 / Engine 2 AI 轻量）。

**Engine 识别规则**（按优先级）：

| 触发条件 | Engine |
|---------|--------|
| 命令含 `--engine=script` | Engine 1（脚本转换） |
| 命令含 `--engine=ai` / `--light` / `--ai` | Engine 2（AI 轻量转换） |
| 用户消息含"轻量转换"/"轻量版本"/"灵活转换"/"灵活版本" | Engine 2 |
| 默认（无上述标识） | Engine 1 |

**完整模式矩阵**（P0 完成后立即判断）：

| 输入 | 引擎 | 后续流程 |
|------|------|---------|
| 1 个 URL | Engine 1 | P1 → P1.5 → P2 → P3（脚本转换） |
| 1 个 URL | Engine 2 | P1-AI → P2-AI → P3（AI 轻量转换） |
| 2 个 URL（或 1 URL + `--add-to`） | Engine 1 | P1(PC) + P1(H5) → P1.4-Ind → P1.5 → P2 → P3（各端独立） |
| 2 个 URL（或 1 URL + `--add-to`）+ `--merge` | Engine 1 | P1 → P1.4-Merge → P1.5 → P2 → P3（旧合并模式） |
| 2 个 URL（或 1 URL + `--add-to`） | Engine 2 | P1-AI(PC) + P1-AI(H5) → P2-AI → P3（双端 AI 轻量） |

> **Mode A（`--add-to`）属于 Dual 的子场景**：区别仅在于已有一端产物，只需转换新增端。独立模式下检测已有端并跳过其转换即可。

> **Engine 2 前置条件**：`$SKILL_DIR/scripts/context_start.py` 存在（已内置于 figma-to-code skill 中）。无需额外安装。

### 模式 D Engine 1 — 双稿独立转换（默认，无需 base 选择）

Mode D + Engine 1 **默认走独立转换**：PC 和 H5 各自执行完整转换流程，最终通过 JS 运行时切换显示。无需 base 选择决策。`--add-to` 场景下检测已有端，仅转换缺失端，入口文件保持/补全 isMobile 切换逻辑。

### 模式 D `--merge` — 响应式合并（旧流程，需 base 选择）

仅当命令含 `--merge` 时执行旧的合并流程（P1.4-Merge）。此时需额外执行 base 选择决策：

```
检查 TARGET_DIR/DEST_DIR/ 是否已存在产物目录

两端都是新的          → PC base，无需询问
PC 已存在，H5 是新的   → PC base（升级现有产物），无需询问
H5 已存在，PC 是新的   → 询问用户：
    "检测到 H5 产物已存在：{h5_code_path}
     [A] 以 H5 为 base（推荐，避免现有代码回归）
     [B] 以 PC 为 base（HTML 结构重建，H5 产物将被替换）"
两端都已存在（重新合并） → 询问用户选择 base
```

检测方法：
```bash
H5_CODE_EXISTS=$([ -d "$TARGET_DIR/$DEST_DIR" ] && \
  find "$TARGET_DIR/$DEST_DIR" -maxdepth 1 -name "${H5_NODE_ID_SAFE}-*" -type d | head -1)
H5_IR_EXISTS=$([ -f ".figma-to-code/2-figma-extract/${H5_NODE_ID_SAFE}.ir.json" ] && echo "yes" || echo "no")

if [ -n "$H5_CODE_EXISTS" ] && [ "$H5_IR_EXISTS" = "yes" ]; then
    # H5 已存在，询问 base 选择
fi
```

---

## Engine 2 — AI 轻量转换流程（`--light` / `--ai` 时执行）

> **Engine 2 跳过 P1/P1.5/P2**，替换为 P1-AI + P2-AI。P3 落地复用。
>
> 核心理念：**不生成代码，让 AI 自己看懂设计稿**。REST API 提供精确 CSS 数据，AI 结合项目上下文自主写代码。

### P0-AI — 环境检测 + 启动 context server

**① 项目环境检测**（与 Engine 1 P0 共用，但 Engine 2 自行执行）：

```bash
# 检测 CSS 扩展名
CSS_EXT=$(python3 -c "
import json; p=json.load(open('$TARGET_DIR/package.json'))
a={**p.get('dependencies',{}),**p.get('devDependencies',{})}
print('scss' if 'sass' in a or 'node-sass' in a else 'less')
" 2>/dev/null || echo less)

# 扫描目录结构
ls "$TARGET_DIR/src/" 2>/dev/null
find "$TARGET_DIR" -maxdepth 2 -type d \( -name "public" -o -name "assets" -o -name "static" \) 2>/dev/null

```

确定 `$DEST_DIR`（默认 `src/pages`）和 `$ASSETS_DIR`（默认 `public/assets`）。

**② 启动 context server**：

```bash
python3 $SKILL_DIR/scripts/context_start.py '<Figma URL>'
```

脚本输出 server URL、PID、rootNodeId。记录：`$FC_PORT`（默认 7181）、`$FC_ROOT_NODE_ID`。

缓存位于 `.figma-to-code/5-context-server/{fileKey}-{nodeIdSafe}/`（遵循 `.figma-to-code/` 的数字前缀规范，与 Engine 1 的 `1-raw-data/`、`2-figma-extract/`、`3-page-code/` 平级）。双稿模式下 PC 和 H5 各自独立子目录（nodeId 不同自然分离）。server 的 `.server.pid`、`.server.port`、`server.log` 也在 `5-context-server/` 根。

**双稿模式**：两端各启动一个 server（指定不同端口）：
```bash
FIGMA_CONTEXT_PORT=7181 python3 $SKILL_DIR/scripts/context_start.py '$PC_URL'
FIGMA_CONTEXT_PORT=7182 python3 $SKILL_DIR/scripts/context_start.py '$H5_URL'
```
记录 `$FC_PORT_PC=7181`、`$FC_PORT_H5=7182`。

**③ 预查 Code Connect**（与启动 server 并行执行）

在 P1-AI 开始前，提前获取整个 Frame 的 Code Connect 映射，P1-AI 遇到 `component-instance` 时直接查表，不再单独请求：

```
# 单稿
mcp__plugin_figma_figma__get_code_connect_map(
  fileKey="{figmaFileKey}",
  nodeId="{rootNodeId}",          ← 整个页面 Frame 的 nodeId
  codeConnectLabel="Web"
)
```

结果保存到内存变量 `$CC_MAP`。若调用失败或无结果，`$CC_MAP={}` 继续（P1-AI 降级为手动还原 component-instance）。

双稿模式下 PC 和 H5 各查一次，分别保存 `$CC_MAP_PC` 和 `$CC_MAP_H5`。

### P1-AI — AI 自主实现

**7 条硬规则**（必须遵守）：
1. **所有 CSS 值必须来自 API** — 不许目测截图取值
2. **装饰优先合图** — `likely-decorative` 节点用 `/screenshot` 拉 PNG 当背景，不用 CSS 还原
3. **内容优先 CSS** — 文字/布局/尺寸用 `/context` 返回的 css 字段表达
4. **代码风格由项目决定** — 读项目的 CLAUDE.md / package.json 确定技术栈
5. **重复区块先截图再判断** — 发现 N 个内容相同的元素（列表/卡片）时，必须先 `GET /screenshot/{sectionNodeId}` 截取该区块设计稿截图，确认设计稿本身如此后才继续；不得自行替换为编造内容
6. **绝对定位叠层用坐标算间距** — 当父容器使用 `position: absolute` 背景图、子内容需要 `padding-top` 定位时，必须查询子节点的 `bounds.y` 和父节点的 `bounds.y`，用差值 `子.bounds.y - 父.bounds.y` 计算 `padding-top`。禁止凭视觉目测或经验猜测——偏差超过 50px 会导致文字与图片严重叠压，P2-AI 才能发现时已浪费一轮修复
7. **命名语义化** — 生成的变量名、函数名、组件名、CSS 类名应使用语义化的功能描述，避免直接照抄设计稿中的品牌名或项目代号。例如 `HeroSection`（而非 `BrandXHeroSection`）、`logo`（而非 `brandLogo`）

**Step 1: 总览定位**
```bash
curl -sS http://127.0.0.1:$FC_PORT/overview | jq '.nodes[] | {id, name, type, bounds}'
```
从返回中定位目标模块的 nodeId。

**Step 2: 逐模块实现**

对每个目标模块：
```bash
# 拉模块上下文（布局 + CSS + 子节点分类）
curl -sS "http://127.0.0.1:$FC_PORT/context/$NODE_ID?depth=2" | jq
```

根据返回的 `children` 数组，按 `tags` 分类处理：
- `likely-decorative` → `GET /screenshot/{id}` 拉 PNG 存到 `$TARGET_DIR/$ASSETS_DIR/{ComponentName}/`，用 `background-image` 或 `<img>`
- `keep-text` → 从 `css` 字段取字号/颜色/字重，代码中用 CSS 表达
- `component-instance` → **必须先查 Code Connect，再决定是用项目组件库还是自行还原**：

  **Step 2a: 查 Code Connect**（在 P0-AI 阶段集中查一次，此处复用结果）
  ```
  mcp__plugin_figma_figma__get_code_connect_map(
    fileKey="{figmaFileKey}",
    nodeId="{rootFrameNodeId}",   ← 整个 Frame 的 nodeId，不是 instance 的 nodeId
    codeConnectLabel="Web"
  )
  ```
  > 此调用应在 P0-AI 末尾执行一次并缓存结果，P1-AI 遇到所有 component-instance 时复用，不要每个 instance 单独请求。

  **Step 2b: 匹配 Code Connect 结果**
  - **有匹配** → 按 Code Connect 提供的 snippet 直接使用对应组件，**不要自己写 CSS**，组件已内置 border-radius/padding/color
  - **无匹配** → `GET /context/{id}?depth=1` 深入查子组件结构，但要注意：
    - `border-radius` 等由主组件决定的属性，context API 返回的可能是局部 override（偏小或为 0），需结合视觉判断是否合理
    - 遇到明显异常值（如 button 的 `border-radius: 4px` 但视觉是 pill 形状），以 Figma 截图为准，通过 `GET /screenshot/{id}` 验证后手动修正

**代码直接写到目标位置**（不经过暂存区）：
- 单稿：`$TARGET_DIR/$DEST_DIR/{ComponentName}/`（ComponentName 从 Figma 根节点 `name` 推断）
- 双稿：`$TARGET_DIR/$DEST_DIR/{PageName}/pc/` 和 `.../h5/`（直接写入，无需事后 mv 重组）

**AI 自主决策**：
- 组件拆分粒度（section 级还是原子级）
- 文件命名（语义化，结合 Figma `name` 字段 + 项目约定）
- 技术栈（CSS Modules / Emotion / Tailwind，从 `$CSS_EXT` 和项目 package.json 推断）
- **样式变量 — Design Token 两种处理路径**（必须区分处理，不能混用）：

  **路径 A：API 返回 `var(--xxx)`**（设计师在 Figma 中绑定了 Variables）
  ```
  "css": { "color": "var(--color-text-primary)", "background": "var(--color-bg-100)" }
  ```
  → 直接照抄到 CSS，无需转换。这是最理想情况。

  **路径 B：API 返回原始 hex/rgba**（设计师使用了直接色值，未绑定 Variables）
  ```
  "css": { "color": "#121214", "background": "#f5f7fa" }
  ```
  → 对照项目自身的 design token 映射替换（若项目有 CSS 变量体系）；若项目无 token 体系，保留原始 hex 值。

  **格式规范**：使用 `var(--token-name, fallback)` 带 fallback 的格式，保证在 token CSS 未加载时页面不完全崩溃。

**Step 3: 数值复查**

写完代码后，对关键节点再 `GET /context/{id}` 复查 CSS 值与实现是否一致。发现差异立即修正。

**Step 3.5: 内容自查（写完所有代码后必须执行）**

```bash
# 检查是否有 placeholder 文字残留
grep -rn "lorem ipsum\|Lorem ipsum\|placeholder text" \
  "$TARGET_DIR/$DEST_DIR/{ComponentName}/" 2>/dev/null
```
有输出 → 回到 `GET /context/{nodeId}`，取对应节点的 `firstTexts` / `characters` 字段替换。

```bash
# 检查是否有重复内容数组（可能是设计意图，也可能是偷懒）
grep -n "Array.from\|\.fill(\|Array(" \
  "$TARGET_DIR/$DEST_DIR/{ComponentName}/index.tsx" 2>/dev/null
```
有输出 → 执行规则 5：`GET /screenshot/{sectionNodeId}` 截取该区块设计稿截图，Read 确认后才决定保留或修改。

**Step 4: 资源使用率核查**（代码写完后、进入 P2-AI 之前必须执行）

所有通过 `/screenshot` 下载的资源必须在代码中被引用。遗漏会导致设计稿里的关键图（如 hero 背景）在页面上消失，且 P2-AI 截图对比才能发现，此时修复成本已翻倍。

```bash
COMPONENT="{ComponentName}"
echo "=== 已下载资源 ==="
ls $TARGET_DIR/$ASSETS_DIR/$COMPONENT/ 2>/dev/null
echo "=== 代码引用 ==="
grep -roh "[^/\"']*\.\(png\|svg\|jpg\|webp\)" \
  "$TARGET_DIR/$DEST_DIR/$COMPONENT/" 2>/dev/null | sort -u
```

两列对比，有下载但未出现在代码中的文件 → 判断它是否是应使用的主图（如 `hero-bg.png`、`banner.png`）：
- **是**：找到对应节点的 bounds.y 坐标，按规则 6 写入正确的 CSS 引用
- **否**（确为临时/辅助资源）：记录跳过原因，继续

**双稿模式**：两端分别执行 Step 1-4（PC 用 `$FC_PORT_PC`，H5 用 `$FC_PORT_H5`）。

完成后生成平台切换入口（同 Engine 1 的 P1.4-Ind 产物结构）：
- `$TARGET_DIR/$DEST_DIR/{PageName}/index.tsx` — usePageEnv + isMobile 切换
- `$TARGET_DIR/$DEST_DIR/{PageName}/hooks/usePageEnv.ts`
- 模板见 `references/dual-independent-templates.md`

### P2-AI — 视觉比对修正

**前置：启动 dev server**（若项目尚未运行）：
```bash
PM=$([ -f "$TARGET_DIR/pnpm-lock.yaml" ] && echo pnpm || \
     [ -f "$TARGET_DIR/yarn.lock" ] && echo yarn || echo npm)
cd "$TARGET_DIR" && $PM run dev -- --port 5173 &
sleep 4
```

**Step 1: 截图对比**
- 拉设计稿截图：`curl -sS "http://127.0.0.1:$FC_PORT/screenshot/$FC_ROOT_NODE_ID" -o design.png`
- 拉实现截图：通过 chrome-devtools 导航到页面并截图（使用已有 Chrome 实例）

**Step 2: AI Vision 对比**（必须同时 Read 两张图，不得凭印象判断）

```
Read design.png     # 设计稿截图（Step 1 已下载）
Read browser.png    # 浏览器全页截图（Step 1 已拍摄）
```

逐区块输出结构化对比报告，每行一个区块：

| 区块 | 设计稿关键特征 | 实现状态 | 差异描述 | 级别 |
|------|--------------|---------|---------|------|
| Hero | ... | ✅/❌ | ... | P0/P1/P2/- |

级别定义：
- **P0**：布局错乱（容器宽度异常、内容溢出、大片空白）
- **P1**：区块可见偏差（颜色错误、文字内容不符、主要元素缺失）
- **P2**：细节偏差（间距轻微偏差、字重差异）可接受，记录即可

**Step 3: 修正循环**（最多 3 轮）
- 定位差异对应的 Figma 节点 → `GET /context/{nodeId}` 取精确值
- 修正代码
- 重新截图对比

P2-AI 通过标准：
- 无 P0（布局错乱）或 P1（区块偏差明显）级差异
- P2（细节偏差）可接受，记录在汇报中

> ⛔ **P2-AI 是不可跳过的硬卡口**：P3 落地只能在 P2-AI 通过后执行。
> 即使 agent 面临 token 超时压力，也应**优先完成 P2-AI 截图对比**，而不是先落地代码。
> 跳过视觉验证就落地 = 把"发现问题"的成本转移给用户，代价是当前修复的 3–5 倍。
> 若因超时确实无法完成 P2-AI，必须在汇报中注明 `"browserVerification": "skipped_timeout"`，不得写 `"status": "success"`。

### P3-AI — 落地（复用通用 P3）

Engine 2 的 P3 与 Engine 1 一致：
- 步骤零：design token CSS 引入检查（可选）
- 步骤一：代码落地确认
- 步骤一.五：写入 `.figma-source.json`（`"engine": "ai"` 标记）
- 汇报格式同通用 P3

**最后：停止 server**
```bash
# 停止所有 context server 实例
python3 $SKILL_DIR/scripts/context_stop.py

# 或停止指定实例（双稿模式下按需）
python3 $SKILL_DIR/scripts/context_stop.py '{fileKey}-{nodeIdSafe}'
```

### Engine 2 适用场景建议

| 适合 Engine 2 | 适合 Engine 1 |
|--------------|--------------|
| 快速原型 / MVP | 像素级精确还原 |
| 新技术栈项目（非 React + CSS Modules） | 大型复杂页面（50+ 节点） |
| 简单组件/卡片 | 需严格一致性的重复执行 |
| 探索性开发 | 需 validate.py 规则校验 |
| 不想等脚本处理 | 需组件拆分自动化 |

---

## P1 — 生成并定位（Engine 1）

> **双稿独立模式（P1.4-Ind）**：P1 对两端分别执行步骤①②（即两次 `convert.py` + 两次 Code Connect 查询），步骤③目录扫描只需执行一次（共享同一个 `$TARGET_DIR`）。两端的 P1 可并行。

**同一条消息发出三个并行调用：**

**① 运行转换脚本**（产物暂存到 `.figma-to-code/3-page-code/`）：
```bash
python3 $SKILL_DIR/scripts/convert.py '<Figma URL>' --css-ext=$CSS_EXT --split
```

**② 查询 Code Connect**（保存到 `2-figma-extract/{nodeId}.code-connect.json`，供 P1.5 使用）：
```
mcp__plugin_figma_figma__get_code_connect_map(
  fileKey="<从 URL 提取>",
  nodeId="<nodeId，冒号格式如 169:33787>",
  codeConnectLabel="Web"
)
```
成功后用 `lib/code_connect.py` 的 `parse_mcp_response` + `save_code_connect_json` 保存：
```python
from lib.code_connect import parse_mcp_response, save_code_connect_json
parsed = parse_mcp_response(mcp_result)
save_code_connect_json(parsed, Path(f'.figma-to-code/2-figma-extract/{NODE_ID_SAFE}.code-connect.json'))
```
失败/超时 → 跳过（P1.5 会打印警告，组件渲染为 div）。

**③ 扫描项目目录结构**（同时执行，供落地决策使用）：
```bash
ls "$TARGET_DIR/src/" 2>/dev/null
find "$TARGET_DIR" -maxdepth 2 -type d \( -name "public" -o -name "assets" -o -name "static" \) 2>/dev/null
```
Agent 据此确定两个落地目标：`$DEST_DIR`（代码，默认 `src/pages`）和 `$ASSETS_DIR`（图片，默认 `public/assets`）。

`.figma-to-code/` 下的数据目录：

| 目录 | 内容 |
|------|------|
| `1-assets/` | 下载的图片/SVG 资源（暂存，默认落地到 `public/assets/`） |
| `1-raw-data/` | Figma API 原始响应 |
| `1-design-token/` | design token CSS 缓存（`design-tokens.json`） |
| `2-figma-extract/` | IR 中间表示（`{nodeId}.ir.json`），CSS 提取后的结构化数据 |
| `3-page-code/` | 生成的单页文件（`{Name}-{nodeId}/`） |

重跑选项（无需重新拉 API）：

| 参数 | 跳过 | 适用场景 |
|------|------|---------|
| `--from-raw-data` | Figma API | 修了 `lib/css_extractor.py`，从原始数据重新提取 IR |
| `--from-ir` | API + IR 提取 | 修了 `tsx_generator.py`，直接从 IR 重新生成代码 |

---

## P1.4-Ind — 双稿独立转换（模式 D 默认，模式 S 跳过）

> **适用条件**：2 个 URL（或 `--add-to`）且**未**指定 `--merge`。

PC 和 H5 各自独立转换，最终通过 JS 运行时 isMobile 切换显示。无节点匹配、无 CSS merge。

**第一步：双端并行转换**

P1 步骤①对两端分别执行 `convert.py`（可并行）：
```bash
# PC 端
python3 $SKILL_DIR/scripts/convert.py '$PC_URL' --css-ext=$CSS_EXT --split

# H5 端
python3 $SKILL_DIR/scripts/convert.py '$H5_URL' --css-ext=$CSS_EXT --split
```

> `--add-to` 场景：已有端跳过转换，仅执行新增端。

**第二步：各端独立执行 P1.5 拆分**

两端各自执行完整 P1.5 流程（`--auto` → AI rename → `--apply-renames` → `--apply` → `validate_split.py`）。

`--apply --dest` 传与单稿相同的目标目录 `$TARGET_DIR/$DEST_DIR`。脚本会按各自 IR 的 figmaName 推断 `pageComponent` 并落地：
```bash
# PC 端落地
python3 $SKILL_DIR/scripts/split_components.py \
  --node-id=$PC_NODE_ID_SAFE --apply --dest=$TARGET_DIR/$DEST_DIR --css-ext=$CSS_EXT

# H5 端落地
python3 $SKILL_DIR/scripts/split_components.py \
  --node-id=$H5_NODE_ID_SAFE --apply --dest=$TARGET_DIR/$DEST_DIR --css-ext=$CSS_EXT
```

两端落地后，`$TARGET_DIR/$DEST_DIR` 下出现两个目录（如 `EuKycLanding/` 和 `EuKycLandingH5/`，名称取决于设计稿 figmaName）。

**第三步：目录重组 + 生成平台切换入口**

将两端产物重组为统一页面目录结构：

```bash
# 变量由 P1.5 落地结果确定
PC_COMPONENT_DIR="$TARGET_DIR/$DEST_DIR/$PC_PAGE_COMPONENT"   # 如 EuKycLanding
H5_COMPONENT_DIR="$TARGET_DIR/$DEST_DIR/$H5_PAGE_COMPONENT"   # 如 EuKycLandingH5
PAGE_DIR="$TARGET_DIR/$DEST_DIR/$PAGE_NAME"                    # 统一页面目录名（取 PC 端名称）

# 重组：PC 产物移入 pc/ 子目录，H5 产物移入 h5/ 子目录
mkdir -p "$PAGE_DIR"
mv "$PC_COMPONENT_DIR" "$PAGE_DIR/pc"
mv "$H5_COMPONENT_DIR" "$PAGE_DIR/h5"
```

> **`PAGE_NAME` 命名规则**：取 PC 端的 `pageComponent`（即 PC 端 figmaName 推断的组件名）作为统一页面目录名。若 PC 端落地目录即为 `$PAGE_NAME` 本身（重命名冲突），则先 `mv` 到临时名再重组。

最终产物结构：
```
$TARGET_DIR/$DEST_DIR/{PageName}/
├── index.tsx              ← 平台切换入口（下方生成）
├── hooks/
│   └── usePageEnv.ts      ← isMobile + theme hook
├── pc/                    ← PC 端完整产物
│   ├── index.tsx
│   ├── index.module.$CSS_EXT
│   ├── components/...
│   └── hooks/...          ← PC 端原有 hooks（保留，不与外层冲突）
└── h5/                    ← H5 端完整产物
    ├── index.tsx
    ├── index.module.$CSS_EXT
    ├── components/...
    └── hooks/...
```

按 `references/dual-independent-templates.md` 中的模板生成：
- `$PAGE_DIR/index.tsx` — 平台切换入口（`usePageEnv` + isMobile 判断渲染 PC/H5）
- `$PAGE_DIR/hooks/usePageEnv.ts` — 统一入口级 hook（isMobile 检测 + 主题注入）

> 外层入口 `usePageEnv()` **不传 theme**。各端子目录下的 hooks 保留不动，各自管理 theme。

**第四步：清理各端冗余 hooks**

两端各自 `pc/hooks/usePageEnv.ts` 和 `h5/hooks/usePageEnv.ts`（若存在）中的 `usePageEnv` 已被外层入口替代。但各端的 `index.tsx` 可能仍引用本地 `usePageEnv`——**不做删除**，让各端保持自治（各端 `index.tsx` 仍可独立运行）。外层入口不传 theme，由各端各自的 `usePageEnv` 调用注入各自的 theme。

**P1.4-Ind 完成后**：进入 P2 校验（两端分别校验）。

---

## P1.4-Merge — 响应式合并（仅 `--merge` 时执行）

> **适用条件**：命令含 `--merge` 的双稿场景。旧流程保留，通过节点匹配 + CSS diff 产出 `@media` 覆盖。

**第一步：执行合并**
```bash
python3 $SKILL_DIR/scripts/merge_responsive.py \
  --base-node-id=$BASE_NODE_ID_SAFE \
  --supplement-node-id=$SUPP_NODE_ID_SAFE \
  --base=$BASE_PLATFORM \
  --css-ext=$CSS_EXT \
  --breakpoint=768
```

**第二步：检查匹配报告**
```bash
cat .figma-to-code/2-figma-extract/merged-$BASE_NODE_ID_SAFE.match-report.json | \
  python3 -c "import json,sys; r=json.load(sys.stdin); print(f'auto:{len(r[\"auto\"])} pending:{len(r.get(\"pending_confirm\",[]))}')"
```

若 `pending_confirm` 非空 → 逐条向用户展示，每条询问：
```
[A] 确认此对匹配
[B] 跳过（两端各标为独有节点）
[C] 手动指定配对（输入 PC section 序号）
[D] 确认本条及所有剩余
```

收集用户答案后写入 `merged-{BASE_NODE_ID_SAFE}.match-confirmed.json`（即 `.figma-to-code/2-figma-extract/merged-$BASE_NODE_ID_SAFE.match-confirmed.json`），然后：
```bash
python3 $SKILL_DIR/scripts/merge_responsive.py \
  --base-node-id=$BASE_NODE_ID_SAFE \
  --supplement-node-id=$SUPP_NODE_ID_SAFE \
  --base=$BASE_PLATFORM \
  --css-ext=$CSS_EXT \
  --resume
```

**P1.4-Merge 完成后**：以 `merged-$BASE_NODE_ID_SAFE` 替换 `$NODE_ID`，执行完整的 **P1.5 流程**（见 P1.5 章节，包含 `--auto` → AI rename pass → `--apply-renames` → `--apply` → `validate_split.py` 五步）。

---

## P1.5 — 组件拆分（默认执行）

P1 完成后，默认执行语义化组件拆分（消除重复渲染，CC 组件精确映射）。

**整页 vs 局部判断（调用脚本前必须确定）：**

判断权在 agent 层，依据**用户意图**而非节点尺寸：

| 用户表达 | 模式 | `--dest` 传值 |
|---------|------|--------------|
| "转这个页面"、"这个设计稿还原"、给的链接是完整 Landing Page / 活动页 | 整页 | `$TARGET_DIR/$DEST_DIR`（如 `src/pages/demo`） |
| "把这个组件抽出来"、"这个区块放到 XX 目录" | 局部 | **先问用户确认路径**，再传确认值 |
| 无法判断（用户只给了链接没说意图） | **问用户** | — |

- **整页**：`--auto` 只生成 plan（暂存区），不落地；落地由 AI rename pass 后的 `--apply` 完成
- **局部组件**：必须先确认目标路径，`--apply` 时传入确认路径

```bash
# --auto 只做分析：生成 plan.json + plan.naming.json（不落地，不需要 --dest）
python3 $SKILL_DIR/scripts/split_components.py --node-id=$NODE_ID --auto \
  --css-ext=$CSS_EXT
```

**跳过条件**：`NO_SPLIT=true` 时跳过（已在 P0 根据用户意图和 `--no-split` flag 判断；P1 调用 `convert.py` 时已据此决定是否带 `--split`）。

#### AI 重命名 pass（P1.5 完成后必须执行）

`--auto` 生成的 `plan.naming.json` 包含所有仍为 generic 名（`Frame*`、`Section{N}` 等）的条目，**必须消费它，否则产物含无语义名称**。

**步骤：**

1. 读取 `.figma-to-code/3-page-code/*-$NODE_ID_SAFE/split-a/plan.naming.json`（新格式 `{Name}-{nodeId}`；旧格式为 `$NODE_ID_SAFE-*`）
2. 若 `sections`、`subSections`、`leaves`、`props`、`cssClasses` 均为空数组 → 跳过（名称已全部语义化）
3. 若非空，**分两步处理**：

**3a. 非 cssClasses 字段**（`sections`/`subSections`/`leaves`/`props`，通常条目少）：根据每项的 `figmaName`、`firstTexts`、`leafNames`/`varyingPropNames`/`sampleValues` 推断语义名称，写入 `renames.json`。命名规则：PascalCase，语义化描述功能，避免直接照抄品牌名（如 `BrandHeroSection` 应改为 `HeroSection`）：

```json
{
  "page": "SemanticName",
  "sections": { "OldSection": "NewSection" },
  "subSections": { "Section1": "NewSubSection" },
  "leaves": { "Frame123": "SemanticCard" },
  "props": { "LeafName.text0": "semanticProp" },
  "cssClasses": {}
}
```

> **键名说明**：`renames.json` 用 `cssClasses` 传入映射；脚本写入 `plan.json` 时改名为 `cssClassRenames`；`--apply` 时按 `plan.json.cssClassRenames` 做替换。三者不同，不要混用。

> **subSections 冲突规则**：若 `plan.naming.json` 中同一名称（如 `Section1`）出现在多个不同父 Section 下，该条目**不得写入 `renames.json`**——脚本全局匹配，会把所有同名 subSection 改成同一个名称，造成语义错误。

**3b. cssClasses 字段（分批处理，每批 ≤ 50 条）**：

> 脚本详见 `references/rename-inline-scripts.md`。

先运行 **CHUNK_SPLIT** 脚本（见 reference），把 `frame-*/node-*` 条目按 50 条一批写成 chunk 文件。

然后**逐批**读取 chunk 文件并推断语义名称（每批单独完成再处理下一批），将结果合并追加到 `renames.json` 的 `cssClasses` 字段。

**`cssClasses` 命名规则**（每批处理时遵守）：

- 命名优先级：`firstTexts`（最直接）> `figmaName`（非 "Frame 数字" 时）> `css` 属性推断布局角色
- `css` 布局推断：`flex-direction:row` → bar/strip/row/header；`flex-direction:column` → panel/card/stack/col；`width:100%` → full-width 前缀
- `node-I*` 且 `figmaName="路径"` → 统一命名为 `icon-svg-path`（SVG 内部路径节点，全局唯一）
- kebab-case，全小写，2-4 个词，不超过 30 字符，不含 "frame"/"node" 等技术词汇，避免品牌名（如 `brand-logo` 应改为 `logo`，`brand-hero` 改为 `hero`）
- 每个 `currentClass` 只写一条映射（脚本全局替换，同名类会同时被替换）

每批处理完后运行 **MERGE_BATCH** 脚本（见 reference），将该批的 `{ "frame-xxx": "semantic-name", ... }` 映射赋值给环境变量 `BATCH_MAP`，合并入 `renames.json`。所有批次处理完毕后再执行步骤 4。

4. **覆盖率验证（必须通过，退出码非 0 则补写 `renames.json` 再重跑，禁止跳过）**：

运行 **COVERAGE_CHECK** 脚本（见 `references/rename-inline-scripts.md`）。退出码 1 → 回到步骤 3，补写缺失条目后重跑，直到全部 `✓`。

5. 执行重命名并重新落地：

```bash
# 更新 plan.json
python3 $SKILL_DIR/scripts/split_components.py \
  --node-id=$NODE_ID_SAFE --apply-renames=renames.json

# 用新名称重新生成文件
python3 $SKILL_DIR/scripts/split_components.py \
  --node-id=$NODE_ID_SAFE --apply --dest=$TARGET_DIR/$DEST_DIR --css-ext=$CSS_EXT

# 重新验证
python3 $SKILL_DIR/scripts/validate_split.py --node-id=$NODE_ID_SAFE --css-ext=$CSS_EXT
```

6. `--auto` 不落地，因此不会产生旧名目录；`--apply` 是唯一落地时机，执行一次产生正确名称目录。

---

拆分后的产物结构（整页模式）：
```
3-page-code/{Name}-{nodeId}/split-a/stage/   ← 暂存（结构与 dest/ 完全一致）
src/pages/{Name}/                             ← dest 落地（--apply --dest 后）
├── index.tsx                  → 页面组件入口
├── {Name}.module.less
├── {Name}.texts.ts
├── {Name}.texts.defaults.ts
├── hooks/
│   └── usePageEnv.ts
├── components/
│   ├── Section1/
│   │   ├── index.tsx
│   │   ├── index.module.less
│   │   └── ButtonComp.tsx     → CC snippet 直出
│   └── Section2/
│       └── ...
└── common/                    → 顶层叶子（跨 Section 共用）
```

#### Code Connect 数据（P1 步骤②已获取）

`split_components.py --analyze` 从 `.figma-to-code/2-figma-extract/{nodeId}.code-connect.json` 加载 Code Connect 映射。
若文件缺失，脚本会打印 `⚠️ Code Connect 数据缺失` 警告——此时应回到 P1 步骤②补查。

Code Connect 命中的组件（confidence=1.0）自动替换为对应组件库组件，无需用户确认。
未命中的组件 → fallback 到 MCP `search_components` 搜索（需用户确认）。

---

## P2 — 结果校验

**单稿模式（Mode S）/ 合并模式（--merge）**：
```bash
python3 $SKILL_DIR/scripts/validate.py '<URL>' --css-ext=$CSS_EXT
```

**双稿独立模式（P1.4-Ind）**：两端分别校验，各自评分 ≥ 90：
```bash
# PC 端校验
python3 $SKILL_DIR/scripts/validate.py '$PC_URL' --css-ext=$CSS_EXT
# H5 端校验
python3 $SKILL_DIR/scripts/validate.py '$H5_URL' --css-ext=$CSS_EXT
```
> 两端可并行校验。任一端不通过则修复该端后仅重跑该端的 P1 → P1.5，不影响另一端。

四层校验通过，综合评分 ≥ 90（Layer 3/4 暂不启用，需要时追加 `--layers=assets,static,compile,computed,visual`）：

| 层 | 内容 |
|----|------|
| Layer 0 | 资源落地（/assets/ 引用是否存在，自动修复） |
| Layer 1 | 静态规则（Sass 变量、flex/position 合法性） |
| Layer 2 | 编译检查（sass/less 语法、无 NaN/undefined 值） |

校验不通过时，修复后**仅重跑 P1**；仅当修改了脚本源代码时才需运行对应的全量测试：

| 场景 | 命令 | 说明 |
|------|------|------|
| 产物出问题（转换后验收） | `bash $SKILL_DIR/scripts/run_cross_test.sh '<URL>'` | 契约测试 + Layer 0+1+2 |
| 改了 `lib/` 或 `convert.py` | `python3 $SKILL_DIR/scripts/tests/test_all.py` | 算法单元测试全量，确保算法层未退化 |

> **改了脚本源代码必须在对应的测试模块中补充单元测试（见 `references/dev-change-guide.md`），运行 `test_all.py` 全部通过后再提交。**

偏差对应修改位置见 `references/validation-guide.md`。

校验通过（评分 ≥ 90）后，执行 P3 落地。

---

## P3 — 落地 & 验证（必须完整执行，不可跳过）

> ℹ️ **资源落地已由 validate.py Layer 0 自动处理**：P2 校验脚本会检测 `.figma-to-code/1-assets/` 中的暂存资源，若 `public/assets/` 目标目录不存在则自动 `shutil.copytree` 复制（**保留暂存区原件**，便于后续对比）；无法找到源文件时脚本报 error 扣分，强制触发修复流程。
>
> 因此 P3 的资源落地**无需手动执行**，只需落地代码目录。

**步骤零：design token CSS 引入检查**（可选）

如项目使用 design token CSS 变量（通过 `DESIGN_TOKEN_CSS_URL` 配置），确认已在项目入口引入。若项目已有自己的 token CSS 加载方式，跳过此步骤。

**步骤一：代码落地确认**

**单稿 / 合并模式**：
```bash
ls $TARGET_DIR/$DEST_DIR/{ComponentName}/ && echo "✓ 落地确认" || echo "⚠️ 目录不存在，请检查 P1.5 --apply 是否成功"
```

**双稿独立模式（P1.4-Ind）**：
```bash
# 验证重组后的目录结构
ls $TARGET_DIR/$DEST_DIR/{PageName}/index.tsx && echo "✓ 入口确认"
ls $TARGET_DIR/$DEST_DIR/{PageName}/pc/index.tsx && echo "✓ PC 端确认"
ls $TARGET_DIR/$DEST_DIR/{PageName}/h5/index.tsx && echo "✓ H5 端确认"
```

**步骤一.五：写入 `.figma-source.json`**

按当前模式选择对应的写入逻辑（详见 `references/dual-independent-templates.md`）：
- **单稿**：写入 `{COMPONENT_NAME}/.figma-source.json`，key 为 `"single"`
- **双稿合并（--merge）**：写入 `{COMPONENT_NAME}/.figma-source.json`，key 为 `"pc"` + `"h5"`
- **双稿独立（P1.4-Ind）**：写入 `{PAGE_NAME}/.figma-source.json`，含 `"mode": "independent"` + `"pc"` + `"h5"`

> ⚠️ 公开仓库建议将 `.figma-source.json` 加入 `.gitignore`。

**步骤二：资源缺失补充流程**（仅当 P2 Layer 0 报 `✗ 缺失且无法修复` 时执行；P2 评分 ≥ 90 表明资源已就绪，直接进入汇报）

**资源缺失三阶段处理流程**（Layer 0 报 `✗ 缺失且无法修复` 时执行）：

**阶段一：自动重试下载**
对所有报缺失的资源，重新调用 Figma Images API 强制重新下载一次：
```bash
# 重跑 convert.py，跳过 IR 提取，仅重新下载资源（独立模式下对报错端执行）
python3 $SKILL_DIR/scripts/convert.py '<URL>' --css-ext=$CSS_EXT --from-ir
# 然后重跑 validate.py 检查是否已补全
python3 $SKILL_DIR/scripts/validate.py '<URL>' --css-ext=$CSS_EXT --layers=assets
```
- 若此次通过 → 进入汇报
- 若仍有缺失 → 进入阶段二

**阶段二：识别 Figma 不可访问资源**
从 Layer 0 输出中提取仍缺失的文件名（格式：`I{nodeId}-{suffix}.svg/png`）。这些文件在 Figma 端不可访问（已删除或权限受限），无法自动修复。

**阶段三：提示用户手动补充**
向用户明确报告：
```
⚠️ 以下资源在 Figma 中不可访问，已无法自动下载，请手动补充后继续：
  · public/assets/{ComponentName}/I9986-31251-9248-19735.svg  ← 示例格式
  · public/assets/{ComponentName}/I9986-31251-9248-19745.svg

请将对应图片放入上述路径，完成后回复"已补充"，将自动重新验证。
```
用户回复"已补充"后，重跑 validate.py `--layers=assets` 确认，通过后进入汇报。

**汇报格式**（subagent 必须包含 `assetsPath` 和 `missingAssets` 字段）：

**单稿 / 合并模式**：
```json
{
  "componentName": "...",
  "outputPath": "src/pages/{ComponentName}/",
  "assetsPath": "public/assets/{ComponentName}/ (N files)",
  "missingAssets": [],
  "pageTheme": "light",
  "score": 99,
  "status": "success"
}
```

**双稿独立模式**：
```json
{
  "componentName": "...",
  "mode": "independent",
  "outputPath": "src/pages/{PageName}/",
  "outputStructure": {
    "entry": "src/pages/{PageName}/index.tsx",
    "pc": "src/pages/{PageName}/pc/",
    "h5": "src/pages/{PageName}/h5/"
  },
  "assetsPath": {
    "pc": "public/assets/{PcComponentName}/ (N files)",
    "h5": "public/assets/{H5ComponentName}/ (N files)"
  },
  "missingAssets": [],
  "pageTheme": { "pc": "light", "h5": "dark" },
  "score": { "pc": 99, "h5": 97 },
  "status": "success"
}
```

- 若无资源，`assetsPath` 写 `"none"`（独立模式下对应端写 `"none"`）
- 若存在 Figma 不可访问资源（阶段三），`missingAssets` 列出文件名数组，`status` 写 `"partial"` 而非 `"success"`
- `pageTheme`：写入 `<html>` 的实际主题值（`"light"` / `"dark"`）；若项目已有则填已有值；无法注入则写 `"manual"`；独立模式下分端记录
- `score`：独立模式下分端记录，任一端 < 90 即视为不通过
- 以上字段均不得省略

**P3 完成后**：

> **是否启动 dev server 做视觉验证？**（新项目/首次还原强烈推荐）
> - **是** → 执行 P-Verify（见下方阶段）
> - **跳过** → 流程结束

---

## P-Verify — Dev Server 视觉验证（可选，用户选择后执行）

> **仅当用户在 P3 末尾的询问中回答「是」时，才读取 `references/p-verify-guide.md` 并完整执行。** 跳过则流程结束。
>
> 完整检查清单（7 步）和汇报格式见 `references/p-verify-guide.md`。

---

<NEVER>
- 手写或推断任何 CSS 数值，或绕过 `convert.py` 直接生成 CSS——所有值必须来自脚本提取的 IR
- 跳过 Layer 0/1/2 校验就声称组件完成
- 修改 `lib/` 或 `convert.py` 后不在对应测试模块（`tests/extract/`、`tests/transform/`）补单元测试就提交
- 发明 mock 数据——文本内容必须来自 Figma `characters` 字段
- 综合评分 < 90 必须修复；3 轮仍不达标时停止并向用户报告，不得声称通过
- 跳过 validate.py Layer 0（`--layers` 中去掉 `assets`）——资源落地的自动检测和修复依赖 Layer 0，绕过后图片 404 无法被发现
- subagent 汇报 JSON 缺少 `assetsPath`、`missingAssets`、`pageTheme` 三个字段中的任何一个——这三个字段是确认资源状态和主题注入的唯一证据
- Layer 0 报缺失时直接跳到提示用户手动补充——必须先执行阶段一自动重试（`--from-ir` 重下载），确认 Figma 端确实不可访问后才能进入阶段三
- 有不可访问资源时将 `status` 写成 `"success"`——应写 `"partial"`，否则调用方无法感知产物不完整
- 将 `data-theme` 静态写入 `index.html` 或共用入口文件——`index.html` 是所有页面共享的模板，硬编码会污染其他路由；主题应由 `usePageEnv` 在页面 mount 时动态注入、unmount 时恢复
- P-Verify 执行时跳过任何检查步骤——步骤①全局 CSS 检查（跳过会导致模板污染截图结论）、步骤⑤视口对齐（1440px 设计稿在 800px 视口下截图无效）、步骤⑦双截图对比（单张截图无法做并排差异分级）三步缺一不可
- P1.5 步骤 4 覆盖率验证脚本退出码非 0 时继续执行 `--apply-renames`——覆盖率不足 100% 意味着产物仍含 `frame-*/node-*` 原生 ID 类名，必须补全 `renames.json` 后重跑验证脚本直到全部 `✓`
- P1.5 步骤 3b 中跳过任意一个 chunk 文件直接进入步骤 4——每个 `naming-chunk-N-of-M.json` 都必须处理完并合并进 `renames.json` 后才能运行覆盖率验证；批次进度必须在消息中逐批汇报（"批次 N/M 完成，累计 X 条"）
- 双稿独立模式（P1.4-Ind）下跳过第三步目录重组——两端 `--apply` 落地后必须执行 `mv` 重组到 `pc/`、`h5/` 子目录并生成入口 `index.tsx`，否则两端产物各自散落在 `$DEST_DIR` 下无法通过统一入口切换
- **从 stage 目录直接复制文件到 P1.4-Ind 的 `pc/` 或 `h5/` 目标目录**——stage 是 `--apply` 的中间产物，资源路径尚未重命名（保留节点 ID 格式如 `/assets/H5-39603-19204/`）；直接复制绕过路径重命名步骤，导致所有 `<img src>` 路径 404。重建落地必须走 `--apply --exact-dest=$TARGET_DIR/$DEST_DIR/{PageName}/pc（或 /h5）` 流程（见 `dev-change-guide.md` 步骤 5 P1.4-Ind 变体）
- 双稿独立模式下 P-Verify 只验证一端——必须两轮（PC 视口 + H5 视口）都完成截图对比后才能汇报，缺任何一端等于只验证了一半
- 双稿独立模式下在外层入口 `index.tsx` 的 `usePageEnv` 中传入 theme 参数——外层入口不负责 theme，各端 `pc/index.tsx` 和 `h5/index.tsx` 各自通过自己的 `usePageEnv` 管理各自的 theme
- Engine 2 中目测截图取 CSS 值——所有 color/fontSize/gap/padding/borderRadius 必须来自 `GET /context/{nodeId}` 返回的 `css` 或 `inferredFlex` 字段，不许从截图"看着像 18px"推断
- Engine 2 中用 CSS 还原 `likely-decorative` 节点——装饰层（渐变叠加、纹理、光效）应拉 `/screenshot` 存 PNG 用 `background-image`，不要逐层手写 CSS
- 产物代码（变量名/函数名/组件名/CSS 类名/注释）中出现品牌名——应使用语义化功能描述命名；Engine 1 在 AI rename pass 中处理，Engine 2 遵守规则 7；Figma 设计稿文本内容（JSX 文本节点）不在此限
- Engine 2 中直接硬编码 hex/rgba 颜色值（`#rrggbb`、`rgb()`）——若项目有 design token，对照项目 token 映射替换；不转换的颜色在 light/dark 主题切换时可能异常
- Engine 2 中对 `component-instance` 节点直接取 context API 的 `border-radius` 写进 CSS——component-instance 的 border-radius 由主组件定义，API 返回的往往是局部覆盖值（如 `4px`）而非实际渲染值（如 `24px` pill 形状）；必须先查 Code Connect 识别对应组件，使用组件自带的正确样式，而不是从 API 读取不完整的 CSS 片段
- Engine 2 中跳过 Code Connect 查询直接给 component-instance 生成 `<div>` 或裸 `<button>`——component-instance 节点高度可能对应项目组件库中的 Button/Tag/Badge 等组件；不查 Code Connect 就自行生成 CSS 会产生错误的 border-radius、padding、颜色
- Engine 2 中 `position:absolute` 背景图叠文字时凭感觉写 `padding-top`——必须用规则 6：查询 `子节点 bounds.y - 父节点 bounds.y`，用差值写入 `padding-top`；感觉填的值通常偏差 100px+ 以上，P2-AI 才能发现时已浪费整轮修复
- Engine 2 中跳过 Step 4 资源核查直接进 P2-AI——下载了但未引用的资源（如主 hero 背景图）只有在 P2-AI 视觉对比才能发现，此时补救成本是 Step 4 主动核查的数倍；核查只需 30 秒，不是瓶颈
- Engine 2 中 P2-AI 未完成就执行 P3 落地——P2-AI 是唯一视觉兜底检查；超时情形下也应先完成 P2-AI，实在无法完成则在汇报中标注 `"browserVerification": "skipped_timeout"` 而非 `"status": "success"`
- Engine 2 中忘记停止 figma-context server——P3 完成后必须执行 `stop.py`，否则端口持续占用
- Engine 2 修正超过 3 轮仍有 P0/P1 级差异时继续循环——停止并向用户报告，建议切换 Engine 1 获得精确还原
- Engine 2 P2-AI 截图对比时只截浏览器截图、未同时 Read 设计稿截图就声称通过——必须同时 Read `design.png`（`GET /screenshot/{rootNodeId}`）和 `browser.png` 两张图，才能做有效对比
- Engine 2 发现 N 张内容相同的卡片/列表项后，未截图确认设计稿就将其替换为编造的不同内容——相同内容 × N 是合法的 UI 占位设计意图，必须先 `GET /screenshot/{sectionNodeId}` 确认设计稿，再决定是否修改
- **暗色 app shell 项目**：Engine 2 生成 LP 类独立全屏页面时，若项目默认 dark theme 且 LP 页嵌入其中，需在组件顶层添加 `useEffect` 覆盖 `data-theme` 为 `light`，并在 unmount 时恢复；light theme 项目跳过本条
</NEVER>
