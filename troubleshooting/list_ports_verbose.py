from __future__ import annotations

def main() -> int:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is required to list serial ports.")
        return 2

    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return 1

    for port in ports:
        print("-" * 60)
        print("device:", port.device)
        print("description:", port.description)
        print("hwid:", port.hwid)
        print("manufacturer:", port.manufacturer)
        print("product:", port.product)
        print("interface:", port.interface)
        print("serial_number:", port.serial_number)
        print("vid:", port.vid)
        print("pid:", port.pid)
        print("location:", port.location)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
