#!/usr/bin/env python3
"""Shared gate rules (scripts/qc_gate.py) — fake Jev, no TypeSafe network."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import qc_gate  # noqa: E402

SPECS = {
    "pick": {"type": "Choice", "instructions": "pick", "criteria": {"a": "A", "b": "B"}},
    "ok": {"type": "Noul", "instructions": "ok?"},
}


class FakeJev:
    def __init__(self, answers=None, fail=(), key=True):
        self.answers = answers or {}
        self.fail = set(fail)
        self.key = key
        self.calls = 0

    def key_present(self):
        return self.key

    def ask(self, state, questions, *, label):
        self.calls += 1
        if label in self.fail:
            raise RuntimeError("secret detail that must not be saved")
        return {"model": "jev-test", "answers": self.answers.get(label, {})}


def packet_for(det_ok=True):
    return {
        "det_ok": det_ok,
        "calls": [
            {"id": "policy:pick", "label": "pick_v1", "questions": ["pick"], "state": {}},
            {"id": "policy:ok", "label": "ok_v1", "questions": ["ok"], "state": {}},
        ],
    }


GOOD = {
    "pick_v1": {"pick": {"type": "choice", "choice": "a"}},
    "ok_v1": {"ok": {"type": "noul", "noul": 0.9}},
}


def deterministic(packet, checks):
    checks.add("det", packet.get("det_ok", True), "")


def jev_checks(look, checks):
    got = look.choice("policy:pick", "pick")
    checks.add("pick", got in {None, "a"}, f"choice={got}")


def evaluate_gate(packet, judgments):
    return qc_gate.evaluate(packet, judgments, deterministic, jev_checks)


def run(root: Path, argv, jev=None, det_ok=True):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = qc_gate.main(
            ["--root", str(root), *argv],
            description="test",
            default_root=root,
            packet_name="t-packet.json",
            judgments_name="t-judgments.json",
            build_packet=lambda _root: packet_for(det_ok),
            evaluate_gate=evaluate_gate,
            specs=SPECS,
            summary=lambda _pk: "",
            jev_module=jev,
        )
    return rc


class GateRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        self.judgments = self.root / "scripts" / "t-judgments.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_prior(self):
        prior = {"ran": True, "complete": True, "model": "jev-prior", "calls": [], "marker": "prior"}
        self.judgments.write_text(json.dumps(prior), encoding="utf-8")

    def test_all_calls_error_is_not_ran_and_exits_2(self):
        self.write_prior()
        jev = FakeJev(fail={"pick_v1", "ok_v1"})
        j = qc_gate.run_jev(packet_for(), SPECS, jev_module=jev)
        self.assertFalse(j["ran"])
        self.assertTrue(j["attempted"])
        self.assertEqual(j["errors"], 2)
        self.assertEqual({c["error"] for c in j["calls"]}, {"RuntimeError"})
        self.assertNotIn("secret", json.dumps(j))
        gate = evaluate_gate(packet_for(), j)
        self.assertFalse(gate["pass"])
        self.assertIn("jev_api", gate["failed"])
        self.assertEqual(run(self.root, [], jev=FakeJev(fail={"pick_v1", "ok_v1"})), 2)
        self.assertEqual(json.loads(self.judgments.read_text())["marker"], "prior")

    def test_missing_answer_fails_gate(self):
        jev = FakeJev(answers=GOOD, fail={"ok_v1"})
        j = qc_gate.run_jev(packet_for(), SPECS, jev_module=jev)
        self.assertTrue(j["ran"])
        self.assertFalse(j["complete"])
        gate = evaluate_gate(packet_for(), j)
        self.assertFalse(gate["pass"])
        self.assertIn("answer:policy:ok:ok", gate["failed"])

    def test_empty_answers_count_as_missing(self):
        jev = FakeJev(answers={"pick_v1": GOOD["pick_v1"]})
        j = qc_gate.run_jev(packet_for(), SPECS, jev_module=jev)
        self.assertEqual(j["errors"], 1)
        self.assertFalse(evaluate_gate(packet_for(), j)["pass"])

    def test_partial_run_keeps_prior_and_require_jev_exits_2(self):
        self.write_prior()
        rc = run(self.root, ["--require-jev"], jev=FakeJev(answers=GOOD, fail={"ok_v1"}))
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(self.judgments.read_text())["marker"], "prior")

    def test_complete_run_replaces_judgments(self):
        self.write_prior()
        rc = run(self.root, ["--require-jev"], jev=FakeJev(answers=GOOD))
        self.assertEqual(rc, 0)
        data = json.loads(self.judgments.read_text())
        self.assertTrue(data["complete"])
        self.assertNotIn("marker", data)

    def test_keyless_run_keeps_prior(self):
        self.write_prior()
        self.assertEqual(run(self.root, [], jev=FakeJev(key=False)), 0)
        self.assertEqual(json.loads(self.judgments.read_text())["marker"], "prior")
        self.assertEqual(run(self.root, ["--require-jev"], jev=FakeJev(key=False)), 2)

    def test_packet_only_runs_deterministic_checks(self):
        self.assertEqual(run(self.root, ["--packet-only"], det_ok=False), 1)
        self.assertEqual(run(self.root, ["--packet-only"], det_ok=True), 0)

    def test_packet_only_keeps_prior(self):
        self.write_prior()
        run(self.root, ["--packet-only"])
        self.assertEqual(json.loads(self.judgments.read_text())["marker"], "prior")

    def test_bad_choice_fails(self):
        answers = dict(GOOD, pick_v1={"pick": {"type": "choice", "choice": "b"}})
        j = qc_gate.run_jev(packet_for(), SPECS, jev_module=FakeJev(answers=answers))
        gate = evaluate_gate(packet_for(), j)
        self.assertEqual(gate["failed"], ["pick"])


if __name__ == "__main__":
    unittest.main()
