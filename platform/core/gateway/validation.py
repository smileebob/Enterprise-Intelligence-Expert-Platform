"""工具入参 JSON Schema 子集校验（离线依赖最小化，覆盖工具契约实际使用的关键字）。

支持：type/required/properties/items/enum/minLength/maxLength/minimum/maximum。
未知关键字不在此处否决（宽松子集）；未声明 type 的子树不做类型推断。
"""

from __future__ import annotations

from typing import Any

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "integer" and isinstance(value, bool):
        return False
    if expected == "number" and isinstance(value, bool):
        return False
    py_types = _JSON_TYPES.get(expected)
    return True if py_types is None else isinstance(value, py_types)


def validate_tool_args(args: Any, schema: dict[str, Any], path: str = "args") -> list[str]:
    """返回违规项列表；空列表表示通过。"""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    expected = schema.get("type")
    if isinstance(expected, str) and not _type_matches(args, expected):
        return [f"{path}: 期望 {expected}"]

    enum = schema.get("enum")
    if isinstance(enum, list) and args not in enum:
        errors.append(f"{path}: 取值不在允许枚举内")

    if isinstance(args, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in args:
                errors.append(f"{path}.{key}: 缺少必填参数")
        properties = schema.get("properties") or {}
        for key, value in args.items():
            if key in properties:
                errors.extend(validate_tool_args(value, properties[key], f"{path}.{key}"))
    elif isinstance(args, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(args):
                errors.extend(validate_tool_args(value, items, f"{path}[{index}]"))
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(args) > max_items:
            errors.append(f"{path}: 元素数量超过上限 {max_items}")
    elif isinstance(args, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(args) < min_length:
            errors.append(f"{path}: 长度小于 {min_length}")
        if isinstance(max_length, int) and len(args) > max_length:
            errors.append(f"{path}: 长度超过 {max_length}")
    elif isinstance(args, (int, float)) and not isinstance(args, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and args < minimum:
            errors.append(f"{path}: 小于最小值 {minimum}")
        if isinstance(maximum, (int, float)) and args > maximum:
            errors.append(f"{path}: 大于最大值 {maximum}")

    return errors
