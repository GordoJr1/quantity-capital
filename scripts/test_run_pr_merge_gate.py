#!/usr/bin/env python3
"""Packet builder tests — no TypeSafe network."""
from __future__ import annotations

import unittest
from pathlib import Path

from run_pr_merge_gate import (
    QUESTION_SPECS,
    audit_summary,
    build_packet,
    evaluate_gate,
)


class Packet(unittest.TestCase):
    def test_packet_has_audit_and_typed_questions(self):
        root = Path(__file__).resolve().parents[1]
        packet = build_packet(root)
        self.assertEqual(packet["schema"], "qc-pr-merge-gate-v1")
        self.assertEqual(packet["work"], "A")
        self.assertEqual(packet["model"], "jev-latest")
        self.assertGreaterEqual(packet["audit"]["n"], 191)
        self.assertEqual(packet["audit"]["counts"].get("matched"), packet["audit"]["n"])
        ids = {c["id"] for c in packet["calls"]}
        self.assertIn("policy:empty_badge_copy", ids)
        self.assertIn("issuer:probe-gold", ids)
        self.assertIn("issuer:gold-fields", ids)
        for spec in QUESTION_SPECS.values():
            self.assertIn(spec["type"], {"Choice", "Noul", "Score"})

    def test_gate_passes_without_jev(self):
        root = Path(__file__).resolve().parents[1]
        packet = build_packet(root)
        gate = evaluate_gate(packet, {"ran": False, "reason": "no_typesafe_key_or_sdk", "calls": []})
        self.assertTrue(gate["pass"])
        self.assertFalse(gate["jev_ran"])

    def test_gate_fails_low_noul_when_jev_ran(self):
        packet = {
            "audit": {
                "n": 2,
                "counts": {"matched": 2, "missing-ticker": 0},
                "new_publics": [{"id": "probe-gold"}, {"id": "g2-goldfields"}],
            }
        }
        judgments = {
            "ran": True,
            "model": "jev-latest",
            "calls": [
                {
                    "id": "issuer:probe-gold",
                    "answers": {"on_universe": {"type": "noul", "noul": 0.1}},
                }
            ],
        }
        gate = evaluate_gate(packet, judgments)
        self.assertFalse(gate["pass"])
        self.assertIn("issuer:probe-gold_on_universe", gate["failed"])

    def test_audit_summary_picks_samples(self):
        rows = [
            {"status": "matched", "id": "newmont", "tickers": "NEM"},
            {"status": "matched", "id": "gold-fields", "tickers": "GFI"},
            {"status": "matched", "id": "probe-gold", "tickers": "PRB.TO"},
            {"status": "matched", "id": "g2-goldfields", "tickers": "GTWO.TO"},
        ]
        s = audit_summary(rows)
        self.assertEqual(s["n"], 4)
        self.assertEqual(s["samples"]["filings_normal"]["id"], "newmont")
        self.assertEqual({r["id"] for r in s["new_publics"]}, {"probe-gold", "g2-goldfields"})


if __name__ == "__main__":
    unittest.main()
