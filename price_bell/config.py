# -*- coding: utf-8 -*-
"""配置加载与校验。相对路径一律相对配置文件所在目录解析。"""
import copy
import json
import os
import re

DEFAULTS = {
    "poll_interval": 3,
    "quote_source": {
        "provider": "tencent",
        "endpoint": "https://qt.gtimg.cn/q={codes}",
        "timeout": 5,
    },
    "market_hours": ["09:15-11:35", "12:55-15:10"],
    "timezone_offset_hours": 8,
    "remind_interval_minutes": 30,
    "rearm_margin_pct": 0.5,
    "min_trigger_gap_minutes": 15,
    "daily_push_budget": 0,
    "reminder_budget_reserve": 1,
    "notify_on_exit_zone": False,
    "state_file": "state.json",
    "log_file": "logs/price_bell.log",
    "log_level": "INFO",
    "notifications": {
        "serverchan": {
            "enabled": False,
            "sendkey": "",
            "sendkey_env": "SERVERCHAN_SENDKEY",
            "endpoint": "https://sct3api.ftqq.com/{key}.send",
            "daily_budget": 5,
        },
        "ntfy": {
            "enabled": False,
            "base_url": "https://ntfy.sh",
            "topic": "",
            "topic_env": "NTFY_TOPIC",
            "token": "",
            "token_env": "NTFY_TOKEN",
            "username": "",
            "username_env": "NTFY_USERNAME",
            "password": "",
            "password_env": "NTFY_PASSWORD",
            "priority": 5,
            "tags": ["chart_with_upwards_trend"],
            "daily_budget": 0,
        },
    },
    "bells": [],
}

VALID_OPS = (">=", "<=")


class ConfigError(Exception):
    pass


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _norm_code(code):
    c = str(code).strip().lower()
    if len(c) == 8 and c[:2] in ("sh", "sz") and c[2:].isdigit():
        return c
    if len(c) == 6 and c.isdigit():
        return ("sh" if c.startswith("6") else "sz") + c
    raise ConfigError("无法识别的证券代码: %r (示例: sz000001 / sh600000)" % (code,))


def _resolve_value(section, key):
    env_name = str(section.get(key + "_env") or "").strip()
    if env_name:
        value = os.environ.get(env_name)
        if value:
            return value
    return str(section.get(key) or "").strip()


def load_config(path):
    if not os.path.exists(path):
        raise ConfigError("配置文件不存在: %s (可运行 python3 -m price_bell init 生成示例)" % path)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    cfg = _merge(DEFAULTS, raw)
    base_dir = os.path.dirname(os.path.abspath(path))
    for key in ("state_file", "log_file"):
        if not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(base_dir, cfg[key])
    if int(cfg["poll_interval"]) < 1:
        raise ConfigError("poll_interval 必须 >= 1")
    if int(cfg["daily_push_budget"]) < 0:
        raise ConfigError("daily_push_budget 不能为负数；0 表示不限额")
    source = cfg["quote_source"]
    if source.get("provider") != "tencent":
        raise ConfigError("当前仅支持 quote_source.provider=tencent")
    if "{codes}" not in str(source.get("endpoint") or ""):
        raise ConfigError("quote_source.endpoint 必须包含 {codes}")

    enabled_channels = []
    serverchan = cfg["notifications"]["serverchan"]
    if serverchan.get("enabled"):
        serverchan["sendkey"] = _resolve_value(serverchan, "sendkey")
        if not serverchan["sendkey"]:
            raise ConfigError("Server酱已启用，但未配置 sendkey/sendkey_env")
        enabled_channels.append("serverchan")
    ntfy = cfg["notifications"]["ntfy"]
    if ntfy.get("enabled"):
        for key in ("topic", "token", "username", "password"):
            ntfy[key] = _resolve_value(ntfy, key)
        if not re.match(r"^[-_A-Za-z0-9]{1,64}$", ntfy["topic"]):
            raise ConfigError("ntfy.topic 必须为 1-64 位字母、数字、- 或 _")
        if not str(ntfy.get("base_url") or "").startswith(("https://", "http://")):
            raise ConfigError("ntfy.base_url 必须是 HTTP(S) 地址")
        priority = int(ntfy.get("priority", 5))
        if priority < 1 or priority > 5:
            raise ConfigError("ntfy.priority 必须在 1-5 之间")
        ntfy["priority"] = priority
        enabled_channels.append("ntfy")
    if not enabled_channels:
        raise ConfigError("至少启用一个 notifications 通道：serverchan 或 ntfy")
    cfg["enabled_channels"] = enabled_channels
    bells = []
    for i, b in enumerate(cfg["bells"]):
        code = _norm_code(b.get("code", ""))
        name = b.get("name") or code
        rules = b.get("rules") or []
        if not rules:
            raise ConfigError("第%d个bell(%s)没有任何rules" % (i + 1, code))
        norm_rules = []
        for r in rules:
            op = r.get("op")
            if op not in VALID_OPS:
                raise ConfigError("%s 存在非法op: %r (仅支持 >= / <=)" % (code, op))
            try:
                price = float(r.get("price"))
            except (TypeError, ValueError):
                raise ConfigError("%s 存在非法price: %r" % (code, r.get("price")))
            norm_rules.append({
                "op": op,
                "price": price,
                "action": r.get("action") or "",
                "remind_interval_minutes": r.get("remind_interval_minutes"),
            })
        bells.append({"code": code, "name": name, "rules": norm_rules})
    cfg["bells"] = bells
    return cfg
