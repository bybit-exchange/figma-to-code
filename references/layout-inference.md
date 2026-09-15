# 布局推断算法

## 核心问题

Figma 无 Auto Layout 的节点使用绝对坐标存储子节点位置，
但 Web 布局应尽量使用 flexbox。

脚本需从坐标模式推断设计意图。

## 决策树

```
父节点有 layoutMode
  → 直接转 flexbox（见 css-mapping.md §一）
  → 跳过推断

父节点无 layoutMode，运行推断：
  子节点有视觉重叠       → 保留 absolute（层叠意图）
  子节点 < 2 个         → 保留 absolute（无法判断方向）
  y 坐标偏差 ≤ 4px      → 尝试推断为 flex row
  x 坐标偏差 ≤ 4px      → 尝试推断为 flex column
  gap 偏差 > 5px        → 保留 absolute（非等间距）
  存在旋转子节点         → 保留 absolute
  推断成功              → 生成 flex CSS，标记 confidence
  推断失败              → position: relative，子节点用 absolute
```

## 常量定义

```typescript
const GAP_TOLERANCE  = 2;   // px 内的间距偏差视为相同 gap
const AXIS_TOLERANCE = 4;   // px 内的坐标偏差视为同轴对齐
const GAP_MAX_VARIANCE = 5; // 超过此值放弃推断，保留 absolute
```

## 重叠检测

```typescript
function hasOverlap(children: FigmaNode[]): boolean {
  for (let i = 0; i < children.length; i++) {
    for (let j = i + 1; j < children.length; j++) {
      const a = children[i], b = children[j];
      if (
        a.x < b.x + b.width  && a.x + a.width  > b.x &&
        a.y < b.y + b.height && a.y + a.height > b.y
      ) return true;
    }
  }
  return false;
}
```

## Flex Row/Column 推断

```typescript
function inferFlexLayout(
  children: FigmaNode[],
  parent: FigmaNode
): InferredFlex | null {

  if (children.length < 2 || hasOverlap(children)) return null;

  // 尝试 row：y 坐标接近
  const byX = [...children].sort((a, b) => a.x - b.x);
  if (maxDeviation(children.map(c => c.y)) <= AXIS_TOLERANCE) {
    const gaps = consecutiveGaps(byX, 'horizontal');
    if (isConsistent(gaps, GAP_TOLERANCE) && maxDeviation(gaps) <= GAP_MAX_VARIANCE) {
      return buildFlexResult('row', byX, gaps, parent);
    }
  }

  // 尝试 column：x 坐标接近
  const byY = [...children].sort((a, b) => a.y - b.y);
  if (maxDeviation(children.map(c => c.x)) <= AXIS_TOLERANCE) {
    const gaps = consecutiveGaps(byY, 'vertical');
    if (isConsistent(gaps, GAP_TOLERANCE) && maxDeviation(gaps) <= GAP_MAX_VARIANCE) {
      return buildFlexResult('column', byY, gaps, parent);
    }
  }

  return null;
}
```

## 从坐标提取 padding 和 gap

```typescript
function buildFlexResult(
  direction: 'row' | 'column',
  sorted: FigmaNode[],
  gaps: number[],
  parent: FigmaNode
): InferredFlex {
  const gap   = Math.round(median(gaps));
  const first = sorted[0];
  const last  = sorted[sorted.length - 1];

  let paddingTop, paddingRight, paddingBottom, paddingLeft;

  if (direction === 'row') {
    paddingLeft   = first.x;
    paddingRight  = parent.width - (last.x + last.width);
    paddingTop    = Math.min(...sorted.map(c => c.y));
    paddingBottom = Math.min(...sorted.map(c => parent.height - c.y - c.height));
  } else {
    paddingTop    = first.y;
    paddingBottom = parent.height - (last.y + last.height);
    paddingLeft   = Math.min(...sorted.map(c => c.x));
    paddingRight  = Math.min(...sorted.map(c => parent.width - c.x - c.width));
  }

  return {
    direction, gap,
    paddingTop:    Math.max(0, Math.round(paddingTop)),
    paddingRight:  Math.max(0, Math.round(paddingRight)),
    paddingBottom: Math.max(0, Math.round(paddingBottom)),
    paddingLeft:   Math.max(0, Math.round(paddingLeft)),
    alignItems:    inferAlignment(sorted, direction),
    justifyContent: detectSpaceBetween(sorted, direction, parent) ? 'space-between' : undefined,
    confidence: maxDeviation(gaps) <= 1 ? 'high' : 'low',
  };
}
```

## 对齐方式推断

```typescript
function inferAlignment(sorted: FigmaNode[], direction: 'row' | 'column'): string {
  if (direction === 'row') {
    const tops    = sorted.map(c => c.y);
    const mids    = sorted.map(c => c.y + c.height / 2);
    const bottoms = sorted.map(c => c.y + c.height);
    if (maxDeviation(tops)    <= AXIS_TOLERANCE) return 'flex-start';
    if (maxDeviation(bottoms) <= AXIS_TOLERANCE) return 'flex-end';
    if (maxDeviation(mids)    <= AXIS_TOLERANCE) return 'center';
    if (sorted.every(c => c.type === 'TEXT'))    return 'baseline';
    return 'flex-start';
  } else {
    const lefts   = sorted.map(c => c.x);
    const centers = sorted.map(c => c.x + c.width / 2);
    const rights  = sorted.map(c => c.x + c.width);
    if (maxDeviation(lefts)   <= AXIS_TOLERANCE) return 'flex-start';
    if (maxDeviation(rights)  <= AXIS_TOLERANCE) return 'flex-end';
    if (maxDeviation(centers) <= AXIS_TOLERANCE) return 'center';
    return 'flex-start';
  }
}
```

## space-between 检测

```typescript
function detectSpaceBetween(
  sorted: FigmaNode[],
  direction: 'row' | 'column',
  parent: FigmaNode
): boolean {
  if (sorted.length < 2) return false;
  const totalChild = sorted.reduce((s, c) => s + (direction === 'row' ? c.width : c.height), 0);
  const freeSpace  = (direction === 'row' ? parent.width : parent.height) - totalChild;
  const expectedGap = freeSpace / (sorted.length - 1);
  const gaps = consecutiveGaps(sorted, direction === 'row' ? 'horizontal' : 'vertical');
  return gaps.every(g => Math.abs(g - expectedGap) <= AXIS_TOLERANCE);
}
```

## 推断结果处理

推断成功后生成的 CSS：

```typescript
if (inferred) {
  css.display       = 'flex';
  css.flexDirection = inferred.direction;
  css.gap           = `${inferred.gap}px`;
  css.padding       = formatPadding(inferred);
  css.alignItems    = inferred.alignItems;
  if (inferred.justifyContent) css.justifyContent = inferred.justifyContent;
  // 标记置信度供 AI Layer 参考
  ir.inferredFlex = { direction: inferred.direction, confidence: inferred.confidence };
}
```

## confidence: 'low' 的处理

写入 IR 后，AI Layer prompt 中加入：

```
若节点的 inferredFlex.confidence === 'low'：
  检查 className 和 htmlTag 是否符合上下文语义
  若推断方向明显不对，可在 semantic 字段中标记 layoutOverride
```

## 不推断的场景

| 场景 | 保持策略 |
|------|---------|
| 有视觉重叠 | `position: relative` + 子节点 `absolute` |
| 子节点 < 2 | `position: relative` + 子节点 `absolute` |
| gap 偏差 > 5px | `position: relative` + 子节点 `absolute` |
| 有旋转子节点 | `position: relative` + 子节点 `absolute` |
| 子节点超出父容器 | `position: relative` + `overflow: visible` + 子节点 `absolute` |
