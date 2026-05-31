from __future__ import annotations

import argparse
import sys
import time
from typing import Iterable

TERM_BYTES = {
    "CR": b"\r",
    "LF": b"\n",
    "CRLF": b"\r\n",
    "NONE": b"",
}

TERM_TEXT = {
    "CR": "\r",
    "LF": "\n",
    "CRLF": "\r\n",
    "NONE": "",
}

def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports()]

def list_visa_resources() -> list[str]:
    try:
        import pyvisa
    except ImportError:
        return []

    rm = pyvisa.ResourceManager()
    try:
        return list(rm.list_resources())
    finally:
        rm.close()

def print_port_lists() -> None:
    print("Serial Ports:")
    for port in list_serial_ports():
        print(f"  {port}")
    print("VISA Resources:")
    for resource in list_visa_resources():
        print(f"  {resource}")

def serial_raw_query(
    port: str,
    command: str,
    terminator: str,
    timeout_s: float,
    ) -> bytes:
    import serial

    with serial.Serial(
        port=port,
        baudrate=38400,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
        write_timeout=timeout_s
    ) as ser:
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        payload = command.encode("ascii") + TERM_BYTES[terminator]
        ser.write(payload)
        ser.flush()

        chunks = []
        deadline = time.monotonic() + timeout_s
        last_rx_time = None

        while time.monotonic() < deadline:
            n = ser.in_waiting
            if n:
                chunk = ser.read(n)
                chunks.append(chunk)
                last_rx_time = time.monotonic()
            else:
                time.sleep(0.02)

            if last_rx_time is not None and (time.monotonic() - last_rx_time) > 0.25:
                break

        return b"".join(chunks)

def visa_query(
    resource: str,
    command: str,
    write_terminator: str,
    read_terminator: str,
    timeout_s: float,
    ) -> str:
    import pyvisa

    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(resource)

    try:
        inst.timeout = int(timeout_s * 1000)

        try:
            inst.baud_rate = 38400
            inst.data_bits = 8
            inst.stop_bits = pyvisa.constants.StopBits.one
            inst.parity = pyvisa.constants.Parity.none
        except Exception:
            pass

        inst.write_termination = TERM_TEXT[write_terminator]
        inst.read_termination = TERM_TEXT[read_terminator]

        try:
            inst.clear()
        except Exception:
            pass

        return str(inst.query(command)).strip()

    finally:
        try:
            inst.close()
        finally:
            rm.close()

def commands_for_args(args: argparse.Namespace) -> list[str]:
    if args.full:
        return [
            "*IDN?",
            "ECHO?",
            "ADDRess?",
            "PM:RUN?",
            "PM:UNITS?",
            "PM:CHAN?",
            "PM:P?",
            "PM:PWS?",
            "ERRSTR?",
        ]
    return [args.command]

def termination_for_args(args: argparse.Namespace) -> list[str]:
    if args.try_terminators:
        return ["CR", "CRLF", "LF", "NONE"]

    return [args.terminator]

def run_serial(args: args.Namespace) -> int:
    ok = False

    for terminator in termination_for_args(args):
        for command in commands_for_args(args):
            print(f"\n[SERIAL] port={args.serial_port} term={terminator} cmd={command!r}")

            try:
                raw = serial_raw_query(
                    port=args.serial_port,
                    command=command,
                    terminator=terminator,
                    timeout_s=args.timeout_s,
                )
            except Exception as exc:
                print(f"  Error: {type(exc).__name__}: {exc}")
                continue

            print(f"  raw bytes: {raw!r}")
            
            if raw:
                try:
                    print(f"  text: {raw.decode('ascii', errors='replace')!r}")
                except Exception:
                    pass

                ok = True

            return 0 if ok else 2

def run_visa(args: argparse.Namespace) -> int:
    ok = False

    for command in commands_for_args(args):
        print(
            f"\n[VISA] resource={args.visa_resource} "
            f"write_term={args.write_terminator} "
            f"read_term={args.read_terminator} "
            f"cmd={command!r}"
        )
        try:
            response = visa_query(
                resource=args.visa_resource,
                command=command,
                write_terminator=args.write_terminator,
                read_terminator=args.read_terminator,
                timeout_s=args.timeout_s,
            )
        except Exception as exc:
            print(f"  Error: {type(exc).__name__}: {exc}")
            continue

        print(f"  Response: {response!r}")
        if response:
              ok = True
    return 0 if ok else 2

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Raw Newport 2936-R communication probe.")

    parser.add_argument("--list", action="store_true")
    parser.add_argument("--serial-port", default=None, help="Example: COM3")
    parser.add_argument("--visa-resource", default=None, help="Example: ASRL3::INSTR")
    parser.add_argument("--command", default="*IDN?")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=2.0)
    parser.add_argument("--terminator", choices=list(TERM_BYTES), default="CR", help="Serial write terminator")
    parser.add_argument("--try-terminators", action="store_true", help="Try CR, CRLF, LF, and no terminator for serial probing")
    parser.add_argument("--write-terminator", choices=list(TERM_TEXT), default="CR", help="VISA write terminator")
    parser.add_argument("--read-terminator", choices=list(TERM_TEXT), default="LF", help="VISA read terminator")

    return parser

def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        print_port_lists()
        return 0

    if bool(args.serial_port) == bool(args.visa_resource):
        parser.error("Specify exactly one of --serial-port or --visa-resource, or use --list")

    if args.serial_port:
        return run_serial(args)

    return run_visa(args)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
