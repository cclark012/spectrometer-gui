"""Optional read-only Lake Shore 335 USB virtual-COM adapter (57600, 7O1)."""
import math
import time
from core.time_utils import utc_now_iso
from planning.parameter_scan import TemperatureReading

class LakeShore335:

    def __init__(self, port, *, transport=None):
        if transport is None:
            import serial
            transport = serial.Serial(port, baudrate=57600, bytesize=serial.SEVENBITS, parity=serial.PARITY_ODD, stopbits=serial.STOPBITS_ONE, timeout=1.0, write_timeout=1.0)
        self.transport = transport
        try:
            self.identity = self._query('*IDN?')
            if 'MODEL335' not in self.identity.upper().replace(' ', ''):
                raise ValueError(f'Expected Lake Shore MODEL335, received {self.identity!r}')
        except Exception:
            self.close()
            raise

    def _query(self, command):
        if command not in {'*IDN?', 'KRDG? A', 'KRDG? B', 'RDGST? A', 'RDGST? B'}:
            raise ValueError('Unsupported read-only query')
        self.transport.write((command + '\r\n').encode('ascii'))
        response = self.transport.read_until(b'\n', 256)
        if not response.endswith(b'\n'):
            raise TimeoutError(f'Incomplete response to {command}')
        return response.decode('ascii').strip()

    def read(self):
        readings = []
        for channel in ('A', 'B'):
            sampled = time.perf_counter()
            stamp = utc_now_iso()
            value = float(self._query('KRDG? ' + channel))
            status = int(self._query('RDGST? ' + channel))
            if not math.isfinite(value) or value < 0:
                status = status or -1
            readings.append(TemperatureReading('lakeshore_' + channel, value, 'K', sampled, stamp, status, self.identity))
        return readings

    def close(self):
        self.transport.close()

class EmulatedLakeShore335:
    identity = 'EMULATED Lake Shore 335 (fixed 4.2/4.3 K)'

    def read(self):
        return [TemperatureReading('lakeshore_' + c, v, 'K', time.perf_counter(), utc_now_iso(), 0, self.identity) for c, v in [('A', 4.2), ('B', 4.3)]]

    def close(self):
        pass
