# Optical control starter

Python 3.14 starter code for:

- Ocean Optics QE-Pro spectra via `seabreeze`
- Newport 2936-R power readings via SCPI-like commands over RS-232 or VISA

## Install

Create a virtual environment with Python 3.14 and install the packages:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
seabreeze_os_setup
```

## Recommended hardware path

For the Newport meter, prefer **RS-232 first**. The command set is documented and the protocol is simple.
Direct USB control is possible, but it is more driver-dependent.

## Quick commands

List candidate devices:

```bash
python acquire.py list-ports
python acquire.py list-spectrometers
```

Read power from channel A over RS-232:

```bash
python acquire.py power --serial-port COM5 --channel 1 --units-code 2
```

Read power through VISA serial resource:

```bash
python acquire.py power --visa-resource ASRL5::INSTR --channel 1 --units-code 2
```

Acquire and save a spectrum:

```bash
python acquire.py spectrum --integration-ms 100 --averages 5 --outfile data/run001.csv
```

Acquire a spectrum and bracket it with power readings:

```bash
python acquire.py both \
  --serial-port COM5 \
  --channel 1 \
  --units-code 2 \
  --integration-ms 100 \
  --averages 5 \
  --out-prefix data/run001
```

This writes:

- `data/run001.csv` with wavelength and intensity
- `data/run001.json` with metadata and pre/post power readings

## Notes

- `units-code 2` means Watts.
- For Newport over VISA USB, you may need `--write-termination ''`.
- If `seabreeze` cannot see the QE-Pro with the default backend, try `--backend pyseabreeze`.
