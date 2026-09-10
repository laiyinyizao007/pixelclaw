#!/usr/bin/env python3
"""
小红书自动化 Skill

提供基于 UI Automator 的精确坐标获取和自动化操作。

使用方法:
    from skills.xhs import XHSAutomationSkill

    xhs = XHSAutomationSkill()
    xhs.favorite_current_post()  # 收藏当前帖子
    xhs.like_current_post()      # 点赞当前帖子
"""

import time
from typing import Dict, Optional, Tuple

from skills.android.android_skill import AndroidSkill


XHS_PACKAGE = "com.xingin.xhs"


class XHSAutomationSkill(AndroidSkill):
    """
    小红书自动化 Skill

    继承 AndroidSkill，只保留 XHS 特有的 ELEMENTS 映射和业务方法。
    """

    APP_PACKAGE = XHS_PACKAGE

    # 小红书 UI 元素 resource-id 映射表
    # 标注来源: [verified] = dump_xhs_ui.py 真机确认(登录态); [inferred] = 推测, 未确认
    # 最后验证: 2026-09-04, Pixel 8a (42231JEKB04971), 登录态, 288 个唯一 resource-id
    ELEMENTS = {
        # ── 帖子详情互动栏（verified, 旧版图文帖 04_blogger_profile dump） ──
        'like':          'com.xingin.xhs:id/noteLikeLayout',
        'collect':       'com.xingin.xhs:id/noteCollectLayout',
        'comment':       'com.xingin.xhs:id/noteCommentLayout',
        'input_comment': 'com.xingin.xhs:id/inputCommentTV',
        'more':          'com.xingin.xhs:id/moreOperateIV',
        'back':          'com.xingin.xhs:id/backIV',
        # ── 互动计数（verified, 旧版图文帖 04_blogger_profile dump） ──
        'like_count':    'com.xingin.xhs:id/noteLikeTV',
        'collect_count': 'com.xingin.xhs:id/noteCollectTV',
        'comment_count': 'com.xingin.xhs:id/noteCommentTV',
        # ── 视频/新版帖子互动（verified, 04_blogger_profile 登录态 dump） ──
        'follow':        'com.xingin.xhs:id/followBtn',       # [verified] 关注博主
        'share':         'com.xingin.xhs:id/navBarShareBtn',  # [verified] 分享（导航栏分享键）
        'bottom_comment':'com.xingin.xhs:id/bottomComment',   # 底部评论区
        'engage':        'com.xingin.xhs:id/engage',          # 互动区容器
        # ── 底部导航 Tab（verified, 01_home / 05_messages / 07_profile dump） ──
        'tab_home':      'com.xingin.xhs:id/index_home',
        'tab_video':     'com.xingin.xhs:id/index_store',
        'tab_post':      'com.xingin.xhs:id/index_post',
        'tab_message':   'com.xingin.xhs:id/index_message',
        'tab_me':        'com.xingin.xhs:id/index_me',
        # ── 首页搜索（verified, 01_home dump） ──
        'search_bar':    'com.xingin.xhs:id/search',
        # ── 搜索输入页（verified, 02a_search_input dump） ──
        'search_input':  'com.xingin.xhs:id/mSearchToolBarEt',
        'search_btn':    'com.xingin.xhs:id/mSearchToolBarSearchBtn',
        'search_back':   'com.xingin.xhs:id/mSearchToolBarBackIv',
        # ── 搜索结果页（verified, 02_search_results dump） ──
        'search_tab_bar':       'com.xingin.xhs:id/mSearchResultTabBar',
        'search_results_list':  'com.xingin.xhs:id/mSearchResultListContentTRv',
        'search_results_back':  'com.xingin.xhs:id/mSearchResultToolBarBackIv',
        'search_author':        'com.xingin.xhs:id/authorName',
        # ── 消息页（verified, 05_messages 登录态 dump） ──
        'msg_list':         'com.xingin.xhs:id/msgRecyclerView',
        'msg_comment_at':   'com.xingin.xhs:id/commentAndAt',
        'msg_like_collect': 'com.xingin.xhs:id/likeAndCollect',
        'msg_fans':         'com.xingin.xhs:id/fans',
        'msg_search':       'com.xingin.xhs:id/iv_search',
        # ── 个人中心（verified, 07_profile 登录态 dump） ──
        'profile_avatar':      'com.xingin.xhs:id/matrix_profile_user_head_img',
        'profile_nickname':    'com.xingin.xhs:id/profile_new_page_avatar_card_nickname',
        'profile_follow_count':'com.xingin.xhs:id/follow_count',
        'profile_fans_count':  'com.xingin.xhs:id/fans_count',
        'profile_fav_count':   'com.xingin.xhs:id/fav_count',
        'profile_search':      'com.xingin.xhs:id/profileSearchEntrance',
        'profile_share':       'com.xingin.xhs:id/profileActionBarShareView',
        # ── 发布菜单（verified, 06_publish 登录态 dump） ──
        'publish_option_1':  'com.xingin.xhs:id/rlFirst',              # 图文/视频第一选项
        'publish_option_2':  'com.xingin.xhs:id/rlSecondWithSubtitle', # 第二选项
        'publish_cancel':    'com.xingin.xhs:id/tvCancel',
        # ── 页面状态特征元素（用于 get_current_page 检测） ──
        'page_anchor_home':    'com.xingin.xhs:id/exploreCoordinator',
        'page_anchor_detail':  'com.xingin.xhs:id/noteDetailRoot',      # 旧版图文帖
        'page_anchor_video':   'com.xingin.xhs:id/matrix_video_feed_note_detail_list',  # 视频/新版帖
        'page_anchor_message': 'com.xingin.xhs:id/msgRecyclerView',
        'page_anchor_profile': 'com.xingin.xhs:id/matrix_profile_new_page_coordinator_layout',
        # ── 登录弹窗检测（verified, 00_login_popup dump） ──
        'login_popup_close':   'com.xingin.xhs:id/close',
        'login_popup_dialog':  'com.xingin.xhs:id/dialog',
        'login_popup_wechat':  'com.xingin.xhs:id/mWeiChatLoginView',
    }

    def __init__(
        self,
        adb_manager=None,
        device_id: Optional[str] = None,
        output_dir: Optional[str] = None,
        action_delay: float = 1.0,
    ):
        super().__init__(
            device_id=device_id,
            adb=adb_manager,
            output_dir=output_dir,
            action_delay=action_delay,
        )
        # Canvas 按钮坐标（从 config/devices/<device_id>.json 加载，若存在）
        self._video_bar_coords: Dict[str, Tuple[int, int]] = {}
        if device_id:
            self._video_bar_coords = self.get_canvas_coords("xhs", "video_post_bar")
            if self._video_bar_coords:
                self._logger.debug("已加载视频帖坐标: %s", self._video_bar_coords)

    # ── XHS 特有业务方法 ──────────────────────────────────────────────────────

    def favorite_current_post(self) -> bool:
        """收藏当前帖子（仅适用于旧版图文帖，可见 noteCollectLayout）。"""
        self._logger.info("[favorite] 收藏当前帖子")
        collect_btn = self.find_element(resource_id=self.ELEMENTS['collect'])
        if not collect_btn or not collect_btn.center:
            self._logger.warning("[favorite] 未找到收藏按钮，请确保在帖子详情页")
            return False
        cx, cy = collect_btn.center
        self._logger.info("[favorite] 收藏按钮位置: (%d, %d)", cx, cy)
        self.screenshot("fav_before.png")
        self.tap(cx, cy)
        time.sleep(0.5)
        self.screenshot("fav_after.png")
        self._logger.info("[favorite] 收藏完成")
        return True

    def like_current_post(self) -> bool:
        """点赞当前帖子。"""
        self._logger.info("[like] 点赞当前帖子")
        like_btn = self.find_element(resource_id=self.ELEMENTS['like'])
        if not like_btn or not like_btn.center:
            self._logger.warning("[like] 未找到点赞按钮")
            return False
        cx, cy = like_btn.center
        self._logger.info("[like] 点赞按钮: (%d, %d)", cx, cy)
        self.tap(cx, cy)
        self._logger.info("[like] 已点赞")
        return True

    def go_back(self) -> None:
        """返回上一页（优先找 back 按钮，否则 KEYCODE_BACK）。"""
        back_btn = self.find_element(resource_id=self.ELEMENTS['back'])
        if back_btn and back_btn.center:
            self.tap(*back_btn.center)
        else:
            self.press_back()
        time.sleep(0.3)


# CLI 接口
if __name__ == "__main__":
    import sys

    skill = XHSAutomationSkill()

    if len(sys.argv) < 2:
        print("小红书自动化 Skill")
        print("\n用法:")
        print(f"  python3 {sys.argv[0]} fav    # 收藏当前帖子")
        print(f"  python3 {sys.argv[0]} like   # 点赞当前帖子")
        print(f"  python3 {sys.argv[0]} back   # 返回")
        sys.exit(1)

    action = sys.argv[1]
    if action == "fav":
        skill.favorite_current_post()
    elif action == "like":
        skill.like_current_post()
    elif action == "back":
        skill.go_back()
    else:
        print(f"未知操作: {action}")
