"""
从 Figma raw-data 提取 P4 所需的语义上下文。
输出格式与 .figma-to-code/2-figma-extract/{nodeId}.semantic.json 一致。
"""


def extract_semantic_context(raw_data: dict) -> dict:
    """从 Figma API 原始响应中提取语义上下文。"""
    ctx = {
        'componentInstances': {},
        'interactiveNodes': {},
        'readyFrames': [],
    }
    for node_data in raw_data.get('nodes', {}).values():
        doc = node_data.get('document', {})
        if doc:
            _walk(doc, ctx)
    return ctx


def _walk(node: dict, ctx: dict) -> None:
    node_id = node.get('id', '')
    node_type = node.get('type', '')

    # INSTANCE → 记录 componentId 与变体属性
    if node_type == 'INSTANCE':
        comp_id = node.get('componentId')
        if comp_id:
            if comp_id not in ctx['componentInstances']:
                ctx['componentInstances'][comp_id] = {
                    'occurrences': [],
                    'componentProperties': {},
                }
            ctx['componentInstances'][comp_id]['occurrences'].append(node_id)
            for k, v in (node.get('componentProperties') or {}).items():
                if isinstance(v, dict):
                    ctx['componentInstances'][comp_id]['componentProperties'][k] = v.get('value')

    # transitionNodeID → 可交互（click），优先于 interactions
    if node.get('transitionNodeID') and node_id not in ctx['interactiveNodes']:
        ctx['interactiveNodes'][node_id] = 'click'

    # interactions → click / hover
    for ia in (node.get('interactions') or []):
        trigger_type = (ia.get('trigger') or {}).get('type', '')
        if trigger_type == 'MOUSE_DOWN' and node_id not in ctx['interactiveNodes']:
            ctx['interactiveNodes'][node_id] = 'click'
        elif trigger_type in ('MOUSE_ENTER', 'HOVER') and node_id not in ctx['interactiveNodes']:
            ctx['interactiveNodes'][node_id] = 'hover'

    # devStatus
    if (node.get('devStatus') or {}).get('type') == 'READY_FOR_DEV':
        ctx['readyFrames'].append(node_id)

    for child in (node.get('children') or []):
        _walk(child, ctx)
