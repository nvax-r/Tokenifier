"""Tests for tokenifier.parser."""
import json
import tempfile
from pathlib import Path

import pytest

from tokenifier.parser import parse_session


@pytest.fixture
def tmp_jsonl():
    """Yield a function that writes rows to a temp .jsonl and returns its Path."""
    paths: list[Path] = []

    def _write(rows):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        for row in rows:
            f.write(json.dumps(row) + "\n")
        f.close()
        path = Path(f.name)
        paths.append(path)
        return path

    yield _write
    for p in paths:
        p.unlink(missing_ok=True)


def _assistant_row(msg_id="msg_1", model="claude-opus-4-7", input_tokens=1000, output_tokens=500):
    """Minimal valid assistant row matching the recon-validated schema."""
    return {
        "type": "assistant",
        "timestamp": "2026-05-05T12:00:00.000Z",
        "isSidechain": False,
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }


def _user_prompt_row():
    """A fresh user prompt: content is a string. Triggers a new talk boundary."""
    return {
        "type": "user",
        "timestamp": "2026-05-05T12:00:00.000Z",
        "message": {
            "role": "user",
            "content": "PROMPT_TEXT_PLACEHOLDER",
        },
    }


def _tool_result_row():
    """A tool-result user row: content[0].type == 'tool_result'. Mid-talk, NOT a boundary."""
    return {
        "type": "user",
        "timestamp": "2026-05-05T12:00:00.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "result"}],
        },
    }


# ---- Filtering ----

def test_assistant_row_creates_one_talk(tmp_jsonl):
    path = tmp_jsonl([_assistant_row()])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turn_count == 1
    assert talks[0].turns[0].message_id == "msg_1"


def test_filters_non_assistant_rows(tmp_jsonl):
    path = tmp_jsonl([
        {"type": "system", "subtype": "turn_duration"},
        _assistant_row(msg_id="msg_a"),
        {"type": "attachment"},
    ])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turns[0].message_id == "msg_a"


def test_filters_api_error_messages(tmp_jsonl):
    err = _assistant_row(msg_id="msg_err")
    err["isApiErrorMessage"] = True
    path = tmp_jsonl([err, _assistant_row(msg_id="msg_ok")])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turn_count == 1
    assert talks[0].turns[0].message_id == "msg_ok"


def test_filters_synthetic_model(tmp_jsonl):
    syn = _assistant_row(msg_id="msg_syn", model="<synthetic>")
    path = tmp_jsonl([syn, _assistant_row(msg_id="msg_ok")])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turns[0].message_id == "msg_ok"


def test_dedupes_by_message_id(tmp_jsonl):
    """Parallel tool calls in one turn share message.id — recon found groups of 13."""
    path = tmp_jsonl([
        _assistant_row(msg_id="msg_dup", input_tokens=100),
        _assistant_row(msg_id="msg_dup", input_tokens=200),
        _assistant_row(msg_id="msg_other", input_tokens=300),
    ])
    talks = parse_session(path)
    # All three rows fall within one (boundary-less) talk; dedupe gives 2 turns.
    assert len(talks) == 1
    assert talks[0].turn_count == 2
    assert talks[0].turns[0].message_id == "msg_dup"
    assert talks[0].turns[0].usage.input_tokens == 100
    assert talks[0].turns[1].message_id == "msg_other"


def test_handles_extra_usage_keys(tmp_jsonl):
    """Recon found 6 undocumented usage sub-keys — Usage(extra='allow') must accept them."""
    row = _assistant_row()
    row["message"]["usage"].update({
        "service_tier": "standard",
        "cache_creation": {"ephemeral_1h_input_tokens": 50, "ephemeral_5m_input_tokens": 0},
        "inference_geo": "us",
        "server_tool_use": {"web_search_requests": 0},
        "iterations": [],
        "speed": "standard",
    })
    path = tmp_jsonl([row])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turn_count == 1


def test_skips_malformed_rows(tmp_jsonl):
    """Rows missing required fields (e.g. no timestamp) are skipped, not raised."""
    bad = _assistant_row(msg_id="msg_bad")
    del bad["timestamp"]
    path = tmp_jsonl([bad, _assistant_row(msg_id="msg_ok")])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turns[0].message_id == "msg_ok"


# ---- Talk grouping ----

def test_user_prompt_starts_new_talk(tmp_jsonl):
    path = tmp_jsonl([
        _assistant_row(msg_id="msg_a"),
        _user_prompt_row(),
        _assistant_row(msg_id="msg_b"),
        _user_prompt_row(),
        _assistant_row(msg_id="msg_c"),
    ])
    talks = parse_session(path)
    assert len(talks) == 3
    assert [t.turns[0].message_id for t in talks] == ["msg_a", "msg_b", "msg_c"]


def test_tool_result_does_NOT_start_new_talk(tmp_jsonl):
    """Tool-result user rows are mid-talk plumbing, not boundaries."""
    path = tmp_jsonl([
        _assistant_row(msg_id="msg_a"),
        _tool_result_row(),
        _assistant_row(msg_id="msg_b"),
        _tool_result_row(),
        _assistant_row(msg_id="msg_c"),
    ])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turn_count == 3


def test_user_prompt_with_text_content_list_starts_new_talk(tmp_jsonl):
    """Some user prompts have content=[{type: 'text', ...}] instead of a plain string."""
    text_prompt = {
        "type": "user",
        "timestamp": "2026-05-05T12:00:00.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "PROMPT"}],
        },
    }
    path = tmp_jsonl([
        _assistant_row(msg_id="msg_a"),
        text_prompt,
        _assistant_row(msg_id="msg_b"),
    ])
    talks = parse_session(path)
    assert len(talks) == 2


def test_talk_aggregates_output_across_turns(tmp_jsonl):
    """A talk's total_output sums output_tokens across every constituent turn."""
    path = tmp_jsonl([
        _assistant_row(msg_id="msg_a", output_tokens=300),
        _tool_result_row(),
        _assistant_row(msg_id="msg_b", output_tokens=500),
        _tool_result_row(),
        _assistant_row(msg_id="msg_c", output_tokens=200),
    ])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turn_count == 3
    assert talks[0].total_output == 1000


def test_empty_talks_dropped(tmp_jsonl):
    """A user prompt with no assistant response → no Talk emitted."""
    path = tmp_jsonl([
        _user_prompt_row(),
        # no assistant response
        _user_prompt_row(),
        _assistant_row(msg_id="msg_b"),
    ])
    talks = parse_session(path)
    assert len(talks) == 1
    assert talks[0].turns[0].message_id == "msg_b"


# ---- 1M context auto-detection ----

def test_infers_1m_variant_when_input_exceeds_default_window(tmp_jsonl):
    path = tmp_jsonl([_assistant_row(msg_id="msg_big", input_tokens=250_000)])
    talks = parse_session(path)
    assert talks[0].turns[0].model == "claude-opus-4-7[1m]"


def test_no_promotion_when_input_within_default(tmp_jsonl):
    path = tmp_jsonl([_assistant_row(msg_id="msg_small", input_tokens=100_000)])
    talks = parse_session(path)
    assert talks[0].turns[0].model == "claude-opus-4-7"


def test_promotion_applies_to_all_turns_of_same_model(tmp_jsonl):
    path = tmp_jsonl([
        _assistant_row(msg_id="msg_small", input_tokens=50_000),
        _assistant_row(msg_id="msg_big", input_tokens=300_000),
        _assistant_row(msg_id="msg_smaller", input_tokens=80_000),
    ])
    talks = parse_session(path)
    all_turns = [t for talk in talks for t in talk.turns]
    assert all(t.model == "claude-opus-4-7[1m]" for t in all_turns)


def test_promotion_per_model_not_global(tmp_jsonl):
    path = tmp_jsonl([
        _assistant_row(msg_id="msg_a", model="claude-opus-4-7", input_tokens=300_000),
        _assistant_row(msg_id="msg_b", model="claude-opus-4-6", input_tokens=100_000),
    ])
    talks = parse_session(path)
    all_turns = [t for talk in talks for t in talk.turns]
    models = {t.model for t in all_turns}
    assert "claude-opus-4-7[1m]" in models
    assert "claude-opus-4-6" in models
    assert "claude-opus-4-6[1m]" not in models
