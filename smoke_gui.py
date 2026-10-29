# -*- coding: utf-8 -*-
"""GUI 冒烟测试：真的把窗口建起来，检查关键控件都在、布局没崩，然后关掉。

不会发起任何网络请求。用法：python smoke_gui.py
"""
import sys
import os
import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app as appmod  # noqa: E402
import longvideo  # noqa: E402
import prompt_spec  # noqa: E402
import validate  # noqa: E402


def walk(w, depth=0, out=None):
    out = out if out is not None else []
    for c in w.winfo_children():
        out.append((depth, c.winfo_class(), str(c)))
        walk(c, depth + 1, out)
    return out


def main():
    # 别动用户真实的配置：先挪走，跑完还回去
    import config as cfgstore
    cfgp = cfgstore.config_path()
    backup = None
    if os.path.exists(cfgp):
        backup = cfgp + ".smoke-backup"
        os.replace(cfgp, backup)

    try:
        return _run()
    finally:
        if os.path.exists(cfgp):
            os.remove(cfgp)
        if backup:
            os.replace(backup, cfgp)


def _run():
    root = tk.Tk()
    a = appmod.App(root)
    root.update_idletasks()
    root.update()

    # 关键控件必须存在
    for name in ("nb", "btn_gen", "out", "rep", "brief", "duration", "seg_style",
                 "camera", "v_api_base", "v_api_key", "v_model", "preview",
                 "extra_rules", "prohib", "sound", "dialogue", "primary",
                 "scheme", "lbl_seg_style", "lbl_seg_count", "chk_audio",
                 "btn_skill_fetch", "skill_doc", "skill_text", "skill_rules", "skill_status"):
        assert hasattr(a, name), "缺少控件 %s" % name
    assert len(a.pic_widgets) == 3, "参考图输入框不是 3 个"
    for i, w in enumerate(a.pic_widgets):
        for k in ("role", "short", "body"):
            assert k in w, "参考图 %d 缺少 %s" % (i + 1, k)

    # 五个标签页
    tabs = [a.nb.tab(i, "text").strip() for i in a.nb.tabs()]
    assert len(tabs) == 5, tabs
    assert any("官方 Skill" in t for t in tabs), tabs
    assert any("长视频" in t for t in tabs), tabs
    print("tabs:", tabs)

    # 长视频页的控件
    for name in ("tab_long", "lv_pics", "lv_brief", "lv_duration", "lv_mode", "lv_shots",
                 "lv_window", "lv_context", "lv_camera", "lv_primary", "lv_dialogue",
                 "lv_sound", "lv_prohib", "lv_plan", "lv_budget_bar", "lv_budget_var",
                 "lv_budget_lbl", "lv_out", "lv_rep", "btn_gen_lv", "btn_cancel_lv",
                 "btn_fix_lv", "lv_scroll"):
        assert hasattr(a, name), "缺少长视频页控件 %s" % name
    assert len(a.lv_pics) == 3, "长视频页参考图输入框不是 3 个"
    for i, w in enumerate(a.lv_pics):
        for k in ("role", "short", "body"):
            assert k in w, "长视频页参考图 %d 缺少 %s" % (i + 1, k)

    # 默认值
    assert float(a.duration.get()) == 6.0, a.duration.get()
    assert a.v_api_base.get() == "https://api.siliconflow.cn/v1", a.v_api_base.get()
    assert a.v_model.get() == "Pro/deepseek-ai/DeepSeek-V3.2", a.v_model.get()
    import h3_skill
    assert a._current_scheme() == "official", a._current_scheme()
    assert a.seg_style.get() == appmod.OFFICIAL_SHOT_ZH, a.seg_style.get()
    assert str(a.seg_style.cget("state")) == "disabled", "官方方案下分段样式应该锁死"
    assert a.lbl_seg_count.cget("text") == "分镜数"
    assert "声音两段" in a.chk_audio.cget("text")

    # 载入示例 -> 采集输入 -> 组装消息，全程不联网
    a._load_demo()
    inp = a.collect_input()
    assert inp.duration == 6.0, inp.duration
    assert inp.format_scheme == "official", inp.format_scheme
    assert inp.segment_style == "shot", inp.segment_style
    assert inp.pictures[0].role == "character"
    assert inp.pictures[1].role == "face_detail"
    assert inp.pictures[2].role == "scene"
    assert "草莓" in inp.brief
    import prompt_spec
    msgs = prompt_spec.build_messages(inp)
    for s in h3_skill.REF2VA_SECTIONS:
        assert s in msgs[0]["content"], "官方 system prompt 里缺 %s" % s
    assert "<Picture 3>" in msgs[1]["content"]
    assert "FORBIDDEN TAGS" in msgs[1]["content"]

    # 切到五段方案，控件和消息都要跟着变
    a.scheme.set(appmod.SCHEME_ZH_LIST[1])
    a._on_scheme_change(quiet=True)
    assert a._current_scheme() == "workflow5"
    assert str(a.seg_style.cget("state")) == "readonly", "五段方案下分段样式应该可用"
    assert a.seg_style.get() in appmod.STYLE_ZH, a.seg_style.get()
    assert a.lbl_seg_count.cget("text") == "分段数"
    assert "音频三段" in a.chk_audio.cget("text")
    inp5 = a.collect_input()
    assert inp5.format_scheme == "workflow5"
    msgs5 = prompt_spec.build_messages(inp5)
    assert "overall_style" in msgs5[0]["content"]
    assert "retention_analysis" not in msgs5[0]["content"]
    print("方案切换 OK：官方六段 %d 字符 / 五段 %d 字符"
          % (len(msgs[0]["content"]), len(msgs5[0]["content"])))

    # 方案要能随草稿存下来
    a._save_all()
    saved = __import__("config").load().get("work") or {}
    assert saved.get("scheme") == appmod.SCHEME_ZH_LIST[1], saved.get("scheme")
    print("方案持久化 OK ->", saved.get("scheme"))
    a.scheme.set(appmod.SCHEME_ZH_LIST[0])
    a._on_scheme_change(quiet=True)

    # ---- 长视频页：默认参数、分段预算、消息、校验模式 ----
    lvi = a.collect_input("lv")
    assert lvi.long_video is True, lvi.long_video
    assert lvi.page == "lv", lvi.page
    assert lvi.duration == 30.0, lvi.duration
    assert lvi.lv_mode in longvideo.MODE_KEYS, lvi.lv_mode
    assert lvi.lv_window == 124 and lvi.lv_context == 22, (lvi.lv_window, lvi.lv_context)
    plan = appmod.txt_get(a.lv_plan)
    assert "7 段" in plan, plan[:120]
    assert "124" in plan and "102" in plan, plan[:120]
    assert "7000" in a.lv_budget_var.get(), a.lv_budget_var.get()
    lvmsgs = prompt_spec.build_messages(lvi)
    assert "LONG VIDEO MODE" in lvmsgs[0]["content"], "长视频规则块没进 system prompt"
    assert str(longvideo.SUBMISSION_CHAR_LIMIT) in lvmsgs[0]["content"]
    assert "绝对禁止任何绝对时间码" in lvmsgs[1]["content"]
    assert "At MM:SS.mmm, ` and the cut times" not in lvmsgs[1]["content"]

    # 逐段模式要额外要 segment_prompts_json
    a.lv_mode.set(longvideo.MODE_ZH[longvideo.MODE_SEGMENTED])
    assert "segment_prompts_json" in prompt_spec.build_user_message(a.collect_input("lv"))
    a.lv_mode.set(longvideo.MODE_ZH[longvideo.MODE_CONTINUOUS])

    # 短视频页不受影响
    gi = a.collect_input("gen")
    assert gi.long_video is False and gi.page == "gen", (gi.long_video, gi.page)
    assert "LONG VIDEO MODE" not in prompt_spec.build_messages(gi)[0]["content"]

    # 长视频校验：时间码必须报错，长视频模式不能误报缺 At MM:SS.mmm
    dirty = ("subject_definitions:\n<Picture 1> ...\n\ndetailed_description:\n"
             "[0-4s] she looks at the camera.\n[Shot 2] At 00:03.500, she turns.\n")
    lvrep = validate.check(dirty, 30.0, True, "shot", "official", None, long_video=True)
    lverr = [m for lv, m in lvrep if lv == "err"]
    assert any("绝对时间码" in m for m in lverr), lverr
    assert any("一致性声明" in m for m in lverr), lverr
    assert not any("缺少 “At MM:SS.mmm,”" in m for m in lverr), lverr
    print("长视频页 OK：%d 段 / %s" % (longvideo.plan_summary(30.0)["segments"], plan.split("｜")[1].strip()))

    # 结果必须写回本页的输出框和报告框（两页不能串台）
    fake = ("subject_definitions:\n<Picture 1>, <Picture 2> and <Picture 3> define "
            "<Subject 1>.\n\n" + h3_skill.OFFICIAL_EXAMPLE)
    a._on_result("generate", fake, {"prompt_tokens": 1, "completion_tokens": 2}, 1.5, lvi)
    assert len(appmod.txt_get(a.lv_out)) > 100, "长视频结果没写进 lv_out"
    assert "✗" in appmod.txt_get(a.lv_rep) or "✓" in appmod.txt_get(a.lv_rep), "lv_rep 没出报告"
    assert appmod.txt_get(a.out).strip() in ("",) or "Picture 3" not in appmod.txt_get(a.out), \
        "长视频的结果串到短视频页的输出框了"
    a._on_result("generate", fake, None, 1.0, gi)
    assert "Picture 3" in appmod.txt_get(a.out), "短视频结果没写进 out"
    print("结果回写分页 OK")

    # 官方 Skill 页：没缓存时给提示，不崩
    a.skill_doc.set("ref-en.txt")
    a._show_skill_doc()
    body = appmod.txt_get(a.skill_text)
    assert body.strip(), "官方指南阅读区是空的"
    assert h3_skill.RULES_ZH.strip()[:20] in appmod.txt_get(a.skill_rules)
    print("官方 Skill 页 OK：%s" % a.skill_status.get()[:46])

    # 尺寸/布局：窗口真的有内容
    w, h = root.winfo_width(), root.winfo_height()
    assert w > 800 and h > 500, (w, h)
    n = len(walk(root))
    print("窗口 %dx%d，控件总数 %d" % (w, h, n))
    print("system prompt %d chars / user message %d chars"
          % (len(msgs[0]["content"]), len(msgs[1]["content"])))

    # 设置能存能读
    a.on_save_settings()
    again = __import__("config").load()
    assert again["model"] == a.v_model.get(), again["model"]
    assert again["api_base"] == a.v_api_base.get(), again["api_base"]
    assert again["timeout"] == int(float(a.v_timeout.get()))
    print("设置持久化 OK")
    print("SMOKE OK")

    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
