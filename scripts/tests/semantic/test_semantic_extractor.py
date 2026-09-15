import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.semantic_extractor import extract_semantic_context


MINIMAL_RAW = {
    'nodes': {
        '42:100': {
            'document': {
                'id': '42:100', 'type': 'FRAME', 'name': 'Page',
                'children': [
                    {
                        'id': '42:10', 'type': 'INSTANCE', 'name': 'Button',
                        'componentId': 'master-btn-001',
                        'componentProperties': {
                            'variant': {'value': 'primary', 'type': 'VARIANT'}
                        },
                        'transitionNodeID': '42:20',
                        'children': [],
                    },
                    {
                        'id': '42:11', 'type': 'INSTANCE', 'name': 'Button',
                        'componentId': 'master-btn-001',
                        'componentProperties': {
                            'variant': {'value': 'secondary', 'type': 'VARIANT'}
                        },
                        'children': [],
                    },
                    {
                        'id': '42:30', 'type': 'FRAME', 'name': 'Hero',
                        'devStatus': {'type': 'READY_FOR_DEV', 'description': ''},
                        'interactions': [
                            {'trigger': {'type': 'MOUSE_ENTER'}, 'actions': []}
                        ],
                        'children': [],
                    },
                ],
            }
        }
    }
}


def test_extracts_component_instances():
    ctx = extract_semantic_context(MINIMAL_RAW)
    assert 'master-btn-001' in ctx['componentInstances']
    assert len(ctx['componentInstances']['master-btn-001']['occurrences']) == 2
    assert ctx['componentInstances']['master-btn-001']['componentProperties']['variant'] == 'secondary'


def test_extracts_interactive_nodes_from_transition():
    ctx = extract_semantic_context(MINIMAL_RAW)
    assert ctx['interactiveNodes']['42:10'] == 'click'


def test_extracts_interactive_nodes_from_interactions():
    ctx = extract_semantic_context(MINIMAL_RAW)
    assert ctx['interactiveNodes']['42:30'] == 'hover'


def test_extracts_ready_frames():
    ctx = extract_semantic_context(MINIMAL_RAW)
    assert '42:30' in ctx['readyFrames']


def test_empty_raw_data():
    ctx = extract_semantic_context({'nodes': {}})
    assert ctx == {'componentInstances': {}, 'interactiveNodes': {}, 'readyFrames': []}


if __name__ == '__main__':
    tests = [test_extracts_component_instances, test_extracts_interactive_nodes_from_transition,
             test_extracts_interactive_nodes_from_interactions, test_extracts_ready_frames,
             test_empty_raw_data]
    passed = 0
    for t in tests:
        try:
            t(); print(f'  ✓ {t.__name__}'); passed += 1
        except Exception as e:
            print(f'  ✗ {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')
