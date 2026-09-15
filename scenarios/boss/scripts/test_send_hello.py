import sys
import logging

sys.path.insert(0, r"C:\Dev\projects\pixelclaw")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from skills.android.adb_runner import ADBRunner
from skills.boss.boss_automation_skill import BOSSAutomationSkill

OUTPUT_DIR = r"C:\Dev\projects\pixelclaw\scenarios\boss\output"

adb = ADBRunner()
skill = BOSSAutomationSkill(adb_manager=adb, output_dir=OUTPUT_DIR)

print("=" * 50)
print("测试：发送「你好」并验证")
print("  请确认手机已打开 BOSS 聊天界面（看到输入框）")
print("=" * 50)

ok = skill.send_greeting("你好", verify=True)
print()
print("=" * 50)
print("✓ 发送成功，消息已出现在聊天气泡！" if ok else "✗ 发送失败，请查看日志和截图")
print("=" * 50)
print(f"调试截图保存在: {OUTPUT_DIR}")
print("  debug_send_01_before_type.png — 点击输入框后")
print("  debug_send_02_after_type.png  — 输入文字后")
print("  debug_send_03_after_send.png  — 点击发送后")
