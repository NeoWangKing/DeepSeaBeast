# SnowLuma 支持的 QQ 接口（整理）

> **来源**：直接从服务器上 SnowLuma 的打包文件里提取的动作名（不是照抄 OneBot 标准）。
> 重新生成：`./deploy/tools/dump-snowluma-actions.sh`
> 提取到 **158** 个动作名，下面按用途分类。

**状态标记**

| 标记 | 含义 |
|---|---|
| ✅ | 我们已经在用 |
| ⚠️ | 代码写了但**没在真机上验证**（工具侧标 `experimental`，不给模型） |
| ⬜ | 还没用 |
| 风险列 | 高 = 会改变群里/账号的状态，用之前必须有权限模型 |

## 先用得上的几条结论

1. **表情包（贴图）走 `send_custom_face`**——原生 `face` 段只有那 281 个经典表情，
   「狗头」这类属于自定义表情，要用这条。
2. **看图**：订阅到图片消息后，用 `get_image` 把文件取回来，再交给视觉模型。
3. **成员档案**：`get_group_member_list` 能一次拉全，不用等消息里出现。
4. **记忆回填**：`get_group_msg_history` 能拉历史，群印象可以先补一段再实时更新。
5. **群管理接口是齐的**（踢人/禁言/改名片/退群/处理加群请求），但**风险极高**，
   要做必须配权限模型（谁能让她做、哪些群允许、要不要二次确认）。


## 一、消息发送

| 动作 | 状态 | 说明 | 风险 |
|---|---|---|---|
| `send_group_msg` | ✅ | 发群消息 | 已用 |
| `send_private_msg` | ✅ | 发私聊消息 | 已用 |
| `send_msg` | ⬜ | 通用发送（自动判别群/私） |  |
| `send_forward_msg` | ⬜ | 发合并转发 |  |
| `send_group_forward_msg` | ⬜ | 群合并转发 |  |
| `send_private_forward_msg` | ⬜ | 私聊合并转发 |  |
| `send_flash_msg` | ⬜ | 闪照 |  |
| `send_poke` | ⬜ | 戳一戳 |  |
| `send_like` | ⬜ | 名片点赞 |  |
| `send_group_sign` | ⬜ | 群打卡 |  |
| `send_custom_face` | ⬜ | **发表情包（贴图）**——「狗头」这类走这里 |  |
| `send_group_ai_record` | ⬜ | 群 AI 语音（声聊） |  |
| `get_ai_record` | ⬜ | 取 AI 语音 |  |
| `get_ai_characters` | ⬜ | AI 声聊角色列表 |  |
| `send_ark_share` | ⬜ | 发卡片 |  |
| `send_group_ark_share` | ⬜ | 群卡片 |  |
| `send_tuwen_ark` | ⬜ | 图文卡片 |  |
| `send_qzone_msg` | ⬜ | 发空间说说 |  |
| `delete_qzone_msg` | ⬜ | 删空间说说 |  |

## 二、消息操作

| 动作 | 状态 | 说明 | 风险 |
|---|---|---|---|
| `delete_msg` | ⬜ | 撤回消息 |  |
| `get_msg` | ⬜ | 取单条消息 |  |
| `get_forward_msg` | ⬜ | 取合并转发内容 |  |
| `get_image` | ⬜ | **取图片文件**（看图要用） |  |
| `get_record` | ⬜ | 取语音文件 |  |
| `get_record_text` | ⬜ | 语音转文字 |  |
| `get_ptt_text` | ⬜ | 语音转文字（另一实现） |  |
| `get_group_msg_history` | ⬜ | **拉群历史消息**（记忆回填） |  |
| `get_friend_msg_history` | ⬜ | 拉私聊历史消息 |  |
| `mark_msg_as_read` | ⬜ | 标记已读 |  |
| `mark_group_msg_as_read` | ⬜ | 群消息已读 |  |
| `mark_private_msg_as_read` | ⬜ | 私聊已读 |  |
| `set_msg_emoji_like` | ⬜ | 消息贴表情回应 |  |
| `get_msg_emoji_likes` | ⬜ | 查消息的表情回应 |  |
| `get_emoji_likes` | ⬜ | 查表情回应（同义） |  |
| `set_essence_msg` | ⬜ | 设为群精华 |  |
| `delete_essence_msg` | ⬜ | 取消精华 |  |
| `get_essence_msg_list` | ⬜ | 精华列表 |  |
| `set_input_status` | ⬜ | 「正在输入」 |  |

## 三、群管理（高风险，需要权限模型）

| 动作 | 状态 | 说明 | 风险 |
|---|---|---|---|
| `set_group_kick` | ⬜ | 踢人 | 高 |
| `set_group_kick_members` | ⬜ | 批量踢人 | 高 |
| `set_group_ban` | ⬜ | 禁言某人 | 高 |
| `set_group_whole_ban` | ⬜ | 全员禁言 | 高 |
| `get_group_shut_list` | ⬜ | 禁言列表 |  |
| `set_group_admin` | ⬜ | 设/撤管理员 | 高 |
| `get_group_admin_settings` | ⬜ | 管理员设置 |  |
| `set_group_card` | ⬜ | 改群名片 | 中 |
| `set_group_remark` | ⬜ | 改群备注 | 中 |
| `set_group_name` | ⬜ | 改群名 | 高 |
| `set_group_portrait` | ⬜ | 改群头像 | 高 |
| `set_group_leave` | ⬜ | **退群** | 高 |
| `set_group_add_request` | ⬜ | 处理加群请求 | 高 |
| `set_group_add_option` | ⬜ | 加群设置 | 高 |
| `set_group_robot_add_option` | ⬜ | 机器人加群设置 | 高 |
| `get_group_ignore_add_request` | ⬜ | 被忽略的加群请求 |  |
| `get_group_ignored_notifies` | ⬜ | 被忽略的通知 |  |
| `set_group_special_title` | ⬜ | 设专属头衔 | 中 |
| `set_group_anonymous` | ⬜ | 开/关匿名 | 中 |
| `set_group_anonymous_ban` | ⬜ | 匿名禁言 | 中 |
| `set_group_member_permissions` | ⬜ | 成员权限 | 高 |
| `set_group_member_invite_policy` | ⬜ | 邀请策略 | 高 |
| `set_group_reaction` | ⬜ | 群互动开关 |  |
| `set_group_todo` | ⬜ | 设群待办 |  |
| `get_group_todo_list` | ⬜ | 群待办列表 |  |
| `set_group_search` | ⬜ | 群可被搜索 | 中 |
| `set_group_sign` | ⬜ | 群签到设置 |  |
| `get_group_info` | ✅ | 群信息（**已用**：查群名） | 已用 |
| `get_group_info_ex` | ⬜ | 群信息（扩展） |  |
| `get_group_detail_info` | ⬜ | 群详情 |  |
| `get_group_list` | ⬜ | 群列表 |  |
| `get_group_member_info` | ⬜ | 成员信息 |  |
| `get_group_member_list` | ⬜ | **成员列表**（建成员档案用） |  |
| `get_group_honor_info` | ⬜ | 群荣誉 |  |
| `get_group_signed_list` | ⬜ | 签到列表 |  |
| `get_group_at_all_remain` | ⬜ | @全体剩余次数 |  |
| `get_group_system_msg` | ⬜ | 群系统消息 |  |

## 四、群文件 / 相册

| 动作 | 状态 | 说明 | 风险 |
|---|---|---|---|
| `upload_group_file` | ⚠️ | 上传文件到群（**已写未验证**） |  |
| `upload_private_file` | ⚠️ | 上传文件到私聊（**已写未验证**） |  |
| `get_group_root_files` | ⬜ | 群文件根目录 |  |
| `get_group_files_by_folder` | ⬜ | 按目录列文件 |  |
| `get_group_file_url` | ⬜ | 取文件下载链接 |  |
| `get_group_file_system_info` | ⬜ | 群文件系统信息 |  |
| `upload_file_stream` | ⬜ | 流式上传 |  |
| `download_file` | ⬜ | 下载文件 |  |
| `delete_group_file` | ⬜ | 删群文件 |  |
| `delete_group_folder` | ⬜ | 删文件夹 |  |
| `create_group_file_folder` | ⬜ | 建文件夹 |  |
| `rename_group_file` | ⬜ | 重命名文件 |  |
| `rename_group_file_folder` | ⬜ | 重命名文件夹 |  |
| `move_group_file` | ⬜ | 移动文件 |  |
| `get_group_album_list` | ⬜ | 群相册列表 |  |
| `get_group_album_media_list` | ⬜ | 相册内容 |  |
| `upload_image_to_qun_album` | ⬜ | 传图到相册 |  |
| `upload_video_to_qun_album` | ⬜ | 传视频到相册 |  |
| `set_group_album_media_like` | ⬜ | 相册点赞 |  |
| `get_qun_album_list` | ⬜ | 相册列表（同义） |  |

## 五、好友 / 账号 / 状态

| 动作 | 状态 | 说明 | 风险 |
|---|---|---|---|
| `get_login_info` | ✅ | 机器人自己信息（**已用**：查昵称） | 已用 |
| `get_friend_list` | ⬜ | 好友列表 |  |
| `get_friends_with_category` | ⬜ | 好友（带分组） |  |
| `get_unidirectional_friend_list` | ⬜ | 单向好友 |  |
| `set_friend_add_request` | ⬜ | 处理加好友请求 | 高 |
| `set_doubt_friends_add_request` | ⬜ | 处理可疑好友请求 | 中 |
| `get_doubt_friends_add_request` | ⬜ | 查可疑好友请求 |  |
| `delete_friend` | ⬜ | 删好友 | 高 |
| `set_friend_remark` | ⬜ | 好友备注 | 中 |
| `set_friends_category` | ⬜ | 好友分组 |  |
| `get_stranger_info` | ⬜ | 陌生人信息 |  |
| `get_recent_contact` | ⬜ | 最近联系人 |  |
| `set_qq_profile` | ⬜ | 改资料 | 中 |
| `set_qq_avatar` | ⬜ | 改头像 | 中 |
| `set_self_longnick` | ⬜ | 改长昵称 | 中 |
| `set_online_status` | ⬜ | 在线状态 |  |
| `set_diy_online_status` | ⬜ | 自定义在线状态 |  |
| `get_online_clients` | ⬜ | 在线设备 |  |
| `get_status` | ⬜ | 运行状态 |  |
| `get_version_info` | ⬜ | 版本信息 |  |
| `set_restart` | ⬜ | 重启 | 高 |
| `get_cookies` | ⬜ | 取 cookies |  |
| `get_credentials` | ⬜ | 取凭证 |  |
| `get_csrf_token` | ⬜ | 取 csrf token |  |
| `get_clientkey` | ⬜ | 取 clientkey |  |
| `can_send_image` | ⬜ | 能否发图（探测） |  |
| `can_send_record` | ⬜ | 能否发语音（探测） |  |
| `can_at_all` | ⬜ | 能否 @全体（探测） |  |
| `kick_me` | ⬜ | 踢自己下线 | 高 |

## 六、其它（空间 / 内部）

| 动作 | 状态 | 说明 | 风险 |
|---|---|---|---|
| `get_profile_like` | ⬜ | 名片赞 |  |
| `like_qzone` | ⬜ | 空间点赞 |  |
| `get_qzone_feeds` | ⬜ | 空间动态 |  |
| `get_qzone_msg_list` | ⬜ | 空间消息 |  |
| `set_qzone_ban` | ⬜ | 空间权限 |  |
| `set_qzone_msg_right` | ⬜ | 空间消息权限 |  |
| `get_share_link` | ⬜ | 取分享链接 |  |
| `get_mini_app_ark` | ⬜ | 小程序卡片 |  |
| `get_rkey` | ⬜ | 内部 rkey |  |
| `get_rkey_server` | ⬜ | 内部 rkey 服务 |  |
| `send_packet` | ⬜ | 原始协议包（底层） | 高 |
| `delete_custom_face` | ⬜ | 删自定义表情 |  |
| `move_custom_face_to_front` | ⬜ | 表情排序 |  |
| `get_collection_list` | ⬜ | 收藏列表 |  |
| `create_collection` | ⬜ | 加收藏 |  |
| `get_flash_file_list` | ⬜ | 闪存文件列表 |  |
| `get_flash_file_url` | ⬜ | 闪存文件链接 |  |
| `rename_flash_file` | ⬜ | 重命名闪存文件 |  |
| `delete_flash_file` | ⬜ | 删闪存文件 |  |
| `create_flash_task` | ⬜ | 创建闪存任务 |  |
| `upload_number` | ⬜ | 上传编号（内部） |  |
| `get_fileset_id` | ⬜ | 文件集 id（内部） |  |
| `get_fileset_info` | ⬜ | 文件集信息（内部） |  |
| `download_fileset` | ⬜ | 下载文件集（内部） |  |
| `get_file` | ⬜ | 取文件（内部） |  |
| `download_file_stream` | ⬜ | 流式下载 |  |
| `download_file_image_stream` | ⬜ | 流式下载图片 |  |
| `download_file_record_stream` | ⬜ | 流式下载语音 |  |

## 附录：提取到的全部动作名（158 个）

按字母序，含上面没分类的内部接口：

```
can_at_all
can_send_image
can_send_record
create_collection
create_flash_task
create_group
create_group_file_folder
create_time
delete_custom_face
delete_essence_msg
delete_flash_file
delete_friend
delete_group_file
delete_group_file_folder
delete_group_folder
delete_msg
delete_qzone_msg
download_file
download_file_image_stream
download_file_record_stream
download_file_stream
download_fileset
get_ai_characters
get_ai_record
get_clientkey
get_collection_list
get_cookies
get_credentials
get_csrf_token
get_doubt_friends_add_request
get_emoji_likes
get_essence_msg_list
get_file
get_fileset_id
get_fileset_info
get_flash_file_list
get_flash_file_url
get_forward_msg
get_friend_list
get_friend_msg_history
get_friends_with_category
get_group_admin_settings
get_group_album_list
get_group_album_media_list
get_group_at_all_remain
get_group_detail_info
get_group_file_system_info
get_group_file_url
get_group_files_by_folder
get_group_honor_info
get_group_ignore_add_request
get_group_ignored_notifies
get_group_info
get_group_info_ex
get_group_list
get_group_member_info
get_group_member_list
get_group_msg_history
get_group_root_files
get_group_shut_list
get_group_signed_list
get_group_system_msg
get_group_todo_list
get_image
get_login_info
get_mini_app_ark
get_msg
get_msg_emoji_likes
get_online_clients
get_private_file_url
get_profile_like
get_ptt_text
get_qun_album_list
get_qzone_feeds
get_qzone_msg_list
get_recent_contact
get_record
get_record_text
get_rkey
get_rkey_server
get_share_link
get_status
get_stranger_info
get_unidirectional_friend_list
get_version_info
kick_me
mark_group_msg_as_read
mark_msg_as_read
mark_private_msg_as_read
move_custom_face_to_front
move_group_file
rename_flash_file
rename_group_file
rename_group_file_folder
send_ark_share
send_custom_face
send_flash_msg
send_forward_msg
send_group_ai_record
send_group_ark_share
send_group_forward_msg
send_group_msg
send_group_sign
send_like
send_msg
send_packet
send_poke
send_private_forward_msg
send_private_msg
send_qzone_msg
send_tuwen_ark
set_diy_online_status
set_doubt_friends_add_request
set_essence_msg
set_friend_add_request
set_friend_remark
set_friends_category
set_group_add_option
set_group_add_request
set_group_admin
set_group_album_media_like
set_group_anonymous
set_group_anonymous_ban
set_group_ban
set_group_card
set_group_kick
set_group_kick_members
set_group_leave
set_group_member_invite_policy
set_group_member_permissions
set_group_name
set_group_portrait
set_group_reaction
set_group_remark
set_group_robot_add_option
set_group_search
set_group_sign
set_group_special_title
set_group_todo
set_group_whole_ban
set_input_status
set_msg_emoji_like
set_online_status
set_qq_avatar
set_qq_profile
set_qzone_ban
set_qzone_msg_right
set_restart
set_self_longnick
upload_album
upload_file_stream
upload_forward_msg
upload_foward_msg
upload_group_file
upload_image_to_qun_album
upload_number
upload_private_file
upload_video_to_qun_album
```
