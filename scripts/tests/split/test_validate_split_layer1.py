#!/usr/bin/env python3
"""
test_validate_split_layer1.py — validate_split _run_layer_1_new 单元测试

覆盖：
  U-321: nodeId 后缀类名（leaf wrapper）的视觉属性在 base 类中 → 不报错
  U-322: nodeId 后缀类名，base 类也没有该属性 → 正常报错
"""
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import check, print_summary, reset

reset()


def _mk_orig_css_map(cls: str, props: dict) -> dict:
    """构造 orig_css_map（原始单文件 CSS 映射）。"""
    return {cls: props}


def _mk_split_css(sections_dir: Path, filename: str, content: str, css_ext: str = 'less'):
    """在 sections_dir 下写入一个 CSS 文件。"""
    path = sections_dir / f'{filename}.module.{css_ext}'
    path.write_text(content)
    return path


def test_nodeid_suffix_class_base_fallback():
    """U-321/322: _run_layer_1_new base class 回退逻辑。

    U-321: 类名 select-field-n10664-26956 在 split CSS 中只有定位属性，
           但 base 类 select-field 有 padding/background-color/border-radius
           → Layer 1 不应报 MISSING（leaf 组件已提供，wrapper 正确 strip）

    U-322: 类名 frame-ghost-n9999-00001 在 split CSS 中只有定位属性，
           base 类 frame-ghost 也没有 background-color
           → Layer 1 应报 MISSING（属性真实丢失）

    Real data: select-field-n10664-26956 from page 10664-26578
    (SelectField leaf component provides visual CSS under .select-field)
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        section_dir = tmp / 'Frame2147223812Section'
        section_dir.mkdir(parents=True)

        # index.module.less: wrapper class 只有定位属性（视觉已在 leaf）
        _mk_split_css(section_dir, 'index', """\
.select-field-n10664-26956 {
  width: 136px;
  height: 40px;
  flex-shrink: 0;
  position: relative;
}

.frame-ghost-n9999-00001 {
  width: 200px;
  position: absolute;
  left: 0px;
  top: 0px;
}
""")

        # SelectField.module.less: base class 有完整视觉属性
        # Real data: .select-field from SelectField leaf (page 10664-26578)
        _mk_split_css(section_dir, 'SelectField', """\
.select-field {
  width: 136px;
  height: 40px;
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
  padding: 12px 12px 12px 12px;
  overflow: hidden;
  flex-shrink: 0;
  background-color: var(--bds-gray-ele-line);
  border-radius: 4px;
  position: relative;
}
""")
        # frame-ghost 的 base 类不存在（无任何 CSS 文件有 .frame-ghost）

        # 原始单文件 CSS：select-field-n10664-26956 有完整属性（含视觉）
        orig_css_map = {
            'select-field-n10664-26956': {
                'width': '136px',
                'height': '40px',
                'display': 'flex',
                'flex-direction': 'row',
                'justify-content': 'space-between',
                'align-items': 'center',
                'padding': '12px 12px 12px 12px',
                'overflow': 'hidden',
                'flex-shrink': '0',
                'background-color': 'var(--bds-gray-ele-line)',
                'border-radius': '4px',
                'position': 'relative',
            },
            'frame-ghost-n9999-00001': {
                'width': '200px',
                'background-color': '#ff0000',  # 真实丢失，base 类也没有
                'position': 'absolute',
                'left': '0px',
                'top': '0px',
            },
        }

        from validate_split import _run_layer_1_new

        issues = _run_layer_1_new(
            page_dir=section_dir,
            page_root=section_dir.parent,
            ir_css_map=orig_css_map,
            css_ext='less',
            node_id_safe='10664-26578',
        )

        issue_msgs = [i['msg'] for i in issues if i['level'] == 'error']

        # U-321: select-field-n10664-26956 的视觉属性不应被报告（base class 有）
        select_errors = [m for m in issue_msgs if 'select-field-n10664-26956' in m
                        and any(p in m for p in ('padding', 'background-color', 'border-radius'))]
        check('U-321', 'select-field-n10664-26956 视觉属性由 base class 提供 → 不报 MISSING',
              len(select_errors) == 0)

        # U-322: frame-ghost-n9999-00001 的 background-color 应被报告（base class 也没有）
        ghost_errors = [m for m in issue_msgs if 'frame-ghost-n9999-00001' in m
                       and 'background-color' in m]
        check('U-322', 'frame-ghost-n9999-00001 background-color 无 base class → 报 MISSING',
              len(ghost_errors) >= 1)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_u411_parse_flat_css_first_wins_in_validate():
    """
    U-411: _parse_flat_css in validate_split.py must use first-occurrence-wins.

    Real data: Web-merged-181-2980/Web.module.less has .group-2007673545 twice:
      1. Top-level: display:flex; width:100%; height:800px; ... (correct)
      2. Inside @media (max-width:768px): display:none (pcOnly hide-on-mobile rule)
    With last-wins, ground truth becomes {display:none}, causing section CSS to
    appear as "drift" even though it correctly has display:flex.
    """
    from validate_split import _parse_flat_css

    # Real data from Web-merged-181-2980/Web.module.less (node 181:2981)
    css_text = """.group-2007673545 {
  width: 100%;
  height: 800px;
  flex-shrink: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}

@media (max-width: 768px) {
  .group-2007673545 {
    display: none;
  }
}
"""
    result = _parse_flat_css(css_text)
    cls = result.get('group-2007673545', {})
    check('U-411a', 'first-occurrence display:flex preserved in validate', cls.get('display') == 'flex')
    check('U-411b', 'first-occurrence width:100% preserved in validate', cls.get('width') == '100%')
    check('U-411c', 'first-occurrence height:800px preserved in validate', cls.get('height') == '800px')


print('\n── validate_split Layer 1 tests ──')
test_nodeid_suffix_class_base_fallback()
test_u411_parse_flat_css_first_wins_in_validate()
print_summary('split/test_validate_split_layer1')