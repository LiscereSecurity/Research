# LTR-2026-01

**Context-Aware Evaluation of Industrial Control Actions: A Modbus/TCP Baseline in the OT Lab**

This folder holds the evidence behind the results reported in LTR-2026-01: the control pair and
the two negative controls run in OT Lab on 13 June 2026.

## Contents

- `evidence/scenario_summary_2026-06-13.csv`: the per-scenario summary written by the scenario
  runner, one row per scenario. Columns include the declared context (`maintenance_window`), the
  expected and actual decision, the expected and matched rule (`OBS-R000` to `OBS-R004`), the
  artefact, address and value. Appendix A of the report reproduces the production half of the
  control pair from this file.
- `evidence/runner_output_2026-06-13.txt`: the runner's console output for the same run.

## Provenance

The runner wrote these files to `studies/evidence/liscere_contextual_v0_1/` in the laboratory's
working tree. That directory was excluded from version control by the laboratory's `.gitignore`,
and the original files were not retained. The two files here are the verbatim copies recorded in
Liscere's internal results note of 13 June 2026, made at the time of the run. They are published
unchanged.

The decision entries the laboratory emits carry a `rule_id` (`OBS-R000` to `OBS-R004`): these are
identifiers of the rule that matched, shared by every evaluation that matches it, not identifiers
of individual records. The runner pairs each decision with the scenario's declared context and
expected outcome to produce the summary.

## Code and scenarios

The laboratory code, the scenario runner (`scripts/run_liscere_scenarios.py`) and the scenario pack
(`studies/liscere/scenarios/SCN-MODBUS-CTX-*.json`) are published in the archived repository
[LiscereSecurity/OT-Lab](https://github.com/LiscereSecurity/OT-Lab), commit `e634220`. That
repository preserves the laboratory tree of 23 June 2026, which contains the scenario pack and the
rule set used here (including `OBS-R004`, sensitive write permitted during an active maintenance
window). The run itself was made ten days earlier, from the laboratory's development branch at the
time.

The decision export in `OT-Lab/studies/evidence/otlab-policy-decisions-2026-06-08T18-22-31-770Z.json`
predates this run: it records the earlier baseline without a context dimension, and it is not the
evidence for the results in LTR-2026-01.

## Published document

The published PDF is available at [liscere.com/research/ltr-2026-01](https://liscere.com/research/ltr-2026-01/).
