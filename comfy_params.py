# -*- coding: utf-8 -*-
"""把工作流里的节点数学算给用户看（纯提示用，不影响生成的文本）。

来源：3图参考加速.json
  * ComfyMathExpression: max(5, round(a*24)) + (5 - (max(5, round(a*24)) % 17)) % 17
    —— a 是时长（秒），24fps，帧数被对齐到 ≡5 (mod 17)
  * reference_video_policy = official_2_to_15s  —— 官方时长区间 2~15 秒
  * ResolutionSelector: aspect_ratio + megapixels + multiple=32
  * VHS_VideoCombine: frame_rate=24, format=video/h264-mp4, crf=19
"""

from __future__ import annotations

import math

FPS = 24
MIN_SECONDS = 2.0
MAX_SECONDS = 15.0


def frame_count(seconds: float, fps: int = FPS) -> int:
    f = max(5, int(round(float(seconds) * fps)))
    return f + (5 - (f % 17)) % 17


def duration_note(seconds: float) -> str:
    frames = frame_count(seconds)
    real = frames / float(FPS)
    warn = ""
    if seconds < MIN_SECONDS or seconds > MAX_SECONDS:
        warn = "（超出官方 2~15s 区间，节点会拒绝或行为异常）"
    return "时长 %s s → length=%d 帧 @%dfps ≈ %.2f s%s" % (
        _n(seconds), frames, FPS, real, warn)


def estimate_size(aspect: str = "16:9", megapixels: float = 0.5, multiple: int = 32) -> tuple[int, int]:
    """按 ResolutionSelector 的思路估算输出分辨率（宽向上取整到 multiple 的倍数）。"""
    try:
        aw, ah = (float(x) for x in aspect.replace("：", ":").split(":")[:2])
    except Exception:
        aw, ah = 16.0, 9.0
    ratio = aw / ah if ah else 16 / 9
    total = float(megapixels) * 1_000_000
    w = math.sqrt(total * ratio)
    h = w / ratio
    w = int(math.ceil(w / multiple) * multiple)
    h = int(math.ceil(h / multiple) * multiple)
    return w, h


# 工作流 MarkdownNote 里的官方对照表（16:9）
SIZE_TABLE_16_9 = [
    (0.2, "608 x 352"), (0.3, "736 x 416"), (0.4, "864 x 480"), (0.5, "960 x 544"),
    (0.6, "1056 x 608"), (0.7, "1152 x 640"), (0.8, "1216 x 672"), (0.9, "1280 x 736"),
    (0.98, "1344 x 768"), (1.0, "1376 x 768"), (1.2, "1504 x 832"), (1.5, "1664 x 928"),
    (1.8, "1824 x 1024"), (2.0, "1920 x 1088"),
]


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else ("%.2f" % x).rstrip("0").rstrip(".")
