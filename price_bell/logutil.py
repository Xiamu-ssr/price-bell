# -*- coding: utf-8 -*-
"""日志: 控制台 + RotatingFileHandler(2MB x 3)。"""
import logging
import logging.handlers
import os
import sys


def setup_logging(log_file=None, level="INFO"):
    logger = logging.getLogger("price_bell")
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    if log_file:
        d = os.path.dirname(os.path.abspath(log_file))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
