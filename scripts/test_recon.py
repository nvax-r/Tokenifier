"""Tests for recon.py — privacy-critical prose-stripping behavior."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from recon import strip_prose, PROSE_FIELDS, read_jsonl, Aggregator


class TestStripProse(unittest.TestCase):
    def test_empty_dict_passes_through(self):
        self.assertEqual(strip_prose({}), {})

    def test_primitive_passes_through(self):
        self.assertEqual(strip_prose(42), 42)
        self.assertEqual(strip_prose("not_a_field_value"), "not_a_field_value")
        self.assertEqual(strip_prose(None), None)

    def test_non_prose_key_kept_verbatim(self):
        row = {"model": "claude-opus-4-7", "id": "abc"}
        self.assertEqual(strip_prose(row), {"model": "claude-opus-4-7", "id": "abc"})

    def test_top_level_prose_key_scrubbed(self):
        row = {"text": "secret prompt", "id": "abc"}
        out = strip_prose(row)
        self.assertNotIn("secret", str(out))
        self.assertEqual(out["id"], "abc")
        self.assertTrue(out["text"].startswith("<stripped:"))

    def test_nested_prose_key_scrubbed(self):
        row = {"message": {"content": "secret"}, "id": "abc"}
        out = strip_prose(row)
        self.assertNotIn("secret", str(out))
        self.assertEqual(out["id"], "abc")

    def test_prose_inside_list_scrubbed(self):
        row = {"message": {"content": [{"text": "secret", "type": "text"}]}}
        out = strip_prose(row)
        self.assertNotIn("secret", str(out))
        # Structure preserved: still a list of dicts. But ALL primitive values
        # under "content" (including non-prose siblings like "type") are
        # scrubbed because we are under a prose ancestor.
        self.assertIsInstance(out["message"]["content"], list)
        self.assertIsInstance(out["message"]["content"][0], dict)
        self.assertTrue(out["message"]["content"][0]["type"].startswith("<stripped:"))
        self.assertTrue(out["message"]["content"][0]["text"].startswith("<stripped:"))

    def test_all_documented_prose_fields_covered(self):
        # Each prose field name in PROSE_FIELDS must be scrubbed when used as a key.
        for field in PROSE_FIELDS:
            row = {field: "leak"}
            out = strip_prose(row)
            self.assertNotIn("leak", str(out), f"field {field!r} not scrubbed")

    def test_prose_key_with_dict_value(self):
        """Non-prose keys inside a prose-keyed dict still get their primitive values scrubbed."""
        row = {"input": {"command": "rm -rf /", "cwd": "/etc", "nested": {"thing": "secret"}}}
        out = strip_prose(row)
        self.assertNotIn("rm -rf", str(out))
        self.assertNotIn("/etc", str(out))
        self.assertNotIn("secret", str(out))
        # Structure preserved.
        self.assertIsInstance(out["input"], dict)
        self.assertIn("command", out["input"])
        self.assertIsInstance(out["input"]["nested"], dict)

    def test_prose_key_with_list_of_primitives(self):
        """A list of primitives under a prose key has each item scrubbed."""
        row = {"content": ["user said: secret", "more prose", 42]}
        out = strip_prose(row)
        self.assertNotIn("secret", str(out))
        self.assertNotIn("more prose", str(out))
        # Numeric primitives also scrubbed (any leaf under prose ancestor).
        self.assertNotIn("42", str(out))
        self.assertIsInstance(out["content"], list)
        self.assertEqual(len(out["content"]), 3)

    def test_does_not_mutate_input(self):
        original = {"content": "secret", "id": "abc", "nested": {"text": "leak"}}
        snapshot = {"content": "secret", "id": "abc", "nested": {"text": "leak"}}
        _ = strip_prose(original)
        self.assertEqual(original, snapshot)


class TestReadJsonl(unittest.TestCase):
    def _write_tmp(self, lines):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        f.write("\n".join(lines) + "\n")
        f.close()
        path = Path(f.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_reads_valid_lines(self):
        path = self._write_tmp([
            json.dumps({"id": 1}),
            json.dumps({"id": 2}),
        ])
        rows = list(read_jsonl(path))
        self.assertEqual(rows, [{"id": 1}, {"id": 2}])

    def test_skips_empty_lines(self):
        path = self._write_tmp([
            json.dumps({"id": 1}),
            "",
            json.dumps({"id": 2}),
        ])
        rows = list(read_jsonl(path))
        self.assertEqual(rows, [{"id": 1}, {"id": 2}])

    def test_skips_malformed_lines(self):
        path = self._write_tmp([
            json.dumps({"id": 1}),
            "{not valid json",
            json.dumps({"id": 2}),
        ])
        rows = list(read_jsonl(path))
        self.assertEqual(rows, [{"id": 1}, {"id": 2}])

    def test_malformed_line_message_to_stderr(self):
        """The skip is loud, not silent — caller must see path and line number."""
        path = self._write_tmp([
            json.dumps({"id": 1}),
            "{not valid json",
            json.dumps({"id": 2}),
        ])
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            list(read_jsonl(path))
        err = captured.getvalue()
        self.assertIn("[skip]", err)
        self.assertIn(str(path), err)
        self.assertIn(":2", err)  # line number of the malformed line


class TestAggregator(unittest.TestCase):
    def test_counts_top_level_keys(self):
        agg = Aggregator()
        agg.ingest({"type": "assistant", "model": "x"})
        agg.ingest({"type": "user"})
        self.assertEqual(agg.top_level_keys["type"], 2)
        self.assertEqual(agg.top_level_keys["model"], 1)
        self.assertEqual(agg.row_count, 2)

    def test_collects_models(self):
        agg = Aggregator()
        agg.ingest({"message": {"model": "claude-opus-4-7"}})
        agg.ingest({"message": {"model": "claude-opus-4-7[1m]"}})
        agg.ingest({"message": {"model": "claude-opus-4-7"}})
        self.assertEqual(agg.models["claude-opus-4-7"], 2)
        self.assertEqual(agg.models["claude-opus-4-7[1m]"], 1)

    def test_groups_duplicate_message_ids(self):
        agg = Aggregator()
        agg.ingest({"type": "assistant", "message": {"id": "msg_A"}})
        agg.ingest({"type": "assistant", "message": {"id": "msg_A"}})  # parallel tool use
        agg.ingest({"type": "assistant", "message": {"id": "msg_B"}})
        groups = agg.duplicate_message_id_groups()
        # Only msg_A appears more than once.
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["message_id"], "msg_A")
        self.assertEqual(groups[0]["count"], 2)

    def test_counts_type_markers(self):
        agg = Aggregator()
        agg.ingest({"type": "assistant"})
        agg.ingest({"type": "user"})
        agg.ingest({"type": "assistant"})
        agg.ingest({"no_type_here": True})  # rows without `type` are ignored
        self.assertEqual(agg.type_markers["assistant"], 2)
        self.assertEqual(agg.type_markers["user"], 1)
        self.assertEqual(agg.row_count, 4)

    def test_usage_key_optionality_tracked(self):
        agg = Aggregator()
        agg.ingest({"message": {"usage": {"input_tokens": 100, "output_tokens": 50}}})
        agg.ingest({"message": {"usage": {"input_tokens": 0, "output_tokens": 50,
                                          "cache_read_input_tokens": 10}}})
        # input_tokens present in both rows (1 zero, 1 non-zero).
        self.assertEqual(agg.usage_keys_present["input_tokens"], 2)
        self.assertEqual(agg.usage_keys_zero["input_tokens"], 1)
        # cache_read_input_tokens present in 1 row only.
        self.assertEqual(agg.usage_keys_present["cache_read_input_tokens"], 1)


class TestSampleCollection(unittest.TestCase):
    def test_one_sample_per_category_kept(self):
        agg = Aggregator()
        agg.ingest({"type": "assistant", "message": {"id": "1", "role": "assistant"}})
        agg.ingest({"type": "assistant", "message": {"id": "2", "role": "assistant"}})
        agg.ingest({"type": "user", "message": {"id": "3", "role": "user"}})
        # Two assistant rows, one user row → two distinct categories stored.
        self.assertIn("assistant", agg.samples)
        self.assertIn("user", agg.samples)
        # First-seen-wins: assistant sample is the one with id=1.
        self.assertEqual(agg.samples["assistant"]["message"]["id"], "1")

    def test_sidechain_categorized_separately(self):
        agg = Aggregator()
        agg.ingest({"type": "assistant", "isSidechain": True, "message": {"id": "1"}})
        agg.ingest({"type": "assistant", "message": {"id": "2"}})
        self.assertIn("sidechain", agg.samples)
        self.assertIn("assistant", agg.samples)

    def test_error_categorized_separately(self):
        agg = Aggregator()
        agg.ingest({"type": "assistant", "isApiErrorMessage": True, "message": {"id": "1"}})
        self.assertIn("error", agg.samples)

    def test_samples_have_prose_stripped(self):
        agg = Aggregator()
        agg.ingest({"type": "assistant", "message": {"id": "1", "content": "SECRET PROMPT"}})
        self.assertNotIn("SECRET PROMPT", str(agg.samples))

    def test_error_beats_sidechain_when_both_flags_set(self):
        """Precedence: isApiErrorMessage takes priority over isSidechain."""
        agg = Aggregator()
        agg.ingest({
            "type": "assistant",
            "isApiErrorMessage": True,
            "isSidechain": True,
            "message": {"id": "1"},
        })
        self.assertIn("error", agg.samples)
        self.assertNotIn("sidechain", agg.samples)
        self.assertNotIn("assistant", agg.samples)

    def test_row_without_category_not_sampled(self):
        """A row with no type, no error flag, and no sidechain flag is silently excluded."""
        agg = Aggregator()
        agg.ingest({"foo": "bar", "message": {"id": "1"}})
        self.assertEqual(agg.samples, {})
        # row_count still increments — only sample storage skips it.
        self.assertEqual(agg.row_count, 1)


if __name__ == "__main__":
    unittest.main()
