# Figma → CSS 完整映射规则

Script Layer 依据此文件将 Figma 节点属性映射为 CSS 属性。
所有映射为确定性规则，不依赖 AI 判断。

---

## 一、Auto Layout → Flexbox（容器）

| Figma 字段 | CSS |
|-----------|-----|
| `layoutMode: HORIZONTAL` | `display: flex; flex-direction: row` |
| `layoutMode: VERTICAL` | `display: flex; flex-direction: column` |
| `primaryAxisAlignItems: MIN` | `justify-content: flex-start` |
| `primaryAxisAlignItems: CENTER` | `justify-content: center` |
| `primaryAxisAlignItems: MAX` | `justify-content: flex-end` |
| `primaryAxisAlignItems: SPACE_BETWEEN` | `justify-content: space-between` |
| `counterAxisAlignItems: MIN` | `align-items: flex-start` |
| `counterAxisAlignItems: CENTER` | `align-items: center` |
| `counterAxisAlignItems: MAX` | `align-items: flex-end` |
| `counterAxisAlignItems: BASELINE` | `align-items: baseline` |
| `counterAxisAlignContent: SPACE_BETWEEN` | `align-content: space-between`（WRAP 模式） |
| `layoutWrap: WRAP` | `flex-wrap: wrap` |
| `itemSpacing` | `gap: Npx`（非 WRAP）/ `column-gap: Npx`（WRAP） |
| `counterAxisSpacing` | `row-gap: Npx`（仅 WRAP 模式） |
| `paddingTop/Right/Bottom/Left` | `padding: T R B Lpx` |
| `clipsContent: true` | `overflow: hidden` |

## 二、Auto Layout 子节点属性

| Figma 字段 | CSS（加在子节点） |
|-----------|-----------------|
| `layoutGrow: 1` | `flex-grow: 1` |
| `layoutAlign: STRETCH` | `align-self: stretch` |
| `layoutAlign: MIN` | `align-self: flex-start` |
| `layoutAlign: CENTER` | `align-self: center` |
| `layoutAlign: MAX` | `align-self: flex-end` |
| `layoutPositioning: ABSOLUTE` | `position: absolute`（脱离 flex 流） |

## 三、Sizing 模式

⚠️ 最高频的还原失准来源，必须逐节点检查。

| `layoutSizingHorizontal/Vertical` | CSS |
|-----------------------------------|-----|
| `FIXED` | `width/height: Npx` |
| `HUG` | （不写，靠内容撑开） |
| `FILL` | `flex: 1`（⚠️ 不是 `width: 100%`） |

同时存在 `minWidth/maxWidth/minHeight/maxHeight` 时必须输出对应 CSS。

## 四、定位（非 Auto Layout 父节点）

父节点无 `layoutMode`：父节点加 `position: relative`，子节点按 constraints 绝对定位。

| `constraints.horizontal` | CSS |
|--------------------------|-----|
| `LEFT` | `left: {x}px` |
| `RIGHT` | `right: {parentWidth - x - w}px` |
| `CENTER` | `left: 50%; transform: translateX(-50%)` |
| `SCALE` | `left: {x/pw*100}%; width: {w/pw*100}%` |
| `LEFT_RIGHT` | `left: {x}px; right: {pw-x-w}px` |

垂直同理（TOP/BOTTOM/CENTER/SCALE/TOP_BOTTOM）。

若有旋转，transform 合并：`transform: translateX(-50%) rotate(Ndeg)`

## 五、圆角

| Figma 字段 | CSS |
|-----------|-----|
| `cornerRadius`（统一） | `border-radius: Npx` |
| 四角独立（topLeft/topRight/bottomRight/bottomLeft） | `border-radius: TL TR BR BLpx`（⚠️ 即使某角为 0 也写完整四值） |
| `cornerSmoothing` | ❌ CSS 无对应，忽略 |

## 六、透明度与混合模式

| Figma 字段 | CSS |
|-----------|-----|
| `opacity`（节点整体） | `opacity: N` |
| `fill.opacity`（填充层） | rgba alpha 通道（与节点 opacity 独立） |
| `blendMode: NORMAL` | （不写） |
| `blendMode: MULTIPLY` | `mix-blend-mode: multiply` |
| `blendMode: SCREEN` | `mix-blend-mode: screen` |
| `blendMode: OVERLAY` | `mix-blend-mode: overlay` |
| `blendMode: DARKEN/LIGHTEN/...` | 同名映射 |
| `blendMode: PASS_THROUGH`（GROUP） | ❌ 无 CSS 等价，子节点各自处理 |

## 七、填充（Fills）

跳过 `visible: false` 的层。多层 fills 逆序拼接为 CSS background 多值。

### 纯色（SOLID）
```
r/g/b (0-1) → Math.round(v * 255) → hex
opacity < 1 → rgba(r, g, b, opacity)
有 Figma Variable binding → 用 Sass 变量名（优先）
```

### 线性渐变（GRADIENT_LINEAR）
```
角度：dx = end.x - start.x, dy = end.y - start.y
     angle = atan2(dy, dx) * (180/π) + 90
输出：linear-gradient({angle}deg, color1 P%, color2 P%)
```

### 径向渐变（GRADIENT_RADIAL）
```
输出：radial-gradient(ellipse {rx}% {ry}% at {cx}% {cy}%, ...)
```

### 角度渐变（GRADIENT_ANGULAR）
```
输出：conic-gradient(from {angle}deg at {cx}% {cy}%, ...)
```

### 图片填充（IMAGE）
```
scaleMode: FILL    → background-size: cover; background-position: center
scaleMode: FIT     → background-size: contain; background-repeat: no-repeat
scaleMode: TILE    → background-repeat: repeat; background-size: auto
scaleMode: STRETCH → background-size: 100% 100%
```

## 八、描边（Strokes）

| 场景 | CSS |
|------|-----|
| `strokeAlign: INSIDE/CENTER` | `border: Npx solid color`（box-sizing: border-box） |
| `strokeAlign: OUTSIDE`（无圆角） | `outline: Npx solid color; outline-offset: 0` |
| `strokeAlign: OUTSIDE`（有圆角） | `box-shadow: 0 0 0 Npx color`（outline 不支持圆角） |
| 四边独立粗细 | `border-top/right/bottom/left-width: Npx` |
| `strokeDashes` 有值 | `border-style: dashed`（近似，CSS 无法精确控制） |

## 九、效果（Effects）

跳过 `visible: false` 的 effect，多个同类型用逗号合并。

| 类型 | CSS |
|------|-----|
| `DROP_SHADOW` | `box-shadow: {x}px {y}px {blur}px {spread}px rgba(r,g,b,a)` |
| `INNER_SHADOW` | `box-shadow: inset {x}px {y}px {blur}px rgba(r,g,b,a)` |
| `LAYER_BLUR` | `filter: blur(Npx)` |
| `BACKGROUND_BLUR` | `backdrop-filter: blur(Npx); -webkit-backdrop-filter: blur(Npx)` |

多个 filter 合并：`filter: blur(4px) drop-shadow(...)` 

## 十、排版（TEXT 节点）

| Figma 字段 | CSS | 注意 |
|-----------|-----|------|
| `fontSize` | `font-size: Npx` | |
| `fontWeight` | `font-weight: N` | 数字直接用 |
| `fontFamily` | `font-family: 'X', sans-serif` | |
| `lineHeightPx` | `line-height: Npx` | ⚠️ 用 px，不转倍数 |
| `letterSpacing` (PIXELS) | `letter-spacing: Npx` | |
| `letterSpacing` (PERCENT) | `letter-spacing: Nem` | N/100 换算 |
| `lineHeight: AUTO` | `line-height: normal` | |
| `textAlignHorizontal: LEFT/CENTER/RIGHT/JUSTIFIED` | `text-align: left/center/right/justify` | |
| `textCase: UPPER/LOWER/TITLE/SMALL_CAPS` | `text-transform: uppercase/lowercase/capitalize` / `font-variant: small-caps` | |
| `textDecoration: UNDERLINE/STRIKETHROUGH` | `text-decoration: underline/line-through` | |
| `paragraphSpacing` | `margin-bottom: Npx` | |
| `paragraphIndent` | `text-indent: Npx` | |

### 文字尺寸模式（textAutoResize）

| 值 | CSS |
|----|-----|
| `NONE` | 固定 width + height，加 `overflow: hidden` |
| `HEIGHT` | 只写 width |
| `WIDTH_AND_HEIGHT` | 都不写 |
| `TRUNCATE` | 只写 width，加溢出处理 |

### 文字溢出（textTruncation）

| 场景 | CSS |
|------|-----|
| `ENDING` + 单行 | `overflow: hidden; white-space: nowrap; text-overflow: ellipsis` |
| `ENDING` + `maxLines > 1` | `display: -webkit-box; -webkit-line-clamp: N; -webkit-box-orient: vertical; overflow: hidden` |

## 十一、旋转

```
rotation (Figma 顺时针为正) → transform: rotate({-rotation}deg)
⚠️ 需取反：Figma 存的是负值
与其他 transform 合并时注意空格拼接
```

## 十二、Z-index

```
有视觉重叠的兄弟节点 → 按数组 index 分配 z-index: 1, 2, 3...
无重叠 → 省略 z-index
```

## 十三、节点可见性

```
visible: false → 跳过节点及其所有 children，不生成任何代码
```

## 十四、Figma Variables 绑定（优先级最高）

```
fill.boundVariables.color.id → 找 variable.name → 替换 "/" 为 "-" → $color-primary-500
spacing.boundVariables.xxx   → 找 variable.name → $spacing-md
优先于原始颜色/数值计算
```

## 十五、特殊节点处理

| 节点类型 | 处理策略 |
|---------|---------|
| `RECTANGLE` + `fills[0].type: IMAGE` | `isImageNode=true`，转为 `<img>` |
| `VECTOR` / `BOOLEAN_OPERATION` | 标记 `vectorStrategy`，由 AI Layer 决定 inline/static/library |
| `LINE` | `height: 0; border-top: {strokeWeight}px solid color` |
| `ELLIPSE`（完整圆） | `border-radius: 50%` |
| `ELLIPSE`（扇形） | ❌ 需 SVG 或 conic-gradient 近似 |
| `GROUP`（PASS_THROUGH） | 扁平化，子节点提升到父级 |
| `GROUP`（非 PASS_THROUGH） | 保留，加 `isolation: isolate` |

## 十六、不映射的属性

| 属性 | 原因 |
|------|------|
| `layoutGrids` | 仅辅助设计参考线 |
| `exportSettings` | 不影响组件代码 |
| `reactions` | Prototype 动画，超出范围 |
| `locked` | 仅 Figma 编辑器行为 |

## 十七、Design Token 替换（后处理阶段）

IR 构建完成后、`patch_flex_shrink` 之前，`token_resolver.resolve_ir_tokens()` 遍历整棵 IR 树，将裸值替换为 CSS 自定义属性引用（若已配置 `DESIGN_TOKEN_CSS_URL`）。

### 替换规则

| CSS 属性 | 替换条件 | 示例（使用项目自定义 token）|
|----------|---------|------|
| `color` / `background-color` / `border-color` 等 | 值以 `#` 或 `rgb` 开头，且 token 映射命中 | `#121214` → `var(--color-text-primary)` |
| `font-size` | 精确匹配字阶 map | `14px` → `var(--font-size-sm)` |
| `font-weight` | 精确匹配权重 map | `500` → `var(--font-weight-medium)` |
| `font-family` | 值含项目主字体名（不区分大小写） | `"Inter", sans-serif` → `var(--font-family)` |
| `border-radius`（单值） | 精确匹配圆角 map | `4px` → `var(--border-radius-sm)` |

### 不替换的属性

| 属性 | 原因 |
|------|------|
| `padding` / `margin` / `gap` | 裸像素值缺乏语义，强行映射会引入语义污染 |
| `line-height` | 通常为推算值，非设计系统固定 token |
| `border-radius`（多值） | 如 `4px 8px` 无对应单一 token |
| 已有 `var()` 的值 | 跳过，不二次处理 |

### 兜底值

建议始终写 `var(--token-name, fallback)` 格式，保证 token CSS 未加载时有可读的 fallback。

```scss
color: var(--color-text-primary, #121214);  /* ✅ */
color: #121214;                              /* ⚠️ 硬编码，无法主题切换 */
```

### Design token 来源与缓存

- 通过环境变量 `DESIGN_TOKEN_CSS_URL` 配置，指向项目的 CSS 变量文件（支持任意 URL）
- 缓存：`.figma-to-code/1-design-token/design-tokens.json`，TTL 86400s（1 天）
- 未配置或网络失败时静默降级，CSS 值保持裸 hex/px，不影响主流程
