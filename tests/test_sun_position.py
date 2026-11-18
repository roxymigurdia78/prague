import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streetlab import north_orientation as north
from streetlab import sun_sky


class NorthAndSunTests(unittest.TestCase):
    def test_random_north_is_persistent_and_seed_zero_is_reproducible(self):
        scene = {}
        first = north.ensure_north(scene, seed=0)
        self.assertEqual(first, north.ensure_north(scene, seed=42))
        self.assertEqual(first, north.ensure_north({}, seed=0))
        self.assertTrue(0 <= first['north_angle_deg'] < 360)
        with patch.object(north.secrets, 'randbits', side_effect=[1, 2]):
            self.assertNotEqual(north.ensure_north({}), north.ensure_north({}))

    def test_world_folder_json_survives_preparation_from_earlier_scene(self):
        temp_root = Path(__file__).resolve().parents[1] / 'scratch/tests/north'
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as tmp:
            path = Path(tmp) / 'north.json'
            original = north.ensure_north({}, seed=42, path=path)
            self.assertEqual(north.ensure_north({}, seed=0, path=path), original)
            self.assertEqual(north.read_north(path), original)
            with self.assertRaises(ValueError):
                north.ensure_north({north.PROPERTY: json.dumps(north.random_north(seed=7))}, path=path)
            self.assertEqual(north.read_north(path), original)

    def test_corrupt_state_is_not_silently_randomised(self):
        for raw in ('{}', '{', json.dumps({**north.random_north(seed=0), 'north_angle_deg': float('nan')})):
            scene = {north.PROPERTY: raw}
            with self.assertRaises(ValueError):
                north.ensure_north(scene)
            self.assertEqual(scene[north.PROPERTY], raw)
        for seed in (-1, True, '42', 2**64):
            with self.assertRaises(ValueError):
                north.random_north(seed=seed)

    def test_cardinal_azimuths_map_to_blender_and_world_vectors(self):
        # Geographic north at world +Y, geographic east at world +X.
        for azimuth, rotation, direction in ((0, 0, (0, 1, 0)), (90, 90, (1, 0, 0)),
                                             (180, 180, (0, -1, 0)), (270, 270, (-1, 0, 0))):
            result = sun_sky.solar_to_sky(0, azimuth, 90)
            self.assertEqual(result['sun_rotation'], rotation)
            for actual, expected in zip(result['sun_direction_world'], direction):
                self.assertAlmostEqual(actual, expected)
        # North at +X: morning in the east must move towards world -Y.
        result = sun_sky.solar_to_sky(30, 90, 0)
        self.assertEqual(result['sun_rotation'], 180)
        self.assertAlmostEqual(result['sun_direction_world'][1], -math.sqrt(3)/2)
        self.assertAlmostEqual(result['sun_direction_world'][2], .5)

    def test_datetime_to_sky_connection_keeps_north_across_time_changes(self):
        scene = {}
        with patch.object(sun_sky, 'set_sky', side_effect=lambda scene, **changes: sun_sky.SkySettings(**changes)) as apply:
            morning = sun_sky.set_sun(50.08804, 14.42076, '2026-07-15', '08:00+02:00', scene=scene, north_seed=0, altitude=202)
            evening = sun_sky.set_sun(50.08804, 14.42076, '2026-07-15', '20:00+02:00', scene=scene, north_seed=42)
        self.assertEqual(morning['north'], evening['north'])
        self.assertNotEqual(morning['sky_settings']['sun_rotation'], evening['sky_settings']['sun_rotation'])
        self.assertEqual(apply.call_args_list[0].kwargs['altitude'], 202)
        self.assertEqual(set(apply.call_args_list[1].kwargs), {'sun_elevation', 'sun_rotation'})
        self.assertEqual(json.loads(scene['_research_sun_position'])['north'], evening['north'])

    def test_timezone_and_validation_happen_before_north_or_sky_changes(self):
        scene = {}
        with patch.object(sun_sky, 'set_sky') as apply:
            for clock, options in (('18:00', {}), ('18:00+01:00', {'timezone_name': 'Europe/Prague'}),
                                   ('02:30', {'timezone_name': 'Europe/Prague'})):
                date = '2026-03-29' if clock == '02:30' else '2026-07-15'
                with self.assertRaises(ValueError):
                    sun_sky.set_sun(50, 14, date, clock, scene=scene, **options)
            apply.assert_not_called()
        self.assertEqual(scene, {})
        with patch.object(sun_sky, 'set_sky', side_effect=RuntimeError('unsupported Blender')):
            with self.assertRaises(RuntimeError):
                sun_sky.set_sun(50, 14, '2026-07-15', '18:00+02:00', scene=scene)
        self.assertEqual(scene, {})

    def test_explicit_timezone_agrees_with_offset_and_manual_north_can_be_used(self):
        scene = {}
        north.set_north(90, scene)
        with patch.object(sun_sky, 'set_sky', side_effect=lambda scene, **changes: sun_sky.SkySettings(**changes)):
            named = sun_sky.set_sun(50, 14, '2026-07-15', '18:00', timezone_name='Europe/Prague', scene=scene)
            offset = sun_sky.set_sun(50, 14, '2026-07-15', '18:00+02:00', scene=scene)
        self.assertEqual(named['sky_settings'], offset['sky_settings'])
        self.assertEqual(named['north']['method'], 'manual')


if __name__ == '__main__':
    unittest.main()
