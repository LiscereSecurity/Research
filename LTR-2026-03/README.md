# LTR-2026-03

**Contextual Evaluation of Industrial Control Actions: Hardware Validation and
Learned Operational Grammar**

This folder holds the material behind LTR-2026-03.

## Contents

- The source Markdown for the report.
- The raw evidence from three validation increments:
  - `evidence/decisions/` decision logs.
  - `evidence/captures/` packet captures.
  - `evidence/grammars/` learned grammars.
- `evaluator/` the source code for the evaluator.
- `plc/` the tank-level routine that runs on the PLC (process model only).

## What the evidence covers

- **Test phases.** Each increment's test phase has a decision log in
  `evidence/decisions/` and a packet capture in `evidence/captures/`.
- **Learning sessions (Step 3).** Each learning session is evidenced by the grammar it
  exported, in `evidence/grammars/`. The learning sessions have no decision log or packet
  capture. The UNUSUAL verdict described in Section 6 of the report was observed in a session
  whose decision log was not retained; it is recorded in `evidence/step3_closure.md`.
- **Setup.** The hardware, addressing, port mirroring and register map are recorded in
  `evidence/manifest.txt`. The TIA Portal project, the HMI project and the switch
  configuration are not published.

## Published document

The published PDF for this report is available at liscere.com. This folder holds
the source material behind it.
