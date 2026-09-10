"""
LinkedIn Automation Skill

Provides high-level operations for the LinkedIn Android app using
AndroidSkill as the base (with injected ADBManager for device control).

Real-device findings (Pixel 8a, LinkedIn v9.x, 2026-09-09):
- LinkedIn uses Jetpack Compose (SDUI) for job list and detail pages —
  no per-item resource-ids; all content is in text/content-desc attributes.
- Bottom nav tabs confirmed: tab_feed, tab_jobs, tab_relationships,
  tab_notifications, tab_post.
- Job card identification: content-desc = "关闭{title}职位" at x≈985-1069.
- Job detail opens as a bottom sheet overlay; tap the title Button to get full page.
- Description "更多" expands via accessibility ACTION_CLICK (uiautomator2),
  NOT via coordinate tap (Compose AnnotatedString).
- Applicant/location/time in single combined node:
  "城市 · 已转发的时间: X · N 位会员点击了申请"
"""

import logging
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from skills.android.android_skill import AndroidSkill
from skills.android.ui_types import UIElement, parse_bounds


LINKEDIN_PACKAGE = "com.linkedin.android"
_SAFE_TAP_MAX_Y  = 2000  # cards below this y get scrolled into view before tap

_TRAILING_WHITESPACE = re.compile(r"\s+$")
# Matches combined info field, e.g.:
#   "中国 上海市 · 的时间: 5 天前 · 23 位申请者"
#   "上海市 · 已转发的时间: 1 周前 · 30 位会员点击了申请"
_INFO_PATTERN = re.compile(
    r"^(?P<location>.+?)\s*·\s*"
    r"(?:已转发的时间:\s*|的时间:\s*)?(?P<posted>.+?)\s*·\s*"
    r"(?P<applicants>\d+\s*位[^·]+)",
    re.DOTALL,
)


def normalize_job_title(title: str) -> str:
    """Strip trailing whitespace/special chars for stable dedup."""
    # Also strip the LinkedIn "(已通过验证的职位)" verification suffix
    t = re.sub(r"\s*\(已通过验证的职位\)\s*$", "", title or "").strip()
    return _TRAILING_WHITESPACE.sub("", t).strip()


class PageState:
    HOME        = "home"
    JOB_LIST    = "job_list"
    JOB_DETAIL  = "job_detail"
    DIALOG      = "dialog"
    UNKNOWN     = "unknown"


class DialogType:
    SESSION_EXPIRED = "session_expired"
    RATE_LIMITED    = "rate_limited"
    JOB_CLOSED      = "job_closed"
    CAPTCHA         = "captcha"
    DISMISSED       = "dismissed"
    NONE            = "none"
    UNKNOWN_DIALOG  = "unknown_dialog"

    _SIGNATURES: Dict[str, str] = {
        # Session expired / login required
        "Sign in":                  "session_expired",
        "Log in":                   "session_expired",
        "Sign In":                  "session_expired",
        "Log In":                   "session_expired",
        "登录":                     "session_expired",
        # Rate limiting
        "You've reached the":       "rate_limited",
        "too many requests":        "rate_limited",
        "temporarily restricted":   "rate_limited",
        # Job closed
        "No longer accepting":      "job_closed",
        "no longer accepting":      "job_closed",
        "职位已关闭":               "job_closed",
        "该职位已停止招聘":         "job_closed",
        # CAPTCHA / bot detection
        "verify you're human":      "captcha",
        "verify that you're human": "captcha",
        "I'm not a robot":          "captcha",
    }


@dataclass
class JobInfo:
    title:       str = ""
    company:     str = ""
    location:    str = ""
    posted_time: str = ""
    easy_apply:  bool = False
    tap_x:       int = 0
    tap_y:       int = 0
    raw:         Dict[str, Any] = field(default_factory=dict)


class LinkedInAutomationSkill(AndroidSkill):
    """
    High-level automation skill for LinkedIn.

    Real device confirmed resource-ids (only home-tab pages have IDs;
    job list / detail use Compose SDUI with only sdui_compose_view).
    """

    # Confirmed resource-ids from real device (2026-09-09)
    ELEMENTS: Dict[str, str] = {
        "search_bar":       f"{LINKEDIN_PACKAGE}:id/search_bar",
        "search_bar_text":  f"{LINKEDIN_PACKAGE}:id/search_bar_text",
        "tab_home":         f"{LINKEDIN_PACKAGE}:id/tab_feed",
        "tab_network":      f"{LINKEDIN_PACKAGE}:id/tab_relationships",
        "tab_jobs":         f"{LINKEDIN_PACKAGE}:id/tab_jobs",
        "tab_notifications":f"{LINKEDIN_PACKAGE}:id/tab_notifications",
        "tab_post":         f"{LINKEDIN_PACKAGE}:id/tab_post",
        "me_launcher":      f"{LINKEDIN_PACKAGE}:id/me_launcher",
    }

    _TAB_TEXTS: Dict[str, List[str]] = {
        "home":    ["首页", "Home"],
        "network": ["我的人脉", "My Network"],
        "jobs":    ["职位", "Jobs"],
    }

    APP_PACKAGE = LINKEDIN_PACKAGE

    def __init__(
        self,
        adb_manager,
        output_dir: str = "",
        device_id: Optional[str] = None,
        action_delay: float = 1.5,
    ):
        super().__init__(
            device_id=device_id,
            adb=adb_manager,
            output_dir=output_dir or str(Path(tempfile.gettempdir()) / "pixelclaw_output"),
            action_delay=action_delay,
        )
        self._u2 = None  # lazy uiautomator2 connection

    def _get_u2(self):
        """Lazy connect via uiautomator2 (used for accessibility clicks)."""
        if self._u2 is None:
            try:
                import uiautomator2 as u2
                self._u2 = u2.connect(self.device_id)
            except Exception as e:
                self._logger.warning("[u2] 连接失败: %s", e)
        return self._u2

    # ------------------------------------------------------------------
    # App lifecycle
    # ------------------------------------------------------------------

    def _lock_portrait(self) -> None:
        """Lock screen rotation to portrait (0°).

        LinkedIn's Compose UI renders fine in landscape, but the
        accessibility-tree bounds shift to a 2400-wide coordinate space,
        which breaks all tap-coordinate calculations.  Locking portrait
        before every navigation sequence prevents this silently.
        """
        self._adb("shell settings put system accelerometer_rotation 0")
        self._adb("shell settings put system user_rotation 0")

    def _pre_launch(self) -> None:
        self._lock_portrait()

    # ------------------------------------------------------------------
    # UI hierarchy
    # ------------------------------------------------------------------

    def get_ui_hierarchy(self, force_refresh: bool = False) -> str:
        """Get the current UI hierarchy as XML.

        Uses uiautomator2's dump_hierarchy() instead of `adb shell uiautomator dump`
        because the latter has a known cache bug on LinkedIn's Compose UI:
        after a sheet→page transition, it returns stale list-only content
        instead of the actually visible sheet. uiautomator2 talks to the
        accessibility service directly and reports the real on-screen tree.
        Falls back to adb dumpsys if u2 is unavailable.
        """
        d = self._get_u2()
        if d is not None:
            try:
                xml = d.dump_hierarchy()
                self.last_ui_dump = xml or ""
                return self.last_ui_dump
            except Exception as e:
                self._logger.warning("[get_ui_hierarchy] u2 dump 失败, fallback: %s", e)
        # Fallback: adb dumpsys (unreliable on Compose sheet transitions)
        self._adb("shell uiautomator dump /sdcard/window_dump.xml")
        ok, content = self._adb("shell cat /sdcard/window_dump.xml")
        self.last_ui_dump = content if ok else ""
        return self.last_ui_dump

    def _parse_xml(self, xml: Optional[str]) -> Optional[ET.Element]:
        if not xml:
            return None
        try:
            return ET.fromstring(xml)
        except ET.ParseError:
            return None

    # ------------------------------------------------------------------
    # Dialog detection and handling
    # ------------------------------------------------------------------

    def detect_dialog(self, xml: Optional[str] = None) -> str:
        if xml is None:
            xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
            return DialogType.NONE
        for node in root.iter("node"):
            t = node.attrib.get("text", "") + node.attrib.get("content-desc", "")
            for kw, dtype in DialogType._SIGNATURES.items():
                if kw in t:
                    self._logger.info("[detect_dialog] %s (触发词: %s)", dtype, kw)
                    return dtype
        return DialogType.NONE

    def dismiss_dialog(self, dialog_type: str) -> bool:
        self.press_back()
        time.sleep(0.8)
        return True

    def ensure_ready(self) -> bool:
        xml = self.get_ui_hierarchy()
        if not xml:
            return False
        dialog = self.detect_dialog(xml)
        if dialog == DialogType.NONE:
            return True
        self._logger.warning("[ensure_ready] 弹窗: %s", dialog)
        self.dismiss_dialog(dialog)
        return dialog not in (DialogType.SESSION_EXPIRED, DialogType.RATE_LIMITED,
                               DialogType.CAPTCHA)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _has_bottom_nav(self, xml: str) -> bool:
        """Return True if the bottom navigation bar is currently visible."""
        return self.ELEMENTS["tab_jobs"] in xml

    def _back_to_main_screen(self, max_backs: int = 5) -> bool:
        """Press Back until the bottom nav bar is visible (up to max_backs times)."""
        for _ in range(max_backs):
            xml = self.get_ui_hierarchy()
            if self._has_bottom_nav(xml):
                return True
            self._logger.debug("[back_to_main] 底部导航不可见，按 Back")
            self._adb("shell input keyevent 4")
            time.sleep(1.2)
        xml = self.get_ui_hierarchy()
        return self._has_bottom_nav(xml)

    def navigate_to_tab(self, tab_name: str) -> bool:
        """Navigate to a confirmed bottom-nav tab by resource-id or text."""
        rid_map = {
            "home":    self.ELEMENTS["tab_home"],
            "network": self.ELEMENTS["tab_network"],
            "jobs":    self.ELEMENTS["tab_jobs"],
        }

        # Make sure the bottom nav is visible first
        if not self._back_to_main_screen():
            self._logger.error("[navigate_to_tab] 无法返回主屏幕（底部导航不可见）")
            return False

        xml = self.get_ui_hierarchy()

        # Try by resource-id first (confirmed IDs)
        rid = rid_map.get(tab_name)
        if rid:
            elem = self.find_element(resource_id=rid, xml=xml)
            if elem:
                self._tap_element(elem)
                time.sleep(1.5)
                return True

        # Fallback: by text/content-desc
        for label in self._TAB_TEXTS.get(tab_name, []):
            elem = (self.find_element(text=label, xml=xml) or
                    self.find_element(content_desc=label, xml=xml))
            if elem:
                self._tap_element(elem)
                time.sleep(1.5)
                return True

        return False

    @staticmethod
    def _is_search_results_page(xml: str) -> bool:
        """True when the current screen is the job search results page.

        Two card layouts exist:
        1. Jobs-tab browse view:  close button content-desc "关闭{title}职位"
                                  AND tab_jobs present → this is the BROWSE tab, NOT search results
        2. Job search results:    card content-desc "{title}, 已验证, {company}, ..., Button"
                                  AND tab_jobs ABSENT
        """
        return "已验证" in xml and "tab_jobs" not in xml

    def browse_jobs(self, keyword: str) -> bool:
        """Navigate to job search results using an HTTPS App Link.

        LinkedIn's HTTPS deep link is routed to the native app after
        `pm set-app-links --package com.linkedin.android 2 all` enables it.
        This avoids the "Open with" chooser dialog.
        """
        import urllib.parse
        encoded = urllib.parse.quote(keyword)
        https_url = f"https://www.linkedin.com/jobs/search/?keywords={encoded}"

        # Enable LinkedIn as the App Link handler (idempotent, safe to repeat).
        self._adb("shell pm set-app-links --package com.linkedin.android 2 all")

        # If LinkedIn is already foreground (e.g. after a previous search), the
        # VIEW intent will be silently ignored ("Activity not started, intent
        # has been delivered to currently running top-most instance"). Force-stop
        # first so the URL intent actually launches a fresh Search Results
        # activity.
        self._adb("shell am force-stop com.linkedin.android")
        self._lock_portrait()
        time.sleep(1.0)

        self._logger.debug("[browse_jobs] HTTPS App Link: %s", https_url)
        self._adb(f'shell am start -a android.intent.action.VIEW -d "{https_url}"')

        for _ in range(12):
            time.sleep(1.5)
            xml = self.get_ui_hierarchy(force_refresh=True)
            if self._is_search_results_page(xml):
                self._logger.debug("[browse_jobs] 搜索结果页已加载（已验证卡片检测到）")
                return True

        self._logger.warning("[browse_jobs] 等待职位列表超时，尝试继续")
        return True

    # ------------------------------------------------------------------
    # Job list parsing
    # ------------------------------------------------------------------

    def get_job_list(self, xml: Optional[str] = None) -> List[JobInfo]:
        """
        Parse job list from XML.

        Supports two layouts:
        1. Jobs-tab browse: content-desc "关闭{title}职位" close button at right edge
        2. Search results:  content-desc "{title}, 已验证, {company}, ..., Button"
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
            return []
        if self._is_search_results_page(xml):
            return self._parse_jobs_by_button_format(root)
        return self._parse_jobs_by_close_button(root)

    def _parse_jobs_by_close_button(self, root: ET.Element) -> List[JobInfo]:
        """
        Use the close-button pattern to identify job cards.
        Pattern: content-desc = "关闭{title}职位" at x=985-1069
        """
        jobs: List[JobInfo] = []
        close_pattern = re.compile(r"^关闭(.+?)职位$")

        for node in root.iter("node"):
            desc = node.attrib.get("content-desc", "").strip()
            m = close_pattern.match(desc)
            if not m:
                continue
            raw_title = m.group(1).strip()
            title = normalize_job_title(raw_title)
            if not title:
                continue

            # The close button is at the right edge; job card tap target
            # is the main content at left, same y band
            bounds = node.attrib.get("bounds", "")
            bm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
            if not bm:
                continue
            x1, y1, x2, y2 = int(bm.group(1)), int(bm.group(2)), int(bm.group(3)), int(bm.group(4))
            card_center_y = (y1 + y2) // 2

            # Tap target: left side of card row (avoid close button at right)
            tap_x = 540
            tap_y = card_center_y

            # Extract company, location, easy_apply from sibling text nodes
            company = location = posted = ""
            easy_apply = False
            self._extract_card_metadata(root, title, raw_title, y1, y2,
                                         company, location, posted, easy_apply,
                                         out_dict := {})
            company    = out_dict.get("company", "")
            location   = out_dict.get("location", "")
            posted     = out_dict.get("posted", "")
            easy_apply = out_dict.get("easy_apply", False)

            jobs.append(JobInfo(
                title=title,
                company=company,
                location=location,
                posted_time=posted,
                easy_apply=easy_apply,
                tap_x=tap_x,
                tap_y=tap_y,
            ))
        return jobs

    def _extract_card_metadata(
        self, root: ET.Element,
        title: str, raw_title: str,
        y1: int, y2: int,
        company: str, location: str, posted: str, easy_apply: bool,
        out_dict: dict,
    ) -> None:
        """
        Scan sibling nodes in the same y-band as the job card to extract metadata.
        Expected order: [title node] [company node] [location node] [time node] ...
        """
        easy_keywords = ("快速申请", "抢先申请", "Easy Apply")
        found = {"title": False, "company": "", "location": "", "posted": "", "easy": False}
        margin = (y2 - y1) * 2

        for node in root.iter("node"):
            t = node.attrib.get("text", "").strip()
            d = node.attrib.get("content-desc", "").strip()
            b = node.attrib.get("bounds", "")
            bm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
            if not bm:
                continue
            ny1, ny2 = int(bm.group(2)), int(bm.group(4))
            # Must be in same vertical band as the card (within margin)
            if not (y1 - margin <= ny1 and ny2 <= y2 + margin):
                continue

            content = t or d
            if not content:
                continue

            # Skip the close-button node itself
            if re.match(r"^关闭.+职位$", content):
                continue

            # The title node (may have "(已通过验证的职位)" suffix)
            if raw_title in content or title in content:
                found["title"] = True
                continue

            if any(k in content for k in easy_keywords):
                found["easy"] = True
                continue

            # After finding title, fill company then location
            if found["title"]:
                if not found["company"]:
                    found["company"] = content
                elif not found["location"]:
                    found["location"] = content
                elif not found["posted"] and re.search(r"\d+\s*(天|周|月|小时|day|week|hour|month)", content):
                    found["posted"] = content

        out_dict.update({
            "company":    found["company"],
            "location":   found["location"],
            "posted":     found["posted"],
            "easy_apply": found["easy"],
        })

    def _parse_jobs_by_button_format(self, root: ET.Element) -> List[JobInfo]:
        """Parse job cards from the search results page.

        On this page, each card is a clickable Button node whose content-desc has
        the form: "{title}, 已验证, {company}, {location}[, other info], Button"
        """
        jobs: List[JobInfo] = []
        easy_keywords = ("快速申请", "抢先申请", "Easy Apply")
        seen_titles: set = set()

        for node in root.iter("node"):
            desc = node.attrib.get("content-desc", "").strip()
            if "已验证" not in desc:
                continue
            if node.attrib.get("clickable", "false") != "true":
                continue

            # Strip trailing ", Button" (role appended by Compose accessibility)
            if desc.endswith(", Button"):
                desc = desc[: -len(", Button")]

            # Split on first ", 已验证, " to separate title from the rest
            parts = desc.split(", 已验证, ", 1)
            if len(parts) != 2:
                continue
            raw_title = parts[0].strip()
            title = normalize_job_title(raw_title)
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)

            rest = parts[1]  # "Company, Location[, other]"
            rest_parts = [p.strip() for p in rest.split(", ")]
            company = rest_parts[0] if rest_parts else ""
            location = rest_parts[1] if len(rest_parts) > 1 else ""
            easy_apply = any(k in desc for k in easy_keywords)

            bounds = node.attrib.get("bounds", "")
            bm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
            if not bm:
                continue
            x1, y1, x2, y2 = int(bm.group(1)), int(bm.group(2)), int(bm.group(3)), int(bm.group(4))
            tap_x = (x1 + x2) // 2
            tap_y = (y1 + y2) // 2

            jobs.append(JobInfo(
                title=title,
                company=company,
                location=location,
                easy_apply=easy_apply,
                tap_x=tap_x,
                tap_y=tap_y,
            ))
        return jobs

    # ------------------------------------------------------------------
    # Job detail navigation
    # ------------------------------------------------------------------

    def navigate_to_job(self, job: JobInfo) -> bool:
        """
        Tap on a job card to open the bottom sheet, then use uiautomator2
        to click the title Button and navigate to the full detail page.

        Sets self._nav_depth = 2 if full page opened, 1 if only bottom sheet.
        Call navigate_back_from_job() to return to the correct screen.
        """
        if not (job.tap_x and job.tap_y):
            return False

        self._nav_depth = 1  # default: bottom sheet only

        # If card is near screen bottom, scroll it into mid-viewport first.
        # Tapping at y > 2000 on Pixel 8a often hits the gesture-nav area
        # instead of the card, preventing the bottom sheet from opening.
        if job.tap_y > _SAFE_TAP_MAX_Y:
            offset = min(job.tap_y - 1200, 1200)
            self._logger.debug(
                "[navigate_to_job] tap_y=%d 超出安全区，向上滚动 %dpx",
                job.tap_y, offset,
            )
            self._adb(
                f"shell input swipe 540 1800 540 {1800 - offset} 500"
            )
            time.sleep(1.0)
            xml = self.get_ui_hierarchy(force_refresh=True)
            target = normalize_job_title(job.title)
            for refreshed in self.get_job_list(xml=xml):
                if normalize_job_title(refreshed.title) == target:
                    job.tap_x = refreshed.tap_x
                    job.tap_y = refreshed.tap_y
                    self._logger.debug(
                        "[navigate_to_job] 滚动后新坐标 (%d, %d)",
                        job.tap_x, job.tap_y,
                    )
                    break

        # Step 1: tap job card row → opens bottom sheet
        self._logger.info("[navigate_to_job] 点击坐标 (%d, %d)  job='%s'",
                          job.tap_x, job.tap_y, job.title[:40])
        self._adb(f"shell input tap {job.tap_x} {job.tap_y}")
        time.sleep(self.action_delay + 1.0)

        # Verify bottom sheet opened: bottom nav should NOT be visible
        # Retry a few times — the sheet may not be in the dumpsys immediately
        # if the previous sheet is still finishing its close animation.
        xml_after_tap = ""
        for _attempt in range(5):
            xml_after_tap = self.get_ui_hierarchy(force_refresh=True)
            if not self._has_bottom_nav(xml_after_tap):
                break
            time.sleep(1.0)
        if self._has_bottom_nav(xml_after_tap):
            self._logger.warning("[navigate_to_job] 卡片点击后仍在列表页，导航失败")
            self._nav_depth = 0
            return False

        # Step 1b: verify the bottom sheet is showing the right job.
        # The close button in the bottom sheet has content-desc "关闭{title}职位".
        # Check that it exists; if not, we tapped the wrong card.
        expected_close = f"关闭{job.title.split('(')[0].strip()}职位"
        xml_check = xml_after_tap
        if expected_close not in xml_check:
            # Loose check: just see if the first word of the title appears in XML
            first_word = (job.title.split()[0] if job.title else "").strip()
            if first_word and first_word not in xml_check:
                self._logger.warning(
                    "[navigate_to_job] 底部弹出层内容与预期不符（找不到 '%s'），跳过", first_word
                )
                self._adb("shell input keyevent 4")
                time.sleep(0.8)
                self._nav_depth = 0
                return False

        # Stay in bottom sheet (nav_depth=1).
        # Full-page navigation via Button click is unreliable because u2 may pick up
        # background card Buttons. The bottom sheet already exposes company, location,
        # and partial description — sufficient for our use case.
        self._logger.debug("[navigate_to_job] 底部弹出层已打开，保持 nav_depth=1")

        return True

    def navigate_back_from_job(self) -> bool:
        """Press Back the correct number of times to return to the job list.

        Returns True if successfully back on the search results list,
        False if we ended up somewhere else (caller should recover).
        """
        nav_depth = getattr(self, "_nav_depth", 1)
        self._nav_depth = 0

        for attempt in range(nav_depth + 1):
            self._adb("shell input keyevent 4")
            time.sleep(1.5)
            xml = self.get_ui_hierarchy(force_refresh=True)
            if self._is_search_results_page(xml) and "公司: " not in xml:
                self._logger.debug("[navigate_back_from_job] 列表已恢复 (attempt %d)", attempt + 1)
                return True
            time.sleep(0.5)

        self._logger.warning("[navigate_back_from_job] %d 次 back 后仍未回到列表",
                             nav_depth + 1)
        return False

    def expand_description(self) -> bool:
        """
        Expand the truncated job description by performing an accessibility
        ACTION_CLICK on the description text node via uiautomator2.

        LinkedIn uses Compose AnnotatedString for the "更多" link, which
        does NOT respond to coordinate-based input tap. The accessibility
        ACTION_CLICK (uiautomator2's .click()) expands it reliably.
        """
        d = self._get_u2()
        if d is None:
            self._logger.warning("[expand_description] uiautomator2 不可用，跳过")
            return False

        # Find the node whose text contains the '更多' ClickableSpan.
        # Click at offset (0.9, 0.95) near the bottom-right to trigger the span.
        # Y-coordinate filter: only click elements that live inside the bottom
        # sheet (which opens after we tap a job card).  The background search
        # results list also contains a "更多" ClickableSpan at the top of the
        # first card; without this filter, u2 picks that one and reopens Job 1
        # every time.  Compute a screen-height-aware cutoff via `wm size`.
        try:
            _, size_out = self._adb("shell wm size")
            import re as _re
            m = _re.search(r"(\d+)x(\d+)", size_out)
            screen_h = int(m.group(1)) * int(m.group(2)) if m else 2400
            # Hmm — wm size returns "WxH", parse correctly:
            m = _re.search(r"Physical size:\s*(\d+)x(\d+)", size_out) or _re.search(r"(\d+)x(\d+)", size_out)
            if m:
                screen_h = int(m.group(2))
            else:
                screen_h = 2400
            sheet_min_top = int(screen_h * 0.35)   # ≈840 on Pixel 8a
        except Exception:
            sheet_min_top = 840

        def _in_sheet(info: dict) -> bool:
            """True iff the element's top edge sits inside the bottom sheet."""
            b = info.get("bounds") or {}
            top = b.get("top") if isinstance(b, dict) else None
            if top is None:
                # Some u2 builds return bounds as a 4-tuple-like list
                try:
                    top = int(b[1]) if isinstance(b, (list, tuple)) and len(b) >= 2 else None
                except Exception:
                    top = None
            return (top is not None) and (top >= sheet_min_top)

        try:
            el = None
            for selector in [
                d(textContains=" 更多"),  # \xa0更多
                d(textContains="更多"),         # 更多
                d(textContains="Position Overview"),
                d(textContains="About "),               # "About CompanyName"
            ]:
                # Iterate ALL matches, prefer the first one inside the bottom sheet
                for node in selector:
                    info = node.info
                    txt = info.get("text", "") or info.get("contentDescription", "")
                    if len(txt) > 80 and _in_sheet(info):
                        el = node
                        break
                if el is not None:
                    break

            # English fallbacks (short standalone buttons)
            if el is None:
                for txt in ("See more", "Show more"):
                    c = d(text=txt)
                    for node in c:
                        if _in_sheet(node.info):
                            el = node
                            break
                    if el is not None:
                        break

            if el is None:
                self._logger.debug(
                    "[expand_description] 未找到 sheet 内展开按钮 (sheet_min_top=%d)",
                    sheet_min_top,
                )
                return False

            # Click near bottom-right to trigger the ClickableSpan at end of text
            el.click(offset=(0.9, 0.95))
            time.sleep(2.0)
            self._logger.info("[expand_description] 已通过无障碍 click 展开描述")
            return True
        except Exception as e:
            self._logger.warning("[expand_description] 失败: %s", e)
            return False

    # ------------------------------------------------------------------
    # Job detail extraction
    # ------------------------------------------------------------------

    def get_job_detail(self, xml: Optional[str] = None) -> dict:
        """
        Extract structured fields from the job detail page.

        Confirmed patterns (real device):
        - Company: content-desc = "公司: {name}。"
        - Combined info: "{city} · 已转发的时间: X · N 位会员点击了申请"
        - Description: text node starting with job content (post expand_description)
        - Easy apply: text contains "快速申请" or "抢先申请"
        """
        if xml is None:
            xml = self.get_ui_hierarchy(force_refresh=True)
        root = self._parse_xml(xml)
        if root is None:
            return {}

        detail: dict = {
            "title": "", "company": "", "location": "",
            "description": "", "applicant_count": "", "posted_time": "",
            "seniority_level": "", "employment_type": "", "job_function": "",
            "industries": "", "easy_apply": False, "skills": [], "raw_texts": [],
        }

        # Collect text values from background search-result card nodes so we can
        # exclude them when picking the description.  The search results page renders
        # as a backdrop under the bottom sheet and can pollute description extraction.
        background_texts: set = set()
        for node in root.iter("node"):
            if ("已验证" in node.attrib.get("content-desc", "")
                    and node.attrib.get("clickable", "false") == "true"):
                for child in node.iter("node"):
                    ct = child.attrib.get("text", "").strip()
                    if ct:
                        background_texts.add(ct)

        all_texts: List[str] = []
        easy_keywords = ("快速申请", "抢先申请", "Easy Apply", "快速申请按钮")
        ui_noise = ("逐步淘汰", "职位订阅已开启", "尝试用 AI 进行职位搜索")

        for node in root.iter("node"):
            t = node.attrib.get("text", "").strip()
            d = node.attrib.get("content-desc", "").strip()
            content = t or d
            if not content:
                continue

            # De-duplicate
            if content not in all_texts:
                all_texts.append(content)

            # Easy apply
            if any(k in content for k in easy_keywords):
                detail["easy_apply"] = True

            # Company: "公司: Autodesk。" pattern
            if not detail["company"] and d.startswith("公司: ") and d.endswith("。"):
                detail["company"] = d[3:-1].strip()

            # Combined location/time/applicants: "城市 · 的时间: X · N 位申请者" or similar
            _COMBINED_MARKERS = ("位申请者", "位会员点击了申请", "applicants")
            if not detail["location"] and any(m in content for m in _COMBINED_MARKERS):
                m = _INFO_PATTERN.match(content)
                if m:
                    detail["location"]       = m.group("location").strip()
                    detail["posted_time"]    = m.group("posted").strip()
                    detail["applicant_count"]= m.group("applicants").strip()
                else:
                    # Fallback: split on ·
                    parts = [p.strip() for p in content.split("·")]
                    if parts:
                        detail["location"] = parts[0]
                    if len(parts) > 1:
                        detail["posted_time"] = parts[1]
                    if len(parts) > 2:
                        detail["applicant_count"] = parts[2]

            # Description: longest text node (after expand_description is called).
            # Exclude text from background search-result cards to avoid
            # picking up a card's description preview that sits behind the sheet.
            if t and len(t) > len(detail["description"]) and len(t) > 50:
                if (t not in background_texts
                        and "位会员点击了申请" not in t and "公司: " not in d
                        and "筛选" not in t and not t.startswith("按")
                        and not any(n in t for n in ui_noise)):
                    detail["description"] = t

        detail["raw_texts"] = all_texts

        # Debug: log top candidate texts for description
        candidates = sorted(
            [(len(t), t) for t in all_texts
             if len(t) > 50 and t not in background_texts
             and "位会员点击了申请" not in t and "筛选" not in t
             and not any(n in t for n in ui_noise)],
            reverse=True,
        )[:3]
        for rank, (ln, snippet) in enumerate(candidates, 1):
            self._logger.info("[get_job_detail] 描述候选 #%d len=%d: %.80s", rank, ln, snippet)

        # Heuristic fallbacks for company, location, seniority, employment
        self._fill_missing_fields(detail, all_texts)

        return detail

    def _fill_missing_fields(self, detail: dict, texts: List[str]) -> None:
        # Exact-match sets: LinkedIn criteria nodes are short standalone strings.
        # Substring matching causes false positives on search-result card content-desc.
        _SENIORITY_EXACT = {
            "Entry level", "Associate", "Mid-Senior level",
            "Director", "Executive", "Internship", "Not Applicable",
            "初级", "中级", "高级",
        }
        _EMPLOYMENT_EXACT = {
            "Full-time", "Part-time", "Contract", "Temporary",
            "Internship", "全职", "兼职", "合同", "实习",
        }

        for t in texts:
            if not detail["applicant_count"] and ("applicant" in t.lower() or "位会员" in t):
                detail["applicant_count"] = t
            if not detail["seniority_level"] and t in _SENIORITY_EXACT:
                detail["seniority_level"] = t
            if not detail["employment_type"] and t in _EMPLOYMENT_EXACT:
                detail["employment_type"] = t
            if not detail["posted_time"] and re.search(r"\d+\s*(天|周|月|小时|day|week|hour|month)", t):
                detail["posted_time"] = t

        # Company fallback: look for a text after the title that looks like a company name
        _SKIP_COMPANY = ("·", "申请", "保存", "职位", "验证", "通过", "关注",
                         "招聘", "已通过", "Easy Apply", "快速申请", "筛选", "排序",
                         "推荐", "相似", "订阅", "关于", "位员工", "位关注")
        if not detail["company"] and len(texts) >= 2:
            for t in texts[1:8]:
                if (4 <= len(t) <= 50
                        and not any(k in t for k in _SKIP_COMPANY)
                        and not re.match(r"^\d", t)
                        and not t.startswith("按")):
                    detail["company"] = t
                    break

    # ------------------------------------------------------------------
    # Scroll helpers
    # ------------------------------------------------------------------

    def scroll_down(self, start_y: int = 1600, end_y: int = 800) -> None:
        self._adb(f"shell input swipe 540 {start_y} 540 {end_y} 600")

    def recover_detail_page(self) -> bool:
        xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
            return False
        for node in root.iter("node"):
            t = node.attrib.get("text", "")
            if t in ("Retry", "Try again", "重试"):
                b = node.attrib.get("bounds", "")
                bm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
                if bm:
                    cx = (int(bm.group(1)) + int(bm.group(3))) // 2
                    cy = (int(bm.group(2)) + int(bm.group(4))) // 2
                    self._adb(f"shell input tap {cx} {cy}")
                    time.sleep(2.0)
                    return True
        return False

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _tap_element(self, elem) -> None:
        center = elem.center if hasattr(elem, "center") else None
        if center is None:
            return
        self._adb(f"shell input tap {center[0]} {center[1]}")

    def _type_text(self, text: str) -> bool:
        return self.type_text(text)
