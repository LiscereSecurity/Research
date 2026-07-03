#!/usr/bin/env python3
"""
s71200_api_probe.py
-------------------
Controllable JSON-RPC client for the Siemens S7-1200 G2 Web Server API.
Reproduces the exact calls the shared BasicWeb dashboard makes, but from a
script instead of a browser, and records everything as inspectable evidence.

Role in the Liscere test plan:
  T1 (channel characterisation): drives a clean login/read/write session and
      writes a structured transcript of every JSON-RPC request/response.
  T2 (TLS blindness): when --keylog is set, exports the TLS session secrets to
      a keylog file. Capture the mirrored traffic with tshark in parallel, then
      load the keylog in Wireshark ("(Pre)-Master-Secret log filename") to
      decrypt the pcap. Comparing the pcap with and without the keylog is the
      T2 result.
  T3 (real surface): proves the API is fully driveable without the browser -
      the sanctioned dashboard and this script are indistinguishable at /api/jsonrpc.

Evidence hygiene:
  - The password is REDACTED in the saved transcript (never written to disk).
  - No values are hardcoded: host and credentials come from CLI args / env.

Security note:
  The PLC serves a self-signed certificate, so certificate verification is
  disabled deliberately for lab use. That choice is itself part of what the
  certificate-trust analysis examines; it is NOT a recommendation.

Requires: Python 3.8+ (stdlib only).

Example:
  export PLC_USER=Everybody PLC_PASS=...        # or pass --user/--password
  python3 s71200_api_probe.py --host 192.168.0.12 \
      --keylog evidence/tls_keys.log \
      --transcript evidence/session_transcript.jsonl
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone

DB = '"DW_WEB"'  # DB name is quoted inside the symbolic var string, as the app does

READ_VARS = [f"{DB}.IB{i}" for i in range(8)]
OUT_VARS = [f"{DB}.QB{i}" for i in range(6)]
INT_VAR = f"{DB}.TagInt"


def _now():
    return datetime.now(timezone.utc).isoformat()


class Probe:
    def __init__(self, host, transcript_path, keylog_path=None):
        self.url = f"https://{host}/api/jsonrpc"
        self.token = None
        self.rid = 0
        self.transcript_path = transcript_path
        os.makedirs(os.path.dirname(transcript_path) or ".", exist_ok=True)

        # Self-signed PLC cert: verification off by design for lab use.
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        if keylog_path:
            os.makedirs(os.path.dirname(keylog_path) or ".", exist_ok=True)
            # Exports TLS secrets so Wireshark can decrypt the captured pcap.
            self.ctx.keylog_filename = keylog_path

    def _log(self, direction, payload):
        # Redact the password before anything touches the disk.
        safe = json.loads(json.dumps(payload))
        try:
            if safe.get("params", {}).get("password") is not None:
                safe["params"]["password"] = "***REDACTED***"
        except AttributeError:
            pass
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), "dir": direction, "msg": safe}) + "\n")

    def _call(self, method, params=None):
        self.rid += 1
        req = {"jsonrpc": "2.0", "method": method, "id": self.rid}
        if params is not None:
            req["params"] = params
        self._log("request", req)

        body = json.dumps(req).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Auth-Token"] = self.token

        r = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(r, context=self.ctx, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        self._log("response", parsed)
        if "error" in parsed:
            raise RuntimeError(f"API error on {method}: {parsed['error']}")
        return parsed.get("result")

    def login(self, user, password):
        result = self._call("Api.Login", {"user": user, "password": password})
        self.token = result.get("token") if isinstance(result, dict) else result
        return self.token

    def logout(self):
        return self._call("Api.Logout")

    def read(self, var):
        return self._call("PlcProgram.Read", {"var": var, "mode": "simple"})

    def write(self, var, value):
        return self._call("PlcProgram.Write", {"var": var, "mode": "simple", "value": value})


def demo_sequence(p):
    """A small, controllable T1 generator: read the inputs, toggle one output,
    nudge the analogue value, read back. Deliberately regular so the captured
    traffic shape is easy to reason about for T2."""
    print("[*] reading digital inputs...")
    for v in READ_VARS:
        print(f"    {v} = {p.read(v)}")

    print("[*] reading analogue value...")
    print(f"    {INT_VAR} = {p.read(INT_VAR)}")

    print("[*] writing Q0 = True, then reading it back...")
    p.write(OUT_VARS[0], True)
    time.sleep(0.2)
    print(f"    {OUT_VARS[0]} = {p.read(OUT_VARS[0])}")

    print("[*] writing TagInt = 12000, then reading it back...")
    p.write(INT_VAR, 12000)
    time.sleep(0.2)
    print(f"    {INT_VAR} = {p.read(INT_VAR)}")


def main():
    ap = argparse.ArgumentParser(description="S7-1200 G2 Web API probe (Liscere T1/T2/T3).")
    ap.add_argument("--host", required=True, help="PLC IP, e.g. 192.168.0.12")
    ap.add_argument("--user", default=os.environ.get("PLC_USER", ""))
    ap.add_argument("--password", default=os.environ.get("PLC_PASS", ""))
    ap.add_argument("--transcript", default="evidence/session_transcript.jsonl")
    ap.add_argument("--keylog", default=None,
                    help="Path to write TLS keylog (enables Wireshark decryption for T2).")
    ap.add_argument("--no-demo", action="store_true", help="Log in only, skip the demo sequence.")
    args = ap.parse_args()

    if not args.user or not args.password:
        print("ERROR: provide --user/--password or set PLC_USER/PLC_PASS.", file=sys.stderr)
        sys.exit(2)

    p = Probe(args.host, args.transcript, args.keylog)
    print(f"[*] endpoint: {p.url}")
    print(f"[*] transcript: {args.transcript}" + (f"  keylog: {args.keylog}" if args.keylog else ""))

    try:
        p.login(args.user, args.password)
        print(f"[+] logged in, token acquired ({'yes' if p.token else 'no'})")
        if not args.no_demo:
            demo_sequence(p)
        p.logout()
        print("[+] logged out. Session complete.")
    except Exception as e:
        print(f"[!] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
