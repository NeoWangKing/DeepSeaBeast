# 控制台上线（dsb.neowangking.top）

控制台只绑 `127.0.0.1:8765`，由 nginx 反代。**不开新公网端口**。

## 1. DNS

在 Cloudflare 加一条 A 记录：

```
类型 A   名称 dsb   内容 117.72.111.241   代理：橙色小云（开启）或灰色（直连）都可以
```

## 2. 服务器上跑起来（已配置好，确认即可）

```bash
systemctl status deepsea-console        # 应该 active
cat /opt/deepsea/.secrets/console.env   # 里面有 DSB_CONSOLE_PASSWORD
```

口令在这一行：`DSB_CONSOLE_PASSWORD=...`（用户名固定 `admin`）

## 3. nginx 反代 + 证书

```bash
sudo tee /etc/nginx/sites-available/dsb >/dev/null <<'CONF'
server {
    listen 80;
    server_name dsb.neowangking.top;
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
CONF
sudo ln -sf /etc/nginx/sites-available/dsb /etc/nginx/sites-enabled/dsb
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d dsb.neowangking.top     # 自动改上面那段为 443
```

## 4. 两道锁（建议都开）

**① nginx basic auth**（控制台自己也有一道，这是第二道）：

```bash
sudo sh -c 'echo "admin:$(openssl passwd -apr1)" > /etc/nginx/.dsb_htpasswd'
# 按提示输入密码
```

在上面 server 块的 `location /` 里加：

```nginx
auth_basic "deepsea console";
auth_basic_user_file /etc/nginx/.dsb_htpasswd;
```

**② IP 白名单**（只允许你自己的出口 IP）：

```nginx
allow 你的公网IP;
deny all;
```

## 5. 验证

```bash
curl -I https://dsb.neowangking.top/          # 401（没带账号）= 正常
```

浏览器打开 `https://dsb.neowangking.top/`，输入 `admin` + 口令。

## 6. 之后怎么起停

```bash
sudo systemctl restart deepsea-console   # 控制台自己（不影响核心）
sudo systemctl status deepsea-console
```

改完配置页里的东西，**核心重启后才生效**：

```bash
sudo systemctl restart deepsea-beast
```

## 注意

- 控制台**只写** `core__custom__.toml`（每次写前自动备份，保留最近 10 份）
- 它**改不动**通道（`[adapters.*]`）和插件配置——那些在各自的页面里管
- 如果哪天忘了口令：`sudo cat /opt/deepsea/.secrets/console.env` 或删掉该文件重启服务会重新生成
