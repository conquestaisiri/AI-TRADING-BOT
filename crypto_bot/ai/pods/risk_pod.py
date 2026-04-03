from __future__ import annotations
from typing import TYPE_CHECKING
from ai.pods.base_pod import BasePod
from ai.prompts import RISK_POD_SYSTEM, RISK_POD_USER
if TYPE_CHECKING:
    from ai.schemas import SignalPacket

class RiskPod(BasePod):
    POD_NAME = "risk"
    REQUIRED_KEYS = ["decision", "confidence", "reasoning"]
    VALID_DECISIONS = ["acceptable", "marginal", "unacceptable"]

    def build_messages(self, signal: "SignalPacket"):
        summary = signal.to_prompt_summary()
        msgs = [
            {"role": "system", "content": RISK_POD_SYSTEM},
            {"role": "user", "content": RISK_POD_USER.format(signal_summary=summary)},
        ]
        return msgs, msgs
