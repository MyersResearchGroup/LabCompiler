# Changelog

## 0.2.0

### Migration from 0.1.0

- Pass shared decks with `lab.compile(protocol, deck=deck, liquid_handler=...)`. A positional second argument is now reserved for a concrete target.
- `lab.compile(protocol)` defaults to a manual target and writes a bundle to `~/.lab/<protocol>/<target>/`. Use `to=None` to compile without writing, `to=path` to choose a directory, or `LAB_HOME` to change the default root. Recompilation replaces the files in that protocol/target bundle; `Compilation.write()` still refuses to replace different existing artifacts.
- Replace the removed `lab.protocols` request and material API with the cloning experiment builders and `lab.samples`. `Assembly` and `Transformation` recipes take native `sbol3.Component` objects; pySBOL3 is now a core dependency.

### Added

- Sample identities, lineage, design references, and optional implementation references are preserved in the protocol snapshot. Declared outputs produce `manifest.json` and are available through `Compilation.manifest`.
- Assembly, transformation, and plating examples carry native SBOL design identities through their planned outputs, with a separate example for computational design provenance.
- An optional LabOP integration exports prospective protocol plans using upstream LabOP. It is installed from the checkout in a separate environment and is not imported by the core package.

### Repository

- Move the source repository to `https://github.com/MyersResearchGroup/LabCompiler` and update package URLs, README links, and publishing instructions. The PyPI package remains `lab-compiler`; the Python import remains `lab`.
