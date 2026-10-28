"""Shorter tool list for Claude (text and schema). Every Claude session loads all tool schemas into its context, so
long descriptions cost time and tokens on every turn (measured 51,447 chars for 41 tools, the
action schema alone 8,367). Kept: what is needed to pick a tool and fill its arguments.
Like web-reader's tool-text.mjs."""

import json
import re
from typing import Any, Dict, List

from scrapling.webreader.paths import ROOT

# The tool list is an index (2026-10-27): one line per tool and the parameter names/types, enough to
# pick the tool; before the first call of a tool Claude reads USAGE_DIR/<tool>.md, written by the server
# at start with the full description, parameter meanings and defaults. Measured: 35,705 -> ~15,000 chars
# per session.
USAGE_DIR = ROOT / "docs" / "tools"

# Scrapling's own tools that remain, described in one line each.
DESC: Dict[str, str] = {
    "browser_fetch": "Navigate a browser session's tab to a URL and return its content (markdown/html/text or snapshot). Reading without a session: read.",
    "browser_extract": "Read the current tab of a session without navigating: snapshot with element refs (default), markdown, html or text; search keeps matching snapshot lines.",
    "browser_evaluate": "Run JavaScript in a session's tab (isolated world by default) and return JSON. Page APIs: page_fetch.",
    "browser_screenshot": "Screenshot of a session's tab: viewport, full page or one element (selector or snapshot ref).",
    "browser_network_requests": "Requests a session's tab made (API calls by default; include_static for all), with IDs for browser_network_request / api_remember.",
    "browser_network_request": "One recorded request: summary, headers or body.",
    "list_sessions": "Open browser sessions and their settings.",
}

_FIRST = re.compile(r"^(.+?[.!?。])(\s|$)", re.S)


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    m = _FIRST.match(text)
    first = m.group(1) if m else text
    if len(first) <= limit:
        return first
    cut = first[: limit - 1]
    return cut[: cut.rfind(" ")].rstrip(",;:") + "…"  # whole words only


def _trim_schema(node: Any, limit: int = 120) -> None:
    """Shorter text and less pydantic noise, same meaning: Optional[X] is X (Claude leaves it out),
    `const` needs no type, validators Claude never reads go, and so does the discriminator table
    (the `type` const in each variant says the same)."""
    if isinstance(node, dict):
        if isinstance(node.get("description"), str):
            node["description"] = _short(node["description"], limit)
        if isinstance(node.get("title"), str):  # pydantic adds a title per field; the name already says it (a parameter named "title" is a dict)
            node.pop("title")
        if "const" in node and node.get("type") == "string":
            node.pop("type")
        for k in ("minLength", "exclusiveMinimum", "uniqueItems", "discriminator"):
            node.pop(k, None)
        if node.get("minimum") == 0 and "maximum" not in node:
            node.pop("minimum")
        if node.get("default") is None:
            node.pop("default", None)
        for k, v in list(node.items()):
            if isinstance(v, dict) and isinstance(v.get("anyOf"), list):
                node[k] = _flat(v)
        for v in node.values():
            _trim_schema(v, limit)
    elif isinstance(node, list):
        for v in node:
            _trim_schema(v, limit)


def _flat(v: Dict[str, Any]) -> Dict[str, Any]:
    alts = [a for a in v["anyOf"] if a != {"type": "null"}]
    rest = {k: x for k, x in v.items() if k != "anyOf" and not (k == "default" and x is None)}
    if len(alts) == 1:
        return {**alts[0], **rest}
    if {a.get("type") for a in alts} == {"integer", "number"}:
        return {"type": "number", **rest}
    return {"anyOf": alts, **rest}


def _short_defs(schema: Dict[str, Any]) -> None:
    """`#/$defs/_CheckboxFormField` -> `#/$defs/Checkbox`."""
    defs = schema.get("$defs")
    if not defs:
        return
    names = {n: re.sub(r"^_|FormField$|Action$", "", n) for n in defs}
    if len(set(names.values())) != len(names):
        return
    schema["$defs"] = {names[n]: v for n, v in defs.items()}

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.rsplit("/", 1)[-1] in names:
                node["$ref"] = "#/$defs/" + names[ref.rsplit("/", 1)[-1]]
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
    fix(schema)


def _slim(node: Any) -> None:
    """Index form: no descriptions, no defaults (both in the usage file)."""
    if isinstance(node, dict):
        node.pop("description", None)
        node.pop("default", None)
        for v in node.values():
            _slim(v)
    elif isinstance(node, list):
        for v in node:
            _slim(v)


def _kind(p: Dict[str, Any]) -> str:
    if p.get("enum"):
        return " | ".join(map(str, p["enum"]))
    if "const" in p:
        return f"const {json.dumps(p['const'])}"
    if p.get("$ref"):
        return p["$ref"].rsplit("/", 1)[-1]
    if p.get("anyOf"):
        return " or ".join(_kind(a) for a in p["anyOf"])
    t = p.get("type", "")
    if t == "array" and isinstance(p.get("items"), dict):
        return "array of " + (_kind(p["items"]) or "object")
    return t


def _param(name: str, p: Dict[str, Any], required: bool) -> str:
    """`name* type =default min-max`: * required; false and 0 defaults are left out."""
    bits = [name + ("*" if required else ""), _kind(p)]
    if not required and "default" in p and p["default"] is not False and p["default"] != 0:
        bits.append("=" + json.dumps(p["default"], ensure_ascii=False))
    if "minimum" in p or "maximum" in p:
        bits.append(f"{p.get('minimum', '')}-{p.get('maximum', '')}")
    if "maxItems" in p:
        bits.append(f"max {p['maxItems']}")
    return " ".join(b for b in bits if b)


def _param_lines(schema: Dict[str, Any]) -> List[str]:
    req = set(schema.get("required") or [])
    return [f"- {_param(n, p, n in req)}" + (f" — {p['description']}" if p.get("description") else "")
            for n, p in (schema.get("properties") or {}).items()]


def _def_line(dname: str, d: Dict[str, Any], said: set) -> str:
    """One line per type; an action variant is named by its `type` value. A parameter text already given
    in an earlier type is not repeated."""
    if not d.get("properties"):
        return f"- {dname}: {_kind(d)}" + (f" — {d['description']}" if d.get("description") else "")
    props = dict(d["properties"])
    label = dname
    if "const" in props.get("type", {}):
        label = f"{dname} (type {json.dumps(props.pop('type')['const'])})"
    req = set(d.get("required") or [])
    parts = []
    for n, p in props.items():
        part = _param(n, p, n in req)
        desc = (p.get("description") or "").rstrip(".")
        if desc and (part, desc) not in said:
            said.add((part, desc))
            part += f" ({desc})"
        parts.append(part)
    return f"- {label}: " + "; ".join(parts)


def _usage_text(name: str, desc: str, schema: Dict[str, Any]) -> str:
    lines = [f"# {name}", "", " ".join(desc.split()), "", "Parameters (* required):"] + (_param_lines(schema) or ["(none)"])
    defs = schema.get("$defs") or {}
    if defs:
        said: set = set()
        lines += ["", "Types:"] + [_def_line(n, d, said) for n, d in defs.items()]
    return "\n".join(lines) + "\n"


def write_usage(full: Dict[str, Any]) -> None:
    """docs/tools/<tool>.md for every tool; stale files of removed tools are deleted."""
    try:
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        for name, (desc, schema) in full.items():
            text = _usage_text(name, desc, schema)
            f = USAGE_DIR / f"{name}.md"
            if not f.is_file() or f.read_text(encoding="utf-8") != text:
                f.write_text(text, encoding="utf-8")
        for f in USAGE_DIR.glob("*.md"):
            if f.stem not in full:
                f.unlink()
    except OSError:
        pass  # a read-only copy still serves


def shorten(server: Any, own: set) -> None:
    """own: this fork's tools, whose docstrings are already written short and carry the how-to."""
    full: Dict[str, Any] = {}
    for name, tool in server._tool_manager._tools.items():
        if name in DESC:
            tool.description = DESC[name]
        elif name not in own:
            tool.description = _short(tool.description, 220)
        whole = json.loads(json.dumps(tool.parameters))  # the usage file keeps every parameter text whole
        _trim_schema(whole, 10_000)
        _short_defs(whole)
        full[name] = (tool.description, whole)
        _trim_schema(tool.parameters)
        _short_defs(tool.parameters)
        tool.description = _short(tool.description, 140)
        _slim(tool.parameters)
        if name == "browser_actions":  # the 12 action variants (3,400 chars) live in the usage file; the server still validates them
            tool.parameters.pop("$defs", None)
            tool.parameters["properties"]["actions"] = {"type": "array", "items": {"type": "object"}}
    write_usage(full)
