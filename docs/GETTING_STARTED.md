# PixelClaw 使用指南

> 本指南带你从零开始，完成环境安装、设备连接，并运行第一个自动化场景。

---

## 1. 前提条件

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Python | 3.9+ | 建议 3.11 |
| ADB | 任意近期版本 | Android Debug Bridge |
| Android 设备 | Pixel 8a（1080×2400）| 需开启无线调试 |
| Shizuku | 13+（可选） | 提升 ADB 权限，部分操作需要 |

---

## 2. 环境安装

### 2.1 克隆仓库

```bash
git clone https://github.com/laiyinyizao007/pixelclaw.git
cd pixelclaw
```

### 2.2 安装 Python 依赖

```bash
# 标准安装（核心功能）
pip install -r requirements.txt

# 可选：OCR 回退策略（PaddleOCR）
# pip install paddlepaddle paddleocr
```

> **Raspberry Pi 用户**：运行 `bash scripts/setup.sh` 完成包括 ADB 在内的完整安装，并生成 `activate` 快捷脚本（`source ./activate`）。

### 2.3 配置 API Key（Step-1V 云端 VLM，可选）

编辑 `config/api_keys.json`：

```json
{
  "step1v": {
    "api_key": "your-api-key-here",
    "base_url": "https://api.stepfun.com/v1"
  }
}
```

> 如果只使用规则驱动的 Boss/XHS 场景脚本（无需 VLM 分析屏幕），可跳过此步。

---

## 3. 连接 Pixel 8a

### 3.1 主机环境与配置档位

`config/devices.json` 当前默认是 **Raspberry Pi 5** 主机档（2026-10 起的实际运行环境）。如要在 **Windows** 主机上跑，参见 §3.10「切换到 Windows 主机档」。

| 主机 | 档位文件 | `ip` | `port`（ADB） | `pairing_port` | `pairing_code` | 备注 |
|---|---|---|---|---|---|---|
| **Raspberry Pi 5**（默认） | `config/devices.json` | `10.32.7.125` | `42527` | `36111` | `988963` | 当前值；每次重开无线调试都要重读手机屏幕 |
| **Windows**（历史） | `config/devices.windows.example.json` | `172.19.0.1` | `45373` | —（沿用 `port`） | `444047` | 切换到 Pi 之前的旧值；`pairing_port` 字段缺失时 `DeviceConnector.pair()` 会回退用 `port` |

### 3.2 开启无线调试（Raspberry Pi 5 主机）

1. 设置 → 关于手机 → 连续点击"版本号"7 次，开启开发者选项
2. 设置 → 系统 → 开发者选项 → 开启"无线调试"
3. 进入"无线调试"页面，记录屏幕上显示的 **IP 地址和端口**（如 `10.32.7.125:xxxxx`）

> ⚠️ **IP 地址取决于手机和 Pi 当前所在的 WiFi 子网**，不是固定值。本次实测 Pi 在 `10.32.7.0/24`，手机 `10.32.7.125`。换网或换手机时必须重新确认。

### 3.3 同步配置到 `config/devices.json`（Pi 档）

把屏幕上的值写进配置：

```json
{
  "ip": "10.32.7.125",
  "port": 42527,
  "pairing_code": "988963",
  "pairing_port": 36111
}
```

| 字段 | 来源 |
|---|---|
| `ip` | 手机"无线调试"主页 IP 地址 |
| `pairing_port` | 点击"使用配对码配对"后弹窗的端口（与主页端口不同，~30s 过期） |
| `pairing_code` | 同上弹窗的 6 位配对码 |
| `port` | **配对成功后**手机主页会刷新出新的"IP 地址 & 端口"，那是 ADB 接入端口 |

### 3.4 ADB 配对（首次 / 重连）

在手机"无线调试"页面点"使用配对码配对设备"，**保持弹窗不退出**，然后：

```bash
adb pair 10.32.7.125:<配对端口> <配对码>
# 示例：adb pair 10.32.7.125:36111 988963
```

成功输出：`Successfully paired to 10.32.7.125:36111 [guid=...]`

### 3.5 ADB 连接

配对成功后回到"无线调试"主页，会看到新刷出的"IP 地址 & 端口"（如 `10.32.7.125:42527`），用它：

```bash
adb connect 10.32.7.125:42527
```

### 3.6 验证连接

```bash
adb devices -l
```

预期输出：

```
List of devices attached
10.32.7.125:42527      device product:akita model:Pixel_8a ...
```

看到 `device`（而非 `offline` / `unauthorized`）即表示连接成功。

### 3.7 启动 Keepalive 后台服务（可选）

`./pixelclaw` 是项目根的 launcher 脚本，让 `python -m pixelclaw` 不必切目录也能用：

```bash
# 启动后台守护进程
./pixelclaw --service

# 查看状态
./pixelclaw --service --status

# 查看日志
tail -f logs/keepalive.log

# 停止
./pixelclaw --service --stop
```

或从项目根父目录直接：

```bash
cd /home/averypi/Projects
python -m pixelclaw --service
```

PID 文件：`/tmp/pixelclaw_keepalive.pid`
状态文件：`logs/keepalive_status.json`（每 10 次健康检查刷新）

### 3.8 重新配对的时机

只要手机端无线调试**保持开启**，连接会一直存活。**关闭再打开无线调试**则必须重走 §3.4 + §3.5，因为配对端口和配对码会重新生成。

### 3.9 Shizuku（可选，提升权限）

Shizuku 允许在不 root 的情况下通过 ADB shell 执行高权限操作：

1. 从 Play Store 安装 [Shizuku](https://play.google.com/store/apps/details?id=moe.shizuku.privileged.api)
2. 通过 ADB 启动 Shizuku 服务：
   ```bash
   adb shell sh /sdcard/Android/data/moe.shizuku.privileged.api/start.sh
   ```
3. 在手机上打开 Shizuku 应用，点击"通过无线调试启动"

### 3.10 切换到 Windows 主机档（历史 / 默认值）

> ⚠️ 这套档位已不再维护（2026-10 起项目默认在 Pi 上跑）。仅当你需要在 Windows 主机上临时调试时参考。

`config/devices.windows.example.json` 是切换前的占位默认值（`ip: 172.19.0.1`，`port: 45373`，`pairing_code: 444047`）。要把项目跑回 Windows 主机：

```bash
# 备份当前 Pi 档
cp config/devices.json config/devices.raspberrypi.json.bak

# 用 Windows 档覆盖
cp config/devices.windows.example.json config/devices.json

# 然后按需改 IP / 配对码（Windows 上 Pi 的值会变；反之亦然）
```

**配对码在 Windows 档下**：原 `pairing_port` 字段为 `null`，`DeviceConnector.pair()` 会回退使用 `port`（即 `45373`）。这个回退是 2026-10 之前的行为，是否仍能在 Android 11+ 上配对成功**未经实测**——可能需要按 Pi 档那样手动补一个 `pairing_port`。如果配对一直失败，先临时把 `pairing_port` 写成手机屏幕显示的端口。

---

## 4. 验证项目正常运行

运行单元测试套件（**无需连接设备，无需 GPU**）：

```bash
python -m pytest tests/ -v --import-mode=importlib
```

预期结果：`162 passed`（全部通过，包含 Boss 和核心策略测试）。

---

## 5. Boss直聘 自动化（主场景）

> **平台规则**：只有双方都发过消息后，聊天页才会出现"投递简历"按钮。
> 请分两个阶段运行，中间等待 HR 回复。
> 详见 [scenarios/boss/docs/boss_platform_rules.md](../scenarios/boss/docs/boss_platform_rules.md)

### 第一阶段：搜索职位并发打招呼

```bash
python scenarios/boss/tasks/boss_greet_task.py --keyword "Python 工程师"

# 可选参数
# --max-jobs 50          最多处理多少个职位（默认 50）
# --device 42231JEKB04971  指定设备序列号（多设备时使用）
```

**预期输出示例**：

```
[1/12] 产品经理招聘 @ 科技公司 → 打招呼成功
[2/12] 数据分析师 @ 互联网企业 → 已有聊天，跳过
...
结果：成功 10，跳过 2，失败 0
```

### 等待

等待数小时至一天，HR 陆续回复消息。

### 第二阶段：检查回复并投递简历

```bash
python scenarios/boss/tasks/boss_apply_task.py --max-apply 20

# --device 参数同上
```

**预期输出示例**：

```
[1/8] 王HR @ 产品经理 → 已投递
[2/8] 李HR @ 数据工程师 → HR 未回复，跳过
...
结果：已投递 5，跳过 3，失败 0
```

### API 参考

详细方法说明见 [scenarios/boss/docs/boss_automation_guide.md](../scenarios/boss/docs/boss_automation_guide.md)

---

## 6. XHS（小红书）自动化

XHS 场景通过 HTTP Skill API（`http://localhost:18791/v1/skills/android-automation/execute`）调用。

```bash
# 博主推荐任务（搜索 OpenClaw 相关博主并发帖推荐）
python scenarios/xhs/tasks/xhs_recommend_task_v2.py

# 博主互动任务
python scenarios/xhs/tasks/run_blogger_task.py
```

> XHS 场景需要先启动 Skill API 服务（`python -m pixelclaw --service`）。
> 详见 [scenarios/xhs/docs/xhs_automation_guide.md](../scenarios/xhs/docs/xhs_automation_guide.md)

---

## 7. 运行视觉代理（高级）

视觉代理模式通过 VLM（云端/本地）分析屏幕并执行开放式任务：

```python
import asyncio
from pixelclaw import DeviceConnector, VisionAgent, FallbackManager
from core.app_knowledge import AppKnowledge

async def main():
    connector = DeviceConnector()
    connector.connect()
    agent = VisionAgent(
        connector,
        FallbackManager(),
        som_enabled=True,           # SoM 截图标注
        reflection_enabled=True,    # 操作后验证屏幕变化
        app_knowledge=AppKnowledge("boss"),
    )
    result = await agent.execute_task("搜索 Python 工程师职位并打招呼")
    print(result)

asyncio.run(main())
```

---

## 8. 常见问题

| 问题 | 解决方案 |
|------|---------|
| `adb devices` 显示 `unauthorized` | 在手机上点击"始终允许来自此计算机的调试" |
| `Connection refused` | 确认无线调试已开启，重新 `adb connect` |
| `adb: error: more than one device` | 用 `--device <serial>` 指定目标设备 |
| 测试全部失败 | 检查 Python 版本 ≥ 3.9，重新 `pip install -r requirements.txt` |
| Boss脚本报 `ModuleNotFoundError` | 从项目根目录运行，而非 `scenarios/boss/tasks/` 目录内 |

---

## 9. 相关文档

| 文档 | 内容 |
|------|------|
| [README.md](../README.md) | 项目概述与架构说明 |
| [PROJECTWIKI.md](../PROJECTWIKI.md) | 技术参考（模块、API、数据模型） |
| [scenarios/boss/docs/boss_automation_guide.md](../scenarios/boss/docs/boss_automation_guide.md) | Boss直聘 Skill API 参考 |
| [scenarios/boss/docs/boss_platform_rules.md](../scenarios/boss/docs/boss_platform_rules.md) | Boss直聘 平台规则（投递前提、每日限制） |
| [scenarios/xhs/docs/xhs_automation_guide.md](../scenarios/xhs/docs/xhs_automation_guide.md) | XHS 坐标驱动操作说明 |
| [docs/app_research_checklist.md](app_research_checklist.md) | 新增 App 前必须研究的内容清单 |
