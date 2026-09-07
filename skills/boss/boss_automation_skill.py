"""
Boss直聘 (BOSS Zhipin) Automation Skill

Provides high-level operations for the Boss直聘 job-search app using
AndroidSkill as the base (with injected ADBManager for device control).
"""

import logging
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from skills.android.android_skill import AndroidSkill
from skills.android.ui_types import UIElement, parse_bounds


BOSS_PACKAGE = "com.hpbr.bosszhipin"

# 列表卡标题尾部会出现 " &@ " 之类的占位字符——那是异步加载的角标 span，
# 同一张卡在不同 dump 中可能带也可能不带，直接按原文去重会把同一职位当成两条。
_BADGE_TRAIL = re.compile(r"[\s&@]+$")


def normalize_card_title(title: str) -> str:
    """Strip the async badge placeholder suffix so the same card dedups stably."""
    return _BADGE_TRAIL.sub("", title or "").strip()


class PageState:
    """Page identifiers returned by get_current_page()."""
    HOME           = "home"
    JOB_LIST       = "job_list"       # search results list
    JOB_DETAIL     = "job_detail"
    CHAT           = "chat"
    DIALOG         = "dialog"
    UNKNOWN        = "unknown"
    RECOMMEND      = "recommend"      # 推荐 tab job-card feed
    MESSAGES       = "messages"       # 消息 tab chat list
    PROFILE        = "profile"        # 我的 tab
    FILTER_PANEL   = "filter_panel"   # filter modal overlay
    COMPANY_DETAIL = "company_detail" # company detail page
    RESUME         = "resume"         # resume view/edit page
    APPLICATIONS   = "applications"   # 投递记录 page


class DialogType:
    """Dialog identifiers returned by detect_dialog()."""
    DAILY_LIMIT    = "daily_limit"
    LOGIN_REQUIRED = "login_required"
    JOB_OFFLINE    = "job_offline"
    EXISTING_CHAT  = "existing_chat"
    DISMISSED      = "dismissed"
    NONE           = "none"
    UNKNOWN_DIALOG = "unknown_dialog"

    # keyword → type mapping (checked via `in` on each node's text)
    _SIGNATURES: Dict[str, str] = {
        "免费沟通名额已使用完": "daily_limit",
        "今日免费沟通":        "daily_limit",
        "名额已用":            "daily_limit",
        "立即登录":            "login_required",
        "请登录后操作":        "login_required",
        "该职位已下线":        "job_offline",
        "职位已下线":          "job_offline",
        "暂停招聘":            "job_offline",
        "已和对方建立沟通":    "existing_chat",
    }


@dataclass
class JobInfo:
    """Parsed job listing entry."""

    title: str = ""
    company: str = ""
    salary: str = ""
    location: str = ""
    hr_name: str = ""
    hr_title: str = ""
    hr_active: str = ""
    tap_x: int = 0
    tap_y: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatEntry:
    """A parsed message-list entry from the 消息 tab."""

    hr_name: str = ""
    position: str = ""
    last_msg: str = ""
    time_str: str = ""
    tap_x: int = 0
    tap_y: int = 0


class BOSSAutomationSkill(AndroidSkill):
    """
    High-level automation skill for Boss直聘.

    Args:
        adb_manager: Injected ADBManager instance for device control.
        output_dir:  Directory for saving screenshots.
        device_id:   Optional ADB device serial; uses first connected device if omitted.
        action_delay: Seconds to wait between actions (default 1.0).
    """

    # Verified resource-ids from real Pixel 8a UIAutomator dump (com.hpbr.bosszhipin v10.x)
    # send_btn has no resource-id; send_greeting() uses KEYCODE_ENTER instead.
    ELEMENTS: Dict[str, str] = {
        "search_bar":      f"{BOSS_PACKAGE}:id/et_search",               # home page search field
        "search_submit":   f"{BOSS_PACKAGE}:id/tv_search",               # 「搜索」 button on the suggestion overlay
        "job_name":        f"{BOSS_PACKAGE}:id/tv_position_name",        # job title in list view
        "job_name_detail": f"{BOSS_PACKAGE}:id/tv_job_name",             # job title in detail view
        "chat_btn":        f"{BOSS_PACKAGE}:id/btn_chat",                # "立即沟通" button
        "chat_input":      f"{BOSS_PACKAGE}:id/editText_with_scrollbar", # chat message input
        "filter_btn":      f"{BOSS_PACKAGE}:id/filterBarRightTabView",   # search-results filter bar
        "apply_btn":       f"{BOSS_PACKAGE}:id/btn_apply",               # 仅当双方均已发消息后在聊天页出现（Boss直聘平台规则）
        # Bottom nav tabs — tap target cl_tab_N (verified from real-device dump)
        "tab_1":           f"{BOSS_PACKAGE}:id/cl_tab_1",
        "tab_2":           f"{BOSS_PACKAGE}:id/cl_tab_2",
        "tab_3":           f"{BOSS_PACKAGE}:id/cl_tab_3",
        "tab_4":           f"{BOSS_PACKAGE}:id/cl_tab_4",
        # Filter panel (verified from real-device dump)
        "filter_confirm":  f"{BOSS_PACKAGE}:id/btn_confirm",
        "filter_reset":    f"{BOSS_PACKAGE}:id/btn_reset",
        # Messages tab anchors (verified from real-device dump)
        "messages_list":   f"{BOSS_PACKAGE}:id/recyclerView",
        "contact_vp":      f"{BOSS_PACKAGE}:id/contact_vp",
        # Profile tab anchor (verified from real-device dump)
        "profile_root":    f"{BOSS_PACKAGE}:id/myGeekRoot",
        # Resume page anchors (verified from real-device dump)
        "resume_basic":    f"{BOSS_PACKAGE}:id/basic_info",
        "resume_list":     f"{BOSS_PACKAGE}:id/rv_list",
        # Recommend tab anchor (verified from real-device dump)
        "recommend_card":  f"{BOSS_PACKAGE}:id/boss_job_card_view",
        # Job-list toolbar keyword indicator (visible even while cards are loading)
        "search_indicator": f"{BOSS_PACKAGE}:id/magic_indicator",
        # Toolbar search icon (rightmost img_icon in ly_menu)
        "toolbar_menu":    f"{BOSS_PACKAGE}:id/ly_menu",
    }

    # Tab name → text labels on tv_tab_N nodes (verified from real-device dump)
    _TAB_TEXTS: Dict[str, List[str]] = {
        "recommend": ["推荐"],
        "jobs":      ["职位"],
        "messages":  ["消息"],
        "profile":   ["我的"],
    }

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
            output_dir=output_dir or str(Path(tempfile.gettempdir()) / "pixelclaw_output"),
        )
        self.action_delay = action_delay

    # ------------------------------------------------------------------
    # App lifecycle
    # ------------------------------------------------------------------

    def launch(self) -> bool:
        """Launch Boss直聘 app."""
        ok, _ = self._adb(
            f"shell monkey -p {BOSS_PACKAGE} -c android.intent.category.LAUNCHER 1"
        )
        if ok:
            time.sleep(4)  # allow app to fully restore its activity state
        return ok

    # ------------------------------------------------------------------
    # UI hierarchy (Boss always fetches fresh — no caching needed)
    # ------------------------------------------------------------------

    def get_ui_hierarchy(self, force_refresh: bool = False) -> str:  # noqa: ARG002
        """Dump current UI hierarchy XML via UIAutomator (always fresh, no caching)."""
        self._adb("shell uiautomator dump /sdcard/window_dump.xml")
        ok, content = self._adb("shell cat /sdcard/window_dump.xml")
        self.last_ui_dump = content if ok else ""
        return self.last_ui_dump

    # ------------------------------------------------------------------
    # Screenshot (saves to disk and returns path, matching base class contract)
    # ------------------------------------------------------------------

    def screenshot(self, filename: str = "boss_screenshot.png") -> str:
        """Capture screenshot, save to output_dir, return local path."""
        img = self.adb.screenshot(self.device_id)
        if img is None:
            self._logger.warning("screenshot 失败: adb 返回 None")
            return ""
        local_path = str(Path(self.output_dir) / filename)
        img.save(local_path)
        return local_path

    # ------------------------------------------------------------------
    # Page state detection
    # ------------------------------------------------------------------

    def get_current_page(self, xml: Optional[str] = None) -> str:
        """
        Identify the current screen.

        Detection priority (high → low):
          dialog > filter_panel > chat > job_detail > resume > profile >
          messages > recommend > job_list > home > unknown

        Args:
            xml: Pre-fetched XML; fetches fresh dump if None.
        Returns:
            One of the PageState constants.
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        if not xml:
            return PageState.UNKNOWN

        if self._has_dialog(xml):
            return PageState.DIALOG
        # Filter panel: both btn_confirm and btn_reset present (unique combo)
        if (self.find_element(resource_id=self.ELEMENTS["filter_confirm"], xml=xml) and
                self.find_element(resource_id=self.ELEMENTS["filter_reset"], xml=xml)):
            return PageState.FILTER_PANEL
        if self.find_element(resource_id=self.ELEMENTS["chat_input"], xml=xml):
            return PageState.CHAT
        if self.find_element(resource_id=self.ELEMENTS["job_name_detail"], xml=xml):
            return PageState.JOB_DETAIL
        # Resume: basic_info + rv_list present (not a job list)
        if (self.find_element(resource_id=self.ELEMENTS["resume_basic"], xml=xml) and
                self.find_element(resource_id=self.ELEMENTS["resume_list"], xml=xml)):
            return PageState.RESUME
        if self.find_element(resource_id=self.ELEMENTS["profile_root"], xml=xml):
            return PageState.PROFILE
        if self.find_element(resource_id=self.ELEMENTS["contact_vp"], xml=xml):
            return PageState.MESSAGES
        if self.find_element(resource_id=self.ELEMENTS["recommend_card"], xml=xml):
            return PageState.RECOMMEND
        if (self.find_element(resource_id=self.ELEMENTS["job_name"], xml=xml) or
                self.find_element(resource_id=self.ELEMENTS["search_indicator"], xml=xml)):
            return PageState.JOB_LIST
        if self.find_element(resource_id=self.ELEMENTS["search_bar"], xml=xml):
            return PageState.HOME
        return PageState.UNKNOWN

    def _has_dialog(self, xml: str) -> bool:
        """Return True if xml contains any known dialog keyword."""
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return False
        for node in root.iter("node"):
            t = node.attrib.get("text", "")
            for kw in DialogType._SIGNATURES:
                if kw in t:
                    return True
        return False

    # ------------------------------------------------------------------
    # Dialog detection and handling
    # ------------------------------------------------------------------

    def detect_dialog(self, xml: Optional[str] = None) -> str:
        """
        Identify the type of any blocking dialog on screen.

        Args:
            xml: Pre-fetched XML; fetches fresh dump if None.
        Returns:
            One of the DialogType constants.
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        if not xml:
            return DialogType.NONE
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return DialogType.NONE

        for node in root.iter("node"):
            t = node.attrib.get("text", "")
            for kw, dtype in DialogType._SIGNATURES.items():
                if kw in t:
                    self._logger.info("[detect_dialog] ← %s (触发词: %s)", dtype, kw)
                    return dtype
        return DialogType.NONE

    def dismiss_dialog(
        self,
        dialog_type: str,
        xml: Optional[str] = None,
    ) -> bool:
        """
        Dismiss a dialog according to its type.

        Returns True if the dismissal action was sent (not necessarily
        that the dialog closed).
        """
        self._logger.info("[dismiss_dialog] → %s", dialog_type)
        if dialog_type == DialogType.DAILY_LIMIT:
            for txt in ("我知道了", "确定", "知道了"):
                elem = self.find_element(text=txt, xml=xml)
                if elem and elem.center:
                    return self.tap(*elem.center)
            return self.press_back()
        elif dialog_type in (DialogType.LOGIN_REQUIRED, DialogType.JOB_OFFLINE):
            return self.press_back()
        elif dialog_type == DialogType.EXISTING_CHAT:
            return True  # already in chat, proceed
        elif dialog_type == DialogType.UNKNOWN_DIALOG:
            for txt in ("确定", "关闭", "取消"):
                elem = self.find_element(text=txt, xml=xml)
                if elem and elem.center:
                    return self.tap(*elem.center)
            return self.press_back()
        return False

    # ------------------------------------------------------------------
    # High-level workflows
    # ------------------------------------------------------------------

    def _open_search_from_job_list(self) -> bool:
        """
        From the job-list page, tap the search icon (rightmost img_icon in ly_menu)
        to open the search input overlay.
        """
        xml = self.get_ui_hierarchy()
        if not xml:
            return False
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return False

        # Find the ly_menu container, then pick the rightmost img_icon child.
        menu_node = None
        for n in root.iter("node"):
            if "ly_menu" in n.attrib.get("resource-id", ""):
                menu_node = n
                break
        if menu_node is None:
            return False

        rightmost_x = -1
        rightmost_bounds: Optional[Tuple[int, int, int, int]] = None
        for child in menu_node.iter("node"):
            if "img_icon" in child.attrib.get("resource-id", ""):
                b = parse_bounds(child.attrib.get("bounds", ""))
                if b and b[0] > rightmost_x:
                    rightmost_x = b[0]
                    rightmost_bounds = b
        if rightmost_bounds is None:
            return False

        cx = (rightmost_bounds[0] + rightmost_bounds[2]) // 2
        cy = (rightmost_bounds[1] + rightmost_bounds[3]) // 2
        return self.tap(cx, cy)

    def _submit_search(self, keyword: str) -> bool:
        """
        Clear the focused search input, type the keyword and submit.

        提交后有两种偶发卡死，都只能靠点击页面上的按钮恢复：
          ① 回车没能提交，页面停在搜索建议浮层（ASCII 关键词经 `input text`
             输入时尤其常见）——需点击浮层右下角的「搜索」按钮；
          ② 结果页返回「网络异常，请点击按钮刷新」占位页，职位列表节点永远
             不会出现——需点击「重新加载」。
        故每轮轮询失败后查找恢复按钮并点击，最多 3 轮。
        """
        del_chain = " ".join(["67"] * 30)
        self._adb(f"shell input keyevent 123 {del_chain}")
        time.sleep(0.2)
        if not self.type_text(keyword):
            return False
        self._adb("shell input keyevent 66")  # Enter
        time.sleep(self.action_delay)

        for _ in range(3):
            if self.wait_for_element(self.ELEMENTS["job_name"], timeout=8.0):
                return True
            btn = self._find_recovery_button()
            if btn is None:
                return False
            self._logger.info("[browse_jobs] 搜索未出结果，点击恢复按钮重试")
            cx, cy = btn.center or (0, 0)
            self.tap(cx, cy)
            time.sleep(2.0)
        return False

    def _find_recovery_button(self) -> Optional[UIElement]:
        """
        Locate a button that can un-stick a search that produced no job list:
        the reload button of the network-error placeholder page, or the
        「搜索」 submit button left over when Enter failed to submit.
        """
        xml = self.get_ui_hierarchy()
        if not xml:
            return None
        for label in ("重新加载", "点击重试", "重试"):
            elem = self.find_element(text=label, xml=xml)
            if elem:
                return elem
        return self.find_element(self.ELEMENTS["search_submit"], xml=xml)

    def recover_detail_page(self, max_rounds: int = 3) -> bool:
        """
        修复详情页偶发的「网络异常，请检查网络后重试」占位页。

        占位页仍会渲染 tv_job_name 与顶部 chips（面议/城市/经验/学历），
        wait_for_element 判不出异常，只有描述和公司信息块整块缺失，
        故成功判据必须是 dump 中不再含「网络异常」。
        页面上的「重试」按钮正好落在 _find_recovery_button() 的匹配列表内。
        """
        for _ in range(max_rounds):
            xml = self.get_ui_hierarchy()
            if not xml:
                return False
            if "网络异常" not in xml:
                return True
            btn = self._find_recovery_button()
            if btn is None:
                return False
            self._logger.info("[detail] 网络异常占位页，点击重试")
            cx, cy = btn.center or (0, 0)
            self.tap(cx, cy)
            time.sleep(2.0)
        return "网络异常" not in (self.get_ui_hierarchy() or "网络异常")

    def browse_jobs(self, keyword: str) -> bool:
        """
        Search for jobs with the given keyword.

        Works from both the Boss home page (et_search visible) and the job-list
        page (et_search hidden; uses search icon fallback via _open_search_from_job_list).
        Returns True if the search was submitted successfully.
        """
        if not self.tap_element("search_bar"):
            # Job-list page: open search overlay via the search icon in the toolbar.
            if not self._open_search_from_job_list():
                # Neither search bar nor search icon found — may be on RECOMMEND or HOME tab.
                # Navigate to the "职位" (jobs) tab which always has the search icon.
                self._logger.info("[browse_jobs] falling back to jobs tab navigation")
                if not self.navigate_to_tab("jobs"):
                    self._logger.warning("[browse_jobs] search_bar and search icon both not found")
                    return False
                time.sleep(1.0)
                # Now try the search icon again from the job list page.
                if not self._open_search_from_job_list():
                    # Jobs tab may have shown the search bar directly — check again.
                    if not self.tap_element("search_bar"):
                        self._logger.warning("[browse_jobs] search_bar not found after jobs tab nav")
                        return False
                    # search_bar tapped — skip the overlay wait below.
                    time.sleep(0.3)
                    return self._submit_search(keyword)
            # Let the search overlay animation settle before polling.
            time.sleep(1.5)
            # Wait up to 5 s for the search overlay (et_search) to appear.
            search_elem = self.wait_for_element(
                self.ELEMENTS["search_bar"], timeout=5.0
            )
            if not search_elem:
                self._logger.warning("[browse_jobs] search overlay did not appear after icon tap")
                return False
            # Tap the overlay input directly using the coordinates we just found.
            cx, cy = search_elem.center or (0, 0)
            if not self.tap(cx, cy):
                return False

        time.sleep(0.3)
        return self._submit_search(keyword)

    def get_job_list(self, xml: Optional[str] = None) -> List[JobInfo]:
        """
        Parse job entries visible in the current list view.

        Captures tap coordinates and sibling fields (company, salary,
        location, hr_name, hr_active) from each job card via parent-map
        traversal.  Only matches tv_position_name (list-page element),
        never tv_job_name (detail-page element).

        Args:
            xml: Pre-fetched XML; fetches fresh dump if None.
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        if not xml:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []

        parent_map = self._build_parent_map(root)
        jobs: List[JobInfo] = []
        target_rid = f"{BOSS_PACKAGE}:id/tv_position_name"

        for node in root.iter("node"):
            if node.attrib.get("resource-id", "") != target_rid:
                continue
            title = node.attrib.get("text", "")
            if not title:
                continue

            bounds = parse_bounds(node.attrib.get("bounds", ""))
            tap_x = (bounds[0] + bounds[2]) // 2 if bounds else 0
            tap_y = (bounds[1] + bounds[3]) // 2 if bounds else 0

            # tv_position_name 的直接父节点是 cl_position，只含标题本身；
            # 公司/薪资/地点/HR 都挂在更上层的 view_job_card 上。
            card = node
            for _ in range(5):
                parent = parent_map.get(card)
                if parent is None:
                    break
                card = parent
                if "view_job_card" in parent.attrib.get("resource-id", ""):
                    break

            company = salary = location = hr_name = hr_active = ""

            for sibling in card.iter("node"):
                srid = sibling.attrib.get("resource-id", "")
                stext = sibling.attrib.get("text", "")
                if not stext or srid == target_rid:
                    continue
                if "company_name" in srid:
                    company = stext
                elif "salary" in srid:                      # tv_salary_statue
                    salary = stext
                elif ("distance" in srid or "job_area" in srid
                      or "tv_area" in srid or "area_district" in srid):
                    location = stext                        # tv_distance
                elif "employer" in srid or "boss_name" in srid:
                    hr_name = stext                         # tv_employer: "刘女士 · 招聘经理"
                elif "boss_status" in srid or "active_time" in srid:
                    hr_active = stext

            hr_title = ""
            if "·" in hr_name:
                hr_name, _, hr_title = (p.strip() for p in hr_name.partition("·"))

            jobs.append(JobInfo(
                title=title,
                company=company,
                salary=salary,
                location=location,
                hr_name=hr_name,
                hr_title=hr_title,
                hr_active=hr_active,
                tap_x=tap_x,
                tap_y=tap_y,
            ))
        return jobs

    def navigate_to_job(self, job: JobInfo) -> bool:
        """Tap a job card using the coordinates captured by get_job_list()."""
        self._logger.info("[navigate_to_job] → %s @ (%d, %d)", job.title, job.tap_x, job.tap_y)
        if job.tap_x or job.tap_y:
            ok = self.tap(job.tap_x, job.tap_y)
            self._logger.info("[navigate_to_job] ← %s", "成功" if ok else "失败")
            return ok
        self._logger.warning("[navigate_to_job] 坐标缺失，回退到 tap_element")
        ok = self.tap_element("job_name")
        self._logger.info("[navigate_to_job] ← (回退) %s", "成功" if ok else "失败")
        return ok

    def can_apply(self, xml: Optional[str] = None) -> bool:
        """Return True if an Apply button is present on the current page.

        Returns True only after both parties have exchanged messages (platform rule).
        Call this from a chat page opened via navigate_to_chat(), not from job detail.
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        return self.find_element(
            resource_id=self.ELEMENTS["apply_btn"], xml=xml
        ) is not None

    def apply_to_job(self, xml: Optional[str] = None) -> bool:
        """
        Tap the 'Apply' (投递简历) button in the current chat page.

        Prerequisite: can_apply() must return True. See boss_apply_task.py for the
        correct two-phase workflow (greet first, apply after HR replies).
        Returns False when the button is absent.
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        apply_elem = self.find_element(
            resource_id=self.ELEMENTS["apply_btn"], xml=xml
        )
        if not apply_elem or not apply_elem.center:
            self._logger.info("[apply_to_job] ← 无 apply_btn，跳过")
            return False
        ok = self.tap(*apply_elem.center)
        self._logger.info("[apply_to_job] ← %s", "投递成功" if ok else "点击失败")
        return ok

    def send_greeting(self, message: str, verify: bool = False) -> bool:
        """
        Type and send a greeting message in an open chat.

        Args:
            message: Text to send.
            verify: If True, poll UI after sending to confirm the message
                    appears in the chat bubble list (costs one extra dump).
        Returns:
            True if send succeeded (and verification passed when requested).
        """
        self._logger.info("[send_greeting] → 发送消息 (verify=%s)", verify)
        if not self.tap_element("chat_input"):
            self._logger.warning("[send_greeting] ← 找不到 chat_input")
            return False
        time.sleep(0.3)
        if not self.type_text(message):
            self._logger.warning("[send_greeting] ← type_text 失败")
            return False
        time.sleep(0.2)
        ok, _ = self._adb("shell input keyevent 66")  # KEYCODE_ENTER
        if ok and verify:
            time.sleep(1.0)
            verified = self.verify_message_sent(message)
            self._logger.info("[send_greeting] ← %s", "已验证发送" if verified else "发送后验证失败")
            return verified
        self._logger.info("[send_greeting] ← %s", "发送成功" if ok else "keyevent 失败")
        return ok

    def verify_message_sent(self, message: str, timeout: float = 3.0) -> bool:
        """
        Poll the UI hierarchy until the sent message appears as a bubble.

        Args:
            message: Exact text that was sent.
            timeout: Maximum seconds to wait.
        Returns:
            True if the message was found within timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            xml = self.get_ui_hierarchy()
            if self.find_element(text=message, xml=xml):
                return True
            time.sleep(0.5)
        return False

    def scroll_job_list(
        self,
        n_jobs: int,
        max_scrolls: int = 10,
        screen_height: int = 2400,
    ) -> List[JobInfo]:
        """
        Scroll the job list until n_jobs unique entries are collected.

        Args:
            n_jobs: Target number of job listings.
            max_scrolls: Safety cap on scroll attempts.
            screen_height: Device screen height in pixels (Pixel 8a default 2400).
        Returns:
            Deduplicated list of up to n_jobs JobInfo entries.
        """
        all_jobs: List[JobInfo] = []
        seen: Dict[str, JobInfo] = {}
        scrolls = 0

        while len(all_jobs) < n_jobs and scrolls < max_scrolls:
            xml = self.get_ui_hierarchy()
            page_jobs = self.get_job_list(xml=xml)

            new_found = 0
            for job in page_jobs:
                key = normalize_card_title(job.title)
                if not key:
                    continue
                known = seen.get(key)
                if known is None:
                    job.title = key
                    seen[key] = job
                    all_jobs.append(job)
                    new_found += 1
                else:
                    # 边缘卡片首次可能只渲染出 title，再次出现时补齐空字段。
                    for attr in ("company", "salary", "location",
                                 "hr_name", "hr_title", "hr_active"):
                        if not getattr(known, attr) and getattr(job, attr):
                            setattr(known, attr, getattr(job, attr))

            if new_found == 0:
                break  # end of list

            if len(all_jobs) >= n_jobs:
                break

            start_y = int(screen_height * 0.75)
            end_y   = int(screen_height * 0.25)
            self.swipe(540, start_y, 540, end_y, 500)
            time.sleep(self.action_delay)
            scrolls += 1

        return all_jobs[:n_jobs]

    def _find_description(
        self, xml: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[Tuple[int, int, int, int]], int]:
        """Return (text, bounds, list_bottom) of the detail-page description node."""
        if xml is None:
            xml = self.get_ui_hierarchy()
        if not xml:
            return None, None, 0
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return None, None, 0

        list_bottom = 0
        text = bounds = None
        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            if "rv_list" in rid or "recyclerView" in rid:
                b = parse_bounds(node.attrib.get("bounds", ""))
                if b:
                    list_bottom = max(list_bottom, b[3])
            if text is None and ("tv_description" in rid or "tv_job_desc" in rid):
                text = node.attrib.get("text", "")
                bounds = parse_bounds(node.attrib.get("bounds", ""))
        return text, bounds, list_bottom

    def expand_description(self, max_scrolls: int = 6) -> bool:
        """
        展开职位详情页被"查看更多"折叠的职位描述。

        "查看更多" 是 tv_description 内的 inline span（TextView 本身 clickable=false，
        XML 里没有独立节点），只能按坐标点击最后一行右端。而 TextView 的 bounds 会被
        外层 RecyclerView 裁剪，所以必须先小步滚动到描述整体可见，否则算出的"最后一行"
        其实在屏幕外。

        Returns:
            True 表示描述已完整（本来就完整，或点击后成功展开）。
        """
        for _ in range(max_scrolls):
            text, bounds, list_bottom = self._find_description()
            if text is None or bounds is None:
                return False
            if "查看更多" not in text:
                return True
            if bounds[3] < list_bottom - 5:
                break                       # 未被裁剪，最后一行可信
            self.scroll_down(start_y=1600, end_y=1200)
            time.sleep(self.action_delay)
        else:
            return False

        x1, y1, x2, y2 = bounds
        self.tap(x2 - 90, y2 - 28)
        time.sleep(self.action_delay + 0.6)

        text, _, _ = self._find_description()
        return bool(text) and "查看更多" not in text

    def get_job_detail(self, xml: Optional[str] = None) -> Dict[str, Any]:
        """
        从当前职位详情页提取结构化信息。

        双轨策略：
          1. 尝试已知 resource-id 片段 → 映射到语义字段
          2. 收集所有非空文本节点到 raw_texts（保底，不丢信息）

        调用方应先 scroll_down() 1-2 次确保长描述已加载。

        Returns:
            Dict containing known fields (title, salary, etc.) plus
            ``skills`` (list), ``benefits`` (list), ``raw_texts`` (list).
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        if not xml:
            return {}
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return {}

        field_map = {
            "tv_job_name":      "title",
            "tv_salary_desc":   "salary",
            "tv_area_district": "location",
            "tv_job_area":      "location",
            "tv_location":      "location",    # actual id on device
            "tv_experience":    "experience",
            "tv_degree":        "education",
            "tv_job_desc":      "description",
            "tv_description":   "description", # actual id on device
            "tv_company_name":  "company",
            "tv_com_name":      "company",     # actual id on device
            "tv_com_info":      "company_info",
            "tv_company_scale": "company_scale",
            "tv_industry":      "industry",
            "tv_boss_name":     "hr_name",
            "tv_boss_title":    "hr_title",
            "boss_status":      "hr_active",
            "active_time":      "hr_active",
            "tv_skill_tag":     "skills",
            "tv_benefit":       "benefits",
        }

        result: Dict[str, Any] = {}
        raw_texts: List[str] = []
        skills: List[str] = []
        benefits: List[str] = []

        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            text = node.attrib.get("text", "").strip()
            if not text:
                continue

            matched = False
            for rid_frag, field in field_map.items():
                if rid_frag in rid:
                    if field == "skills":
                        skills.append(text)
                    elif field == "benefits":
                        benefits.append(text)
                    elif field not in result:
                        result[field] = text
                    matched = True
                    break

            if not matched:
                raw_texts.append(text)

        if skills:
            result["skills"] = skills
        if benefits:
            result["benefits"] = benefits
        result["raw_texts"] = raw_texts
        return result

    def is_on_job_list(self) -> bool:
        """Return True if the current page contains at least one job list item."""
        return self.find_element(resource_id=self.ELEMENTS["job_name"]) is not None

    def filter_jobs(
        self,
        city: Optional[str] = None,
        salary: Optional[str] = None,
        experience: Optional[str] = None,
    ) -> bool:
        """
        Open the filter panel and apply criteria via text matching.

        The filter panel's internal resource-ids are not yet verified on a
        real device; this implementation falls back to text-based element
        lookup which tolerates version differences.
        """
        if not self.tap_element("filter_btn"):
            return False
        time.sleep(0.8)

        changed = False
        for value in (city, salary, experience):
            if not value:
                continue
            elem = self.find_element(text=value)
            if elem and elem.center:
                self.tap(*elem.center)
                time.sleep(0.3)
                changed = True

        confirm_elem = self.find_element(resource_id=self.ELEMENTS["filter_confirm"])
        if confirm_elem and confirm_elem.center:
            self.tap(*confirm_elem.center)
            time.sleep(self.action_delay)
            return True
        for txt in ("确定", "完成"):
            elem = self.find_element(text=txt)
            if elem and elem.center:
                self.tap(*elem.center)
                time.sleep(self.action_delay)
                return True

        self.press_back()
        return changed

    def navigate_to_tab(self, tab: str) -> bool:
        """
        Switch to a bottom-nav tab by name.

        Args:
            tab: One of 'recommend', 'jobs', 'messages', 'profile'.
        Returns:
            True if the tab was tapped successfully.
        """
        xml = self.get_ui_hierarchy()
        if not xml:
            return False
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return False

        labels = self._TAB_TEXTS.get(tab, [tab])
        for n in range(1, 5):
            tv_rid = f"{BOSS_PACKAGE}:id/tv_tab_{n}"
            for node in root.iter("node"):
                if node.attrib.get("resource-id") == tv_rid:
                    if node.attrib.get("text", "") in labels:
                        cl_rid = f"{BOSS_PACKAGE}:id/cl_tab_{n}"
                        for btn in root.iter("node"):
                            if btn.attrib.get("resource-id") == cl_rid:
                                bounds = parse_bounds(btn.attrib.get("bounds", ""))
                                if bounds:
                                    ok = self.tap(
                                        (bounds[0] + bounds[2]) // 2,
                                        (bounds[1] + bounds[3]) // 2,
                                    )
                                    if ok:
                                        time.sleep(self.action_delay)
                                    return ok
        return False

    def get_message_list(self, xml: Optional[str] = None) -> List[ChatEntry]:
        """
        Parse the 消息 tab chat list into ChatEntry objects.

        Uses parent-map traversal to extract sibling fields (position,
        last message, time) for each contact row anchored by tv_name.

        Args:
            xml: Pre-fetched XML; fetches fresh dump if None.
        Returns:
            List of ChatEntry, each with tap coordinates.
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        if not xml:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []

        parent_map = self._build_parent_map(root)
        name_rid  = f"{BOSS_PACKAGE}:id/tv_name"
        pos_rid   = f"{BOSS_PACKAGE}:id/tv_position"
        msg_rid   = f"{BOSS_PACKAGE}:id/tv_msg"

        entries: List[ChatEntry] = []
        seen: set = set()

        for node in root.iter("node"):
            if node.attrib.get("resource-id") != name_rid:
                continue
            hr_name = node.attrib.get("text", "")
            if not hr_name or hr_name in seen:
                continue
            seen.add(hr_name)

            bounds = parse_bounds(node.attrib.get("bounds", ""))
            tap_x = (bounds[0] + bounds[2]) // 2 if bounds else 0
            tap_y = (bounds[1] + bounds[3]) // 2 if bounds else 0

            card = parent_map.get(node, node)
            position = last_msg = time_str = ""
            for sibling in card.iter("node"):
                srid  = sibling.attrib.get("resource-id", "")
                stext = sibling.attrib.get("text", "")
                if not stext:
                    continue
                if srid == pos_rid:
                    position = stext
                elif srid == msg_rid:
                    last_msg = stext
                elif "tv_time" in srid:
                    time_str = stext

            entries.append(ChatEntry(
                hr_name=hr_name,
                position=position,
                last_msg=last_msg,
                time_str=time_str,
                tap_x=tap_x,
                tap_y=tap_y,
            ))
        return entries

    def navigate_to_chat(self, entry: ChatEntry) -> bool:
        """Open an existing chat by tapping a ChatEntry from get_message_list()."""
        self._logger.info("[navigate_to_chat] → %s / %s @ (%d, %d)",
                          entry.hr_name, entry.position, entry.tap_x, entry.tap_y)
        if entry.tap_x or entry.tap_y:
            ok = self.tap(entry.tap_x, entry.tap_y)
            if ok:
                time.sleep(self.action_delay)
            self._logger.info("[navigate_to_chat] ← %s", "成功" if ok else "失败")
            return ok
        self._logger.warning("[navigate_to_chat] ← 坐标缺失")
        return False

    def set_filter(
        self,
        salary: Optional[str] = None,
        experience: Optional[str] = None,
        education: Optional[str] = None,
        city: Optional[str] = None,
    ) -> bool:
        """
        Apply filter options inside an already-open filter panel.

        Taps each requested option by text, then confirms with btn_confirm
        (verified resource-id). Does NOT open the filter panel itself —
        call filter_jobs() or tap_element('filter_btn') first.
        """
        changed = False
        for value in (city, salary, experience, education):
            if not value:
                continue
            elem = self.find_element(text=value)
            if elem and elem.center:
                self.tap(*elem.center)
                time.sleep(0.3)
                changed = True

        confirm_elem = self.find_element(resource_id=self.ELEMENTS["filter_confirm"])
        if confirm_elem and confirm_elem.center:
            self.tap(*confirm_elem.center)
            time.sleep(self.action_delay)
            return True
        for txt in ("确定", "完成"):
            elem = self.find_element(text=txt)
            if elem and elem.center:
                self.tap(*elem.center)
                time.sleep(self.action_delay)
                return True
        self.press_back()
        return changed

    # ------------------------------------------------------------------
    # Error recovery
    # ------------------------------------------------------------------

    def _is_screen_on(self) -> bool:
        ok, out = self.adb.shell("shell dumpsys power", self.device_id)
        return ok and "mWakefulness=Awake" in out

    def _wake_screen(self) -> None:
        self.adb.shell("shell input keyevent 224", self.device_id)
        time.sleep(0.5)
        self.adb.shell("shell wm dismiss-keyguard", self.device_id)
        time.sleep(0.5)

    def ensure_ready(self) -> bool:
        """
        Pre-iteration health check: ADB → screen → App foreground → dialog clear.
        Returns False when the session cannot auto-recover (fatal dialog, ADB lost).
        """
        if not self.adb.is_device_connected(self.device_id):
            self._logger.warning("[ensure_ready] ADB 设备未连接: %s", self.device_id)
            return False
        if not self._is_screen_on():
            self._logger.info("[ensure_ready] 屏幕熄灭，正在唤醒")
            self._wake_screen()
        page = self.get_current_page()
        if page == PageState.UNKNOWN:
            # Lock screen can appear even when mWakefulness=Awake.  Dismiss it
            # first, then poll up to 12 s for the app to reach a known state.
            self._logger.warning("[ensure_ready] 页面 UNKNOWN，尝试唤醒并清除锁屏…")
            self._wake_screen()   # WAKEUP + dismiss-keyguard
            deadline = time.time() + 12.0
            while time.time() < deadline:
                time.sleep(1.5)
                page = self.get_current_page()
                if page != PageState.UNKNOWN:
                    self._logger.info("[ensure_ready] 页面已恢复: %s", page)
                    break
            else:
                self._logger.warning("[ensure_ready] 等待超时，尝试重启 App")
                self.launch()   # already sleeps 4 s internally
                self._wake_screen()
                page = self.get_current_page()
                if page == PageState.UNKNOWN:
                    self._logger.error("[ensure_ready] 重启后仍 UNKNOWN，放弃恢复")
                    return False
                self._logger.info("[ensure_ready] 重启成功，当前页: %s", page)
        if page == PageState.DIALOG:
            dialog = self.detect_dialog()
            if dialog in (DialogType.DAILY_LIMIT, DialogType.LOGIN_REQUIRED):
                self._logger.error("[ensure_ready] 致命弹窗 %s，无法绕过", dialog)
                return False
            self._logger.info("[ensure_ready] 清理弹窗: %s", dialog)
            self.dismiss_dialog(dialog)
        self._logger.debug("[ensure_ready] 就绪，当前页: %s", page)
        return True
