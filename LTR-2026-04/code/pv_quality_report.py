#!/usr/bin/env python3
"""
pv_quality_report.py — Fase 0.5 quality analysis for Liscere cross-protocol work.

Reads the CSV produced by pv_read_probe.py and answers the three exequibility unknowns
that gate the whole cross-protocol LTR:

  (1) Sustainable polling RATE   -> effective Hz, and its stability over the run.
  (2) Signal RESOLUTION          -> distinct values, smallest non-zero step; is the Real
                                    coming through with enough precision for a slope to carry
                                    signal, or is it quantised/truncated?
  (3) Temporal JITTER            -> distribution of inter-read intervals; how regular is the
                                    sampling? (Modbus was regular; Web API adds TLS+HTTP overhead.)

It then runs the SAME windowed least-squares slope that LTR-2026-03 uses for phase inference,
to check the slope is clean enough to distinguish rising / falling / flat on this channel. This
is the real go/no-go: if the slope is dominated by jitter/quantisation noise rather than by the
underlying ramp, phase inference will not transfer, and that itself is a finding.

stdlib only. Usage:
  python pv_quality_report.py pv_read.csv
  python pv_quality_report.py pv_read.csv --window 8 --slope-thresh 0.25
"""

import argparse
import csv
import math
import statistics
import sys


def load(path):
    t_perf, values, rtt = [], [], []
    n_err = 0
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if row["value"] == "ERR":
                n_err += 1
                continue
            try:
                t_perf.append(float(row["t_perf"]))
                values.append(float(row["value"]))
                rtt.append(float(row["rtt_ms"]) if row["rtt_ms"] else float("nan"))
            except (ValueError, KeyError):
                n_err += 1
    return t_perf, values, rtt, n_err


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def lsq_slope(xs, ys):
    """Least-squares slope of ys vs xs (same math as LTR-2026-03 phase inference)."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--window", type=int, default=8,
                    help="samples per slope window (LTR-2026-03 used 8)")
    ap.add_argument("--slope-thresh", type=float, default=0.25,
                    help="LTR-2026-03 threshold in units/sample; here interpreted per-sample")
    args = ap.parse_args()

    t_perf, values, rtt, n_err = load(args.csv)
    n = len(values)
    if n < 2:
        print("Not enough successful reads to analyse.", file=sys.stderr)
        sys.exit(1)

    duration = t_perf[-1] - t_perf[0]
    eff_hz = (n - 1) / duration if duration > 0 else float("nan")

    # --- (3) jitter: inter-read intervals ---
    intervals = [t_perf[i + 1] - t_perf[i] for i in range(n - 1)]
    iv_sorted = sorted(intervals)
    iv_mean = statistics.mean(intervals)
    iv_med = statistics.median(intervals)
    iv_std = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
    iv_p05 = pct(iv_sorted, 0.05)
    iv_p95 = pct(iv_sorted, 0.95)
    jitter_ratio = (iv_std / iv_mean) if iv_mean else float("nan")

    # --- (2) resolution: distinct values and smallest non-zero step ---
    distinct = sorted(set(values))
    n_distinct = len(distinct)
    steps = [abs(values[i + 1] - values[i]) for i in range(n - 1)]
    nonzero_steps = [s for s in steps if s > 0]
    min_step = min(nonzero_steps) if nonzero_steps else 0.0
    med_step = statistics.median(nonzero_steps) if nonzero_steps else 0.0
    val_min, val_max = min(values), max(values)
    val_range = val_max - val_min
    # crude decimal-precision probe: how many decimals ever appear
    max_decimals = 0
    for v in distinct[:2000]:
        s = f"{v:.10f}".rstrip("0")
        if "." in s:
            max_decimals = max(max_decimals, len(s.split(".")[1]))

    # rtt
    rtt_clean = [x for x in rtt if not math.isnan(x)]
    rtt_med = statistics.median(rtt_clean) if rtt_clean else float("nan")
    rtt_p95 = pct(sorted(rtt_clean), 0.95) if rtt_clean else float("nan")

    # --- slope test: windowed lsq slope over sample index (as in LTR-2026-03) ---
    # xs are sample indices 0..window-1 (per-sample slope), ys are the values in the window.
    slopes = []
    for i in range(n - args.window + 1):
        win = values[i:i + args.window]
        xs = list(range(args.window))
        slopes.append(lsq_slope(xs, win))
    # classify each window rising/falling/flat by the threshold
    rising = sum(1 for s in slopes if s > args.slope_thresh)
    falling = sum(1 for s in slopes if s < -args.slope_thresh)
    flat = sum(1 for s in slopes if abs(s) <= args.slope_thresh)
    # signal-to-noise proxy: within a monotonic ramp the slope should be near-constant;
    # we estimate noise as the stdev of slopes among windows classified 'rising'.
    rising_slopes = [s for s in slopes if s > args.slope_thresh]
    slope_noise = statistics.pstdev(rising_slopes) if len(rising_slopes) > 1 else 0.0
    slope_mean_rise = statistics.mean(rising_slopes) if rising_slopes else 0.0
    snr = (slope_mean_rise / slope_noise) if slope_noise else float("inf")

    # ---- report ----
    print("=" * 68)
    print("Fase 0.5 — PV read quality report")
    print("=" * 68)
    print(f"file                : {args.csv}")
    print(f"successful reads    : {n}   (errors: {n_err})")
    print(f"run duration        : {duration:.1f} s")
    print()
    print("(1) RATE")
    print(f"   effective rate   : {eff_hz:.2f} Hz")
    print(f"   RTT median/p95   : {rtt_med:.1f} / {rtt_p95:.1f} ms")
    print()
    print("(2) RESOLUTION")
    print(f"   value range      : {val_min:.4f} .. {val_max:.4f}  (span {val_range:.4f})")
    print(f"   distinct values  : {n_distinct}")
    print(f"   smallest step    : {min_step:.6f}   median step: {med_step:.6f}")
    print(f"   decimals seen    : up to {max_decimals}")
    print()
    print("(3) JITTER")
    print(f"   interval mean    : {iv_mean * 1000:.1f} ms   median: {iv_med * 1000:.1f} ms")
    print(f"   interval std     : {iv_std * 1000:.1f} ms   (jitter ratio std/mean = {jitter_ratio:.3f})")
    print(f"   interval p05..p95: {iv_p05 * 1000:.1f} .. {iv_p95 * 1000:.1f} ms")
    print()
    print(f"SLOPE TEST (window={args.window}, thresh=±{args.slope_thresh}/sample)")
    print(f"   windows          : {len(slopes)}")
    print(f"   rising/falling/flat: {rising} / {falling} / {flat}")
    print(f"   mean rising slope: {slope_mean_rise:.4f}/sample")
    print(f"   slope noise (std): {slope_noise:.4f}   crude SNR: {snr:.1f}")
    print()

    # ---- verdict heuristics (advisory, not gospel) ----
    print("ADVISORY VERDICT")
    verdict_ok = True
    notes = []
    if eff_hz < 1.0:
        notes.append(f"  [!] rate {eff_hz:.2f} Hz is low; phase inference needs enough samples "
                     f"per phase. Below ~1 Hz, slow phases may still work but transitions get coarse.")
        # not an automatic fail; depends on process timescale
    else:
        notes.append(f"  [ok] rate {eff_hz:.2f} Hz is workable for a slow tank process.")

    if jitter_ratio > 0.5:
        verdict_ok = False
        notes.append(f"  [!] jitter ratio {jitter_ratio:.2f} is high; per-sample slope may be "
                     f"unreliable. Recalibrate inference to per-SECOND slope using t_perf, "
                     f"not per-sample.")
    else:
        notes.append(f"  [ok] jitter ratio {jitter_ratio:.2f} is acceptable.")

    if n_distinct < 20 or max_decimals == 0:
        verdict_ok = False
        notes.append(f"  [!] resolution looks coarse ({n_distinct} distinct, {max_decimals} decimals). "
                     f"If the Real is being truncated to int by the API, the slope loses signal. "
                     f"Check the tag is Real and read mode preserves decimals.")
    else:
        notes.append(f"  [ok] resolution adequate ({n_distinct} distinct, up to {max_decimals} decimals).")

    if snr != float("inf") and snr < 3.0:
        verdict_ok = False
        notes.append(f"  [!] slope SNR {snr:.1f} is low; the ramp slope is not clean relative to "
                     f"noise. Phase inference would struggle. Investigate before porting the tank.")
    else:
        notes.append(f"  [ok] slope SNR {snr:.1f} — the ramp direction is cleanly recoverable.")

    for ln in notes:
        print(ln)
    print()
    print(f"  => Fase 0.5 {'LOOKS VIABLE' if verdict_ok else 'HAS A RED FLAG — discuss before porting'}")
    print("=" * 68)


if __name__ == "__main__":
    main()
