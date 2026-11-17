"""叙事分析业务流程的行为测试。"""
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
