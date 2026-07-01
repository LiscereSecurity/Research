#!/usr/bin/env python3
"""
Liscere evaluator - Step D2 (robust phase inference, four states, confidence).
Phase-inertia model (Philosophy B): a short pause WITHIN a moving phase keeps
that phase at high confidence; confidence only drops on a genuine reversal
(movement in the opposite direction). A prolonged plateau settles into STABLE.
States: FILLING, DRAINING, STABLE, plus a low-confidence TRANSITION condition.
Policy: PUMP_FLOW_SP is coherent only during FILLING (a pause within filling
still counts as FILLING). Incoherent in a KNOWN phase (DRAINING/STABLE) => ALERT
(high confidence, by policy). During a genuine reversal => ALERT_UNCERTAIN.
Reconstruction logic adapted from OT-Lab tshark_runtime.py.
"""
import json
import os
import subprocess
import time
from collections import deque
from datetime import datetime

# --- Configuration ---
# Adjust to your capture interface
IFACE = "en6"
# Adjust to your system: path to tshark
TSHARK = "/Applications/Wireshark.app/Contents/MacOS/tshark"
# Evidence path, relative to this script's location in the repo
_HERE = os.path.dirname(os.path.abspath(__file__))
DECISION_LOG = os.path.normpath(
    os.path.join(_HERE, "..", "evidence", "decisions", "liscere_decisions_step2.jsonl"))

MODBUS_DEFAULT_PORTS = {502, 5020, 15020}
WRITE_FUNCTIONS = {5, 6, 15, 16}

REGISTER_NAMES = {
    0: "PUMP_FLOW_SP", 1: "VALVE_FLOW_SP",
    2: "ALARM_HI_SP", 3: "ALARM_LO_SP", 5: "LEVEL_AI",
}
LEVEL_REGISTER = 5

PHASE_AWARE_REGISTERS = {
    0: {"name": "PUMP_FLOW_SP", "coherent_phases": {"FILLING"}},
}

# --- Phase inference tuning (Step 2, Philosophy B) ---
WINDOW = 8              # samples used for the slope regression
SLOPE_RISING = 0.25     # slope >= this => rising movement
SLOPE_FALLING = -0.25   # slope <= this => falling movement
REVERSAL_N = 3          # consecutive opposite-direction samples to confirm a reversal
STABLE_N = 6            # consecutive flat samples (no movement) to settle into STABLE
CONF_FULL_SLOPE = 1.0   # |slope| at/above which moving confidence is maximal
CONF_HOLD = 0.9         # confidence held during a short pause within a moving phase

TSHARK_FIELDS = [
    "frame.time_epoch", "frame.protocols", "ip.src", "tcp.srcport",
    "ip.dst", "tcp.dstport", "mbtcp.trans_id", "mbtcp.unit_id",
    "modbus.func_code", "modbus.reference_num", "modbus.read_reference_num",
    "modbus.word_cnt", "modbus.bit_cnt", "modbus.regval_uint16",
    "modbus.bitval", "modbus.exception_code",
]


def _to_int(value, default=None):
    if value is None:
        return default
    raw = str(value).strip()
    if raw == "":
        return default
    try:
        return int(raw, 0)
    except Exception:
        return default


def _parse_first_int(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return _to_int(raw.split(",")[0].strip())


def _parse_int_list(value):
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    out = []
    for token in raw.split(","):
        v = _to_int(token.strip())
        if v is not None:
            out.append(v)
    return out


def _event_type_for(func_code, dst_port):
    if func_code is None:
        return "UNKNOWN_REQUEST"
    base_fc = func_code & 0x7F if func_code > 127 else func_code
    if dst_port in MODBUS_DEFAULT_PORTS:
        return "WRITE_REQUEST" if base_fc in WRITE_FUNCTIONS else "READ_REQUEST"
    return "WRITE_RESPONSE" if base_fc in WRITE_FUNCTIONS else "READ_RESPONSE"


def build_cmd():
    cmd = [TSHARK, "-l", "-n", "-Q", "-i", IFACE, "-f", "tcp",
           "-T", "fields", "-E", "separator=\t", "-E", "occurrence=a", "-E", "quote=n"]
    for f in TSHARK_FIELDS:
        cmd.extend(["-e", f])
    return cmd


def _slope(samples):
    n = len(samples)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(samples) / n
    num = sum((xs[i] - mx) * (samples[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


class PhaseTracker:
    """
    Philosophy B (phase inertia):
      - moving trend confirms/holds the moving phase at high confidence;
      - a short flat pause WITHIN a moving phase keeps the phase (held confidence);
      - only an opposite-direction movement (reversal) lowers confidence,
        and after REVERSAL_N confirmations switches phase;
      - a prolonged flat run (>= STABLE_N) settles into STABLE at high confidence.
    """
    def __init__(self):
        self.levels = deque(maxlen=WINDOW)
        self.phase = "UNKNOWN"
        self.confidence = 0.0
        self.transitioning = False
        self.last_level = None
        self._reversal_dir = None
        self._reversal_count = 0
        self._flat_count = 0

    def _movement(self, slope):
        if slope >= SLOPE_RISING:
            return "FILLING"
        if slope <= SLOPE_FALLING:
            return "DRAINING"
        return None  # no clear movement (flat or near-flat)

    def update(self, level):
        self.levels.append(level)
        self.last_level = level
        slope = _slope(list(self.levels))
        mag = min(abs(slope) / CONF_FULL_SLOPE, 1.0)
        move = self._movement(slope)

        if move is not None:
            # There is movement in some direction.
            self._flat_count = 0
            if self.phase in ("FILLING", "DRAINING") and move != self.phase:
                # Movement opposite to current phase => candidate reversal.
                self.transitioning = True
                if self._reversal_dir == move:
                    self._reversal_count += 1
                else:
                    self._reversal_dir = move
                    self._reversal_count = 1
                self.confidence = mag * (self._reversal_count / REVERSAL_N) * 0.5
                if self._reversal_count >= REVERSAL_N:
                    self.phase = move
                    self.confidence = mag
                    self.transitioning = False
                    self._reversal_dir = None
                    self._reversal_count = 0
            else:
                # Movement confirms current phase, or adopt from STABLE/UNKNOWN.
                self.phase = move
                self.confidence = mag
                self.transitioning = False
                self._reversal_dir = None
                self._reversal_count = 0
        else:
            # No clear movement: a pause/plateau.
            self._reversal_dir = None
            self._reversal_count = 0
            self._flat_count += 1
            if self.phase in ("FILLING", "DRAINING"):
                if self._flat_count < STABLE_N:
                    # Short pause within a moving phase: hold the phase (inertia).
                    self.transitioning = False
                    self.confidence = CONF_HOLD
                else:
                    # Prolonged flat run: settle into STABLE.
                    self.phase = "STABLE"
                    self.confidence = 1.0
                    self.transitioning = False
            elif self.phase == "STABLE":
                self.confidence = 1.0
                self.transitioning = False
            else:
                # UNKNOWN: needs a sustained flat run to become STABLE.
                if self._flat_count >= STABLE_N:
                    self.phase = "STABLE"
                    self.confidence = 1.0
                    self.transitioning = False
                else:
                    self.transitioning = True
                    self.confidence = 0.4

        return self.phase, self.confidence, self.transitioning


def evaluate_write(register, phase, confidence, transitioning):
    spec = PHASE_AWARE_REGISTERS.get(register)
    if spec is None:
        return "ALLOW", "OBS-R000", "write to a register without a phase constraint"

    if transitioning or phase == "UNKNOWN":
        return "ALERT_UNCERTAIN", "OBS-R005", (
            f"write to {spec['name']} during a phase transition / unsettled phase "
            f"(phase={phase}, confidence={confidence:.2f}); surfaced for review")

    if phase in spec["coherent_phases"]:
        return "ALLOW", "OBS-R004", (
            f"write to {spec['name']} is coherent with phase {phase} "
            f"(confidence={confidence:.2f})")
    return "ALERT", "OBS-R004", (
        f"write to {spec['name']} is incoherent with known phase {phase} "
        f"(confidence={confidence:.2f}); this artefact belongs to "
        f"{sorted(spec['coherent_phases'])}")


def parse_line(line):
    cols = line.rstrip("\n").split("\t")
    if len(cols) < 15:
        return None
    ts = float(cols[0]) if cols[0] else time.time()
    protocols = str(cols[1] or "").lower()
    src_ip = cols[2] or None
    dst_ip = cols[4] or None
    dst_port = _to_int(cols[5])
    func_code = _parse_first_int(cols[8])
    ref_write = _parse_first_int(cols[9])
    ref_read = _parse_first_int(cols[10])
    reg_val_first = _parse_first_int(cols[13])
    reg_val_list = _parse_int_list(cols[13])
    bit_val = _parse_first_int(cols[14])

    if "modbus" not in protocols:
        return None
    if not src_ip or not dst_ip or func_code is None:
        return None

    event_type = _event_type_for(func_code, dst_port)
    register = ref_write if ref_write is not None else ref_read
    value = reg_val_first if reg_val_first is not None else bit_val
    return {
        "timestamp": ts, "src_ip": src_ip, "dst_ip": dst_ip,
        "function_code": func_code, "type": event_type,
        "register": register, "value": value, "reg_val_list": reg_val_list,
    }


def main():
    cmd = build_cmd()
    tracker = PhaseTracker()
    print(f"Liscere evaluator (D2 phase-inertia, four-state) on iface {IFACE}")
    print(f"Decision log: {DECISION_LOG}")
    print("A pause within a phase holds it; only a reversal lowers confidence. Ctrl+C to stop.\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    logf = open(DECISION_LOG, "a")
    try:
        for line in proc.stdout:
            ev = parse_line(line)
            if not ev:
                continue
            t = datetime.fromtimestamp(ev["timestamp"]).strftime("%H:%M:%S")

            if ev["type"] == "READ_RESPONSE" and ev["reg_val_list"]:
                vals = ev["reg_val_list"]
                if len(vals) > LEVEL_REGISTER:
                    level = vals[LEVEL_REGISTER]
                    phase, conf, trans = tracker.update(level)
                    tag = phase + ("*" if trans else "")
                    print(f"[{t}] LEVEL={level:3d}  phase={tag:11s} conf={conf:.2f}", flush=True)

            elif ev["type"] == "WRITE_REQUEST":
                reg = ev["register"]
                regname = REGISTER_NAMES.get(reg, f"addr{reg}")
                phase = tracker.phase
                conf = tracker.confidence
                trans = tracker.transitioning
                decision, rule, reason = evaluate_write(reg, phase, conf, trans)
                if decision == "ALLOW":
                    marker = "ALLOW            "
                elif decision == "ALERT":
                    marker = "ALERT!           "
                else:
                    marker = "ALERT (uncertain)"
                print(f"[{t}] === {marker} WRITE {regname}(reg{reg})={ev['value']} "
                      f"| phase={phase} conf={conf:.2f} trans={trans} | {rule} | {reason}", flush=True)
                record = {
                    "timestamp": ev["timestamp"], "time_hms": t,
                    "action": "WRITE", "function_code": ev["function_code"],
                    "register": reg, "register_name": regname, "value": ev["value"],
                    "observed_level": tracker.last_level,
                    "inferred_phase": phase, "confidence": round(conf, 3),
                    "transitioning": trans,
                    "decision": decision, "rule": rule, "reason": reason,
                }
                logf.write(json.dumps(record) + "\n")
                logf.flush()
    except KeyboardInterrupt:
        proc.terminate()
        logf.close()
        print("\nStopped.")


if __name__ == "__main__":
    main()