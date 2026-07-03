#!/usr/bin/env python3
"""
pv_read_probe.py — Fase 0.5 exequibility probe for Liscere cross-protocol work.

Purpose
-------
Continuously read ONE numeric tag (a process-variable analogue, e.g. "DW_WEB".RampVal)
from the S7-1200 G2 Web API (JSON-RPC over HTTPS), logging the exact local timestamp and
value of every successful read. The resulting CSV lets pv_quality_report.py answer the
three exequibility unknowns:
  (1) sustainable polling rate,
  (2) resolution of the numeric signal as seen through the API,
  (3) temporal jitter between reads.

This probe only READS. It never writes. It holds a single session (login once, reuse token,
logout at the end), which is the fair test of sustained polling.

Design notes
------------
- stdlib only (urllib, ssl, json, csv, time, argparse). Runs on the Windows host or the Mac.
- Self-signed cert on the PLC: we do NOT disable verification silently; we require an explicit
  --insecure flag so the operator is aware. (Lab-only convenience; never a deployment default.)
- Password never written to disk. Read from --password or env PLC_PASS.
- Optional --keylog writes TLS secrets (for later offline decryption / correlation), same as capB.
  Keep it OFF unless you also want to decrypt this capture.

Usage
-----
  set PLC_USER=User
  set PLC_PASS=<your-password>
  python pv_read_probe.py --host <plc-ip> --var "\"DW_WEB\".RampVal" \
      --duration 120 --insecure --out pv_read.csv

  # optional TLS keylog (only if you plan to decrypt this run too):
  #   --keylog pv_read_keys.log

Then run:
  python pv_quality_report.py pv_read.csv
"""

import argparse
import csv
import json
import os
import ssl
import sys
import time
import urllib.request


def make_ssl_context(insecure: bool, keylog_path: str | None) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if insecure:
        # Lab-only: the PLC presents a self-signed cert. Explicit, not silent.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if keylog_path:
        # Writes TLS secrets so the capture can be decrypted offline (V1 method).
        ctx.keylog_filename = keylog_path
    return ctx


def rpc(host: str, ctx: ssl.SSLContext, method: str, params, req_id: int, token: str | None):
    """Single JSON-RPC call. Returns parsed 'result' or raises."""
    body = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params is not None:
        body["params"] = params
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
    if token:
        headers["X-Auth-Token"] = token
    url = f"https://{host}/api/jsonrpc"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"JSON-RPC error on {method}: {payload['error']}")
    return payload.get("result")


def login(host, ctx, user, password, req_id):
    result = rpc(host, ctx, "Api.Login",
                 {"user": user, "password": password}, req_id, None)
    # Siemens returns a token; field name is commonly 'token'.
    token = result.get("token") if isinstance(result, dict) else result
    if not token:
        raise RuntimeError(f"Login returned no token: {result!r}")
    return token


def logout(host, ctx, token, req_id):
    try:
        rpc(host, ctx, "Api.Logout", None, req_id, token)
    except Exception as e:
        print(f"[warn] logout failed (non-fatal): {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Continuous PV reader for Web API exequibility test")
    ap.add_argument("--host", required=True, help="PLC IP, e.g. <plc-ip>")
    ap.add_argument("--var", default=None,
                    help='Full tag incl. quotes, e.g. "\\"DW_WEB\\".RampVal" '
                         '(fragile across shells; prefer --db/--member)')
    ap.add_argument("--db", default=None,
                    help='DB name without quotes, e.g. DW_WEB (shell-proof)')
    ap.add_argument("--member", default=None,
                    help='Member name, e.g. RampVal. Used with --db.')
    ap.add_argument("--duration", type=float, default=120.0, help="seconds to poll")
    ap.add_argument("--user", default=os.environ.get("PLC_USER"))
    ap.add_argument("--password", default=os.environ.get("PLC_PASS"))
    ap.add_argument("--out", default="pv_read.csv")
    ap.add_argument("--insecure", action="store_true",
                    help="accept self-signed PLC cert (lab only)")
    ap.add_argument("--keylog", default=None,
                    help="optional TLS keylog path (only if decrypting this run)")
    ap.add_argument("--mode", default="simple",
                    help="PlcProgram.Read mode (default: simple)")
    ap.add_argument("--max-rate", type=float, default=0.0,
                    help="optional cap in Hz (0 = as fast as possible)")
    args = ap.parse_args()

    # Build the properly-quoted symbolic address, shell-proof.
    # Web API expects: "DB_NAME".Member  (double-quotes around the DB name).
    if args.db and args.member:
        var = f'"{args.db}".{args.member}'
    elif args.var:
        var = args.var
    else:
        print("ERROR: provide either --db and --member, or --var", file=sys.stderr)
        sys.exit(2)
    print(f"[info] resolved var = {var!r}", file=sys.stderr)

    if not args.user or not args.password:
        print("ERROR: provide --user/--password or set PLC_USER/PLC_PASS", file=sys.stderr)
        sys.exit(2)

    ctx = make_ssl_context(args.insecure, args.keylog)

    # min interval if a rate cap is requested
    min_interval = (1.0 / args.max_rate) if args.max_rate > 0 else 0.0

    print(f"[info] logging in to {args.host} ...", file=sys.stderr)
    rid = 1
    token = login(args.host, ctx, args.user, args.password, rid)
    rid += 1
    print(f"[info] token acquired; polling {var!r} for {args.duration:.0f}s ...",
          file=sys.stderr)

    n_ok = 0
    n_err = 0
    t_start = time.perf_counter()
    wall_start = time.time()

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        # t_perf: monotonic seconds from start (for jitter); t_wall: unix time (for cross-ref)
        w.writerow(["t_perf", "t_wall", "value", "rtt_ms"])

        try:
            while True:
                elapsed = time.perf_counter() - t_start
                if elapsed >= args.duration:
                    break

                t0 = time.perf_counter()
                try:
                    result = rpc(args.host, ctx, "PlcProgram.Read",
                                 {"var": var, "mode": args.mode}, rid, token)
                    t1 = time.perf_counter()
                    rid += 1
                    # result is the raw value for mode 'simple'
                    value = result
                    w.writerow([f"{t1 - t_start:.6f}",
                                f"{time.time():.6f}",
                                value,
                                f"{(t1 - t0) * 1000:.3f}"])
                    n_ok += 1
                    if n_ok % 50 == 0:
                        rate = n_ok / (time.perf_counter() - t_start)
                        print(f"[info] {n_ok} reads, ~{rate:.2f} Hz, last={value}",
                              file=sys.stderr)
                except Exception as e:
                    n_err += 1
                    w.writerow([f"{time.perf_counter() - t_start:.6f}",
                                f"{time.time():.6f}", "ERR", ""])
                    if n_err <= 5 or n_err % 20 == 0:
                        print(f"[warn] read #{rid} failed: {e}", file=sys.stderr)
                    # if token expired, try one re-login
                    if "token" in str(e).lower() or "auth" in str(e).lower():
                        try:
                            token = login(args.host, ctx, args.user, args.password, rid)
                            rid += 1
                            print("[info] re-logged in after auth error", file=sys.stderr)
                        except Exception as e2:
                            print(f"[error] re-login failed: {e2}", file=sys.stderr)
                            break

                if min_interval:
                    sleep_left = min_interval - (time.perf_counter() - t0)
                    if sleep_left > 0:
                        time.sleep(sleep_left)
        finally:
            logout(args.host, ctx, token, rid)

    total_t = time.perf_counter() - t_start
    print(f"\n[done] {n_ok} ok, {n_err} err in {total_t:.1f}s "
          f"({n_ok / total_t:.2f} Hz effective). CSV -> {args.out}", file=sys.stderr)
    if args.keylog:
        print(f"[done] TLS keylog -> {args.keylog}", file=sys.stderr)


if __name__ == "__main__":
    main()
