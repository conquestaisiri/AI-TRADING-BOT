from __future__ import annotations
from typing import TYPE_CHECKING
from ai.pods.base_pod import BasePod
from ai.prompts import STRUCTURE_POD_SYSTEM, STRUCTURE_POD_USER
if TYPE_CHECKING:
    from ai.schemas import SignalPacket

class StructurePod(BasePod):
    POD_NAME = "structure"
    REQUIRED_KEYS = ["decision", "confidence", "reasoning"]
    VALID_DECISIONS = ["valid_breakout", "weak_breakout", "failed_breakout"]

    def build_messages(self, signal: "SignalPacket"):
        summary = signal.to_prompt_summary()
        msgs = [
            {"role": "system", "content": STRUCTURE_POD_SYSTEM},
            {"role": "user", "content": STRUCTURE_POD_USER.format(signal_summary=summary)},
        ]
        return msgs, msgs
