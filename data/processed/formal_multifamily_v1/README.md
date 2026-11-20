# Role 1 dataset — 1000 designs

Use train.csv to fit; validation.csv to tune; heldout_test.csv only for final T07 evaluation. Exclude design_id from ML features. model_dataset_1000.csv is for archival/analysis, NOT training without filtering. Geometry, mass and safety factor are not extra independent targets. See feature_schema.json for the exact 20 inputs and 2 labels.

All references: 500 N in -Y, fixed back X=0, load on horizontal free-end face. E=200000 MPa, yield=250 MPa, density=7850 kg/m^3, variable Poisson ratio. Numerical QA does not establish physical design feasibility. Source manifest retains original pending_fea plan status intentionally; completed label_source is in model_dataset_1000.csv. Keep original checkpoints and logs until Handoff is complete.
