"""Minimal JSON Schema validator.

Deliberately hand-rolled: pulling in jsonschema would hide the part that
matters in an interview, namely *where* you validate and *what you do* when
validation fails (protocol error vs. isError result).
"""

import re

TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate(schema, value, path="$"):
    errors = []
    if not schema:
        return errors

    expected = schema.get("type")
    if expected and expected in TYPE_MAP:
        py_type = TYPE_MAP[expected]
        if expected == "integer" and isinstance(value, bool):
            errors.append(f"{path}: expected integer, got boolean")
            return errors
        if not isinstance(value, py_type):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")

    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: length {len(value)} exceeds maxLength {schema['maxLength']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: length {len(value)} below minLength {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: {value!r} does not match pattern {schema['pattern']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required field missing")
        for key, sub_value in value.items():
            if key in props:
                errors.extend(validate(props[key], sub_value, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: unexpected field")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(validate(schema["items"], item, f"{path}[{index}]"))

    return errors
