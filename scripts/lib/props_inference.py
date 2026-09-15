from __future__ import annotations
import re
from lib.code_connect import extract_snippet_props


# ─── Name normalization ───────────────────────────────────────────────────────

def normalize_prop_name(raw: str) -> str:
    """Convert Figma property names to camelCase."""
    if not raw:
        return raw

    # Strip #id suffix (e.g., "Show Jumper#20864:53" → "Show Jumper")
    name = re.sub(r"#[\w:]+$", "", raw)

    # Strip surrounding/embedded quotes
    name = name.replace('"', "").replace("'", "")

    # Split on spaces, hyphens, underscores
    tokens = re.split(r"[\s\-_]+", name.strip())
    if not tokens:
        return ""

    # Single token with no separators:
    # - All-uppercase (acronym/constant) → fully lowercase
    # - Mixed case (camelCase) → lowercase just the first character to preserve the rest
    if len(tokens) == 1:
        word = tokens[0]
        if not word:
            return ""
        if word.isupper():
            return word.lower()
        return word[0].lower() + word[1:]

    result = tokens[0].lower()
    for token in tokens[1:]:
        if token:
            result += token[0].upper() + token[1:].lower()

    return result


# ─── Fuzzy matching ───────────────────────────────────────────────────────────

def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[lb]


def _fuzzy_match(name: str, candidates: dict, threshold: int = 3) -> str | None:
    """Return the best matching key from candidates for a given normalized name.

    Priority: exact > contains > Levenshtein ≤ threshold.
    """
    lower = name.lower()

    # Exact match
    if name in candidates:
        return name

    # Case-insensitive exact
    for key in candidates:
        if key.lower() == lower:
            return key

    # Contains: candidate key contains our name, or our name contains the key
    best_contains = None
    best_len = float("inf")
    for key in candidates:
        kl = key.lower()
        if lower in kl or kl in lower:
            if len(key) < best_len:
                best_len = len(key)
                best_contains = key
    if best_contains:
        return best_contains

    # Levenshtein ≤ threshold
    best_lev = None
    best_dist = threshold + 1
    for key in candidates:
        d = _levenshtein(lower, key.lower())
        if d <= threshold and d < best_dist:
            best_dist = d
            best_lev = key
    return best_lev


# ─── Ordinal helpers ──────────────────────────────────────────────────────────

_ORDINAL_MAP = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "last": -1,
}
# Ordinal words that only count when they appear as whole words (word boundary match)
_ORDINAL_WHOLE_WORD_RE = re.compile(
    r"\b(" + "|".join(_ORDINAL_MAP.keys()) + r")\b", re.IGNORECASE
)

_SIZE_KEYWORDS = {
    "xxl", "xl", "large", "l", "medium", "m", "small", "s", "xs", "xsmall",
}


def _extract_ordinal(text: str) -> int | None:
    m = _ORDINAL_WHOLE_WORD_RE.search(text)
    if m:
        return _ORDINAL_MAP[m.group(1).lower()]
    m2 = re.search(r"\b(\d+)\b", text)
    if m2:
        return int(m2.group(1))
    return None


def _looks_like_size(value: str) -> bool:
    lower = value.lower().strip()
    if re.search(r"\d+px", lower):
        return True
    return lower in _SIZE_KEYWORDS


def _snippet_prop_type(val) -> str:
    """Infer expected type from a snippet prop value."""
    if val is True:
        return "boolean"
    if isinstance(val, str):
        if re.fullmatch(r"-?\d+(\.\d+)?", val.strip()):
            return "number"
        if val.strip().startswith("["):
            return "array"
    return "string"


# ─── Boolean inference ────────────────────────────────────────────────────────

def infer_boolean_props(component_properties: dict, snippet_props: dict) -> dict:
    """Map BOOLEAN=True Figma props to snippet props. False values are omitted."""
    result = {}
    for raw_name, prop in component_properties.items():
        if prop.get("type") != "BOOLEAN":
            continue
        if not prop.get("value"):
            # value is False or falsy → omit
            continue

        normalized = normalize_prop_name(raw_name)
        matched_key = _fuzzy_match(normalized, snippet_props)
        output_key = matched_key if matched_key else normalized
        result[output_key] = True

    return result


# ─── Variant inference ────────────────────────────────────────────────────────

def infer_variant_props(component_properties: dict, snippet_props: dict) -> dict:
    """Map VARIANT Figma props to snippet props using generic data-driven rules."""
    result = {}

    # Pre-compute which snippet props expect numeric values
    numeric_snippet_keys = {
        k for k, v in snippet_props.items() if _snippet_prop_type(v) == "number"
    }

    for raw_name, prop in component_properties.items():
        if prop.get("type") != "VARIANT":
            continue

        value = prop.get("value", "")
        normalized_name = normalize_prop_name(raw_name)

        # Yes/No → boolean semantics
        if isinstance(value, str) and value.strip().lower() == "no":
            # Explicitly "No" → omit
            continue
        if isinstance(value, str) and value.strip().lower() == "yes":
            matched_key = _fuzzy_match(normalized_name, snippet_props)
            output_key = matched_key if matched_key else normalized_name
            result[output_key] = True
            continue

        # Size-like value → if snippet has a "size" prop, assign there
        if isinstance(value, str) and _looks_like_size(value):
            size_key = _fuzzy_match("size", snippet_props)
            if size_key:
                result[size_key] = value
            else:
                result[normalized_name] = value
            continue

        # Numeric / ordinal value
        ordinal = _extract_ordinal(value) if isinstance(value, str) else None
        if ordinal is not None:
            # Try to find a matching numeric snippet prop by name first
            matched_key = _fuzzy_match(normalized_name, {k: snippet_props[k] for k in numeric_snippet_keys})
            if not matched_key and numeric_snippet_keys:
                # Fall back to first numeric prop
                matched_key = next(iter(numeric_snippet_keys))
            output_key = matched_key if matched_key else normalized_name
            result[output_key] = ordinal
            continue

        # Generic string: match by normalized name
        matched_key = _fuzzy_match(normalized_name, snippet_props)
        output_key = matched_key if matched_key else normalized_name
        result[output_key] = value

    return result


# ─── Text collection ─────────────────────────────────────────────────────────

def _collect_text_nodes(node: dict) -> list[str]:
    """Recursively collect all TEXT node textContent values."""
    texts = []
    if node.get("type") == "TEXT":
        content = node.get("textContent", "").strip()
        if content:
            texts.append(content)
    for child in node.get("children", []):
        texts.extend(_collect_text_nodes(child))
    return texts


# ─── Text inference ───────────────────────────────────────────────────────────

_RANGE_PATTERN = re.compile(
    r"(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)", re.IGNORECASE
)

def infer_text_props(instance_node: dict, snippet_props: dict) -> dict:
    """Infer props from text nodes in the instance. Generic, data-driven."""
    texts = _collect_text_nodes(instance_node)
    if not texts:
        return {}

    result = {}

    # Detect snippet prop categories
    has_children = "children" in snippet_props
    has_items = "items" in snippet_props
    numeric_keys = {k for k, v in snippet_props.items() if _snippet_prop_type(v) == "number"}
    string_label_keys = {
        k for k, v in snippet_props.items()
        if _snippet_prop_type(v) == "string" and k in ("label", "title", "placeholder")
    }

    # If snippet uses items array → collect all texts as [{key, label}] list
    if has_items:
        result["items"] = [{"key": str(i + 1), "label": t} for i, t in enumerate(texts)]
        return result

    # Try to extract numeric values from texts (e.g., "1-10 of 50 Items")
    remaining_texts = list(texts)
    if numeric_keys:
        for text in texts:
            m = _RANGE_PATTERN.search(text)
            if m:
                start_val, end_val, total_val = int(m.group(1)), int(m.group(2)), int(m.group(3))
                # Map to snippet numeric props by common names
                for k in numeric_keys:
                    kl = k.lower()
                    if kl in ("total", "count", "max") and "total" not in result:
                        result[k] = total_val
                    elif kl in ("current", "page", "start") and "current" not in result:
                        result[k] = start_val
                    elif kl == "end" and "end" not in result:
                        result[k] = end_val
                remaining_texts = [t for t in remaining_texts if t != text]
                break

        # If still have unmatched numeric snippet props, try bare numbers
        unmatched_numeric = [k for k in numeric_keys if k not in result]
        for text in list(remaining_texts):
            if unmatched_numeric:
                m = re.fullmatch(r"\d+", text.strip())
                if m:
                    result[unmatched_numeric.pop(0)] = int(text.strip())
                    remaining_texts.remove(text)

    # children → primary text
    if has_children and remaining_texts:
        result["children"] = remaining_texts[0]
        remaining_texts = remaining_texts[1:]

    # label/title/placeholder → first non-numeric remaining text
    for key in string_label_keys:
        if key not in result and remaining_texts:
            result[key] = remaining_texts[0]
            remaining_texts = remaining_texts[1:]

    # If nothing matched but snippet has children or string props, use first text
    if not result and texts:
        if has_children:
            result["children"] = texts[0]
        elif string_label_keys:
            key = next(iter(string_label_keys))
            result[key] = texts[0]

    return result


# ─── Instance swap inference ──────────────────────────────────────────────────

def infer_instance_swap_props(instance_node: dict, code_connect_map: dict) -> dict:
    """Map exposed sub-instances to icon/slot props."""
    result = {}
    exposed = instance_node.get("exposedInstances", [])
    if not exposed:
        return result

    for node_id in exposed:
        entry = code_connect_map.get(node_id)
        if not entry:
            continue

        name = entry.get("componentName", node_id)
        import_str = entry.get("ccImport", "")

        if entry.get("isIcon"):
            result["icon"] = {"component": name, "import": import_str}
        else:
            slot_key = f"slot_{name.lower()}"
            result[slot_key] = {"component": name, "import": import_str}

    return result


# ─── Full inference ───────────────────────────────────────────────────────────

def infer_props_full(instance_node: dict, cc_entry: dict, code_connect_map: dict) -> dict:
    """Combine all inference layers into a final props dict.

    Layer order (later overrides earlier):
    1. Snippet defaults (except children — always inferred from content)
    2. Boolean overrides
    3. Variant overrides
    4. Text extraction
    5. Instance swap
    """
    snippet = cc_entry.get("snippet", "")
    snippet_props = extract_snippet_props(snippet)

    # Start from snippet defaults, but exclude children (always infer from actual content)
    result = {k: v for k, v in snippet_props.items() if k != "children"}

    # Component properties may be on the instance node or provided separately
    component_properties = instance_node.get("componentProperties", {})

    # Layer 2: boolean props
    bool_props = infer_boolean_props(component_properties, snippet_props)
    result.update(bool_props)

    # Layer 3: variant props
    variant_props = infer_variant_props(component_properties, snippet_props)
    result.update(variant_props)

    # Layer 4: text extraction
    text_props = infer_text_props(instance_node, snippet_props)
    result.update(text_props)

    # Layer 5: instance swap
    swap_props = infer_instance_swap_props(instance_node, code_connect_map)
    result.update(swap_props)

    return result
