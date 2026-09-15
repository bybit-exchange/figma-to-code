# 语义化组件拆分（P1.5）

此文件仅在 `--split` flag 触发或 P3 后用户选择拆分时读取。

## 触发方式与模式

| 命令 | 执行路径 |
|------|---------|
| `--split`（默认） | **Path A**（脚本检测 + Claude 语义决策，当前实现） |
| `--split --mode=b` | Path B（Agent 整体推理，尚未实现） |
| `--split --mode=ab` | AB 对比（调试用，两路都跑） |

---

## 前置检测

```bash
ls .figma-to-code/2-figma-extract/{nodeId}.ir.json \
   .figma-to-code/3-page-code/ 2>&1
```

- ir.json 和 3-page-code/ 均存在 → 跳过 P1，直接进 P1.5
- 任一不存在 → 先执行 P1

## 溯源（从已落地页面触发时）

> `src/pages/` 落地目录**不含 nodeId**（如 `src/pages/MyLandingPage`）。
> `3-page-code/` 暂存目录使用 `{Name}-{nodeId}` 格式（如 `MyLandingPage-169-33787`）。

用户给出页面路径（如 `src/pages/MyLandingPage`）时：
1. 读取 `src/pages/MyLandingPage/.figma-source.json` → 取 `nodeId` 字段
2. 若 `.figma-source.json` 不存在，搜索 `.figma-to-code/3-page-code/MyLandingPage-*/split-a/plan.json` → 取 `nodeId`
3. 检测 `.figma-to-code/2-figma-extract/{nodeId}.ir.json`
4. 有缓存 → 完整 P1.5；无缓存 → 降级 P1.5（Agent 直读 TSX，跳过 CSS 校验）

---

## Path B（默认）— Claude Agent 整体推理

> ⚠️ **Path B 尚未在脚本层实现**（`split-b/` 目录仍标注"待实现"）。
> 当前 SKILL.md P1.5 使用的是 **Path A**（`split_components.py --auto` / `--apply`）。
> 本节内容为设计规格，供将来实现参考；实际执行请遵循 SKILL.md P1.5 流程。

### 输入准备

```bash
# 1. 生成 semantic.json（若不存在）
python3 $SKILL_DIR/scripts/semantic_context.py '<URL>'

# 2. 读取以下文件作为上下文
cat .figma-to-code/3-page-code/{Name}-{nodeId}/{Name}.tsx   # 视觉结构上下文
cat .figma-to-code/2-figma-extract/{nodeId}.ir.json           # CSS ground truth（只读）
cat .figma-to-code/2-figma-extract/{nodeId}.semantic.json     # 语义辅助信息
```

### 组件边界识别规则

**应该拆**：
- `figmaType === COMPONENT`（Figma 原生组件标记）
- `componentId` 在 semantic.json 中出现 3+ 次（重复实例）
- 同一 `figmaName` 出现 3+ 次
- 视觉上完整的功能单元（英雄区/导航栏/商品卡片/底部栏）
- 有明确 Code Connect 匹配

**不应该拆**：
- 纯布局容器（只有 flexbox，无视觉身份）
- `isDecorativeElement: true`
- 单个文本节点 / 图标
- CSS 极简的包装节点

### AI 语义决策规则

**htmlTag**（优先级从高到低）：

```
semantic.json interactiveNodes[id] === 'click'  → button
figmaName 含 btn/button/cta（不区分大小写）      → button
figmaName 含 nav/navbar                         → nav
figmaName 含 header                             → header
figmaName 含 footer                             → footer
figmaName 含 link/anchor                        → a
isTextNode + fontSize >= 24px                   → h2 / h3（按层级）
isTextNode + 多行内容                            → p
isTextNode + 短标签                              → span
isImageNode                                     → img
其余                                             → div
```

**className**：
- kebab-case，语义名（`product-card`、`hero-section`）
- 禁止 `frame-N`、`group-N` 等结构名
- 优先用 `description` > `componentProperties` 变体名 > figmaName 语义化

**props 识别**：
- `componentPropertyReferences.characters` 有值 → text prop
- isImageNode → `imageSrc: string` + `imageAlt?: string`
- htmlTag=button → `onClick?: () => void`
- htmlTag=a → `href: string`
- 静态 UI 文案 → 不加 props

### 组件库匹配（Code Connect First）

**Step 0 — Code Connect 查询**（P1.5 开始时执行一次）：

```
调用: mcp__plugin_figma_figma__get_code_connect_map
参数:
  fileKey: <从 URL 提取>
  nodeId: <从 URL 提取>
  codeConnectLabel: "Web"

输出: .figma-to-code/2-figma-extract/{nodeId}.code-connect.json
```

调用失败/超时 → 跳过 Code Connect，完全 fallback 到 MCP 搜索。
调用成功 → 保存结果，后续 `split_components.py --analyze` 自动读取。

**置信度分流**：

| 来源 | 置信度 | 处理 |
|------|--------|------|
| Code Connect | 1.0 | 自动替换，无需确认 |
| MCP search ≥ 0.85 | 0.85-0.99 | 展示方案，等用户确认 |
| MCP search < 0.85 | < 0.85 | 保留 P3 原始代码 |
| 查询失败 | 0 | 保留 P3 原始代码 |

**Code Connect 命中时的 Props 推断**：

引擎基于数据驱动，不含组件特定逻辑。以 Code Connect snippet 为结构参考，从以下数据源推断实际值：

1. **snippet 解析**：确定组件接受哪些 props 及其类型（string/number/boolean/array）
2. **componentProperties BOOLEAN** → boolean props（value=true 输出，false 省略）
3. **componentProperties VARIANT** → enum/size/boolean props（Yes/No→bool，size keywords→size prop，ordinal→number）
4. **TEXT 子节点 characters** → children / label / 数据 props（模式匹配数字范围/普通文本）
5. **exposedInstances** → icon / slot props（递归查 Code Connect map）

Props 推断无法确定值时 → 使用 snippet 默认值 + `/* TODO: verify */` 注释。

**MCP 搜索 Fallback**（仅对 Code Connect 未覆盖的组件）：

1. 先检查项目 `package.json` 是否已有组件库依赖（通过 Code Connect 配置判断）
2. 如果 Code Connect 无匹配，询问用户是否使用项目的组件库手动替换
3. 有 Code Connect MCP 工具时可用 `search_components(query: figmaName + className)`，取前 3 候选
4. 对比 props 接口覆盖度
5. 置信度 ≥ 0.85 展示方案待确认；< 0.85 保留生成代码

**组件替换时的 CSS 处理**：
- 丢弃组件自身视觉 CSS（颜色/圆角/阴影）— 组件库有自己的样式系统
- 保留父容器布局定位 CSS（position、flex 子项、margin）

### 生成产物

生成到暂存区：`.figma-to-code/3-page-code/{Name}-{nodeId}/split-b/`

```
components/
  {ComponentA}/
    {ComponentA}.tsx
    {ComponentA}.module.{ext}
    index.ts
  {ComponentB}/...
  index.ts     ← 桶文件：export { default as X } from './X'
  Page.tsx     ← 组装文件：import 所有组件 + render
```

**TSX 代码生成硬约束**（违反任何一条均会被 validate_split.py 拦截）：

每个组件 `{Name}.tsx` 必须：
- `export default function {Name}()` — **禁止 named export**（`export function X()` 是错的）
- `import React from 'react'` — 必须显式引入
- CSS import 只能是 `import styles from './{Name}.module.{ext}'` — **禁止 `../../` 或跨目录路径**
- **不得 import 父目录的任何文件**（原单文件组件、原 CSS module、原 index）
- 除通过 Code Connect 引入的组件库外，不得 import 其他非 npm 的外部路径

`Page.tsx` 必须：
- 只 import 各子组件（`import X from './X'`）和 React
- **不得 import 原单文件的 `.module.{ext}`**，也不得引用原页面组件
- render 函数中直接组合所有子组件，不加额外包装 CSS

`index.ts`（每个组件目录内）必须：
- 只写 `export { default } from './{Name}'`，一行，不加其他内容

**CSS 硬约束**：
- 所有 CSS 值必须与 `ir.json` 中对应节点 `css` 字段完全一致
- 禁止手写或修改任何 px 值、颜色、间距
- Code Connect 匹配的组件除外

### 落地

> ⚠️ 以下为 Path B（未实现）的落地方式。Path A（当前实现）通过
> `split_components.py --apply --dest=...` 落地，stage/ 与 dest/ 结构一致，无需手动 cp。

```bash
# Path B 落地（供参考，当前不可用）
cp -rf .figma-to-code/3-page-code/{Name}-{nodeId}/split-b/components \
       $TARGET_DIR/$DEST_DIR/{Name}/
```

### 后置校验（必须门禁，不可跳过）

落地完成后立即运行：

```bash
python3 $SKILL_DIR/scripts/validate_split.py '<URL>' --css-ext=$CSS_EXT
# 或: python3 $SKILL_DIR/scripts/validate_split.py --node-id={nodeId}
```

五层检查，综合评分 ≥ 90 才视为通过：

| 层 | 内容 |
|----|------|
| Split Layer 0 | 文件完整性 + TSX 结构（文件存在、export default、import 路径可解析、无 undefined/NaN） |
| Split Layer 1 | CSS 漂移（对比 ir.json，差异 > 1px = error） |
| Split Layer 2 | 类名覆盖（IR 中所有类名在某个组件 CSS 里出现） |
| Split Layer 3 | Less/SCSS 编译无报错 |
| Split Layer 4 | TSX 编译检查（tsc --noEmit，捕获 Fragment 缺失、import 错误、类型错误） |

**Split Layer 4 执行方式**（validate_split.py 之后立即运行）：
```bash
npx tsc --noEmit --project $TARGET_DIR/tsconfig.json 2>&1 | grep "{nodeId}" || echo "✓ tsc 通过"
```
- 有输出 → 修复 TSX 语法错误后重跑
- 无输出 → 通过（典型问题：相邻 JSX 元素缺 `<>...</>` Fragment 包裹）

评分 < 90 或 Layer 4 报错时停止，修复后重新运行 --apply 再校验，不得声称通过。

### P1.5 完成汇报格式

```
✅ 语义化组件拆分完成

  组件数量: N 个
  CC 组件替换: N 个（ComponentA × 1, ComponentB × 1）
  落地: src/pages/{Name}/（入口 index.tsx，sections 在 components/）

  原文件保留（确认无误后可删除）:
    {Name}.tsx / {Name}.module.{ext}
  全量备份: .figma-to-code/3-page-code/{Name}-{nodeId}/split-a/
```

---

## Path A — 脚本路径（--mode=a）

### 步骤 1：检测边界

```bash
python3 $SKILL_DIR/scripts/split_components.py '<URL>' --analyze
# 或: python3 $SKILL_DIR/scripts/split_components.py --node-id={nodeId} --analyze
# 输出: .figma-to-code/3-page-code/{Name}-{nodeId}/split-a/plan.json
```

### 步骤 2：Claude 填写 plan.json 的 semantic 字段

读取 plan.json，对每个 `"semantic": null` 的组件，按上方"AI 语义决策规则"填写：

```json
{
"name": "HeroSection",
"semantic": {
  "htmlTag": "section",
  "className": "hero-section",
  "componentName": "HeroSection",
  "isExtractedComponent": true,
  "props": []
}
```

如有 Code Connect 匹配，同时填写：
```json
"ccComponent": "Button",
"ccImport": "import { Button } from 'your-component-lib'"
```

### 步骤 3：应用并落地

```bash
python3 $SKILL_DIR/scripts/split_components.py --node-id={nodeId} --apply
```

### 步骤 4：必须校验（不可跳过）

Path A 生成完成后**立即运行**：

```bash
python3 $SKILL_DIR/scripts/validate_split.py --node-id={nodeId} --css-ext=$CSS_EXT
# Layer 4: TSX 编译检查
npx tsc --noEmit --project $TARGET_DIR/tsconfig.json 2>&1 | grep "{nodeId}" || echo "✓ tsc 通过"
```

五层检查，评分 ≥ 90 且 Layer 4 无报错才视为通过。评分 < 90 或 tsc 报错时停止并修复，不得声称通过。

---

## AB 对比（--mode=ab）

两条路径都运行，生成 `.figma-to-code/3-page-code/{Name}-{nodeId}/ab-report.md`：

```markdown
# AB 对比 — {Name}

| 指标 | Path A | Path B |
|------|--------|--------|
| 组件数 | N | N |
| CC 组件替换数 | N | N |
| CSS 偏差警告 | N | N |
| 耗时 | Ns | Ns |
```

默认落地 Path B 的结果。

---

## 硬约束（贯穿 P1.5 全程）

**CSS**
- CSS 值只来自 IR，禁止手写或修改任何 px 值

**TSX 语法**
- 每个组件必须用 `export default function`，禁止 named export
- 每个组件 CSS import 只能是 `import styles from './{Name}.module.{ext}'`
- 禁止 import 父目录文件（`../../` 路径）
- Page.tsx 不得 import 原单文件的 `.module.{ext}`
- 一个组件返回多个并列根元素时，必须用 `<>...</>` Fragment 包裹

**流程**
- `components/` 落地后必须通过 `validate_split.py`（五层，评分 ≥ 90）+ tsc 编译检查
- `3-page-code/` 暂存目录名格式 `{Name}-{nodeId}` 不得修改（validate_split 溯源 key）；`src/pages/` 落地目录只用 `{Name}`，不含 nodeId
- index.ts 更新时保留原行为注释
- Code Connect 无匹配时 P1.5 不失败，跳过匹配继续执行
