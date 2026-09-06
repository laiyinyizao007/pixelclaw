"""
Canvas 按钮探测工具。

XHS 等 App 的部分互动按钮（❤️⭐💬）用 Canvas 绘制，
uiautomator dump 无法识别，只能通过像素扫描定位坐标。

典型用法::

    probe = CanvasProbe(device_id="42231JEKB04971")
    probe.take_screenshot()
    coords = probe.probe_bar(y_range=(2100, 2400))
    # {"icon_0": (534, 2286), "icon_1": (741, 2286), "icon_2": (928, 2286)}
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from skills.android.adb_runner import ADBRunner

# config/devices/ 默认位置：相对于项目根（skills/android/canvas_probe.py → parents[2]）
_DEFAULT_CONFIG_ROOT = Path(__file__).parents[2] / "config" / "devices"


class CanvasProbe:
    """
    通过像素扫描在截图中定位 Canvas 绘制的图标。

    假设：图标为亮色前景（所有 RGB 通道 > fg_threshold），背景为深色。
    坐标系与 ADB 截图一致（原始设备分辨率）。
    """

    def __init__(
        self,
        device_id: str,
        bg_threshold: int = 50,
        fg_threshold: int = 200,
        min_cluster_width: int = 30,
        min_cluster_pixels: int = 3,
        adb: Optional[ADBRunner] = None,
    ):
        """
        Args:
            device_id:          ADB 设备序列号
            bg_threshold:       背景亮度上限（保留备用，暂未使用）
            fg_threshold:       前景亮度下限（所有通道 > 此值视为亮色像素）
            min_cluster_width:  图标群最小宽度（像素），用于过滤噪点
            min_cluster_pixels: 某列至少需要多少亮像素才算"有内容"
            adb:                ADBRunner 实例；None 时自动创建。
        """
        self.device_id = device_id
        self.bg_threshold = bg_threshold
        self.fg_threshold = fg_threshold
        self.min_cluster_width = min_cluster_width
        self.min_cluster_pixels = min_cluster_pixels
        self._adb_runner = adb if adb is not None else ADBRunner()
        self._screenshot: Optional[np.ndarray] = None

    def take_screenshot(self) -> np.ndarray:
        """
        通过 ADB 截图并返回 numpy 数组（H×W×3，RGB）。
        结果缓存在 self._screenshot，可直接重复调用 find_icon_clusters。
        """
        tmp = Path(tempfile.gettempdir()) / f"canvas_probe_{self.device_id}.png"
        ok_cap, _ = self._adb_runner.shell("shell screencap -p /sdcard/_canvas_probe.png", self.device_id)
        if not ok_cap:
            raise RuntimeError(f"screencap 失败，设备 {self.device_id} 是否已连接？")
        ok = self._adb_runner.pull("/sdcard/_canvas_probe.png", str(tmp), self.device_id)
        if not ok:
            raise RuntimeError(f"adb pull 失败，设备 {self.device_id} 是否已连接？")
        img = Image.open(tmp).convert("RGB")
        self._screenshot = np.asarray(img)
        return self._screenshot

    def find_icon_clusters(
        self,
        y_start: int,
        y_end: int,
        x_start: int = 0,
        x_end: Optional[int] = None,
        min_gap: int = 10,
    ) -> List[Dict]:
        """
        在指定矩形区域内找所有亮色图标群（连续白色像素段）。

        扫描算法：
        1. 取区域的亮色像素掩码（所有通道 > fg_threshold）
        2. 按列求和得到逐列亮度轮廓
        3. 把"有内容的列"分段，小于 min_gap 的间隙合并到同一群
        4. 过滤宽度 < min_cluster_width 的噪点
        5. 对每个群求行方向范围，得到完整边界和中心坐标

        Args:
            y_start:  扫描起始行（原始坐标）
            y_end:    扫描结束行（原始坐标，不含）
            x_start:  扫描起始列（默认 0）
            x_end:    扫描结束列（默认图像宽度）
            min_gap:  列间距超过此值才视为两个独立图标

        Returns:
            按 x_start 排序的列表，每项包含:
            x_start, x_end, y_start, y_end, center_x, center_y, width, height
        """
        if self._screenshot is None:
            raise RuntimeError("请先调用 take_screenshot()")

        x_end = x_end or self._screenshot.shape[1]
        region = self._screenshot[y_start:y_end, x_start:x_end]  # (h, w, 3)

        # 亮色像素掩码：所有通道 > fg_threshold
        bright = np.all(region > self.fg_threshold, axis=2)  # (h, w)

        # 每列亮像素数
        col_sum = bright.sum(axis=0)  # (w,)

        # 有效列
        active_cols = np.where(col_sum >= self.min_cluster_pixels)[0]
        if len(active_cols) == 0:
            return []

        # 按列间距分段
        gaps = np.diff(active_cols)
        break_indices = np.where(gaps > min_gap)[0]

        segments = []
        start_idx = 0
        for b in break_indices:
            segments.append((int(active_cols[start_idx]), int(active_cols[b])))
            start_idx = b + 1
        segments.append((int(active_cols[start_idx]), int(active_cols[-1])))

        results = []
        for x1_rel, x2_rel in segments:
            width = x2_rel - x1_rel + 1
            if width < self.min_cluster_width:
                continue

            # 找 y 方向范围（只看该列段内的亮行）
            sub = bright[:, x1_rel : x2_rel + 1]
            row_sum = sub.sum(axis=1)
            active_rows = np.where(row_sum > 0)[0]
            if len(active_rows) == 0:
                continue

            y1_rel = int(active_rows[0])
            y2_rel = int(active_rows[-1])

            results.append(
                {
                    "x_start":  x_start + x1_rel,
                    "x_end":    x_start + x2_rel,
                    "y_start":  y_start + y1_rel,
                    "y_end":    y_start + y2_rel,
                    "center_x": x_start + (x1_rel + x2_rel) // 2,
                    "center_y": y_start + (y1_rel + y2_rel) // 2,
                    "width":    width,
                    "height":   y2_rel - y1_rel + 1,
                }
            )

        return results

    # ── 配置文件读写 ───────────────────────────────────────────────────────────

    def save_coords(
        self,
        app: str,
        section: str,
        coords: Dict[str, list],
        screen_size: Optional[Tuple[int, int]] = None,
        config_root: Optional[Path] = None,
    ) -> Path:
        """
        将坐标写入 config/devices/<device_id>.json。

        Args:
            app:         应用名称（如 "xhs", "boss"），作为 JSON 顶层 key
            section:     功能区名称（如 "video_post_bar"），嵌套在 app 下
            coords:      {label: [x, y]} 格式的坐标字典
            screen_size: (width, height) 若提供则一并写入
            config_root: 覆盖默认的 config/devices/ 目录

        Returns:
            写入的 JSON 文件路径
        """
        root = config_root or _DEFAULT_CONFIG_ROOT
        root.mkdir(parents=True, exist_ok=True)
        out = root / f"{self.device_id}.json"

        data: dict = {}
        if out.exists():
            try:
                data = json.loads(out.read_text(encoding="utf-8"))
            except Exception:
                pass

        data["device_id"] = self.device_id
        if screen_size:
            data["screen"] = {"width": screen_size[0], "height": screen_size[1]}
        data["probed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        app_data = data.get(app, {})
        app_data[section] = coords
        data[app] = app_data

        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    def load_coords(
        self,
        app: str,
        section: str,
        config_root: Optional[Path] = None,
    ) -> Dict[str, Tuple[int, int]]:
        """
        从 config/devices/<device_id>.json 读取坐标。

        Returns:
            {label: (x, y)} 坐标字典；文件不存在或无对应数据时返回空字典
        """
        root = config_root or _DEFAULT_CONFIG_ROOT
        cfg = root / f"{self.device_id}.json"
        if not cfg.exists():
            return {}
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            raw = data.get(app, {}).get(section, {})
            return {k: tuple(v) for k, v in raw.items()}
        except Exception:
            return {}

    def probe_and_save(
        self,
        app: str,
        section: str,
        labels: List[str],
        y_range: Tuple[int, int],
        config_root: Optional[Path] = None,
        min_clusters: int = 1,
        screen_size: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, Tuple[int, int]]:
        """
        一步完成：扫描图标群 → 按 labels 顺序映射 → 保存到设备配置文件。

        各 app 的初始化脚本只需导航到含 Canvas 按钮的页面，然后调此方法即可::

            probe.take_screenshot()
            coords = probe.probe_and_save(
                app="xhs",
                section="video_post_bar",
                labels=["like", "collect", "comment"],
                y_range=(2100, 2400),
                min_clusters=2,
            )

        Args:
            app:          应用名称（"xhs" / "boss" / ...）
            section:      功能区名称（"video_post_bar" / "job_card_bar" / ...）
            labels:       从左到右的语义标签，如 ["like", "collect", "comment"]
            y_range:      (y_start, y_end) 扫描行范围（原始设备坐标）
            config_root:  覆盖默认的 config/devices/ 目录
            min_clusters: 最少需要发现的图标群数，不足则抛出 RuntimeError
            screen_size:  (width, height) 若提供则一并写入配置

        Returns:
            {label: (x, y)} 坐标字典

        Raises:
            RuntimeError: 图标群数量不足 min_clusters
        """
        clusters = self.find_icon_clusters(y_range[0], y_range[1])
        if len(clusters) < min_clusters:
            raise RuntimeError(
                f"仅找到 {len(clusters)} 个图标群，至少需要 {min_clusters} 个；"
                f"截图: {Path(tempfile.gettempdir()) / ('canvas_probe_' + self.device_id + '.png')}"
            )

        coords_raw = {
            labels[i]: [c["center_x"], c["center_y"]]
            for i, c in enumerate(clusters)
            if i < len(labels)
        }
        self.save_coords(app, section, coords_raw, screen_size=screen_size, config_root=config_root)
        return {k: tuple(v) for k, v in coords_raw.items()}

    # ── 低级探测接口 ────────────────────────────────────────────────────────────

    def probe_bar(
        self,
        y_range: Tuple[int, int],
        label_prefix: str = "icon",
    ) -> Dict[str, Tuple[int, int]]:
        """
        对一行图标区做完整探测，按从左到右顺序返回 {label: (center_x, center_y)}。

        Args:
            y_range:      (y_start, y_end) 扫描的行范围（原始坐标）
            label_prefix: 标签前缀；结果 key 为 "prefix_0", "prefix_1", ...

        Returns:
            {"icon_0": (x, y), "icon_1": (x, y), ...}

        Example::

            coords = probe.probe_bar((2100, 2400))
            # XHS 语义：icon_0=like, icon_1=collect, icon_2=comment
        """
        clusters = self.find_icon_clusters(y_range[0], y_range[1])
        return {
            f"{label_prefix}_{i}": (c["center_x"], c["center_y"])
            for i, c in enumerate(clusters)
        }
