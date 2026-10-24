# TeamSpeak 通道

把大肥鱼接进 TS 语音服务器的**频道文字聊天**。

## 为什么走 SSH Query

TS6 换了查询接口（实测你这台）：

| 接口 | 端口 | 能发消息 | 能收消息 |
|---|---|---|---|
| 经典 ServerQuery（TS3 时代） | 10011 | — | — | **TS6 已取消** |
| Web Query（HTTP） | 10080 | ✅ | ❌ 官方文档明确不支持 `servernotifyregister` |
| **SSH Query** | 10022 | ✅ | ✅ 支持事件订阅 |

所以收发都走 SSH Query（`asyncssh` 连接，账号 `serveradmin`）。

## 服务端要开的两个开关（只绑回环，不开公网端口）

```ini
# /etc/teamspeak-query.env（600，root:teamspeak6）
TSSERVER_QUERY_SSH_ENABLED=1
TSSERVER_QUERY_SSH_IP=127.0.0.1
TSSERVER_QUERY_SSH_PORT=10022
TSSERVER_QUERY_ADMIN_PASSWORD=<口令>
```

systemd 单元加一行 `EnvironmentFile=-/etc/teamspeak-query.env`，然后重启 `teamspeak.service`。

## 协议要点（踩过的坑都在注释里）

```
use port=9987                                   选中虚拟服务器
clientupdate client_nickname=大肥鱼              设昵称（被占用会 513）
servernotifyregister event=textchannel id=<cid> 订阅频道文字
notifytextmessage targetmode=2 msg=<escaped> invokerid=.. invokername=..
sendtextmessage targetmode=2 target=<cid> msg=<escaped>
```

- **行尾是 `\n\r`**：按 `\n` 切开后，下一行开头残留 `\r`，判断前要先 `strip("\r")`
- 值里的空格是 `\s`（`|` → `\p`，`/` → `\/`），所以按空格切分是安全的
- 自己发的消息会以 `notifytextmessage` 回显回来，要按 `invokerid` 过滤
- **读循环不能 await 回合**：回合要跑几秒模型，卡住就读不到发送命令的回执
- 重启会留下僵尸查询会话（`serveradmin2`、`大肥鱼（文字）`…），启动时要按昵称前缀清理

## 她的身份

她是**查询客户端**：消息会出现在频道文字里，但**不在频道成员列表**、不进语音、也不显示为说话状态。
昵称被占用时自动加后缀（`大肥鱼（文字）` / `大肥鱼·bot`）。

## 能力与形态

- 能力：`text`（TS 文字不支持图片/文件/表情）
- 形态：`room`（多人在场、没有 @ 机制；文字里叫名字算"被叫到"）
- 回执：`SendResult.confirmed = False`（TS 文字没有消息 id 可回读）
