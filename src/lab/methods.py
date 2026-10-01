"""Digital methods rendered from the complete frozen experiment."""

from lab.documents import describe
from lab.experiment import ExperimentPlan
from lab.units import number


def render(experiment: ExperimentPlan) -> str:
    """Render a planned methods specification without claiming completed work."""
    lines = [
        "# Planned methods",
        "",
        f"Experiment: <{experiment.identity}>",
        "",
        f"Semantic plan SHA-256: {experiment.digest}",
        "",
        "This document specifies planned work. It does not establish that any physical "
        "step was performed. The accompanying SBOL document contains design sequences "
        "and provenance; the LabOP document specifies control and material flow.",
        "",
    ]
    if not experiment.stages:
        lines += [
            "The requested materials are allocated from inventory; no procedure is required.",
            "",
        ]
    for index, stage in enumerate(experiment.stages, 1):
        protocol = stage.protocol
        lines += [
            f"## {index}. {protocol.name}",
            "",
            f"Stage: <{stage.identity}>. Protocol: <{protocol.identity}>.",
            "",
            protocol.description,
            "",
            "### Initial conditions",
            "",
            "These loads are preconditions, not inferred dispensing operations.",
            "",
        ]
        for resource in protocol.resources:
            lines.append(
                f"- {resource.name}: {resource.rows} × {resource.columns} wells, "
                f"{number(resource.capacity)} µL capacity and "
                f"{number(resource.dead_volume)} µL residual volume per well."
            )
            for fill in resource.fills:
                lines.append(
                    f"- {resource.name}:{fill.well}: {number(fill.volume)} µL of {fill.material}."
                )
        lines += ["", "### Material identity", ""]
        for sample in protocol.samples:
            quantity = f"; {sample.count} unit(s)" if sample.count is not None else ""
            form = f"; form {sample.form.value}" if sample.form is not None else ""
            lines.append(
                f"- {sample.id}: design {sample.material_identity}; implementation "
                f"{sample.implementation.identity if sample.implementation else 'unspecified'}"
                f"{form}{quantity}."
            )
        lines += ["", "### Handoffs", ""]
        if not stage.handoffs:
            lines += ["No upstream stage handoff.", ""]
        for handoff in stage.handoffs:
            amount = (
                f"{handoff.count} unit(s)"
                if handoff.count is not None
                else f"{number(handoff.volume_ul)} µL"
            )
            lines += [
                f"- Carry {handoff.implementation.identity} from stage <{handoff.producer}> "
                f"at {handoff.source} to the logical binding {handoff.destination}, with "
                f"{amount} remaining. This identifies the same material "
                "and plate; it does not specify an additional liquid transfer.",
            ]
        lines += ["", "### Procedure", ""]
        for step_index, step in enumerate(protocol.steps, 1):
            lines += [f"{step_index}. {describe(step)} Step: <{step.identity}>."]
        lines += ["", "### Planned outputs", ""]
        for sample in protocol.output_manifest().samples:
            lines.append(f"- {sample.id}; intended design {sample.material_identity}.")
        lines.append("")
    return "\n".join(lines)
