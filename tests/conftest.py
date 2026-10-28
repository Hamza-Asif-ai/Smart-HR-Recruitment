import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Make the repository importable and point the MCP filesystem sandbox at the
# synthetic dataset before any src module is imported.
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HR_DATA_ROOT", str(ROOT / "data"))
os.chdir(ROOT)
