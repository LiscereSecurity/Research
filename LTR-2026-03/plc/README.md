# PLC tank-level routine

`tank_level.scl` is the tank-level integration that runs on the Siemens S7-1200 in the 100 ms
cyclic interrupt. It reads the inlet and outlet flow setpoints from the Modbus holding-register
window (`HR[0]` = `PUMP_FLOW_SP`, `HR[1]` = `VALVE_FLOW_SP`), integrates the net flow into the
level, clamps it to 0..100, and exposes it at `HR[5]` (`LEVEL_AI`). This is the register map in
`../evidence/manifest.txt`.

The routine was first published with LTR-2026-04 as `code/tank_webapi.scl`; it is copied here
unchanged so that it sits with the report whose register map it uses.

## What is not published

The routine is the process model only. The TIA Portal project (including the `MB_SERVER`
configuration, the cyclic-interrupt organisation block and the logic that sets `PumpRunning` and
`ValveOpen`), the HMI project and the switch configuration are not published. The hardware,
addressing and port mirroring are recorded in `../evidence/manifest.txt`.
