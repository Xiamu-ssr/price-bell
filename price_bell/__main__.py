# -*- coding: utf-8 -*-
"""命令行入口:
  python3 -m price_bell init                  生成示例配置 config.json
  python3 -m price_bell test-push             发一条测试推送, 验证已启用通道
  python3 -m price_bell validate              安全校验配置, 不显示凭证
  python3 -m price_bell check [--push]        单次体检: 现价/距触发距离/将推送内容
  python3 -m price_bell run                   常驻运行
"""
import argparse
import json
import logging
import os
import shutil
import sys
import time

from . import __version__
from .config import load_config, ConfigError
from .engine import BellEngine
from .logutil import setup_logging
from .notify import (MultiPusher, NotificationChannel, NtfyPusher,
                     ServerChanPusher)
from .quotes import TencentQuoteSource
from .state import StateStore

log = logging.getLogger("price_bell.main")


def _shifted(cfg):
    return time.gmtime(time.time() + cfg["timezone_offset_hours"] * 3600)


def today_str(cfg):
    t = _shifted(cfg)
    return "%04d%02d%02d" % (t.tm_year, t.tm_mon, t.tm_mday)


def in_market_hours(cfg):
    t = _shifted(cfg)
    if t.tm_wday >= 5:
        return False
    hm = "%02d:%02d" % (t.tm_hour, t.tm_min)
    for win in cfg["market_hours"]:
        a, b = win.split("-")
        if a <= hm <= b:
            return True
    return False


def build(cfg, verbose=False):
    setup_logging(cfg["log_file"], "DEBUG" if verbose else cfg["log_level"])
    channels = []
    serverchan = cfg["notifications"]["serverchan"]
    if serverchan.get("enabled"):
        channels.append(NotificationChannel(
            "serverchan",
            ServerChanPusher(serverchan["sendkey"], serverchan["endpoint"]),
            serverchan.get("daily_budget", 0)))
    ntfy = cfg["notifications"]["ntfy"]
    if ntfy.get("enabled"):
        channels.append(NotificationChannel(
            "ntfy",
            NtfyPusher(
                topic=ntfy["topic"], base_url=ntfy["base_url"],
                token=ntfy.get("token", ""), username=ntfy.get("username", ""),
                password=ntfy.get("password", ""), priority=ntfy["priority"],
                tags=ntfy.get("tags")),
            ntfy.get("daily_budget", 0)))
    pusher = MultiPusher(channels)
    source_cfg = cfg["quote_source"]
    source = TencentQuoteSource(
        endpoint=source_cfg["endpoint"], timeout=int(source_cfg.get("timeout", 5)))
    engine = BellEngine(cfg)
    store = StateStore(cfg["state_file"])
    return engine, pusher, source, store


def channels_for_level(cfg, level="default", explicit=None):
    if explicit and explicit != "all":
        return [explicit]
    if explicit == "all":
        return None
    routes = cfg.get("notification_routes") or {}
    return routes.get(level, routes.get("default"))


def channels_for_event(cfg, event):
    explicit = event.rule.get("channels")
    if explicit == "all":
        return None
    if explicit is not None:
        return explicit
    return channels_for_level(cfg, event.rule.get("level", "default"))


def group_events_by_channels(cfg, events):
    groups = []
    indexes = {}
    for event in events:
        channels = channels_for_event(cfg, event)
        key = tuple(channels or ())
        if key not in indexes:
            indexes[key] = len(groups)
            groups.append((channels, []))
        groups[indexes[key]][1].append(event)
    return groups


def push_events(cfg, engine, pusher, events, state):
    chosen = engine.select_within_budget(events, state)
    dropped = [e for e in events if e not in chosen]
    if dropped:
        engine.mark_deferred(dropped, state)
    if not chosen:
        return False
    any_ok = False
    for channels, grouped in group_events_by_channels(cfg, chosen):
        budget = cfg["daily_push_budget"]
        if budget > 0 and state["sent_today"] >= budget:
            engine.mark_deferred(grouped, state)
            log.warning("全局通知额度已用完，本轮%d条路由事件只记状态", len(grouped))
            continue
        title, desp, short = engine.format_message(grouped, state)
        res = pusher.send(title, desp, short, state=state, only=channels)
        if res.ok:
            engine.mark_sent(grouped, state)
            any_ok = True
            log.info("已推送 %d 条事件(合并为1条消息) | 通道=%s | 今日提醒=%d",
                     len(grouped), ",".join(res.delivered_channels), state["sent_today"])
        elif not res.attempted:
            engine.mark_deferred(grouped, state)
            log.warning("通知通道无剩余额度，本轮事件只记状态: %s", res.message)
        else:
            log.error("推送失败(下周期自动补发): %s", res.message)
    return any_ok


def cmd_run(args):
    cfg = load_config(args.config)
    engine, pusher, source, store = build(cfg, args.verbose)
    state = store.load(today_str(cfg))
    interval = max(1, int(cfg["poll_interval"]))
    backoff = interval
    budget_label = str(cfg["daily_push_budget"]) if cfg["daily_push_budget"] > 0 else "不限"
    log.info("price-bell v%s 启动 | 监控 %d 只 / %d 条规则 | 轮询 %ds | 提醒间隔 %s 分钟 | 全局日预算 %s | 通道 %s",
             __version__, len(engine.codes), len(engine.rules), interval,
             cfg["remind_interval_minutes"], budget_label, ",".join(pusher.names))
    last_idle_log = 0.0
    while True:
        try:
            if not in_market_hours(cfg):
                if time.time() - last_idle_log > 3600:
                    log.info("非交易时段, 休眠中 (窗口: %s, 节假日由行情日期校验兜底)",
                             ", ".join(cfg["market_hours"]))
                    last_idle_log = time.time()
                time.sleep(60)
                continue
            day = today_str(cfg)
            if state["date"] != day:
                store.save(state)
                state = store.load(day)
            try:
                quotes = source.fetch(engine.codes)
                backoff = interval
            except Exception as e:
                backoff = min(60, backoff * 2)
                log.error("行情获取失败: %s: %s | %ds 后重试", type(e).__name__, e, backoff)
                time.sleep(backoff)
                continue
            fresh = {c: q for c, q in quotes.items() if q.date == day}
            stale = [c for c, q in quotes.items() if q.date != day]
            if stale:
                log.debug("非当日行情(休市/节假日/停牌): %s", ",".join(stale))
            if fresh:
                events, exits = engine.evaluate(fresh, state)
                pending = events + (exits if cfg["notify_on_exit_zone"] else [])
                if pending:
                    push_events(cfg, engine, pusher, pending, state)
            store.save(state)
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("收到中断信号, 保存状态后退出")
            store.save(state)
            return 0
        except Exception:
            log.exception("主循环未捕获异常, 10s 后继续")
            time.sleep(10)


def cmd_check(args):
    cfg = load_config(args.config)
    engine, pusher, source, store = build(cfg, args.verbose)
    state = store.load(today_str(cfg))
    quotes = source.fetch(engine.codes)
    if not quotes:
        print("行情获取失败或为空")
        return 1
    print("%-10s %-8s %8s %8s   %s" % ("代码", "名称", "现价", "涨跌%", "规则状态"))
    for bell in cfg["bells"]:
        q = quotes.get(bell["code"])
        if q is None:
            print("%-10s %-8s   [无行情]" % (bell["code"], bell["name"]))
            continue
        descs = []
        for r in engine.rules:
            if r["code"] != bell["code"]:
                continue
            hit = (q.price >= r["price"]) if r["op"] == ">=" else (q.price <= r["price"])
            if hit:
                descs.append("[在 %s %.2f 触发区内]" % (r["op"], r["price"]))
            else:
                dist = abs(q.price - r["price"]) / r["price"] * 100
                descs.append("距 %s %.2f 还差 %.1f%%" % (r["op"], r["price"], dist))
        print("%-10s %-8s %8.2f %+7.2f%%   %s" % (
            bell["code"], q.name, q.price, q.change_pct, " ".join(descs)))
    print()
    events, exits = engine.evaluate(quotes, state)
    pending = events + (exits if cfg["notify_on_exit_zone"] else [])
    if not pending:
        print("当前无触发事件。")
        return 0
    chosen = engine.select_within_budget(pending, state)
    if not chosen:
        print("有 %d 条事件但被预算拦截。" % len(events))
        return 0
    title, desp, short = engine.format_message(chosen, state)
    print("将推送(合并后1条): %s" % title)
    print(desp)
    if args.push:
        ok = push_events(cfg, engine, pusher, pending, state)
        store.save(state)
        print("实际推送: %s" % ("成功" if ok else "失败"))
    return 0


def _bell_rows(cfg, quotes):
    rows = []
    for bell in cfg["bells"]:
        q = quotes.get(bell["code"])
        row = {"code": bell["code"], "name": bell["name"], "quote": None, "rules": []}
        if q is not None:
            row["name"] = q.name or bell["name"]
            row["quote"] = {
                "price": q.price, "change_pct": q.change_pct,
                "date": q.date, "datetime": q.dt,
            }
            for rule in bell["rules"]:
                hit = BellEngine._hit(rule["op"], q.price, rule["price"])
                distance = abs(q.price - rule["price"]) / rule["price"] * 100.0
                row["rules"].append({
                    "op": rule["op"], "price": rule["price"], "hit": hit,
                    "distance_pct": distance, "level": rule.get("level", "default"),
                })
        rows.append(row)
    return rows


def cmd_bells(args):
    cfg = load_config(args.config)
    _, _, source, _ = build(cfg, args.verbose)
    quotes = source.fetch([b["code"] for b in cfg["bells"]])
    rows = _bell_rows(cfg, quotes)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
        return 0
    for row in rows:
        q = row["quote"]
        if q is None:
            print("%s %-8s [无行情]" % (row["code"][2:], row["name"]))
            continue
        bits = []
        for rule in row["rules"]:
            if rule["hit"]:
                bits.append("%s%.2f 已触发" % (rule["op"], rule["price"]))
            else:
                bits.append("%s%.2f 距%.1f%%" % (
                    rule["op"], rule["price"], rule["distance_pct"]))
        print("%s %-8s %.2f (%+.2f%%) | %s" % (
            row["code"][2:], row["name"], q["price"], q["change_pct"],
            "；".join(bits)))
    return 0


def _clean_text(value, label, limit, single_line=False):
    value = str(value or "")
    value = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32).strip()
    if single_line:
        value = " ".join(value.split())
    if not value:
        raise ConfigError("%s 不能为空" % label)
    if len(value) > limit:
        raise ConfigError("%s 不能超过%d字符" % (label, limit))
    return value


def _read_message(args):
    if args.message is not None:
        return args.message
    if args.message_file == "-":
        return sys.stdin.read(8001)
    try:
        with open(args.message_file, "r", encoding="utf-8") as f:
            return f.read(8001)
    except (OSError, UnicodeError) as e:
        raise ConfigError("无法读取 message-file: %s" % e)


def cmd_notify(args):
    cfg = load_config(args.config)
    _, pusher, _, store = build(cfg, args.verbose)
    state = store.load(today_str(cfg))
    budget = cfg["daily_push_budget"]
    if budget > 0 and state["sent_today"] >= budget:
        print("推送被全局日预算拦截: %d/%d" % (state["sent_today"], budget))
        return 3
    title = _clean_text(args.title, "title", 120, single_line=True)
    message = _clean_text(_read_message(args), "message", 8000)
    short = args.short or " ".join(message.split())[:160]
    short = _clean_text(short, "short", 256, single_line=True)
    channels = channels_for_level(cfg, args.level, args.channel)
    res = pusher.send(title, message, short, state=state, only=channels)
    if res.ok:
        state["sent_today"] += 1
        store.save(state)
        print("推送成功: %s" % ", ".join(res.delivered_channels))
        return 0
    store.save(state)
    print("推送失败: %s" % res.message)
    return 1


def cmd_test_push(args):
    cfg = load_config(args.config)
    _, pusher, _, _ = build(cfg, args.verbose)
    t = _shifted(cfg)
    now_s = "%04d-%02d-%02d %02d:%02d:%02d" % (t.tm_year, t.tm_mon, t.tm_mday,
                                               t.tm_hour, t.tm_min, t.tm_sec)
    desp = chr(10).join([
        "如果你看到这条消息，说明 price-bell 通知通道配置成功 ✔",
        "",
        "- 时间: %s" % now_s,
        "- 版本: v%s" % __version__,
        "- 监控标的: %d 只" % len(cfg["bells"]),
        "- 已启用通道: %s" % ", ".join(pusher.names),
    ])
    res = pusher.send("股价铃 | 通道测试", desp, "股价铃测试",
                      state={"channels": {}}, only=args.channel)
    print("推送结果: %r" % (res,))
    return 0 if res.ok else 1


def cmd_init(args):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(here, "config.example.json")
    dst = args.config
    if os.path.exists(dst):
        print("已存在: %s (不覆盖)" % dst)
        return 1
    shutil.copyfile(src, dst)
    print("已创建 %s —— 请启用至少一个通知通道并配置你的铃铛" % dst)
    return 0


def cmd_validate(args):
    cfg = load_config(args.config)
    rule_count = sum(len(b["rules"]) for b in cfg["bells"])
    print("配置有效: %d 只标的 / %d 条规则 / 通道: %s" % (
        len(cfg["bells"]), rule_count, ", ".join(cfg["enabled_channels"])))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="price_bell",
                                description="股价铃: 可配置的 A 股价格提醒 CLI")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd")

    def add(name, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--config", default="config.json")
        sp.add_argument("-v", "--verbose", action="store_true")
        return sp

    sp_check = add("check", help="单次体检(默认不推送)")
    sp_check.add_argument("--push", action="store_true", help="检查并真实推送")
    add("run", help="常驻运行")
    sp_test = add("test-push", help="发送测试推送")
    sp_test.add_argument("--channel", default="all",
                         choices=("all", "serverchan", "ntfy"),
                         help="测试指定通道，默认全部")
    sp_bells = add("bells", help="腾讯行情与铃铛距离的轻量快查")
    sp_bells.add_argument("--json", action="store_true", help="输出结构化 JSON")
    sp_notify = add("notify", help="通过已配置通道发送一条受限文本通知")
    sp_notify.add_argument("--title", required=True)
    msg = sp_notify.add_mutually_exclusive_group(required=True)
    msg.add_argument("--message")
    msg.add_argument("--message-file", help="UTF-8 文件；- 表示从标准输入读取")
    sp_notify.add_argument("--short", default="")
    sp_notify.add_argument("--level", default="default",
                           help="使用 notification_routes 中的级别")
    sp_notify.add_argument("--channel", default=None,
                           choices=("all", "serverchan", "ntfy"),
                           help="显式覆盖级别路由")
    add("validate", help="校验配置且不显示凭证")
    add("init", help="生成示例配置文件")
    args = p.parse_args(argv)
    try:
        if args.cmd == "run":
            return cmd_run(args)
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "test-push":
            return cmd_test_push(args)
        if args.cmd == "bells":
            return cmd_bells(args)
        if args.cmd == "notify":
            return cmd_notify(args)
        if args.cmd == "validate":
            return cmd_validate(args)
        if args.cmd == "init":
            return cmd_init(args)
        p.print_help()
        return 0
    except ConfigError as e:
        print("配置错误: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
