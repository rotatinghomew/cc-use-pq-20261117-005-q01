"""样本分组、证据、一致性和导出测试。"""
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
