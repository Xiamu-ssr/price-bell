# -*- coding: utf-8 -*-
"""离线冒烟测试：用假行情与假时钟验证状态机、预算和合并推送。"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.CRITICAL)

from price_bell.engine import BellEngine  # noqa: E402

CFG = {
    "remind_interval_minutes": 30,
    "rearm_margin_pct": 0.5,
    "min_trigger_gap_minutes": 15,
    "daily_push_budget": 3,
    "reminder_budget_reserve": 1,
    "timezone_offset_hours": 8,
    "bells": [
        {"code": "sh600000", "name": "标的一", "rules": [
            {"op": "<=", "price": 10.0, "action": "复核计划", "remind_interval_minutes": None},
            {"op": "<=", "price": 8.0, "action": "检查风险", "remind_interval_minutes": None},
        ]},
        {"code": "sz000001", "name": "标的二", "rules": [
            {"op": ">=", "price": 20.0, "action": "检查收益", "remind_interval_minutes": None},
        ]},
    ],
}


class Q(object):
    def __init__(self, price):
        self.price = price


class FakeClock(object):
    def __init__(self):
        self.t = 1788220000.0

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


FAILED = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        FAILED.append(name)


def main():
    clock = FakeClock()
    eng = BellEngine(CFG, clock=clock)
    state = {"date": "20260901", "sent_today": 0, "rules": {}}

    ev, ex = eng.evaluate({"sh600000": Q(10.5), "sz000001": Q(19.0)}, state)
    check("区外无事件", not ev and not ex)

    ev, _ = eng.evaluate({"sh600000": Q(9.9), "sz000001": Q(19.0)}, state)
    check("穿越即报", len(ev) == 1 and ev[0].kind == "trigger")
    eng.mark_sent(ev, state)

    clock.advance(10 * 60)
    ev, _ = eng.evaluate({"sh600000": Q(9.8), "sz000001": Q(19.0)}, state)
    check("区内未到间隔不骚扰", not ev)

    clock.advance(21 * 60)
    ev, _ = eng.evaluate({"sh600000": Q(9.8), "sz000001": Q(19.0)}, state)
    check("区内持续提醒", len(ev) == 1 and ev[0].kind == "reminder" and ev[0].remind_no == 2)
    eng.mark_sent(ev, state)

    ev, ex = eng.evaluate({"sh600000": Q(10.1), "sz000001": Q(19.0)}, state)
    check("离场重新武装", not ev and len(ex) == 1)

    clock.advance(16 * 60)
    ev, _ = eng.evaluate({"sh600000": Q(9.7), "sz000001": Q(19.0)}, state)
    check("再穿越再触发", len(ev) == 1 and ev[0].kind == "trigger")
    eng.mark_sent(ev, state)

    clock.advance(31 * 60)
    ev, _ = eng.evaluate({"sh600000": Q(9.6), "sz000001": Q(19.0)}, state)
    check("预算耗尽拦截", bool(ev) and not eng.select_within_budget(ev, state))

    state2 = {"date": "20260901", "sent_today": 0, "rules": {}}
    ev, _ = eng.evaluate({"sh600000": Q(9.9), "sz000001": Q(20.1)}, state2)
    check("同周期双触发", len(ev) == 2)
    title, desp, _ = eng.format_message(ev, state2)
    check("合并推送省额度", "2条提醒" in title and "标的一" in desp and "标的二" in desp)

    state3 = {"date": "20260901", "sent_today": 2, "rules": {}}
    ev, _ = eng.evaluate({"sh600000": Q(9.9)}, state3)
    chosen = eng.select_within_budget(ev, state3)
    check("预算紧张穿越优先", len(chosen) == 1 and chosen[0].kind == "trigger")
    eng.mark_sent(chosen, state3)
    clock.advance(31 * 60)
    ev, _ = eng.evaluate({"sh600000": Q(9.8)}, state3)
    check("预算紧张提醒降级", bool(ev) and not eng.select_within_budget(ev, state3))

    state4 = {"date": "20260901", "sent_today": 0, "rules": {}}
    eng.evaluate({"sh600000": Q(9.9)}, state4)
    clock.advance(31 * 60)
    ev, _ = eng.evaluate({"sh600000": Q(9.8)}, state4)
    check("推送失败自动补发(trigger优先级)", len(ev) == 1 and ev[0].kind == "trigger")

    state5 = {"date": "20260901", "sent_today": 0, "rules": {}}
    ev, _ = eng.evaluate({"sh600000": Q(9.9)}, state5)
    eng.mark_sent(ev, state5)
    clock.advance(60)
    ev, ex = eng.evaluate({"sh600000": Q(10.02)}, state5)
    check("滞回带内不重新武装", not ev and not ex)
    ev, ex = eng.evaluate({"sh600000": Q(10.1)}, state5)
    check("越过滞回带才重新武装", not ev and len(ex) == 1)

    clock.advance(5 * 60)
    ev, _ = eng.evaluate({"sh600000": Q(9.9)}, state5)
    check("触发防抖(15分钟内不报)", not ev)

    clock.advance(26 * 60)
    ev, _ = eng.evaluate({"sh600000": Q(9.8)}, state5)
    state5["sent_today"] = 3
    chosen = eng.select_within_budget(ev, state5)
    eng.mark_deferred([e for e in ev if e not in chosen], state5)
    clock.advance(5)
    ev2, _ = eng.evaluate({"sh600000": Q(9.8)}, state5)
    check("被拦截提醒复位不刷屏", bool(ev) and not chosen and not ev2)

    print()
    if FAILED:
        print("失败 %d 项: %s" % (len(FAILED), FAILED))
        return 1
    print("全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
