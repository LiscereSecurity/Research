#!/usr/bin/env python3
"""
Liscere - Step D3.2 (graded evaluation with LEARNED grammar).
Loads the grammar learned in D3.1 (learned_grammar.json) - not a hand-declared
rule - and judges each write by the degree of coherence between the current
inferred phase and the phase distribution learned for that register.
Verdicts: COHERENT (allow), UNUSUAL (surface for review), INCOHERENT (alert),
plus UNCERTAIN when the phase itself is unsettled (from Step 2).
Phase inference reused from Step 2.
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
# Evidence paths, relative to this script's location in the repo
_HERE = os.path.dirname(os.path.abspath(__file__))
GRAMMAR_FILE = os.path.normpath(
    os.path.join(_HERE, "..", "evidence", "grammars", "learned_grammar.json"))
DECISION_LOG = os.path.normpath(
    os.path.join(_HERE, "..", "evidence", "decisions", "liscere_decisions_step3.jsonl"))

MODBUS_DEFAULT_PORTS = {502, 5020, 15020}
WRITE_FUNCTIONS = {5, 6, 15, 16}

REGISTER_NAMES = {
    0: "PUMP_FLOW_SP", 1: "VALVE_FLOW_SP",
    2: "ALARM_HI_SP", 3: "ALARM_LO_SP", 5: "LEVEL_AI",
}
LEVEL_REGISTER = 5

# Graded thresholds on the learned phase fraction for the current register.
COHERENT_MIN = 0.30   # fraction >= this => COHERENT
# 0 < fraction < COHERENT_MIN => UNUSUAL ; fraction == 0 => INCOHERENT

# Phase confidence below which we treat the phase itself as uncertain (Step 2).
PHASE_MIN_CONFIDENCE = 0.6

# --- Phase inference tuning (same as Step 2) ---
WINDOW = 8
SLOPE_RISING = 0.25
SLOPE_FALLING = -0.25
REVERSAL_N = 3
STABLE_N = 6
CONF_FULL_SLOPE = 1.0
CONF_HOLD = 0.9

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
        return None

    def update(self, level):
        self.levels.append(level)
        self.last_level = level
        slope = _slope(list(self.levels))
        mag = min(abs(slope) / CONF_FULL_SLOPE, 1.0)
        move = self._movement(slope)
        if move is not None:
            self._flat_count = 0
            if self.phase in ("FILLING", "DRAINING") and move != self.phase:
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
                self.phase = move
                self.confidence = mag
                self.transitioning = False
                self._reversal_dir = None
                self._reversal_count = 0
        else:
            self._reversal_dir = None
            self._reversal_count = 0
            self._flat_count += 1
            if self.phase in ("FILLING", "DRAINING"):
                if self._flat_count < STABLE_N:
                    self.transitioning = False
                    self.confidence = CONF_HOLD
                else:
                    self.phase = "STABLE"
                    self.confidence = 1.0
                    self.transitioning = False
            elif self.phase == "STABLE":
                self.confidence = 1.0
                self.transitioning = False
            else:
                if self._flat_count >= STABLE_N:
                    self.phase = "STABLE"
                    self.confidence = 1.0
                    self.transitioning = False
                else:
                    self.transitioning = True
                    self.confidence = 0.4
        return self.phase, self.confidence, self.transitioning


def load_grammar():
    with open(GRAMMAR_FILE) as f:
        data = json.load(f)
    return data.get("grammar", {})


def phase_fraction(grammar, register, phase):
    """Learned fraction of writes to `register` that occurred in `phase`."""
    entry = grammar.get(str(register))
    if not entry:
        return None  # register never seen during learning
    dist = entry.get("phase_distribution", {})
    info = dist.get(phase)
    if not info:
        return 0.0   # this phase never seen for this register
    return info.get("fraction", 0.0)


def evaluate_write(grammar, register, phase, confidence, transitioning):
    regname = REGISTER_NAMES.get(register, f"addr{register}")

    # Phase itself unsettled -> uncertainty dominates (Step 2 carry-over).
    if transitioning or phase == "UNKNOWN" or confidence < PHASE_MIN_CONFIDENCE:
        return ("UNCERTAIN", "OBS-R005", None,
                f"write to {regname} while phase is unsettled "
                f"(phase={phase}, confidence={confidence:.2f}); surfaced for review")

    frac = phase_fraction(grammar, register, phase)
    if frac is None:
        # Register never observed at all during learning -> cannot vouch for it.
        return ("UNUSUAL", "OBS-R006", None,
                f"write to {regname} but this register was never observed during "
                f"learning; surfaced for review")

    if frac >= COHERENT_MIN:
        return ("COHERENT", "OBS-R007", round(frac, 3),
                f"write to {regname} in phase {phase} matches learned grammar "
                f"(learned fraction={frac:.2f})")
    if frac > 0.0:
        return ("UNUSUAL", "OBS-R008", round(frac, 3),
                f"write to {regname} in phase {phase} is rare in learned grammar "
                f"(learned fraction={frac:.2f}); surfaced for review")
    return ("INCOHERENT", "OBS-R009", 0.0,
            f"write to {regname} in phase {phase} was never observed during "
            f"learning for this register; incoherent with learned grammar")


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
        "timestamp": ts, "type": event_type, "function_code": func_code,
        "register": register, "value": value, "reg_val_list": reg_val_list,
    }


def main():
    grammar = load_grammar()
    cmd = build_cmd()
    tracker = PhaseTracker()
    print(f"Liscere evaluator (D3.2 graded, LEARNED grammar) on iface {IFACE}")
    print(f"Loaded grammar for registers: {sorted(grammar.keys())}")
    print(f"Decision log: {DECISION_LOG}")
    print("Judging writes by learned phase grammar. Ctrl+C to stop.\n")
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
                verdict, rule, frac, reason = evaluate_write(grammar, reg, phase, conf, trans)
                print(f"[{t}] === {verdict:11s} WRITE {regname}(reg{reg})={ev['value']} "
                      f"| phase={phase} conf={conf:.2f} | {rule} | {reason}", flush=True)
                record = {
                    "timestamp": ev["timestamp"], "time_hms": t,
                    "action": "WRITE", "function_code": ev["function_code"],
                    "register": reg, "register_name": regname, "value": ev["value"],
                    "observed_level": tracker.last_level,
                    "inferred_phase": phase, "phase_confidence": round(conf, 3),
                    "learned_fraction": frac,
                    "verdict": verdict, "rule": rule, "reason": reason,
                }
                logf.write(json.dumps(record) + "\n")
                logf.flush()
    except KeyboardInterrupt:
        proc.terminate()
        logf.close()
        print("\nStopped.")


if __name__ == "__main__":
    main()