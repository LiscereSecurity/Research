# Evaluator

Source code for the LTR-2026-03 experiment. The evaluator scripts are kept as a
lineage, one per validation increment, so each can be matched to the evidence it
produced.

## Lineage

- `liscere_evaluator_step1.py` produced
  `../evidence/decisions/liscere_decisions.jsonl` (Step 1).
- `liscere_evaluator_step2.py` produced
  `../evidence/decisions/liscere_decisions_step2.jsonl`
  (Step 2, phase-inertia model).
- `liscere_evaluator_step3.py` together with `liscere_learn.py` produced
  `../evidence/decisions/liscere_decisions_step3.jsonl` and the files in
  `../evidence/grammars/` (Step 3, learned grammar).
- `supervisor_poll.py` is the Modbus supervisory injector. It runs on the
  Windows workstation.

## Note on paths

The evidence paths (decision logs and grammars) are now relative to each
script's location in the repo, resolved via `os.path.dirname(__file__)`, so the
scripts read and write under `../evidence/` without editing. Only the
environment paths still need adjusting to your system: the tshark binary
(`TSHARK`) and the capture interface (`IFACE`). Both are marked with a comment
in each script.
