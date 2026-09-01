# Agent 操作约束

- `config.json` 可能包含通知凭证和私有 topic；不得读取后回显、复制到对话、日志或 Git。
- 了解配置字段时只读 `config.example.json`。
- 修改配置后先运行 `price-bell validate --config config.json`，再运行 `price-bell check --config config.json`。
- 常驻实例由进程管理器托管时，不得从会话中重复启动第二个 `price-bell run`。
- 部署提供 `price-bellctl` 时，使用 `status/check/restart/logs/version` 管理，不直接操作后台 PID。
- 保留 `state.json` 与 `logs/`；升级代码不得清空提醒状态。
