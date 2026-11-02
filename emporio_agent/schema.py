"""Builds Gemini function declarations from the tool functions.

The SDK's automatic conversion drops Literal enums and per-argument
descriptions, which are exactly what keeps the model from inventing
categories or topics. So we build a JSON Schema with pydantic, using the
type hints and the Google-style "Args:" section of each docstring.
"""

import inspect
import re
from typing import Callable

from google.genai import types
from pydantic import Field, create_model


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    doc = inspect.cleandoc(doc or "")
    summary, _, args_block = doc.partition("Args:")
    arg_docs: dict[str, str] = {}
    current = None
    for line in args_block.splitlines():
        match = re.match(r"^\s{4}(\w+):\s*(.*)$", line)
        if match:
            current = match.group(1)
            arg_docs[current] = match.group(2).strip()
        elif current and line.strip():
            arg_docs[current] += " " + line.strip()
    return " ".join(summary.split()), arg_docs


def declaration_for(func: Callable) -> types.FunctionDeclaration:
    description, arg_docs = _parse_docstring(func.__doc__)
    fields = {}
    for name, param in inspect.signature(func).parameters.items():
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (param.annotation, Field(default, description=arg_docs.get(name)))
    schema = create_model(func.__name__, **fields).model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return types.FunctionDeclaration(
        name=func.__name__, description=description, parameters_json_schema=schema
    )
