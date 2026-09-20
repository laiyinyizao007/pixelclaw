"""
企业微信 (WeCom) Automation Skill

基于 AndroidSkill 封装企业微信消息读取所需的高层操作。

resource-id 经由 UIAutomator dump（设备 42231JEKB04971）确认，版本 2026-09：
  search_entry  com.tencent.wework:id/nxm   会话列表页右上角搜索图标
  search_input  com.tencent.wework:id/lrk   搜索页输入框 (hint="搜索")
  session_item  com.tencent.wework:id/ge0   1-on-1 联系人资料页「进入」按钮（群聊用 mid1Txt 回退）
  message_sender com.tencent.wework:id/iz3  发送人头像右侧姓名容器（取首个子 TextView）
  message_text  com.tencent.wework:id/ilm   消息气泡正文 TextView
  message_time  com.tencent.wework:id/imi   时间戳/系统消息（用时间正则过滤）
  card_container com.tencent.wework:id/ipg  卡片消息可点击容器
  card_title    com.tencent.wework:id/nmd   卡片消息标题

message_sender 为空时，parse_messages() 仍可运行，仅 ChatMessage.sender 保持空字符串。
详见 config/app_knowledge/wecom.json。
"""

import logging
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from skills.android.android_skill import AndroidSkill

WECOM_PACKAGE = "com.tencent.wework"


@dataclass
class ChatMessage:
    """从聊天页 UI dump 解析出的一条消息。"""

    sender: str = ""
    text: str = ""
    time_str: str = ""
    url: str = ""      # 卡片消息附带的外部链接（如小红书 deeplink）


class WeComAutomationSkill(AndroidSkill):
    """
    企业微信高层自动化操作。

    Args:
        adb_manager:  注入的 ADBManager 实例。
        output_dir:   截图等文件的本地存储目录。
        device_id:    ADB 设备 serial；None 使用默认设备。
        action_delay: 操作间隔秒数。
    """

    # resource-id 已通过 UIAutomator dump 确认（设备 42231JEKB04971，2026-09）
    ELEMENTS: Dict[str, str] = {
        "search_entry":    "com.tencent.wework:id/nxm",  # 会话列表页右上角搜索图标
        "search_input":    "com.tencent.wework:id/lrk",  # 搜索页 EditText (hint=搜索)
        "session_item":    "com.tencent.wework:id/ge0",  # 联系人资料页「进入」按钮
        "message_sender":  "com.tencent.wework:id/iz3",  # 发送人头像右侧姓名容器
        "message_text":    "com.tencent.wework:id/ilm",  # 消息气泡正文 TextView
        "message_time":    "com.tencent.wework:id/imi",  # 时间戳/系统消息（需时间正则过滤）
        "card_container":  "com.tencent.wework:id/ipg",  # 卡片消息可点击容器
        "card_title":      "com.tencent.wework:id/nmd",  # 卡片消息标题
        "chat_page_title": "com.tencent.wework:id/nwv",  # 聊天页标题栏（含群名，页面检测用）
    }

    # message_sender / card_* / chat_page_title 为可选项（1-on-1 或无卡片时不出现）
    _REQUIRED_ELEMENTS: frozenset = frozenset({
        "search_entry", "search_input", "session_item", "message_text", "message_time",
    })

    APP_PACKAGE = WECOM_PACKAGE

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
        self._logger = logging.getLogger(type(self).__name__)

    def _pre_launch(self) -> None:
        """唤醒屏幕并清除锁屏，确保 App 可操作。"""
        self._adb("shell input keyevent 224")
        time.sleep(1.0)
        self._adb("shell wm dismiss-keyguard")
        time.sleep(0.5)
        # WeCom 聊天页持续触发 accessibility 事件，需禁用动画才能让 uiautomator dump 稳定返回
        self._adb("shell settings put global window_animation_scale 0")
        self._adb("shell settings put global transition_animation_scale 0")
        self._adb("shell settings put global animator_duration_scale 0")

    def _elements_ready(self) -> bool:
        """必填 ELEMENTS 是否已填入真实 resource-id（message_sender 为可选项）。"""
        missing = [k for k in self._REQUIRED_ELEMENTS if not self.ELEMENTS.get(k)]
        if missing:
            self._logger.warning(
                "ELEMENTS 尚未探测（缺失: %s），open_conversation/parse_messages 不可用",
                ", ".join(sorted(missing)),
            )
            return False
        return True

    def open_conversation(self, contact_name: str) -> bool:
        """
        搜索并打开指定联系人/群的会话页。

        Returns:
            True 表示已点击进入会话；False 表示未找到或 ELEMENTS 未就绪。
        """
        if not self._elements_ready():
            return False

        # 强制刷新获取当前真实页面状态
        xml = self.get_ui_hierarchy(force_refresh=True)
        search_entry_id = self.ELEMENTS["search_entry"]

        if not self.find_element(resource_id=search_entry_id, xml=xml):
            # 不在会话列表页，先最多 Back 3 次尝试回退
            found = False
            for _ in range(3):
                self._adb("shell input keyevent 4")
                time.sleep(1.0)
                xml = self.get_ui_hierarchy(force_refresh=True)
                if self.find_element(resource_id=search_entry_id, xml=xml):
                    found = True
                    break
                # 尝试点击消息 Tab（仅在会话列表以外的页面可见时有效）
                tab = self.find_element(text="消息", xml=xml)
                if tab and tab.center:
                    self.tap(*tab.center)
                    time.sleep(1.5)
                    xml = self.get_ui_hierarchy(force_refresh=True)
                    if self.find_element(resource_id=search_entry_id, xml=xml):
                        found = True
                        break
            if not found:
                # 兜底：强制清栈重启 WeCom（应对 in-app browser 等深层状态）
                self._logger.info("Back 退出未能找到搜索入口，强制重启 WeCom 到会话列表")
                self._adb(
                    f"shell am start --activity-clear-task "
                    f"-n {WECOM_PACKAGE}/.launch.LaunchSplashActivity"
                )
                time.sleep(3.0)
                xml = self.get_ui_hierarchy(force_refresh=True)
                if not self.find_element(resource_id=search_entry_id, xml=xml):
                    self._logger.warning("未找到搜索入口")
                    return False

        # 点击搜索入口（用已刷新的 xml）
        entry = self.find_element(resource_id=search_entry_id, xml=xml)
        if not entry or not entry.center:
            self._logger.warning("未找到搜索入口")
            return False
        self.tap(*entry.center)
        time.sleep(self.action_delay)

        # 强制刷新：点击搜索入口后页面已切换，不能用旧缓存
        xml_search = self.get_ui_hierarchy(force_refresh=True)
        if not self.tap_element("search_input", xml=xml_search):
            self._logger.warning("未找到搜索输入框")
            return False
        self.type_text(contact_name)
        time.sleep(self.action_delay)

        xml = self.get_ui_hierarchy(force_refresh=True)
        # ge0 仅出现在 1-on-1 联系人资料页；群聊搜索结果直接是可点击列表行（mid1Txt 为行标题）
        # 注意：不能用 find_element(text=contact_name) 因为搜索框 lhb 在文档顺序更靠前且同样含该文本
        elem = self.find_element(resource_id=self.ELEMENTS["session_item"], xml=xml)
        if not elem or not elem.center:
            elem = self.find_element(resource_id="com.tencent.wework:id/mid1Txt", xml=xml)
        if not elem or not elem.center:
            self._logger.warning("搜索结果中未找到会话：%s", contact_name)
            return False
        if not self.tap(*elem.center):
            return False
        # 等待聊天页加载完成，预热缓存供 parse_messages() 使用
        time.sleep(self.action_delay)
        self.get_ui_hierarchy(force_refresh=True)
        return True

    def _extract_card_url(self, card_bounds: str) -> str:
        """
        点击卡片气泡，等待 WeCom 内置浏览器加载，从 copyhackinput 节点读取外部链接 URL，
        然后按 Back 回到聊天页。

        Args:
            card_bounds: 卡片节点的 bounds 字符串，格式 "[x1,y1][x2,y2]"。

        Returns:
            URL 字符串；无法提取时返回空字符串。
        """
        import re as _re
        m = _re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", card_bounds)
        if not m:
            return ""
        cx = (int(m.group(1)) + int(m.group(3))) // 2
        cy = (int(m.group(2)) + int(m.group(4))) // 2
        self.tap(cx, cy)
        time.sleep(2.0)  # 等待 WeCom 内置浏览器加载

        xml_browser = self.get_ui_hierarchy(force_refresh=True)
        root = self._parse_xml(xml_browser)
        url = ""
        if root is not None:
            for n in root.iter("node"):
                if n.attrib.get("resource-id", "") == "copyhackinput":
                    url = n.attrib.get("text", "")
                    break
        # 返回聊天页
        self._adb("shell input keyevent 4")
        time.sleep(1.0)
        return url

    def parse_messages(self, xml: Optional[str] = None, fetch_card_urls: bool = False) -> List[ChatMessage]:
        """
        解析当前聊天页可见的消息列表，包括纯文字消息和卡片消息。

        卡片消息（j5_ 可点击 + mww 标题节点）以「[卡片] <标题>」形式存入 text 字段，
        描述文字（fw9 子节点）附加在其后。若 fetch_card_urls=True，则点击每张卡片
        提取外部链接 URL（会产生额外导航动作）。

        Args:
            xml:             已有的 XML 字符串；为 None 则重新 dump。
            fetch_card_urls: 是否实际点击卡片以获取 URL（默认 False）。

        Returns:
            按 UI dump 中出现顺序排列的 ChatMessage 列表；ELEMENTS 未就绪或
            解析失败时返回空列表。
        """
        if not self._elements_ready():
            return []
        if xml is None:
            xml = self.get_ui_hierarchy()
        root = self._parse_xml(xml)
        if root is None:
            return []

        # 以 ctw（行容器）为单位解析，每个 ctw 内部直接找时间/发送人/内容
        # 这比全局列表顺序匹配更准确：时间节点 imi 就在同一个 ctw 内
        sender_id  = self.ELEMENTS.get("message_sender", "")   # iz3
        time_id    = self.ELEMENTS["message_time"]              # imi
        text_id    = self.ELEMENTS["message_text"]              # ilm
        card_id    = self.ELEMENTS.get("card_container", "")   # ipg
        title_id   = self.ELEMENTS.get("card_title", "")       # nmd

        messages: List[ChatMessage] = []
        last_time = ""

        for ctw in root.iter("node"):
            if "com.tencent.wework:id/ctw" not in ctw.attrib.get("resource-id", ""):
                continue

            # 时间：ctw 内的 imi 节点；无则继承上一条已知时间
            time_str = ""
            for n in ctw.iter("node"):
                if time_id in n.attrib.get("resource-id", ""):
                    time_str = n.attrib.get("text", "")
                    break
            if time_str:
                last_time = time_str
            else:
                time_str = last_time

            # 发送人：ctw 内的 iz3 → 第一个有 text 的子节点
            sender = ""
            if sender_id:
                for n in ctw.iter("node"):
                    if sender_id in n.attrib.get("resource-id", ""):
                        for child in n.iter("node"):
                            t = child.attrib.get("text", "")
                            if t:
                                sender = t
                                break
                        break

            # 纯文字消息：ctw 内的 ilm
            ilm = None
            for n in ctw.iter("node"):
                if text_id in n.attrib.get("resource-id", ""):
                    ilm = n
                    break
            if ilm is not None:
                text = ilm.attrib.get("text", "")
                if text:
                    messages.append(ChatMessage(sender=sender, text=text, time_str=time_str))
                continue

            # 卡片消息：ctw 内的 ipg（clickable）含 nmd 标题
            if card_id:
                for n in ctw.iter("node"):
                    if card_id not in n.attrib.get("resource-id", ""):
                        continue
                    if n.attrib.get("clickable") != "true":
                        continue
                    title = ""
                    desc = ""
                    for child in n.iter("node"):
                        child_rid = child.attrib.get("resource-id", "")
                        if title_id and title_id in child_rid:
                            title = child.attrib.get("text", "")
                        elif not child_rid:
                            t = child.attrib.get("text", "")
                            if t and t != title and not desc:
                                desc = t
                    if not title:
                        continue
                    text = f"[卡片] {title}"
                    if desc:
                        text += f"\n{desc}"
                    card_url = ""
                    if fetch_card_urls:
                        card_url = self._extract_card_url(n.attrib.get("bounds", ""))
                    messages.append(ChatMessage(sender=sender, text=text, time_str=time_str, url=card_url))
                    break

        # 向前回填：首批无时间的消息从后面第一个已知时间借用
        first_known = next((m.time_str for m in messages if m.time_str), "")
        for m in messages:
            if not m.time_str:
                m.time_str = first_known
            else:
                break

        return messages
