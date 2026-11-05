"""The manual target: logical bindings, a printable worksheet, and bench pipettes."""

from dataclasses import dataclass

from lab.limits import MANUAL_LIMITS, PipettingLimits
from lab.model import RecordedProtocol, TargetPlan
from lab.validation import logical_bindings


@dataclass(frozen=True)
class Manual:
    """Compile for a person at the bench with the given adjustable pipettes."""

    pipettes: tuple[PipettingLimits, ...] = MANUAL_LIMITS

    def prepare(self, protocol: RecordedProtocol) -> TargetPlan:
        return TargetPlan("Manual", logical_bindings(protocol), "{}", limits=self.pipettes)
