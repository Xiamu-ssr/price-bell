# -*- coding: utf-8 -*-
"""状态持久化: tmp+replace原子写入; 跨日自动重置(已发计数清零, 各规则重新武装)。"""
import json
import logging
import os

log = logging.getLogger("price_bell.state")


class StateStore(object):
    def __init__(self, path):
        self.path = path

    def load(self, today):
        st = None
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    st = json.load(f)
            except (ValueError, IOError) as e:
                log.warning("状态文件损坏, 重建: %s", e)
        if not isinstance(st, dict):
            st = {}
        if st.get("date") != today:
            if st:
                log.info("跨日重置: %s -> %s (各规则重新武装)", st.get("date"), today)
            st = {"date": today, "sent_today": 0, "rules": {}, "channels": {}}
        st.setdefault("sent_today", 0)
        st.setdefault("rules", {})
        st.setdefault("channels", {})
        return st

    def save(self, st):
        d = os.path.dirname(os.path.abspath(self.path))
        if not os.path.isdir(d):
            os.makedirs(d)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
