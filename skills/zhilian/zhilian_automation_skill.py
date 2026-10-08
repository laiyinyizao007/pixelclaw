"""
智联招聘 (Zhilian Zhaopin) Automation Skill

Provides high-level operations for the 智联招聘 job-search app using
AndroidSkill as the base (with injected ADBManager for device control).

Key UX differences vs BOSS直聘:
  - "先聊聊" auto-sends a default greeting immediately (no dialog to fill)
  - HR response shown as frequency: "今日回复50+次" / "34分钟前回复"
  - Cards have a "v_delete" dismiss area (right edge, ~[896,*][1027,*])
  - Safety popup on launch: tv_ok at ~(534, 1654)
  - Tutorial overlay on first detail entry: tap (540, 1140) to dismiss
"""

import logging
import re
import shlex
import subprocess
import time
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from skills.android.android_skill import AndroidSkill
from skills.android.ui_types import UIElement, parse_bounds


ZHILIAN_PACKAGE = "com.zhaopin.social"

_BADGE_TRAIL = re.compile(r"(?:（）|[\s￼])+$")


def normalize_card_title(title: str) -> str:
    """Strip badge suffix (empty parens, ORC, whitespace) and collapse all whitespace."""
    t = _BADGE_TRAIL.sub("", title or "").strip()
    t = t.replace("￼", "")  # remove ORC (U+FFFC) from anywhere after suffix strip
    return re.sub(r"\s+", " ", t).strip()


class PageState:
    HOME         = "home"
    JOB_LIST     = "job_list"
    JOB_DETAIL   = "job_detail"
    CHAT         = "chat"
    SEARCH       = "search"
    UNKNOWN      = "unknown"


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
    hr_status: str = ""    # response frequency e.g. "今日回复50+次"
    tap_x: int = 0
    tap_y: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


class ZhilianAutomationSkill(AndroidSkill):
    """
    High-level automation skill for 智联招聘.

    Args:
        adb_manager:  Injected ADBManager instance.
        output_dir:   Directory for saving screenshots.
        device_id:    Optional ADB device serial.
        action_delay: Seconds to wait between actions (default 1.0).
    """

    # Verified resource-ids from Pixel 8a UIAutomator dump (com.zhaopin.social)
    ELEMENTS: Dict[str, str] = {
        # ── List page ────────────────────────────────────────────────────
        "job_list_rv":    f"{ZHILIAN_PACKAGE}:id/rv_position_recommend_new",
        "card_root":      f"{ZHILIAN_PACKAGE}:id/cl_root",
        "job_name":       f"{ZHILIAN_PACKAGE}:id/tv_position_name",
        "job_salary":     f"{ZHILIAN_PACKAGE}:id/tv_position_salary",
        "company_name":   f"{ZHILIAN_PACKAGE}:id/tv_company_name",
        "company_scale":  f"{ZHILIAN_PACKAGE}:id/tv_company_scale",
        "hr_name":        f"{ZHILIAN_PACKAGE}:id/tv_hr_name",
        "hr_job":         f"{ZHILIAN_PACKAGE}:id/tv_hr_job",
        "hr_status":      f"{ZHILIAN_PACKAGE}:id/tv_hr_status",   # "今日回复50+次"
        "location":       f"{ZHILIAN_PACKAGE}:id/tv_location",
        "card_delete":    f"{ZHILIAN_PACKAGE}:id/v_delete",
        # ── Search ───────────────────────────────────────────────────────
        "search_bar":     f"{ZHILIAN_PACKAGE}:id/ll_search",
        "search_hint":    f"{ZHILIAN_PACKAGE}:id/tv_search",
        # ── Detail page ──────────────────────────────────────────────────
        "job_name_detail": f"{ZHILIAN_PACKAGE}:id/tv_job_name_new",
        "salary_detail":   f"{ZHILIAN_PACKAGE}:id/tv_salary_new",
        "tag":             f"{ZHILIAN_PACKAGE}:id/tv_tag",          # experience / headcount / date
        "location_detail": f"{ZHILIAN_PACKAGE}:id/tv_street",
        "hr_name_detail":  f"{ZHILIAN_PACKAGE}:id/tv_name",
        "hr_desc":         f"{ZHILIAN_PACKAGE}:id/tv_desc",         # response freq + JD (multi)
        "hr_job_detail":   f"{ZHILIAN_PACKAGE}:id/tv_hr_job",
        "hr_company":      f"{ZHILIAN_PACKAGE}:id/tv_hr_comany",   # "· 可利邦"
        "chat_btn":        f"{ZHILIAN_PACKAGE}:id/tv_before_chat_small",   # "先聊聊"
        "apply_btn":       f"{ZHILIAN_PACKAGE}:id/tv_before_deliver",      # "立即投递"
        # ── Chat page ────────────────────────────────────────────────────
        "chat_safe_close": f"{ZHILIAN_PACKAGE}:id/ll_safe_dialog_close",
        "chat_safe_bg":    f"{ZHILIAN_PACKAGE}:id/rl_safe_dialog_top",
        "chat_input":      f"{ZHILIAN_PACKAGE}:id/editTextMessage",
        "chat_send":       f"{ZHILIAN_PACKAGE}:id/sendLayout",
        # ── Launch safety popup ──────────────────────────────────────────
        "safety_ok":       f"{ZHILIAN_PACKAGE}:id/tv_ok",
    }

    APP_PACKAGE = ZHILIAN_PACKAGE
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
            output_dir=output_dir or str(Path(tempfile.gettempdir()) / "pixelclaw_zhilian"),
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

        # Chat page: has the chat safety overlay OR chat send area
        if "rl_safe_dialog_top" in xml or "ll_safe_dialog_close" in xml:
            return PageState.CHAT
        # Also detect chat page by "view_placeholder" (chat background)
        if f"{ZHILIAN_PACKAGE}:id/view_placeholder" in xml:
            return PageState.CHAT
        # Chat page (NIM-based): message list + send area
        if "messageActivityLayout" in xml or "messageListView" in xml:
            return PageState.CHAT

        # Detail page: has "先聊聊" or "立即投递" action buttons
        if "tv_before_chat_small" in xml or "tv_before_deliver" in xml:
            return PageState.JOB_DETAIL

        # Detail page fallback: has job name detail field
        if "tv_job_name_new" in xml:
            return PageState.JOB_DETAIL

        # Job list page: home-feed RecyclerView OR search-results filter bar
        if "rv_position_recommend_new" in xml or "cl_filter_view" in xml:
            return PageState.JOB_LIST

        # Search overlay: focused search input
        if "ll_search" in xml and "tv_search" in xml:
            return PageState.SEARCH

        return PageState.UNKNOWN

    # ------------------------------------------------------------------
    # App launch helpers
    # ------------------------------------------------------------------

    def launch_app(self) -> bool:
        """Launch 智联招聘 and dismiss the safety popup if it appears."""
        self._logger.info("[launch_app] 启动智联招聘")
        ok, _ = self._adb(f"shell monkey -p {ZHILIAN_PACKAGE} -c android.intent.category.LAUNCHER 1")
        time.sleep(3.0)
        return self._dismiss_safety_popup()

    def _dismiss_safety_popup(self) -> bool:
        """Dismiss the 求职安全提醒 popup if present."""
        xml = self.get_ui_hierarchy()
        elem = self.find_element(resource_id=self.ELEMENTS["safety_ok"], xml=xml)
        if elem and elem.center:
            self._logger.info("[launch_app] 关闭安全弹窗")
            self.tap(*elem.center)
            time.sleep(1.0)
        return True

    def ensure_ready(self) -> bool:
        """Ensure the app is on the job list page. Launch if needed."""
        xml = self.get_ui_hierarchy()
        page = self.get_current_page(xml)
        if page == PageState.JOB_LIST:
            return True
        if page in (PageState.JOB_DETAIL, PageState.CHAT, PageState.SEARCH):
            # Press back until we reach the list
            for _ in range(3):
                self.press_back()
                time.sleep(1.0)
                page = self.get_current_page()
                if page == PageState.JOB_LIST:
                    return True
        # Try launching
        return self.launch_app()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def browse_jobs(self, keyword: str) -> bool:
        """
        Tap the search bar, enter keyword, and submit to get filtered job list.
        """
        self._logger.info("[browse_jobs] → 搜索关键词: %s", keyword)

        # Tap search bar
        if not self.tap_element("search_bar"):
            self._logger.warning("[browse_jobs] 找不到搜索栏")
            return False
        time.sleep(1.5)

        # Clear and type keyword
        self._adb("shell input keyevent KEYCODE_CTRL_A")
        time.sleep(0.2)
        if not self.type_text(keyword):
            return False
        self._adb("shell input keyevent 66")   # Enter
        time.sleep(2.0)

        # Wait for list to load (search results page has cl_filter_view + job cards)
        deadline = time.time() + 18.0
        while time.time() < deadline:
            xml = self.get_ui_hierarchy()
            # cl_filter_view = search-results filter bar; tv_position_name = first job card
            if "cl_filter_view" in xml and "tv_position_name" in xml:
                self._logger.info("[browse_jobs] ← 搜索结果已加载")
                return True
            # Accept if filter bar appeared even without cards yet (cards load on scroll)
            if "cl_filter_view" in xml and time.time() > deadline - 6.0:
                self._logger.info("[browse_jobs] ← 搜索页已就绪（无职位卡片，可能需要滚动）")
                return True
            time.sleep(1.2)
        self._logger.warning("[browse_jobs] ← 超时：搜索结果未出现")
        return False

    # ------------------------------------------------------------------
    # Job list parsing
    # ------------------------------------------------------------------

    def get_job_list(self, xml: Optional[str] = None) -> List[JobInfo]:
        """
        Parse job cards visible on the current list page.

        Each card is rooted at cl_root (clickable). Fields are collected
        by traversing sibling nodes within the card subtree.
        """
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

            # Walk up to cl_root card container (up to 6 levels)
            card = node
            for _ in range(6):
                parent = parent_map.get(card)
                if parent is None:
                    break
                card = parent
                if "cl_root" in parent.attrib.get("resource-id", ""):
                    break

            company = company_scale = salary = location = hr_name = hr_title = hr_status = ""
            for sibling in card.iter("node"):
                srid = sibling.attrib.get("resource-id", "")
                stext = sibling.attrib.get("text", "").strip()
                if not stext or srid == target_rid:
                    continue
                if "tv_company_name" in srid:
                    company = stext
                elif "tv_company_scale" in srid:
                    company_scale = stext
                elif "tv_position_salary" in srid:
                    salary = stext
                elif "tv_location" in srid:
                    location = stext
                elif "tv_hr_name" in srid:
                    hr_name = stext
                elif "tv_hr_job" in srid:
                    hr_title = stext
                elif "tv_hr_status" in srid:
                    hr_status = stext

            jobs.append(JobInfo(
                title=title,
                company=company,
                company_scale=company_scale,
                salary=salary,
                location=location,
                hr_name=hr_name,
                hr_title=hr_title,
                hr_status=hr_status,
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
            title, salary, experience, location, headcount, published,
            hr_name, hr_title, hr_company, hr_response, description
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
            return {}

        detail: Dict[str, Any] = {
            "title": "", "salary": "", "experience": "",
            "location": "", "headcount": "", "published": "",
            "hr_name": "", "hr_title": "", "hr_company": "",
            "hr_response": "", "description": "",
        }

        desc_nodes: List[str] = []   # collect all tv_desc texts

        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            text = node.attrib.get("text", "").strip()

            if "tv_job_name_new" in rid:
                detail["title"] = text
            elif "tv_salary_new" in rid:
                detail["salary"] = text
            elif "tv_tag" in rid and text:
                # Tags: experience (N年 / 应届), headcount (招N人), date (N月N日)
                if re.search(r"年|应届", text):
                    detail["experience"] = text
                elif re.search(r"招\d+人", text):
                    detail["headcount"] = text
                elif re.search(r"\d+月\d+日", text):
                    detail["published"] = text
            elif "tv_street" in rid:
                detail["location"] = text
            elif "tv_name" in rid and not detail["hr_name"]:
                detail["hr_name"] = text
            elif "tv_hr_job" in rid:
                detail["hr_title"] = text
            elif "tv_hr_comany" in rid:
                detail["hr_company"] = text.lstrip("· ·").strip()
            elif "tv_desc" in rid and text:
                desc_nodes.append(text)

        # First tv_desc is HR response frequency; last/longest is the JD content
        if desc_nodes:
            detail["hr_response"] = desc_nodes[0]
            # JD is the longest text node
            jd_text = max(desc_nodes, key=len)
            if len(jd_text) > 30:
                detail["description"] = jd_text

        return detail

    def is_already_chatted(self, xml: Optional[str] = None) -> bool:
        """
        Return True if the chat button shows a state indicating prior contact.

        Currently uses absence of "先聊聊" text as the signal — when already
        chatted, the button may show "继续聊聊" or similar.
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        elem = self.find_element(resource_id=self.ELEMENTS["chat_btn"], xml=xml)
        if elem is None:
            return False
        return (elem.text or "").strip() not in ("先聊聊", "")

    # ------------------------------------------------------------------
    # Greeting
    # ------------------------------------------------------------------

    def send_greeting(self, message: str = "") -> bool:
        """
        Tap "先聊聊" to send the default greeting, then optionally send a
        personalized follow-up message in the chat input.

        Zhilian immediately sends the default greeting upon button tap.
        If `message` is provided, it is typed and sent as a second message
        in the same chat using ADBKeyboard broadcast (no visible soft keyboard).

        Returns True if the default greeting was sent (chat page reached),
        regardless of whether the optional custom message succeeded.
        """
        xml = self.get_ui_hierarchy()
        btn = self.find_element(resource_id=self.ELEMENTS["chat_btn"], xml=xml)
        if btn is None or not btn.center:
            self._logger.warning("[send_greeting] ← 未找到先聊聊按钮")
            return False

        btn_text = (btn.text or "").strip()
        if btn_text not in ("先聊聊", ""):
            self._logger.info("[send_greeting] ← 按钮文本「%s」，非先聊聊，跳过", btn_text)
            return False

        self._logger.info("[send_greeting] → 点击先聊聊")
        self.tap(*btn.center)
        time.sleep(2.5)

        # Check if we landed on chat page (default greeting auto-sent by app)
        page = self.get_current_page()
        if page != PageState.CHAT:
            # Fallback: save debug XML to identify unexpected dialog or page state
            import os as _os
            _dbg_dir = _os.path.join(tempfile.gettempdir(), "pixelclaw_zhilian")
            _os.makedirs(_dbg_dir, exist_ok=True)
            _dbg_path = _os.path.join(_dbg_dir, "debug_send_greeting_fail.xml")
            try:
                _xml_raw = self.get_ui_hierarchy()
                with open(_dbg_path, "w", encoding="utf-8") as _f:
                    _f.write(_xml_raw or "")
                self._logger.warning("[send_greeting] ← 调试 XML 已保存至 %s", _dbg_path)
            except Exception as _e:
                self._logger.warning("[send_greeting] ← 调试 XML 保存失败: %s", _e)
            self._logger.warning("[send_greeting] ← 点击后未进入聊天页 (page=%s)", page)
            return False

        self._logger.info("[send_greeting] ← 默认招呼已发，已进入聊天页")
        self._dismiss_chat_safety_overlay()

        # Send personalized follow-up message if provided
        if message:
            self._send_chat_message(message)

        self.press_back()
        time.sleep(1.0)
        return True

    def _send_chat_message(self, message: str) -> bool:
        """Type and send a message in the open NIM chat page using ADBKeyboard."""
        self._logger.info("[send_chat_message] → 发送个性化消息（%d 字）", len(message))

        _, prev_ime = self._adb("shell settings get secure default_input_method")
        prev_ime = (prev_ime or "").strip()

        # Switch to ADBKeyboard before focusing the EditText to avoid layout shifts
        self._adb(f"shell ime enable {self._ADB_IME}")
        self._adb(f"shell ime set {self._ADB_IME}")
        time.sleep(0.5)

        if not self.tap_element("chat_input"):
            self._logger.warning("[send_chat_message] ← 找不到 editTextMessage，跳过个性化消息")
            if prev_ime:
                self._adb(f"shell ime set {prev_ime}")
            return False
        time.sleep(2.5)

        # Broadcast the message text via ADBKeyboard (preserves Unicode and spaces)
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

        # Verify the text actually landed in the EditText
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

        # Tap the send button
        send_elem = self.find_element(resource_id=self.ELEMENTS["chat_send"], xml=xml_after)
        if send_elem is None or not send_elem.center:
            self._logger.warning("[send_chat_message] ← 找不到 sendLayout")
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
        if "左右滑动即可查看更多职位" in xml:
            self._logger.info("[detail] 关闭新手引导")
            self.tap(540, 1140)
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # Scrolling
    # ------------------------------------------------------------------

    def scroll_job_list(
        self,
        n_jobs: int,
        max_scrolls: int = 20,
        screen_height: int = 2400,
    ) -> List[JobInfo]:
        """
        Scroll the Zhilian job list until n_jobs unique entries are collected.

        Uses ADB swipe (not uiautomator2) so there is no extra dependency.
        Dedup key: title + hr_name (or title + company when hr_name absent).
        """
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
                    # Merge lazy-loaded fields on subsequent appearances
                    for attr in ("company", "company_scale", "salary",
                                 "location", "hr_name", "hr_title", "hr_status"):
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
