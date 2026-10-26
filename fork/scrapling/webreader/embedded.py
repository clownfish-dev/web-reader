"""Framework data embedded in a page's HTML (Next, Nuxt, Remix, window.__X__ = {...}), read from
the HTML source without running the page's scripts. Port of web-reader's embeddedState /
embeddedReport, so the result shape matches (found / selected / find / data)."""

import json
import math
import re
from typing import Any, Dict, List, Optional

_DECODER = json.JSONDecoder()
_SCRIPT = re.compile(r"<script\b([^>]*)>([\s\S]*?)</script\s*>", re.I)
_NEXT_PUSH = re.compile(r"self\.__next_f\.push\((\[[\s\S]*\])\)\s*;?\s*$")
_GLOBAL = re.compile(r"window(?:\.(__[A-Za-z0-9_]+__)|\[\s*[\"'](__[A-Za-z0-9_]+__)[\"']\s*\])\s*=\s*")
_NOISE = re.compile(r"^__(REACT_DEVTOOLS|VUE_DEVTOOLS|VUE|NEXT_DATA|NEXT_F|NUXT|zone_symbol|SENTRY|webpack|PRERENDER|REACT_REFRESH|INTL)", re.I)
LIMIT = 8_000_000


def _attr(attrs: str, name: str) -> Optional[str]:
    m = re.search(rf"\b{name}\s*=\s*[\"']?([^\"'\s>]+)", attrs, re.I)
    return m.group(1) if m else None


def _revive_nuxt(nd: list) -> Any:
    memo: Dict[int, Any] = {}

    def rev(i: Any, depth: int = 0) -> Any:
        if not isinstance(i, int) or isinstance(i, bool):
            return i
        special = {-1: None, -2: None, -3: math.nan, -4: math.inf, -5: -math.inf, -6: -0.0}
        if i in special:
            return special[i]
        if i in memo:
            return memo[i]
        if depth > 200 or i >= len(nd):
            return "[deep]"
        v = nd[i]
        if v is None or not isinstance(v, (list, dict)):
            memo[i] = v
            return v
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                tag = v[0]
                if tag in ("Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef", "NuxtError", "Island"):
                    r = None if tag.startswith("Empty") else rev(v[1], depth + 1)
                    memo[i] = r
                    return r
                if tag in ("Date", "BigInt"):
                    return v[1]
                if tag == "Set":
                    return [rev(x, depth + 1) for x in v[1:]]
                if tag == "Map":
                    return {str(rev(v[k], depth + 1)): rev(v[k + 1], depth + 1) for k in range(1, len(v) - 1, 2)}
                if tag == "null":
                    return {v[k]: rev(v[k + 1], depth + 1) for k in range(1, len(v) - 1, 2)}
            a: list = []
            memo[i] = a
            a.extend(rev(x, depth + 1) for x in v)
            return a
        o: dict = {}
        memo[i] = o
        for k, x in v.items():
            o[k] = rev(x, depth + 1)
        return o

    return rev(0)


def embedded_state(html: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    chunks: List[Any] = []
    for attrs, body in _SCRIPT.findall(html or ""):
        sid = (_attr(attrs, "id") or "").strip()
        if sid == "__NEXT_DATA__":
            try:
                data["next_data"] = json.loads(body)
            except ValueError:
                errors["next_data"] = "parse"
            continue
        if sid == "__NUXT_DATA__":
            try:
                nd = json.loads(body)
                if isinstance(nd, list):
                    data["nuxt_data"] = _revive_nuxt(nd)
            except (ValueError, RecursionError) as e:
                errors["nuxt_data"] = str(e)[:100]
            continue
        if _attr(attrs, "src"):
            continue
        m = _NEXT_PUSH.search(body.strip())
        if m:
            try:
                chunks.append(json.loads(m.group(1)))
            except ValueError:
                pass
            continue
        for g in _GLOBAL.finditer(body):
            name = g.group(1) or g.group(2)
            if _NOISE.match(name) or len(data) > 20:
                continue
            try:
                value, _ = _DECODER.raw_decode(body, g.end())
            except ValueError:
                continue  # a JavaScript literal that is not JSON
            if isinstance(value, (dict, list)) and value:
                data[name.strip("_").lower()] = value
        if "remixContext" in body:
            g = re.search(r"window\.__remixContext\s*=\s*", body)
            if g:
                try:
                    value, _ = _DECODER.raw_decode(body, g.end())
                    data["remix"] = (value.get("state") or {}).get("loaderData", value)
                except (ValueError, AttributeError):
                    pass
    if chunks:
        text = "".join(x[1] for x in chunks if isinstance(x, list) and len(x) > 1 and x[0] == 1 and isinstance(x[1], str))
        rows = []
        for line in text.split("\n"):
            m = re.match(r"^([0-9a-z]+):(.*)$", line, re.I)
            if not m:
                continue
            p = m.group(2)
            if re.match(r"^[A-Z][\[{]", p):
                p = p[1:]
            if not re.match(r"^[\[{\"]", p):
                continue
            try:
                rows.append({"id": m.group(1), "data": json.loads(p)})
            except ValueError:
                pass
        if rows:
            data["next_f"] = rows
        elif text:
            data["next_f_text"] = text[:500000]
    for k, v in list(data.items()):
        size = len(json.dumps(v, ensure_ascii=False, default=str))
        if size > LIMIT:
            data[k] = {"__cut": True, "chars": size, "head": json.dumps(v, ensure_ascii=False, default=str)[:200000]}
    return {"data": data, "errors": errors or None}


def _parts(p: str) -> List[str]:
    return [x for x in re.sub(r"\[(\d+)\]", r".\1", str(p)).split(".") if x]


def _get(root: Any, path: str) -> Any:
    v = root
    for k in _parts(path):
        if isinstance(v, dict):
            v = v.get(k)
        elif isinstance(v, list) and k.isdigit() and int(k) < len(v):
            v = v[int(k)]
        else:
            return None
    return v


def shape(v: Any, depth: int = 0) -> Any:
    if v is None:
        return "null"
    if isinstance(v, list):
        if not v:
            return "array[0]"
        return f"array[{len(v)}]" if depth >= 2 else {f"array[{len(v)}]": shape(v[0], depth + 1)}
    if isinstance(v, dict):
        ks = list(v)
        if depth >= 2:
            return "object{" + ",".join(ks[:12]) + (",…" if len(ks) > 12 else "") + "}"
        o = {k: shape(v[k], depth + 1) for k in ks[:30]}
        if len(ks) > 30:
            o["…"] = f"{len(ks) - 30} more keys"
        return o
    return {bool: "boolean", int: "number", float: "number", str: "string"}.get(type(v), type(v).__name__)


def _cap(v: Any, limit: int) -> Any:
    s = json.dumps(v, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return v
    return {"cut": True, "chars": len(s), "shape": shape(v), "head": s[:limit]}


def _find(root: Any, term: str, limit: int = 40) -> List[dict]:
    t = term.lower()
    hits: List[dict] = []
    nodes = [0]

    def walk(v: Any, p: str, d: int) -> None:
        if len(hits) >= limit or nodes[0] > 300000 or d > 40:
            return
        nodes[0] += 1
        items = v.items() if isinstance(v, dict) else enumerate(v) if isinstance(v, list) else ()
        for k, x in items:
            q = f"{p}[{k}]" if isinstance(v, list) else (f"{p}.{k}" if p else str(k))
            if isinstance(v, dict) and t in str(k).lower():
                hits.append({"path": q, "key_match": True, "value": _cap(x, 300)})
            elif isinstance(x, str) and t in x.lower():
                hits.append({"path": q, "value": x[:300] + ("…" if len(x) > 300 else "")})
            elif isinstance(x, (int, float)) and not isinstance(x, bool) and str(x) == term:
                hits.append({"path": q, "value": x})
            if len(hits) >= limit:
                return
            walk(x, q, d + 1)

    walk(root, "", 0)
    return hits


def embedded_report(st: Dict[str, Any], paths: Optional[List[str]] = None, find: Optional[str] = None, max_chars: int = 20000) -> Dict[str, Any]:
    data = st["data"]
    names = list(data)
    if not names:
        out: Dict[str, Any] = {"found": [], "note": "페이지에 박힌 프레임워크 JSON(__NEXT_DATA__, __next_f, __NUXT_DATA__, window.__X__ 등)을 찾지 못했어요."}
        if st.get("errors"):
            out["errors"] = st["errors"]
        return out
    found = []
    for k in names:
        v = data[k]
        cut = isinstance(v, dict) and v.get("__cut")
        found.append({"source": k, "chars": v["chars"] if cut else len(json.dumps(v, ensure_ascii=False, default=str)), **({"too_large": True} if cut else {"shape": shape(v)})})
    out = {"found": found}
    if st.get("errors"):
        out["errors"] = st["errors"]
    paths = paths or []
    if paths:
        each = max(1000, max_chars // len(paths))
        out["selected"] = {}
        for p in paths:
            head, *rest = _parts(p) or [""]
            if head in names:
                v = _get(data[head], ".".join(rest)) if rest else data[head]
            else:
                v = next((x for x in (_get(data[n], p) for n in names) if x is not None), None)
            out["selected"][p] = "(없음)" if v is None else _cap(v, each)
    if find:
        out["find"] = _find(data, find)
    if not paths and not find:
        total = sum(f["chars"] for f in found)
        if total <= max_chars:
            out["data"] = data
        else:
            out["note"] = f'JSON이 {total}자로 커서 구조(shape)만 보여 줘요. json_paths(예: "next_data.props.pageProps")나 json_find(키워드)로 골라 받으세요.'
    return out
