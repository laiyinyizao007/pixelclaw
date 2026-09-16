"""
Boss直聘 (BOSS Zhipin) Automation Skill

Provides high-level operations for the Boss直聘 job-search app using
AndroidSkill as the base (with injected ADBManager for device control).
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
        # Job-list RecyclerView (only present on search results, not on recommend feed)
        "job_list_rv":     f"{BOSS_PACKAGE}:id/recyclerView_list",
        # Search-results toolbar hint (only on GeekSearchActivity, not on home feed)
        "search_hint":     f"{BOSS_PACKAGE}:id/tv_search_hint",
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

    APP_PACKAGE = BOSS_PACKAGE
    _DIALOG_SIGNATURES = DialogType._SIGNATURES

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
            action_delay=action_delay,
        )

    # ------------------------------------------------------------------
    # UI hierarchy (Boss always fetches fresh — no caching needed)
    # ------------------------------------------------------------------

    def get_ui_hierarchy(self, force_refresh: bool = False) -> str:  # noqa: ARG002
        return super().get_ui_hierarchy(force_refresh=True)

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
            # boss_job_card_view appears on BOTH the recommend (home) feed and
            # the search results page. Disambiguate: search results always have
            # recyclerView_list + tv_search_hint (toolbar shows the keyword);
            # the home feed has neither.
            if (self.find_element(resource_id=self.ELEMENTS["job_list_rv"], xml=xml)
                    and self.find_element(resource_id=self.ELEMENTS["search_hint"], xml=xml)):
                return PageState.JOB_LIST
            return PageState.RECOMMEND
        if (self.find_element(resource_id=self.ELEMENTS["job_name"], xml=xml) or
                self.find_element(resource_id=self.ELEMENTS["search_indicator"], xml=xml)):
            return PageState.JOB_LIST
        if self.find_element(resource_id=self.ELEMENTS["search_bar"], xml=xml):
            return PageState.HOME
        return PageState.UNKNOWN

    def _has_dialog(self, xml: str) -> bool:
        """Return True if xml contains any known dialog keyword."""
        return self._scan_for_dialog(xml) != "none"

    # ------------------------------------------------------------------
    # Dialog detection and handling
    # ------------------------------------------------------------------

    def detect_dialog(self, xml: Optional[str] = None) -> str:
        """Identify the type of any blocking dialog on screen."""
        return self._scan_for_dialog(xml)

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
        root = self._parse_xml(xml)
        if root is None:
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
        root = self._parse_xml(xml)
        if root is None:
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

    def find_and_navigate_to_job(
        self,
        title: str,
        company: str,
        fallback_job: JobInfo,
        max_scrolls: int = 25,
        screen_height: int = 2400,  # noqa: ARG004
    ) -> bool:
        """
        Locate a job card in the current list view using fresh coordinates.

        After _return_to_job_list() the RecyclerView may be at a different scroll
        position than when the card was originally collected, making stored
        tap_x/tap_y stale and potentially hitting the wrong card. This method
        scrolls the list back to the top, scans the live UI dump, and scrolls
        down until it finds the target card, then taps it with the coordinates
        visible right now.

        Self-healing scroller resolution (three stages, never silent):

        1. ``_make_u2_scroller()`` — long-wait (10s) for ``recyclerView_list``,
           the known-good scroller on the search-results page.
        2. ``_find_working_scroller()`` — if stage 1 fails, dump the XML,
           enumerate every ``scrollable="true"`` node, try each via
           ``scroll.backward()`` until one actually moves the page
           (verified by XML-hash delta).
        3. ``_log_scroll_failure_diagnosis()`` — if stage 2 also fails,
           dump the live UI state (page, scrollable ids, XML snippet) and
           return ``False`` explicitly. We do NOT silently fall back to
           swipe: ``adb shell input swipe`` is intercepted by Boss's
           onTouchListener (proven by ``diag_swipe.py``: 23/23 Δ=0 on
           Boss RecyclerView), so it would just waste time and return a
           fake success that taps the wrong card.

        Final fallback: if scroller was obtained but the card title never
        shows up after ``max_scrolls`` iterations, use the stored
        ``fallback_job.tap_x/y`` as a best-effort tap (only valid when
        we are actually on JOB_LIST — see the page-check below).
        """
        norm_title = normalize_card_title(title)

        # ── 三段式自愈：长等 → 枚举候选 → 诊断 ──────────────────────
        scroller = self._make_u2_scroller()
        if scroller is None:
            scroller = self._find_working_scroller()
        if scroller is None:
            self._log_scroll_failure_diagnosis()
            return False  # 显式失败，不再静默回退 swipe
        # ─────────────────────────────────────────────────────────────

        self._scroll_recycler_to_top(scroller)

        for attempt in range(max_scrolls + 1):
            xml = self.get_ui_hierarchy()
            for visible_job in self.get_job_list(xml=xml):
                if normalize_card_title(visible_job.title) != norm_title:
                    continue
                c_match = (
                    not company
                    or not visible_job.company
                    or company[:6] in visible_job.company
                    or visible_job.company[:6] in company
                )
                if not c_match:
                    continue
                self._logger.info(
                    "[find_and_navigate] 找到「%s」，新坐标 (%d, %d)",
                    visible_job.title, visible_job.tap_x, visible_job.tap_y,
                )
                return self.tap(visible_job.tap_x, visible_job.tap_y)

            if attempt < max_scrolls and scroller is not None:
                try:
                    scroller.scroll.forward()
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "[find_and_navigate] scroll.forward 失败（可能已到底）: %s", exc,
                    )
                    break
                time.sleep(0.5)

        # 卡片扫完一遍都没找到 — 用陈旧坐标兜底，但先校验页面防止误 tap
        page = self.get_current_page()
        if page != PageState.JOB_LIST:
            self._logger.warning(
                "[find_and_navigate] 卡片未找到且当前不在 JOB_LIST (page=%s)，"
                "放弃陈旧坐标兜底，返回 False",
                page,
            )
            return False
        self._logger.warning(
            "[find_and_navigate] 未找到「%s」，回退旧坐标 (%d, %d)",
            title, fallback_job.tap_x, fallback_job.tap_y,
        )
        return self.navigate_to_job(fallback_job)

    def _make_u2_scroller(self, max_wait: float = 10.0):
        """Return a uiautomator2 UiObject for recyclerView_list, or None.

        Long-waits (default 10s) for the search-results RecyclerView to inflate —
        the page often takes 3-7 s to render after search submission, so the
        original 2 s wait raced and silently returned None.

        Returns None only if uiautomator2 is not installed, init raises, or
        the RecyclerView genuinely never appears (we're on the wrong page).
        All failure paths now emit a WARNING log carrying the current page,
        so the regression is loud instead of silent.
        """
        try:
            import uiautomator2 as u2  # noqa: WPS433
        except ImportError:
            self._logger.warning("[find_and_navigate] uiautomator2 未安装")
            return None
        try:
            device = u2.connect(self.device_id) if self.device_id else u2.connect()
            obj = device(resourceId="com.hpbr.bosszhipin:id/recyclerView_list")
            if not obj.wait(timeout=max_wait):
                page = self.get_current_page()
                self._logger.warning(
                    "[find_and_navigate] recyclerView_list 等 %.1fs 仍不出现 (page=%s)",
                    max_wait, page,
                )
                return None
            self._logger.debug("[find_and_navigate] uiautomator2 scroller 就绪")
            return obj
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("[find_and_navigate] uiautomator2 初始化失败: %s", exc)
            return None

    def _parse_scrollable_candidates(self, xml: str) -> list[tuple[str, str]]:
        """Parse a UI dump for scrollable nodes.

        Returns ``[(resource_id, bounds), ...]`` for nodes where
        ``scrollable="true"`` AND the resource-id is non-empty. Duplicate
        resource-ids are collapsed (we can only target one UiObject per rid).

        Used by the self-healing path: when ``recyclerView_list`` isn't
        available, we enumerate every scrollable container on the page and
        try each as a scroller candidate.
        """
        import xml.etree.ElementTree as ET
        out: list[tuple[str, str]] = []
        if not xml:
            return out
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return out
        seen: set[str] = set()
        for node in root.iter("node"):
            if node.attrib.get("scrollable") != "true":
                continue
            rid = node.attrib.get("resource-id", "")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            out.append((rid, node.attrib.get("bounds", "")))
        return out

    def _find_working_scroller(self):
        """Self-healing fallback: enumerate scrollable containers and try each.

        When the primary path (``_make_u2_scroller``) can't get a handle on
        ``recyclerView_list`` (page-settle race, wrong tab, partial render),
        dump the live XML, list every node with ``scrollable="true"``, and
        try each one via uiautomator2 ``scroll.backward()`` exactly once.
        Movement is verified by comparing XML hashes before/after the call
        — a candidate "succeeds" only if the page actually changed.

        Returns the working UiObject, or None if every candidate failed.
        """
        try:
            import uiautomator2 as u2  # noqa: WPS433
        except ImportError:
            return None
        try:
            device = (
                u2.connect(self.device_id) if self.device_id else u2.connect()
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("[find_and_navigate] uiautomator2 重连失败: %s", exc)
            return None

        xml = self.get_ui_hierarchy()
        candidates = self._parse_scrollable_candidates(xml)
        self._logger.info(
            "[find_and_navigate] 发现 %d 个 scrollable 候选: %s",
            len(candidates), [c[0] for c in candidates[:6]],
        )

        before = hash(xml)
        for rid, _bounds in candidates:
            try:
                obj = device(resourceId=rid)
                if not obj.wait(timeout=0.5):
                    continue
                obj.scroll.backward()
                time.sleep(0.5)
                after_xml = self.get_ui_hierarchy()
                if hash(after_xml) != before:
                    self._logger.info(
                        "[find_and_navigate] scrollable 候选命中: %s", rid,
                    )
                    return obj
                before = hash(after_xml)
            except Exception as exc:  # noqa: BLE001
                self._logger.debug(
                    "[find_and_navigate] 候选 %s 失败: %s", rid, exc,
                )
                continue
        return None

    def _log_scroll_failure_diagnosis(self) -> None:
        """Emit a structured diagnosis when every scroller-resolution path fails.

        Captures the live UI state so the caller (or a human reading the
        log) can see exactly why we gave up: which page we're on, what
        scrollable containers are visible, and a snippet of the XML.
        Never silent — this is the opposite of the old "已知无效 swipe
        兜底" behaviour.
        """
        page = self.get_current_page()
        xml = self.get_ui_hierarchy()
        candidates = self._parse_scrollable_candidates(xml)
        self._logger.error(
            "[find_and_navigate] 滚动失败诊断\n"
            "  current_page=%s\n"
            "  scrollable 节点数=%d, ids=%s\n"
            "  XML 前 200 字符: %s",
            page, len(candidates), [c[0] for c in candidates[:8]],
            (xml or "")[:200],
        )

    def _scroll_recycler_to_top(
        self, scroller, max_steps: int = 60, stable_runs: int = 4,
    ) -> None:
        """Scroll the job-list RecyclerView to the beginning via UiScrollable.

        Stops when the first visible card's title stops changing for
        ``stable_runs`` consecutive ``scroll.backward()`` calls, or after
        ``max_steps`` total calls (safety bound).

        scroller must be a uiautomator2 UiObject whose target is the
        recyclerView_list. Callers should pass ``self._make_u2_scroller()``.
        """
        prev_title = ""
        same_count = 0
        for i in range(max_steps):
            try:
                scroller.scroll.backward()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "[scroll_top] scroll.backward 失败（可能已在顶部）: %s", exc,
                )
                return
            time.sleep(0.4)
            try:
                xml = self.get_ui_hierarchy()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("[scroll_top] dump 失败 %d: %s", i, exc)
                continue
            cur_title = ""
            for node in ET.fromstring(xml).iter("node"):
                if node.attrib.get("resource-id", "") == f"{BOSS_PACKAGE}:id/tv_position_name":
                    cur_title = node.attrib.get("text", "")
                    break
            if cur_title and cur_title == prev_title:
                same_count += 1
                if same_count >= stable_runs:
                    self._logger.info(
                        "[scroll_top] 已到顶部 (稳定 %d 次): %s", same_count, cur_title,
                    )
                    return
            else:
                same_count = 0
                prev_title = cur_title
        self._logger.warning("[scroll_top] 达到 max_steps=%d，强制结束", max_steps)

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

    # ADBKeyboard IME component name (invisible — no soft-keyboard UI)
    _ADB_IME = "com.android.adbkeyboard/.AdbIME"

    def send_greeting(self, message: str, verify: bool = False) -> bool:
        """
        Type and send a greeting message in an open chat.

        Args:
            message: Text to send.
            verify: If True, poll UI after sending to confirm the message
                    appears in the chat bubble list (costs one extra dump).
        Returns:
            True only if text was confirmed entered AND the send button was tapped
            (and message verified when verify=True).
        """
        self._logger.info("[send_greeting] → 发送消息 (verify=%s)", verify)

        # Save current IME so we can restore it after sending.
        _, prev_ime = self._adb("shell settings get secure default_input_method")
        prev_ime = (prev_ime or "").strip()

        # Switch to ADBKeyboard BEFORE tapping the EditText.
        # Reason: if we tap first (with Gboard active), Gboard's soft keyboard appears
        # and shifts the UI upward.  We then switch to ADBKeyboard which has no visible
        # keyboard — but the keyboard hide animation causes a second layout shift.
        # Capturing the send-button coordinates during these transitions gives wrong
        # positions.  By enabling ADBKeyboard first (it stays invisible), the BOSS chat
        # layout never shifts and all coordinates are stable throughout.
        self._adb(f"shell ime enable {self._ADB_IME}")
        self._adb(f"shell ime set {self._ADB_IME}")
        time.sleep(0.5)

        # Focus the chat input (ADBKeyboard is now active → no visible keyboard).
        if not self.tap_element("chat_input"):
            self._logger.warning("[send_greeting] ← 找不到 chat_input")
            if prev_ime:
                self._adb(f"shell ime set {prev_ime}")
            return False
        # Give ADBKeyboard time to receive onStartInput() and establish
        # the InputConnection with this EditText.
        time.sleep(2.5)

        self._save_debug_screenshot("send_01_before_type")

        # Broadcast the message text directly (ADBKeyboard is already active).
        # IMPORTANT: do NOT go through self._adb() → adb_runner.shell() here.
        # shell() uses shlex.split(posix=True) which strips the single quotes that
        # shlex.quote() adds, then subprocess.run passes the text as a bare arg.
        # adb joins all shell args with spaces WITHOUT re-quoting them, so the
        # device shell splits the greeting at any space character — truncating the
        # message to the first word.  By passing the entire shell command as ONE
        # arg to "adb shell" we preserve the single quotes on the device side.
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
        # Wait for ADBKeyboard's onReceive/commitText to complete before we
        # dump the XML or tap the send button.
        time.sleep(1.5)

        self._save_debug_screenshot("send_02_after_type")

        # Verify the text actually landed in the EditText.
        xml_after_type = self.get_ui_hierarchy(force_refresh=True)
        chat_elem = self.find_element(
            resource_id=self.ELEMENTS["chat_input"], xml=xml_after_type
        )
        typed_text = (chat_elem.text if chat_elem else "") or ""
        msg_start = message[:6]
        if msg_start not in typed_text:
            self._logger.error(
                "[send_greeting] ← 文字未进入输入框 "
                "(EditText='%s'，期望前缀='%s')；已保存诊断 XML。",
                typed_text[:30], msg_start,
            )
            _dbg = Path(self.output_dir) / "debug_type_fail.xml"
            _dbg.write_text(xml_after_type or "", encoding="utf-8")
            if prev_ime:
                self._adb(f"shell ime set {prev_ime}")
            return False
        self._logger.info(
            "[send_greeting] ✓ 输入框已有文字（前20字）：%s", typed_text[:20]
        )

        # Locate the send button while ADBKeyboard is still active (no soft keyboard
        # visible → layout is stable, coordinates match what we tap below).
        # The BOSS send button is a gradient-circle ImageView with no resource-id,
        # text, or content-desc.  Use positional fallback.
        _send_btn = (
            self.find_element(content_desc="发送", xml=xml_after_type)
            or self.find_element(text="发送", xml=xml_after_type)
        )
        if not (_send_btn and _send_btn.center):
            _send_btn = self._find_send_btn_by_position(xml_after_type, chat_elem)
        if not (_send_btn and _send_btn.center):
            self._logger.error("[send_greeting] ← 未找到发送按钮；已保存诊断 XML。")
            _dbg2 = Path(self.output_dir) / "debug_no_sendbtn.xml"
            _dbg2.write_text(xml_after_type or "", encoding="utf-8")
            if prev_ime:
                self._adb(f"shell ime set {prev_ime}")
            return False

        self._logger.info("[send_greeting] 点击发送按钮 bounds=%s center=%s",
                          _send_btn.bounds, _send_btn.center)
        ok = self.tap(*_send_btn.center)
        time.sleep(0.5)
        self._save_debug_screenshot("send_03_after_send")

        # Restore IME after sending (not before — restoring causes keyboard to appear,
        # which would shift the layout and invalidate the coordinates used above).
        if prev_ime:
            self._adb(f"shell ime set {prev_ime}")

        if not ok:
            self._logger.warning("[send_greeting] ← 点击发送按钮失败")
            return False

        if verify:
            time.sleep(1.0)
            verified = self.verify_message_sent(message)
            self._logger.info(
                "[send_greeting] ← %s", "已验证发送" if verified else "发送后验证失败"
            )
            return verified

        self._logger.info("[send_greeting] ← 发送成功（未验证）")
        return True

    def _save_debug_screenshot(self, tag: str) -> None:
        path = self.screenshot(f"debug_{tag}.png")
        if path:
            self._logger.info("[debug] 截图：%s", path)

    def _find_send_btn_by_position(
        self, xml: Optional[str], chat_elem: Optional[Any]
    ) -> Optional["UIElement"]:
        """Find the chat send button when it has no text/content-desc.

        The BOSS send button is an icon-only ImageView positioned to the right
        of the chat EditText.  We find it by locating clickable nodes whose
        x-range starts past the EditText's right edge, in the same y-row.
        """
        if not xml or not chat_elem or not chat_elem.bounds or len(chat_elem.bounds) < 4:
            return None
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return None

        edit_x2 = chat_elem.bounds[2]  # right edge of the input box
        edit_y1 = chat_elem.bounds[1]
        edit_y2 = chat_elem.bounds[3]
        y_mid = (edit_y1 + edit_y2) // 2

        best = None
        for node in root.iter("node"):
            if node.attrib.get("clickable") != "true":
                continue
            bounds = parse_bounds(node.attrib.get("bounds", ""))
            if not bounds or len(bounds) < 4:
                continue
            nx1, ny1, nx2, ny2 = bounds
            # Must start to the right of the EditText and overlap the y-row
            if nx1 < edit_x2:
                continue
            if ny2 < y_mid or ny1 > edit_y2:
                continue
            # Prefer the rightmost candidate (the send button is furthest right)
            if best is None or nx1 > best.bounds[0]:
                best = UIElement(
                    resource_id=node.attrib.get("resource-id", ""),
                    text=node.attrib.get("text", ""),
                    content_desc=node.attrib.get("content-desc", ""),
                    bounds=bounds,
                    clickable=True,
                )
        if best:
            self._logger.info(
                "[send_greeting] 按位置找到发送按钮 bounds=%s", best.bounds
            )
        return best

    def verify_message_sent(self, message: str, timeout: float = 8.0) -> bool:
        """
        Poll the UI hierarchy until the sent message appears as a bubble.

        Args:
            message: Exact text that was sent.
            timeout: Maximum seconds to wait.
        Returns:
            True if the message was found within timeout.
        """
        # Use first 15 chars as the search key: the message bubble text may be
        # longer than the prefix, so find_element(text=exact) won't match.
        # Search the raw XML string for the prefix substring instead.
        prefix = message[:15]
        deadline = time.time() + timeout
        while time.time() < deadline:
            xml = self.get_ui_hierarchy(force_refresh=True)
            if xml and prefix in xml:
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
        # RecyclerView inflates lazily: a single zero-new-found doesn't mean end of list.
        # Only exit when the XML hasn't changed across MAX_STALE consecutive scrolls.
        max_stale = 5
        stale = 0
        prev_xml = ""

        while len(all_jobs) < n_jobs and scrolls < max_scrolls:
            xml = self.get_ui_hierarchy()
            page_jobs = self.get_job_list(xml=xml)

            new_found = 0
            for job in page_jobs:
                key = f"{normalize_card_title(job.title)}\t{job.company or ''}\t{job.hr_name or ''}"
                if not key.strip("\t"):
                    continue
                known = seen.get(key)
                if known is None:
                    job.title = normalize_card_title(job.title)
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
                if xml == prev_xml:
                    stale += 1
                    if stale >= max_stale:
                        break  # XML truly unchanged — end of list
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

        return all_jobs[:n_jobs]

    def _find_description(
        self, xml: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[Tuple[int, int, int, int]], int]:
        """Return (text, bounds, list_bottom) of the detail-page description node."""
        if xml is None:
            xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
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
        root = self._parse_xml(xml)
        if root is None:
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
        """Open the filter panel and apply criteria via ``set_filter()``."""
        if not self.tap_element("filter_btn"):
            return False
        time.sleep(0.8)
        return self.set_filter(salary=salary, experience=experience, city=city)

    def navigate_to_tab(self, tab: str) -> bool:
        """
        Switch to a bottom-nav tab by name.

        Args:
            tab: One of 'recommend', 'jobs', 'messages', 'profile'.
        Returns:
            True if the tab was tapped successfully.
        """
        xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
            return False

        labels = self._TAB_TEXTS.get(tab, [tab])

        # Primary: resource-ID-based lookup (tv_tab_N / cl_tab_N)
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

        # Fallback: text-based lookup — find any node whose text matches a
        # tab label; the node itself or its closest clickable ancestor is tapped.
        for node in root.iter("node"):
            if node.attrib.get("text", "") in labels:
                bounds = parse_bounds(node.attrib.get("bounds", ""))
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
        root = self._parse_xml(xml)
        if root is None:
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
