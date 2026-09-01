# -*- coding: utf-8 -*-
"""腾讯免费行情源 qt.gtimg.cn: 秒级延迟, 免key, GBK编码。
字段(88列, ~分隔): [1]名称 [3]现价 [4]昨收 [30]时间戳yyyyMMddHHMMSS [33]最高 [34]最低 [38]换手率"""
import logging
import urllib.request

log = logging.getLogger("price_bell.quotes")

QUOTE_URL = "https://qt.gtimg.cn/q={codes}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) price-bell/0.1"


class Quote(object):
    __slots__ = ("code", "name", "price", "prev_close", "high", "low", "dt", "turnover")

    @property
    def date(self):
        return self.dt[:8] if self.dt else ""

    @property
    def change_pct(self):
        if self.prev_close:
            return (self.price / self.prev_close - 1.0) * 100.0
        return 0.0


def parse_payload(code, payload):
    parts = payload.split("~")
    if len(parts) < 39:
        return None
    try:
        q = Quote()
        q.code = code
        q.name = parts[1]
        q.price = float(parts[3])
        q.prev_close = float(parts[4])
        q.dt = parts[30]
        q.high = float(parts[33])
        q.low = float(parts[34])
        q.turnover = parts[38]
    except (ValueError, IndexError):
        return None
    if q.price <= 0:
        return None
    return q


class TencentQuoteSource(object):
    def __init__(self, endpoint=QUOTE_URL, timeout=5):
        self.endpoint = endpoint
        self.timeout = timeout

    def fetch(self, codes):
        """codes: ['sz000001', ...] -> {code: Quote}; 自动去重, 单次批量请求。"""
        uniq = sorted(set(codes))
        url = self.endpoint.format(codes=",".join(uniq))
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
        text = raw.decode("gbk", errors="replace")
        out = {}
        for seg in text.split(";"):
            seg = seg.strip()
            if not seg.startswith("v_"):
                continue
            try:
                var, payload = seg.split("=", 1)
                code = var[2:]
                payload = payload.strip().strip('"')
            except ValueError:
                continue
            q = parse_payload(code, payload)
            if q is not None:
                out[code] = q
        log.debug("请求%d只 -> 解析%d只 (%d字节)", len(uniq), len(out), len(raw))
        return out
