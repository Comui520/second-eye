import pytest

from goodprice.analysis.json_extract import extract_json_object
from goodprice.analysis.judge import parse_requirement_typed
from goodprice.analysis.llm import parse_analysis_json, parse_requirement_json


def test_plain_object():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_fenced_with_language_tag():
    raw = '```json\n{"condition_score": 3}\n```'
    assert extract_json_object(raw) == {"condition_score": 3}


def test_surrounding_prose():
    raw = '好的，结论如下：{"matched": true, "reason": "ok"} 希望有帮助'
    assert extract_json_object(raw) == {"matched": True, "reason": "ok"}


def test_braces_inside_string_do_not_break_scan():
    raw = '前言 { "note": "价格 {面议}", "n": 1 } 结尾 }'
    assert extract_json_object(raw) == {"note": "价格 {面议}", "n": 1}


def test_picks_first_complete_object_when_multiple():
    raw = '{"a": 1} 以及 {"b": 2}'
    assert extract_json_object(raw) == {"a": 1}


def test_tolerates_trailing_comma():
    assert extract_json_object('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}


def test_truncated_fence_still_parses():
    raw = '```json\n{"value_score": 8}'
    assert extract_json_object(raw) == {"value_score": 8}


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json_object("抱歉，我无法判断")


def test_empty_raises():
    with pytest.raises(ValueError):
        extract_json_object("   ")


def test_parsers_use_shared_extractor():
    verdict = parse_analysis_json(
        '```json\n{"condition_score": 99, "defects": [], "recommended": false, "reason": "x"}\n```'
    )
    assert verdict["condition_score"] == 10
    assert parse_requirement_json('说明：{"matched": true, "reason": "ok"}') == {
        "matched": True,
        "reason": "ok",
    }
    typed = parse_requirement_typed(
        '```json\n{"matched": true, "probability": 0.9, "reason": "ok"}\n```'
    )
    assert typed["matched"] is True
