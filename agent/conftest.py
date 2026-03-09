"""Root conftest — ensure agent/ is on sys.path for test imports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
