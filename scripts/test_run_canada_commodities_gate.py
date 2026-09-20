#!/usr/bin/env python3
"""Packet builder tests for Canada commodities — no TypeSafe network."""
from __future__ import annotations

import unittest
from pathlib import Path

from run_canada_commodities_gate import (
    QUESTION_SPECS,
    build_packet,
    evaluate_gate,
)


class Packet(unittest.TestCase):
    def test_packet_has_policy_and_typed_questions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        packet = build_packet(root)
        self.assertEqual(packet["schema"], "qc-pr-merge-gate-v1")
        self.assertEqual(packet["work"], "C")
        self.assertEqual(packet["model"], "jev-latest")
        ids = {c["id"] for c in packet["calls"]}
        self.assertIn("policy:mine_tonnes", ids)
        self.assertIn("policy:overview_first", ids)
        self.assertIn("policy:refresh_cadence", ids)
        for spec in QUESTION_SPECS.values():
            self.assertIn(spec["type"], {"Choice", "Noul", "Score"})
        owner_calls = [c for c in packet["calls"] if c["id"].startswith("owner:")]
        self.assertLessEqual(len(owner_calls), 8)

    def test_gate_passes_without_jev(self) -> None:
        root = Path(__file__).resolve().parents[1]
        packet = build_packet(root)
        gate = evaluate_gate(packet, {"ran": False, "reason": "no_typesafe_key_or_sdk", "calls": []})
        self.assertTrue(gate["pass"])
        self.assertFalse(gate["jev_ran"])

    def test_gate_fails_when_jev_invents_tonnes(self) -> None:
        packet = {
            "index": {
                "n_commodities": 2,
                "gold_years": 20,
                "gold_mines": 3,
                "schema": "qc-canada-commodities-v1",
            }
        }
        judgments = {
            "ran": True,
            "model": "jev-latest",
            "calls": [
                {
                    "id": "policy:mine_tonnes",
                    "answers": {"mine_tonnes": {"type": "choice", "choice": "guess_43_101"}},
                }
            ],
        }
        gate = evaluate_gate(packet, judgments)
        self.assertFalse(gate["pass"])
        self.assertIn("mine_tonnes", gate["failed"])


if __name__ == "__main__":
    unittest.main()
