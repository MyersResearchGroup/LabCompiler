"""LabOP execution evidence without invoking the LabOP execution engine."""

from rdflib import RDF, Literal, Namespace, URIRef

from lab.compiler import ExperimentCompilation
from lab.execution.records import Outcome, StepRecord, step_activity
from lab.labop.primitives import LAB, LABOP, UML
from lab.labop.protocol import _Writer, export, turtle
from lab.provenance import DocumentSnapshot

PROV = Namespace("http://www.w3.org/ns/prov#")


def project(
    compilation: ExperimentCompilation,
    provenance: DocumentSnapshot,
    *,
    identity: str,
    records: tuple[StepRecord, ...],
    complete: bool,
) -> str:
    writer = _Writer()
    writer.graph.parse(data=export(compilation.experiment).text, format="turtle")
    # SBOL and LabOP use the same observed activity identities and timestamps.
    writer.graph.parse(data=provenance.to_turtle(), format="turtle")
    root = URIRef(identity)
    protocol = URIRef(compilation.experiment.identity + "/labop")
    writer.add(root, RDF.type, LABOP.ProtocolExecution)
    writer.add(root, LABOP.protocol, protocol)
    writer.add(root, PROV.type, protocol)
    writer.add(root, LABOP.completedNormally, Literal(complete))
    writer.add(
        root,
        LAB.recordCoverage,
        Literal(
            "Explicit semantic-step observations; structural UML token firings were not captured."
        ),
    )
    step_indices = {
        step.identity: index
        for index, step in enumerate(
            step for stage in compilation.stages for step in stage.protocol.steps
        )
    }
    owners: dict[str, URIRef] = {}
    for index, stage in enumerate(compilation.stages, 1):
        ids = [str(step.identity) for step in stage.protocol.steps]
        observed = tuple(record for record in records if record.step in ids)
        if not observed:
            continue
        scope = URIRef(identity + f"/stage_{index}")
        stage_protocol = URIRef(str(stage.protocol.identity))
        writer.add(scope, RDF.type, LABOP.ProtocolExecution)
        writer.add(scope, LABOP.protocol, stage_protocol)
        writer.add(scope, PROV.type, stage_protocol)
        latest = {record.step: record for record in sorted(observed, key=lambda item: item.attempt)}
        finished = len(latest) == len(ids) and all(
            record.outcome is Outcome.SUCCEEDED for record in latest.values()
        )
        ordered = [latest[step] for step in ids if step in latest]
        finished = finished and all(
            first.ended_at <= second.started_at
            for first, second in zip(ordered, ordered[1:], strict=False)
        )
        writer.add(scope, LABOP.completedNormally, Literal(finished))
        writer.add(root, LAB.subprotocolExecution, scope)
        owners.update({step: scope for step in ids})
    for record in records:
        # A skipped step has an observation, but does not claim an action firing.
        if record.outcome is Outcome.SKIPPED:
            continue
        activity = URIRef(step_activity(identity, step_indices[record.step], record.attempt))
        action = URIRef(record.step)
        writer.add(activity, RDF.type, LABOP.BehaviorExecution)
        writer.add(activity, PROV.type, writer.one(action, UML.behavior))
        writer.add(activity, LABOP.completedNormally, Literal(record.outcome is Outcome.SUCCEEDED))
        node = writer.node(str(activity) + "/firing", LABOP.CallBehaviorExecution)
        writer.add(node, LABOP.node, action)
        writer.add(node, LABOP.call, activity)
        writer.add(owners[record.step], LABOP.execution, node)
    return turtle(writer.graph)
