# IR 中间层数据结构

IR（Intermediate Representation）是 Script Layer 的输出，AI Layer 的输入。
所有 CSS 属性在此层完全确定，AI 只填充语义字段。

## TypeScript 类型定义

```typescript
// ─── Script Layer 填充（完整，不留空）───────────────────────────

interface NodeIR {
  // 元信息
  figmaId: string;
  figmaName: string;
  figmaType:
    | 'FRAME' | 'GROUP' | 'COMPONENT' | 'COMPONENT_SET'
    | 'INSTANCE' | 'RECTANGLE' | 'ELLIPSE' | 'TEXT'
    | 'VECTOR' | 'BOOLEAN_OPERATION' | 'LINE';

  // 节点特征
  isImageNode: boolean;        // RECTANGLE + fills[0].type === 'IMAGE'
  isVectorNode: boolean;       // type === 'VECTOR' | 'BOOLEAN_OPERATION'
  isTextNode: boolean;         // type === 'TEXT'
  isComponentInstance: boolean;
  textContent?: string;        // 文字节点原始内容

  // 组件变体（Component Set）
  variants?: Record<string, string[]>;  // { Type: ['Primary','Secondary'], Size: ['SM','MD'] }

  // 推断元信息（布局推断时写入）
  inferredFlex?: {
    direction: 'row' | 'column';
    confidence: 'high' | 'low';  // low → AI Layer 需二次确认
  };

  // 矢量处理策略（Script 标记，AI 决定）
  vectorStrategy?: 'static' | 'inline' | 'library';

  // CSS（Script 完整填充，AI 不得修改）
  css: CSSProperties;

  // 语义（AI Layer 填充）
  semantic?: SemanticDecision;

  children: NodeIR[];
}

// ─── CSS 属性（Script Layer 负责填充）────────────────────────────

interface CSSProperties {
  // 定位
  position?: 'absolute' | 'relative';
  top?: string;
  right?: string;
  bottom?: string;
  left?: string;
  transform?: string;
  zIndex?: string;

  // 尺寸
  width?: string;       // FIXED → 'Npx'，HUG → 不写，FILL → 不写（用 flex）
  height?: string;
  minWidth?: string;
  maxWidth?: string;
  minHeight?: string;
  maxHeight?: string;
  flex?: '1';           // FILL sizing
  alignSelf?: string;   // layoutAlign

  // Flex 容器
  display?: 'flex';
  flexDirection?: 'row' | 'column';
  justifyContent?: string;
  alignItems?: string;
  alignContent?: string;
  flexWrap?: 'wrap';
  gap?: string;
  rowGap?: string;
  columnGap?: string;
  padding?: string;
  paddingTop?: string;
  paddingRight?: string;
  paddingBottom?: string;
  paddingLeft?: string;
  overflow?: 'hidden';

  // 视觉
  backgroundColor?: string;
  background?: string;   // 渐变或多层 fills
  borderRadius?: string;
  border?: string;
  borderTop?: string;
  borderRight?: string;
  borderBottom?: string;
  borderLeft?: string;
  outline?: string;
  boxShadow?: string;
  opacity?: string;
  mixBlendMode?: string;
  filter?: string;
  backdropFilter?: string;

  // 排版（TEXT 节点）
  fontSize?: string;
  fontWeight?: string;
  fontFamily?: string;
  fontStyle?: string;
  lineHeight?: string;
  letterSpacing?: string;
  textAlign?: string;
  textTransform?: string;
  textDecoration?: string;
  textIndent?: string;
  whiteSpace?: string;
  overflow2?: 'hidden';       // 文字溢出（与上面 overflow 分开）
  textOverflow?: 'ellipsis';
  WebkitLineClamp?: string;
  WebkitBoxOrient?: 'vertical';
}

// ─── 语义字段（AI Layer 填充）────────────────────────────────────

interface SemanticDecision {
  htmlTag: string;               // 'div' | 'button' | 'nav' | 'img' | 'p' | 'h2' ...
  componentName: string | null;  // 'ProductCard' | null（不提取时为空）
  className: string;             // kebab-case，如 'product-card'
  isExtractedComponent: boolean;
  props: PropDef[];
}

interface PropDef {
  name: string;                                          // 'imageSrc' | 'title' | 'onClick'
  type: string;                                          // 'string' | '() => void'
  replaces: 'textContent' | 'imageSrc' | 'href' | 'onClick';
}
```

## IR JSON 示例

```json
{
  "figmaId": "42:100",
  "figmaName": "ProductCard",
  "figmaType": "COMPONENT",
  "isImageNode": false,
  "isVectorNode": false,
  "isTextNode": false,
  "isComponentInstance": false,
  "variants": {
    "State": ["Default", "Hover", "Disabled"]
  },
  "css": {
    "display": "flex",
    "flexDirection": "column",
    "width": "240px",
    "borderRadius": "12px",
    "backgroundColor": "$color-bg",
    "boxShadow": "0 4px 8px rgba(0,0,0,0.12)",
    "overflow": "hidden"
  },
  "semantic": {
    "htmlTag": "div",
    "componentName": "ProductCard",
    "className": "product-card",
    "isExtractedComponent": true,
    "props": [
      { "name": "imageSrc", "type": "string", "replaces": "imageSrc" },
      { "name": "name", "type": "string", "replaces": "textContent" },
      { "name": "price", "type": "string", "replaces": "textContent" },
      { "name": "onAdd", "type": "() => void", "replaces": "onClick" }
    ]
  },
  "children": []
}
```

## IR 文件存储规范

- 所有临时文件按步骤顺序保存至 `.figma-to-code/`（可加入 `.gitignore` 或 commit 作为审计留档）
  - `1-raw-data/{figmaId}-raw-data.json` — Figma API 完整原始响应
  - `2-figma-extract/{figmaId}.ir.json` — 从 Figma 提取的布局结构 + CSS 属性（`:` 替换为 `-`）
  - `3-page-code/{Name}-{nodeId}/` — 生成的单页组件文件
  - `2-figma-extract/{figmaId}.semantic.json` — AI 语义决策（semantic_context.py 生成）
- IR 是可重入的：修复 Script Layer 后重新生成 IR，Code Gen 自动重新输出

## 属性优先级

```
1. visible: false → 不生成 IR 节点，children 也跳过
2. Figma Variables binding → css 值用 Sass 变量名，不用原始 hex
3. layoutSizingHorizontal/Vertical → 决定 width/height/flex 写哪个
4. layoutPositioning: ABSOLUTE → position: absolute，不走 flex sizing 逻辑
5. 推断 flex（layoutMode 不存在时）→ 写入 inferredFlex，confidence 低时 AI 确认
```
