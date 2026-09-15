# 程序化校验体系（默认 Layer 0-2，Layer 3/4 可选）

## 概览

```
Layer 0: 资源落地    → /assets/ 引用检测，自动 move 到 public/assets/（默认启用）
Layer 1: 静态校验    → 渲染前，检查 IR 和代码文件（默认启用）
Layer 2: 编译校验    → less/scss 编译无报错、无 NaN/undefined 值（默认启用）
Layer 3: 计算样式    → Playwright getComputedStyle vs IR 期望值（需 playwright）
Layer 4: 视觉截图    → Pixelmatch：浏览器截图 vs Figma 截图（需 playwright + pillow）
```

综合评分 ≥ 90 为 pass。

---

## Layer 1：静态校验

在 Code Gen 输出后立即运行，不需要浏览器。

**检查项：**

```typescript
// 1. Sass 变量引用完整性
//    css 中出现 $xxx → 检查 _variables.scss 是否定义
for (const [prop, val] of Object.entries(ir.css)) {
  if (typeof val === 'string' && val.startsWith('$')) {
    if (!sassVariables.has(val)) → ERROR: 引用了未定义的 Sass 变量
  }
}

// 2. FILL sizing 但父节点不是 flex 容器
if (ir.css.flex === '1' && !parentIsFlex(ir)) → ERROR: flex:1 父节点不是 flex 容器

// 3. position: absolute 但父节点缺少 position: relative
if (ir.css.position === 'absolute' && !parentHasPosition(ir)) → WARNING

// 4. transform 多值拼接检查
if (ir.css.transform?.includes('  ')) → WARNING: transform 含多余空格
```

---

## Layer 2：编译校验

```typescript
import * as sass from 'sass';

sass.compile(scssFile, { loadPaths: ['src/styles'] });
// 编译失败 → ERROR

// 检查生成 CSS 中的无效值
if (value === 'undefinedpx' || value === 'NaNpx' || value.includes('undefined'))
  → ERROR: 变量未定义导致无效 CSS 值
```

---

## Layer 3：计算样式校验（Playwright）

**前提：** Code Gen 给每个 DOM 元素自动附加 `data-figma-id`（生产构建时 Babel 插件移除）。

```typescript
const el = page.locator(`[data-figma-id="${ir.figmaId}"]`);
const computed = await el.evaluate(node => {
  const s = window.getComputedStyle(node);
  const r = node.getBoundingClientRect();
  return { width: r.width, height: r.height, gap: parseFloat(s.gap) || 0,
           display: s.display, flexDirection: s.flexDirection, ... };
});
```

**校验规则：**

| 属性类别 | 容忍度 |
|---------|--------|
| width/height/padding/gap/top/left（尺寸位置类） | ≤ 2px → pass；2-8px → warning；> 8px → error |
| display/flexDirection/position/overflow（布局类） | 精确匹配 |
| color/backgroundColor（颜色类） | 归一化为 rgba 后精确匹配 |
| fontSize/fontWeight | 精确匹配 |

---

## Layer 4：视觉截图 Diff

```typescript
// 1. 获取 Figma 参考截图（@2x）
const figmaShot = await figmaMCP.getScreenshot({ nodeId, format: 'png', scale: 2 });

// 2. 获取浏览器渲染截图
const browserShot = await page.locator(`[data-figma-id="${nodeId}"]`)
  .screenshot({ scale: 'device' });

// 3. Pixelmatch 比对
const diffPixels = pixelmatch(expected.data, actual.data, diff.data, w, h, {
  threshold: 0.1,    // 颜色差异容忍度
  includeAA: false,  // 忽略抗锯齿差异
});

const diffPercent = diffPixels / (w * h);
// < 2% → pass
// 差异图保存至 reports/diff-{figmaId}.png
```

---

## 综合评分算法

```typescript
function computeScore(report: ValidationReport): number {
  let score = 100;

  // Layer 1
  report.layers.static.issues.forEach(i => {
    score -= i.severity === 'error' ? 5 : 1;
  });

  // Layer 3
  report.layers.computed.mismatches.forEach(m => {
    if (m.severity === 'error') {
      score -= Math.min((m.delta ?? 5) * 0.5, 10);
    } else {
      score -= 1;
    }
  });

  // Layer 4
  report.layers.visual.results.forEach(r => {
    score -= r.diffPercent * 100 * 0.3;
  });

  return Math.max(0, Math.round(score));
}
```

---

## 校验命令

```bash
# 单组件默认三层校验（Layer 0/1/2）
python3 scripts/validate.py 'https://...?node-id=42-100'

# 批量校验所有 IR 节点
python3 scripts/validate.py --all

# 只跑计算样式 + 截图（需要 playwright + pillow）
python3 scripts/validate.py 'https://...?node-id=42-100' --layers=computed,visual

# 完整五层校验
python3 scripts/validate.py 'https://...?node-id=42-100' --layers=assets,static,compile,computed,visual
```

---

## 终端报告格式

```
╔══════════════════════════════════════╗
║  ProductCard  校验报告               ║
╠══════════════════════════════════════╣
║  Layer 1 静态校验    ✓  0 issues     ║
║  Layer 2 编译校验    ✓  0 issues     ║
║  Layer 3 计算样式    ⚠  2 warnings   ║
║    · gap: 期望 8px，实际 10px  +2px  ║
║    · padding-left: 期望 16px, 18px   ║
║  Layer 4 视觉截图    ✓  diff 0.8%   ║
╠══════════════════════════════════════╣
║  综合还原度评分:  94 / 100           ║
╚══════════════════════════════════════╝
```

---

## 快速反馈循环

```
浏览器发现偏差
  → DevTools 取 data-figma-id（5s）
  → Console getComputedStyle 确认偏差属性（10s）
  → 判断偏差层：CSS 值错 → Script Layer；命名/结构 → AI Layer
  → 修改映射函数（scripts/lib/css_extractor.py 等）
  → python3 scripts/convert.py 'https://...?node-id=XX-XX' --from-raw-data（< 3s）
  → Vite HMR 自动更新浏览器（< 1s）
  → python3 scripts/validate.py 'https://...?node-id=XX-XX'（< 5s）
────────────────────────────────────
全程约 1-2 分钟 / 次
```

---

## 偏差修改位置速查

| 偏差类型 | 修改文件 |
|---------|---------|
| CSS 值错 | `$SKILL_DIR/scripts/lib/css_extractor.py` |
| 类名冲突 | `$SKILL_DIR/scripts/lib/normalize_classes.py` |
| 文件格式 | `$SKILL_DIR/scripts/lib/tsx_generator.py` |
