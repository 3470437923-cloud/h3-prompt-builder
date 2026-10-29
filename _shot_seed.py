# -*- coding: utf-8 -*-
"""截图前：把真实配置挪走，塞一份示例数据（这样截图里不会露出你的 API Key 和真实需求）。

跑完截图必须再执行 _shot_restore.py。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfgstore
import h3_skill
import prompt_spec

cfg_path = cfgstore.config_path()
backup = cfg_path + ".shot-backup"

if os.path.exists(cfg_path):
    if os.path.exists(backup):
        os.remove(backup)
    os.replace(cfg_path, backup)
    print("真实配置已挪到 %s" % backup)

d = prompt_spec.demo_input()
dlv = prompt_spec.demo_input_longvideo()

# 长视频页的示例输出：拿官方范例改成长视频合法写法（去时间戳 + 补一致性声明），
# 这样截图里的字数预算和校验报告都是真实结果，不是假数据。
import longvideo  # noqa: E402
import re  # noqa: E402
LV_DEMO_OUTPUT = re.sub(r"At\s+\d{2}:\d{2}\.\d{3},\s*", "", h3_skill.OFFICIAL_EXAMPLE)
LV_DEMO_OUTPUT = LV_DEMO_OUTPUT.replace(
    "overall_soundscape:", longvideo.CONSISTENCY_SENTENCE + "\n\noverall_soundscape:", 1)

work = {
    "pictures": [{"role": prompt_spec.ROLES[p.role]["zh"], "short": p.short_name,
                  "body": p.description} for p in d.pictures],
    "brief": d.brief,
    "scheme": h3_skill.SCHEMES["official"]["zh"],
    "duration": "6",
    "seg_style": "[Shot N] At MM:SS.mmm",
    "seg_count": "0",
    "camera": d.camera,
    "cam_custom": "",
    "loop": True,
    "audio": True,
    "primary": "<Subject %d>" % (d.primary_audio_subject + 1),
    "dialogue": d.dialogue,
    "sound": d.sound_brief,
    "prohib": d.prohibitions,
    "extra_rules": "",
    "output": h3_skill.OFFICIAL_EXAMPLE,
    "active_page": "lv",          # 截图拍长视频页（新做的那个）
    "lv": {
        "pictures": [{"role": prompt_spec.ROLES[p.role]["zh"], "short": p.short_name,
                      "body": p.description} for p in dlv.pictures],
        "brief": dlv.brief,
        "duration": "%g" % dlv.duration,
        "mode": longvideo.MODE_ZH[dlv.lv_mode],
        "shots": "0",
        "window": str(dlv.lv_window),
        "context": str(dlv.lv_context),
        "camera": dlv.camera,
        "cam_custom": "",
        "loop": True,
        "audio": True,
        "primary": "<Subject 1>",
        "dialogue": "",
        "sound": dlv.sound_brief,
        "prohib": dlv.prohibitions,
        "output": LV_DEMO_OUTPUT,
    },
}

cfg = cfgstore.load()          # 真实配置已挪走，这里拿到的是默认值
cfg["api_key"] = "sk-DEMO-KEY-NOT-REAL-0000000000000000000000"
cfg["remember_key"] = True
cfg["work"] = work
err = cfgstore.save(cfg)
assert not err, err
print("示例配置已写入 %s（%d 字节）" % (cfg_path, os.path.getsize(cfg_path)))
print("output 段落数：%d，字符 %d" % (len(h3_skill.OFFICIAL_EXAMPLE.split("\n")),
                                      len(h3_skill.OFFICIAL_EXAMPLE)))
