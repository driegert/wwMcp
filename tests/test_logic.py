import json

import pytest

from wwmcp import logic
from wwmcp.config import ConfigError, load_profile


def write_cfg(tmp_path, cfg):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    return {"WWMCP_CONFIG": str(p)}


CFG = {
    "default_profile": "dev",
    "profiles": {
        "dev": {"base_url": "http://dev:8000/", "api_key": "k1"},
        "prod": {"base_url": "https://prod", "api_key": "k2"},
    },
}


def test_profile_default_and_env(tmp_path):
    env = write_cfg(tmp_path, CFG)
    assert load_profile(env).name == "dev"
    assert load_profile(env).base_url == "http://dev:8000"  # trailing slash dropped
    assert load_profile({**env, "WWMCP_PROFILE": "prod"}).api_key == "k2"


def test_profile_errors(tmp_path):
    env = write_cfg(tmp_path, CFG)
    with pytest.raises(ConfigError, match="not in"):
        load_profile({**env, "WWMCP_PROFILE": "nope"})
    with pytest.raises(ConfigError, match="no profile selected"):
        load_profile(write_cfg(tmp_path, {"profiles": CFG["profiles"]}))
    with pytest.raises(ConfigError, match="needs both"):
        load_profile(write_cfg(tmp_path, {"default_profile": "x", "profiles": {"x": {"base_url": "u"}}}))
    with pytest.raises(ConfigError, match="no config"):
        load_profile({"WWMCP_CONFIG": str(tmp_path / "absent.json")})


def test_wire_true_false():
    w = logic.to_wire_question({"format": "tf", "text": "s", "is_true": False, "tags": ["t"]})
    assert w["format"] == "true_false" and "is_true" not in w
    assert w["answer"]["choices"] == [
        {"text": "True", "correct": False},
        {"text": "False", "correct": True},
    ]
    assert w["answer"]["randomize_choices"] is False and w["tags"] == ["t"]


def test_wire_multiple_choice_and_passthrough():
    ch = [{"text": "a", "correct": True}, {"text": "b", "correct": False}]
    w = logic.to_wire_question({"format": "mc", "text": "s", "choices": ch})
    assert w["answer"] == {"format": "multiple_choice", "randomize_choices": True, "choices": ch}
    assert "choices" not in w
    full = {"format": "multiple_choice", "text": "s", "answer": {"format": "multiple_choice", "choices": ch}}
    assert logic.to_wire_question(full) == full


def test_wire_rejects_unconvertible_answers():
    for bad in (
        {"format": "tf", "text": "s", "is_true": "false"},
        {"format": "tf", "text": "s", "choices": []},
        {"format": "mc", "text": "s", "is_true": True},
        {"format": "mc", "text": "s", "choices": "a,b"},
        {"format": "essay", "text": "s", "is_true": True},
        {"format": "tf", "text": "s", "is_true": True, "answer": {}},
    ):
        with pytest.raises(ValueError):
            logic.to_wire_question(bad)


def test_wire_pinned_format_wins():
    # an edit cannot switch format: the stored row's format is pinned
    w = logic.to_wire_question({"format": "mc", "is_true": True}, fmt="true_false")
    assert w["format"] == "true_false" and w["answer"]["format"] == "true_false"


def test_valid_id():
    for good in ("q_dave_0001", "sm_dave_01", "sm_01", "tim_a-b_12"):
        assert logic.valid_id(good)
    for bad in ("", "q/../x", "q x", "../etc", "q?x=1", "q%2f", "_q", None):
        assert not logic.valid_id(bad)


def test_config_shape_errors(tmp_path):
    for cfg in ([], {"profiles": []}, {"default_profile": "x", "profiles": {"x": None}}):
        with pytest.raises(ConfigError):
            load_profile(write_cfg(tmp_path, cfg))


def test_wire_partial_change_has_no_answer():
    # an edit that only changes the text must not invent an answer
    assert logic.to_wire_question({"format": "true_false", "text": "new"}) == {
        "format": "true_false", "text": "new"}


ROWS = [
    {"question_id": "q1", "format": "true_false", "status": "draft", "tags": ["a"], "text": "x",
     "answer": {"choices": [{"text": "True", "correct": False}, {"text": "False", "correct": True}]}},
    {"question_id": "q2", "format": "multiple_choice", "status": "approved", "tags": ["a", "b"], "text": "y",
     "answer": {"choices": [{"text": "p", "correct": True}]}},
    {"question_id": "q3", "format": "true_false", "status": "approved", "tags": [], "text": "z"},
]


def test_filter_questions():
    ids = lambda rows: [r["question_id"] for r in rows]
    assert ids(logic.filter_questions(ROWS, tag="a")) == ["q1", "q2"]
    assert ids(logic.filter_questions(ROWS, tag="b", fmt="mc")) == ["q2"]
    assert ids(logic.filter_questions(ROWS, fmt="tf", status="approved")) == ["q3"]
    assert ids(logic.filter_questions(ROWS, question_id="q1")) == ["q1"]
    assert ids(logic.filter_questions(ROWS)) == ["q1", "q2", "q3"]


def test_compact_question():
    c = logic.compact_question(ROWS[0])
    assert c == {"question_id": "q1", "format": "true_false", "status": "draft",
                 "tags": ["a"], "text": "x", "correct": "False"}
    assert logic.compact_question(ROWS[2])["correct"] is None


def test_gates():
    assert logic.draft_gate({"status": "draft"}, "q1", "live") is None
    assert "live" in logic.draft_gate({"status": "approved"}, "q1", "live")
    assert "retired" in logic.draft_gate({"status": "retired"}, "q1", "live")
    assert "no such" in logic.draft_gate(None, "q1", "live")
    assert logic.live_gate({"status": "approved"}, "q1", "draft_tool") is None
    assert "draft_tool" in logic.live_gate({"status": "draft"}, "q1", "draft_tool")
    assert "retired" in logic.live_gate({"status": "retired"}, "q1", "draft_tool")
    assert "no such" in logic.live_gate(None, "q1", "draft_tool")


def test_scenario_view():
    row = {"scenario_id": "sm_x_01", "status": "draft", "fields_json": '{"title": "T"}'}
    assert logic.scenario_view(row, False) == {"scenario_id": "sm_x_01", "status": "draft"}
    assert logic.scenario_view(row, True)["fields"] == {"title": "T"}
    assert "fields_json" not in logic.scenario_view(row, True)
    assert logic.scenario_view({"fields_json": "not json"}, True)["fields"] is None


def test_read_source(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("x" * logic.SOURCE_TEXT_MAX)
    assert logic.read_source(str(ok))[1] is None
    big = tmp_path / "big.txt"
    big.write_text("x" * (logic.SOURCE_TEXT_MAX + 1))
    text, err = logic.read_source(str(big))
    assert text is None and "40001" in err and "truncation" in err
    empty = tmp_path / "e.txt"
    empty.write_text("  \n")
    assert "empty" in logic.read_source(str(empty))[1]
    assert "not a file" in logic.read_source(str(tmp_path / "nope"))[1]
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\xff\xfe\x00")
    assert "UTF-8" in logic.read_source(str(binary))[1]


def test_trim_preview():
    resp = {
        "preview_seed": 3,
        "payload": {
            "template": {"title": "T"}, "question_text": "Q",
            "questions": [{"id": "hypotheses", "text": "State...", "answer": {"x": 1}}],
            "substitutions": {"null_value": 16, "p_value": 0.01, "sample_mean": 15.3},
            "solution": {"big": 1}, "data": {"values": [1, 2]},
        },
        "variants": [{"focus": "null", "direction": "less", "question_text": "V", "extra": 1}],
    }
    out = logic.trim_preview(resp)
    assert out["title"] == "T" and out["question_text"] == "Q" and out["preview_seed"] == 3
    assert out["parts"] == [{"id": "hypotheses", "text": "State..."}]
    assert out["drawn"] == {"null_value": 16, "sample_mean": 15.3}
    assert out["variants"] == [{"focus": "null", "direction": "less", "question_text": "V"}]
    assert "solution" not in out and "data" not in out
