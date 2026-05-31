from qe_pro import QEProSpectrometer
from newport_2936r import SerialTransport, Newport2936R

with QEProSpectrometer() as spec:
    s = spec.acquire_spectrum(integration_time_ms=100, averages=5)
    QEProSpectrometer.save_csv("data/run001.csv", s)

with Newport2936R(SerialTransport("COM5")) as pm:
    pm.set_run(True)
    pm.set_units(2)
    print(pm.identify())
    print(pm.read_power(channel=1))
