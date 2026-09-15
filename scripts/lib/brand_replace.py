"""
brand_replace.py — IR 层品牌词替换。

在 convert.py 代码生成前调用，将 IR 中代码标识符和路径中的品牌词替换为目标词。
文本内容（characters / textContent / textSegments）保留原始值，不做替换。

替换范围：
  代码标识符：figmaName、cssClass、semantic.componentName/className
  资源路径：localAssetPath
不替换范围：
  用户可见文字：characters、textContent、textSegments[].text

配置：将 _PAIRS 中的 (原词, 替换词) 对替换为你的项目品牌词。
默认为空列表，不做任何替换。
"""

import shutil
from pathlib import Path
from .paths import ASSETS_STAGING_DIR, PAGE_CODE_DIR


# 替换对：(原词大写, 替换词大写), (原词首字母大写, ...), (原词全小写, ...)
# 默认为空，不替换任何品牌词。如需替换，填入 (old, new) 对。
_PAIRS: list[tuple[str, str]] = []

# 代码标识符字段（不含 localAssetPath，单独处理；不含文本内容字段）
_CODE_FIELDS = ('figmaName', 'cssClass')


def replace_text(s: str) -> str:
    """对字符串做 case-aware 品牌词替换。"""
    for old, new in _PAIRS:
        s = s.replace(old, new)
    return s


def _replace_asset_path(path: str) -> str:
    """替换资源路径中目录部分的品牌词，保留文件名中的节点 ID 不变。

    /assets/OldCoAiHub20-169-33787/I169-33977.svg
    →  /assets/NewCoAiHub20-169-33787/I169-33977.svg

    文件名 I169-33977.svg 基于节点 ID，不含品牌词，不需替换（replace_text 也不会改它）。
    """
    return replace_text(path)


def brand_replace_ir(node: dict) -> dict:
    """递归替换 IR 节点中代码标识符和路径中的品牌词，原地修改并返回节点。

    文本内容（characters / textContent / textSegments）不做替换，保留设计稿原始文字。
    """
    for field in _CODE_FIELDS:
        if isinstance(node.get(field), str):
            node[field] = replace_text(node[field])

    if isinstance(node.get('localAssetPath'), str):
        node['localAssetPath'] = _replace_asset_path(node['localAssetPath'])

    # semantic 层：componentName / className 驱动导出名和目录名
    if isinstance(node.get('semantic'), dict):
        sem = node['semantic']
        for field in ('componentName', 'className'):
            if isinstance(sem.get(field), str):
                sem[field] = replace_text(sem[field])

    for child in node.get('children') or []:
        brand_replace_ir(child)

    return node


def rename_staging_dirs(base: Path, old_comp: str, new_comp: str, node_id_safe: str) -> None:
    """重命名 .figma-to-code 中间目录（1-assets、3-page-code），使其与新组件名对齐。

    调用时机：brand_replace_ir 之后，tsx_generator 之前。
    幂等：若新目录已存在则跳过，若旧目录不存在则跳过。
    """
    if old_comp == new_comp:
        return

    dirs_to_rename = [
        base / ASSETS_STAGING_DIR.name / f'{old_comp}-{node_id_safe}',
        base / PAGE_CODE_DIR.name / f'{old_comp}-{node_id_safe}',
    ]

    for old_dir in dirs_to_rename:
        if not old_dir.exists():
            continue
        new_dir = old_dir.parent / f'{new_comp}-{node_id_safe}'
        if new_dir.exists():
            shutil.rmtree(old_dir)
        else:
            old_dir.rename(new_dir)
