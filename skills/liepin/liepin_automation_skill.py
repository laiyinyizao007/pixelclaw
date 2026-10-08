"""
猎聘 (Liepin) Automation Skill

Provides high-level operations for the 猎聘 job-search app using
AndroidSkill as the base (with injected ADBManager for device control).

Key UX differences vs BOSS直聘 / 智联:
  - "直聊" / "与TA直聊" button triggers direct chat (like Zhilian's "先聊聊")
  - HR online status shown as activity text ("今日活跃" / "3天前活跃" / "本周活跃")
  - ELEMENTS resource-ids are placeholders — run Phase 0 ADB dump to verify:
      adb shell uiautomator dump /sdcard/liepin_list.xml
      adb pull /sdcard/liepin_list.xml .
    and replace all com.liepin.zhipin:id/* values in ELEMENTS below.
"""

import logging
import re
import shlex
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from skills.android.android_skill import AndroidSkill
from skills.android.ui_types import UIElement, parse_bounds


LIEPIN_PACKAGE = "com.liepin.zhipin"

_BADGE_TRAIL = re.compile(r"(?:（）|[\s￼])+$")


def normalize_card_title(title: str) -> str:
    """Strip badge suffix (empty parens, ORC, whitespace) and collapse all whitespace."""
    t = _BADGE_TRAIL.sub("", title or "").strip()
    t = t.replace("￼", "")
    return re.sub(r"\s+", " ", t).strip()


class PageState:
    HOME       = "home"
    JOB_LIST   = "job_list"
    JOB_DETAIL = "job_detail"
    CHAT       = "chat"
    SEARCH     = "search"
    UNKNOWN    = "unknown"


@dataclass
class JobInfo:
    """Parsed job listing entry from list or detail page."""

    title: str = ""
    company: str = ""
    company_scale: str = ""
    salary: str = ""
    location: str = ""
    hr_name: str = ""
    hr_title: str = ""
    hr_active: str = ""    # online status e.g. "今日活跃" / "3天前活跃"
    tap_x: int = 0
    tap_y: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


class LiepinAutomationSkill(AndroidSkill):
    """
    High-level automation skill for 猎聘.

    Args:
        adb_manager:  Injected ADBManager instance.
        output_dir:   Directory for saving screenshots.
        device_id:    Optional ADB device serial.
        action_delay: Seconds to wait between actions (default 1.0).

    NOTE: All ELEMENTS resource-ids are placeholders. Run Phase 0 ADB dump:
        adb shell uiautomator dump /sdcard/liepin_list.xml && adb pull /sdcard/liepin_list.xml .
    then update the ELEMENTS dict with real values before first use.
    """

    # ── PLACEHOLDER resource-ids (fill from Phase 0 ADB dump) ────────────────
    # Format: com.liepin.zhipin:id/<actual_id>
    # Run: adb shell uiautomator dump /sdcard/liepin_list.xml then inspect XML
    ELEMENTS: Dict[str, str] = {
        # ── List page ────────────────────────────────────────────────────────
        "job_list_rv":    f"{LIEPIN_PACKAGE}:id/job_list_rv",        # RecyclerView with scrollable=true
        "card_root":      f"{LIEPIN_PACKAGE}:id/card_item_root",      # card container (clickable)
        "job_name":       f"{LIEPIN_PACKAGE}:id/tv_job_title",        # job title in card
        "job_salary":     f"{LIEPIN_PACKAGE}:id/tv_salary",           # salary range in card
        "company_name":   f"{LIEPIN_PACKAGE}:id/tv_company_name",     # company name in card
        "company_scale":  f"{LIEPIN_PACKAGE}:id/tv_company_scale",    # company size
        "hr_name":        f"{LIEPIN_PACKAGE}:id/tv_hr_name",          # HR name in card
        "hr_active":      f"{LIEPIN_PACKAGE}:id/tv_hr_active",        # "今日活跃" / "3天前活跃"
        "location":       f"{LIEPIN_PACKAGE}:id/tv_location",         # job location in card
        # ── Search ───────────────────────────────────────────────────────────
        "search_bar":     f"{LIEPIN_PACKAGE}:id/search_input",        # search bar / trigger area
        "search_hint":    f"{LIEPIN_PACKAGE}:id/tv_search_hint",
        # ── Detail page ──────────────────────────────────────────────────────
        "job_name_detail":  f"{LIEPIN_PACKAGE}:id/tv_job_title_detail",
        "salary_detail":    f"{LIEPIN_PACKAGE}:id/tv_salary_detail",
        "experience_detail": f"{LIEPIN_PACKAGE}:id/tv_experience",
        "location_detail":  f"{LIEPIN_PACKAGE}:id/tv_location_detail",
        "company_detail":   f"{LIEPIN_PACKAGE}:id/tv_company_detail",
        "company_scale_detail": f"{LIEPIN_PACKAGE}:id/tv_company_scale_detail",
        "hr_name_detail":   f"{LIEPIN_PACKAGE}:id/tv_hr_name_detail",
        "hr_title_detail":  f"{LIEPIN_PACKAGE}:id/tv_hr_title_detail",
        "hr_active_detail": f"{LIEPIN_PACKAGE}:id/tv_hr_active_detail",
        "job_desc":         f"{LIEPIN_PACKAGE}:id/tv_job_desc",        # JD full text
        "chat_btn":         f"{LIEPIN_PACKAGE}:id/btn_chat",           # "直聊" / "与TA直聊"
        "apply_btn":        f"{LIEPIN_PACKAGE}:id/btn_apply",          # "投递简历"
        # ── Chat page ────────────────────────────────────────────────────────
        "chat_safe_close":  f"{LIEPIN_PACKAGE}:id/chat_safety_close",  # anti-fraud overlay close
        "chat_input":       f"{LIEPIN_PACKAGE}:id/chat_input",         # EditText for typing
        "chat_send":        f"{LIEPIN_PACKAGE}:id/btn_send",           # send button
        # ── Launch safety popup ──────────────────────────────────────────────
        "safety_ok":        f"{LIEPIN_PACKAGE}:id/btn_know",           # 我知道了 / 确定
    }

    APP_PACKAGE = LIEPIN_PACKAGE
    _ADB_IME = "com.android.adbkeyboard/.AdbIME"

    def __init__(
        self,
        adb_manager,
        output_dir: str = "",
        device_id: Optional[str] = None,
        action_delay: float = 1.0,
    ):
        super().__init__(
            device_id=device_id,
            adb=adb_manager,
            output_dir=output_dir or str(Path(tempfile.gettempdir()) / "pixelclaw_liepin"),
            action_delay=action_delay,
        )

    # ------------------------------------------------------------------
    # UI hierarchy (always fresh)
    # ------------------------------------------------------------------

    def get_ui_hierarchy(self, force_refresh: bool = False) -> str:  # noqa: ARG002
        return super().get_ui_hierarchy(force_refresh=True)

    # ------------------------------------------------------------------
    # Page state detection
    # ------------------------------------------------------------------

    def get_current_page(self, xml: Optional[str] = None) -> str:
        if xml is None:
            xml = self.get_ui_hierarchy()
        if not xml:
            return PageState.UNKNOWN

        # Chat page: safety overlay or chat send area present
        if "chat_safety_close" in xml or "chat_input" in xml:
            return PageState.CHAT
        # Also detect chat page by common chat view identifiers
        if "messageActivityLayout" in xml or "messageListView" in xml:
            return PageState.CHAT

        # Detail page: chat/apply button or job title detail present
        if "btn_chat" in xml or "btn_apply" in xml:
            return PageState.JOB_DETAIL
        if "tv_job_title_detail" in xml:
            return PageState.JOB_DETAIL

        # Job list page: RecyclerView or filter bar
        if "job_list_rv" in xml:
            return PageState.JOB_LIST
        if "tv_job_title" in xml and "tv_company_name" in xml:
            return PageState.JOB_LIST

        # Search overlay
        if "search_input" in xml:
            return PageState.SEARCH

        return PageState.UNKNOWN

    # ------------------------------------------------------------------
    # App launch helpers
    # ------------------------------------------------------------------

    def launch_app(self) -> bool:
        """Launch 猎聘 and dismiss the safety popup if it appears."""
        self._logger.info("[launch_app] 启动猎聘")
        ok, _ = self._adb(f"shell monkey -p {LIEPIN_PACKAGE} -c android.intent.category.LAUNCHER 1")
        time.sleep(3.0)
        return self._dismiss_safety_popup()

    def _dismiss_safety_popup(self) -> bool:
        """Dismiss the 求职安全提醒 popup if present."""
        xml = self.get_ui_hierarchy()
        # Try dedicated safety_ok button first
        elem = self.find_element(resource_id=self.ELEMENTS["safety_ok"], xml=xml)
        if elem and elem.center:
            self._logger.info("[launch_app] 关闭安全弹窗")
            self.tap(*elem.center)
            time.sleep(1.0)
            return True
        # Fallback: generic dismiss buttons
        for btn_text in ("我知道了", "确定", "关闭", "跳过", "以后再说"):
            btn = self.find_element(text=btn_text, xml=xml)
            if btn and btn.center:
                self._logger.info("[launch_app] 关闭弹窗「%s」", btn_text)
                self.tap(*btn.center)
                time.sleep(1.0)
                return True
        return True

    def ensure_ready(self) -> bool:
        """Ensure the app is on the job list page. Launch if needed."""
        xml = self.get_ui_hierarchy()
        page = self.get_current_page(xml)
        if page == PageState.JOB_LIST:
            return True
        if page in (PageState.JOB_DETAIL, PageState.CHAT, PageState.SEARCH):
            for _ in range(3):
                self.press_back()
                time.sleep(1.0)
                page = self.get_current_page()
                if page == PageState.JOB_LIST:
                    return True
        return self.launch_app()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def browse_jobs(self, keyword: str) -> bool:
        """Tap the search bar, enter keyword, and submit to get filtered job list."""
        self._logger.info("[browse_jobs] → 搜索关键词: %s", keyword)

        if not self.tap_element("search_bar"):
            self._logger.warning("[browse_jobs] 找不到搜索栏，尝试直接输入")
            return False
        time.sleep(1.5)

        self._adb("shell input keyevent KEYCODE_CTRL_A")
        time.sleep(0.2)
        if not self.type_text(keyword):
            return False
        self._adb("shell input keyevent 66")   # Enter
        time.sleep(2.0)

        # Wait for list to load
        deadline = time.time() + 18.0
        while time.time() < deadline:
            xml = self.get_ui_hierarchy()
            page = self.get_current_page(xml)
            if page == PageState.JOB_LIST:
                self._logger.info("[browse_jobs] ← 搜索结果已加载")
                return True
            time.sleep(1.2)
        self._logger.warning("[browse_jobs] ← 超时：搜索结果未出现")
        return False

    # ------------------------------------------------------------------
    # Job list parsing
    # ------------------------------------------------------------------

    def get_job_list(self, xml: Optional[str] = None) -> List[JobInfo]:
        """Parse job cards visible on the current list page."""
        if xml is None:
            xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
            return []

        target_rid = self.ELEMENTS["job_name"]
        jobs: List[JobInfo] = []
        parent_map = self._build_parent_map(root)

        for node in root.iter("node"):
            if node.attrib.get("resource-id", "") != target_rid:
                continue
            title = node.attrib.get("text", "").strip()
            if not title:
                continue

            bounds = parse_bounds(node.attrib.get("bounds", ""))
            tap_x = (bounds[0] + bounds[2]) // 2 if bounds else 0
            tap_y = (bounds[1] + bounds[3]) // 2 if bounds else 0

            # Walk up to card_root container (up to 6 levels)
            card = node
            for _ in range(6):
                parent = parent_map.get(card)
                if parent is None:
                    break
                card = parent
                if "card_item_root" in parent.attrib.get("resource-id", ""):
                    break

            company = company_scale = salary = location = hr_name = hr_title = hr_active = ""
            for sibling in card.iter("node"):
                srid = sibling.attrib.get("resource-id", "")
                stext = sibling.attrib.get("text", "").strip()
                if not stext or srid == target_rid:
                    continue
                if "tv_company_name" in srid:
                    company = stext
                elif "tv_company_scale" in srid:
                    company_scale = stext
                elif "tv_salary" in srid and "detail" not in srid:
                    salary = stext
                elif "tv_location" in srid and "detail" not in srid:
                    location = stext
                elif "tv_hr_name" in srid:
                    hr_name = stext
                elif "tv_hr_title" in srid or "tv_hr_job" in srid:
                    hr_title = stext
                elif "tv_hr_active" in srid:
                    hr_active = stext

            jobs.append(JobInfo(
                title=title,
                company=company,
                company_scale=company_scale,
                salary=salary,
                location=location,
                hr_name=hr_name,
                hr_title=hr_title,
                hr_active=hr_active,
                tap_x=tap_x,
                tap_y=tap_y,
            ))
        return jobs

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_to_job(self, job: JobInfo) -> bool:
        """Tap a job card using coordinates captured by get_job_list()."""
        self._logger.info("[navigate_to_job] → %s @ (%d, %d)", job.title, job.tap_x, job.tap_y)
        if job.tap_x or job.tap_y:
            return self.tap(job.tap_x, job.tap_y)
        return self.tap_element("job_name")

    def return_to_job_list(self) -> bool:
        """Press back until we reach the job list page."""
        for _ in range(3):
            page = self.get_current_page()
            if page == PageState.JOB_LIST:
                return True
            self.press_back()
            time.sleep(1.0)
        return self.get_current_page() == PageState.JOB_LIST

    # ------------------------------------------------------------------
    # Detail page
    # ------------------------------------------------------------------

    def get_job_detail(self, xml: Optional[str] = None) -> Dict[str, Any]:
        """
        Extract structured fields from the current detail page.

        Returns a dict with keys:
            title, salary, experience, location, company, company_scale,
            hr_name, hr_title, hr_active, description
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
            return {}

        detail: Dict[str, Any] = {
            "title": "", "salary": "", "experience": "",
            "location": "", "company": "", "company_scale": "",
            "hr_name": "", "hr_title": "", "hr_active": "",
            "description": "",
        }

        desc_candidates: List[Tuple[str, int]] = []

        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            text = node.attrib.get("text", "").strip()

            if "tv_job_title_detail" in rid and text:
                detail["title"] = text
            elif "tv_salary_detail" in rid and text:
                detail["salary"] = text
            elif "tv_experience" in rid and text:
                detail["experience"] = text
            elif "tv_location_detail" in rid and text:
                detail["location"] = text
            elif "tv_company_detail" in rid and text:
                detail["company"] = text
            elif "tv_company_scale_detail" in rid and text:
                detail["company_scale"] = text
            elif "tv_hr_name_detail" in rid and text and not detail["hr_name"]:
                detail["hr_name"] = text
            elif ("tv_hr_title_detail" in rid or "tv_hr_job" in rid) and text:
                detail["hr_title"] = text
            elif "tv_hr_active_detail" in rid and text:
                detail["hr_active"] = text
            elif "tv_job_desc" in rid and text:
                desc_candidates.append((text, len(text)))

        if desc_candidates:
            detail["description"] = max(desc_candidates, key=lambda x: x[1])[0]

        return detail

    def is_already_chatted(self, xml: Optional[str] = None) -> bool:
        """
        Return True if the chat button shows a state indicating prior contact.

        Liepin uses "直聊" / "与TA直聊" for new contact;
        may show "继续沟通" / "查看简历" after prior contact.
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        elem = self.find_element(resource_id=self.ELEMENTS["chat_btn"], xml=xml)
        if elem is None:
            return False
        return (elem.text or "").strip() not in ("直聊", "与TA直聊", "聊一聊", "")

    # ------------------------------------------------------------------
    # Greeting
    # ------------------------------------------------------------------

    def send_greeting(self, message: str = "") -> bool:
        """
        Tap the chat button ("直聊") to initiate direct chat, then optionally
        send a personalized message via ADBKeyboard broadcast.

        Returns True if the chat page was reached (greeting initiated),
        regardless of whether the optional custom message succeeded.
        """
        xml = self.get_ui_hierarchy()
        btn = self.find_element(resource_id=self.ELEMENTS["chat_btn"], xml=xml)
        if btn is None or not btn.center:
            # Fallback: try finding by text
            for btn_text in ("直聊", "与TA直聊", "聊一聊"):
                btn = self.find_element(text=btn_text, xml=xml)
                if btn and btn.center:
                    break
        if btn is None or not btn.center:
            self._logger.warning("[send_greeting] ← 未找到直聊按钮")
            return False

        btn_text = (btn.text or "").strip()
        if btn_text not in ("直聊", "与TA直聊", "聊一聊", ""):
            self._logger.info("[send_greeting] ← 按钮文本「%s」，非直聊，跳过", btn_text)
            return False

        self._logger.info("[send_greeting] → 点击直聊")
        self.tap(*btn.center)
        time.sleep(2.5)

        page = self.get_current_page()
        if page != PageState.CHAT:
            _dbg_dir = str(Path(tempfile.gettempdir()) / "pixelclaw_liepin")
            Path(_dbg_dir).mkdir(parents=True, exist_ok=True)
            _dbg_path = str(Path(_dbg_dir) / "debug_send_greeting_fail.xml")
            try:
                _xml_raw = self.get_ui_hierarchy()
                Path(_dbg_path).write_text(_xml_raw or "", encoding="utf-8")
                self._logger.warning("[send_greeting] ← 调试 XML 已保存至 %s", _dbg_path)
            except Exception as _e:
                self._logger.warning("[send_greeting] ← 调试 XML 保存失败: %s", _e)
            self._logger.warning("[send_greeting] ← 点击后未进入聊天页 (page=%s)", page)
            return False

        self._logger.info("[send_greeting] ← 已进入聊天页")
        self._dismiss_chat_safety_overlay()

        if message:
            self._send_chat_message(message)

        self.press_back()
        time.sleep(1.0)
        return True

    def _send_chat_message(self, message: str) -> bool:
        """Type and send a message in the open chat page using ADBKeyboard."""
        self._logger.info("[send_chat_message] → 发送个性化消息（%d 字）", len(message))

        _, prev_ime = self._adb("shell settings get secure default_input_method")
        prev_ime = (prev_ime or "").strip()

        self._adb(f"shell ime enable {self._ADB_IME}")
        self._adb(f"shell ime set {self._ADB_IME}")
        time.sleep(0.5)

        if not self.tap_element("chat_input"):
            self._logger.warning("[send_chat_message] ← 找不到 chat_input，跳过个性化消息")
            if prev_ime:
                self._adb(f"shell ime set {prev_ime}")
            return False
        time.sleep(2.5)

        safe_text = shlex.quote(message)
        _bcast_cmd = (
            f"am broadcast -p com.android.adbkeyboard"
            f" -a ADB_INPUT_TEXT --es msg {safe_text}"
        )
        _bcast_args = ["adb"]
        if self.device_id:
            _bcast_args += ["-s", self.device_id]
        _bcast_args += ["shell", _bcast_cmd]
        subprocess.run(
            _bcast_args,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        time.sleep(1.5)

        xml_after = self.get_ui_hierarchy(force_refresh=True)
        input_elem = self.find_element(resource_id=self.ELEMENTS["chat_input"], xml=xml_after)
        typed_text = (input_elem.text if input_elem else "") or ""
        msg_start = message[:6]
        if msg_start not in typed_text:
            self._logger.warning(
                "[send_chat_message] ← 文字未入框（期望「%s」，实际「%s」）", msg_start, typed_text[:20]
            )
            if prev_ime:
                self._adb(f"shell ime set {prev_ime}")
            return False

        send_elem = self.find_element(resource_id=self.ELEMENTS["chat_send"], xml=xml_after)
        if send_elem is None or not send_elem.center:
            # Try by text fallback
            send_elem = self.find_element(text="发送", xml=xml_after)
        if send_elem is None or not send_elem.center:
            self._logger.warning("[send_chat_message] ← 找不到发送按钮")
            if prev_ime:
                self._adb(f"shell ime set {prev_ime}")
            return False

        self.tap(*send_elem.center)
        time.sleep(1.0)
        self._logger.info("[send_chat_message] ← 个性化消息发送成功")

        if prev_ime:
            self._adb(f"shell ime set {prev_ime}")
        return True

    def _dismiss_chat_safety_overlay(self) -> None:
        """Dismiss the anti-fraud safety overlay in the chat page."""
        xml = self.get_ui_hierarchy()
        close_btn = self.find_element(resource_id=self.ELEMENTS["chat_safe_close"], xml=xml)
        if close_btn and close_btn.center:
            self._logger.info("[chat] 关闭安全提示浮层")
            self.tap(*close_btn.center)
            time.sleep(0.8)
            return
        # Try generic dismiss
        for btn_text in ("我知道了", "确定", "关闭"):
            btn = self.find_element(text=btn_text, xml=xml)
            if btn and btn.center:
                self._logger.info("[chat] 关闭安全弹窗「%s」", btn_text)
                self.tap(*btn.center)
                time.sleep(0.8)
                return

    # ------------------------------------------------------------------
    # Screen helpers
    # ------------------------------------------------------------------

    def _is_screen_on(self) -> bool:
        ok, out = self._adb("shell dumpsys power")
        return ok and "mWakefulness=Awake" in out

    def _wake_screen(self) -> None:
        self._adb("shell input keyevent 224")
        time.sleep(0.5)
        self._adb("shell wm dismiss-keyguard")
        time.sleep(0.5)

    # ------------------------------------------------------------------
    # Tutorial overlay
    # ------------------------------------------------------------------

    def dismiss_tutorial_if_present(self, xml: Optional[str] = None) -> None:
        """Dismiss the first-time tutorial overlay on detail entry."""
        if xml is None:
            xml = self.get_ui_hierarchy()
        for btn_text in ("我知道了", "知道了", "跳过"):
            btn = self.find_element(text=btn_text, xml=xml)
            if btn and btn.center:
                self._logger.info("[detail] 关闭新手引导「%s」", btn_text)
                self.tap(*btn.center)
                time.sleep(0.5)
                return

    # ------------------------------------------------------------------
    # Scrolling
    # ------------------------------------------------------------------

    def scroll_job_list(
        self,
        n_jobs: int,
        max_scrolls: int = 20,
        screen_height: int = 2400,
    ) -> List[JobInfo]:
        """Scroll the 猎聘 job list until n_jobs unique entries are collected."""
        all_jobs: List[JobInfo] = []
        seen: Dict[str, JobInfo] = {}
        scrolls = 0
        max_stale = 4
        stale = 0
        prev_xml = ""

        while len(all_jobs) < n_jobs and scrolls < max_scrolls:
            xml = self.get_ui_hierarchy()
            page_jobs = self.get_job_list(xml=xml)

            new_found = 0
            for job in page_jobs:
                norm_title = normalize_card_title(job.title)
                job.title = norm_title
                hr = job.hr_name or ""
                comp = job.company or ""
                key = f"{norm_title}\t{hr}" if hr else f"{norm_title}\t\t{comp}"
                if not key.strip("\t"):
                    continue

                known = seen.get(key)
                if known is None:
                    seen[key] = job
                    all_jobs.append(job)
                    new_found += 1
                else:
                    for attr in ("company", "company_scale", "salary",
                                 "location", "hr_name", "hr_title", "hr_active"):
                        if not getattr(known, attr) and getattr(job, attr):
                            setattr(known, attr, getattr(job, attr))

            if new_found == 0:
                if xml == prev_xml:
                    stale += 1
                    if stale >= max_stale:
                        self._logger.info("[scroll] XML 连续 %d 次不变，到达列表底部", stale)
                        break
                    time.sleep(1.0)
                    continue
                stale = 0
            else:
                stale = 0
            prev_xml = xml

            if len(all_jobs) >= n_jobs:
                break

            start_y = int(screen_height * 0.75)
            end_y   = int(screen_height * 0.25)
            self.swipe(540, start_y, 540, end_y, 500)
            time.sleep(self.action_delay)
            scrolls += 1

        self._logger.info("[scroll] 采集完成: %d 条 (滑动 %d 次)", len(all_jobs), scrolls)
        return all_jobs[:n_jobs]
