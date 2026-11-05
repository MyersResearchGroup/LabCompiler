"""Advisory findings that compilation records without rejecting a protocol.

Hard limits stay errors: a volume no head can pipette or an overflowing well raises
``CompileError``. Diagnostics describe what compiles but deserves attention, such as a dose
below the floor at which a head's accuracy is trusted.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from lab.limits import PipettingLimits, smallest_covering
from lab.model import Distribute, Mix, Origin, RecordedProtocol, Transfer
from lab.samples import Location
from lab.units import number


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    """One finding; ``step_index`` is zero-based when the finding names a recorded step."""

    severity: Severity
    code: str
    message: str
    locations: tuple[Location, ...] = ()
    step_index: int | None = None
    origin: Origin | None = None


Diagnostics = tuple[Diagnostic, ...]

BELOW_TRUSTED_MINIMUM = "volume-below-trusted-minimum"
VOLUME_RESOLUTION = "volume-resolution"


def advise(
    protocol: RecordedProtocol,
    limits: tuple[PipettingLimits, ...],
    *,
    warn_below: Decimal | None = None,
    resolution: Decimal | None = None,
) -> Diagnostics:
    """Flag liquid steps below the trusted floor of the smallest head that covers them.

    ``warn_below`` raises every head's floor to at least that volume. ``resolution`` adds an
    informational finding for volumes that are not a multiple of the smallest dialable step.
    With no limits at all, every liquid step is compared against ``warn_below`` alone.
    """
    found: list[Diagnostic] = []
    for index, step in enumerate(protocol.steps):
        if not isinstance(step, (Transfer, Distribute, Mix)):
            continue
        if isinstance(step, Transfer):
            where: tuple[Location, ...] = (step.destination,)
        elif isinstance(step, Distribute):
            where = step.destinations
        else:
            where = (step.location,)
        head = smallest_covering(limits, step.volume)
        floor = max(
            head.trusted_minimum_ul if head is not None else Decimal(0),
            warn_below if warn_below is not None else Decimal(0),
        )
        if (head is None and limits) or step.volume < floor:
            label = head.label if head is not None else "any configured pipette"
            detail = (
                f"below the trusted {number(floor)} µL floor of {label}"
                if head is not None
                else f"outside the range of {label}"
            )
            found.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code=BELOW_TRUSTED_MINIMUM,
                    message=f"{number(step.volume)} µL is {detail}; "
                    "consider a serial dilution or a premix.",
                    locations=where,
                    step_index=index,
                    origin=step.origin,
                )
            )
        if resolution is not None and step.volume.remainder_near(resolution) != 0:
            found.append(
                Diagnostic(
                    severity=Severity.INFO,
                    code=VOLUME_RESOLUTION,
                    message=f"{number(step.volume)} µL cannot be dialed at "
                    f"{number(resolution)} µL resolution; the compiler does not round doses.",
                    locations=where,
                    step_index=index,
                    origin=step.origin,
                )
            )
    return tuple(found)
