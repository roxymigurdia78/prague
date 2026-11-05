import tempfile
from pathlib import Path
import unittest

from streetlab.sun_sky import SkySettings, read_settings, write_settings


class SkySettingsTests(unittest.TestCase):
    def test_updates_preserve_other_controls_and_normalize_angle(self):
        original = SkySettings(air=.8, sun_intensity=2)
        changed = original.updated(aerosols=1.5, sun_rotation=-30)
        self.assertEqual(changed.sun_rotation,330)
        self.assertEqual(changed.air,.8)
        self.assertEqual(changed.sun_intensity,2)
        self.assertEqual(original.aerosols,1)

    def test_reject_nonphysical_types_and_bad_values(self):
        for bad in [dict(air=float('nan')),dict(aerosols=-1),dict(sun_disc='false'),
                    dict(sun_size=0),dict(sun_elevation=100),dict(altitude=-10),dict(sun_intensity=True)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):SkySettings(**bad)
        with self.assertRaises(TypeError):SkySettings(turbidity=3)

    def test_saved_units_are_required_and_values_roundtrip(self):
        temp_root=Path(__file__).resolve().parents[1]/'scratch'/'tests'/'sun_sky'
        temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as tmp:
            p=Path(tmp)/'sky.json'
            value=SkySettings(sun_disc=False, sun_elevation=-6, aerosols=2)
            write_settings(p,value)
            self.assertEqual(read_settings(p),value)
            p.write_text(p.read_text().replace('degrees','radians'))
            with self.assertRaises(ValueError):read_settings(p)


if __name__=='__main__':unittest.main()
