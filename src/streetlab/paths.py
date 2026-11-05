"""Project paths shared by CLI tools and Blender workers."""
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parents[1]


def trellis_config(root=ROOT):
    """Use machine-specific settings when present, otherwise the tracked example."""
    local = root / "config" / "trellis.local.json"
    return local if local.is_file() else root / "config" / "trellis.example.json"
