from pymodbus.client import ModbusTcpClient
import time
import threading
from datetime import datetime

PLC_IP = "192.168.1.12"
PORT = 502
POLL_INTERVAL = 1.0

ADDR_PUMP_FLOW_SP = 0
ADDR_VALVE_FLOW_SP = 1
ADDR_LEVEL_AI = 5

client = ModbusTcpClient(PLC_IP, port=PORT)
if not client.connect():
    print("FAILED to connect to PLC at", PLC_IP)
    raise SystemExit

# Shared flag for requesting a test write from the keyboard thread
write_request = {"pending": False, "value": 77}
lock = threading.Lock()


def keyboard_listener():
    print("\nCommands: type a number + ENTER to write PUMP_FLOW_SP, or just ENTER for default 77.\n")
    while True:
        line = input()
        val = 77
        if line.strip().isdigit():
            val = int(line.strip())
        with lock:
            write_request["pending"] = True
            write_request["value"] = val


threading.Thread(target=keyboard_listener, daemon=True).start()

print(f"Supervisor polling {PLC_IP} every {POLL_INTERVAL}s on a single connection.")
try:
    while True:
        # Normal polling read
        rr = client.read_holding_registers(address=0, count=16)
        if not rr.isError():
            pump = rr.registers[ADDR_PUMP_FLOW_SP]
            valve = rr.registers[ADDR_VALVE_FLOW_SP]
            level = rr.registers[ADDR_LEVEL_AI]
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] LEVEL={level:3d}  PUMP_SP={pump:3d}  VALVE_SP={valve:3d}")

        # If a test write was requested, send it on the same connection
        with lock:
            do_write = write_request["pending"]
            wval = write_request["value"]
            write_request["pending"] = False
        if do_write:
            client.write_register(ADDR_PUMP_FLOW_SP, wval)
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] >>> TEST WRITE sent: PUMP_FLOW_SP = {wval}")

        time.sleep(POLL_INTERVAL)
except KeyboardInterrupt:
    client.close()
    print("\nSupervisor stopped.")