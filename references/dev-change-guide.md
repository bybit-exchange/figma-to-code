# figma-to-code Skill 开发变更规范

任何对 `scripts/` 下逻辑的修改，**必须按以下顺序执行**，不得跳步。

---

## 测试工具体系

| 工具 | 职责 | 何时用 | 是否需要 Figma |
|------|------|--------|--------------|
| `python3 tests/test_all.py` | 算法单元测试（多 suite，无外部依赖） | **改了任何脚本后必跑**，秒级完成 | ❌ |
| `python3 tests/test_product.py` | 产物契约测试（文件结构/语法）+ 特定设计稿 CSS 回归断言 | 已有产物，想补充验证内容正确性 | ❌ |
| `python3 tests/test_pipeline.py` | 中间产物完整性（IR / plan.json / import 路径可解析） | 已有产物，想验证 pipeline 各阶段有没有跑完 | ❌ |
| `python3 tests/test_visual.py` | 视觉回归测试：convert → split → sandbox → Playwright 截图 → HTML 报告 | 跑视觉对比，或大版本迭代后验证整体还原度 | ✅ |
| `bash scripts/run_cross_test.sh 'URL'` | 端到端算法验证：单元测试 → Figma API → convert → split-auto → validate | 改了脚本后想用真实设计稿跑全链路，或 CI 冒烟测试 | ✅ |

> ⚠️ `run_cross_test.sh` 只覆盖**可自动化的算法步骤**。完整流程（AI 语义重命名、产物落地、截图对比）需真实运行 `/figma-to-code <URL>`。

**`test_visual.py` 用法：**

```bash
# 从 tests/visual/cases.json 运行所有配置的设计稿（fast 模式，跳过 Code Connect）
python3 scripts/tests/test_visual.py

# 指定单个 URL（可重复 --url 指定多个）
python3 scripts/tests/test_visual.py --url='https://www.figma.com/design/...'

# 从缓存 IR 重建，跳过 Figma API（适合脚本迭代验证）
python3 scripts/tests/test_visual.py --from-ir

# 完整模式（含 Code Connect 组件映射，需预先有 CC 数据）
python3 scripts/tests/test_visual.py --full

# 保留临时 sandbox 目录供调试
python3 scripts/tests/test_visual.py --url='...' --keep
```

报告输出到 `.figma-to-code/4-test-output/*.html`，日志同目录 `*.log`。  
视觉测试依赖：`playwright`（截图）、`pillow`（diff）、`pnpm`（sandbox）、`FIGMA_TOKEN`（Figma API）。

---

## 变更流程

```
1. 写/更新单测  →  2. 改代码  →  3. 跑单测  →  4. 全部通过  →  5. 验证产物
```

### 1. 先写（或更新）单测

- 根据修改的模块，在对应的测试文件中追加 `U-*` 测试：
  - `css_extractor.py` → `scripts/tests/extract/test_css_extractor.py`
  - `ir_builder.py` → `scripts/tests/extract/test_ir_builder_unit.py`
  - `convert.py` patches → `scripts/tests/transform/test_convert_patches.py`
  - `token_resolver.py` / 主题检测 → `scripts/tests/transform/test_token_resolver.py`
- 用 `check('U-NNN', '描述', <表达式>)` 写法（从 `tests.helpers` 导入 `check`）。
- 测试命名：下一个编号 = 现有最大 `U-*` 编号 + 1。
- 新测试必须先**跑一次确认是 FAIL 状态**，证明你在测真实行为，而不是空测。

#### 单测数据：来自真实排查，但自包含在测试文件中

**数据来源**：每条 bug 修复的单测，必须使用**触发该 bug 的真实 Figma 节点数据**——即排查问题时实际用到的 IR 结构、CSS 属性、CC snippet 等，而不是手工捏造一个"差不多"的样例。真实数据能重现具体的边界条件，捏造数据容易漏掉触发 bug 的关键字段。

**自包含要求**：数据必须**硬编码在测试文件中**，不得通过路径、import 或文件读取引用项目中的任何外部文件：
- 不得引用 `.figma-to-code/` 目录下的任何产物或中间文件
- 不得引用 `src/pages/` 中的任何页面文件
- 不得依赖本地环境中的特定数据文件存在

目的：任何人在任何机器上执行 `python3 scripts/tests/test_all.py`，无需项目数据即可完整复现。

**如何提取数据**：从排查 bug 时的 IR JSON 中拷贝触发问题的完整节点片段，粘贴到 test case 中，保留所有与判断条件相关的字段，并在注释中标注来源：

```python
# Real data: node 123:456 from PageName (nodeId: 1-234)
node = make_node(
    type="FRAME",
    fills=[{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1, "a": 1}}],
    # ... 其余从真实 IR 中提取的字段
)
```

### 2. 改代码

- 修改 `scripts/lib/` 或 `scripts/convert.py` 里的逻辑。
- **不要**在改代码前就让测试通过——测试先红，代码后绿。

### 3. 跑单测

```bash
python3 scripts/tests/test_all.py
```

只要输出包含 `❌` 或 `FAIL`，必须修复后再继续。也可单独跑修改对应的测试文件加快反馈：

```bash
python3 scripts/tests/extract/test_css_extractor.py      # CSS 提取相关
python3 scripts/tests/extract/test_ir_builder_unit.py     # IR 构建相关
python3 scripts/tests/transform/test_convert_patches.py   # convert.py patches
python3 scripts/tests/transform/test_token_resolver.py    # token/主题相关
```

### 4. 全部通过

输出必须以 `✅  全部通过 (N/N)` 结尾，N 必须等于变更前数量 + 新增测试数量。

如果改动破坏了已有测试，说明有回归，**不允许提交**，需先修复。

### 5. 验证产物并落地

用受影响的 Figma 设计稿重建产物，确认 `.figma-to-code/3-page-code/` 输出符合预期：

```bash
# 改了 css_extractor / ir_builder → 必须从 raw-data 重建（IR 是 extractor 产物缓存）
python3 scripts/convert.py '<Figma URL>' --from-raw-data --css-ext=less

# 改了 convert.py patches / tsx_generator → 可从 IR 重建（不涉及 extractor）
python3 scripts/convert.py '<Figma URL>' --from-ir --css-ext=less

# 跑 Layer 0~2 校验
python3 scripts/validate.py '<Figma URL>' --css-ext=less
```

校验分数 ≥ 90 后，**必须将重建的产物落地到 `$TARGET_DIR/$DEST_DIR/`**：

```bash
# 必须先重新生成 split 中间产物（convert.py 重建后 stage/ 已是旧版，--apply 直接运行会落地旧代码）
python3 scripts/split_components.py --node-id={nodeId} --auto --css-ext=less
# 若有已有重命名方案（renames.json），复用：
# python3 scripts/split_components.py --node-id={nodeId} --apply-renames=renames.json
# 落地到项目（单稿 / 首次生成）
python3 scripts/split_components.py --node-id={nodeId} --apply --dest=$TARGET_DIR/$DEST_DIR --css-ext=less
```

**P1.4-Ind 双稿独立模式重建落地（pc/ 或 h5/ 目录已存在时）**：
使用 `--exact-dest` 直接指定目标路径，路径重命名在 `--apply` 内部完成，无需临时目录绕路：

```bash
# PC 端落地到 pc/（路径重命名在 --apply 内部自动完成）
python3 scripts/split_components.py --node-id={PC_nodeId} --apply \
  --exact-dest=$TARGET_DIR/$DEST_DIR/{PageName}/pc --css-ext=less

# H5 端落地到 h5/
python3 scripts/split_components.py --node-id={H5_nodeId} --apply \
  --exact-dest=$TARGET_DIR/$DEST_DIR/{PageName}/h5 --css-ext=less
```

> ⚠️ **禁止从 stage 直接复制**：stage 里的资源路径尚未经过 `--apply` 的路径重命名（保留节点 ID 格式，如 `/assets/H5-39603-19204/`），直接复制会导致所有图片 404。正确落地方式必须经过 `--apply` 或 `--apply --exact-dest`。

落地后在浏览器中验证页面渲染正确，确认无误后提交。

---

## 逻辑变更时的单测维护规则

| 变更类型 | 单测要求 |
|---------|---------|
| 新增逻辑分支 | 为每个新分支补至少 1 条 `U-*` 测试 |
| 修改已有逻辑 | 更新对应的 `U-*` 测试，确保描述和断言与新行为一致 |
| 删除逻辑 | 同步删除对应的 `U-*` 测试（过时测试会掩盖真实回归） |
| 重命名/重构 | 测试计数不变，`check()` 的描述同步更新 |

---

## 测试文件位置

### 算法单元测试（test_all.py）

| 目录/文件 | 作用 |
|-----------|------|
| `scripts/tests/test_all.py` | **单元测试入口**，串联所有 36 个 suite，无外部依赖 |
| `scripts/tests/helpers.py` | 共享基础设施：`check()`, `make_node()`, `make_ctx()` |
| `scripts/tests/extract/test_css_extractor.py` | CSS 提取单元测试 |
| `scripts/tests/extract/test_ir_builder_unit.py` | IR 构建单元测试 |
| `scripts/tests/extract/test_ir_builder_existing.py` | IR 构建原有回归测试 |
| `scripts/tests/extract/test_adaptive.py` | 自适应 CSS 测试 |
| `scripts/tests/transform/test_convert_patches.py` | convert.py patches 测试 |
| `scripts/tests/transform/test_token_resolver.py` | Token/主题检测测试 |
| `scripts/tests/transform/test_validate.py` | validate.py 单元测试 |
| `scripts/tests/codegen/test_tsx_generator.py` | TSX 生成测试 |
| `scripts/tests/codegen/test_i18n.py` | 国际化测试 |
| `scripts/tests/codegen/test_split_codegen.py` | Split 代码生成测试 |
| `scripts/tests/split/test_boundary_detector.py` | 组件边界检测 |
| `scripts/tests/split/test_instance_merger.py` | 实例合并 |
| `scripts/tests/split/test_node_classifier.py` | 节点分类 |
| `scripts/tests/split/test_semantic_naming.py` | 语义命名 |
| `scripts/tests/split/test_position_conflict.py` | 位置冲突检测 |
| `scripts/tests/split/test_zindex_layer_order.py` | z-index 层级顺序 |
| `scripts/tests/split/test_moly_wrapper_css.py` | CC wrapper CSS 剥离 |
| `scripts/tests/split/test_cc_leaf_and_variant.py` | CC 叶子组件 + variant |
| `scripts/tests/split/test_token_revert_and_wrapper.py` | Token 反转 + wrapper |
| `scripts/tests/split/test_apply_landing.py` | 拆分落地集成 |
| `scripts/tests/split/test_section_root_strip.py` | Section 根节点处理 |
| `scripts/tests/split/test_inline_root_strip.py` | Inline 根节点处理 |
| `scripts/tests/split/test_image_composition.py` | 图片合成检测 |
| `scripts/tests/split/test_validate_split_layer1.py` | validate_split Layer 1 |
| `scripts/tests/split/test_validate_split_layer5.py` | validate_split Layer 5 |
| `scripts/tests/split/test_leaf_css_strip.py` | Leaf CSS 剥离 |
| `scripts/tests/split/test_code_connect.py` | Code Connect 解析 + Snippet Props 提取 |
| `scripts/tests/split/test_props_inference.py` | Props 推断（boolean/variant/text/instance-swap） |
| `scripts/tests/semantic/test_semantic_extractor.py` | 语义提取 |
| `scripts/tests/merge/test_node_matcher.py` | 节点匹配算法 |
| `scripts/tests/merge/test_css_differ.py` | CSS 差分 |
| `scripts/tests/merge/test_h5_overlay.py` | H5 overlay 合并 |
| `scripts/tests/merge/test_scss_responsive.py` | SCSS 响应式生成 |
| `scripts/tests/merge/test_struct_scorer.py` | 结构打分 |
| `scripts/tests/merge/test_split_dual_codegen.py` | 双端代码生成 |
| `scripts/tests/merge/test_integration.py` | 合并集成 |

### 产物验证测试（需要先运行 convert.py）

| 文件 | 作用 | 命令 |
|------|------|------|
| `scripts/tests/test_product.py` | 产物契约（文件结构/语法）+ 特定设计稿 CSS 回归断言 | `python3 scripts/tests/test_product.py` |
| `scripts/tests/test_pipeline.py` | 中间产物完整性：IR / plan.json / import 路径可解析 | `python3 scripts/tests/test_pipeline.py` |

### 全链路 E2E（需要 Figma API）

```bash
bash scripts/run_cross_test.sh 'https://www.figma.com/design/...'
```

**全量单元测试命令**（改了 `lib/` 或 `convert.py` 时必跑）：

```bash
python3 scripts/tests/test_all.py
```

---

## 典型变更示例

### 修改 `detect_page_theme` 逻辑

```
1. 在 tests/transform/test_token_resolver.py 末尾加 U-* 测试
2. 确认新测试 FAIL（旧实现跑不过）
3. 修改 tsx_generator.py 的 detect_page_theme 函数
4. python3 scripts/tests/test_all.py → 全绿
5. 用受影响页面重建产物并目测验证主题正确
```

### 修改 CSS 提取规则

```
1. 在 tests/extract/test_css_extractor.py 末尾加 U-* case（先红）
2. 修改 css_extractor.py
3. python3 scripts/tests/extract/test_css_extractor.py → 全绿
4. python3 scripts/convert.py '<URL>' --from-raw-data  # 注意：改了 extractor 必须从 raw-data 重建
5. python3 scripts/validate.py '<URL>' → 分数 ≥ 90
```

### 修改 ir_builder 后处理逻辑

```
1. 在 tests/extract/test_ir_builder_unit.py 末尾加 U-* case（先红）
2. 修改 ir_builder.py
3. python3 scripts/tests/extract/test_ir_builder_unit.py → 全绿
4. python3 scripts/convert.py '<Figma URL>' --from-raw-data
5. python3 scripts/validate.py '<Figma URL>' → 分数 ≥ 90
```

### 修改 convert.py patches

```
1. 在 tests/transform/test_convert_patches.py 末尾加 U-* case（先红）
2. 修改 convert.py 对应 patch
3. python3 scripts/tests/transform/test_convert_patches.py → 全绿
4. python3 scripts/convert.py '<Figma URL>' --from-raw-data
5. python3 scripts/validate.py '<Figma URL>' → 分数 ≥ 90
```

---

## 铁律（不可违反）

### 1. 禁止直接修改任何生成产物（含中间产物）

所有 bug 修复**必须在脚本（`scripts/lib/`、`scripts/convert.py`）中完成**，不得直接修改以下任何文件——它们全部是生成产物，手动修补在下次重建时会被覆盖，等于没修：

| 禁止直接修改的路径 | 应修复的脚本层 |
|---|---|
| `.figma-to-code/2-ir/*.json`（IR） | `css_extractor.py` / `ir_builder.py` |
| `.figma-to-code/2-ir/*-merged.json`（merged IR） | `scripts/merge/` 相关脚本 |
| `.figma-to-code/3-page-code/`（页面代码） | `convert.py` / `split_components.py` |
| `src/pages/`（落地产物） | 同上 |

修复路径：定位脚本中的根因 → 写测试 → 改脚本 → 重建 → 落地。

### 2. 修当前页面，立即重建落地验证

修复完一个页面的问题后，**立即重建该页面并落地到 `$TARGET_DIR/$DEST_DIR/`**，验证产物正确。

**没有用户明确指令，不要全量重建所有页面。** 全量重建耗时且可能引入其他页面的意外回归，只处理当前正在修的页面。

### 3. 单测：来自真实排查数据，且自包含独立于项目

这是两个相互配合但独立的约束：

**约束 A — 数据必须来自真实问题排查**

单测数据不能凭空捏造，必须使用**排查该 bug 时实际观察到的 Figma 节点数据**（IR 结构、CSS 属性、CC snippet 等）。捏造数据往往遗漏触发 bug 的关键字段，导致测试通过但 bug 依然存在。

- 从 IR JSON 中提取触发 bug 的**完整节点数据**（含所有参与判断的字段）
- CC 相关测试需要同时内联真实的 CC JSON 数据
- 白名单/配置类修改（如 `_MOLY_WRAPPER_POSITIONING_PROPS`）需同时内联真实的 `orig_css_map` 数据
- 每个 test case 注明来源：`# Real data: node {figmaId} from {page_name} ({nodeId})`

**约束 B — 测试必须自包含，独立于项目**

所有测试数据必须**硬编码在测试文件内部**，禁止通过路径、import 或文件读取引用任何项目文件：

- 禁止引用 `.figma-to-code/` 下的任何文件（产物、IR、raw-data）
- 禁止引用 `src/pages/` 中的任何页面文件
- 禁止依赖本地特定路径或环境变量指向的项目数据

目的：任何人在任何环境下执行 `python3 scripts/tests/test_all.py` 都能复现，不依赖本地项目数据。

**注意**：修改白名单/配置后，检查是否有现有测试断言与新行为冲突（如 `wrapper strips X` → `wrapper keeps X`），同步更新断言描述。
