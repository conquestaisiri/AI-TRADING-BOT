from __future__ import annotations
from typing import TYPE_CHECKING
from ai.pods.base_pod import BasePod
from ai.prompts import EXECUTION_POD_SYSTEM, EXECUTION_POD_USER
if TYPE_CHECKING:
    from ai.schemas import SignalPacket

class ExecutionPod(BasePod):
    POD_NAME = "execution"
    REQUIRED_KEYS = ["decision", "confidence", "reasoning"]
    VALID_DECISIONS = ["safe", "risky", "unsafe"]

    def build_messages(self, signal: "SignalPacket"):
        summary = signal.to_prompt_summary()
        msgs = [
            {"role": "system", "content": EXECUTION_POD_SYSTEM},
            {"role": "user", "content": EXECUTION_POD_USER.format(signal_summary=summary)},
        ]
        return msgs, msgs
