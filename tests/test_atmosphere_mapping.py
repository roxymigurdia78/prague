import unittest

from streetlab.atmosphere_mapping import initial_atmosphere


REFERENCE = {
    'schema': 'streetlab/atmosphere-reference/v1',
    'dataset': 'cams-global-reanalysis-eac4',
    'selected_sample': {'aod550': 0.12, 'total_column_ozone_du': 300},
}


class AtmosphereMappingTests(unittest.TestCase):
    def test_blender_column_at_sea_level(self):
        mapping = initial_atmosphere(REFERENCE, 0)
        self.assertAlmostEqual(mapping['aod550_at_aerosols_one'], 0.0186326, places=7)
        self.assertAlmostEqual(mapping['initial']['aerosols'], 0.12 / 0.0186326, places=4)
        self.assertAlmostEqual(mapping['initial']['ozone'], 300 / 334.5)

    def test_altitude_reduces_reference_aerosol_column(self):
        low = initial_atmosphere(REFERENCE, 100)
        high = initial_atmosphere(REFERENCE, 900)
        self.assertLess(high['aod550_at_aerosols_one'], low['aod550_at_aerosols_one'])
        self.assertGreater(high['initial']['aerosols'], low['initial']['aerosols'])
        self.assertEqual(high['initial']['ozone'], low['initial']['ozone'])

    def test_missing_or_invalid_sample_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'no selected sample'):
            initial_atmosphere({**REFERENCE, 'selected_sample': None}, 100)
        for value in (float('nan'), -1, True, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'aod550'):
                sample = {**REFERENCE['selected_sample'], 'aod550': value}
                initial_atmosphere({**REFERENCE, 'selected_sample': sample}, 100)
