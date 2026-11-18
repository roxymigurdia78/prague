from pathlib import Path
import base64
import io
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import Mock,patch

import numpy as np
from PIL import Image

from streetlab.sky_loss_confirmation import confirm,validate
from streetlab.paths import ROOT


class LossConfirmationTests(unittest.TestCase):
    def test_tiny_alleged_building_loss_is_really_inspected_instead_of_automatically_retained(self):
        from streetlab import sky_residual_loop as loop
        from streetlab.cleanup_convergence import save
        parent=ROOT/'scratch/tests/sky_loss';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            folder=Path(temp);before=folder/'before';after=folder/'after'
            before.mkdir();after.mkdir()
            save(before/'views.json',{'views':[{'id':'v0'}]})
            rgb=np.full((100,100,3),150,dtype=np.uint8);rgb[45:48,45:48]=0
            Image.fromarray(rgb).save(before/'v0_after.png')
            rgb[45:48,45:48]=[255,0,255];Image.fromarray(rgb).save(after/'v0_after.png')
            response={'views':[{'id':'v0','protected_polygons':[[[400,400],[500,400],[500,500],[400,500]]]}]}
            with patch('openai.OpenAI',return_value=Mock()),patch.object(
                    loop,'hpc_settings',return_value=('test','https://example.invalid','model')),patch(
                    'streetlab.sky_cleanup_review.request_review',return_value=response),patch(
                    'streetlab.sky_loss_confirmation.confirm',return_value={'verdict':'sky'}) as inspect:
                loss=loop.building_loss(before,after,folder/'guard','model')
            self.assertEqual(loss,0)
            self.assertEqual(inspect.call_count,1)
            self.assertTrue(inspect.call_args.kwargs['context'])
            self.assertEqual(int(inspect.call_args.args[4].sum()),9)

    def test_only_actual_removed_surface_is_sent_to_the_confirmation_model(self):
        parent=ROOT/'scratch/tests/sky_loss';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            folder=Path(temp);before=np.full((60,80,3),150,dtype=np.uint8)
            changed=np.zeros((60,80),dtype=bool);changed[20:40,25:50]=True
            Image.fromarray(before).save(folder/'before.png')
            after=before.copy();after[changed]=[255,0,255];Image.fromarray(after).save(folder/'after.png')
            calls=[]
            class Stream:
                def __iter__(self):
                    yield NS(choices=[NS(delta=NS(content='{"verdict":"sky","reason":"uniform gray"}'),finish_reason='stop')])
                def close(self):pass
            def create(**kwargs):calls.append(kwargs);return Stream()
            client=NS(chat=NS(completions=NS(create=create)))
            result=confirm(client,'test-model',folder/'before.png',folder/'after.png',changed,folder/'cache')
            self.assertEqual(result['verdict'],'sky')
            content=calls[0]['messages'][1]['content']
            self.assertEqual(sum(row['type']=='image_url' for row in content),1)
            confirm(client,'test-model',folder/'before.png',folder/'after.png',changed,folder/'cache')
            self.assertEqual(len(calls),1)

    def test_unrecognized_verdict_is_not_accepted_as_sky(self):
        with self.assertRaises(ValueError):validate('{"verdict":"probably fine"}')
        self.assertEqual(validate('{"verdict":"uncertain"}')['verdict'],'uncertain')

    def test_tiny_black_target_is_visible_against_checkerboard_with_context(self):
        parent=ROOT/'scratch/tests/sky_loss';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            folder=Path(temp)
            rgb=np.full((100,100,3),(255,0,255),dtype=np.uint8)
            changed=np.zeros((100,100),dtype=bool);changed[45:48,45:48]=True;rgb[changed]=0
            Image.fromarray(rgb).save(folder/'before.png')
            after=rgb.copy();after[changed]=[255,0,255];Image.fromarray(after).save(folder/'after.png')
            calls=[]
            class Stream:
                def __iter__(self):
                    yield NS(choices=[NS(delta=NS(content='{"verdict":"uncertain"}'),finish_reason='stop')])
                def close(self):pass
            def create(**kwargs):calls.append(kwargs);return Stream()
            client=NS(chat=NS(completions=NS(create=create)))
            confirm(client,'model',folder/'before.png',folder/'after.png',changed,folder/'cache',context=True)
            content=calls[0]['messages'][1]['content']
            raw=next(row['image_url']['url'] for row in content if row['type']=='image_url').split(',',1)[1]
            with Image.open(io.BytesIO(base64.b64decode(raw))) as image:sheet=np.asarray(image)
            target=sheet[24:536,:512]
            self.assertTrue((target==[0,0,0]).all(axis=2).any())
            self.assertTrue((target==[32,75,100]).all(axis=2).any())
            self.assertIn('BEFORE',calls[0]['messages'][0]['content'])


if __name__=='__main__':unittest.main()
