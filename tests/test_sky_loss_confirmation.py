from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest

import numpy as np
from PIL import Image

from streetlab.sky_loss_confirmation import confirm,validate
from streetlab.paths import ROOT


class LossConfirmationTests(unittest.TestCase):
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


if __name__=='__main__':unittest.main()
