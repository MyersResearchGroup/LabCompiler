from dataclasses import dataclass

from lab.model import RecordedProtocol
from lab.target import TargetPlan
from lab.validation import logical_bindings


@dataclass(frozen=True)
class Manual:
    def prepare(self, protocol: RecordedProtocol) -> TargetPlan:
        return TargetPlan("Manual", logical_bindings(protocol), "{}")
