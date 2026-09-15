# 转换 Prompt 模板

可直接复用的提示词模板，用于 Claude Code 中调用 Figma MCP 进行转换。

---

## 模板 1：Phase 1 提取 Design Token

```
读取这个 Figma 文件的全部 design token，只输出 _variables.scss 文件内容：

文件链接：{FIGMA_URL}

命名规则：
- 颜色：$color-[语义]-[变体]（如 $color-primary, $color-text-muted, $color-bg）
- 间距：$spacing-[xs|sm|md|lg|xl]（按值从小到大排列）
- 圆角：$radius-[sm|md|lg|full]
- 阴影：$shadow-[card|modal|dropdown|button]
- 字号：$font-size-[xs|sm|md|lg|xl|2xl]

格式要求：
- 使用 Sass 变量（$variable-name: value;）
- 按类别分组，每组加注释（// Colors, // Spacing 等）
- 颜色优先使用 hex，opacity < 1 用 rgba()
- 不生成任何组件代码，只输出 _variables.scss

若设计师使用了 Figma Variables，用 get_variable_defs 工具直接提取，
变量名映射规则：将 "/" 替换为 "-"（color/primary → $color-primary）
```

---

## 模板 2：单组件转换

```
将以下 Figma 组件转换为 React + Sass Module：

节点链接：{FIGMA_URL}?node-id={NODE_ID}

## 项目配置
- CSS 方案：Sass Modules（@use 语法，禁止 @import）
- 组件目录：src/components/{ComponentName}/
- 变量文件：src/styles/_variables.scss（内容如下）

{_VARIABLES_SCSS_CONTENT}

## 布局映射规则
- 有 layoutMode → 转为 flexbox（方向、对齐、gap 按规则映射）
- 无 layoutMode → 推断 flex（y 偏差≤4px→row，x 偏差≤4px→column）；无法推断→ absolute
- layoutSizingHorizontal/Vertical：FIXED→px，HUG→不写，FILL→flex:1（不是 width:100%）
- layoutPositioning: ABSOLUTE → position: absolute，父节点加 position: relative
- clipsContent: true → overflow: hidden

## 样式映射规则
- 颜色必须引用 _variables.scss 变量，禁止硬编码 hex
- strokeAlign: OUTSIDE + 有圆角 → box-shadow: 0 0 0 Npx color（不用 outline）
- lineHeight 用 px 值，不转倍数
- 描边外侧（OUTSIDE）无圆角用 outline，有圆角用 box-shadow 模拟

## 输出格式（严格遵守，不增减）

文件1：{ComponentName}.tsx
---
[仅包含 import、interface Props、函数组件，不加注释]
[每个 DOM 元素加 data-figma-id="{figmaId}" 属性]

文件2：{ComponentName}.module.scss
---
[仅包含 .className 规则，@use 引入变量，不内联数值]
---

文件3：index.ts
---
export { {ComponentName} } from './{ComponentName}';
---
```

---

## 模板 3：AI Layer 语义决策

```
你是一个 React 组件语义分析器。

## 输入（CSS 已由脚本提取完毕，禁止修改）

{IR_JSON}

## 仅输出以下 JSON，禁止输出任何其他文字

{
  "htmlTag": string,
  "componentName": string | null,
  "className": string,
  "isExtractedComponent": boolean,
  "props": [
    { "name": string, "type": string, "replaces": "textContent|imageSrc|href|onClick" }
  ]
}

## 判断规则

htmlTag:
  isImageNode=true → img
  figmaName 含 btn/button/cta → button
  figmaName 含 nav/navbar → nav
  figmaName 含 header → header
  figmaName 含 footer → footer
  isTextNode + 段落 → p
  isTextNode + 短文字 → span
  其余 → div

isExtractedComponent:
  variants 非空 → true
  figmaType=COMPONENT → true
  文件中出现 2+ 次 → true
  其余 → false

props:
  isTextNode + 动态内容（商品名/价格/用户名） → { name, type:string, replaces:textContent }
  isImageNode → { name:imageSrc, type:string, replaces:imageSrc }
  htmlTag=button → { name:onClick, type:()=>void, replaces:onClick }
  静态 UI 文案（按钮文字/标签） → 不加 props

className:
  使用语义名（product-card, price-row, nav-links）
  kebab-case
  禁止 frame-XX, group-XX
```

---

## 模板 4：批量组件转换（多个节点）

```
将以下 Figma 组件批量转换为 React + Sass Module：

节点列表：
1. {NODE_ID_1}（Button 组件）
2. {NODE_ID_2}（Tag 组件）
3. {NODE_ID_3}（Badge 组件）

处理顺序：按依赖关系从原子到复合
每个组件独立输出，遵循模板2的格式要求

公共配置：
{同模板2的配置部分}
```

---

## 模板 5：还原度验收对比

```
请对这个已生成的组件做视觉还原度对比：

Figma 截图：（调用 get_screenshot {nodeId}）
当前组件路径：src/components/{ComponentName}/{ComponentName}.tsx

对比检查清单：
1. 尺寸：width/height 是否与 Figma 一致（容忍 ±2px）
2. 间距：padding/gap 是否正确
3. 颜色：背景色/文字颜色是否匹配
4. 字体：fontSize/fontWeight/lineHeight 是否正确
5. 圆角：border-radius 是否匹配
6. 阴影：box-shadow 是否匹配

输出格式：
对每一项列出：期望值 vs 实际值，偏差 px，是否 pass
最终给出综合评分（0-100）和需要修复的优先级列表
```
