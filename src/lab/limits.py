"""Accurate pipetting ranges per head, with a floor at which the lab trusts the head.

The vendor range is where a head operates at all. The trusted minimum is the volume below
which a lab does not rely on the head's accuracy. The defaults here are conventional
starting points, meant to be replaced from calibration data or raised with ``warn_below``.
"""

from dataclasses import dataclass
from decimal import Decimal

from lab.deck import Channel
from lab.equipment import PipetteModel


@dataclass(frozen=True, slots=True, kw_only=True)
class PipettingLimits:
    """One head's operating range and trusted floor, in microlitres."""

    label: str
    min_volume_ul: Decimal
    max_volume_ul: Decimal
    trusted_minimum_ul: Decimal
    channels: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Pipetting limits need a label.")
        values = (self.min_volume_ul, self.max_volume_ul, self.trusted_minimum_ul)
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
            raise ValueError(f"{self.label}: volume limits must be finite Decimals.")
        if not 0 < self.min_volume_ul <= self.trusted_minimum_ul <= self.max_volume_ul:
            raise ValueError(f"{self.label}: need 0 < min <= trusted <= max.")
        if type(self.channels) is not int or self.channels < 1:
            raise ValueError("A head has at least one channel.")

    def covers(self, volume: Decimal) -> bool:
        return self.min_volume_ul <= volume <= self.max_volume_ul


def _limits(label: str, low: str, high: str, trusted: str, channels: int = 1) -> PipettingLimits:
    return PipettingLimits(
        label=label,
        min_volume_ul=Decimal(low),
        max_volume_ul=Decimal(high),
        trusted_minimum_ul=Decimal(trusted),
        channels=channels,
    )


OPENTRONS_LIMITS: dict[PipetteModel, PipettingLimits] = {
    PipetteModel.P20_SINGLE_GEN2: _limits("P20 single GEN2", "1", "20", "2"),
    PipetteModel.P300_SINGLE_GEN2: _limits("P300 single GEN2", "20", "300", "20"),
    PipetteModel.FLEX_1CHANNEL_1000: _limits("Flex 1-channel 1000", "5", "1000", "50"),
}
"""Vendor ranges for the supported Opentrons heads.

The P20's floor is 2 µL, the volume at which its deviation is routinely accepted, rather than
its 1 µL minimum; the P300 is trusted across its range; the Flex 1000 is trusted from 50 µL.
"""

MANUAL_LIMITS: tuple[PipettingLimits, ...] = (
    _limits("P2", "0.2", "2", "0.5"),
    _limits("P10", "1", "10", "1"),
    _limits("P20", "2", "20", "2"),
    _limits("P200", "20", "200", "20"),
    _limits("P1000", "100", "1000", "100"),
)
"""A conventional set of adjustable manual micropipettes."""


def channel_limits(channel: Channel) -> PipettingLimits:
    """Limits of a configured STAR channel; its declared minimum is also its trusted floor."""
    return PipettingLimits(
        label=f"STAR channel {channel.index}",
        min_volume_ul=channel.min_volume_ul,
        max_volume_ul=channel.max_volume_ul,
        trusted_minimum_ul=channel.min_volume_ul,
    )


def smallest_covering(
    limits: tuple[PipettingLimits, ...], volume: Decimal
) -> PipettingLimits | None:
    """The head with the smallest maximum that can pipette ``volume``, if any."""
    fits = [head for head in limits if head.covers(volume)]
    return min(fits, key=lambda head: head.max_volume_ul, default=None)
