# price-bell

一个零依赖的 A 股价格提醒 CLI：腾讯实时行情，Server酱与 ntfy 多通道通知。

## 特性

- 启动时已进入触发区也会提醒
- 区内定时复报，离场后重新武装
- 滞回与防抖，避免阈值附近反复横跳
- 同周期事件合并，通道预算彼此独立
- 配置、状态、日志分离；适合 systemd、容器和 Agent Harness

## 使用

```bash
cp config.example.json config.json
# 编辑 config.json：启用通知通道，设置 bells

# ntfy 推荐用随机 topic，并通过环境变量传入
export NTFY_TOPIC='replace-with-a-long-random-topic'

python3 -m price_bell validate
python3 -m price_bell check
python3 -m price_bell test-push --channel ntfy
python3 -m price_bell run
```

也可以安装为系统命令：

```bash
python3 -m pip install .
price-bell --help
```

## 通知通道

`notifications.serverchan` 与 `notifications.ntfy` 可以同时启用，也可以分别关闭。每个通道有自己的 `daily_budget`，`0` 表示不限额。敏感值建议通过 `*_env` 指定的环境变量提供。

Google Play 版 ntfy 连接 `ntfy.sh` 时可走 FCM。topic 本质上相当于密码，请使用足够长的随机值；敏感场景建议使用 ntfy 账户权限或自建服务。

## DSH / Agent Harness

仓库内的 `AGENTS.md` 约束 Agent 不回显通知凭证。Agent 修改配置后应依次运行：

```bash
price-bell validate
price-bell check
price-bellctl restart   # 仅部署了配套 systemd 控制器时
```

本项目只提供提醒，不构成投资建议。
