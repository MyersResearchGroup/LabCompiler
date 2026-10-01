# Cloning provenance walkthrough

Open [cloning_provenance.ipynb](cloning_provenance.ipynb) for the worked reporter-reference example, from supplied material records through planning, readable SBOL and LabOP excerpts, and robot compilation. Saved outputs are included.

From the repository root, launch it with:

```sh
uv run --extra opentrons --extra star --with jupyterlab jupyter lab examples/cloning_provenance/cloning_provenance.ipynb
```

The notebook's [presentation helper](notebook_views.py) and [input files](data/reporter_reference/README.md) belong to this example. Generated bundles go under the repository's ignored `build/cloning-notebook/` directory.
