import builtins
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from io import StringIO

import pytest

import lab
from examples.deck_layouts import deck as example_deck
from examples.deck_layouts import protocol as example_protocol
from lab import CompileError, Protocol, celsius, seconds, uL
from lab.deck import Container, Deck, DeckSite, HolderSite, Module, Slot
from lab.equipment import LabwareModel, ModuleModel
from lab.labware import COLD_BLOCK_24, PCR_PLATE_96, PLATE_96, LabwareKind, LabwareSpec
from lab.samples import Location
from lab.targets import Labware, LiquidHandler, Manual
from lab.targets.lower import lower_deck
from tests.target_fixture import target, water_aliquots
from tests.thermal_fixture import thermal_aliquots

try:
    from opentrons.protocol_api import ThermocyclerContext
    from opentrons.simulate import simulate
except ImportError:
    _has_opentrons = False
else:
    _has_opentrons = True

try:
    from pylabrobot.liquid_handling.backends import SerializingBackend
    from pylabrobot.liquid_handling.backends.hamilton.STAR_chatterbox import STARChatterboxBackend
    from pylabrobot.resources import Resource, set_volume_tracking

except ImportError:
    _has_star = False
else:
    _has_star = True

requires_opentrons = pytest.mark.skipif(not _has_opentrons, reason="Opentrons SDK not installed")
requires_star = pytest.mark.skipif(not _has_star, reason="PyLabRobot SDK not installed")


def compiled(protocol, hardware):
    declared = getattr(hardware, "liquid_handler", None)
    if isinstance(declared, LiquidHandler):
        return lab.compile(protocol, hardware, liquid_handler=declared)
    return lab.compile(protocol, hardware)


@pytest.mark.integration
@requires_opentrons
@pytest.mark.parametrize("name", ["ot2", "flex"])
def test_generated_opentrons_runs_in_official_simulator(name):
    p = water_aliquots()
    bundle = compiled(p, target(name))
    log, _ = simulate(StringIO(bundle.files["protocol.py"]))
    texts = [event["payload"]["text"] for event in log]
    assert sum(text.startswith("Aspirating 25.0") for text in texts) == 8
    assert sum(text.startswith("Dispensing 25.0") for text in texts) == 8
    destinations = [
        text.split(" into ", 1)[1].split(" of ", 1)[0]
        for text in texts
        if text.startswith("Dispensing 25.0")
    ]
    assert destinations == [f"A{index}" for index in range(1, 9)]
    assert sum(text.startswith("Picking up tip") for text in texts) == 9
    assert sum(text.startswith("Dropping tip") for text in texts) == 9
    assert any(text.startswith("Mixing 3 times with a volume of 20.0") for text in texts)
    data = json.loads(bundle.plan_json)
    assert data["source_sha256"] == hashlib.sha256(bundle.files["protocol.py"].encode()).hexdigest()
    assert dict(bundle.final_volumes)[Location("water", "A1")] == Decimal(100)
    assert any("A1" in text and "plate" in text.lower() for text in texts)


def test_core_does_not_import_robot_sdks(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("opentrons", "pylabrobot")):
            raise AssertionError("Manual compilation must not import a robot SDK")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert lab.compile(water_aliquots(), Manual()).target.name == "Manual"


@pytest.mark.integration
@requires_opentrons
@pytest.mark.parametrize("name", ["ot2", "flex"])
def test_opentrons_missing_bindings_conflicts_and_tips(name):
    t = target(name)
    t.labware.pop("water")
    with pytest.raises(CompileError, match="exactly"):
        compiled(water_aliquots(), t)
    t = target(name)
    t.tip_racks = ()
    with pytest.raises(CompileError, match="fresh tips"):
        compiled(water_aliquots(), t)
    t = target(name)
    t.labware["aliquots"] = replace(t.labware["aliquots"], slot=t.labware["water"].slot)
    with pytest.raises(CompileError, match="Conflicting labware"):
        compiled(water_aliquots(), t)
    t = target(name)
    t.tip_racks = (replace(t.tip_racks[0], wells=("A1", "A1")),)
    with pytest.raises(CompileError, match="only be supplied once"):
        compiled(water_aliquots(), t)


@pytest.mark.integration
@requires_opentrons
def test_opentrons_range_trash_and_config_freezing():
    t = target("flex")
    t.labware["water"] = replace(t.labware["water"], slot="A3")
    with pytest.raises(CompileError, match="Unavailable"):
        compiled(water_aliquots(), t)
    t = target("ot2")
    t.tip_racks = (Labware("opentrons_flex_96_tiprack_200ul", "3"),)
    with pytest.raises(CompileError, match="requires"):
        compiled(water_aliquots(), t)
    t = target("ot2")
    bundle = compiled(water_aliquots(), t)
    frozen = bundle.files
    t.labware.clear()
    assert bundle.files == frozen
    p = Protocol("Below range")
    a = p.container("water", contents="water", volume=100 * uL, capacity=300 * uL)
    b = p.plate("aliquots", capacity=200 * uL)
    p.transfer(a, b["A1"], volume=1 * uL)
    with pytest.raises(CompileError, match="outside"):
        compiled(p, target("ot2"))


@pytest.mark.integration
@requires_star
async def test_star_executes_real_pylabrobot_frontend_and_tracks_volumes():
    class Recorder(SerializingBackend):
        def __init__(self):
            super().__init__(num_channels=8)
            self.events = []

        async def send_command(self, command, data=None):
            self.events.append((command, data))
            return None

    async def no_wait(_):
        pass

    t = target("star")
    bundle = compiled(water_aliquots(), t)
    saved_deck = Resource.deserialize(
        json.loads(json.loads(bundle.plan_json)["target"]["configuration"]["deck_json"])
    )
    assert saved_deck.get_resource("aliquot_plate").get_item("A2").name == "aliquot_plate_well_A2"
    before = bundle.files
    t.labware.clear()
    assert bundle.files == before
    namespace = {"__name__": "_generated_protocol"}
    exec(builtins.compile(bundle.files["protocol.py"], "generated_star.py", "exec"), namespace)
    backend = Recorder()
    set_volume_tracking(True)
    try:
        handler = await namespace["run"](backend, sleep=no_wait)
    finally:
        set_volume_tracking(False)
    actions = [
        (name, data)
        for name, data in backend.events
        if name in ("pick_up_tips", "aspirate", "dispense", "drop_tips")
    ]
    names = [name for name, _ in actions]
    assert names[:32] == ["pick_up_tips", "aspirate", "dispense", "drop_tips"] * 8
    assert names[32:] == [
        "pick_up_tips",
        "aspirate",
        "dispense",
        "aspirate",
        "dispense",
        "aspirate",
        "dispense",
        "drop_tips",
    ]
    assert [data["channels"][0]["volume"] for name, data in actions if name == "aspirate"] == (
        [25] * 8 + [20] * 3
    )
    picked = [
        data["channels"][0]["resource_name"] for name, data in actions if name == "pick_up_tips"
    ]
    assert len(set(picked)) == 9
    assert backend.events[0][0] == "setup"
    assert backend.events[-1][0] == "stop"
    assert handler.deck.get_resource("water_plate").get_item("A1").tracker.get_used_volume() == 100
    for name in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"):
        assert (
            handler.deck.get_resource("aliquot_plate").get_item(name).tracker.get_used_volume()
            == 25
        )


@pytest.mark.integration
@requires_star
async def test_star_example_preserves_supplied_carriers_sites_and_well_bindings():
    class Recorder(SerializingBackend):
        async def send_command(self, command, data=None):
            return None

    def placements(deck):
        return {
            resource.name: (resource.model, resource.parent.name, resource.get_absolute_location())
            for resource in deck.get_all_children()
        }

    deck = example_deck()
    hardware = lower_deck(deck, LiquidHandler.STAR)
    source = hardware.labware["sources"].get_item("B1")
    assay = hardware.labware["assay"]
    source_name = source.name
    source_location = source.get_absolute_location()
    assay_location = assay.get_absolute_location()
    expected_placements = placements(hardware.deck)
    bundle = lab.compile(example_protocol(), deck=deck, liquid_handler=LiquidHandler.STAR)
    configuration = json.loads(bundle.plan_json)["target"]["configuration"]
    saved_deck = Resource.deserialize(json.loads(configuration["deck_json"]))
    assert placements(saved_deck) == expected_placements
    bindings = {binding.location: binding.physical for binding in bundle.target.bindings}
    for column in range(1, 13):
        assert bindings[Location("sources", f"A{column}")] == (
            hardware.labware["sources"].get_item(f"B{column}").name
        )
    assert bindings[Location("assay", "A2")] == assay.get_item("A2").name
    assert bindings[Location("working_reagent", "A1")] == (
        hardware.labware["working_reagent"].get_item("A1").name
    )

    # The generated artifact must retain the supplied placement even if the caller edits it.
    hardware.deck.get_resource("plate_carrier").location.x += 10
    namespace = {"__name__": "_generated"}
    exec(builtins.compile(bundle.files["protocol.py"], "star_example.py", "exec"), namespace)
    set_volume_tracking(True)
    handoffs = []
    try:
        handler = await namespace["run"](Recorder(num_channels=8), confirm=handoffs.append)
    finally:
        set_volume_tracking(False)
    restored_source = handler.deck.get_resource(source_name)
    restored_assay = handler.deck.get_resource("assay")
    assert restored_source.get_absolute_location() == source_location
    assert restored_assay.get_absolute_location() == assay_location
    assert restored_source.tracker.get_used_volume() == 50
    for column in range(1, 13):
        assert (
            handler.deck.get_resource("sources").get_item(f"B{column}").tracker.get_used_volume()
            == 50
        )
        for row in ("A", "B"):
            assert restored_assay.get_item(f"{row}{column}").tracker.get_used_volume() == 225
    reagent = handler.deck.get_resource("working_reagent")
    assert reagent.get_item("A1").tracker.get_used_volume() == 1200
    assert reagent.parent is handler.deck.get_resource("plate_carrier")[4]
    assert len(handoffs) == 1 and "BCA" in handoffs[0]
    assert restored_assay.parent is handler.deck.get_resource("plate_carrier")[3]
    assert handler.deck.get_resource("tips").parent is handler.deck.get_resource("tip_carrier")[2]


@pytest.mark.integration
@requires_opentrons
@pytest.mark.parametrize("handler", [LiquidHandler.OT2, LiquidHandler.FLEX])
def test_same_lab_deck_compiles_for_opentrons(handler):
    bundle = lab.compile(example_protocol(), deck=example_deck(), liquid_handler=handler)
    log, _ = simulate(StringIO(bundle.files["protocol.py"]))
    aspirations = [
        event["payload"]["text"]
        for event in log
        if event["payload"]["text"].startswith("Aspirating")
    ]
    assert len(aspirations) == 48
    assert all(text.startswith("Aspirating 25.0") for text in aspirations[:24])
    assert [text.split(" from ", 1)[1].split(" of ", 1)[0] for text in aspirations[:24]] == [
        f"B{column}" for _ in range(2) for column in range(1, 13)
    ]
    assert all(text.startswith("Aspirating 200.0") for text in aspirations[24:])
    assert all("A1 of NEST 12 Well Reservoir 15 mL" in text for text in aspirations[24:])
    volumes = dict(bundle.final_volumes)
    for column in range(1, 13):
        assert volumes[Location("sources", f"A{column}")] == 50
        for row in ("A", "B"):
            assert volumes[Location("assay", f"{row}{column}")] == 225
    assert volumes[Location("working_reagent", "A1")] == 1200
    assert any("Pausing" in event["payload"]["text"] for event in log)
    configuration = json.loads(bundle.target.configuration_json)
    assert len(configuration["lab_deck"]["layouts"]) == 3


@pytest.mark.integration
@pytest.mark.parametrize("handler", list(LiquidHandler))
def test_ambient_plate_preset_remains_portable(handler):
    if (handler == LiquidHandler.STAR and not _has_star) or (
        handler != LiquidHandler.STAR and not _has_opentrons
    ):
        pytest.skip("Target SDK not installed")
    deck = Deck(
        containers=(
            Container(
                id="water",
                labware=LabwareSpec(
                    kind=LabwareKind.PLATE, rows=1, columns=1, capacity_ul=Decimal(300)
                ),
                site=DeckSite.PLATES,
            ),
            Container(id="aliquots", labware=PLATE_96, site=DeckSite.PLATES),
        )
    )
    bundle = lab.compile(water_aliquots(), deck=deck, liquid_handler=handler)
    assert dict(bundle.final_volumes)[Location("water", "A1")] == 100


@pytest.mark.integration
@requires_opentrons
def test_lab_layout_supports_two_independent_temperature_modules():
    template = example_deck().layout_for(LiquidHandler.OT2)
    containers = tuple(
        Container(id=name, labware=COLD_BLOCK_24, site=DeckSite.TEMPERATURE_MODULE)
        for name in ("first", "second")
    )
    layout = replace(
        template,
        placements=tuple(
            replace(
                template.placements[0],
                container=name,
                model=LabwareModel.OPENTRONS_24_COLD_BLOCK,
                location=HolderSite(f"{name}_module"),
                wells=(),
            )
            for name in ("first", "second")
        ),
        modules=(
            Module(id="first_module", model=ModuleModel.TEMPERATURE_GEN2, location=Slot("1")),
            Module(id="second_module", model=ModuleModel.TEMPERATURE_GEN2, location=Slot("4")),
        ),
    )
    protocol = Protocol("Two temperature devices")
    for name in ("first", "second"):
        block = protocol.plate(name, shape=(4, 6), capacity=1500 * uL)
        protocol.set_temperature(block, celsius(4))
    bundle = lab.compile(
        protocol,
        deck=Deck(containers=containers, layouts=(layout,)),
        liquid_handler=LiquidHandler.OT2,
    )
    simulate(StringIO(bundle.files["protocol.py"]))
    assert "temperature_module_1.set_temperature(4)" in bundle.files["protocol.py"]
    assert "temperature_module_4.set_temperature(4)" in bundle.files["protocol.py"]


@pytest.mark.integration
@requires_opentrons
@pytest.mark.parametrize("source_slot", ["1", "8"])
def test_lab_thermocycler_layout_reserves_all_occupied_slots(source_slot):
    deck = example_deck()
    # This capability check uses a PCR plate, independently of the BCA assay.
    p = Protocol("Thermocycler footprint")
    sources = p.plate("sources", shape=(1, 12), capacity=200 * uL)
    assay = p.plate("assay", capacity=100 * uL)
    p.container("working_reagent", capacity=15000 * uL)
    p.load(sources["A1"], "Buffer", volume=100 * uL)
    p.transfer(sources["A1"], assay["A1"], volume=25 * uL)
    template = deck.layout_for(LiquidHandler.OT2)
    layout = replace(
        template,
        placements=(
            replace(template.placements[0], location=Slot(source_slot)),
            replace(
                template.placements[1],
                model=LabwareModel.NEST_96_PCR_100_UL,
                location=HolderSite("cycler"),
            ),
            *template.placements[2:],
        ),
        modules=(Module(id="cycler", model=ModuleModel.THERMOCYCLER_GEN1, location=Slot("7")),),
    )
    deck = replace(
        deck,
        containers=(
            deck.containers[0],
            replace(deck.containers[1], labware=PCR_PLATE_96),
            *deck.containers[2:],
        ),
        layouts=(layout,),
    )
    if source_slot == "8":
        with pytest.raises(CompileError, match="Unavailable OT-2 deck slot: 8"):
            lab.compile(p, deck=deck, liquid_handler=LiquidHandler.OT2)
    else:
        bundle = lab.compile(p, deck=deck, liquid_handler=LiquidHandler.OT2)
        simulate(StringIO(bundle.files["protocol.py"]))


@pytest.mark.integration
@requires_star
def test_star_missing_tips_range_and_deck_membership():
    t = target("star")
    t.labware.pop("water")
    with pytest.raises(CompileError, match="bindings.*exactly"):
        compiled(water_aliquots(), t)
    t = target("star")
    t.tip_racks = ()
    with pytest.raises(CompileError, match="fresh tips"):
        compiled(water_aliquots(), t)
    t = target("star")
    t.max_volume = 20 * uL
    with pytest.raises(CompileError, match="outside"):
        compiled(water_aliquots(), t)
    t = target("star")
    foreign = target("star")
    t.labware["water"] = foreign.labware["water"]
    with pytest.raises(CompileError, match="does not belong"):
        compiled(water_aliquots(), t)


@pytest.mark.integration
@requires_star
@pytest.mark.parametrize("thermal,tips,actions", [(False, 9, 11), (True, 6, 7)])
async def test_star_firmware_generation_without_hardware(capsys, thermal, tips, actions):
    p = thermal_aliquots() if thermal else water_aliquots()
    bundle = compiled(p, target("star", thermal=thermal))
    namespace = {"__name__": "_generated"}
    exec(builtins.compile(bundle.files["protocol.py"], "star.py", "exec"), namespace)

    async def no_wait(_):
        pass

    await namespace["run"](
        STARChatterboxBackend(), sleep=no_wait, thermocycle=namespace.get("preview_thermocycle")
    )
    commands = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("C0TP") for line in commands) == tips
    assert sum(line.startswith("C0AS") for line in commands) == actions
    assert sum(line.startswith("C0DS") for line in commands) == actions
    assert sum(line.startswith("C0TR") for line in commands) == tips


@pytest.mark.integration
@requires_star
async def test_star_manual_preflight_and_failure_cleanup():
    class FailingBackend(SerializingBackend):
        def __init__(self):
            super().__init__(num_channels=8)
            self.events = []

        async def send_command(self, command, data=None):
            self.events.append(command)
            if command == "aspirate":
                raise RuntimeError("Synthetic backend failure")
            return None

    p = water_aliquots()
    p.manual("Inspect the water aliquots without changing their contents.")
    namespace = {"__name__": "_generated"}
    bundle = compiled(p, target("star"))
    exec(builtins.compile(bundle.files["protocol.py"], "star.py", "exec"), namespace)
    backend = FailingBackend()
    with pytest.raises(ValueError, match="Supply confirm"):
        await namespace["run"](backend)
    assert backend.events == []
    with pytest.raises(RuntimeError, match="Synthetic backend failure"):
        await namespace["run"](backend, confirm=lambda text: None)
    assert backend.events[-1] == "stop"
    assert backend.events.count("aspirate") == 1


@pytest.mark.integration
@requires_opentrons
@pytest.mark.parametrize("name", ["ot2", "flex"])
def test_operator_pause_survives_opentrons_generation(name):
    p = water_aliquots()
    message = "Inspect the water aliquots without changing their contents."
    p.manual(message)
    bundle = compiled(p, target(name))
    log, _ = simulate(StringIO(bundle.files["protocol.py"]))
    assert any(
        message in entry["payload"]["text"] and entry["payload"]["text"].startswith("Pausing")
        for entry in log
    )


@pytest.mark.integration
@requires_opentrons
@pytest.mark.parametrize(
    "name,module",
    [
        ("ot2", "thermocycler module"),
        ("ot2", "thermocycler module gen2"),
        ("flex", "thermocycler module gen2"),
    ],
)
def test_stages_with_thermal_profiles_in_official_simulator(name, module):
    t = target(name, thermal=True)
    t.thermocycler = module
    bundle = compiled(thermal_aliquots(), t)
    source = bundle.files["protocol.py"]
    log, _ = simulate(StringIO(source))
    texts = [event["payload"]["text"] for event in log]
    assert sum(text.startswith("Picking up tip") for text in texts) == 6
    assert sum(text.startswith("Dropping tip") for text in texts) == 6
    assert "block_max_volume=100" in source
    assert "block_max_volume=50" in source
    profiles = [
        event for event in log if event["payload"]["text"].startswith("Thermocycler starting")
    ]
    assert len(profiles) == 2
    assert profiles[0]["payload"]["steps"] == [
        {"temperature": 25, "hold_time_seconds": 1},
        {"temperature": 30, "hold_time_seconds": 1},
    ]
    assert profiles[1]["payload"]["steps"] == [{"temperature": 25, "hold_time_seconds": 1}]
    dispenses = [text for text in texts if text.startswith("Dispensing 25.0")]
    assert [text.split(" into ", 1)[1].split(" of ", 1)[0] for text in dispenses[-2:]] == [
        "A3",
        "A4",
    ]


@pytest.mark.integration
@requires_opentrons
@pytest.mark.parametrize("name,slot", [("ot2", "8"), ("flex", "A1")])
def test_thermal_module_reserves_its_deck_footprint(name, slot):
    t = target(name, thermal=True)
    t.labware["water"] = replace(t.labware["water"], slot=slot)
    with pytest.raises(CompileError, match="Unavailable"):
        compiled(thermal_aliquots(), t)


@pytest.mark.integration
@requires_opentrons
def test_thermal_binding_and_limits():
    with pytest.raises(CompileError, match="must be on the thermocycler"):
        compiled(thermal_aliquots(), target("ot2"))
    t = target("flex", thermal=True)
    t.thermocycler = "thermocycler module"
    with pytest.raises(CompileError, match="GEN2"):
        compiled(thermal_aliquots(), t)
    t = target("ot2", thermal=True)
    t.labware["aliquots"] = replace(t.labware["aliquots"], load_name="nest_96_wellplate_200ul_flat")
    with pytest.raises(CompileError, match="PCR plate"):
        compiled(thermal_aliquots(), t)
    p = Protocol("Out of range")
    p.container("water", capacity=300 * uL)
    plate = p.plate("aliquots", capacity=200 * uL)
    p.load(plate["A1"], "water", volume=50 * uL)
    p.thermocycle(plate, [(celsius(120), 1 * seconds)])
    with pytest.raises(CompileError, match="block range"):
        compiled(p, target("ot2", thermal=True))


@pytest.mark.integration
@requires_opentrons
def test_opentrons_deactivates_after_thermal_failure(monkeypatch):
    events = []
    original_deactivate = ThermocyclerContext.deactivate

    def fail_profile(self, *args, **kwargs):
        events.append("profile")
        raise RuntimeError("Synthetic thermal failure")

    def deactivate(self):
        events.append("deactivate")
        return original_deactivate(self)

    monkeypatch.setattr(ThermocyclerContext, "execute_profile", fail_profile)
    monkeypatch.setattr(ThermocyclerContext, "deactivate", deactivate)
    bundle = compiled(thermal_aliquots(), target("ot2", thermal=True))
    with pytest.raises(Exception, match="Synthetic thermal failure"):
        simulate(StringIO(bundle.files["protocol.py"]))
    assert events == ["profile", "deactivate"]


@pytest.mark.integration
@requires_star
async def test_star_external_thermal_handoff_is_awaited_and_required():
    events = []

    class Recorder(SerializingBackend):
        async def send_command(self, command, data=None):
            events.append(command)

    async def thermocycle(plate, *, profile, cycles, lid_temperature):
        events.append("thermal")
        amounts = [plate.get_item(well).tracker.get_used_volume() for well in ("A1", "A2")]
        thermal_calls.append((amounts, profile, cycles, lid_temperature))

    bundle = compiled(thermal_aliquots(), target("star", thermal=True))
    namespace = {"__name__": "_generated"}
    exec(builtins.compile(bundle.files["protocol.py"], "stages_star.py", "exec"), namespace)
    backend = Recorder(num_channels=8)
    with pytest.raises(ValueError, match="async thermocycle"):
        await namespace["run"](backend)
    with pytest.raises(ValueError, match="async thermocycle"):
        await namespace["run"](backend, thermocycle=lambda *args, **kwargs: None)
    assert not events
    thermal_calls = []
    set_volume_tracking(True)
    try:
        handler = await namespace["run"](backend, thermocycle=thermocycle)
    finally:
        set_volume_tracking(False)
    assert thermal_calls == [
        ([100, 0], [(25, 1), (30, 1)], 2, 40),
        ([50, 50], [(25, 1)], 1, None),
    ]
    thermal_positions = [i for i, event in enumerate(events) if event == "thermal"]
    assert all(events[i - 1] == "drop_tips" for i in thermal_positions)
    assert all(events[i + 1] == "pick_up_tips" for i in thermal_positions)
    plate = handler.deck.get_resource("aliquot_plate")
    assert [plate.get_item(f"A{i}").tracker.get_used_volume() for i in range(1, 5)] == [
        50,
        0,
        25,
        25,
    ]


@pytest.mark.integration
@requires_star
async def test_star_thermal_failure_stops_before_later_transfers():
    events = []

    class Recorder(SerializingBackend):
        async def send_command(self, command, data=None):
            events.append(command)

    async def fail_thermal(plate, **kwargs):
        raise RuntimeError("Synthetic thermal failure")

    bundle = compiled(thermal_aliquots(), target("star", thermal=True))
    namespace = {"__name__": "_generated"}
    exec(builtins.compile(bundle.files["protocol.py"], "stages_star.py", "exec"), namespace)
    with pytest.raises(RuntimeError, match="Synthetic thermal failure"):
        await namespace["run"](Recorder(num_channels=8), thermocycle=fail_thermal)
    assert events.count("pick_up_tips") == 3
    assert events[-1] == "stop"
