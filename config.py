# -*- coding: utf-8 -*-
"""设置与草稿的本地持久化（Windows: %APPDATA%\\H3PromptBuilder\\config.json）。"""

from __future__ import annotations

import json
import os
import traceback

import mac_compat

APP_DIR_NAME = "H3PromptBuilder"

DEFAULTS = {
    "api_base": "https://api.siliconflow.cn/v1",
    "api_key": "",
    "model": "Pro/deepseek-ai/DeepSeek-V3.2",
    "temperature": 0.4,
    "max_tokens": 4096,
    "timeout": 300,
    "extra_payload": "",
    "verify_ssl": True,
    "remember_key": True,
    "work": {},
}


def legacy_dir() -> str:
    """老版本写死 `%APPDATA%` 时用的目录（Windows 上它就等于新目录）。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def config_dir() -> str:
    """配置目录。

    Windows: %APPDATA%\\H3PromptBuilder —— 与旧版**完全一致**，老配置无缝续用。
    macOS:   ~/Library/Application Support/H3PromptBuilder
             （旧代码在 macOS 上会落到家目录根的 ~/H3PromptBuilder，很脏；
              但真有人在那儿存过配置就继续用旧目录，不迁移、不丢数据。）
    """
    new = mac_compat.app_support_dir(APP_DIR_NAME, create=False)
    old = legacy_dir()
    if (os.path.abspath(old) != os.path.abspath(new)
            and os.path.isdir(old) and not os.path.isdir(new)):
        d = old                      # 兼容旧路径（只在两个平台路径不同、且旧路径真存在时）
    else:
        d = new
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~")
    return d


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def load() -> dict:
    cfg = dict(DEFAULTS)
    p = config_path()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
    except Exception:
        # 配置文件坏了不该让程序起不来
        try:
            os.replace(p, p + ".bad")
        except Exception:
            pass
    if not cfg.get("remember_key"):
        cfg["api_key"] = ""
    cfg["work"] = cfg.get("work") or {}
    return cfg


def save(cfg: dict) -> str | None:
    """保存设置，返回 None 表示成功，否则返回错误说明。"""
    data = dict(cfg)
    if not data.get("remember_key"):
        data["api_key"] = ""
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return None
    except Exception as e:  # noqa: BLE001
        return "写入配置失败：%s\n%s" % (e, traceback.format_exc(limit=2))


def parse_extra_payload(text: str) -> dict | None:
    """把用户在设置里填的“额外请求参数(JSON)”解析出来；返回 (dict, error)。"""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise ValueError("额外请求参数不是合法 JSON：%s" % e) from None
    if not isinstance(obj, dict):
        raise ValueError("额外请求参数必须是 JSON 对象，例如 {\"top_p\": 0.9}")
    return obj
