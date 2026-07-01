#!/usr/bin/env python3
"""
Liscere evaluator - Step D1.4 (phase-aware verdict).
Passively observes mirrored Modbus traffic, infers operational phase
from the tank level trend, and evaluates control writes for coherence
with the inferred phase. Emits ALLOW/ALERT verdicts and a decision log.
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
    os.path.join(_HERE, "..", "evidence", "decisions", "liscere_decisions.jsonl"))

MODBUS_DEFAULT_PORTS = {502, 5020, 15020}
WRITE_FUNCTIONS = {5, 6, 15, 16}

REGISTER_NAMES = {
    0: "PUMP_FLOW_SP",
    1: "VALVE_FLOW_SP",
    2: "ALARM_HI_SP",
    3: "ALARM_LO_SP",
    5: "LEVEL_AI",
}
LEVEL_REGISTER = 5

# Phase->artefact coherence policy (Step 1 grammar, declared explicitly).
# PUMP_FLOW_SP legitimately belongs to the FILLING phase.
PHASE_AWARE_REGISTERS = {
    0: {"name": "PUMP_FLOW_SP", "coherent_phases": {"FILLING"}},
}

TREND_WINDOW = 5
DEADBAND = 1.0

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


class PhaseTracker:
    def __init__(self):
        self.levels = deque(maxlen=TREND_WINDOW)
        self.phase = "UNKNOWN"
        self.last_level = None

    def update(self, level):
        self.levels.append(level)
        self.last_level = level
        if len(self.levels) < 2:
            return self.phase
        delta = self.levels[-1] - self.levels[0]
        if delta > DEADBAND:
            self.phase = "FILLING"
        elif delta < -DEADBAND:
            self.phase = "DRAINING"
        return self.phase


def evaluate_write(register, phase):
    """Apply the phase-coherence policy. Returns (decision, rule, reason)."""
    spec = PHASE_AWARE_REGISTERS.get(register)
    if spec is None:
        return "ALLOW", "OBS-R000", "write to a register without a phase constraint"
    if phase == "UNKNOWN":
        return "ALERT", "OBS-R004", (
            f"write to {spec['name']} while operational phase is not yet established")
    if phase in spec["coherent_phases"]:
        return "ALLOW", "OBS-R004", (
            f"write to {spec['name']} is coherent with phase {phase}")
    return "ALERT", "OBS-R004", (
        f"write to {spec['name']} is incoherent with phase {phase}; "
        f"this artefact belongs to {sorted(spec['coherent_phases'])}")


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
    print(f"Liscere evaluator (D1.4 phase-aware verdict) on iface {IFACE}")
    print(f"Decision log: {DECISION_LOG}")
    print("Observing traffic. Writes are judged against the inferred phase. Ctrl+C to stop.\n")
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
                    phase = tracker.update(level)
                    print(f"[{t}] LEVEL={level:3d}  phase={phase}", flush=True)

            elif ev["type"] == "WRITE_REQUEST":
                reg = ev["register"]
                regname = REGISTER_NAMES.get(reg, f"addr{reg}")
                phase = tracker.phase
                decision, rule, reason = evaluate_write(reg, phase)
                marker = "ALLOW " if decision == "ALLOW" else "ALERT!"
                print(f"[{t}] === {marker} WRITE {regname}(reg{reg})={ev['value']} "
                      f"| phase={phase} | {rule} | {reason}", flush=True)
                record = {
                    "timestamp": ev["timestamp"], "time_hms": t,
                    "action": "WRITE", "function_code": ev["function_code"],
                    "register": reg, "register_name": regname, "value": ev["value"],
                    "observed_level": tracker.last_level,
                    "inferred_phase": phase,
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