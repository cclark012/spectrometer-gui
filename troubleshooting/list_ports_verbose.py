from serial.tools import list_ports

ports = list(list_ports.comports())

if not ports:
    print("No serial ports found.")
else:
    for p in ports:
        print("-" * 60)
        print("device:", p.device)
        print("description:", p.description)
        print("hwid:", p.hwid)
        print("manufacturer:", p.manufacturer)
        print("product:", p.product)
        print("interface:", p.interface)
        print("serial_number:", p.serial_number)
        print("vid:", p.vid)
        print("pid:", p.pid)
        print("location:", p.location)
