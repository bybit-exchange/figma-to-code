import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.code_connect import parse_mcp_response, load_code_connect_json, extract_snippet_props


BUTTON_RAW = {
    "8662:11052": {
        "componentName": "Button",
        "source": "https://example.com/components",
        "label": "Web",
        "hasTemplate": True,
        "snippetImports": ["import { Button } from \"your-component-lib\""],
        "snippetNestedFunctions": [],
        "snippet": "<Button variant=\"primary\" size=\"large\">\n  Primary_XL\n</Button>",
    }
}

PAGINATION_RAW = {
    "1234:5678": {
        "componentName": "Pagination",
        "source": "https://example.com/components",
        "label": "Web",
        "hasTemplate": True,
        "snippetImports": [
            "import React from \"react\"",
            "import { Pagination } from \"your-component-lib\"",
            "import styles from \"./styles.module.less\"",
        ],
        "snippetNestedFunctions": [],
        "snippet": "<Pagination total={50} showQuickJumper />",
    }
}

ICON_RAW = {
    "9999:0001": {
        "componentName": "IconArrowRight",
        "source": "https://example.com/components",
        "label": "Web",
        "hasTemplate": True,
        "snippetImports": ["import { IconArrowRight } from \"@example/icons\""],
        "snippetNestedFunctions": [],
        "snippet": "<IconArrowRight size={24} />",
    }
}


def test_parse_mcp_response_button():
    result = parse_mcp_response(BUTTON_RAW)
    assert "8662:11052" in result
    entry = result["8662:11052"]
    assert entry["componentName"] == "Button"
    assert entry["ccImport"] == "import { Button } from \"your-component-lib\""
    assert entry["allImports"] == ["import { Button } from \"your-component-lib\""]
    assert "variant" in entry["snippet"]
    assert entry["label"] == "Web"
    assert entry["isIcon"] is False


def test_parse_mcp_response_multiple_imports():
    result = parse_mcp_response(PAGINATION_RAW)
    entry = result["1234:5678"]
    assert entry["componentName"] == "Pagination"
    assert entry["ccImport"] == "import { Pagination } from \"your-component-lib\""
    assert len(entry["allImports"]) == 3
    assert entry["isIcon"] is False


def test_parse_mcp_response_icon():
    result = parse_mcp_response(ICON_RAW)
    entry = result["9999:0001"]
    assert entry["isIcon"] is True
    assert entry["componentName"] == "IconArrowRight"


def test_extract_snippet_props_simple():
    snippet = "<Button variant=\"primary\">\n  Click Me\n</Button>"
    props = extract_snippet_props(snippet)
    assert props["variant"] == "primary"
    assert props["children"] == "Click Me"


def test_extract_snippet_props_self_closing():
    snippet = "<Pagination total={50} showQuickJumper />"
    props = extract_snippet_props(snippet)
    assert props["total"] == "50"
    assert props["showQuickJumper"] is True
    assert "children" not in props


def test_extract_snippet_props_multiline():
    snippet = "<Button\n  variant=\"secondary\"\n  size=\"small\"\n>\n  Label\n</Button>"
    props = extract_snippet_props(snippet)
    assert props["variant"] == "secondary"
    assert props["size"] == "small"
    assert props["children"] == "Label"


def test_load_code_connect_json_missing_file():
    result = load_code_connect_json(Path("/nonexistent/path/code-connect.json"))
    assert result == {}


def test_load_code_connect_json_valid():
    data = {"8662:11052": {"componentName": "Button", "isIcon": False}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp_path = Path(f.name)
    try:
        result = load_code_connect_json(tmp_path)
        assert result == data
    finally:
        tmp_path.unlink()


# Real data: node 250:4498 from EuDepositCampaign (EU-deposit-incentive-campaign-LP, merged-250-2618)
# CC template wrote Python's `False` instead of JS `false`
TEXTLINK_PYTHON_BOOL_RAW = {
    "250:4498": {
        "componentName": "TextLink",
        "source": "https://example.com/components",
        "label": "Web",
        "hasTemplate": True,
        "snippetImports": ["import { TextLink } from \"your-component-lib\";"],
        "snippetNestedFunctions": [],
        "snippet": "<TextLink size=\"normal\" closeIcon={False}>\n        See More\n      </TextLink>",
    }
}


def test_parse_mcp_response_normalizes_python_booleans():
    """parse_mcp_response must replace Python False/True/None with JS false/true/null.
    Real data: node 250:4498 TextLink closeIcon={False} from EU deposit campaign CC template.
    """
    result = parse_mcp_response(TEXTLINK_PYTHON_BOOL_RAW)
    snippet = result["250:4498"]["snippet"]
    assert "False" not in snippet, f"Python False must be normalized: {snippet}"
    assert "false" in snippet, f"JS false must be present: {snippet}"
    assert "closeIcon={false}" in snippet


def test_parse_mcp_response_normalizes_all_python_literals():
    """All Python literals (True, False, None) in snippets must be normalized."""
    raw = {
        "1:1": {
            "componentName": "Comp",
            "source": "",
            "label": "Web",
            "hasTemplate": True,
            "snippetImports": [],
            "snippetNestedFunctions": [],
            "snippet": "<Comp a={True} b={False} c={None} />",
        }
    }
    result = parse_mcp_response(raw)
    snippet = result["1:1"]["snippet"]
    assert "True" not in snippet and "False" not in snippet and "None" not in snippet
    assert "a={true}" in snippet
    assert "b={false}" in snippet
    assert "c={null}" in snippet


def test_normalize_snippet_pagination_controlled_injects_onChange():
    """Pagination with current= (controlled) must get onChange injected to prevent React crash.
    Real data: node 227:13142 from MyPage (nodeId: 169-33787)
    """
    raw = {
        "227:13142": {
            "componentName": "Pagination",
            "source": "https://example.com/components",
            "label": "Web",
            "hasTemplate": True,
            "snippetImports": ["import { Pagination } from \"your-component-lib\";"],
            "snippetNestedFunctions": [],
            "snippet": "<Pagination size={} total={50} current={1} pageSize={10} showQuickJumper={}/>",
        }
    }
    result = parse_mcp_response(raw)
    snippet = result["227:13142"]["snippet"]
    assert "onChange" in snippet, f"Pagination with current= must have onChange injected, got: {snippet}"


def test_normalize_snippet_pagination_already_has_onChange_unchanged():
    """Pagination that already has onChange must not get a duplicate injected."""
    raw = {
        "1:1": {
            "componentName": "Pagination",
            "source": "",
            "label": "Web",
            "hasTemplate": True,
            "snippetImports": ["import { Pagination } from \"your-component-lib\";"],
            "snippetNestedFunctions": [],
            "snippet": "<Pagination total={50} current={1} onChange={handler} />",
        }
    }
    result = parse_mcp_response(raw)
    snippet = result["1:1"]["snippet"]
    assert snippet.count("onChange") == 1, f"Should not duplicate onChange, got: {snippet}"


def test_normalize_snippet_non_pagination_current_unchanged():
    """Non-Pagination components with current= must NOT get onChange injected."""
    raw = {
        "1:2": {
            "componentName": "Tabs",
            "source": "",
            "label": "Web",
            "hasTemplate": True,
            "snippetImports": ["import { Tabs } from \"your-component-lib\";"],
            "snippetNestedFunctions": [],
            "snippet": "<Tabs current={1} />",
        }
    }
    result = parse_mcp_response(raw)
    snippet = result["1:2"]["snippet"]
    # Tabs is not Pagination, should not be touched
    assert "onChange" not in snippet, f"Non-Pagination must not be modified, got: {snippet}"


if __name__ == "__main__":
    tests = [
        test_parse_mcp_response_button,
        test_parse_mcp_response_multiple_imports,
        test_parse_mcp_response_icon,
        test_extract_snippet_props_simple,
        test_extract_snippet_props_self_closing,
        test_extract_snippet_props_multiline,
        test_load_code_connect_json_missing_file,
        test_load_code_connect_json_valid,
        test_parse_mcp_response_normalizes_python_booleans,
        test_parse_mcp_response_normalizes_all_python_literals,
        test_normalize_snippet_pagination_controlled_injects_onChange,
        test_normalize_snippet_pagination_already_has_onChange_unchanged,
        test_normalize_snippet_non_pagination_current_unchanged,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
