# Step 2 — Closure note (LTR-2026-03)

## What this step set out to prove

That the phase inference of Step 1 can be made robust enough for practical use,
and that the evaluator can distinguish three kinds of response rather than a
binary ALLOW/ALERT: a coherent action, an incoherent action in a KNOWN phase,
and an action during a genuinely uncertain phase (a transition). The grammar
(which artefact belongs to which phase) remains declared, not learned; learning
it is Step 3.

## What was demonstrated

The evaluator now infers four states from the observed level trajectory
(FILLING, DRAINING, STABLE, and a low-confidence TRANSITION condition), using a
least-squares slope over a sliding window, hysteresis on direction reversals,
and a confidence value derived from the strength and stability of the trend.

The same write (FC6, register 0, value 90) was evaluated across all cases:

| Time (CEST) | Inferred phase | Confidence | Decision | Basis |
|-------------|----------------|------------|----------|-------|
| 17:21:16 | FILLING | 1.00 | ALLOW | coherent with phase |
| 17:21:26 | FILLING (pause) | 0.40 | ALLOW | pause within filling held as FILLING |
| 17:21:51 | STABLE | 1.00 | ALERT | incoherent with known phase (policy) |
| 17:22:01 | DRAINING | 1.00 | ALERT | incoherent with known phase (policy) |
| 17:23:24 | TRANSITION | 0.08 | ALERT (uncertain) | phase unsettled (uncertainty) |

## Design decisions made during this step (for the paper)

### The plateau problem
An initial confidence model that scaled purely with instantaneous trend
strength incorrectly treated a stable plateau as uncertainty: confidence decayed
to near zero while the tank sat full and steady, which would have produced false
"uncertain" verdicts during the calm periods when many legitimate operations
occur. This was corrected by recognising that stability is knowledge, not doubt:
a sustained plateau is STABLE at high confidence.

### Philosophy A vs B (phase inertia)
Two models were considered for short pauses within a moving phase. Philosophy A
(conservative) raised uncertainty on any flattening. Philosophy B (phase inertia)
holds the moving phase through a short pause and only lowers confidence on a
genuine reversal (movement in the opposite direction). Philosophy B was chosen
because micro-pauses are common during legitimate operation, and treating each as
uncertainty would erode the system's usefulness through alarm fatigue. A pause
within filling is still filling.

### Informed ALERT vs uncertain ALERT
A key distinction was drawn between two reasons to alert:
- ALERT (high confidence, by policy): the action is incoherent with a KNOWN
  phase (DRAINING or STABLE). The system knows the phase and the policy.
- ALERT (uncertain, low confidence): the action occurred during a genuine phase
  transition where the phase itself is not yet settled. Surfaced for review.
This separates "I know this is wrong" from "I do not yet know", which is more
honest than a single undifferentiated alert and avoids attributing certainty the
system does not have.

## Note on capturing the transition case

The uncertain-transition window is narrow (typically 2-3 samples), because the
process reverses quickly once the valve opens. This narrowness is itself a
desirable property: the evaluator spends very little time uncertain. To evidence
the case reproducibly rather than by chance, a short programmatic burst of writes
was issued immediately after a manual phase reversal, so that at least one write
fell inside the transition window. The captured uncertain verdicts (17:23:24-26)
are the result.

## Evidence in this folder

- `run_LTR2603_step2.pcap` — full Modbus capture; packet-level proof of every
  write (FC6, reg 0, value 90), timestamps matching the decision log.
- `liscere_decisions_step2.jsonl` — decision log with phase, confidence,
  transitioning flag, and verdict for each write.
- `step2_closure.md` — this note.
- (screenshots of the evaluator output, if captured.)

## Scope of the claim (what is and is not proven)

PROVEN at Step 2:
- Phase inference is robust to noise, pauses and plateaus (smoothed slope +
  hysteresis + four states).
- The evaluator distinguishes coherent, incoherent-in-known-phase, and uncertain.
- Confidence is attached to every verdict and reflects phase stability.

NOT YET proven (deferred to Step 3):
- The phase-to-artefact grammar ("PUMP_FLOW_SP belongs to FILLING") is still
  declared by us, not learned from observation.

## Known limitations observed during this step

- Phase still starts UNKNOWN until enough samples establish a trend.
- The uncertain-transition window is narrow; capturing a write inside it required
  a programmatic burst for reproducibility.
- Tuning parameters (slope thresholds, hysteresis count, stable count) were set
  empirically for this process and would need recalibration for processes with
  different dynamics. This dependence is itself a motivation for Step 3.
- Single PLC: inferred context is not forge-resistant against a PLC-level
  adversary (unchanged from Step 1).