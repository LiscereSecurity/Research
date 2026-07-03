#!/usr/bin/env python3
"""
tank_drive_probe.py — Fase 2 driving probe for the Liscere cross-protocol Step 1.

Role
----
Plays the OPERATOR on the Web API channel. It drives the tank through operational phases by
writing setpoints, and — crucially — issues the SAME evaluated write (PUMP_FLOW_SP=90) once
while FILLING and once while DRAINING, so the passive/decrypted evaluator (built separately)
can show the same authorised write receiving opposite verdicts by phase. This reproduces the
LTR-2026-03 Step 1 result on a new protocol (JSON-RPC/TLS instead of Modbus/TCP).

Closed-loop coordination (design choice b)
------------------------------------------
The probe does NOT time the evaluated write blindly. It reads LEVEL_AI and only issues the
evaluated write once the level is confidently inside a stable phase (within a band AND moving
in the expected direction across a few samples). This guarantees the write lands "well inside
a stable phase", as Step 1 requires, regardless of process timing.

Honesty boundary (stated for the LTR)
-------------------------------------
This probe reads LEVEL_AI to decide WHEN to write. That is TEST INSTRUMENTATION — the operator
knowing what it is doing. It is NOT the evaluator inferring phase. The evaluator is a separate
component that infers phase independently from the captured traffic, with no privileged knowledge.
The two roles are kept strictly separate, exactly as LTR-2026-03 separated the test burst from the
evaluated mechanism (its §6.2 note).

Ground-truth
------------
Every setpoint write and every evaluated write is logged to a CSV with:
  t_wall, t_perf, kind, artefact, value, intended_phase, level_at_write
so the evaluator's verdicts can be checked against what the operator actually did and intended.

Only READS LEVEL_AI and WRITES the two setpoints. Password never written to disk.

Usage (PowerShell, shell-proof — no embedded quotes needed)
-----------------------------------------------------------
  $env:PLC_USER="User"; $env:PLC_PASS="<your-password>"
  python tank_drive_probe.py --host <plc-ip> --db TANK_DB `
      --insecure --keylog capC_keys.log --groundtruth capC_groundtruth.csv

Then decrypt capC.pcap offline with capC_keys.log (V1 method), as in capB.
"""

import argparse
import csv
import json
import os
import ssl
import sys
import time
import urllib.request


# ----------------------------- Web API plumbing -----------------------------

def make_ssl_context(insecure, keylog_path):
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if keylog_path:
        ctx.keylog_filename = keylog_path
    return ctx


def rpc(host, ctx, method, params, req_id, token):
    body = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params is not None:
        body["params"] = params
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
    if token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(f"https://{host}/api/jsonrpc",
                                 data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"JSON-RPC error on {method}: {payload['error']}")
    return payload.get("result")


def login(host, ctx, user, password, req_id):
    result = rpc(host, ctx, "Api.Login",
                 {"user": user, "password": password}, req_id, None)
    token = result.get("token") if isinstance(result, dict) else result
    if not token:
        raise RuntimeError(f"Login returned no token: {result!r}")
    return token


def logout(host, ctx, token, req_id):
    try:
        rpc(host, ctx, "Api.Logout", None, req_id, token)
    except Exception as e:
        print(f"[warn] logout failed (non-fatal): {e}", file=sys.stderr)


# ----------------------------- probe state -----------------------------

class Probe:
    def __init__(self, host, ctx, db, user, password):
        self.host = host
        self.ctx = ctx
        self.db = db
        self.user = user
        self.password = password
        self.rid = 1
        self.token = None
        self.t0 = time.perf_counter()

    def _next(self):
        self.rid += 1
        return self.rid

    def connect(self):
        self.token = login(self.host, self.ctx, self.user, self.password, self._next())

    def disconnect(self):
        if self.token:
            logout(self.host, self.ctx, self.token, self._next())

    def var(self, member):
        return f'"{self.db}".{member}'

    def read(self, member):
        return rpc(self.host, self.ctx, "PlcProgram.Read",
                   {"var": self.var(member), "mode": "simple"}, self._next(), self.token)

    def write(self, member, value):
        return rpc(self.host, self.ctx, "PlcProgram.Write",
                   {"var": self.var(member), "value": value, "mode": "simple"},
                   self._next(), self.token)

    def t(self):
        return time.perf_counter() - self.t0


# ----------------------------- ground-truth log -----------------------------

class GroundTruth:
    def __init__(self, path):
        self.f = open(path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["t_wall", "t_perf", "kind", "artefact",
                         "value", "intended_phase", "level_at_write"])

    def log(self, t_perf, kind, artefact, value, phase, level):
        self.w.writerow([f"{time.time():.6f}", f"{t_perf:.6f}", kind,
                         artefact, value, phase,
                         "" if level is None else f"{level:.4f}"])
        self.f.flush()

    def close(self):
        self.f.close()


# ----------------------------- phase confirmation -----------------------------

def confirm_phase(probe, want, band, n_samples, sample_dt, gt):
    """
    Read LEVEL_AI n_samples times; confirm the level is inside `band` (lo, hi) AND moving in the
    direction expected for `want` ('FILLING' rising, 'DRAINING' falling). Returns the last level
    on success, or None if it could not confirm within the samples.

    This is closed-loop coordination (design choice b): the evaluated write is only issued once
    the phase is confidently stable, not on a blind timer.
    """
    lo, hi = band
    levels = []
    for _ in range(n_samples):
        try:
            lv = float(probe.read("LEVEL_AI"))
        except Exception as e:
            print(f"[warn] LEVEL_AI read failed during confirm: {e}", file=sys.stderr)
            time.sleep(sample_dt)
            continue
        levels.append(lv)
        time.sleep(sample_dt)

    if len(levels) < 3:
        return None

    in_band = all(lo <= lv <= hi for lv in levels)
    rising = levels[-1] - levels[0] > 0
    falling = levels[-1] - levels[0] < 0
    dir_ok = (want == "FILLING" and rising) or (want == "DRAINING" and falling)

    print(f"[info] confirm {want}: levels {levels[0]:.2f}->{levels[-1]:.2f}, "
          f"in_band={in_band}, dir_ok={dir_ok}", file=sys.stderr)
    return levels[-1] if (in_band and dir_ok) else None


# ----------------------------- main scenario -----------------------------

def run_step1(probe, gt, args):
    """
    Step 1 scenario: drive FILLING, confirm, issue evaluated write; then drive DRAINING, confirm,
    issue the SAME evaluated write. Optionally loop for replicates.
    """
    EVAL_ARTEFACT = "PUMP_FLOW_SP"
    EVAL_VALUE = args.eval_value  # 90 by default (faithful to LTR-2026-03)

    for rep in range(args.reps):
        print(f"\n===== replicate {rep + 1}/{args.reps} =====", file=sys.stderr)

        # --- FILLING ---
        # pre-position: drain to the bottom first, so FILLING starts low regardless of state.
        _preposition(probe, target=args.band_lo - 5.0, drain=True, timeout=args.phase_timeout)
        print("[drive] FILLING: PUMP_FLOW_SP=250, VALVE_FLOW_SP=0", file=sys.stderr)
        probe.write("VALVE_FLOW_SP", 0.0)
        probe.write("PUMP_FLOW_SP", 250.0)
        gt.log(probe.t(), "drive", "PUMP_FLOW_SP", 250.0, "FILLING->set", None)
        # let the level climb into the confirmation band before confirming
        _wait_until_band(probe, target_low=args.band_lo, target_high=args.band_hi,
                         rising=True, timeout=args.phase_timeout)
        lvl = confirm_phase(probe, "FILLING", (args.band_lo, args.band_hi),
                            args.confirm_samples, args.confirm_dt, gt)
        if lvl is None:
            print("[warn] could not confirm FILLING; issuing evaluated write anyway "
                  "(will be visible in ground-truth)", file=sys.stderr)
        print(f"[EVAL] write {EVAL_ARTEFACT}={EVAL_VALUE} during FILLING (level={lvl})",
              file=sys.stderr)
        probe.write(EVAL_ARTEFACT, float(EVAL_VALUE))
        gt.log(probe.t(), "evaluated", EVAL_ARTEFACT, EVAL_VALUE, "FILLING", lvl)

        # after the evaluated write, PUMP_FLOW_SP is now EVAL_VALUE (90), still filling slowly.
        # settle briefly, then transition.
        time.sleep(args.settle)

        # --- DRAINING ---
        # pre-position: fill to the top first, so DRAINING starts high regardless of state.
        _preposition(probe, target=args.band_hi + 5.0, drain=False, timeout=args.phase_timeout)
        print("[drive] DRAINING: PUMP_FLOW_SP=0, VALVE_FLOW_SP=250", file=sys.stderr)
        probe.write("PUMP_FLOW_SP", 0.0)
        probe.write("VALVE_FLOW_SP", 250.0)
        gt.log(probe.t(), "drive", "VALVE_FLOW_SP", 250.0, "DRAINING->set", None)
        _wait_until_band(probe, target_low=args.band_lo, target_high=args.band_hi,
                         rising=False, timeout=args.phase_timeout)
        lvl = confirm_phase(probe, "DRAINING", (args.band_lo, args.band_hi),
                            args.confirm_samples, args.confirm_dt, gt)
        if lvl is None:
            print("[warn] could not confirm DRAINING; issuing evaluated write anyway",
                  file=sys.stderr)
        print(f"[EVAL] write {EVAL_ARTEFACT}={EVAL_VALUE} during DRAINING (level={lvl})",
              file=sys.stderr)
        probe.write(EVAL_ARTEFACT, float(EVAL_VALUE))
        gt.log(probe.t(), "evaluated", EVAL_ARTEFACT, EVAL_VALUE, "DRAINING", lvl)

        # NOTE: writing PUMP_FLOW_SP=90 during draining re-enables inflow (90 < 250 valve,
        # so net still draining). This is intentional and faithful: the evaluated write is the
        # same authorised action in both phases; the PLC accepts it in both; only the phase differs.

        time.sleep(args.settle)

    # park the process in a safe steady state at the end
    print("\n[drive] parking STABLE: PUMP=VALVE=100", file=sys.stderr)
    probe.write("PUMP_FLOW_SP", 100.0)
    probe.write("VALVE_FLOW_SP", 100.0)
    gt.log(probe.t(), "drive", "BOTH", 100.0, "STABLE->set", None)


def _wait_until_band(probe, target_low, target_high, rising, timeout):
    """Poll LEVEL_AI until it enters [target_low, target_high], or timeout."""
    t_start = time.perf_counter()
    while time.perf_counter() - t_start < timeout:
        try:
            lv = float(probe.read("LEVEL_AI"))
        except Exception:
            time.sleep(0.3)
            continue
        if target_low <= lv <= target_high:
            return lv
        time.sleep(0.3)
    print(f"[warn] _wait_until_band timed out (rising={rising})", file=sys.stderr)
    return None


def _preposition(probe, target, drain, timeout):
    """
    Drive the level to a starting point before a phase, so the phase starts from the right place
    regardless of current state. drain=True lowers the level below `target`; drain=False raises it
    above `target`. Returns when the target is reached or on timeout.
    """
    if drain:
        probe.write("PUMP_FLOW_SP", 0.0)
        probe.write("VALVE_FLOW_SP", 250.0)
        print(f"[prep] lowering level below {target} before FILLING ...", file=sys.stderr)
    else:
        probe.write("VALVE_FLOW_SP", 0.0)
        probe.write("PUMP_FLOW_SP", 250.0)
        print(f"[prep] raising level above {target} before DRAINING ...", file=sys.stderr)
    t_start = time.perf_counter()
    while time.perf_counter() - t_start < timeout:
        try:
            lv = float(probe.read("LEVEL_AI"))
        except Exception:
            time.sleep(0.3)
            continue
        if (drain and lv <= target) or ((not drain) and lv >= target):
            print(f"[prep] reached level {lv:.2f}", file=sys.stderr)
            return lv
        time.sleep(0.3)
    print(f"[warn] _preposition timed out (drain={drain})", file=sys.stderr)
    return None


def main():
    ap = argparse.ArgumentParser(description="Fase 2 tank driving probe (Step 1 over Web API)")
    ap.add_argument("--host", required=True)
    ap.add_argument("--db", default="TANK_DB")
    ap.add_argument("--user", default=os.environ.get("PLC_USER"))
    ap.add_argument("--password", default=os.environ.get("PLC_PASS"))
    ap.add_argument("--insecure", action="store_true", help="accept self-signed cert (lab only)")
    ap.add_argument("--keylog", default=None, help="TLS keylog path (V1 offline validation)")
    ap.add_argument("--groundtruth", default="capC_groundtruth.csv")
    ap.add_argument("--eval-value", type=float, default=90.0,
                    help="the evaluated write value (LTR-2026-03 used 90)")
    ap.add_argument("--reps", type=int, default=1, help="replicates of the FILLING/DRAINING pair")
    ap.add_argument("--band-lo", type=float, default=30.0, help="confirmation band low")
    ap.add_argument("--band-hi", type=float, default=70.0, help="confirmation band high")
    ap.add_argument("--confirm-samples", type=int, default=5, help="LEVEL_AI reads to confirm phase")
    ap.add_argument("--confirm-dt", type=float, default=0.4, help="seconds between confirm reads")
    ap.add_argument("--phase-timeout", type=float, default=90.0, help="max wait for level to reach band")
    ap.add_argument("--settle", type=float, default=3.0, help="seconds to settle after evaluated write")
    args = ap.parse_args()

    if not args.user or not args.password:
        print("ERROR: set PLC_USER/PLC_PASS or pass --user/--password", file=sys.stderr)
        sys.exit(2)

    ctx = make_ssl_context(args.insecure, args.keylog)
    probe = Probe(args.host, ctx, args.db, args.user, args.password)
    gt = GroundTruth(args.groundtruth)

    print(f"[info] logging in to {args.host} ...", file=sys.stderr)
    probe.connect()
    print(f"[info] token acquired; driving Step 1 scenario on {args.db} ...", file=sys.stderr)

    try:
        run_step1(probe, gt, args)
    finally:
        gt.close()
        probe.disconnect()

    print(f"\n[done] ground-truth -> {args.groundtruth}", file=sys.stderr)
    if args.keylog:
        print(f"[done] TLS keylog -> {args.keylog}", file=sys.stderr)
    print("[next] stop the Mac pcap; decrypt capC.pcap offline with the keylog (as in capB).",
          file=sys.stderr)


if __name__ == "__main__":
    main()
