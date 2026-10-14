"""Standalone background export; rates and captured counts are explicitly distinct."""
import csv
import numpy as np
from io_utils.atomic import atomic_text_writer

def save_background(path, background):
    wavelengths = np.asarray(background.wavelengths_nm)
    rates = np.asarray(background.counts_per_s)
    if wavelengths.ndim != 1 or wavelengths.shape != rates.shape:
        raise ValueError('Background arrays must be matching one-dimensional arrays')
    with atomic_text_writer(path) as stream:
        writer = csv.writer(stream)
        writer.writerow(['# file_type', 'background'])
        writer.writerow(['# schema_version', 1])
        for key in ('timestamp_utc', 'integration_ms', 'averages', 'correct_dark', 'correct_nonlinearity', 'averaging_mode', 'configuration_signature', 'compatibility_policy'):
            writer.writerow(['# ' + key, getattr(background, key)])
        writer.writerow(['wavelength_nm', 'background_counts', 'background_counts_per_s'])
        for wavelength, rate in zip(wavelengths, rates, strict=True):
            writer.writerow([float(wavelength), float(rate) * background.integration_ms / 1000.0, float(rate)])


def load_background(path):
    """Load only the explicit background export schema; never infer a signature."""
    from pathlib import Path
    from core.records import BackgroundSpectrum
    metadata={};rows=[]
    with Path(path).open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.reader(stream)
        for row in reader:
            if not row:continue
            if row[0].startswith('#'):
                if len(row)>1:metadata[row[0].lstrip('# ').strip()]=row[1]
            elif row[0]=='wavelength_nm':
                if row!=['wavelength_nm','background_counts','background_counts_per_s']:
                    raise ValueError('Unrecognized background columns')
            else:
                if len(row)!=3:raise ValueError('Background needs three columns')
                rows.append([float(v) for v in row])
    if metadata.get('file_type')!='background' or metadata.get('schema_version')!='1':
        raise ValueError('Not a supported background export')
    data=np.asarray(rows,dtype=float)
    if data.ndim!=2 or data.shape[0]<2 or not np.all(np.isfinite(data)):
        raise ValueError('Background must have at least two finite samples')
    integration=float(metadata['integration_ms'])
    if not np.isfinite(integration) or integration<=0:raise ValueError('Invalid background exposure')
    if not np.allclose(data[:,1],data[:,2]*integration/1000,rtol=1e-8,atol=1e-8):
        raise ValueError('Background count and rate columns disagree')
    if np.any(np.diff(data[:,0])<=0):raise ValueError('Background wavelengths must increase')
    def boolean(key):
        value=metadata[key].lower()
        if value not in ('true','false'):raise ValueError('Invalid background boolean')
        return value=='true'
    return BackgroundSpectrum(metadata['timestamp_utc'],data[:,0],data[:,2],integration,int(metadata['averages']),
        boolean('correct_dark'),boolean('correct_nonlinearity'),metadata.get('averaging_mode','software'),
        metadata.get('configuration_signature',''),metadata.get('compatibility_policy','strict'))
