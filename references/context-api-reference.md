# figma-context API Reference

Base URL: `http://127.0.0.1:$FIGMA_CONTEXT_PORT`（默认 `7181`）

All responses are `application/json; charset=utf-8` unless noted.

---

## GET /health

Liveness probe. Cheap.

**Response 200:**
```json
{
  "ok": true,
  "service": "figma-context",
  "version": "0.1.0"
}
```

**Curl:**
```bash
curl -sS http://127.0.0.1:7181/health
```

---

## GET /overview

整份设计稿的节点索引 + 缩略图 URI（相对路径，需拼接 base URL）。返回 root + 最多 2 层深度的直接子节点，避免 IR 全量污染 agent 上下文。

**Query params:** 无

**Response 200:**
```json
{
  "fileKey": "abc123",
  "rootNodeId": "1:1",
  "nodeCount": 47,
  "nodes": [
    {
      "id": "1:1",
      "name": "Page",
      "type": "CANVAS",
      "depth": 0,
      "tags": [],
      "bounds": { "x": 0, "y": 0, "w": 1440, "h": 900 },
      "thumbnail": "/screenshot/1:1?w=160"
    },
    {
      "id": "1:23",
      "name": "红包卡片",
      "type": "FRAME",
      "depth": 1,
      "tags": ["component-instance"],
      "bounds": { "x": 100, "y": 200, "w": 300, "h": 400 },
      "thumbnail": "/screenshot/1:23?w=160"
    }
  ]
}
```

**Errors:**
- `500` — `ir.json` unreadable

**Curl:**
```bash
curl -sS http://127.0.0.1:7181/overview | jq
```

---

## GET /context/:nodeId

单节点的分层上下文（layout + CSS + 子节点分类）。核心降噪 API。

**Path params:**
- `nodeId` — Figma 节点 ID（形如 `1:23`；URL 里冒号可保留）

**Query params:**
- `depth`（default `1`）— 返回多深的子节点。`1` = 只到直接子节点（不含孙节点）；`2` = 也返回一层孙节点

**Response 200:**
```json
{
  "id": "1:23",
  "name": "红包卡片",
  "type": "FRAME",
  "tags": ["component-instance"],
  "layout": "flex column gap:12px",
  "inferredFlex": {
    "direction": "column",
    "gap": 12,
    "padding": "24px 16px",
    "confidence": "high"
  },
  "css": {
    "width": "300px",
    "height": "400px",
    "background": "linear-gradient(180deg, #FFC 0%, #F80 100%)",
    "borderRadius": "16px",
    "padding": "24px 16px"
  },
  "bounds": { "x": 100, "y": 200, "w": 300, "h": 400 },
  "children": [
    {
      "id": "1:24",
      "name": "背景纹理",
      "type": "VECTOR",
      "layerKind": "vector",
      "tags": ["likely-decorative"],
      "bounds": { "x": 0, "y": 0, "w": 300, "h": 400 }
    },
    {
      "id": "1:25",
      "name": "标题文字",
      "type": "TEXT",
      "layerKind": "text",
      "tags": ["keep-text"],
      "bounds": { "x": 24, "y": 24, "w": 252, "h": 26 },
      "css": {
        "color": "var(--color-brand-500)",
        "fontSize": "18px",
        "fontWeight": "600"
      }
    },
    {
      "id": "1:26",
      "name": "领取按钮",
      "type": "INSTANCE",
      "layerKind": "component",
      "tags": ["component-instance"],
      "bounds": { "x": 24, "y": 340, "w": 252, "h": 44 }
    }
  ]
}
```

**Tag meanings:**
- `likely-decorative` — 纯装饰节点（vector / shape / gradient），子节点无 TEXT
- `keep-text` — TEXT 节点或包含 TEXT 后代
- `component-instance` — Figma INSTANCE 类型
- `mask-container` — 含蒙版子节点

**Errors:**
- `404` — nodeId 不在 IR 中
- `500` — 内部读取失败

**Curl:**
```bash
curl -sS 'http://127.0.0.1:7181/context/1:23?depth=1' | jq
curl -sS 'http://127.0.0.1:7181/context/1:23?depth=2' | jq   # 含孙节点
```

---

## GET /screenshot/:nodeId

调 Figma Images API 导出单节点 PNG，磁盘缓存后返回图片流。

**Path params:**
- `nodeId` — Figma 节点 ID

**Query params:**
- `w`（可选）— 请求宽度（当前只影响缓存文件名；实际 scale 固定 `2`。TODO: 根据 IR bounds 换算 scale）

**Response 200:** `image/png` binary。

**Errors:**
- `500` — Figma API 无返回 / token 缺失 / 网络失败

**Curl:**
```bash
# 拉回 PNG 存本地
curl -sS 'http://127.0.0.1:7181/screenshot/1:23?w=800' -o card.png
```

**缓存路径**：`.figma-to-code/5-context-server/{cacheDir}/screenshot/{safeNodeId}_{w}.png`

---

## POST /composite

**⚠️ v0.1 STUB：返回 501。Playwright 集成推迟到 v0.2。**

**Request body:**
```json
{
  "nodeIds": ["1:24", "1:27", "1:28"],
  "format": "png"
}
```

**Response 501 (v0.1):**
```json
{
  "error": "composite API requires Playwright integration; not implemented in v0.1",
  "hint": "call GET /screenshot/{nodeId} for each node individually and stack them manually. See references/coding-agent-guide.md.",
  "phase": "v0.2",
  "status": 501
}
```

**Response 200 (v0.2, planned):**
```json
{
  "path": ".figma-to-code/5-context-server/{fileKey}-1:23/composite/abcdef.png",
  "size": { "w": 300, "h": 400 }
}
```

**Errors:**
- `400` — `nodeIds` 不是 string 数组，或 `format` 不合法
- `501` — v0.1 stub

**Curl:**
```bash
curl -sS -X POST http://127.0.0.1:7181/composite \
  -H 'Content-Type: application/json' \
  -d '{"nodeIds":["1:24","1:27"],"format":"png"}' | jq
```

---

## Error format

所有错误响应均为：
```json
{ "error": "<human message>" }
```

外加相应 HTTP status code。

## Common workflow

1. `GET /overview` → 定位目标模块 nodeId
2. `GET /context/{nodeId}?depth=1` → 拿到布局 + CSS + 子节点分类
3. 对每个 `likely-decorative` 子节点：`GET /screenshot/{childId}` 拉图（v0.2 后可用 `POST /composite`）
4. 结合项目 CSS 规范写代码
5. 完成后：`GET /context/{childId}` 复查值是否与实现一致
