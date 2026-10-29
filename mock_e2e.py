# -*- coding: utf-8 -*-
"""端到端集成测试：本地起一个假的 OpenAI 兼容接口，把 GUI 整条链路跑通。

覆盖两套输出格式方案：
  A) 官方 Ref2VA 六段（默认）：脏输出 -> 抓到段落/分镜/summary/retention/<d>/节点标签错 -> 修正 -> 0 错
  B) 工作流五段（旧，可切换）：同样走一遍脏输出 -> 修正 -> 0 错
另外覆盖：节点级致命标签（<Audio 1>）、请求体内容、模型列表、官方指南拉取的界面接线、
剪贴板、落盘。

全程 127.0.0.1，不碰任何真实 API Key，也不需要联网。
用法：python mock_e2e.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as appmod          # noqa: E402
import config as cfgstore     # noqa: E402
import h3_skill               # noqa: E402
import prompt_spec            # noqa: E402

PORT = 8799
STATE = {"calls": 0, "models": 0, "seen": [], "queue": []}


def dirty_official():
    """官方六段范例 + 一堆典型毛病。"""
    t = h3_skill.OFFICIAL_EXAMPLE
    t = t.replace("[reference generation] The target video", "The target video", 1)   # summary 前缀没了
    t = t.replace("fully_preserved", "kept_as_is", 1)                                 # retention 标记非法
    t = t.replace("[Shot 2] At 00:03.500,", "[Shot 2] At 00:06.500,", 1)              # 切换点超时长
    t = t.replace("<d>[Chinese] 你等等，我还没看完。</d>", "<d>你等等，我还没看完。</d>", 1)   # 缺语言标签
    t = t.replace("<Subject 1> (S1), her lips", "Subject 1 (S1), her lips", 1)        # 裸写 Subject
    t = t.replace("non_diegetic_music:\nN/A",
                  "non_diegetic_music:\nN/A\n\naudio reference: <Audio 1> gives the timbre.", 1)
    return "好的，我按官方格式写好了：\n\n```text\n" + t + "\n```\n\n希望对你有帮助！"


def dirty_canonical():
    """工作流五段范例 + 一堆典型毛病。"""
    t = (prompt_spec.CANONICAL_EXAMPLE
         .replace("<Subject 1>", "Subject 1", 1)
         .replace("[Shot 3, 5-7s]", "[5-7s]")
         .replace("[Shot 4, 7-10s]", "[Shot 4, 7-9s]")
         .replace("Wind moves her silver-white hair", "风吹动她的银白色头发"))
    return "好的，我按你要求写好了，请查收：\n\n```text\n" + t + "\n```\n\n希望对你有帮助！"


MODELS = [{"id": "Pro/deepseek-ai/DeepSeek-V3.2"}, {"id": "deepseek-ai/DeepSeek-V3"}]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n).decode("utf-8"))
        STATE["seen"].append(payload)
        STATE["calls"] += 1
        body = STATE["queue"].pop(0) if STATE["queue"] else h3_skill.OFFICIAL_EXAMPLE
        out = {
            "id": "mock", "object": "chat.completion", "model": payload.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": body}}],
            "usage": {"prompt_tokens": 4200, "completion_tokens": 800, "total_tokens": 5000},
        }
        data = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        STATE["models"] += 1
        data = json.dumps({"data": MODELS}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def pump(root, seconds=25.0, until=None):
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update()
        time.sleep(0.03)
        if until is not None and until():
            return True
    return False


def errs(a):
    return [m for lv, m in a.last_report if lv == "err"]


def main():
    """先把用户真实配置挪走，跑完（不管成功失败）都还原回去。"""
    cfg_path = cfgstore.config_path()
    backup = None
    if os.path.exists(cfg_path):
        backup = cfg_path + ".e2e-backup"
        os.replace(cfg_path, backup)
        print("已把现有配置挪到 %s" % backup)
    try:
        return _run()
    finally:
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        if backup:
            os.replace(backup, cfg_path)
            print("真实配置已还原")


def _run():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("mock server on 127.0.0.1:%d" % PORT)

    dialogs = []
    appmod.messagebox.showinfo = lambda *a, **k: dialogs.append(("info", a))
    appmod.messagebox.showwarning = lambda *a, **k: dialogs.append(("warn", a))
    appmod.messagebox.showerror = lambda *a, **k: dialogs.append(("error", a))
    appmod.messagebox.askyesno = lambda *a, **k: (dialogs.append(("yesno", a)), True)[1]

    root = tk.Tk()
    a = appmod.App(root)
    a._load_demo()
    a.v_api_base.delete(0, "end")
    a.v_api_base.insert(0, "http://127.0.0.1:%d/v1" % PORT)
    a.v_api_key.delete(0, "end")
    a.v_api_key.insert(0, "sk-mock-not-a-real-key")
    a.v_remember.set(False)
    a.v_verify.set(True)

    # ================= A. 官方六段：脏输出 -> 修正 =================
    assert a._current_scheme() == "official", a._current_scheme()
    STATE["queue"] = [dirty_official()]
    a.on_generate()
    pump(root, 25.0, until=lambda: not a.busy)
    out1 = appmod.txt_get(a.out)
    assert out1.startswith("subject_definitions:"), out1[:80]
    assert "```" not in out1 and "希望对你有帮助" not in out1, "围栏/开场白没洗干净"
    e1 = errs(a)
    print("\n[A1] 官方六段脏输出：抓到 %d 个 err" % len(e1))
    for m in e1:
        print("     x", m[:96])
    assert len(e1) >= 6, e1
    for needle, why in (("节点标签", "<Audio 1> 没被判致命"),
                        ("summary", "summary 前缀缺失没抓到"),
                        ("retention_analysis", "非法 retention 标记没抓到"),
                        ("分镜", "切换点超时长没抓到"),
                        ("台词", "<d> 缺语言标签没抓到"),
                        ("尖括号", "裸写 Subject 没抓到")):
        assert any(needle in m for m in e1), why
    assert "你等等，我还没看完。" in out1, "中文台词被 clean_output 吃掉了"

    STATE["queue"] = [h3_skill.OFFICIAL_EXAMPLE]
    a.on_fix()
    pump(root, 25.0, until=lambda: not a.busy)
    assert not errs(a), "官方六段修正后还有 err：%s" % errs(a)
    fixp = STATE["seen"][-1]
    assert "VIOLATIONS TO FIX" in fixp["messages"][-1]["content"]
    assert len(fixp["messages"]) == 4, "修正请求应该是 system/user/assistant/user"
    assert "<Video N>" in fixp["messages"][-1]["content"], "修正请求没重申媒体标签禁令"
    print("[A2] 修正回环：0 err")

    # 请求体本身
    p1 = STATE["seen"][0]
    sp, um = p1["messages"][0]["content"], p1["messages"][1]["content"]
    assert p1["model"] == "Pro/deepseek-ai/DeepSeek-V3.2" and p1["stream"] is False
    for s in h3_skill.REF2VA_SECTIONS:
        assert s in sp, "官方 system prompt 里缺 %s" % s
    for must in ("YOU ARE BLIND", "raises ValueError", "<Video N>", "MM:SS.mmm",
                 "fully_preserved", "<d>[Language]", "WORKED EXAMPLE"):
        assert must in sp, "官方 system prompt 里缺 %s" % must
    for must in ("FORBIDDEN TAGS", "6 seconds", "<Picture 1>", "草莓"):
        assert must in um, "user message 里缺 %s" % must
    print("[A3] 官方六段请求体 OK（system %d 字符 / user %d 字符）" % (len(sp), len(um)))

    # ================= B. 切到五段方案再走一遍 =================
    a.scheme.set(appmod.SCHEME_ZH_LIST[1])
    a._on_scheme_change(quiet=True)
    a.duration.set("10")
    a.seg_style.set(prompt_spec.SEGMENT_STYLES["shot"]["zh"])
    a.seg_count.set("4")
    STATE["queue"] = [dirty_canonical()]
    a.on_generate()
    pump(root, 25.0, until=lambda: not a.busy)
    e2 = errs(a)
    print("\n[B1] 五段脏输出：抓到 %d 个 err" % len(e2))
    for m in e2:
        print("     x", m[:96])
    assert len(e2) >= 4, e2
    assert any("时间轴" in m for m in e2), "时间轴收错没抓到"
    assert any("样式" in m for m in e2), "分段样式混用没抓到"
    assert any("中文" in m for m in e2), "正文中文没抓到"

    STATE["queue"] = [prompt_spec.CANONICAL_EXAMPLE]
    a.on_fix()
    pump(root, 25.0, until=lambda: not a.busy)
    assert not errs(a), "五段修正后还有 err：%s" % errs(a)
    sp5 = STATE["seen"][-2]["messages"][0]["content"]
    assert "overall_style" in sp5 and "retention_analysis" not in sp5, "五段方案发错了 system prompt"
    print("[B2] 五段修正回环：0 err，system prompt 也确实是五段版")

    # ================= C. 模型列表 =================
    a.on_list_models()
    pump(root, 15.0, until=lambda: "已拉取" in a.status_var.get())
    assert "已拉取 2 个模型" in a.status_var.get(), a.status_var.get()
    print("[C] 模型列表：%s" % a.status_var.get())

    # ================= D. 官方指南拉取的界面接线（不联网，打桩） =================
    tmp = tempfile.mkdtemp(prefix="h3_skill_")
    real_cache_dir, real_fetch = h3_skill.cache_dir, h3_skill.fetch
    h3_skill.cache_dir = lambda: tmp

    def fake_fetch(verify_ssl=True, timeout=60):
        meta = {"source": h3_skill.SKILL_HOME, "fetched_at": "2026-09-19 12:00:00",
                "commit": "deadbeef00", "commit_date": "2026-08-11T11:25:43Z", "files": {}}
        for name, _u, _d in h3_skill.SKILL_FILES:
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                f.write("FAKE %s\n官方指南占位内容，用来验证界面接线。" % name)
            meta["files"][name] = {"bytes": 10}
        with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        return meta

    h3_skill.fetch = fake_fetch
    try:
        a.skill_doc.set("ref-en.txt")
        a.on_fetch_skill()
        ok = pump(root, 15.0, until=lambda: "已拉取并缓存" in a.status_var.get())
        assert ok, a.status_var.get()
        assert "FAKE ref-en.txt" in appmod.txt_get(a.skill_text), "拉完没把原文显示出来"
        assert "deadbeef00" in a.skill_status.get(), a.skill_status.get()
        assert str(a.btn_skill_fetch.cget("state")) == "normal", "拉取按钮没恢复"
        print("[D] 官方指南拉取接线 OK：%s" % a.skill_status.get()[:58])
    finally:
        h3_skill.cache_dir, h3_skill.fetch = real_cache_dir, real_fetch

    # ================= E. 剪贴板 / 落盘 =================
    a.on_copy()
    assert root.clipboard_get().startswith("subject_definitions:"), "剪贴板内容不对"
    p = os.path.join(tempfile.gettempdir(), "h3_e2e_out.txt")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(appmod.txt_get(a.out))
    assert os.path.getsize(p) > 2000
    print("[E] 剪贴板 / 落盘：%d 字节" % os.path.getsize(p))

    print("\n统计：接口调用 %d 次，模型列表 %d 次，弹窗 %d 个"
          % (STATE["calls"], STATE["models"], len(dialogs)))
    print("状态栏:", a.status_var.get()[:90])
    print("\nMOCK E2E OK")

    srv.shutdown()
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
