"""从 LLM 文本输出中稳健地提取 JSON 对象。

LLM 常把 JSON 包在 ```json 围栏里、或在前后夹带解释文字，也偶尔多出尾随逗号。
这里先用正则剥掉围栏，再用括号配平扫描取出第一个完整对象（能正确跳过字符串内
的括号与转义）；比原先的 ``find("{")``/``rfind("}")`` 更稳：后者一旦输出里出现
多个对象、或字符串中含有大括号，就会取错范围。
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*(.*?)\s*```", re.DOTALL)


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1)
    # 只有开头围栏、没有结尾（输出被截断）时，去掉开头标记
    if text.lstrip().startswith("```"):
        return re.sub(r"^\s*```[a-zA-Z0-9_-]*\s*", "", text, count=1)
    return text


def _find_balanced(text: str) -> str | None:
    """返回第一个括号配平的 ``{...}`` 片段；跳过字符串内的括号与转义。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def extract_json_object(raw: str) -> dict[str, Any]:
    """从任意 LLM 文本中提取第一个 JSON 对象；失败时抛 ``ValueError``。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"LLM 输出为空: {raw!r}")

    block = _find_balanced(_strip_fence(text))
    if block is not None:
        for candidate in (block, _remove_trailing_commas(block)):
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data

    raise ValueError(f"LLM 输出中没有可解析的 JSON: {raw!r}")
