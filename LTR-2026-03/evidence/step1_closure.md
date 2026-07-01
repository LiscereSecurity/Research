# Step 1 — Closure note (LTR-2026-03)

## What this step set out to prove

That a contextual evaluation layer can distinguish two identical control
actions (same protocol, same function code, same target register, same valid
value) by the operational phase in which they occur, where the phase is
inferred from observed process state rather than declared by the action's
sender. And that this distinction is structurally beyond what a process
interlock provides, since a correctly programmed PLC accepts both actions.

## What was demonstrated

On a physical testbed (Siemens S7-1200 CPU 1212C G2 running a tank process,
exposed over Modbus/TCP), a passive evaluator on a physically separate host,
receiving only mirrored traffic, reconstructed Modbus actions, inferred the
operational phase from the observed tank-level trend, and evaluated a write to
PUMP_FLOW_SP against that phase.

The same write (FC6, register 0, value 90) received opposite verdicts:

| Time (CEST) | Action | Register | Value | Observed level | Inferred phase | Decision |
|-------------|--------|----------|-------|----------------|----------------|----------|
| 16:03:20 | WRITE | PUMP_FLOW_SP (0) | 90 | 8 | FILLING | ALLOW |
| 16:03:51 | WRITE | PUMP_FLOW_SP (0) | 90 | 1 | DRAINING | ALERT |

The only variable distinguishing the two verdicts is the operational phase,
which the evaluator inferred on its own from the level trajectory. The sender
never declared the phase. The PLC accepted both writes.

## Why this is not redundant with a process interlock

A process interlock evaluates the physical instant: it can clamp a value or
block a physically unsafe condition. It has no memory of the process rhythm and
no notion of which artefact is normally touched in which operational phase.
The write tested here is in-range and protocol-valid; a correctly engineered
PLC accepts it in any phase. The illegitimacy in the DRAINING case is not a
property of the value or the instant, but of the action's incoherence with the
operational phase. Evaluating that incoherence requires observed temporal
context that an interlock structurally does not hold. This holds even for a
perfectly programmed PLC; it is not a remedy for missing interlocks.

## Evidence in this folder

- `run_LTR2603.pcap` — full Modbus capture of the session; packet-level proof
  that both writes occurred (FC6, reg 0, value 90) at the recorded timestamps.
- `liscere_decisions.jsonl` — the evaluator's decision log (the two verdicts),
  with timestamps matching the pcap to the millisecond.
- `manifest.txt` — environment, register map, policy, network topology.
- (screenshots of the evaluator output, if captured.)

## Scope of the claim (what is and is not proven)

PROVEN at Step 1:
- The phase is observed from the process (level trend), not declared.
- A write's verdict depends on the observed phase, not on the value.
- The distinction is invisible to per-action or per-instant checks (the action
  is valid, authorised, in-range), and is not made by a process interlock.
- The evaluator is strictly passive: it receives only mirrored traffic and
  never connects to the PLC.

NOT YET proven (deferred to later steps):
- The phase-to-artefact grammar ("PUMP_FLOW_SP belongs to FILLING") is declared
  by us, not learned. Autonomous learning of the grammar is Step 3.
- The phase-inference rule is a simple level-trend rule. Robustness to noise,
  plateaus and transitions is Step 2.

## Known limitations observed during this step

- Phase starts as UNKNOWN until enough level samples establish a trend.
- A write occurring exactly at a phase transition could be evaluated against an
  ambiguous phase. For this evidence run, both writes were placed well inside a
  stable phase to avoid this; the case is to be addressed in Step 2.
- Single PLC: the inferred context is not forge-resistant against an adversary
  who compromises the PLC itself. The passive, separate observation point only
  protects against an adversary on the command path who does not also
  manipulate the observed process telemetry.

## Reproducibility

Register map (measured, no off-by-one): addr0=PUMP_FLOW_SP, addr1=VALVE_FLOW_SP,
addr2=ALARM_HI_SP, addr3=ALARM_LO_SP, addr5=LEVEL_AI.
Policy: write to PUMP_FLOW_SP is coherent only during FILLING; otherwise ALERT.
Evaluator: passive tshark capture on the mirror interface; reconstruction logic
adapted from the OT-Lab runtime monitor.