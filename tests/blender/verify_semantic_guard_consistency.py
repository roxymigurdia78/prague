"""Compare ordinary Python and Blender masks on the same real segmentation files."""
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
# Exercise Blender's fallback even if Pillow is installed by a user later.
sys.modules['PIL']=None
from streetlab.sky_semantics import allowed

request=Path(sys.argv[sys.argv.index('--')+1])
cases=json.loads(request.read_text(encoding='utf-8'))
for case in cases:
    expected=np.load(case['expected'])
    actual=allowed(case['folder'],case['view'],tuple(case['shape']))
    assert np.array_equal(actual,expected),(case['folder'],case['shape'],int((actual!=expected).sum()))
result={'python_and_blender_identical':True,'cases':len(cases)}
(request.parent/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
