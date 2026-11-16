# Role 2 models: the predictors that shortlist designs

Both models learn from Role 1's CalculiX runs (`runs.csv`, the contract's Data to Model hand-off) and answer `predict(spec) -> Prediction`, the same interface as the fake. PyDiscovery uses them through `App(predictor=...)`.

| Model | File | How it works | Needs |
| --- | --- | --- | --- |
| `power_law_v0` (default) | `src/models/power_law.py` | log(stress) and log(deflection) are straight lines in the log of the five dimensions; coefficients are built in | nothing extra |
| `gp_powerlaw_v1` | `src/models/gp.py` | the power law as the mean, plus a Gaussian process on what it misses; retrains from `runs.csv` at start-up (about 1 s) | scikit-learn and `runs.csv` |

## Accuracy on Role 1's 200 runs (5-fold cross-validation)

| | Stress median | Stress p95 | Deflection median | Deflection p95 | 90% band coverage |
| --- | --- | --- | --- | --- | --- |
| Power law | 1.02% | 3.46% | 0.81% | 2.70% | fixed width |
| GP | 0.78% | 3.16% | 0.56% | 1.34% | 89% stress, 91% deflection |

Reproduce with `python -m src.models.gp`. Mass needs no model: the closed-form volume matches Role 1's meshes to 1e-15.

## Getting the training data

Until Role 1's branch is merged, copy the runs file from it. The folder is gitignored, so it won't be committed:

```
git fetch origin
python -c "import subprocess, pathlib; p = pathlib.Path('data/processed/formal_v0/runs.csv'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(subprocess.run(['git', 'show', 'origin/Jin(role--data):data/processed/formal_v0/runs.csv'], check=True, capture_output=True).stdout)"
pip install -r src/models/requirements.txt
```

## Model card essentials

- **Intended use:** shortlisting L-bracket v0 designs (Role 1's family) for a solver to verify. Not a replacement for that check.
- **Training data:** 200 CalculiX runs at 500 N along -Y with E = 200,000 MPa. Other loads and stiffnesses are scaled exactly (stress with force, deflection with force over E); the material's yield and density come from the part.
- **Where it is weaker:** outside the sampled ranges (`in_training_range` flags these), and on peak stress in general, because the labels come from Role 1's production mesh. A 30% finer mesh read peak stress 2 to 7% higher on three rows.
- **Not covered yet:** AutoGluon comparison (course standard), learning curves and a held-out benchmark set.
