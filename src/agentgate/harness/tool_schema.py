"""AgentDojo 툴 인자(pydantic JSON schema)를 인자별 평면 설명으로 바꾼다. 어댑터들이 함께 쓴다."""

from __future__ import annotations

from dataclasses import dataclass

JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


@dataclass
class Argument:
    name: str
    types: list[str]            # JSON schema types without "null"
    description: str
    required: bool
    nullable: bool
    items: dict | None = None
    enum: list | None = None


def _resolve(node: dict, defs: dict) -> dict:
    while "$ref" in node:
        node = {**defs.get(node["$ref"].split("/")[-1], {}), **{k: v for k, v in node.items() if k != "$ref"}}
    if "allOf" in node and len(node["allOf"]) == 1:
        node = {**_resolve(node["allOf"][0], defs), **{k: v for k, v in node.items() if k != "allOf"}}
    return node


def _types(node: dict, defs: dict) -> tuple[list[str], bool, dict | None, list | None]:
    node = _resolve(node, defs)
    if "anyOf" in node or "oneOf" in node:
        types, nullable, items, enum = [], False, None, None
        for option in node.get("anyOf") or node.get("oneOf"):
            sub_types, sub_nullable, sub_items, sub_enum = _types(option, defs)
            nullable = nullable or sub_nullable or sub_types == ["null"]
            types += [t for t in sub_types if t != "null" and t not in types]
            items, enum = items or sub_items, enum or sub_enum
        return types or ["string"], nullable, items, enum
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind] if kind else (["string"] if "enum" in node else ["object"])
    nullable = "null" in kinds
    if kinds == ["null"]:
        return ["null"], True, None, None
    items = _resolve(node["items"], defs) if "items" in node else None
    if items is not None:
        items = {k: v for k, v in items.items() if k in ("type", "enum", "description")} or {"type": "string"}
    return [k for k in kinds if k in JSON_TYPES and k != "null"] or ["string"], nullable, items, node.get("enum")


def arguments(schema: dict) -> list[Argument]:
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    result = []
    for name, node in (schema.get("properties") or {}).items():
        types, nullable, items, enum = _types(node, defs)
        if types == ["array"] and items is None:
            items = {"type": "string"}
        description = node.get("description") or _resolve(node, defs).get("description") or name
        result.append(Argument(name, types, description, name in required, nullable or name not in required,
                               items, enum))
    return result
