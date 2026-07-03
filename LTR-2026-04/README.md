# LTR-2026-04: Contextual Evaluation under Encryption

Evidence, code, and reproduction material for Liscere Technical Report LTR-2026-04.

This report characterises an encrypted operational-technology channel, the Web API of a
Siemens S7-1200 G2 controller (JSON-RPC over HTTPS/TLS 1.3), against the requirements of
contextual action evaluation. It establishes what an external observer can and cannot
recover from such a channel, and isolates the observation-vantage problem that strong
transport encryption introduces. The report itself is in `report/`; this repository holds
the material required to inspect and repeat the work.

## Layout

```
report/
  LTR-2026-04.md          the report
  figures/                the five figures (fig1, fig2, fig3a, fig3b, fig4)
evidence/
  captures/               packet captures (capA, capB, capC), encrypted; see note below
  data/                   derived data: record sizes, ground truth, decrypted export
  logs/                   session transcripts (redacted) and an I/O graph
code/                     the probes and the PLC control logic
```

## The captures are encrypted

The packet captures in `evidence/captures/` are TLS 1.3 traffic. Without the corresponding
session keys, they are opaque application data, which is precisely the point the report
makes: a passive observer cannot read this channel. The session keys are **not** included in
this repository, by design. Publishing them would hand a reader exactly what the report shows
a passive adversary cannot obtain, and would contradict the report's central finding.

To inspect the decrypted payloads yourself, reproduce the setup and generate your own key log.
The probes in `code/` support a `--keylog` option that writes the TLS secrets of the session
they create; loading that key log into Wireshark alongside a capture you record yourself will
decrypt it. This is an offline validation method, not a deployment capability: it requires
instrumenting the client, and it does not represent what a passive observer sees in production.

## Reproducing the work

1. **Controller.** Load the tank control logic (`code/tank_webapi.scl`) into the 100 ms cyclic
   interrupt of an S7-1200 G2, exposing `LEVEL_AI`, `PUMP_FLOW_SP`, and `VALVE_FLOW_SP` as
   Web-accessible tags in a data block. Calibrate the flows so each operational phase lasts on
   the order of tens of seconds.
2. **Observe.** Capture traffic on a passive mirror port with `tshark`, as the report describes.
3. **Characterise the channel.** Use the probes in `code/` to read the process variable and to
   drive control actions over the Web API. `pv_read_probe.py` reads a tag continuously;
   `pv_quality_report.py` analyses the resulting series for rate, resolution, jitter, and slope
   quality. `tank_drive_probe.py` drives the tank through operational phases and issues an
   evaluated write in two distinct phases. `s71200_api_probe.py` is the base JSON-RPC client.
4. **Decrypt for validation only.** Record with `--keylog` on the client and load the key log
   into Wireshark to verify payloads offline, as above.

## Credentials and secrets

The probes take credentials from `--user`/`--password` arguments or the `PLC_USER`/`PLC_PASS`
environment variables; no credentials are hardcoded. Session transcripts in `evidence/logs/`
have passwords and session tokens redacted. TLS key logs are excluded from this repository and
from version control.

## Related reports

This report follows LTR-2026-03, which validated the contextual evaluation core on Modbus/TCP
under passive observation, and LTR-2026-01 and LTR-2026-02 earlier in the series. See
[liscere.com](https://liscere.com) for the full series.
