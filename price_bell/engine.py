# -*- coding: utf-8 -*-
"""铃铛引擎核心语义 (v2, 解决"错过一条消息就永远沉默"问题):

1. 穿越即报: 价格首次进入触发区立即推送; 启动时已在区内也算首次穿越, 立即报。
2. 区内持续提醒: 条件持续成立, 每 remind_interval_minutes 重复提醒并标注次数。
3. 离场重新武装: 价格离开触发区自动复位, 再次穿越再次立即推送。
4. 每日推送预算: 穿越优先, 预算剩余 <= reserve 时提醒类自动降级; 额度耗尽记日志。
5. 合并推送: 同一周期的多条事件合并成 1 条消息, 各通知通道独立计数。
推送失败时不更新 last_notify, 下个周期按"补发"逻辑自动重试。
"""
import logging
import time

log = logging.getLogger("price_bell.engine")

NL = chr(10)


def rule_id(code, rule):
    return "%s%s%s" % (code, rule["op"], rule["price"])


class Event(object):
    __slots__ = ("kind", "code", "name", "rule", "quote", "remind_no")

    def __init__(self, kind, code, name, rule, quote, remind_no=0):
        self.kind = kind            # trigger=穿越/补发 | reminder=区内提醒 | exit=离场
        self.code = code
        self.name = name
        self.rule = rule
        self.quote = quote
        self.remind_no = remind_no


class BellEngine(object):
    def __init__(self, cfg, clock=time.time):
        self.cfg = cfg
        self.clock = clock
        self._last_budget_warn = 0.0
        self.rules = []
        for bell in cfg["bells"]:
            for r in bell["rules"]:
                self.rules.append({
                    "id": rule_id(bell["code"], r),
                    "code": bell["code"],
                    "name": bell["name"],
                    "op": r["op"],
                    "price": r["price"],
                    "action": r["action"],
                    "level": r.get("level", "default"),
                    "channels": r.get("channels"),
                    "remind_minutes": r["remind_interval_minutes"]
                        or cfg["remind_interval_minutes"],
                })

    @property
    def codes(self):
        return sorted({r["code"] for r in self.rules})

    @staticmethod
    def _hit(op, price, threshold):
        return price >= threshold if op == ">=" else price <= threshold

    def evaluate(self, quotes, state, now=None):
        """返回 (待推送事件列表, 离场事件列表)。in_zone 状态即时更新,
        last_notify 仅在推送成功后由 mark_sent 更新。"""
        now = self.clock() if now is None else now
        events, exits = [], []
        rstates = state["rules"]
        for r in self.rules:
            q = quotes.get(r["code"])
            if q is None:
                continue
            st = rstates.setdefault(r["id"], {
                "in_zone": False, "last_notify": 0, "count_today": 0,
                "exit_pending": False})
            hit = self._hit(r["op"], q.price, r["price"])
            if hit and not st["in_zone"]:
                st["exit_pending"] = False
                gap = self.cfg.get("min_trigger_gap_minutes", 15) * 60
                if st.get("last_notify") and now - st["last_notify"] < gap:
                    st["in_zone"] = True
                    log.info("防抖: %s 重新入区但距上次推送不足%d分钟, 转按提醒节奏处理",
                             r["name"], self.cfg.get("min_trigger_gap_minutes", 15))
                    continue
                st["in_zone"] = True
                events.append(Event("trigger", r["code"], r["name"], r, q))
                log.info("触发: %s %.2f %s %.2f -> %s",
                         r["name"], q.price, r["op"], r["price"], r["action"] or "(无动作)")
            elif hit and st["in_zone"]:
                interval = r["remind_minutes"] * 60
                if now - st["last_notify"] >= interval:
                    if not st["last_notify"]:
                        # 首次推送失败过 -> 以trigger优先级补发
                        events.append(Event("trigger", r["code"], r["name"], r, q))
                        log.info("补发(上次推送未成功): %s", r["name"])
                    else:
                        no = st.get("count_today", 0) + 1
                        events.append(Event("reminder", r["code"], r["name"], r, q, remind_no=no))
                        log.info("区内重复提醒: %s 第%d次", r["name"], no)
            elif not hit and st["in_zone"]:
                margin = r["price"] * self.cfg.get("rearm_margin_pct", 0.5) / 100.0
                if r["op"] == "<=":
                    really_out = q.price > r["price"] + margin
                else:
                    really_out = q.price < r["price"] - margin
                if really_out:
                    st["in_zone"] = False
                    if self.cfg.get("notify_on_exit_zone"):
                        st["exit_pending"] = True
                    exits.append(Event("exit", r["code"], r["name"], r, q))
                    log.info("离开触发区(滞回%.2f%%), 重新武装: %s 现价%.2f vs %s%.2f",
                             self.cfg.get("rearm_margin_pct", 0.5),
                             r["name"], q.price, r["op"], r["price"])
                # 滞回带内徘徊: 保持in_zone, 既不报也不重新武装
            elif not hit and not st["in_zone"] and st.get("exit_pending"):
                # 上次离场通知发送失败，继续补发；成功后由 mark_sent 清除。
                exits.append(Event("exit", r["code"], r["name"], r, q))
        return events, exits

    def select_within_budget(self, events, state):
        if not events:
            return []
        budget = self.cfg["daily_push_budget"]
        reserve = self.cfg["reminder_budget_reserve"]
        if budget <= 0:
            return list(events)
        remaining = max(0, budget - state["sent_today"])
        if remaining <= 0:
            if self.clock() - self._last_budget_warn > 600:
                log.warning("今日推送额度已用完(%d条), 后续事件仅记日志", budget)
                self._last_budget_warn = self.clock()
            return []
        triggers = [e for e in events if e.kind in ("trigger", "exit")]
        reminders = [e for e in events if e.kind == "reminder"]
        chosen = list(triggers)
        if remaining > reserve:
            chosen.extend(reminders)
        elif reminders:
            log.info("预算剩余%d<=预留%d, 跳过%d条重复提醒", remaining, reserve, len(reminders))
        return chosen

    def format_message(self, events, state):
        t = time.gmtime(self.clock() + self.cfg["timezone_offset_hours"] * 3600)
        now_s = "%02d:%02d" % (t.tm_hour, t.tm_min)
        if events and all(e.kind == "exit" for e in events):
            title = "股价铃 | %d条离场 %s" % (len(events), now_s)
        else:
            title = "股价铃 | %d条提醒 %s" % (len(events), now_s)
        blocks = []
        for e in events:
            arrow = "▲" if e.rule["op"] == ">=" else "▼"
            if e.kind == "exit":
                arrow = "↩"
                tag = "离开触发区，已重新武装"
            elif e.kind == "trigger":
                tag = "首次触发"
            else:
                tag = "第%d次提醒" % e.remind_no
            lines = [
                "**%s %s(%s)**" % (arrow, e.name, e.code[2:]),
                "现价 **%.2f** · 阈值 %s %.2f · %s" % (
                    e.quote.price, e.rule["op"], e.rule["price"], tag),
            ]
            if e.rule["action"] and e.kind != "exit":
                lines.append("→ %s" % e.rule["action"])
            blocks.append((NL * 2).join(lines))
        budget = self.cfg["daily_push_budget"]
        usage = "%d/%d" % (state["sent_today"] + 1, budget) if budget > 0 else "%d" % (
            state["sent_today"] + 1)
        footer = "---" + NL * 2 + "今日提醒 %s 条 · 腾讯秒级行情" % usage
        desp = (NL * 2).join(blocks) + NL * 2 + footer
        short = "; ".join("%s %.2f" % (e.name, e.quote.price) for e in events)
        return title, desp, short

    def mark_sent(self, events, state, now=None):
        now = self.clock() if now is None else now
        state["sent_today"] += 1
        rstates = state["rules"]
        for e in events:
            st = rstates[e.rule["id"]]
            if e.kind == "exit":
                st["exit_pending"] = False
                st["last_exit_notify"] = now
            else:
                st["last_notify"] = now
                st["count_today"] = st.get("count_today", 0) + 1

    def mark_deferred(self, events, state, now=None):
        """事件被预算拦截时调用: 只重置计时器防止每周期刷屏, 不计已发次数。"""
        now = self.clock() if now is None else now
        rstates = state["rules"]
        for e in events:
            st = rstates.get(e.rule["id"])
            if st is not None:
                if e.kind == "exit":
                    st["exit_pending"] = False
                else:
                    st["last_notify"] = now
