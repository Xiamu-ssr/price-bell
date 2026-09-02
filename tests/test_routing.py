# -*- coding: utf-8 -*-
"""级别与规则覆盖路由离线测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from price_bell.__main__ import (channels_for_event, channels_for_level,
                                 group_events_by_channels)


class E(object):
    def __init__(self, rule):
        self.rule = rule


def main():
    cfg = {
        "notification_routes": {
            "default": None,
            "info": ["ntfy"],
            "critical": ["serverchan", "ntfy"],
        }
    }
    assert channels_for_level(cfg, "info") == ["ntfy"]
    assert channels_for_level(cfg, "unknown") is None
    assert channels_for_level(cfg, "info", "serverchan") == ["serverchan"]

    default = E({"level": "default", "channels": None})
    info = E({"level": "info", "channels": None})
    override = E({"level": "info", "channels": ["serverchan"]})
    all_override = E({"level": "critical", "channels": "all"})
    assert channels_for_event(cfg, default) is None
    assert channels_for_event(cfg, info) == ["ntfy"]
    assert channels_for_event(cfg, override) == ["serverchan"]
    assert channels_for_event(cfg, all_override) is None

    groups = group_events_by_channels(cfg, [default, info, override, all_override])
    compact = [(channels, len(events)) for channels, events in groups]
    assert compact == [(None, 2), (["ntfy"], 1), (["serverchan"], 1)]
    print("路由测试全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
