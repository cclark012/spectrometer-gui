"""Separate analysis/profile formats: acquisition spectrum schema remains v5."""
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from io_utils.atomic import atomic_text_writer
from io_utils.spectrum_io import load_spectrum_record
from processing.spectral_analysis import SpectralTrace, ResponseProfile, trace_from_record


def json_safe(value):
    if isinstance(value,dict): return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)): return [json_safe(v) for v in value]
    if isinstance(value,np.ndarray): return json_safe(value.tolist())
    if isinstance(value,(float,np.floating)):return float(value) if np.isfinite(value) else None
    if isinstance(value,np.integer):return int(value)
    if isinstance(value,np.bool_):return bool(value)
    return value


def write_json(path, value):
    with atomic_text_writer(path) as stream:
        json.dump(json_safe(value),stream,indent=2,allow_nan=False)


def _read_rows(path):
    metadata={};data=[];header=None
    with Path(path).open(newline='',encoding='utf-8-sig') as stream:
        for row in csv.reader(stream):
            if not row or not any(v.strip() for v in row):continue
            if row[0].startswith('#'):
                if len(row)>1: metadata[row[0].lstrip('# ').strip()]=row[1]
                continue
            try: values=[float(v) for v in row]
            except ValueError:
                if header is not None or data:raise ValueError('Unexpected text inside numeric data')
                header=row;continue
            if len(values)<2:raise ValueError('Expected wavelength and intensity columns')
            data.append(values)
    if len(data)<2:raise ValueError('File contains fewer than two data rows')
    return metadata,np.asarray(data,dtype=float)


def save_trace(path, trace):
    with atomic_text_writer(path) as stream:
        writer=csv.writer(stream)
        for key,value in [('file_type','analysis_spectrum'),('schema_version',1),('label',trace.label),
                          ('trace_id',trace.trace_id),('intensity_units',trace.units),
                          ('analysis_json',json.dumps(json_safe(trace.metadata),sort_keys=True,allow_nan=False))]:
            writer.writerow(['# '+key,value])
        writer.writerow(['wavelength_nm','intensity'])
        writer.writerows(zip(trace.x,trace.y,strict=True))


def load_trace(path):
    path=Path(path);meta,data=_read_rows(path)
    kind=meta.get('file_type','')
    if kind=='analysis_spectrum':
        if meta.get('schema_version')!='1':raise ValueError('Unsupported analysis spectrum schema')
        return SpectralTrace(data[:,0],data[:,1],meta.get('label',path.stem),meta['intensity_units'],
                             json.loads(meta['analysis_json']),meta['trace_id'])
    if kind=='background':
        from io_utils.background_io import load_background
        background=load_background(path)
        return SpectralTrace(background.wavelengths_nm,background.counts_per_s,'Background '+path.stem,'counts/s',
                             {'background_reference':True,'configuration_signature':background.configuration_signature,
                              'integration_ms':background.integration_ms,'timestamp_utc':background.timestamp_utc})
    if kind and kind!='spectrum':raise ValueError('Select a spectrum or background, not a response profile')
    trace=trace_from_record(load_spectrum_record(path),path.stem)
    return trace.derive(trace.y,{'type':'load','file_name':path.name,'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})


def save_response(path, profile):
    with atomic_text_writer(path) as stream:
        writer=csv.writer(stream)
        for key,value in [('file_type','response_profile'),('schema_version',1),('label',profile.label),
                          ('grating',profile.grating),('detector',profile.detector),
                          ('profile_json',json.dumps(json_safe(profile.metadata),sort_keys=True,allow_nan=False))]:
            writer.writerow(['# '+key,value])
        writer.writerow(['wavelength_nm','coefficient'])
        writer.writerows(zip(profile.x,profile.coefficients,strict=True))


def load_response(path, *, grating='', detector=''):
    path=Path(path);meta,data=_read_rows(path)
    if data.shape[1]!=2:raise ValueError('Response files must contain exactly two numeric columns')
    kind=meta.get('file_type','')
    if kind and kind!='response_profile':raise ValueError('This file is not a response profile')
    if kind and meta.get('schema_version')!='1':raise ValueError('Unsupported response profile schema')
    metadata=json.loads(meta.get('profile_json','{}'))
    if not kind:
        metadata.update({'imported_file':path.name,'imported_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                         'normalization':'as supplied; no rescaling','wavelength_units':'nm'})
    return ResponseProfile(data[:,0],data[:,1],meta.get('grating',grating),meta.get('detector',detector),
                           meta.get('label',path.stem),metadata)
