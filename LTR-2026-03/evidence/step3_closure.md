# Step 3 — Closure note (LTR-2026-03)

## What this step set out to prove

That the artefact-to-phase grammar, declared by hand in Steps 1 and 2, can
instead be LEARNED by the evaluator from observation of normal operation, with
no manual configuration. And that evaluation against the learned grammar can be
graded (coherent / unusual / incoherent / uncertain) rather than binary. Scope:
the state variable (level) is still known; learning concerns the grammar only.

## What was demonstrated

### Learning (D3.1)
Observing only normal operation, the evaluator recorded, for each control
register, the operational phase in which it was written, and exported a learned
grammar to learned_grammar.json. It learned the structural association
(register -> phases), never the values. Two grammars were learned in separate
sessions:
- PUMP_FLOW_SP (reg 0): coherent phase = FILLING (learned from 10 writes)
- VALVE_FLOW_SP (reg 1): coherent phase = DRAINING (learned from 10 writes)
Neither was declared; both were discovered from observation.

### Graded evaluation (D3.2)
Using the learned grammar, each write is judged by the degree of coherence
between the current inferred phase and the learned phase distribution for that
register:
- COHERENT: phase well represented in the learned grammar (ALLOW).
- UNUSUAL: phase rarely seen for that register (surfaced for review).
- INCOHERENT: phase never seen for that register (ALERT).
- UNCERTAIN: phase itself unsettled (carried over from Step 2).
An early grammar in which PUMP_FLOW_SP was learned as FILLING (83%) and STABLE
(17%) produced UNUSUAL for a write in STABLE, capturing a legitimate-but-rare
operation (adjusting the inlet setpoint during a fill pause) that a hand-written
rule would likely have omitted.

### Anti-memorisation proof (D3.3)
With both registers learned, the evaluator was tested across phases. The two
registers behaved oppositely, each coherent only in its own learned phase:

| Write | in FILLING | in DRAINING | in STABLE |
|-------|-----------|-------------|-----------|
| PUMP_FLOW_SP (learned: FILLING)  | COHERENT   | INCOHERENT | INCOHERENT |
| VALVE_FLOW_SP (learned: DRAINING) | INCOHERENT | COHERENT   | INCOHERENT |

Because the verdicts mirror the learned grammars rather than any fixed rule,
this demonstrates that the evaluator learns each artefact's own grammar from
observation; nothing is hard-coded. A "FILLING is good" rule could not produce
VALVE_FLOW_SP -> INCOHERENT in FILLING, which is exactly what was observed.

## Why this is not anomaly detection

The learned grammar is structural, not value-based. It records WHICH PHASE each
artefact is written in, never which values are normal. A verdict of INCOHERENT
means the action falls outside the learned artefact-phase grammar, not that its
value is statistically rare. The same valid value (90) is COHERENT or INCOHERENT
depending solely on the phase and the learned grammar of the register written.

## Evidence in this folder

- `learned_grammar_step3_pump_only.json` — first learned grammar (reg 0 only).
- `learned_grammar_step3_pump_valve.json` — learned grammar with both registers.
- `learned_grammar.json` — current active grammar (both registers).
- `liscere_decisions_step3.jsonl` — graded decision log.
- `run_LTR2603_step3.pcap` — packet-level capture (if recorded).
- `step3_closure.md` — this note.

## Scope of the claim (what is and is not proven)

PROVEN at Step 3:
- The artefact-to-phase grammar is learned from observation, not declared.
- Learning is genuine and register-specific: two opposite grammars were learned
  and produce mirror-image verdicts.
- Evaluation is graded (coherent / unusual / incoherent / uncertain).
- The approach remains structural (artefact-phase), not value-based.

NOT YET proven (future work, Step 4 / further research):
- The state variable (level) is still known to the evaluator; discovering which
  registers are state variables vs setpoints, autonomously, is future work.
- Generalisation to non-cyclic processes and to a second protocol is future work.
- A learned model across MANY processes (a process database, possibly with an
  ML/AI layer) is a long-term direction; each documented process, including this
  one, is a first entry toward it.

## Known limitations observed during this step

- Learning requires clean traffic: an attack present during the learning window
  would be learned as normal. A supervised or validated baseline period is assumed.
- Learning requires coverage and volume: with few writes, a single accidental
  write can enter the grammar (seen in the first session, where one STABLE write
  reached the inclusion threshold). Larger learning periods dilute this.
- Graded thresholds (coherent/unusual boundary) are set empirically and would be
  tuned per deployment.
- Inherits the phase-inference limitations of Step 2, and the single-PLC,
  non-forge-resistant context limitation of Step 1.