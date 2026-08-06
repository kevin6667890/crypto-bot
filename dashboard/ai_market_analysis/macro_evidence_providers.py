"""Macro evidence providers. Network retrieval is deliberately not implemented in AI-4."""
from __future__ import annotations
from typing import Protocol


AUTOMATIC_MACRO_RETRIEVAL = "NOT_IMPLEMENTED"


class MacroEvidenceProvider(Protocol):
    def evidence(self, instrument: str, decision_time: str) -> list[dict]: ...


class SuppliedMacroEvidenceProvider:
    def __init__(self, items: list[dict]): self.items = items
    def evidence(self, instrument: str, decision_time: str) -> list[dict]: return list(self.items)


class FixtureMacroEvidenceProvider(SuppliedMacroEvidenceProvider):
    def evidence(self, instrument: str, decision_time: str) -> list[dict]:
        return [{**item, "fixture": True} for item in self.items]
