"""Gusseted L-bracket family v1: spec, geometry, Sobol plan, parallel pipeline, merge and mesh study.

    python -m src.data.gusset_v1.pipeline --output data/processed/formal_v1_l_bracket_gusset_v1 --workers 3 --limit 512
    python -m src.data.gusset_v1.mesh_study --output data/generated/gusset_v1_mesh_study
    python -m src.data.gusset_v1.merge --output <merged> <shard dir> [<shard dir> ...]

v0 (src/data/formal_pipeline.py) is untouched; this package reuses its solver, mesh and QA code.
"""
