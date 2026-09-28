"""闲鱼盯价助手：本地 Web 工具。"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:  # 版本号只在 pyproject.toml 定义一处，运行时从包元数据读取。
    __version__ = _pkg_version("good-price")
except PackageNotFoundError:  # 源码直接运行、未安装时
    __version__ = "0.0.0"
