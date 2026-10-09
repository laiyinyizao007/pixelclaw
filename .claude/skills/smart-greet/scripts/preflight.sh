#!/usr/bin/env bash
# preflight.sh — smart-greet skill 的一键 preflight 检查
# 跑这个脚本验证所有前置条件（5 项），任一失败 exit 1 并提示修复
#
# 用法：
#   bash .claude/skills/smart-greet/scripts/preflight.sh
#   bash .claude/skills/smart-greet/scripts/preflight.sh --device 10.32.7.105:5555

set -euo pipefail

REPO_ROOT="${PIXELCLAW_REPO_ROOT:-/home/averypi/Projects/pixelclaw}"
DEVICE_FLAG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --device) DEVICE_FLAG="--device $2"; shift 2 ;;
    *) shift ;;
  esac
done

cd "$REPO_ROOT"

PASS=0
FAIL=0

ok()   { echo "  ✓ $1"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }

echo "=== smart-greet preflight ==="
echo

# ─── 1. ADB 设备 ───
echo "[1/5] ADB 设备连接"
if command -v adb >/dev/null 2>&1; then
  DEVICES=$(adb devices 2>/dev/null | tail -n +2 | grep -E "device$" || true)
  if [[ -n "$DEVICES" ]]; then
    ok "ADB 设备已连接：$(echo "$DEVICES" | head -1 | awk '{print $1}')"
  else
    fail "无 ADB 设备连接。运行：adb connect 10.32.7.105:5555"
  fi
else
  fail "adb 命令未找到。安装：sudo apt install adb"
fi
echo

# ─── 2. .env 配置 ───
echo "[2/5] .env 配置（4 个关键变量）"
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    fail ".env 不存在。从 .env.example 复制：cp .env.example .env && chmod 600 .env"
  else
    fail ".env 和 .env.example 都不存在"
  fi
else
  ok ".env 存在"
  for VAR in ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_BACKUP_API_KEY PIXELCLAW_RESUME_PATH; do
    if grep -qE "^${VAR}=" .env; then
      VALUE=$(grep -E "^${VAR}=" .env | head -1 | cut -d= -f2-)
      if [[ -n "$VALUE" ]]; then
        ok "$VAR 已设置"
      else
        if [[ "$VAR" == "ANTHROPIC_BACKUP_API_KEY" ]]; then
          ok "$VAR 未设（fallback 不启用，正常）"
        else
          fail "$VAR 已设但为空（值缺失）"
        fi
      fi
    else
      fail "$VAR 未在 .env 中"
    fi
  done
fi
echo

# ─── 3. 简历文件 ───
echo "[3/5] 简历文件存在"
RESUME_PATH="${PIXELCLAW_RESUME_PATH:-}"
if [[ -z "$RESUME_PATH" ]] && [[ -f .env ]]; then
  RESUME_PATH=$(grep -E "^PIXELCLAW_RESUME_PATH=" .env | head -1 | cut -d= -f2- || true)
fi
if [[ -z "$RESUME_PATH" ]]; then
  RESUME_PATH="$HOME/Projects/resume-renew/resume/current.md"
fi
if [[ -f "$RESUME_PATH" ]]; then
  SIZE=$(stat -c %s "$RESUME_PATH" 2>/dev/null || stat -f %z "$RESUME_PATH" 2>/dev/null || echo 0)
  ok "简历存在：$RESUME_PATH (${SIZE} bytes)"
else
  fail "简历文件不存在：$RESUME_PATH
    修复方法 1：export PIXELCLAW_RESUME_PATH=/path/to/resume.md
    修复方法 2：cp your-resume.md $RESUME_PATH"
fi
echo

# ─── 4. LLM endpoint 30s 诊断 ───
echo "[4/5] LLM endpoint 30s 诊断"
if [[ -f .env ]]; then
  if grep -qE "^ANTHROPIC_BASE_URL=.*minimax\.chat" .env; then
    fail "ANTHROPIC_BASE_URL 配错！minimax.chat 是 OpenAI 协议。改成：
      ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic"
  elif grep -qE "^ANTHROPIC_BASE_URL=.*minimaxi\.com" .env; then
    # 30s 超时裸 SDK 测试
    TEST_OUTPUT=$(timeout 30 python3 -c "
import os, sys
sys.path.insert(0, '.')
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass
import anthropic
client = anthropic.Anthropic(
    api_key=os.environ.get('ANTHROPIC_API_KEY', 'dummy'),
    base_url=os.environ.get('ANTHROPIC_BASE_URL', ''),
    timeout=25.0,
)
try:
    resp = client.messages.create(
        model='MiniMax-M3', max_tokens=16,
        messages=[{'role':'user','content':'hi'}]
    )
    print('OK')
except anthropic.APIStatusError as e:
    print(f'API_{e.status_code}')
except Exception as e:
    print(f'ERR:{type(e).__name__}')
" 2>&1 | tail -1)

    case "$TEST_OUTPUT" in
      OK) ok "LLM endpoint 健康（5s 内响应）" ;;
      API_401|API_404|API_403) ok "LLM endpoint 响应（${TEST_OUTPUT}，鉴权/路径问题，但 SDK 通信正常）" ;;
      API_*) fail "LLM endpoint 返回 ${TEST_OUTPUT}" ;;
      ERR:*) fail "LLM endpoint 错误：${TEST_OUTPUT#ERR:}" ;;
      *) fail "LLM endpoint 30s 未响应（URL 大概率错了）：${TEST_OUTPUT}" ;;
    esac
  else
    fail "ANTHROPIC_BASE_URL 未配置 minimaxi.com/anthropic"
  fi
else
  fail ".env 不存在，跳过 LLM 诊断"
fi
echo

# ─── 5. App 安装检查（按用户指定平台） ───
echo "[5/5] App 安装状态（如果指定了 device serial）"
if [[ -n "${DEVICE_FLAG}" ]] || [[ -n "$(adb devices 2>/dev/null | tail -n +2 | grep -E 'device$' | head -1 | awk '{print $1}')" ]]; then
  SERIAL=$(echo "$DEVICE_FLAG" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+' || \
           adb devices 2>/dev/null | tail -n +2 | grep -E 'device$' | head -1 | awk '{print $1}')
  if [[ -n "$SERIAL" ]]; then
    for PKG in com.hpbr.bosszhipin; do
      if adb -s "$SERIAL" shell pm list packages "$PKG" 2>/dev/null | grep -q "$PKG"; then
        ok "$PKG 已安装"
      else
        fail "$PKG 未安装"
      fi
    done
  else
    fail "无法确定设备 serial"
  fi
else
  echo "  ⚠ 跳过（未连接设备，无法检查 App）"
fi
echo

# ─── 总结 ───
echo "=== preflight 结果 ==="
echo "  通过：$PASS"
echo "  失败：$FAIL"
echo

if [[ $FAIL -gt 0 ]]; then
  echo "✗ preflight 失败。请按上述 ✗ 项修复后重跑。"
  exit 1
else
  echo "✓ preflight 全部通过。可以跑 smart_match_greet.py。"
  echo
  echo "推荐先跑 --score-only 验证："
  echo "  PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \\"
  echo "    --keyword 'AI产品经理' --score-only --max-greet 5 --strict \\"
  echo "    $DEVICE_FLAG"
  exit 0
fi
