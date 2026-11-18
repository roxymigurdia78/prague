"""Map EAC4 columns to initial values for Blender's Multiple Scattering sky."""

import math


# Blender 5.2 (fbe6228777e7), intern/sky/source/sky_multiple_scattering.cpp.
MODEL_SOURCE = ('https://github.com/blender/blender/blob/fbe6228777e7/'
                'intern/sky/source/sky_multiple_scattering.cpp')
OZONE_REFERENCE_DU = 334.5
AEROSOL_SCALE_KM = 0.73
ATMOSPHERE_TOP_KM = 100.0
BASE_DENSITY = 1.3681e20
BACKGROUND_DENSITY = 2e6
EXTINCTION_490 = 2.0942e-22 + 7.9706e-24
EXTINCTION_560 = 1.7711e-22 + 4.6168e-24
EXTINCTION_550 = EXTINCTION_490 + (550 - 490) / (560 - 490) * (EXTINCTION_560 - EXTINCTION_490)


def initial_atmosphere(reference, altitude_m):
    """Use an approximate vertical aerosol column and the renderer's ozone baseline."""
    if (not isinstance(reference, dict) or reference.get('schema') != 'streetlab/atmosphere-reference/v1'
            or reference.get('dataset') != 'cams-global-reanalysis-eac4'):
        raise ValueError('Expected a saved CAMS EAC4 atmosphere reference')
    sample = reference.get('selected_sample')
    if not isinstance(sample, dict):
        raise ValueError('CAMS EAC4 reference has no selected sample')
    aod, ozone_du = sample.get('aod550'), sample.get('total_column_ozone_du')
    for name, value in [('aod550', aod), ('total_column_ozone_du', ozone_du), ('altitude', altitude_m)]:
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f'{name} must be a finite nonnegative number')
    if altitude_m > 100000:
        raise ValueError('altitude exceeds the Blender sky range of 100000 m')

    # Cycles clamps the observer altitude to [1, 99999] m in this model.
    h = min(max(altitude_m, 1.0), 99999.0) / 1000
    thickness = ATMOSPHERE_TOP_KM - h
    column = (BASE_DENSITY * AEROSOL_SCALE_KM * math.exp(-h / AEROSOL_SCALE_KM)
              * -math.expm1(-thickness / AEROSOL_SCALE_KM) + BACKGROUND_DENSITY * thickness)
    reference_aod = EXTINCTION_550 * column
    return {
        'method': 'blender-5.2-multiple-scattering-column-ratio/v1',
        'model_source': MODEL_SOURCE,
        'dataset': reference['dataset'],
        'location': reference.get('location'),
        'render_local_datetime': reference.get('render_local_datetime'),
        'selected_reference_year': reference.get('selected_reference_year'),
        'selected_sample': sample.copy(),
        'altitude_m': altitude_m,
        'aod550_at_aerosols_one': reference_aod,
        'ozone_reference_du': OZONE_REFERENCE_DU,
        'initial': {'aerosols': aod / reference_aod, 'ozone': ozone_du / OZONE_REFERENCE_DU},
    }
