# 多 URL 并行转换指引

多个 Figma URL 时，在**同一条消息**中为每个 URL 并行派发独立 subagent，各自执行完整的 **P1（含 Code Connect）+ P1.5 + P2 + P3**。

> ⚠️ P3（落地 & 验证）是 subagent 职责的一部分，**不得留给主 Agent 事后补做**。
> ⚠️ Code Connect MCP 调用是 P1 的必要步骤，**不得跳过**——缺少 CC 数据时 split 无法识别项目组件库组件。

## Subagent Prompt 模板

将占位符替换后派发（每个 URL 一个 subagent）：

```
执行 figma-to-code 单页转换（含落地）：
- Figma URL: {{FIGMA_URL}}
- FILE_KEY:  {{FILE_KEY}}    （从 URL /design/后提取的 key）
- NODE_ID:   {{NODE_ID}}     （冒号格式，如 169:33787）
- NODE_ID_SAFE: {{NODE_ID_SAFE}} （横线格式，如 169-33787）
- SKILL_DIR: {{SKILL_DIR}}
- CSS_EXT:   {{CSS_EXT}}
- DEST_DIR:  {{DEST_DIR}}    （代码落地目标，如 src/pages）
- ASSETS_DIR: {{ASSETS_DIR}} （资源落地目标，如 public/assets）
- TARGET_DIR: {{TARGET_DIR}}

步骤：
1. [P1-转换] 运行转换脚本：
   python3 {{SKILL_DIR}}/scripts/convert.py '{{FIGMA_URL}}' --css-ext={{CSS_EXT}} --split

2. [P1-CC] **（与步骤1并行执行）** 查询 Code Connect 并保存：
   a. 用 ToolSearch 加载 mcp__plugin_figma_figma__get_code_connect_map 工具
   b. 调用：mcp__plugin_figma_figma__get_code_connect_map(fileKey="{{FILE_KEY}}", nodeId="{{NODE_ID}}", codeConnectLabel="Web")
   c. 将返回的 JSON 保存到 .figma-to-code/2-figma-extract/{{NODE_ID_SAFE}}.code-connect.json：
      python3 -c "
      import json, sys; from pathlib import Path
      sys.path.insert(0, '{{SKILL_DIR}}/scripts')
      from lib.code_connect import parse_mcp_response, save_code_connect_json
      data = json.loads('''<MCP返回的完整JSON>''')
      parsed = parse_mcp_response(data)
      save_code_connect_json(parsed, Path('.figma-to-code/2-figma-extract/{{NODE_ID_SAFE}}.code-connect.json'))
      "
   d. 若 MCP 调用失败/超时 → 写入 {} 并继续（P1.5 会降级为 div 渲染）

3. [P1.5-拆分] 等步骤1+2都完成后，运行组件拆分（整页模式）：
   python3 {{SKILL_DIR}}/scripts/split_components.py --node-id={{NODE_ID_SAFE}} --auto --css-ext={{CSS_EXT}} --dest={{TARGET_DIR}}/{{DEST_DIR}}
   验证：python3 {{SKILL_DIR}}/scripts/validate_split.py --node-id={{NODE_ID_SAFE}} --css-ext={{CSS_EXT}}

3.5. [AI 重命名 pass] 读取 .figma-to-code/3-page-code/*-{nodeId}/split-a/plan.naming.json：
   - 若 sections/subSections/leaves/props 均为空 → 跳过
   - 若非空 → 根据 figmaName、firstTexts、varyingPropNames/sampleValues 推断语义名，写入 renames.json
     注意：subSections 中同名条目（如两个 Section1 在不同父节点下）不得写入，防止全局错误匹配
   - 执行：
     python3 {{SKILL_DIR}}/scripts/split_components.py --node-id={{NODE_ID_SAFE}} --apply-renames=renames.json
     python3 {{SKILL_DIR}}/scripts/split_components.py --node-id={{NODE_ID_SAFE}} --apply --dest={{TARGET_DIR}}/{{DEST_DIR}} --css-ext={{CSS_EXT}}
     python3 {{SKILL_DIR}}/scripts/validate_split.py --node-id={{NODE_ID_SAFE}} --css-ext={{CSS_EXT}}
   - 若 page 发生重命名，在最终 JSON 中报告旧目录路径，提示用户手动删除

4. [P2] 运行校验：python3 {{SKILL_DIR}}/scripts/validate.py '{{FIGMA_URL}}' --css-ext={{CSS_EXT}}
5. 若校验评分 < 90，按 SKILL.md P2 修复指引处理后重跑，最多 3 轮
6. [P3-代码] 校验通过后，确认代码已落地（split_components.py --apply 在步骤3.5已落地，目录名不含 nodeId）：
   ls {{TARGET_DIR}}/{{DEST_DIR}}/{ComponentName}/ && echo "✓ 落地确认" || echo "⚠️ 目录不存在，请检查步骤3.5 --apply"
7. [P3-资源] 落地资源目录（必须执行，即使目录看起来很小）：
   [ -d ".figma-to-code/1-assets/{ComponentName}" ] && \
     cp -rf ".figma-to-code/1-assets/{ComponentName}" {{TARGET_DIR}}/{{ASSETS_DIR}}/ && \
     echo "资源落地完成" || echo "无资源目录，跳过"
8. [P3-验证] 确认两个目录均已存在后，输出最终 JSON 结果

最终输出 JSON（assetsPath 字段必须填写，无资源写 "none"）：
{
  "task": "{{TASK_NAME}}",
  "componentName": "...",
  "outputPath": "{{DEST_DIR}}/{ComponentName}/",
  "assetsPath": "{{ASSETS_DIR}}/{ComponentName}/ (N files)" | "none",
  "missingAssets": [],
  "pageTheme": "dark" | "light",
  "score": 99,
  "timing": { "p1_convert_ms": ..., "p2_validate_ms": ..., "total_ms": ... },
  "status": "success" | "failed",
  "failReason": null
}
```

### FILE_KEY 和 NODE_ID 提取规则

从 Figma URL 提取（主 Agent 必须在派发前完成提取，不要让 subagent 自己解析）：

```
URL 格式: https://www.figma.com/design/{FILE_KEY}/{FileName}?node-id={NODE_ID_DASH}&m=dev
FILE_KEY: URL 中 /design/ 后的第一段路径
NODE_ID: URL 中 node-id 参数值，将 - 替换为 : （如 169-33787 → 169:33787）
NODE_ID_SAFE: 保持 - 格式（如 169-33787）
```

## 主 Agent 职责

### 派发前（必须由主 Agent 完成）：
1. 从每个 Figma URL 中预先提取 `FILE_KEY`、`NODE_ID`（冒号格式）、`NODE_ID_SAFE`（横线格式）
2. 将提取的值填入 subagent prompt 模板——subagent 不应自己解析 URL
3. 确保所有 subagent 被告知需要调用 Code Connect MCP 工具

### 派发后：
1. 汇总所有 subagent 的 JSON 结果
2. 检测组件名冲突（见下方）
3. 若有 subagent 遗漏 P3 或 CC 步骤，补执行并记录为 bug
4. 验证产物中 Code Connect 组件引用数量 > 0（CC 生效标志，若项目无 CC 配置则跳过此验证）

## 同名组件冲突

多个 Figma 节点可能生成相同的组件名（如两个节点都叫 `Card`）。并行写入时，后完成的 subagent 会静默覆盖先完成的输出。

**检测方式**：各 subagent 完成后，对比各自报告的组件名，有重复时暂停并询问用户：
- 保留哪个版本？
- 各自加前缀区分（如 `HomeCard` / `ProfileCard`）？

## 结果汇总格式

所有 subagent 完成后，主 Agent 汇总输出（包含资源路径）：

```
✓ ComponentA → src/pages/{nodeId}-ComponentA/   资源: public/assets/ComponentA/ (32 files)   评分: 96
✓ ComponentB → src/pages/{nodeId}-ComponentB/   资源: none                                    评分: 91
✗ ComponentC → src/pages/{nodeId}-ComponentC/   资源: ⚠️ 未落地                               评分: 78，剩余偏差：[具体项]
```
