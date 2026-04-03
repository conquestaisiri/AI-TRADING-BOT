from __future__ import annotations
from typing import TYPE_CHECKING
from ai.pods.base_pod import BasePod
from ai.prompts import TREND_POD_SYSTEM, TREND_POD_USER
if TYPE_CHECKING:
    from ai.schemas import SignalPacket

class TrendPod(BasePod):
    POD_NAME = "trend"
    REQUIRED_KEYS = ["decision", "confidence", "reasoning"]
    VALID_DECISIONS = ["bullish", "bearish", "neutral"]

    def build_messages(self, signal: "SignalPacket"):
        summary = signal.to_prompt_summary()
        msgs = [
            {"role": "system", "content": TREND_POD_SYSTEM},
            {"role": "user", "content": TREND_POD_USER.format(signal_summary=summary)},
        ]
        return msgs, msgs
