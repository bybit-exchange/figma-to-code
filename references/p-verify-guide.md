# P-Verify — Dev Server 视觉验证

> 目标：确认组件在真实浏览器环境中与设计稿高度一致，排查宿主环境对产物的干扰。

## 检查清单（按顺序执行，全部通过后再截图对比）

**① 全局 CSS 污染检查**

读取项目的全局样式入口文件（`src/index.css`、`src/App.css`、`src/styles/global.css` 等），检查是否存在以下干扰项：

| 问题 | 典型症状 | 修复方式 |
|------|---------|---------|
| `#root` 设置了固定 `width` | 页面内容被截断、右侧内容消失 | 改为 `width: 100%` |
| `#root` 有 `text-align: center` | 所有文字/元素居中对齐 | 移除 |
| `#root` 有 `display: flex; flex-direction: column` | 破坏组件内部 flex 布局 | 移除 |
| `body` 有非零 `margin`/`padding` | 页面出现意外留白 | 清零 |
| 全局 `box-sizing` 未统一 | 尺寸计算错误 | 添加 `*, *::before, *::after { box-sizing: border-box }` |

**发现污染时**：将全局 CSS 精简为最小 reset，仅保留 `body { margin: 0 }` 和 `#root { width: 100% }`，**不得改动组件自身的样式文件**。

**② data-theme 验证**

`data-theme` 由 `usePageEnv` 动态注入，无需检查 `index.html`。验证页面入口调用参数正确即可：

**单稿 / 合并模式**：
```bash
grep "usePageEnv(" "$TARGET_DIR/$DEST_DIR/{ComponentName}/index.tsx"
```

**双稿独立模式**：各端入口各自管理 theme，分别检查：
```bash
grep "usePageEnv(" "$TARGET_DIR/$DEST_DIR/{PageName}/pc/index.tsx"
grep "usePageEnv(" "$TARGET_DIR/$DEST_DIR/{PageName}/h5/index.tsx"
```

- 有正确主题参数 → ✓
- 无 `usePageEnv` 调用 → 页面入口缺少主题初始化，需补充

**③ 启动 Dev Server**

```bash
PM=$([ -f "$TARGET_DIR/pnpm-lock.yaml" ] && echo pnpm || \
     [ -f "$TARGET_DIR/yarn.lock" ] && echo yarn || echo npm)
PORT=5173
cd "$TARGET_DIR" && $PM run dev -- --port $PORT &
sleep 4
curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT"
```

返回 `200` → 继续；非 `200` → 检查端口占用（`lsof -i :$PORT`）或查看 dev server 输出报错。

**⑤ 视口宽度设置**

从 Figma IR（`.figma-to-code/2-figma-extract/{nodeId}.ir.json`）读取根节点宽度，或直接使用 Figma 截图返回的 `original_width`（通常为 `1440`）。

**双稿独立模式**：需要分两轮验证——先以 PC 视口（通常 `1440`）验证 PC 端，再以 H5 视口（通常 `375`）验证 H5 端。入口组件会根据 `isMobile` 自动切换渲染内容。

```
# PC 端验证
browser_resize width={pc_original_width} height=900

# H5 端验证（独立模式第二轮）
browser_resize width={h5_original_width} height=812
```

**⑥ 资源加载验证**

导航到页面后，用 `browser_network_requests(static=true, filter="assets/")` 检查响应码：

- 全部 `200`/`304` → 通过 ✓
- 存在 `404` → 资源路径错误，检查 `public/assets/` 目录与组件内 `src` 属性是否一致
- 存在 `ERR_CONNECTION_*` → 远程资源网络不可达，参考步骤③本地化

**⑦ 截图对比**

**同时**执行以下两个操作：

1. Playwright 全页截图：`browser_take_screenshot(fullPage=true)`
2. Figma MCP 截图：`get_screenshot(fileKey, nodeId, maxDimension=2048)`

**双稿独立模式**：每轮分别对比——PC 视口下截图与 PC 设计稿对比，H5 视口下截图与 H5 设计稿对比。共执行两轮步骤⑤⑥⑦。

对比两张图，按严重程度分类差异：

| 级别 | 描述 | 处理方式 |
|------|------|---------|
| P0 | 整体布局错乱、主要区块缺失 | 立即回到步骤①重排查 |
| P1 | 单个区块位置/尺寸偏差明显 | 检查该区块 CSS，修复后重截图 |
| P2 | 颜色/字体细节偏差 | 告知用户可能是 design token CSS 未完全加载 |
| 可接受 | 已知缺失 SVG、Figma 不可访问图标 | 在汇报中注明，不阻断流程 |

## 汇报格式

**单稿 / 合并模式**：
```
P-Verify 结果：
✓ 全局 CSS：无污染（已将 index.css 精简为最小 reset）
✓ data-theme：已设置为 "dark"
✓ design token CSS：已加载
✓ Dev Server：http://localhost:5173 (200)
✓ 视口：1440px
✓ 资源加载：72/72 文件正常，2 个 SVG 已知缺失（Figma 端不可访问）
△ 截图对比差异：P2 — 英雄区背景图亮度略低（opacity: 0.3，符合设计稿预期）
```

**双稿独立模式**（两轮验证）：
```
P-Verify 结果（独立模式 — 双端验证）：
✓ 全局 CSS：无污染
✓ design token CSS：已加载
✓ Dev Server：http://localhost:5173 (200)

[PC 端 — 视口 1440px]
✓ data-theme：light（pc/index.tsx usePageEnv('light')）
✓ 资源加载：45/45 正常
✓ 截图对比：无显著差异

[H5 端 — 视口 375px]
✓ data-theme：dark（h5/index.tsx usePageEnv('dark')）
✓ 资源加载：38/38 正常
△ 截图对比差异：P2 — 底部 CTA 按钮圆角偏差 2px
```
