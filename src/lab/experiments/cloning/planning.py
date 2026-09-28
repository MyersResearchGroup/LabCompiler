"""Pure inventory allocation and dependency-aware assembly route planning."""

import hashlib
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from lab._version import __version__
from lab.artifacts import canonical_json, digest, write_bundle
from lab.experiments.cloning.domestication import EditProposal, propose_edits
from lab.experiments.cloning.methods import (
    CloningMethods,
    ExternalPreparationMethod,
    Method,
    PlatingMethod,
    positive,
)
from lab.experiments.cloning.routes import resolve_recipe
from lab.experiments.cloning.sequences import calculate_assembly
from lab.experiments.cloning.systems import (
    AssemblyRecipe,
    CloningSystem,
    Recipe,
)
from lab.inventory import Inventory, MaterialForm
from lab.provenance import (
    Activity,
    Agent,
    AgentKind,
    Association,
    Component,
    Document,
    DocumentSnapshot,
    EvidenceState,
    Implementation,
    Plan,
    Ref,
    Usage,
)
from lab.provenance.types import require_iri
from lab.provenance.vocabulary import LAB
from lab.suppliers.types import AcquisitionRequest, Catalog, Receipt


class TaskKind(StrEnum):
    ASSEMBLY = "assembly"
    TRANSFORMATION = "transformation"
    PLATING = "plating"
    ACQUISITION = "acquisition"
    PREPARATION = "preparation"


class RequirementKind(StrEnum):
    ACQUISITION = "acquisition"
    PREPARATION = "preparation"
    DESIGN = "design"


@dataclass(frozen=True, kw_only=True)
class BuildTarget:
    design: Ref[Component]
    volume_ul: Decimal
    form: MaterialForm = MaterialForm.DNA

    def __post_init__(self) -> None:
        if not isinstance(self.design, Ref) or not isinstance(self.form, MaterialForm):
            raise TypeError("Targets need a design reference and a MaterialForm")
        positive(self.volume_ul, "Target volume")
        if self.form.counted:
            raise ValueError("Use CountTarget for counted material forms")

    @property
    def amount(self) -> Decimal:
        return self.volume_ul


@dataclass(frozen=True, kw_only=True)
class CountTarget:
    design: Ref[Component]
    count: int
    form: MaterialForm = MaterialForm.PLATED_SAMPLE

    def __post_init__(self) -> None:
        if (
            not isinstance(self.design, Ref)
            or not isinstance(self.form, MaterialForm)
            or not self.form.counted
        ):
            raise TypeError("Count targets need a design reference and a counted material form")
        if type(self.count) is not int or self.count < 1:
            raise ValueError("Target count must be a positive integer")

    @property
    def amount(self) -> Decimal:
        return Decimal(self.count)


@dataclass(frozen=True, kw_only=True)
class BuildRequest:
    identity: str
    targets: tuple[BuildTarget | CountTarget, ...]

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if (
            not isinstance(self.targets, tuple)
            or not self.targets
            or not all(isinstance(target, (BuildTarget, CountTarget)) for target in self.targets)
        ):
            raise ValueError("A build request needs a nonempty tuple of targets")
        if len({(target.design, target.form) for target in self.targets}) != len(self.targets):
            raise ValueError("Combine quantities for duplicate targets")


@dataclass(frozen=True, kw_only=True)
class EditablePositions:
    component: Ref[Component]
    positions: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.component, Ref):
            raise TypeError("Editable component must be a reference")
        if (
            not isinstance(self.positions, tuple)
            or any(type(position) is not int or position < 0 for position in self.positions)
            or len(set(self.positions)) != len(self.positions)
        ):
            raise ValueError("Editable positions must be a tuple of unique nonnegative integers")


@dataclass(frozen=True, kw_only=True)
class PlanningPolicy:
    """Reuse available stock, then minimize missing work, depth, and reactions.

    Route combinations are explored across the entire request. Exceeding the
    explicit search limit is an error, never an undisclosed greedy fallback.
    """

    max_states: int = 4096
    editable_positions: tuple[EditablePositions, ...] = ()

    def __post_init__(self) -> None:
        if type(self.max_states) is not int or self.max_states < 1:
            raise ValueError("max_states must be positive")
        if not isinstance(self.editable_positions, tuple) or not all(
            isinstance(item, EditablePositions) for item in self.editable_positions
        ):
            raise TypeError("Editable positions must be an immutable tuple")
        if len({item.component for item in self.editable_positions}) != len(
            self.editable_positions
        ):
            raise ValueError("Specify editable positions once per component")


@dataclass(frozen=True, kw_only=True)
class Allocation:
    consumer: str
    design: Ref[Component]
    implementation: Ref[Implementation]
    volume_ul: Decimal
    stock: str | None
    producer: str | None
    form: MaterialForm = MaterialForm.DNA
    count: int = 0
    role: str = "material"

    @property
    def amount(self) -> Decimal:
        return Decimal(self.count) if self.form.counted else self.volume_ul


@dataclass(frozen=True, kw_only=True)
class Requirement:
    kind: RequirementKind
    design: Ref[Component]
    form: MaterialForm
    volume_ul: Decimal
    message: str
    task: str | None = None
    count: int = 0


@dataclass(frozen=True, kw_only=True)
class BuildTask:
    identity: str
    kind: TaskKind
    requested_design: Ref[Component]
    design: Ref[Component]
    output: Ref[Implementation]
    output_volume_ul: Decimal
    depends_on: tuple[str, ...] = ()
    inputs: tuple[Allocation, ...] = ()
    recipe: Recipe | None = None
    method: Method | None = None
    output_form: MaterialForm = MaterialForm.DNA
    output_count: int = 0


@dataclass(frozen=True, kw_only=True)
class BuildPlan:
    request: BuildRequest
    document: DocumentSnapshot
    inventory: Inventory
    system: CloningSystem
    methods: CloningMethods
    policy: PlanningPolicy
    tasks: tuple[BuildTask, ...]
    allocations: tuple[Allocation, ...]
    products: tuple[Allocation, ...]
    requirements: tuple[Requirement, ...]
    catalog: Catalog = Catalog()
    receipts: tuple[Receipt, ...] = ()
    acquisitions: tuple[AcquisitionRequest, ...] = ()
    edit_proposals: tuple[EditProposal, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.requirements

    def require_ready(self) -> None:
        if not self.ready:
            raise ValueError(
                "Build is not ready:\n" + "\n".join(item.message for item in self.requirements)
            )

    @property
    def plan_json(self) -> str:
        return canonical_json(
            {
                "format": "lab.build.v1",
                "request": self.request,
                "provenance_sha256": self.document.digest,
                "inventory_sha256": self.inventory.digest,
                "system": self.system,
                "methods": self.methods,
                "policy": self.policy,
                "tasks": self.tasks,
                "allocations": self.allocations,
                "products": self.products,
                "requirements": self.requirements,
                "catalog_sha256": self.catalog.digest,
                "receipts": self.receipts,
                "acquisitions": self.acquisitions,
                "edit_proposals": self.edit_proposals,
            }
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.plan_json.encode()).hexdigest()

    def write(self, directory: str | Path) -> Path:
        return write_bundle(
            directory,
            {
                "build.json": self.plan_json,
                "provenance.ttl": self.document.to_turtle(),
                "inventory.json": canonical_json(
                    {"format": "lab.inventory.v1", "inventory": self.inventory}
                ),
                "catalog.json": canonical_json(
                    {"format": "lab.catalog.v1", "catalog": self.catalog}
                ),
            },
        )


@dataclass(frozen=True)
class _Supply:
    requested: Ref[Component]
    design: Ref[Component]
    implementation: Ref[Implementation]
    form: MaterialForm
    remaining: Decimal
    stock: str | None = None
    producer: str | None = None


@dataclass
class _State:
    document: DocumentSnapshot
    supplies: dict[str, _Supply]
    tasks: tuple[BuildTask, ...] = ()
    allocations: tuple[Allocation, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    proposals: tuple[EditProposal, ...] = ()
    resolved: dict[str, Ref[Component]] = field(default_factory=dict)

    def copy(self) -> "_State":
        return replace(self, supplies=dict(self.supplies), resolved=dict(self.resolved))


def _take(
    state: _State,
    consumer: str,
    design: Ref[Component],
    form: MaterialForm,
    amount: Decimal,
    role: str = "material",
) -> Decimal:
    for key, supply in sorted(
        state.supplies.items(), key=lambda item: (item[1].stock is None, item[0])
    ):
        if supply.requested != design or supply.form != form or not supply.remaining:
            continue
        taken = min(amount, supply.remaining)
        allocation = Allocation(
            consumer=consumer,
            design=supply.design,
            implementation=supply.implementation,
            volume_ul=Decimal(0) if form.counted else taken,
            count=int(taken) if form.counted else 0,
            form=form,
            role=role,
            stock=supply.stock,
            producer=supply.producer,
        )
        state.allocations += (allocation,)
        state.supplies[key] = replace(supply, remaining=supply.remaining - taken)
        amount -= taken
        if not amount:
            break
    return amount


def _score(state: _State) -> tuple[object, ...]:
    depths: dict[str, int] = {}
    for task in state.tasks:
        depths[task.identity] = max((depths[parent] for parent in task.depends_on), default=0) + (
            task.recipe is not None
        )
    return (
        sum(item.kind is RequirementKind.DESIGN for item in state.requirements),
        sum(item.kind is RequirementKind.ACQUISITION for item in state.requirements),
        sum(item.kind is RequirementKind.PREPARATION for item in state.requirements),
        max(depths.values(), default=0),
        sum(task.recipe is not None for task in state.tasks),
        tuple(
            task.recipe.identity if task.recipe else task.design.identity for task in state.tasks
        ),
    )


def plan(
    request: BuildRequest,
    *,
    document: DocumentSnapshot | Document,
    inventory: Inventory,
    system: CloningSystem,
    methods: CloningMethods,
    policy: PlanningPolicy | None = None,
    catalog: Catalog | None = None,
    receipts: tuple[Receipt, ...] = (),
) -> BuildPlan:
    """Allocate a whole request and choose a deterministic feasible route.

    Planning never edits input sequences, depletes inventory, orders materials,
    or operates a device. A plan with outstanding requirements is inspectable
    but cannot be built into executable protocols.
    """
    policy = policy or PlanningPolicy()
    catalog = catalog or Catalog()
    snapshot = document.freeze() if isinstance(document, Document) else document
    snapshot.validate().raise_for_errors()
    inventory.validate(snapshot)
    for entry in catalog.entries:
        snapshot.resolve(entry.design)
    if not isinstance(receipts, tuple) or not all(isinstance(item, Receipt) for item in receipts):
        raise TypeError("Receipts must be an immutable tuple")
    for receipt in receipts:
        material = snapshot.resolve(receipt.implementation)
        if (
            material.evidence_state is not EvidenceState.RECORDED
            or receipt.design not in material.derived_from
        ):
            raise ValueError("Record received material in provenance before planning with it")
    for target in request.targets:
        snapshot.get(target.design.identity, Component)
    for recipe in system.recipes:
        snapshot.get(recipe.product.identity, Component)
        resolved_recipe = resolve_recipe(recipe, methods)
        for demand in resolved_recipe.inputs:
            snapshot.get(demand.design.identity, Component)
        if isinstance(resolved_recipe.method, PlatingMethod):
            snapshot.resolve(resolved_recipe.method.substrate)
    initial = _State(
        snapshot,
        {
            stock.identity: _Supply(
                requested=stock.design,
                design=stock.design,
                implementation=stock.implementation,
                form=stock.form,
                remaining=stock.amount,
                stock=stock.identity,
            )
            for stock in inventory.stocks
        },
    )
    explored = 0

    def satisfy(
        state: _State,
        consumer: str,
        design: Ref[Component],
        form: MaterialForm,
        amount: Decimal,
        stack: tuple[tuple[str, MaterialForm], ...],
        role: str = "material",
    ) -> list[_State]:
        nonlocal explored
        explored += 1
        if explored > policy.max_states:
            raise ValueError(
                f"Planning exceeded max_states={policy.max_states}; "
                "narrow the route set or raise the limit"
            )
        state = state.copy()
        missing = _take(state, consumer, design, form, amount, role)
        if not missing:
            return [state]
        if (design.identity, form) in stack:
            state.requirements += (
                Requirement(
                    kind=RequirementKind.DESIGN,
                    design=design,
                    form=form,
                    volume_ul=Decimal(0) if form.counted else missing,
                    count=int(missing) if form.counted else 0,
                    message=f"Cyclic build dependency for {design.identity}",
                ),
            )
            return [state]
        routes = system.routes(design, form)
        if not routes:
            candidates = [
                stock
                for stock in inventory.stocks
                if stock.design == design and stock.form != form and stock.amount > 0
            ]
            received = tuple(receipt for receipt in receipts if receipt.design == design)
            kind = TaskKind.PREPARATION if candidates or received else TaskKind.ACQUISITION
            task_id = request.identity + f"/task_{len(state.tasks) + 1}"
            implementation = Implementation(
                identity=task_id + "/output",
                derived_from=(design,),
                evidence_state=EvidenceState.PLANNED,
            )
            doc = Document.from_snapshot(state.document)
            doc.add(implementation)
            state.document = doc.freeze()
            state.tasks += (
                BuildTask(
                    identity=task_id,
                    kind=kind,
                    requested_design=design,
                    design=design,
                    output=implementation.ref,
                    output_volume_ul=Decimal(0) if form.counted else missing,
                    output_count=int(missing) if form.counted else 0,
                    output_form=form,
                ),
            )
            reason = (
                (
                    f"Prepare {design.identity} as {form.value}; available forms: "
                    + ", ".join(
                        sorted(
                            {stock.form.value for stock in candidates}
                            | {receipt.form.value for receipt in received}
                        )
                    )
                )
                if candidates or received
                else (
                    f"Acquire {missing} {'unit(s)' if form.counted else 'µL'} "
                    f"of {design.identity} as {form.value}"
                )
            )
            state.requirements += (
                Requirement(
                    kind=RequirementKind(kind.value),
                    design=design,
                    form=form,
                    volume_ul=Decimal(0) if form.counted else missing,
                    count=int(missing) if form.counted else 0,
                    message=reason,
                    task=task_id,
                ),
            )
            state.supplies[implementation.identity] = _Supply(
                design, design, implementation.ref, form, missing, producer=task_id
            )
            _take(state, consumer, design, form, missing, role)
            return [state]
        outcomes: list[_State] = []
        for recipe in routes:
            branch = state.copy()
            resolved_recipe = resolve_recipe(recipe, methods)
            method = resolved_recipe.method
            task_id = (
                request.identity
                + "/"
                + recipe.kind
                + "_"
                + str(len(branch.tasks) + 1)
                + "_"
                + digest((recipe.identity, consumer))[:12]
            )
            states = [branch]
            for demand in resolved_recipe.inputs:
                states = [
                    next_state
                    for current in states
                    for next_state in satisfy(
                        current,
                        task_id,
                        demand.design,
                        demand.form,
                        demand.amount,
                        (*stack, (design.identity, form)),
                        demand.role,
                    )
                ]
            for current in states:
                try:
                    calculated = (
                        calculate_assembly(
                            recipe, document=current.document, resolved=current.resolved
                        )
                        if isinstance(recipe, AssemblyRecipe)
                        else None
                    )
                except (ValueError, KeyError) as error:
                    current.requirements += (
                        Requirement(
                            kind=RequirementKind.DESIGN,
                            design=design,
                            form=form,
                            volume_ul=Decimal(0) if form.counted else missing,
                            count=int(missing) if form.counted else 0,
                            message=str(error),
                        ),
                    )
                    for editable in policy.editable_positions:
                        if isinstance(recipe, AssemblyRecipe) and editable.component in tuple(
                            fragment.component for fragment in recipe.fragments
                        ):
                            current.proposals += propose_edits(
                                editable.component,
                                document=current.document,
                                enzyme=recipe.enzyme,
                                editable_positions=editable.positions,
                            )
                    outcomes.append(current)
                    continue
                inputs = tuple(item for item in current.allocations if item.consumer == task_id)
                parents = tuple(
                    sorted({item.producer for item in inputs if item.producer is not None})
                )
                product = calculated.product.ref if calculated else design
                planner = Agent(
                    identity=request.identity + "/planner",
                    kind=AgentKind.SOFTWARE,
                    name="Lab planner",
                    software_version=__version__,
                )
                method_plan = Plan(
                    identity=method.identity,
                    protocol=method.procedure
                    if isinstance(method, ExternalPreparationMethod)
                    else None,
                    description=method.instructions
                    if isinstance(method, ExternalPreparationMethod)
                    else None,
                )
                activity = Activity(
                    identity=task_id,
                    types=(LAB + recipe.kind,),
                    evidence_state=EvidenceState.PLANNED,
                    usage=tuple(
                        Usage(entity=ref)
                        for ref in dict.fromkeys(item.implementation for item in inputs)
                    )
                    + (
                        (Usage(entity=method.substrate, roles=(LAB + "substrate",)),)
                        if isinstance(method, PlatingMethod)
                        else ()
                    ),
                    informed_by=tuple(
                        Ref(parent)
                        for parent in parents
                        if any(
                            task.identity == parent and task.recipe is not None
                            for task in current.tasks
                        )
                    ),
                    association=(
                        Association(
                            agent=planner.ref,
                            plan=method_plan.ref,
                            roles=(LAB + "planner",),
                        ),
                    ),
                )
                implementation = Implementation(
                    identity=task_id + "/output",
                    derived_from=(product,),
                    generated_by=(activity.ref,),
                    evidence_state=EvidenceState.PLANNED,
                )
                doc = Document.from_snapshot(current.document)
                doc.add(
                    *(calculated.objects if calculated else ()),
                    planner,
                    method_plan,
                    activity,
                    implementation,
                )
                current.document = doc.freeze()
                current.tasks += (
                    BuildTask(
                        identity=task_id,
                        kind=TaskKind(recipe.kind),
                        requested_design=design,
                        design=product,
                        output=implementation.ref,
                        output_volume_ul=Decimal(0)
                        if form.counted
                        else resolved_recipe.output_amount,
                        output_count=int(resolved_recipe.output_amount) if form.counted else 0,
                        output_form=form,
                        depends_on=parents,
                        inputs=inputs,
                        recipe=recipe,
                        method=method,
                    ),
                )
                if calculated is not None:
                    current.resolved[design.identity] = product
                current.supplies[implementation.identity] = _Supply(
                    design,
                    product,
                    implementation.ref,
                    form,
                    resolved_recipe.output_amount,
                    producer=task_id,
                )
                remainder = _take(current, consumer, design, form, missing, role)
                if remainder:
                    outcomes.extend(
                        satisfy(current, consumer, design, form, remainder, stack, role)
                    )
                else:
                    outcomes.append(current)
        return outcomes

    states = [initial]
    for index, target in enumerate(request.targets):
        consumer = request.identity + f"/target_{index + 1}"
        states = [
            next_state
            for state in states
            for next_state in satisfy(
                state, consumer, target.design, target.form, target.amount, ()
            )
        ]
    winner = min(states, key=_score)
    products = tuple(
        item
        for item in winner.allocations
        if item.consumer.startswith(request.identity + "/target_")
    )
    return BuildPlan(
        request=request,
        document=winner.document,
        inventory=inventory,
        system=system,
        methods=methods,
        policy=policy,
        tasks=winner.tasks,
        allocations=winner.allocations,
        products=products,
        requirements=winner.requirements,
        catalog=catalog,
        receipts=receipts,
        acquisitions=tuple(
            AcquisitionRequest(
                identity=request.identity + f"/acquisition_{index + 1}",
                design=item.design,
                required_form=item.form,
                volume_ul=item.volume_ul,
                count=item.count,
                candidates=catalog.candidates(item.design),
            )
            for index, item in enumerate(winner.requirements)
            if item.kind is RequirementKind.ACQUISITION
        ),
        edit_proposals=tuple(dict.fromkeys(winner.proposals)),
    )
