# -*- coding: utf-8 -*-
"""通知通道离线测试。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from price_bell.notify import (MultiPusher, NotificationChannel, NtfyPusher,
                               PushResult)


class Response(object):
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakePusher(object):
    def __init__(self, name, ok=True):
        self.name = name
        self.ok = ok
        self.calls = 0

    def send(self, title, desp="", short=""):
        self.calls += 1
        return PushResult(self.ok, 0 if self.ok else 500,
                          "ok" if self.ok else "failed", self.name)


def main():
    captured = {}

    def opener(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return Response({"id": "message-id"})

    ntfy = NtfyPusher("random_topic", token="secret-token", opener=opener,
                      max_retries=0)
    result = ntfy.send("标题", "正文")
    assert result.ok
    assert captured["url"] == "https://ntfy.sh"
    assert captured["body"]["topic"] == "random_topic"
    assert captured["body"]["priority"] == 5
    assert captured["headers"]["Authorization"] == "Bearer secret-token"

    good = FakePusher("ntfy", True)
    bad = FakePusher("serverchan", False)
    multi = MultiPusher([
        NotificationChannel("serverchan", bad, daily_budget=5),
        NotificationChannel("ntfy", good, daily_budget=0),
    ])
    state = {"channels": {"serverchan": {"sent_today": 5}}}
    result = multi.send("t", "d", state=state)
    assert result.ok and result.delivered_channels == ["ntfy"]
    assert result.skipped_channels == ["serverchan"]
    assert bad.calls == 0 and good.calls == 1
    assert state["channels"]["ntfy"]["sent_today"] == 1

    capped = MultiPusher([NotificationChannel("serverchan", bad, daily_budget=1)])
    result = capped.send("t", "d", state={
        "channels": {"serverchan": {"sent_today": 1}}})
    assert not result.ok and not result.attempted

    partial = MultiPusher([
        NotificationChannel("serverchan", bad, 0),
        NotificationChannel("ntfy", good, 0),
    ])
    result = partial.send("t", "d", state={})
    assert result.ok and result.delivered_channels == ["ntfy"]

    only_ntfy = MultiPusher([
        NotificationChannel("serverchan", bad, 0),
        NotificationChannel("ntfy", good, 0),
    ])
    before_bad = bad.calls
    result = only_ntfy.send("t", "d", state={}, only=["ntfy"])
    assert result.ok and result.delivered_channels == ["ntfy"]
    assert bad.calls == before_bad

    print("通知通道测试全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
