from __future__ import annotations
from typing import TYPE_CHECKING
from ai.pods.base_pod import BasePod
from ai.prompts import REGIME_POD_SYSTEM, REGIME_POD_USER
if TYPE_CHECKING:
    from ai.schemas import SignalPacket

class RegimePod(BasePod):
    POD_NAME = "regime"
    REQUIRED_KEYS = ["decision", "confidence", "reasoning"]
    VALID_DECISIONS = ["favorable", "neutral", "unfavorable"]

    def build_messages(self, signal: "SignalPacket"):
        summary = signal.to_prompt_summary()
        msgs = [
            {"role": "system", "content": REGIME_POD_SYSTEM},
            {"role": "user", "content": REGIME_POD_USER.format(signal_summary=summary)},
        ]
        return msgs, msgs
