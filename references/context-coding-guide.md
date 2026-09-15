# Coding Agent Guide — Using figma-context

**给 Coding Agent 的 4 条硬规则**。首次调用 `figma-context` 时先读这份。

---

## 1. 不生成代码

`figma-context` **只提供上下文**。要写 TSX / CSS / Less，是 agent 结合项目仓库自己决定的：

- earn-web → Emotion + `styled.div`
- applet-earn → CSS Modules + `.module.less`
- 其它项目 → 遵循该项目自身的样式方案

**绝不要**在 `figma-context` 的响应里期待"给我一个 TSX 组件"。如果需要一键生成完整 TSX + CSS，改用 `/figma-to-code`。

## 2. 装饰优先合图

Figma 里的装饰层往往有 5～20 层（渐变 + 光晕 + 花纹 + 阴影 + 高光…）。**用 CSS 逐层还原不划算**：

- 代码可读性差
- 性能开销大（浏览器合成多层）
- 与设计稿差异肉眼可见

**正确做法**：
- 先看 `GET /context/{parentId}?depth=1` 的 `children` 数组，筛出 `tags` 含 `likely-decorative` 的节点
- v0.2 起：`POST /composite {"nodeIds":[...]}` 拿到一张合成 PNG，作为 `background-image`
- v0.1（当前）：`POST /composite` 返回 501。退回 fallback：
  - 要么对每个装饰节点单独 `GET /screenshot/{id}`，用多个 `<img>` 堆叠
  - 要么与用户协商，接受"暂时用一张 mock 截图"或"手动 PS 合并"

## 3. 内容优先 CSS

**能用 CSS 表达的东西 → 用 CSS**：

- 文字（`color` / `fontSize` / `fontWeight` / `lineHeight`）
- 布局（flex / grid / gap / padding）
- 尺寸（`width` / `height`）
- 圆角、简单渐变、简单阴影

**能用 CSS 但值不确定时 → 从 API 取**，不许目测截图。

## 4. 所有值必须来自 API

**禁止**：
- 目测截图取 `#F87800` → 错。用 `curl /context/{id}` 拿 `css.background` 里的十六进制
- 目测字号"看着像 18px" → 错。用 `curl /context/{id}` 拿 `css.fontSize`
- 目测间距"大概 12px" → 错。用 `inferredFlex.gap`

**做法**：拿不准就再 `curl 一次` `/context/{id}`。API 廉价，agent 上下文昂贵。

---

## 5.（加分项）迭代查询

写完组件后，可以再 `GET /context/{childId}` 复查每个子节点的 CSS 值是否与你写入代码的一致。发现差异立即修正。

**典型场景**：
- 副标题字重记成了 500，API 返回 400 → 修
- 卡片圆角写了 12px，API 返回 16px → 修
- 装饰层的 y 偏移和实现里 `top: 10px` 对不上 → 查 IR 的 bounds，改成正确的偏移

---

## 6. 什么时候用 `/screenshot`？

- 需要**视觉参考**（如"这个图标长什么样"）
- 需要**装饰节点资源**（v0.1 装饰合图 fallback 时的替代方案）
- 想要在实现完成后**并排对比**（agent 起 Playwright + 拿 Figma screenshot）

**不要**用 screenshot 代替 `/context` — 从图片反推数值不精确。

---

## 7. 什么时候用 `/overview`？

只在**首次定位模块**时用一次。返回可能很大（几百到几千节点），把它当"目录"扫一眼即可，不要反复调。

后续所有精细操作都用 `/context/{nodeId}`。

---

## 8. 与项目仓库的关系

`figma-context` **不知道**你在哪个项目里。它只知道 Figma 的原始数据。

**Agent 的职责**：
- 读项目根目录的 CLAUDE.md（或 `.claude/` 下的规范）
- 决定用什么样式方案、什么组件库
- 把 `figma-context` 的 CSS 值翻译成项目规范里的对应写法（如 `var(--color-brand-primary)` 而非硬编码 `#FFA500`）

如项目有 design token 规范，**颜色/圆角/字号优先使用 CSS 变量**，见项目根目录 CLAUDE.md 的"样式生成规范"章节。
