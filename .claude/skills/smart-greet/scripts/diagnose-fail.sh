#!/usr/bin/env bash
# diagnose-fail.sh — smart-greet 错误诊断
# 从错误日志（stdin 或文件参数）中匹配已知 12 类错误，给出修复建议
#
# 用法：
#   bash .claude/skills/smart-greet/scripts/diagnose-fail.sh
#   bash .claude/skills/smart-greet/scripts/diagnose-fail.sh /path/to/smart_match_greet.log
#   python scenarios/boss/scripts/smart_match_greet.py ... 2>&1 | bash .../diagnose-fail.sh

set -uo pipefail

INPUT="${1:-/dev/stdin}"
if [[ ! -r "$INPUT" ]]; then
  echo "错误：$INPUT 不可读"
  exit 1
fi

LOG=$(cat "$INPUT")

# P0（必须修）
echo "=== smart-greet 错误诊断 ==="
echo
echo "[P0] 阻塞类错误（必须修）"
if echo "$LOG" | grep -qE "404.*page not found|hang 30s|minimax\.chat"; then
  echo "  ⚠ 命中: API 404 + hang"
  echo "    原因: .env 配错 URL（minimax.chat 是 OpenAI 协议）"
  echo "    修复: ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic"
  echo
fi
if echo "$LOG" | grep -qE "ResumeNotFound|FileNotFoundError.*resume"; then
  echo "  ⚠ 命中: 简历文件找不到"
  echo "    原因: PIXELCLAW_RESUME_PATH 设错或简历文件被删"
  echo "    修复: ls -la \${PIXELCLAW_RESUME_PATH:-\$HOME/Projects/resume-renew/resume/current.md}"
  echo
fi
if echo "$LOG" | grep -qE "ModelNotFound|model_not_found"; then
  echo "  ⚠ 命中: 模型名不在 relay"
  echo "    原因: ANTHROPIC_BASE_URL 对应的 relay 不支持该模型"
  echo "    修复: 切到 minimaxi.com/anthropic + MiniMax-M3（已知可用）"
  echo
fi
if echo "$LOG" | grep -qE "protocol fault|protocol fault.*couldn't read status message"; then
  echo "  ⚠ 命中: adb pair 报 protocol fault"
  echo "    原因: 主机无法到达手机端无线调试 endpoint"
  echo "          典型场景: 手机端 VPN/隧道 App（如 com.pigeon.seppd）把 adbd"
  echo "                   绑到了不可达的子网接口（如 tun0）"
  echo "    修复: 走 WiFi 真实 IP + 标准 5555 端口（绕开 pair 弹窗）"
  echo "          adb shell ip addr show wlan0 | grep inet   # 拿手机 WiFi IP"
  echo "          adb connect <手机_wlan0_IP>:5555"
  echo "          （手机「无线调试」开关保持 ON 即可,不需要 pair 弹窗）"
  echo "    严禁: 关闭手机 VPN（项目硬性约束;上述绕路方案是永久方案）"
  echo "    详见: PROJECTWIKI.md §8.4.1b"
  echo
fi

# P1（功能受损）
echo "[P1] 功能受损类（能跑但有偏差）"
if echo "$LOG" | grep -qE "两端 API 配额耗尽|quota.*exhausted|resetAt"; then
  echo "  ⚠ 命中: 两端 API 配额耗尽"
  echo "    原因: 主+fallback relay 都达到限额"
  echo "    行为: 脚本自动 sleep 到 resetAt+10s 后重试，**不需要手动干预**"
  echo "    加速: Ctrl+C 后重跑（已发职位会通过 dedup_key 自动跳过）"
  echo
fi
# 兜底放行死循环 P1（2026-10-15 新增）—— grep 出现 ≥3 次同 v_key 兜底放行
DDC_COUNT=$(echo "$LOG" | grep -cE "visited_keys命中但job_visits无记录，兜底放行" || true)
if [[ "$DDC_COUNT" -ge 3 ]]; then
  echo "  ⚠ 命中: visited_keys 兜底放行死循环（**2026-10-15 新增 P1**）"
  echo "    出现次数: $DDC_COUNT"
  echo "    原因: 列表卡 RecyclerView 懒加载字段（company 短名/hr_name 空）"
  echo "          vs 详情页字段（全名/全填）不一致，DB 兜底查询精确匹配失败"
  echo "          → 误判「未访问」→ 兜底放行 → 同卡死循环评分"
  echo "    状态: **已修**（commits 48c34b4 + 12bff75），下次跑自动生效"
  echo "    当前跑: 只能 Ctrl+C 终止后重跑（已发会去重）"
  echo "    验证: 6 个 TDD 回归 case 全 PASS（tests/test_find_next_job_company_mismatch.py）"
  echo
fi
if echo "$LOG" | grep -qE "发送未确认|verify=False|verify_message_sent.*timeout"; then
  echo "  ⚠ 命中: 发送未确认（**false negative 高发**）"
  echo "    原因: verify_message_sent() 6s 轮询窗口内没抓到气泡"
  echo "    ⚠ 不要仅凭此判定失败！先到 App 手动确认消息是否真发出去"
  echo "    修复: 确认发了 → 加 --no-verify 重跑；没发 → 重跑"
  echo
fi
if echo "$LOG" | grep -qE "EmptyResponse|ThinkingBlock|AttributeError.*content"; then
  echo "  ⚠ 命中: LLM 返回空 / ThinkingBlock 兼容问题"
  echo "    原因: 模型返回 ThinkingBlock 而非 text block（已知 M2.7 有这问题）"
  echo "    修复: 改用 MiniMax-M3（实测可用）或 claude-3-5-haiku-20241022"
  echo
fi

# P2（边界情况）
echo "[P2] 边界情况（少见）"
if echo "$LOG" | grep -qE "DAILY_LIMIT|每日.*上限|今日.*达.*上限"; then
  echo "  ⚠ 命中: 每日沟通名额已用完"
  echo "    修复: 等次日 0 点（App 自动重置）"
  echo
fi
if echo "$LOG" | grep -qE "无法返回职位列表|_return_to_job_list.*失败"; then
  echo "  ⚠ 命中: 无法返回职位列表"
  echo "    原因: App 不在列表页（可能在聊天页/详情页/其他）"
  echo "    修复: 手动点回搜索页，重跑（已发会去重）"
  echo
fi
if echo "$LOG" | grep -qE "手机锁屏|锁屏未解"; then
  echo "  ⚠ 命中: 手机锁屏未解"
  echo "    行为: 脚本等 120s 让用户解锁"
  echo "    修复: 立即解锁手机"
  echo
fi
if echo "$LOG" | grep -qE "DeviceBusyError|device_lock.*占用"; then
  echo "  ⚠ 命中: 设备忙（互斥锁被占）"
  echo "    原因: 同时只能跑一个 smart_match_greet（device_lock 互斥）"
  echo "    修复: ps aux | grep smart_match_greet 找到进程，kill 后重跑"
  echo
fi
if echo "$LOG" | grep -qE "详情页加载超时|get_job_detail.*timeout"; then
  echo "  ⚠ 命中: 详情页加载超时（网络抖动）"
  echo "    行为: 自动跳过当前 job 继续"
  echo "    修复: 不需要修；如大面积出现检查网络"
  echo
fi
if echo "$LOG" | grep -qE "搜索失败|browse_jobs.*False"; then
  echo "  ⚠ 命中: 搜索失败"
  echo "    原因: 关键词无结果 / App 状态异常"
  echo "    修复: 看 output/debug_search_fail.xml；换关键词重试"
  echo
fi

echo
echo "=== 完整错误表见 references/error-fix-table.md ==="
