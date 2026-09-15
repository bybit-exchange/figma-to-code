import json
import re
from pathlib import Path


def _normalize_snippet(snippet: str) -> str:
    """Replace Python boolean/None literals with JS equivalents inside JSX props.

    Figma Code Connect templates are sometimes authored with Python-style literals
    (e.g. closeIcon={False}) which crash at runtime in React.  Normalize on ingest
    so downstream codegen never emits invalid JS.
    """
    snippet = re.sub(r'\{False\}', '{false}', snippet)
    snippet = re.sub(r'\{True\}',  '{true}',  snippet)
    snippet = re.sub(r'\{None\}',  '{null}',  snippet)
    # Pagination with `current` prop is a controlled component — React requires
    # a paired `onChange` handler or it throws and crashes the whole component tree.
    if '<Pagination' in snippet and 'current=' in snippet and 'onChange' not in snippet:
        snippet = snippet.replace('/>', ' onChange={() => {}} />')
    return snippet


def parse_mcp_response(raw: dict) -> dict:
    result = {}
    for node_id, entry in raw.items():
        component_name = entry.get("componentName", "")
        snippet_imports = entry.get("snippetImports") or []

        cc_import = _pick_primary_import(snippet_imports, component_name)
        is_icon = _detect_icon(cc_import, component_name)

        result[node_id] = {
            "componentName": component_name,
            "ccImport": cc_import,
            "allImports": snippet_imports,
            "snippet": _normalize_snippet(entry.get("snippet", "")),
            "label": entry.get("label", ""),
            "source": entry.get("source", ""),
            "isIcon": is_icon,
        }
    return result


def _pick_primary_import(imports: list, component_name: str) -> str:
    if not imports:
        return ""
    for imp in imports:
        if component_name and component_name in imp:
            return imp
    return imports[0]


def _detect_icon(primary_import: str, component_name: str) -> bool:
    if "-icons" in primary_import:
        return True
    return component_name.startswith("Icon")


def load_code_connect_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # Auto-convert raw Figma MCP format (snippetImports) to processed format (ccImport)
    first = next(iter(raw.values()), {}) if raw else {}
    if "snippetImports" in first and "ccImport" not in first:
        return parse_mcp_response(raw)
    return raw


def save_code_connect_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_snippet_props(snippet: str) -> dict:
    props = {}

    # Extract component name and the opening tag content
    tag_match = re.match(r"<(\w+)([\s\S]*?)(/?>)", snippet.strip())
    if not tag_match:
        return props

    tag_body = tag_match.group(2)
    is_self_closing = tag_match.group(3) == "/>"

    # String props: prop="value"
    for m in re.finditer(r'(\w+)="([^"]*)"', tag_body):
        props[m.group(1)] = m.group(2)

    # Expression props: prop={expr}
    for m in re.finditer(r'(\w+)=\{([^}]*)\}', tag_body):
        props[m.group(1)] = m.group(2).strip()

    # Boolean props: standalone word (not followed by =)
    for m in re.finditer(r'(?<!\w)(\w+)(?!\s*=)(?=\s|/|>)', tag_body):
        key = m.group(1)
        if key not in props:
            props[key] = True

    # Children: content between opening and closing tags
    if not is_self_closing:
        component_name = tag_match.group(1)
        children_match = re.search(
            r">" + r"([\s\S]*?)" + r"</" + re.escape(component_name) + r">",
            snippet,
        )
        if children_match:
            children = children_match.group(1).strip()
            if children:
                props["children"] = children

    return props
