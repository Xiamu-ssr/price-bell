# -*- coding: utf-8 -*-
"""配置解析离线测试。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from price_bell.config import ConfigError, load_config


def write_config(data):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def main():
    os.environ["TEST_NTFY_TOPIC"] = "topic_0123456789"
    data = {
        "notifications": {
            "ntfy": {"enabled": True, "topic_env": "TEST_NTFY_TOPIC"}
        },
        "bells": [{
            "code": "600000", "name": "测试",
            "rules": [{"op": ">=", "price": 10}],
        }],
    }
    path = write_config(data)
    try:
        cfg = load_config(path)
        assert cfg["bells"][0]["code"] == "sh600000"
        assert cfg["notifications"]["ntfy"]["topic"] == "topic_0123456789"
        assert cfg["enabled_channels"] == ["ntfy"]
    finally:
        os.unlink(path)

    bad = write_config({"bells": data["bells"]})
    try:
        try:
            load_config(bad)
            raise AssertionError("未启用通知通道时应失败")
        except ConfigError:
            pass
    finally:
        os.unlink(bad)

    print("配置测试全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
