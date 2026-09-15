# AI Layer 约束规范

## 核心原则

AI 只决策语义字段，不碰 CSS。输入输出严格为 JSON。

## AI 决策范围

```typescript
interface SemanticDecision {
  htmlTag: string;               // 语义 HTML 标签
  componentName: string | null;  // null = 不提取为独立组件
  className: string;             // kebab-case
  isExtractedComponent: boolean;
  props: PropDef[];
}
```

## htmlTag 判断规则

```
isImageNode === true                         → img
figmaName 含 btn/button/cta（不区分大小写）   → button
figmaName 含 nav/navbar/navigation           → nav
figmaName 含 header                          → header
figmaName 含 footer                          → footer
figmaName 含 list/menu + 有重复子节点         → ul（子节点 → li）
figmaName 含 link/anchor                     → a
isTextNode + 段落文字                         → p
isTextNode + 标题文字（fontSize >= 24）        → h2 / h3（按层级）
isTextNode + 短标签文字                       → span
其余                                          → div
```

## isExtractedComponent 判断规则

```
variants 字段非空（有变体）                      → true
figmaType === "COMPONENT"                       → true
在整个文件中出现 2 次以上（跨节点检测）            → true
figmaName 含有通用组件关键词（modal/dropdown/...）→ true
其余                                             → false
```

## props 判断规则

```
isTextNode === true + 内容是动态数据（商品名/价格/用户名/标题）→ props
isImageNode === true                                          → props（imageSrc + imageAlt）
htmlTag === 'a'                                               → props（href）
htmlTag === 'button' + 有点击行为意图                          → props（onClick）
isTextNode === true + 内容是静态 UI 文案（按钮文字/标签）       → 不加 props
```

## Prompt 模板

```
## 角色
你是一个 React 组件语义分析器。

## 输入
以下是一个 Figma 节点的中间表示（CSS 已由脚本提取完毕，禁止修改）：

{IR_JSON}

## 输出要求
- 仅输出严格的 JSON，不得输出任何其他文字
- 禁止修改 css 字段
- className 使用 kebab-case

## 判断规则
{SEMANTIC_RULES}

## 输出格式
{
  "htmlTag": string,
  "componentName": string | null,
  "className": string,
  "isExtractedComponent": boolean,
  "props": [
    { "name": string, "type": string, "replaces": "textContent|imageSrc|href|onClick" }
  ]
}
```

## 输出格式强制

使用 `response_format: { type: "json_object" }` 或等价参数，禁止 AI 在 JSON 外输出文字。

## 低置信度布局处理

若 IR 中 `inferredFlex.confidence === 'low'`：

```
额外检查 className 语义是否与 flex 方向一致
若明显不对（如水平导航栏被推断为 column），在 semantic 中标记：
  "layoutOverride": { "direction": "row" }
```

## className 命名风格

- 使用语义名称而非结构名称
  - ✅ `product-card`、`price-row`、`nav-links`
  - ❌ `frame-23`、`group-12`、`container-1`
- 父子关系用 BEM modifier，不用嵌套命名
  - ✅ `.card` / `.card--featured` / `.card__image`
  - ❌ `.card-image-wrapper-container`
- 避免过度具体（`blue-submit-button` → `submit-button`）

## 启用 AI Layer 时的环境变量

```bash
export ANTHROPIC_API_KEY=sk-ant-xxx          # 直连
export ANTHROPIC_AUTH_TOKEN=xxx              # 内网网关
export ANTHROPIC_BEDROCK_BASE_URL=https://your-gateway/bedrock
export ANTHROPIC_DEFAULT_HAIKU_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0
export HTTPS_PROXY=http://your-proxy:port    # 需要代理时
```
