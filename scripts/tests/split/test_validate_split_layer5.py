#!/usr/bin/env python3
"""
test_validate_split_layer5.py — validate_split _run_layer_5_per_node_css 单元测试

覆盖：
  U-401: merged-* 节点 ID 时 Layer 5 不报 warning（merged IR 无对应原始单组件目录，为结构性限制）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()


def test_layer_5_skips_warning_for_merged_node_id():
    """U-401: merged-* 节点 ID 的 Layer 5 应静默跳过，不报 warning。
    Before fix: '找不到原始单组件目录，跳过 Layer 5 CSS 对比' = -1 pt warning.
    After fix: returns [] for merged-* nodes (no original single-component dir exists).

    Real data: merged-181-2980 (PC 181:2980 + H5 6:1998 merged Trump活动 IR).
    The merged IR's single-component directory is structural N/A — there is no
    src/pages/merged-181-2980-*/ directory to compare against.
    """
    from validate_split import _run_layer_5_per_node_css

    with tempfile.TemporaryDirectory() as tmpdir:
        page_dir = Path(tmpdir) / 'TrumpActivity' / 'components'
        page_dir.mkdir(parents=True)
        # Real data: merged node ID from Trump活动 P1.4 merge (merged-181-2980)
        issues = _run_layer_5_per_node_css(page_dir, page_dir.parent, 'merged-181-2980')
        check('U-401', 'merged-* node ID must return no issues from Layer 5',
              len(issues) == 0)


print('\n── validate_split Layer 5 tests ──')
test_layer_5_skips_warning_for_merged_node_id()
print_summary('split/test_validate_split_layer5')
