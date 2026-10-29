# -*- coding: utf-8 -*-
"""截图后：删掉示例配置，把真实配置还原回去。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfgstore

cfg_path = cfgstore.config_path()
backup = cfg_path + ".shot-backup"

if os.path.exists(cfg_path):
    os.remove(cfg_path)
    print("已删掉示例配置")
if os.path.exists(backup):
    os.replace(backup, cfg_path)
    print("真实配置已还原：%s" % cfg_path)
else:
    print("警告：没找到 %s，请手动检查！" % backup)

with open(cfg_path, "r", encoding="utf-8") as f:
    import json
    c = json.load(f)
print("校验：api_base=%s | key len=%d | prefix=%s | pics=%d"
      % (c.get("api_base"), len(c.get("api_key") or ""), (c.get("api_key") or "")[:5],
         len((c.get("work") or {}).get("pictures") or [])))
