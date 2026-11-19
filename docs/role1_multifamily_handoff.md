# Role 1 → Role 2: Multi-family FEA handoff

**2026-11-18 | Status:** 1,000 audited CalculiX cases (200 original + 800 new). All 10 new families passed baseline/representative mesh checks. No unresolved production failures.

## What changed
- Expanded from one L-bracket family to 11: original 200 plus 80 cases each of double-hole, single-slot, twin-slot, rounded-window, side-notch, tapered, stepped, twin-gusset, perforated-gusset and offset-planform.
- Each new family has 60 LHS designs and 20 controlled mass-reduction variants (200 anchor–variant pairs overall). The pair table records changes in mass, stress and displacement.
- Replaced the six-input legacy training table with a separate, common 20-feature CAD descriptor: envelope, thickness, Poisson ratio, volume, surface area, centroid, inertia and support/load face geometry. The old dataset and shared application contracts are unchanged.

## How to use
Folder: `data/processed/formal_multifamily_v1/`.

- `train.csv` (736): fit; `validation.csv` (184): tune; `heldout_test.csv` (80): final test on the entire unseen T07 stepped family. Keep T07 out of training, preprocessing and tuning. Anchors and variants share a split.
- `feature_schema.json`: exact ordered feature and target columns. Predict `reference_max_stress_mpa` and `reference_max_disp_mm`. Mass is CAD volume × density × 1e-9 kg (volume in mm³, density in kg/m³); reference FOS = 250 / predicted stress in MPa. Do not learn mass as an independent target. Do not use IDs, metadata or FEA diagnostics as features; off-diagonal inertia terms may be zero or signed, so do not blindly log-transform all inputs.
- `model_dataset_1000.csv`: complete table for analysis; not an unfiltered training set. `mass_direction_pairs.csv`: paired changes; filter by split when using for training or tuning. `design_manifest.csv`: reconstruction parameters; `baseline_step/`: 10 example geometries. `numerical_diagnostics.csv`, `audit_summary.json`, `evidence/`: quality evidence.

```python
import json
from pathlib import Path
import pandas as pd

root = Path('data/processed/formal_multifamily_v1')
schema = json.loads((root / 'feature_schema.json').read_text(encoding='utf-8'))
X, Y = schema['feature_columns'], schema['label_columns']
train, val, test = [pd.read_csv(root / f'{s}.csv') for s in ('train', 'validation', 'heldout_test')]
# Fit on train[X], train[Y]; tune on val[X], val[Y]; evaluate once on test[X], test[Y].
```

For inference, use the same `src/data/multifamily_v1/features.py::extract_features(case, solid)` on a supported generated shape. `interface_gate.py::new_case(row)` reconstructs a new-family manifest row and `templates.py::FAMILIES[case.template_id].build(case)` builds it. Legacy rows use the original v0 builder and inputs. `params_json` is a generator input, not a model feature. The design manifest retains the frozen plan's `pending_fea` provenance for new rows; accepted labels are in the model table.

## Integration and limits
- Current GP, power-law and PyDiscovery paths are v0-specific; adapt the model/feature extraction and family routing before enabling multi-family fast search. Do not directly substitute this table for the old `runs.csv`. Jack's separate 2,048-case single-gusset dataset stays unchanged; if later pooled, extract the same features and audit splits/labels first.
- Reference conditions: 500 N in −Y distributed on the horizontal free-end face (X = flange length), vertical back face X = 0 fully fixed; linear-elastic isotropic steel, E = 200,000 MPa, yield = 250 MPa, density = 7,850 kg/m³, sampled Poisson ratio 0.25–0.35. Other load/material scaling needs matching assumptions. Stress above yield is not a validated elastic physical response.
- Audit: max relative reaction imbalance 6.3013e-7 (limit 0.01); minimum mesh quality 0.126465 (limit >0.1). Representative mesh tolerances: stress ≤3%, displacement ≤1%. These are numerical checks, not per-case convergence proof or proof of model accuracy. Evaluate stress/displacement, constraint satisfaction and predicted improvement directions against FEA; T07 only tests one unseen family, not arbitrary STEP files. Finalists need real solver verification and human review.

I defined the inputs, outputs, validation and mass-reduction workflow, ran the simulations locally, and used AI to help implement/debug the scripts and prepare documentation. The numerical labels came from real CalculiX runs. Full checkpoint logs are retained separately; do not delete them before backup and course review.
