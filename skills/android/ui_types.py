"""
共用 UI 数据类型。

同时替代 XHSAutomationSkill 和 BOSSAutomationSkill 中各自定义的 UIElement，
以及分散在两处的 bounds 解析逻辑。
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass
class UIElement:
    """从 UIAutomator dump 解析出的单个 UI 节点。"""

    resource_id: str = ""
    text: str = ""
    content_desc: str = ""
    bounds: Tuple[int, int, int, int] = field(default_factory=lambda: (0, 0, 0, 0))
    clickable: bool = False

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        if len(self.bounds) == 4:
            x1, y1, x2, y2 = self.bounds
            return (x1 + x2) // 2, (y1 + y2) // 2
        return None

    @property
    def width(self) -> int:
        if len(self.bounds) == 4:
            return self.bounds[2] - self.bounds[0]
        return 0

    @property
    def height(self) -> int:
        if len(self.bounds) == 4:
            return self.bounds[3] - self.bounds[1]
        return 0


def parse_bounds(bounds_str: str) -> Optional[Tuple[int, int, int, int]]:
    """将 '[x1,y1][x2,y2]' 格式的字符串解析为 (x1, y1, x2, y2) 元组。"""
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())
