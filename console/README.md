# 大肥鱼控制台（核心层）

莱茵生命风格的本地管理面板。**只做核心层**：门控 / 回合 / 记忆 / 待办 / 发送 / 节奏。

## 分层边界（为什么单开一个进程）

```
核心（core/）      跑对话，不认识控制台
控制台（console/） 只读写配置文件，不 import 核心业务逻辑
```

- 读：`core__custom__.toml`、`data/` 里的计数、systemd 状态
- 写：**只写 `*__custom__*` 文件**，写前备份、写后校验、失败回滚
- 不碰：通道（`[adapters.*]`）、插件数据、`data/` 内容

## 运行

```bash
python -m console.server                 # 127.0.0.1:8765，自动找 core__custom__.toml
python -m console.server --port 8765 --config /opt/deepsea/core__custom__.toml
```

**只绑回环**，不开放公网端口。外部访问走 nginx 反代 + basic auth（见 `docs/CONSOLE_DEPLOY.md`）。

## 安全

| 项 | 做法 |
|---|---|
| 绑定 | 默认 `127.0.0.1`；绑非回环必须设 `--user/--password`，否则拒绝启动 |
| 认证 | HTTP Basic（`DSB_CONSOLE_USER` / `DSB_CONSOLE_PASSWORD`，建议放 `.secrets/console.env`） |
| 写入 | 只允许 `*__custom__*`；每段键有类型/范围校验；未知键、非法枚举一律拒绝 |
| 备份 | 每次写入 `core__custom__.toml.bak-时间戳`，只留最近 10 份 |
| 注释 | 行级替换，用户写的注释与对齐都保住 |

## 页面

```
CORE / 核心
  OVERVIEW 总览    服务状态、贴图/记忆/待办计数、配置路径
  GATE 门控        档位、发言额度、打分阈值、连续对话、按会话覆盖（只读表）
  TURN 回合        轮数、token、墙钟、发送条数
  MEMORY 记忆      上下文预算
  TODOS 待办       上限、保留、提醒频率
  SEND 发送        重试次数与退避
  PACING 节奏      打字延迟
```

通道层与插件层按同一条原则后续接：**谁的数据谁管，不跨层显示**。
