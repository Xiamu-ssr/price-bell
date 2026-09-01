# -*- coding: utf-8 -*-
"""可组合通知通道：Server酱与 ntfy。

每个通道独立计数和限额；同一条提醒会发送到所有有额度的已启用通道。
只要至少一个通道成功，本轮就视为送达，避免成功通道因其他通道失败而重复收到。
"""
import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("price_bell.notify")


class PushResult(object):
    def __init__(self, ok, code=None, message="", channel="", attempted=True,
                 delivered_channels=None, skipped_channels=None):
        self.ok = ok
        self.code = code
        self.message = message
        self.channel = channel
        self.attempted = attempted
        self.delivered_channels = delivered_channels or []
        self.skipped_channels = skipped_channels or []

    def __repr__(self):
        return "PushResult(ok=%r, channel=%r, code=%r, message=%r)" % (
            self.ok, self.channel, self.code, self.message)


KNOWN_SERVERCHAN_ENDPOINTS = (
    "https://sct3api.ftqq.com/{key}.send",
    "https://sctapi.ftqq.com/{key}.send",
)


class ServerChanPusher(object):
    name = "serverchan"

    def __init__(self, sendkey, endpoint=KNOWN_SERVERCHAN_ENDPOINTS[0],
                 timeout=8, max_retries=3, opener=None):
        self.candidates = [endpoint] + [
            e for e in KNOWN_SERVERCHAN_ENDPOINTS if e != endpoint]
        self.sendkey = sendkey
        self.url = endpoint.format(key=sendkey)
        self.timeout = timeout
        self.max_retries = max_retries
        self.opener = opener or urllib.request.urlopen

    def _rotate_endpoint(self):
        if len(self.candidates) <= 1:
            return None
        self.candidates.pop(0)
        self.url = self.candidates[0].format(key=self.sendkey)
        return self.url

    def send(self, title, desp="", short=""):
        body = urllib.parse.urlencode(
            {"title": title, "desp": desp, "short": short}).encode("utf-8")
        delays = [0, 5, 15, 45]
        last_err = "unknown"
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(delays[min(attempt, len(delays) - 1)])
            try:
                req = urllib.request.Request(
                    self.url, data=body,
                    headers={"User-Agent": "price-bell/0.3"})
                with self.opener(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8", "replace"))
                code = payload.get("code")
                if code == 0:
                    return PushResult(True, 0, "SUCCESS", self.name)
                msg = payload.get("message") or payload.get("error") or str(payload)[:200]
                log.error("Server酱业务错误 code=%s: %s", code, msg)
                return PushResult(False, code, msg, self.name)
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    return PushResult(False, e.code, "HTTP %s" % e.code, self.name)
                last_err = "HTTPError: %s" % e.code
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                if "ertificate" in last_err:
                    alt = self._rotate_endpoint()
                    if alt:
                        log.warning("Server酱证书异常，切换备用端点")
            log.warning("Server酱第%d次网络失败: %s", attempt + 1, last_err)
        return PushResult(False, None, last_err, self.name)


class NtfyPusher(object):
    name = "ntfy"

    def __init__(self, topic, base_url="https://ntfy.sh", token="",
                 username="", password="", priority=5, tags=None,
                 timeout=8, max_retries=3, opener=None):
        self.topic = topic
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.username = username
        self.password = password
        self.priority = int(priority)
        self.tags = list(tags or ["chart_with_upwards_trend"])
        self.timeout = timeout
        self.max_retries = max_retries
        self.opener = opener or urllib.request.urlopen

    def _headers(self):
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "price-bell/0.3",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        elif self.username:
            raw = (self.username + ":" + self.password).encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        return headers

    def send(self, title, desp="", short=""):
        payload = {
            "topic": self.topic,
            "title": title,
            "message": desp or short,
            "priority": self.priority,
            "tags": self.tags,
            "markdown": True,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        delays = [0, 3, 10, 30]
        last_err = "unknown"
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(delays[min(attempt, len(delays) - 1)])
            try:
                req = urllib.request.Request(
                    self.base_url, data=body, headers=self._headers())
                with self.opener(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                if data.get("id"):
                    return PushResult(True, 0, "SUCCESS", self.name)
                return PushResult(False, None, "ntfy 响应缺少消息ID", self.name)
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    return PushResult(False, e.code, "HTTP %s" % e.code, self.name)
                last_err = "HTTPError: %s" % e.code
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
            log.warning("ntfy 第%d次网络失败: %s", attempt + 1, last_err)
        return PushResult(False, None, last_err, self.name)


class NotificationChannel(object):
    def __init__(self, name, pusher, daily_budget=0):
        self.name = name
        self.pusher = pusher
        self.daily_budget = max(0, int(daily_budget or 0))


class MultiPusher(object):
    """向所有有额度的通道发送；0 表示通道不限额。"""

    def __init__(self, channels):
        self.channels = list(channels)

    @property
    def names(self):
        return [c.name for c in self.channels]

    def send(self, title, desp="", short="", state=None, only=None):
        state = state if state is not None else {}
        channel_state = state.setdefault("channels", {})
        wanted = None if not only or only == "all" else {only}
        delivered, skipped, failures = [], [], []
        attempted = 0
        for channel in self.channels:
            if wanted is not None and channel.name not in wanted:
                continue
            st = channel_state.setdefault(channel.name, {"sent_today": 0})
            sent = int(st.get("sent_today", 0))
            if channel.daily_budget and sent >= channel.daily_budget:
                skipped.append(channel.name)
                continue
            attempted += 1
            result = channel.pusher.send(title, desp, short)
            if result.ok:
                st["sent_today"] = sent + 1
                delivered.append(channel.name)
            else:
                failures.append("%s: %s" % (channel.name, result.message))
        ok = bool(delivered)
        if ok:
            msg = "已送达: " + ", ".join(delivered)
            if failures:
                msg += "；失败: " + "; ".join(failures)
        elif not attempted and skipped:
            msg = "所有目标通道均已达到当日额度"
        else:
            msg = "; ".join(failures) or "没有可用通知通道"
        return PushResult(
            ok, 0 if ok else None, msg, "multi", attempted=bool(attempted),
            delivered_channels=delivered, skipped_channels=skipped)
