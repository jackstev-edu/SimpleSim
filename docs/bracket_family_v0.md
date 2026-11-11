# L-Bracket Family v0


## Scope

The v0 family is based on the `l_bracket.scdocx` model provided by Sanjay and used in Yunus's simulation branch. The repository copy and the uploaded file were verified to be identical.

This specification provides one consistent physical definition for simulation-data generation, surrogate-model training, finalist verification, and bracket visualization in the MVP interface.

## Coordinate Convention

The baseline CAD bounding box is:

- X: 0 to 80 mm
- Y: 0 to 60 mm
- Z: 0 to 40 mm

The coordinate directions are:

- +X: from the vertical leg toward the horizontal free end
- +Y: upward along the vertical leg
- +Z: across the bracket width
- -Y: downward load direction

The vertical back face at X = 0 is the fixed surface. The load is applied to the free-end face at the maximum X position.

All geometry dimensions are recorded in millimeters.

## Baseline Geometry

The verified baseline geometry is:

| Property | Baseline value |
|---|---:|
| Horizontal-leg length | 80 mm |
| Vertical-leg height | 60 mm |
| Bracket width | 40 mm |
| Plate thickness | 6 mm |
| Hole diameter | 8 mm |
| Hole center in the X-Z plane | X = 60 mm, Z = 20 mm |
| Raw CAD inside-corner radius | 0 mm |
| Analysis reference fillet radius | 4 mm |

The hole passes through the horizontal leg in the Y direction.

The original CAD model has a sharp inside corner. Yunus's analysis geometry uses a 4 mm internal fillet to avoid treating the sharp-corner stress singularity as a meaningful maximum stress. The raw CAD value and the analysis reference value are therefore recorded separately.

## Design Variables

The v0 bracket family uses the following five design variables:

- `thickness_mm`: equal thickness of both bracket legs
- `flange_length_mm`: horizontal-leg length in the X direction
- `arm_width_mm`: bracket width in the Z direction
- `hole_diameter_mm`: diameter of the through-hole
- `fillet_radius_mm`: internal corner radius used in the analysis geometry

The vertical-leg height and hole-center position remain fixed in v0.

## Boundary Conditions and Loading

The vertical back face at X = 0 is fully fixed. All translational degrees of freedom on this face are constrained.

The total force is applied to the free-end face of the horizontal leg. This face is always located at the maximum X position, so its location changes with `flange_length_mm`.

The verified baseline load is:

| Property | Baseline value |
|---|---:|
| Force magnitude | 500 N |
| Force vector | (0, -500, 0) N |
| Load direction | Downward, in the -Y direction |
| Load angle | -90 degrees from the +X direction in the X-Y plane |

For the initial v0 dataset:

- the user-supported force range is 100 to 1,000 N;
- the load direction remains fixed in the -Y direction;
- `load_angle_deg` is recorded as -90 degrees;
- gravity, bolt preload, contact, and other external loads are not included.

Changing the horizontal-leg length moves the load face, but the fixed surface remains at X = 0.

## Proposed Pilot Parameter Ranges

The following ranges are the initial Role 1 proposal for pilot data generation. They are narrower than the possible software input limits so that the first dataset remains close to the verified baseline geometry.

| Variable | Baseline | Proposed pilot range |
|---|---:|---:|
| `thickness_mm` | 6 mm | 4-10 mm |
| `flange_length_mm` | 80 mm | 75-120 mm |
| `arm_width_mm` | 40 mm | 32-60 mm |
| `hole_diameter_mm` | 8 mm | 6-12 mm |
| `fillet_radius_mm` | 4 mm | 2-8 mm |
| `load_force_n` | 500 N | 100-1,000 N |

These are working data-generation ranges, not changes to the shared contracts. Final ranges will be coordinated with the model owner before the full dataset is generated.

## Geometry Validity Rules

A requested design must satisfy all of the following conditions before meshing:

- every dimension must be finite and positive;
- every design variable must remain inside its supported range;
- the hole must remain completely inside the horizontal leg;
- the hole must not intersect the free-end face, side faces, or inside fillet;
- the fillet must not remove the hole or cause self-intersecting geometry;
- both bracket legs must use the same `thickness_mm`;
- the vertical-leg height remains fixed at 60 mm;
- the hole center remains fixed at X = 60 mm and Z = 20 mm.

Invalid geometry must be rejected with a clear error message rather than sent to the solver.

## Material Inputs

The material is defined by numerical engineering properties supplied by the user:

| Input | Symbol | Unit |
|---|---|---:|
| Young's modulus | E | MPa |
| Poisson's ratio | nu | dimensionless |
| Yield strength | Sy | MPa |
| Density | rho | kg/m^3 |

The baseline validation material is Structural Steel. Its exact numerical properties must be recorded with each generated run rather than inferred only from a material name.

The initial validation rules are:

- Young's modulus must be greater than zero;
- Poisson's ratio must be greater than 0 and less than 0.5;
- yield strength must be greater than zero;
- density must be greater than zero.

The current shared contracts contain `material_key` but do not contain all four numerical material properties. This document records the intended physical input, but the contract must be updated with the affected contract owners before these fields are added to shared files.

## Analysis Scope

The v0 solver uses a linear static structural analysis with the following assumptions:

- the material is homogeneous, isotropic, and linearly elastic;
- deformation is small;
- the fixed face is perfectly rigid;
- the applied force is treated as a total force distributed over the free-end face;
- no contact, bolt preload, plastic deformation, gravity, fatigue, or dynamic loading is modeled;
- temperature effects and manufacturing residual stresses are not included.

These assumptions make the model suitable for preliminary design comparison. The results are not a replacement for a detailed final engineering review.

## Planned Solver Pipeline

The planned Role 1 implementation is:

1. Create the parametric solid geometry.
2. Check the geometry validity rules.
3. Generate a three-dimensional finite-element mesh.
4. Apply the material, fixed surface, and free-end force.
5. Run a linear static solution.
6. Extract stress, displacement, reaction force, volume, and mass.
7. Save the successful result using the shared `Run` data structure.
8. Save failed cases and their error messages in a separate log.

The planned open-source tools are:

- CadQuery for parametric geometry;
- Gmsh for mesh generation;
- CalculiX for the finite-element solution;
- Python for validation, result extraction, and CSV generation.

This is the planned implementation for Role 1. It is not a claim that these capabilities already exist in the current repository.

## Required Solver Outputs

Each successful analysis must provide:

| Output | Unit | Description |
|---|---:|---|
| Maximum von Mises stress | MPa | Maximum reported equivalent stress |
| Maximum displacement | mm | Maximum total displacement magnitude |
| Volume | mm^3 | Net solid volume after subtracting the hole |
| Mass | kg | Volume multiplied by material density |
| Reaction force | N | Total reaction force at the fixed surface |

Factor of safety is calculated as:

**Factor of safety = yield strength / maximum von Mises stress**

Factor of safety is a derived value and does not need to be predicted independently when yield strength and maximum stress are available.

The dataset does not select one design objective. Its outputs can support different user goals, including:

- minimizing mass;
- minimizing displacement;
- reducing stress;
- meeting a minimum factor of safety;
- comparing feasible design tradeoffs.

## Run Data Record

The current shared `Run` contract expects one row per successful design with the following information:

- `schema_version`
- `design_id`
- `source`
- `mesh_rule`
- `material_key`
- `load_force_n`
- `load_angle_deg`
- `thickness_mm`
- `flange_length_mm`
- `arm_width_mm`
- `hole_diameter_mm`
- `fillet_radius_mm`
- `max_stress_mpa`
- `max_disp_mm`
- `mass_kg`

The following material fields will be proposed as a contract update:

- `youngs_modulus_mpa`
- `poissons_ratio`
- `yield_strength_mpa`
- `density_kg_m3`

Failed solver runs must not be written as successful rows. Their `design_id`, inputs, failure stage, and error message must be saved in a separate failure log.

## Pilot Data-Generation Plan

The dataset will be generated in stages:

1. Run one verified baseline design.
2. Run approximately ten one-variable-at-a-time cases.
3. Generate approximately forty Latin-hypercube pilot cases.
4. Review geometry failures, solver failures, and output trends.
5. Generate approximately 200 training cases after the pilot passes validation.
6. Add more cases only if model accuracy requires them.

The project will not begin by generating thousands of cases. The pilot data must be checked before the dataset is expanded.

## Verification Plan

### Baseline Reproduction

The baseline model must reproduce the documented geometry, support, load, and material settings.

### Reaction-Force Balance

The total reaction force should balance the applied force. The initial acceptance target is a relative imbalance of no more than 1%.

### Mesh Convergence

Selected cases will be solved using progressively refined meshes. Stress and displacement from the final two mesh levels should differ by no more than approximately 5% before the mesh rule is accepted.

### Trend Checks

The results should follow physically reasonable trends:

- increasing thickness should generally reduce stress and displacement;
- increasing force should increase stress and displacement;
- increasing Young's modulus should reduce displacement;
- increasing density should increase mass;
- changing yield strength should change factor of safety but should not change the linear-elastic stress result.

### Independent Comparison

Selected cases should be compared with:

- a simplified hand calculation;
- Yunus's Ansys or PyMechanical reference workflow when available;
- an Ansys result for the same geometry and loading when a comparable result is available.

### Stress-Singularity Handling

The raw CAD model has a sharp inside corner. A zero-radius model may be used for diagnostic comparison, but its corner maximum stress must not be treated as a converged physical stress value.

## Known Limitations

The v0 family represents one simplified L-bracket configuration.

The model does not currently represent:

- realistic bolted-joint stiffness;
- contact between the bracket and another component;
- loads applied to other faces or directions;
- nonlinear material behavior;
- large deformation;
- fatigue, vibration, impact, thermal loading, or manufacturing defects.

Predictions outside the documented parameter ranges are unsupported and should require a new solver run or expert review.

## Required Team Coordination

Before the real dataset is handed to the model owner, the team should coordinate the following changes:

1. Add the four numerical material properties to `StudySpec` and `Run`, or create an agreed material-property structure.
2. Confirm the five geometry-variable names and their final ranges.
3. Confirm that the fixed surface is the vertical back face at X = 0.
4. Confirm that the load is applied to the horizontal free-end face.
5. Replace fake-data source labels with the real solver source.
6. Update examples, UI fixtures, and tests after the contracts change.
7. Update the schema version if the shared data format changes.

Changes to `Run` require coordination between Role 1 and the model owner. Changes to `StudySpec` require coordination with its current owners. UI fixtures should be updated only after the shared definitions are agreed.