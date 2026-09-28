"""Static LabOP/UML projection. No execution engine or robot SDK is imported."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote

from rdflib import RDF, XSD, Graph, Literal, URIRef
from rdflib.compare import to_canonical_graph

from lab.experiment import ExperimentPlan, ProtocolStage
from lab.labop.primitives import (
    DESCRIPTIONS,
    EXT,
    EXTENSIONS,
    LAB,
    LABOP,
    LIQUID,
    OM,
    SBOL,
    UML,
    upstream_liquid_primitives,
)
from lab.model import semantic
from lab.operations import (
    Distribute,
    ExternalPreparation,
    ManualInstruction,
    Mix,
    SetTemperature,
    Thermocycle,
    Transfer,
    Wait,
)
from lab.samples import Location


def turtle(graph: Graph) -> str:
    """Sorted N-Triples is also valid Turtle and gives deterministic artifacts."""
    return (
        "\n".join(
            sorted(
                line
                for line in to_canonical_graph(graph).serialize(format="nt").splitlines()
                if line
            )
        )
        + "\n"
    )


@dataclass(frozen=True)
class LabOPDocument:
    protocol: str
    text: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def graph(self) -> Graph:
        return Graph().parse(data=self.text, format="turtle")


class _Writer:
    def __init__(self) -> None:
        self.graph = Graph()
        self.upstream = upstream_liquid_primitives()

    def node(
        self, identity: str, kind: URIRef, *, name: str | None = None, top: bool = False
    ) -> URIRef:
        node = URIRef(identity)
        self.graph.add((node, RDF.type, kind))
        self.graph.add((node, RDF.type, SBOL.TopLevel if top else SBOL.Identified))
        self.graph.add((node, SBOL.displayId, Literal(identity.rsplit("/", 1)[-1])))
        if top:
            self.graph.add((node, SBOL.hasNamespace, URIRef(identity.rsplit("/", 1)[0])))
        if name is not None:
            self.graph.add((node, SBOL.name, Literal(name)))
        return node

    def add(self, node: URIRef, predicate: URIRef, value: URIRef | Literal) -> None:
        self.graph.add((node, predicate, value))

    def one(self, node: URIRef, predicate: URIRef) -> URIRef:
        value = self.graph.value(node, predicate, any=False)
        if not isinstance(value, URIRef):
            raise ValueError(f"Expected one URI value for {node} {predicate}")
        return value

    def literal(self, identity: str, value: int | str) -> URIRef:
        node = self.node(
            identity, UML.LiteralInteger if isinstance(value, int) else UML.LiteralString
        )
        self.add(
            node, UML.integerValue if isinstance(value, int) else UML.stringValue, Literal(value)
        )
        return node

    def measure(self, identity: str, value: Decimal | int, unit: URIRef) -> URIRef:
        node = self.node(identity, OM.Measure)
        self.add(node, OM.hasNumericalValue, Literal(str(value), datatype=XSD.decimal))
        self.add(node, OM.hasUnit, unit)
        return node

    def parameter(
        self,
        behavior: URIRef,
        name: str,
        kind: URIRef,
        index: int,
        *,
        direction: str = "in",
        required: bool = True,
    ) -> URIRef:
        ordered = self.node(f"{behavior}/parameter_{index}", UML.OrderedPropertyValue)
        parameter = self.node(f"{ordered}/parameter", UML.Parameter, name=name)
        self.add(behavior, UML.ownedParameter, ordered)
        self.add(ordered, UML.indexValue, Literal(index))
        self.add(ordered, UML.propertyValue, parameter)
        self.add(parameter, UML.direction, UML[direction])
        self.add(parameter, UML.type, kind)
        self.add(parameter, UML.isOrdered, Literal(True))
        self.add(parameter, UML.isUnique, Literal(True))
        for field, count in ((UML.lowerValue, int(required)), (UML.upperValue, 1)):
            self.add(
                parameter, field, self.literal(f"{parameter}/{str(field).split('#')[-1]}", count)
            )
        return ordered

    def primitive(self, name: str, *, upstream: bool = False) -> URIRef:
        base = (
            "https://bioprotocols.org/labop/primitives/sample_arrays/"
            if name == "PlateCoordinates"
            else LIQUID
        )
        identity = URIRef((base if upstream else EXT) + name)
        if (identity, RDF.type, LABOP.Primitive) in self.graph:
            return identity
        if upstream:
            # Include this primitive and all of its owned definitions, unchanged.
            for subject, predicate, obj in self.upstream:
                if str(subject) == str(identity) or str(subject).startswith(str(identity) + "/"):
                    self.graph.add((subject, predicate, obj))
        else:
            self.node(str(identity), LABOP.Primitive, name=name, top=True)
            self.add(identity, SBOL.description, Literal(DESCRIPTIONS[name]))
            for index, parameter in enumerate(EXTENSIONS[name]):
                self.parameter(
                    identity, parameter.name, parameter.type, index, required=parameter.required
                )
        return identity

    def flow(self, protocol: URIRef, source: URIRef, target: URIRef, *, control: bool) -> None:
        index = len(tuple(self.graph.objects(protocol, UML.edge)))
        edge = self.node(f"{protocol}/edge_{index}", UML.ControlFlow if control else UML.ObjectFlow)
        self.add(protocol, UML.edge, edge)
        self.add(edge, UML.source, source)
        self.add(edge, UML.target, target)

    def pin(
        self,
        action: URIRef,
        name: str,
        value: URIRef | None = None,
        *,
        owned: bool = False,
        output: bool = False,
    ) -> URIRef:
        identity = f"{action}/{'output' if output else 'input'}_{name}"
        pin = self.node(
            identity,
            UML.OutputPin if output else UML.ValuePin if value is not None else UML.InputPin,
            name=name,
        )
        self.add(action, UML.output if output else UML.input, pin)
        self.add(pin, UML.isOrdered, Literal(True))
        self.add(pin, UML.isUnique, Literal(True))
        if value is not None:
            literal = self.node(
                f"{pin}/value", UML.LiteralIdentified if owned else UML.LiteralReference
            )
            self.add(literal, UML.identifiedValue if owned else UML.referenceValue, value)
            self.add(pin, UML.value, literal)
        return pin

    def chain(self, protocol: URIRef, actions: list[URIRef]) -> None:
        initial = self.node(f"{protocol}/initial", UML.InitialNode)
        final = self.node(f"{protocol}/final", UML.FlowFinalNode)
        for node in (initial, *actions, final):
            self.add(protocol, UML.node, node)
        nodes = [initial, *actions, final]
        for start, end in zip(nodes, nodes[1:], strict=False):
            self.flow(protocol, start, end, control=True)

    def fork(self, protocol: URIRef, source: URIRef) -> URIRef:
        node = self.node(f"{source}/fork", UML.ForkNode)
        self.add(protocol, UML.node, node)
        self.flow(protocol, source, node, control=False)
        return node

    def scalar_pin(self, action: URIRef, name: str, value: int | str) -> URIRef:
        pin = self.pin(action, name)
        self.graph.remove((pin, RDF.type, UML.InputPin))
        self.add(pin, RDF.type, UML.ValuePin)
        self.add(pin, UML.value, self.literal(f"{pin}/value", value))
        return pin

    def amount_pin(self, action: URIRef, name: str, value: Decimal | int, unit: URIRef) -> URIRef:
        return self.pin(
            action,
            name,
            self.measure(
                f"{action}/input_{name}/value/measure",
                value,
                unit,
            ),
            owned=True,
        )


def export(experiment: ExperimentPlan) -> LabOPDocument:
    """Export ordered stage calls and explicit material/control flows."""
    writer = _Writer()
    root = writer.node(experiment.identity + "/labop", LABOP.Protocol, top=True)
    writer.add(root, LAB.planDigest, Literal(experiment.digest))
    writer.add(root, LAB.evidenceState, LAB.planned)
    calls: list[URIRef] = []
    outputs: dict[tuple[str, str], URIRef] = {}
    for stage in experiment.stages:
        protocol, collections, ports = _stage(writer, stage)
        call = writer.node(f"{root}/stage_{len(calls) + 1}", UML.CallBehaviorAction)
        writer.add(call, UML.behavior, protocol)
        writer.add(call, LAB.plannedActivity, URIRef(stage.identity))
        for resource, (name, collection) in collections.items():
            handoffs = tuple(
                item for item in stage.handoffs if item.destination.resource == resource
            )
            if not handoffs:
                writer.pin(call, name, collection)
            else:
                sources = {(item.producer, item.source.resource) for item in handoffs}
                if len(sources) != 1 or any(
                    item.source.well != item.destination.well for item in handoffs
                ):
                    raise ValueError("LabOP stage handoffs require whole-plate continuity")
                pin = writer.pin(call, name)
                writer.flow(root, outputs[next(iter(sources))], pin, control=False)
                writer.add(
                    pin, LAB.handoffs, Literal(json.dumps(semantic(handoffs), sort_keys=True))
                )
        for resource, name in ports.items():
            pin = writer.pin(call, name, output=True)
            outputs[(stage.identity, resource)] = writer.fork(root, pin)
        calls.append(call)
    writer.chain(root, calls)
    return LabOPDocument(str(root), turtle(writer.graph))


def _stage(
    writer: _Writer, stage: ProtocolStage
) -> tuple[URIRef, dict[str, tuple[str, URIRef]], dict[str, str]]:
    recorded = stage.protocol
    protocol = writer.node(str(recorded.identity), LABOP.Protocol, name=recorded.name, top=True)
    writer.add(protocol, SBOL.description, Literal(recorded.description))
    writer.add(protocol, LAB.planDigest, Literal(recorded.digest))
    writer.add(protocol, LAB.evidenceState, LAB.planned)
    writer.add(protocol, LAB.external, Literal(stage.external))
    collections: dict[str, tuple[str, URIRef]] = {}
    sources: dict[str, URIRef] = {}
    samples = {sample.id: sample for sample in recorded.samples}
    for index, resource in enumerate(recorded.resources):
        name = f"collection_{index}"
        ordered = writer.parameter(protocol, name, LABOP.SampleCollection, index)
        parameter = writer.one(ordered, UML.propertyValue)
        default = writer.node(f"{parameter}/default", UML.LiteralIdentified)
        array = writer.node(f"{default}/samples", LABOP.SampleArray, name=resource.name)
        writer.add(parameter, UML.defaultValue, default)
        writer.add(default, UML.identifiedValue, array)
        container = writer.node(
            f"{protocol}/container_{index}", LABOP.ContainerSpec, name=resource.name, top=True
        )
        writer.add(container, LAB.rows, Literal(resource.rows))
        writer.add(container, LAB.columns, Literal(resource.columns))
        writer.add(
            container,
            LAB.capacity,
            writer.measure(f"{container}/capacity", resource.capacity, OM.microlitre),
        )
        writer.add(array, LABOP.containerType, container)
        writer.add(array, LAB.sampleFormat, Literal("json"))
        fills = {fill.well: fill for fill in resource.fills}
        counted = {
            place.location.well: samples[place.sample_id]
            for place in recorded.placements
            if place.location.resource == resource.name
            and place.sample_id in recorded.input_sample_ids
            and samples[place.sample_id].count is not None
        }
        counted_materials = {
            well: sample.implementation.identity
            if sample.implementation
            else sample.material_identity
            for well, sample in counted.items()
        }
        writer.add(
            array,
            LABOP.initial_contents,
            Literal(
                quote(
                    json.dumps(
                        {
                            well: (
                                fills[well].material
                                if well in fills
                                else counted_materials.get(well)
                            )
                            for well in resource.wells
                        }
                    )
                )
            ),
        )
        writer.add(array, LAB.resource, Literal(resource.name))
        for fill in resource.fills:
            node = writer.node(f"{array}/initial_{fill.well}", LAB.InitialMaterial)
            writer.add(array, LAB.initialMaterial, node)
            writer.add(node, LAB.coordinates, Literal(fill.well))
            writer.add(node, LAB.materialIdentity, Literal(fill.material))
            writer.add(
                node, LAB.volume, writer.measure(f"{node}/volume", fill.volume, OM.microlitre)
            )
        for well, sample in counted.items():
            node = writer.node(f"{array}/initial_{well}", LAB.InitialMaterial)
            writer.add(array, LAB.initialMaterial, node)
            writer.add(node, LAB.coordinates, Literal(well))
            writer.add(
                node,
                LAB.materialIdentity,
                Literal(
                    sample.implementation.identity
                    if sample.implementation
                    else sample.material_identity
                ),
            )
            writer.add(node, LAB.itemCount, Literal(sample.count))
        for place in recorded.placements:
            if place.location.resource != resource.name:
                continue
            sample = samples[place.sample_id]
            node = writer.node(f"{array}/sample_{place.location.well}", LAB.SampleAssertion)
            writer.add(array, LAB.plannedSample, node)
            writer.add(node, LAB.coordinates, Literal(place.location.well))
            writer.add(node, LAB.sampleIdentity, Literal(sample.id))
            if sample.form is not None:
                writer.add(node, LAB.materialForm, URIRef(LAB + sample.form.value))
            if sample.count is not None:
                writer.add(node, LAB.sampleCount, Literal(sample.count))
            writer.add(node, LAB.sampleRole, Literal(sample.role))
            for predicate, ref in (
                (LAB.design, sample.design),
                (LAB.implementation, sample.implementation),
            ):
                if ref is not None:
                    writer.add(node, predicate, URIRef(ref.identity))
        input_node = writer.node(f"{protocol}/input_{index}", UML.ActivityParameterNode)
        writer.add(protocol, UML.node, input_node)
        writer.add(input_node, UML.parameter, ordered)
        sources[resource.name] = writer.fork(protocol, input_node)
        collections[resource.name] = (name, array)

    actions: list[URIRef] = []
    selections: dict[Location, URIRef] = {}

    def well_source(location: Location) -> URIRef:
        if location not in selections:
            action = writer.node(f"{protocol}/select_{len(selections)}", UML.CallBehaviorAction)
            writer.add(action, UML.behavior, writer.primitive("PlateCoordinates", upstream=True))
            pin = writer.pin(action, "source")
            writer.flow(protocol, sources[location.resource], pin, control=False)
            writer.scalar_pin(action, "coordinates", location.well)
            output = writer.pin(action, "samples", output=True)
            selections[location] = writer.fork(protocol, output)
            actions.append(action)
        return selections[location]

    def collection_pin(action: URIRef, name: str, source: URIRef) -> None:
        writer.flow(protocol, source, writer.pin(action, name), control=False)

    for step in recorded.steps:
        action = writer.node(str(step.identity), UML.CallBehaviorAction)
        writer.add(action, LAB.semanticStep, Literal(json.dumps(semantic(step), sort_keys=True)))
        if isinstance(step, Transfer):
            primitive = (
                writer.primitive("Transfer", upstream=True)
                if step.destination_height_mm is None
                else writer.primitive("TransferAtHeight")
            )
            collection_pin(action, "source", well_source(step.source))
            collection_pin(action, "destination", well_source(step.destination))
            writer.amount_pin(action, "amount", step.volume, OM.microlitre)
            if step.destination_height_mm is not None:
                writer.amount_pin(
                    action, "destinationHeight", step.destination_height_mm, OM.millimetre
                )
        elif isinstance(step, Mix):
            primitive = writer.primitive("PipetteMix", upstream=True)
            collection_pin(action, "samples", well_source(step.location))
            writer.amount_pin(action, "amount", step.volume, OM.microlitre)
            writer.amount_pin(action, "cycleCount", step.cycles, OM.one)
        elif isinstance(step, Wait):
            primitive = writer.primitive("Wait")
            writer.amount_pin(action, "duration", step.seconds, OM.second)
        elif isinstance(step, ManualInstruction):
            primitive = writer.primitive("OperatorPause")
            writer.scalar_pin(action, "instruction", step.text)
        elif isinstance(step, ExternalPreparation):
            primitive = URIRef(EXT + f"ExternalPreparation_{len(step.inputs)}_{len(step.outputs)}")
            if (primitive, RDF.type, LABOP.Primitive) not in writer.graph:
                writer.node(str(primitive), LABOP.Primitive, top=True)
                writer.add(
                    primitive,
                    SBOL.description,
                    Literal(
                        "Perform the named external procedure using the declared material inputs. "
                        "Outputs specify expected quantities at distinct destination locations. "
                        "Counted amounts use OM one; liquid amounts use microlitres. "
                        "This action requires operator execution and output observations."
                    ),
                )
                writer.parameter(primitive, "procedure", UML.ValueSpecification, 0)
                writer.parameter(primitive, "instruction", UML.ValueSpecification, 1)
                parameter_index = 2
                for prefix, material_ports in (
                    ("source", step.inputs),
                    ("destination", step.outputs),
                ):
                    for index in range(len(material_ports)):
                        writer.parameter(
                            primitive, f"{prefix}_{index}", LABOP.SampleCollection, parameter_index
                        )
                        writer.parameter(
                            primitive, f"{prefix}Amount_{index}", OM.Measure, parameter_index + 1
                        )
                        parameter_index += 2
            writer.scalar_pin(action, "procedure", step.procedure)
            writer.scalar_pin(action, "instruction", step.instructions)
            for prefix, material_ports in (("source", step.inputs), ("destination", step.outputs)):
                for index, port in enumerate(material_ports):
                    collection_pin(action, f"{prefix}_{index}", well_source(port.location))
                    writer.amount_pin(
                        action,
                        f"{prefix}Amount_{index}",
                        port.count or port.volume_ul,
                        OM.one if port.count else OM.microlitre,
                    )
        elif isinstance(step, Distribute):
            primitive = writer.primitive("Distribute")
            collection_pin(action, "source", well_source(step.source))
            members = tuple(well_source(location) for location in step.destinations)
            # A pure structural primitive preserves order and aliases across plates.
            group_type = URIRef(EXT + f"OrderedGroup_{len(members)}")
            if (group_type, RDF.type, LABOP.Primitive) not in writer.graph:
                writer.node(str(group_type), LABOP.Primitive, top=True)
                writer.add(
                    group_type,
                    SBOL.description,
                    Literal(
                        "Return an ordered SampleCollection of the supplied sample aliases. "
                        "Preserve member order and duplicates; perform no laboratory operation."
                    ),
                )
                for index in range(len(members)):
                    writer.parameter(group_type, f"member_{index}", LABOP.SampleCollection, index)
                writer.parameter(
                    group_type, "samples", LABOP.SampleCollection, len(members), direction="out"
                )
            group = writer.node(f"{protocol}/group_{len(actions)}", UML.CallBehaviorAction)
            writer.add(group, UML.behavior, group_type)
            for index, source in enumerate(members):
                collection_pin(group, f"member_{index}", source)
            writer.flow(
                protocol,
                writer.pin(group, "samples", output=True),
                writer.pin(action, "destinations"),
                control=False,
            )
            actions.append(group)
            writer.amount_pin(action, "amount", step.volume, OM.microlitre)
            if step.air_gap is not None:
                writer.amount_pin(action, "airGap", step.air_gap, OM.microlitre)
        elif isinstance(step, (SetTemperature, Thermocycle)):
            primitive = writer.primitive(type(step).__name__)
            collection_pin(action, "samples", sources[step.resource])
            if isinstance(step, SetTemperature):
                writer.amount_pin(action, "temperature", step.celsius, OM.degreeCelsius)
            else:
                profile = writer.node(f"{action}/input_profile/value/profile", LAB.ThermalProfile)
                for index, hold in enumerate(step.profile):
                    node = writer.node(f"{profile}/hold_{index}", LAB.ThermalHold)
                    writer.add(profile, LAB.hold, node)
                    writer.add(node, LAB["index"], Literal(index))
                    writer.add(
                        node,
                        LAB.temperature,
                        writer.measure(f"{node}/temperature", hold.celsius, OM.degreeCelsius),
                    )
                    writer.add(
                        node,
                        LAB.duration,
                        writer.measure(f"{node}/duration", hold.seconds, OM.second),
                    )
                writer.pin(action, "profile", profile, owned=True)
                writer.scalar_pin(action, "cycles", step.cycles)
                if step.lid_celsius is not None:
                    writer.amount_pin(action, "lidTemperature", step.lid_celsius, OM.degreeCelsius)
                if step.block_volume is not None:
                    writer.amount_pin(action, "blockVolume", step.block_volume, OM.microlitre)
        else:
            raise TypeError(f"No LabOP mapping for {type(step).__name__}")
        writer.add(action, UML.behavior, primitive)
        actions.append(action)
    ports: dict[str, str] = {}
    for place in recorded.placements:
        resource_name = place.location.resource
        if place.sample_id not in recorded.output_sample_ids or resource_name in ports:
            continue
        name = f"result_{len(ports)}"
        parameter = writer.parameter(
            protocol, name, LABOP.SampleCollection, len(collections) + len(ports), direction="out"
        )
        node = writer.node(f"{protocol}/output_{len(ports)}", UML.ActivityParameterNode)
        writer.add(protocol, UML.node, node)
        writer.add(node, UML.parameter, parameter)
        writer.flow(protocol, sources[resource_name], node, control=False)
        ports[resource_name] = name
    writer.chain(protocol, actions)
    return protocol, collections, ports
