#!/usr/bin/env python3
"""
Liscere - Step D3.1 (grammar learning mode).
Passively observes mirrored Modbus traffic during normal operation and learns,
on its own, which control artefacts are written in which operational phase.
It records the structural association (register -> phases observed), never the
values. On exit, it exports the learned grammar to learned_grammar.json.
Phase inference reused from Step 2 (slope + hysteresis + four states).
This mode does NOT judge; it only learns.
"""
import json
import os
import subprocess
import time
from collections import deque, defaultdict
from datetime import datetime

# --- Configuration ---
# Adjust to your capture interface
IFACE = "en6"
# Adjust to your system: path to tshark
TSHARK = "/Applications/Wireshark.app/Contents/MacOS/tshark"
# Evidence path, relative to this script's location in the repo
_HERE = os.path.dirname(os.path.abspath(__file__))
GRAMMAR_FILE = os.path.normpath(
    os.path.join(_HERE, "..", "evidence", "grammars", "learned_grammar.json"))

MODBUS_DEFAULT_PORTS = {502, 5020, 15020}
WRITE_FUNCTIONS = {5, 6, 15, 16}

REGISTER_NAMES = {
    0: "PUMP_FLOW_SP", 1: "VALVE_FLOW_SP",
    2: "ALARM_HI_SP", 3: "ALARM_LO_SP", 5: "LEVEL_AI",
}
LEVEL_REGISTER = 5

# Only learn from writes seen under a confidently-known, non-transition phase.
LEARN_MIN_CONFIDENCE = 0.6

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
        "timestamp": ts, "type": event_type,
        "register": register, "value": value, "reg_val_list": reg_val_list,
    }


def export_grammar(counts):
    """Turn observed (register, phase) counts into a learned grammar."""
    grammar = {}
    for reg, phase_counts in counts.items():
        total = sum(phase_counts.values())
        phases = {}
        for ph, c in phase_counts.items():
            phases[ph] = {"count": c, "fraction": round(c / total, 3)}
        # A phase is considered "belonging" if it accounts for a real share
        # of the writes to this register (structural, not value-based).
        belongs = sorted([ph for ph, info in phases.items()
                          if info["fraction"] >= 0.1])
        grammar[str(reg)] = {
            "register_name": REGISTER_NAMES.get(reg, f"addr{reg}"),
            "total_writes_observed": total,
            "phase_distribution": phases,
            "learned_coherent_phases": belongs,
        }
    return grammar


def main():
    cmd = build_cmd()
    tracker = PhaseTracker()
    # counts[register][phase] = number of writes seen in that phase
    counts = defaultdict(lambda: defaultdict(int))
    skipped_low_conf = 0

    print(f"Liscere LEARNING mode (D3.1) on iface {IFACE}")
    print(f"Grammar will be written to: {GRAMMAR_FILE}")
    print("Observing normal operation. Write to registers ONLY legitimately.")
    print("Learns (register -> phases). Does NOT judge. Ctrl+C to finish and export.\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
                # Learn only from confidently-known, non-transition phases.
                if trans or conf < LEARN_MIN_CONFIDENCE or phase == "UNKNOWN":
                    skipped_low_conf += 1
                    print(f"[{t}] (skipped for learning) WRITE {regname}(reg{reg}) "
                          f"phase={phase} conf={conf:.2f} trans={trans}", flush=True)
                else:
                    counts[reg][phase] += 1
                    print(f"[{t}] LEARN WRITE {regname}(reg{reg}) observed in phase {phase} "
                          f"(conf={conf:.2f})  [running counts: {dict(counts[reg])}]", flush=True)
    except KeyboardInterrupt:
        proc.terminate()
        grammar = export_grammar(counts)
        out = {
            "learned_at": datetime.now().isoformat(timespec="seconds"),
            "min_confidence_for_learning": LEARN_MIN_CONFIDENCE,
            "writes_skipped_low_confidence": skipped_low_conf,
            "grammar": grammar,
        }
        with open(GRAMMAR_FILE, "w") as f:
            json.dump(out, f, indent=2)
        print("\n--- Learning finished. Learned grammar: ---")
        for reg, info in grammar.items():
            print(f"  reg{reg} ({info['register_name']}): "
                  f"coherent phases = {info['learned_coherent_phases']} "
                  f"from {info['total_writes_observed']} writes")
        print(f"\nGrammar exported to {GRAMMAR_FILE}")


if __name__ == "__main__":
    main()