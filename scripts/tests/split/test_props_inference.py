import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.props_inference import (
    infer_props_full,
    infer_boolean_props,
    infer_variant_props,
    infer_text_props,
    infer_instance_swap_props,
    normalize_prop_name,
)


# ─── normalize_prop_name ──────────────────────────────────────────────────────

def test_normalize_prop_name_basic():
    assert normalize_prop_name("Show Jumper") == "showJumper"
    assert normalize_prop_name("icon-left") == "iconLeft"
    assert normalize_prop_name("Dark mode") == "darkMode"
    assert normalize_prop_name("size") == "size"


def test_normalize_prop_name_with_id_suffix():
    assert normalize_prop_name("Show Jumper#20864:53") == "showJumper"
    assert normalize_prop_name("icon-left#2715:0") == "iconLeft"
    assert normalize_prop_name("variant#999:0") == "variant"


def test_normalize_prop_name_edge_cases():
    assert normalize_prop_name("") == ""
    assert normalize_prop_name("alreadyCamelCase") == "alreadyCamelCase"
    assert normalize_prop_name("UPPERCASE") == "uppercase"
    assert normalize_prop_name("under_score_name") == "underScoreName"
    # Quotes stripped
    assert normalize_prop_name('Show "..."') == "show..."


# ─── infer_boolean_props ─────────────────────────────────────────────────────

def test_infer_boolean_props_true_with_match():
    component_properties = {
        "Show Jumper#20864:53": {"type": "BOOLEAN", "value": True},
    }
    snippet_props = {"showQuickJumper": True, "total": "50"}
    result = infer_boolean_props(component_properties, snippet_props)
    # "showJumper" should fuzzy-match "showQuickJumper"
    assert "showQuickJumper" in result or "showJumper" in result
    matched_val = result.get("showQuickJumper") or result.get("showJumper")
    assert matched_val is True


def test_infer_boolean_props_true_no_match():
    component_properties = {
        "Enable Feature#1:2": {"type": "BOOLEAN", "value": True},
    }
    snippet_props = {"total": "50", "size": "medium"}
    result = infer_boolean_props(component_properties, snippet_props)
    # No match in snippet — still output with normalized name
    assert "enableFeature" in result
    assert result["enableFeature"] is True


def test_infer_boolean_props_false_omitted():
    component_properties = {
        "Show Jumper#20864:53": {"type": "BOOLEAN", "value": False},
        "Disabled#1:1": {"type": "BOOLEAN", "value": False},
    }
    snippet_props = {"showQuickJumper": True, "disabled": True}
    result = infer_boolean_props(component_properties, snippet_props)
    # value=False → nothing in output
    assert result == {}


def test_infer_boolean_props_mixed():
    component_properties = {
        "Show Label#1:0": {"type": "BOOLEAN", "value": True},
        "Disabled#1:1": {"type": "BOOLEAN", "value": False},
        # Non-BOOLEAN should be ignored
        "Size#1:2": {"type": "VARIANT", "value": "Large"},
    }
    snippet_props = {"showLabel": True, "disabled": True}
    result = infer_boolean_props(component_properties, snippet_props)
    assert len(result) == 1
    assert "showLabel" in result


# ─── infer_variant_props ─────────────────────────────────────────────────────

def test_infer_variant_props_yes_no():
    component_properties = {
        "Show Arrow#1:0": {"type": "VARIANT", "value": "Yes"},
        "Disabled#1:1": {"type": "VARIANT", "value": "No"},
    }
    snippet_props = {"showArrow": True, "disabled": False}
    result = infer_variant_props(component_properties, snippet_props)
    # Yes → True-like, No → omitted
    assert "disabled" not in result
    # showArrow should be truthy
    assert result.get("showArrow") is True or result.get("showArrow") == "Yes"


def test_infer_variant_props_size():
    component_properties = {
        "Size#1:0": {"type": "VARIANT", "value": "Large"},
    }
    snippet_props = {"size": "medium", "variant": "primary"}
    result = infer_variant_props(component_properties, snippet_props)
    # Size keyword maps to snippet's size prop
    assert "size" in result


def test_infer_variant_props_numeric_ordinal():
    component_properties = {
        "Page#1:0": {"type": "VARIANT", "value": "First page"},
    }
    snippet_props = {"current": "1", "total": "50"}
    result = infer_variant_props(component_properties, snippet_props)
    # "First page" → 1 → numeric prop
    assert result.get("current") == 1 or result.get("page") == 1 or any(v == 1 for v in result.values())


def test_infer_variant_props_generic_string():
    component_properties = {
        "variant#1:0": {"type": "VARIANT", "value": "secondary"},
    }
    snippet_props = {"variant": "primary", "size": "medium"}
    result = infer_variant_props(component_properties, snippet_props)
    assert result.get("variant") == "secondary"


def test_infer_variant_props_size_pattern_px():
    component_properties = {
        "Size#1:0": {"type": "VARIANT", "value": "24px"},
    }
    snippet_props = {"size": "16px"}
    result = infer_variant_props(component_properties, snippet_props)
    assert "size" in result


# ─── infer_text_props ────────────────────────────────────────────────────────

def test_infer_text_props_children():
    instance_node = {
        "type": "INSTANCE",
        "children": [
            {"type": "TEXT", "textContent": "Click Me"},
        ],
    }
    snippet_props = {"children": "Label", "variant": "primary"}
    result = infer_text_props(instance_node, snippet_props)
    assert result.get("children") == "Click Me"


def test_infer_text_props_numeric_extraction():
    instance_node = {
        "type": "INSTANCE",
        "children": [
            {"type": "TEXT", "textContent": "1-10 of 50 Items"},
        ],
    }
    snippet_props = {"total": "50", "current": "1"}
    result = infer_text_props(instance_node, snippet_props)
    # Should extract total=50 from "of 50 Items" pattern
    assert result.get("total") == 50 or str(result.get("total")) == "50"


def test_infer_text_props_items_array():
    instance_node = {
        "type": "INSTANCE",
        "children": [
            {"type": "TEXT", "textContent": "Item A"},
            {"type": "TEXT", "textContent": "Item B"},
            {"type": "TEXT", "textContent": "Item C"},
        ],
    }
    snippet_props = {"items": '[{key: "1", label: "Label"}]'}
    result = infer_text_props(instance_node, snippet_props)
    items = result.get("items")
    assert isinstance(items, list)
    assert len(items) == 3
    assert items[0].get("label") == "Item A"


def test_infer_text_props_label_prop():
    instance_node = {
        "type": "INSTANCE",
        "children": [
            {"type": "TEXT", "textContent": "Submit"},
        ],
    }
    snippet_props = {"label": "Button label"}
    result = infer_text_props(instance_node, snippet_props)
    assert result.get("label") == "Submit" or result.get("children") == "Submit"


def test_infer_text_props_nested():
    instance_node = {
        "type": "INSTANCE",
        "children": [
            {
                "type": "FRAME",
                "children": [
                    {"type": "TEXT", "textContent": "Nested Text"},
                ],
            }
        ],
    }
    snippet_props = {"children": ""}
    result = infer_text_props(instance_node, snippet_props)
    assert result.get("children") == "Nested Text"


# ─── infer_instance_swap_props ───────────────────────────────────────────────

def test_infer_instance_swap_with_icon():
    instance_node = {
        "type": "INSTANCE",
        "exposedInstances": ["9999:0001"],
    }
    code_connect_map = {
        "9999:0001": {
            "componentName": "IconArrowRight",
            "ccImport": 'import { IconArrowRight } from "@example/icons"',
            "isIcon": True,
        }
    }
    result = infer_instance_swap_props(instance_node, code_connect_map)
    assert "icon" in result
    assert result["icon"]["component"] == "IconArrowRight"
    assert "@example/icons" in result["icon"]["import"]


def test_infer_instance_swap_without_cc():
    instance_node = {
        "type": "INSTANCE",
        "exposedInstances": ["1234:9999"],  # not in map
    }
    code_connect_map = {}
    result = infer_instance_swap_props(instance_node, code_connect_map)
    assert result == {}


def test_infer_instance_swap_non_icon():
    instance_node = {
        "type": "INSTANCE",
        "exposedInstances": ["5555:1234"],
    }
    code_connect_map = {
        "5555:1234": {
            "componentName": "Badge",
            "ccImport": 'import { Badge } from "your-component-lib"',
            "isIcon": False,
        }
    }
    result = infer_instance_swap_props(instance_node, code_connect_map)
    # Non-icon → slot_<name>
    assert any(k.startswith("slot_") for k in result)


def test_infer_instance_swap_no_exposed():
    instance_node = {"type": "INSTANCE"}
    result = infer_instance_swap_props(instance_node, {})
    assert result == {}


# ─── infer_props_full (integration) ─────────────────────────────────────────

def test_infer_props_full_integration():
    """Pagination-like component: combines boolean, variant, text, and instance layers."""
    instance_node = {
        "type": "INSTANCE",
        "componentProperties": {
            "Show Jumper#20864:53": {"type": "BOOLEAN", "value": True},
            "Size#1:0": {"type": "VARIANT", "value": "Large"},
        },
        "children": [
            {"type": "TEXT", "textContent": "1-10 of 50 Items"},
        ],
        "exposedInstances": [],
    }
    cc_entry = {
        "componentName": "Pagination",
        "snippet": "<Pagination total={50} showQuickJumper />",
        "ccImport": 'import { Pagination } from "your-component-lib"',
        "isIcon": False,
    }
    code_connect_map = {}

    result = infer_props_full(instance_node, cc_entry, code_connect_map)

    # Should have extracted total from text
    assert "total" in result
    # Should have the showQuickJumper or equivalent boolean
    has_jumper = any("jump" in k.lower() or "jumper" in k.lower() for k in result)
    assert has_jumper or "showQuickJumper" in result


def test_infer_props_full_button():
    """Button-like component: variant + text children."""
    instance_node = {
        "type": "INSTANCE",
        "componentProperties": {
            "variant#1:0": {"type": "VARIANT", "value": "primary"},
            "Disabled#1:1": {"type": "BOOLEAN", "value": False},
        },
        "children": [
            {"type": "TEXT", "textContent": "Confirm"},
        ],
        "exposedInstances": [],
    }
    cc_entry = {
        "componentName": "Button",
        "snippet": '<Button variant="primary" size="large">\n  Primary_XL\n</Button>',
        "ccImport": 'import { Button } from "your-component-lib"',
        "isIcon": False,
    }
    result = infer_props_full(instance_node, cc_entry, {})
    assert result.get("variant") == "primary"
    # children inferred from actual text, not snippet default
    assert result.get("children") == "Confirm"
    # disabled=False → not in output
    assert "disabled" not in result or result.get("disabled") is False


if __name__ == "__main__":
    tests = [
        test_normalize_prop_name_basic,
        test_normalize_prop_name_with_id_suffix,
        test_normalize_prop_name_edge_cases,
        test_infer_boolean_props_true_with_match,
        test_infer_boolean_props_true_no_match,
        test_infer_boolean_props_false_omitted,
        test_infer_boolean_props_mixed,
        test_infer_variant_props_yes_no,
        test_infer_variant_props_size,
        test_infer_variant_props_numeric_ordinal,
        test_infer_variant_props_generic_string,
        test_infer_variant_props_size_pattern_px,
        test_infer_text_props_children,
        test_infer_text_props_numeric_extraction,
        test_infer_text_props_items_array,
        test_infer_text_props_label_prop,
        test_infer_text_props_nested,
        test_infer_instance_swap_with_icon,
        test_infer_instance_swap_without_cc,
        test_infer_instance_swap_non_icon,
        test_infer_instance_swap_no_exposed,
        test_infer_props_full_integration,
        test_infer_props_full_button,
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
