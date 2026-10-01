"""Resolved method parameters, supplied independently of provenance graphs."""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TypeVar

from lab.artifacts import canonical_json, write_bundle
from lab.operations import Hold
from lab.provenance import Component, Ref
from lab.provenance.types import require_iri


def positive(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be a positive finite Decimal")


@dataclass(frozen=True, kw_only=True)
class Reagent:
    component: Ref[Component]
    volume_ul: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.component, Ref):
            raise TypeError("Reagents need a component reference")
        positive(self.volume_ul, "Reagent volume")


@dataclass(frozen=True, kw_only=True)
class AssemblyMethod:
    """All volumes are microlitres; all thermal holds use seconds and Celsius.

    ``output_volume_ul`` is an explicit planned usable yield, not a measurement.
    The enzyme is an ordinary explicit reagent in this method.
    """

    identity: str
    enzyme: str
    dna_volume_ul: Decimal
    reaction_volume_ul: Decimal
    output_volume_ul: Decimal
    reagents: tuple[Reagent, ...]
    diluent: Ref[Component]
    profile: tuple[Hold, ...]
    cycles: int
    mix_volume_ul: Decimal
    mix_cycles: int
    lid_celsius: Decimal | None = None

    def __post_init__(self) -> None:
        require_iri(self.identity)
        for name in ("dna_volume_ul", "reaction_volume_ul", "output_volume_ul", "mix_volume_ul"):
            positive(getattr(self, name), name)
        if (
            self.output_volume_ul > self.reaction_volume_ul
            or self.mix_volume_ul > self.reaction_volume_ul
        ):
            raise ValueError("Usable output and mixing volumes cannot exceed reaction volume")
        if not isinstance(self.reagents, tuple) or not all(
            isinstance(item, Reagent) for item in self.reagents
        ):
            raise TypeError("Reagents must be a tuple of Reagent objects")
        if not isinstance(self.diluent, Ref):
            raise TypeError("Diluent must be a component reference")
        if (
            not isinstance(self.profile, tuple)
            or not self.profile
            or not all(isinstance(hold, Hold) for hold in self.profile)
        ):
            raise ValueError("Supply an explicit thermal profile")
        for hold in self.profile:
            if (
                not isinstance(hold.celsius, Decimal)
                or not hold.celsius.is_finite()
                or hold.celsius < Decimal("-273.15")
            ):
                raise ValueError("Thermal temperatures must be finite Decimal Celsius values")
            positive(hold.seconds, "Hold duration")
        for value in (self.cycles, self.mix_cycles):
            if type(value) is not int or value < 1:
                raise ValueError("Cycle counts must be positive integers")
        if self.lid_celsius is not None and (
            not isinstance(self.lid_celsius, Decimal)
            or not self.lid_celsius.is_finite()
            or self.lid_celsius < 0
        ):
            raise ValueError("Lid temperature must be nonnegative finite Decimal Celsius")

    def additions(self, components: tuple[Ref[Component], ...]) -> tuple[Reagent, ...]:
        dna = tuple(
            Reagent(component=component, volume_ul=self.dna_volume_ul) for component in components
        )
        water = self.reaction_volume_ul - sum(
            (item.volume_ul for item in (*dna, *self.reagents)), Decimal(0)
        )
        if water < 0:
            raise ValueError("Method additions exceed the reaction volume")
        return (
            *self.reagents,
            *dna,
            *((Reagent(component=self.diluent, volume_ul=water),) if water else ()),
        )


def thermal_profile(profile: tuple[Hold, ...]) -> None:
    if not isinstance(profile, tuple) or not profile:
        raise ValueError("Supply an explicit nonempty thermal profile")
    for hold in profile:
        if not isinstance(hold, Hold):
            raise TypeError("Thermal profiles contain Hold values")
        if (
            not isinstance(hold.celsius, Decimal)
            or not hold.celsius.is_finite()
            or hold.celsius < Decimal("-273.15")
        ):
            raise ValueError("Invalid Celsius temperature")
        positive(hold.seconds, "Hold duration")


@dataclass(frozen=True, kw_only=True)
class TransformationMethod:
    """Explicit cell/DNA additions, treatment, medium addition, and recovery.

    The output is an unverified recovery mixture, never a confirmed clone.
    No temperatures, durations, or biological yields are inferred.
    """

    identity: str
    cell_volume_ul: Decimal
    dna_volume_ul: Decimal
    recovery: Reagent
    profile: tuple[Hold, ...]
    recovery_profile: tuple[Hold, ...]
    output_volume_ul: Decimal
    cell_mix_volume_ul: Decimal
    cell_mix_cycles: int
    dna_mix_cycles: int
    initial_celsius: Decimal | None = None

    def __post_init__(self) -> None:
        require_iri(self.identity)
        for name in ("cell_volume_ul", "dna_volume_ul", "output_volume_ul", "cell_mix_volume_ul"):
            positive(getattr(self, name), name)
        if self.cell_mix_volume_ul > self.cell_volume_ul:
            raise ValueError("Cell mixing volume cannot exceed the allocated cell volume")
        if not isinstance(self.recovery, Reagent):
            raise TypeError("Recovery medium must be a Reagent")
        thermal_profile(self.profile)
        thermal_profile(self.recovery_profile)
        for value in (self.cell_mix_cycles, self.dna_mix_cycles):
            if type(value) is not int or value < 1:
                raise ValueError("Mixing cycles must be positive integers")
        if self.initial_celsius is not None:
            thermal_profile((Hold(self.initial_celsius, Decimal(1)),))

    def reaction_volume(self, plasmids: int) -> Decimal:
        volume = self.cell_volume_ul + self.dna_volume_ul * plasmids + self.recovery.volume_ul
        if self.output_volume_ul > volume:
            raise ValueError("Usable transformation output cannot exceed the added liquid")
        return volume


@dataclass(frozen=True, kw_only=True)
class PlatingMethod:
    """One deposited sample after an explicit dilution series.

    Each planned reaction produces one counted spot at the final dilution.
    Repeated targets produce independent series; no colony yield is predicted.
    The substrate is a supplied plate precondition, like other deck consumables.
    """

    identity: str
    substrate: Ref[Component]
    diluent: Ref[Component]
    transfer_volume_ul: Decimal
    dilution_factors: tuple[Decimal, ...]
    spot_volume_ul: Decimal
    mix_volume_ul: Decimal
    mix_cycles: int
    spot_height_mm: Decimal

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if not isinstance(self.substrate, Ref) or not isinstance(self.diluent, Ref):
            raise TypeError("Plating substrate and diluent must be component references")
        for name in ("transfer_volume_ul", "spot_volume_ul", "mix_volume_ul", "spot_height_mm"):
            positive(getattr(self, name), name)
        if not isinstance(self.dilution_factors, tuple) or not self.dilution_factors:
            raise ValueError("Supply an explicit dilution series")
        for factor in self.dilution_factors:
            positive(factor, "Dilution factor")
            if factor <= 1 or self.mix_volume_ul > self.transfer_volume_ul * factor:
                raise ValueError("Dilutions must increase volume and cover the mixing volume")
        if self.spot_volume_ul > self.transfer_volume_ul * self.dilution_factors[-1]:
            raise ValueError("The last dilution cannot supply the specified spot volume")
        if type(self.mix_cycles) is not int or self.mix_cycles < 1:
            raise ValueError("Mixing cycles must be positive integers")
        if (
            not isinstance(self.spot_height_mm, Decimal)
            or not self.spot_height_mm.is_finite()
            or self.spot_height_mm < 0
        ):
            raise ValueError("Specify a nonnegative spotting height above the well bottom")


@dataclass(frozen=True, kw_only=True)
class ExternalPreparationMethod:
    """A caller-specified external procedure and prospective material balance.

    Exactly one input and output quantity kind is required. Other consumed
    liquids can be named as reagents. The procedure must specify handling,
    waste, and losses; a declared yield is not a measurement or a robot action.
    """

    identity: str
    procedure: str
    instructions: str
    source_volume_ul: Decimal = Decimal(0)
    source_count: int = 0
    output_volume_ul: Decimal = Decimal(0)
    output_count: int = 0
    reagents: tuple[Reagent, ...] = ()

    def __post_init__(self) -> None:
        require_iri(self.identity)
        require_iri(self.procedure)
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError("External preparation needs explicit instructions")
        for volume, count in (
            (self.source_volume_ul, self.source_count),
            (self.output_volume_ul, self.output_count),
        ):
            if (
                not isinstance(volume, Decimal)
                or not volume.is_finite()
                or volume < 0
                or type(count) is not int
                or count < 0
                or (volume > 0) == (count > 0)
            ):
                raise ValueError("Specify either a positive volume or a positive count")
        if not isinstance(self.reagents, tuple) or not all(
            isinstance(r, Reagent) for r in self.reagents
        ):
            raise TypeError("Preparation reagents must be an immutable tuple")


Method = AssemblyMethod | TransformationMethod | PlatingMethod | ExternalPreparationMethod
M = TypeVar("M", bound=Method)


@dataclass(frozen=True, kw_only=True)
class CloningMethods:
    assemblies: tuple[AssemblyMethod, ...] = ()
    transformations: tuple[TransformationMethod, ...] = ()
    platings: tuple[PlatingMethod, ...] = ()
    preparations: tuple[ExternalPreparationMethod, ...] = ()

    def __post_init__(self) -> None:
        for name, cls in (
            ("assemblies", AssemblyMethod),
            ("transformations", TransformationMethod),
            ("platings", PlatingMethod),
            ("preparations", ExternalPreparationMethod),
        ):
            items = getattr(self, name)
            if not isinstance(items, tuple) or not all(isinstance(item, cls) for item in items):
                raise TypeError(f"{name} must contain an immutable tuple of {cls.__name__}")
            object.__setattr__(self, name, tuple(sorted(items, key=lambda item: item.identity)))
        if len({method.identity for method in self.all}) != len(self.all):
            raise ValueError("Method identities must be unique")

    @property
    def all(self) -> tuple[Method, ...]:
        return (*self.assemblies, *self.transformations, *self.platings, *self.preparations)

    def get(self, identity: str, kind: type[M]) -> M:
        for method in self.all:
            if method.identity == identity:
                if not isinstance(method, kind):
                    raise TypeError(f"Method {identity} must be a {kind.__name__}")
                return method
        raise KeyError(identity)

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        write_bundle(
            path.parent,
            {
                path.name: canonical_json(
                    {
                        "format": "lab.cloning-methods.v1",
                        "methods": self,
                    }
                )
            },
        )
        return path

    @classmethod
    def read(cls, path: str | Path) -> "CloningMethods":
        data = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)
        if data.get("format") != "lab.cloning-methods.v1":
            raise ValueError("Expected lab.cloning-methods.v1")
        methods = []
        for row in data["methods"]["assemblies"]:
            decimal_fields = (
                "dna_volume_ul",
                "reaction_volume_ul",
                "output_volume_ul",
                "mix_volume_ul",
            )
            methods.append(
                AssemblyMethod(
                    **{
                        **row,
                        **{name: Decimal(row[name]) for name in decimal_fields},
                        "lid_celsius": None
                        if row["lid_celsius"] is None
                        else Decimal(row["lid_celsius"]),
                        "reagents": tuple(
                            Reagent(
                                component=Ref(reagent["component"]["identity"]),
                                volume_ul=Decimal(reagent["volume_ul"]),
                            )
                            for reagent in row["reagents"]
                        ),
                        "diluent": Ref(row["diluent"]["identity"]),
                        "profile": tuple(
                            Hold(Decimal(hold["celsius"]), Decimal(hold["seconds"]))
                            for hold in row["profile"]
                        ),
                    }
                )
            )

        def reagent(row: dict) -> Reagent:
            return Reagent(
                component=Ref(row["component"]["identity"]), volume_ul=Decimal(row["volume_ul"])
            )

        def profile(rows: list) -> tuple[Hold, ...]:
            return tuple(Hold(Decimal(row["celsius"]), Decimal(row["seconds"])) for row in rows)

        return cls(
            assemblies=tuple(methods),
            transformations=tuple(
                TransformationMethod(
                    **{
                        **row,
                        **{
                            name: Decimal(row[name])
                            for name in (
                                "cell_volume_ul",
                                "dna_volume_ul",
                                "output_volume_ul",
                                "cell_mix_volume_ul",
                            )
                        },
                        "initial_celsius": None
                        if row["initial_celsius"] is None
                        else Decimal(row["initial_celsius"]),
                        "recovery": reagent(row["recovery"]),
                        "profile": profile(row["profile"]),
                        "recovery_profile": profile(row["recovery_profile"]),
                    }
                )
                for row in data["methods"]["transformations"]
            ),
            platings=tuple(
                PlatingMethod(
                    **{
                        **row,
                        "substrate": Ref(row["substrate"]["identity"]),
                        "diluent": Ref(row["diluent"]["identity"]),
                        **{
                            name: Decimal(row[name])
                            for name in (
                                "transfer_volume_ul",
                                "spot_volume_ul",
                                "mix_volume_ul",
                                "spot_height_mm",
                            )
                        },
                        "dilution_factors": tuple(
                            Decimal(factor) for factor in row["dilution_factors"]
                        ),
                    }
                )
                for row in data["methods"]["platings"]
            ),
            preparations=tuple(
                ExternalPreparationMethod(
                    **{
                        **row,
                        "source_volume_ul": Decimal(row["source_volume_ul"]),
                        "output_volume_ul": Decimal(row["output_volume_ul"]),
                        "reagents": tuple(reagent(r) for r in row["reagents"]),
                    }
                )
                for row in data["methods"]["preparations"]
            ),
        )
