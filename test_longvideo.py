# -*- coding: utf-8 -*-
"""长视频（首尾相连）页的全链路测试：拼消息 → 校验 → 字数预算 → 分段 JSON。

不联网、不碰真实配置。用法：python test_longvideo.py
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3_skill          # noqa: E402
import longvideo         # noqa: E402
import prompt_spec       # noqa: E402
import validate          # noqa: E402

fails = []


def ck(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def errs(rep):
    return [m for lv, m in rep if lv == "err"]


print("=" * 70)
print("1) 长视频 system / user 消息")
print("=" * 70)
inp = prompt_spec.demo_input_longvideo()
msgs = prompt_spec.build_messages(inp)
sys_p, usr_p = msgs[0]["content"], msgs[1]["content"]
pl = longvideo.plan_summary(inp.duration, inp.lv_window, inp.lv_context)
print("   段数 %d ｜ system %d 字符 ｜ user %d 字符" % (pl["segments"], len(sys_p), len(usr_p)))
ck("LONG VIDEO MODE" in sys_p, "system prompt 里进了长视频规则块")
ck(str(longvideo.SUBMISSION_CHAR_LIMIT) in sys_p, "规则块里写明 %d 字符上限"
   % longvideo.SUBMISSION_CHAR_LIMIT)
ck("FORBIDDEN" in sys_p and "absolute timestamps" in sys_p.lower(), "规则块明确禁止绝对时间码")
ck("global_prompt" in sys_p or "SAME prompt to EVERY segment" in sys_p,
   "规则块说明了「同一份 prompt 发给每一段」")
ck(longvideo.CONSISTENCY_SENTENCE[:50] in sys_p, "规则块给出必须照抄的一致性句子")
ck("绝对禁止任何绝对时间码" in usr_p, "user 消息禁止绝对时间码")
ck("%d 段" % pl["segments"] in usr_p, "user 消息写明段数（%d）" % pl["segments"])
ck("7000" in usr_p, "user 消息写明字数预算")
ck("At MM:SS.mmm, ` and the cut times" not in usr_p,
   "user 消息里不再要求 At MM:SS.mmm 切换时间")

inp_seg = prompt_spec.demo_input_longvideo()
inp_seg.lv_mode = longvideo.MODE_SEGMENTED
usr_seg = prompt_spec.build_user_message(inp_seg)
ck("segment_prompts_json" in usr_seg, "逐段模式下 user 消息要 segment_prompts_json")

base = prompt_spec.demo_input()
sys_short = prompt_spec.build_system_prompt(base.duration, True, "", "official")
ck(len(sys_p) > len(sys_short), "长视频 system 比短的长（%d > %d）" % (len(sys_p), len(sys_short)))

print()
print("=" * 70)
print("2) 一份官方范例改成「长视频合法写法」后必须 0 err")
print("=" * 70)
text = h3_skill.OFFICIAL_EXAMPLE
clean = re.sub(r"At\s+\d{2}:\d{2}\.\d{3},\s*", "", text)
ck(clean != text, "把范例里的 At MM:SS.mmm 切换点摘掉了")
ck("overall_soundscape:" in clean, "范例结构完整（有 overall_soundscape）")
clean = clean.replace("overall_soundscape:",
                      longvideo.CONSISTENCY_SENTENCE + "\n\noverall_soundscape:", 1)
rep_lv = validate.check(clean, 30.0, True, "shot", "official", None, long_video=True)
e = errs(rep_lv)
ck(not e, "长视频模式校验 0 err（实际 %d 条：%s）" % (len(e), e[:2]))
ck(any(lv == "ok" and "不含时间码" in m for lv, m in rep_lv), "给出「全部不含时间码」的 ok")
ck(any(lv == "ok" and "一致性声明覆盖" in m for lv, m in rep_lv), "给出一致性声明覆盖的 ok")
ck(any(lv == "ok" and "字符预算内" in m for lv, m in rep_lv), "给出字数预算 ok")
print("   warn：%s" % [m for lv, m in rep_lv if lv == "warn"])

print()
print("=" * 70)
print("3) 同一份文本按「短视频模式」判，应该报缺时间戳（证明模式真的起作用）")
print("=" * 70)
rep_short = validate.check(clean, 30.0, True, "shot", "official", None, long_video=False)
es = errs(rep_short)
ck(any("MM:SS" in m for m in es), "短模式报「缺 At MM:SS.mmm」（%s）" % es[:2])

print()
print("=" * 70)
print("4) 用户那份坏提示词的等价形态：时间码 + 中文 + 缺一致性 → 三类 err")
print("=" * 70)
dirty = (
    "subject_definitions:\n\n"
    "<Subject 1> is the chubby boy shown in <Picture 1>. Preserve exactly his identity: "
    "chubby face, wearing glasses.\n\n"
    "<Subject 2> is the chubby boy shown in <Picture 2>.\n\n"
    "detailed_description:\n\n"
    "A 12-second continuous single shot referencing <Subject 1> and <Subject 2>.\n\n"
    "[0-4s] 人物安静地看着镜头，神情平静，呼吸缓慢。\n"
    "[4-8s] 人物微微转头，视线移动。\n"
    "[8-12s] 人物重新看向镜头。\n\n"
    "overall_soundscape:\nQuiet room tone.\n\n"
    "non_diegetic_music:\nN/A\n"
)
rd = validate.check(dirty, 30.0, True, "shot", "official", None, long_video=True)
ed = errs(rd)
ck(any("绝对时间码" in m for m in ed), "抓出绝对时间码")
ck(any("中文" in m for m in ed), "抓出正文中文（native 音频下会被当台词念）")
ck(any("一致性声明" in m for m in ed), "抓出缺一致性声明")
ck(any("<Picture 3>" in m for m in ed), "抓出第 3 张参考图没被引用")
print("   err 共 %d 条：" % len(ed))
for m in ed:
    print("      - %s" % m[:110])

print()
print("=" * 70)
print("5) 字数预算")
print("=" * 70)
big = clean + "\n" + ("filler " * 600)
rb = validate.check(big, 30.0, True, "shot", "official", None, long_video=True)
ck(any(lv == "warn" and "超出官方 CLI 提交上限" in m for lv, m in rb),
   "超 7000 字符给 warn（%d 字符）" % len(big))
ck(not any(lv == "err" and "超" in m for lv, m in rb),
   "超字数只是 warn 不是 err（本地节点不拦）")

print()
print("=" * 70)
print("6) segment_prompts_json 骨架")
print("=" * 70)
sk = longvideo.segmented_json(pl["plan"], ["seg one", "", "seg three"])
data = json.loads(sk)
ck(isinstance(data, list) and len(data) == pl["segments"],
   "条数 = 段数（%d）" % pl["segments"])
ck(data[0]["prompt"] == "seg one" and data[2]["prompt"] == "seg three",
   "逐段 prompt 按位置填进去了")
ck(all("note" in r for r in data), "每条都带 note")

print()
if fails:
    print("LONGVIDEO FAIL: %d 项" % len(fails))
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("LONGVIDEO OK")
