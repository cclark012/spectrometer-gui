# obis_probe.py

from __future__ import annotations

import argparse
import time

import serial
from serial.tools import list_ports


def list_serial_ports() -> None:
    for p in list_ports.comports():
        print("-" * 60)
        print("device      :", p.device)
        print("description :", p.description)
        print("hwid        :", p.hwid)
        print("manufacturer:", p.manufacturer)
        print("product     :", p.product)
        print("serial      :", p.serial_number)


class ObisSerial:
    def __init__(self, port: str, baudrate: int = 115200, timeout_s: float = 2.0):
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout_s,
            write_timeout=timeout_s,
        )

    def close(self) -> None:
        self.ser.close()

    def query(self, command: str) -> list[str]:
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        self.ser.write(command.encode("ascii") + b"\r")
        self.ser.flush()

        lines = []
        deadline = time.monotonic() + 3.0

        while time.monotonic() < deadline:
            raw = self.ser.readline()

            if not raw:
                break

            text = raw.decode("ascii", errors="replace").strip()

            if text:
                lines.append(text)

            # OBIS/Coherent devices commonly return an OK handshake.
            if text == "OK":
                break

        return lines

    def query_value(self, command: str) -> str:
        lines = self.query(command)

        useful = [
            line for line in lines
            if line and line != "OK" and not line.startswith("ERR")
        ]

        if not useful:
            return ""

        return useful[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--port", default=None)
    args = parser.parse_args()

    if args.list:
        list_serial_ports()
        return 0

    if not args.port:
        raise SystemExit("Specify --port COMx or use --list.")

    obis = ObisSerial(args.port)

    try:
        print("Controller:")
        for command in ["*IDN?", "*IDN0?"]:
            print("TX:", command)
            print("RX:", obis.query(command))

        print("\nLaser Channels:")

        for ch in range(1, 6):
            for cmd in [
                f"*IDN{ch}?",
                f"SYSTem{ch}:INFormation:WAVelength?",
                f"SYSTem{ch}:INFormation:TYPe?",
                f"SYSTem{ch}:INFormation:POWer?",
                f"SOURce{ch}:POWer:LIMit:LOW?",
                f"SOURce{ch}:POWer:LIMit:HIGH?",
                f"SOURce{ch}:POWer:LEVel:IMMediate:AMPLitude?",
                f"SOURce{ch}:AM:STATe?",
            ]:
                value = obis.query_value(cmd)

                if value:
                    print(f"ch{ch} {cmd} -> {value}")

    finally:
        obis.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
