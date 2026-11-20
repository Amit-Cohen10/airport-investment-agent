"""Curated airport context (constraints and background) loaded from `knowledge/airport_notes.yaml`.

Kept separate from computed data on purpose: the agent must label these as curated
context with a citation, never as a measured statistic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class AirportNotes:
    def __init__(self, path: Path):
        self._notes: dict[str, list[dict[str, Any]]] = yaml.safe_load(path.read_text()) or {}

    def for_airport(self, code: str) -> list[dict[str, Any]]:
        return self._notes.get(code.upper(), [])

    def structured(self, code: str, key: str) -> Any:
        """First structured field (e.g. annual_passenger_cap) present in the airport's notes."""
        return next((n[key] for n in self.for_airport(code) if key in n), None)

    def constraint_flags(self, code: str) -> list[str]:
        return [n["topic"] for n in self.for_airport(code) if n.get("kind") == "constraint"]
