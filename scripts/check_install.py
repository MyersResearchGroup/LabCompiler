"""Check a built distribution in a clean environment with Python's -I flag."""

import json
import sys
from importlib import import_module
from importlib.metadata import distribution
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

import lab
from lab.provenance import Activity, Document
from lab.targets import Manual


def main() -> None:
    # Verify public modules and subpackages are present in the installed distribution.
    import_module("lab.experiments.cloning")
    import_module("lab.part")
    import_module("lab.samples")
    import_module("lab.provenance")
    import_module("lab.inventory")

    package = distribution("lab-compiler")
    assert package.version == lab.__version__
    assert package.metadata["Name"] == "lab-compiler"
    assert set(package.metadata.get_all("Provides-Extra", [])) == {"opentrons", "star"}
    assert files("lab").joinpath("py.typed").is_file()
    assert files("lab.provenance").joinpath("resources/lab.ttl").is_file()
    assert any(str(path).endswith("licenses/LICENSE") for path in package.files or ())
    assert lab.__file__ is not None
    assert not Path(lab.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1])

    protocol = lab.Protocol("Installed package check")
    source = protocol.container(
        "source", contents="water", volume=100 * lab.uL, capacity=200 * lab.uL
    )
    destination = protocol.container("destination", capacity=200 * lab.uL)
    protocol.transfer(source, destination, volume=10 * lab.uL)
    compilation = lab.compile(protocol, Manual())
    with TemporaryDirectory() as directory:
        output = compilation.write(directory)
        plan = json.loads((output / "plan.json").read_text())
        assert plan["compiler_version"] == package.version
        assert (output / "protocol.html").stat().st_size > 0

        provenance = Document(namespace="https://example.org/install_check")
        provenance.add(Activity(identity=provenance.iri("activity")))
        snapshot = provenance.freeze()
        snapshot.write(output / "provenance.ttl")
        assert Document.read(output / "provenance.ttl").freeze().digest == snapshot.digest
        assert not snapshot.to_sbol3().validate().errors

    assert not any(name.split(".")[0] in {"opentrons", "pylabrobot"} for name in sys.modules)
    print(f"lab-compiler {package.version}: installed package check passed")


if __name__ == "__main__":
    main()
