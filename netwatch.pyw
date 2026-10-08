"""双击启动网络波动悬浮窗。没有黑色命令行窗口。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from netwatch.app import main

if __name__ == "__main__":
    main()
