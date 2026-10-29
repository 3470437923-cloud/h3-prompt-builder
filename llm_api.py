# -*- coding: utf-8 -*-
"""硅基流动 (SiliconFlow) / 任意 OpenAI 兼容接口的最小客户端，只用标准库。"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Pro/deepseek-ai/DeepSeek-V3.2"
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT = 300

# 只是给下拉框一点起点，真正的列表用“拉取模型列表”按钮从接口取
MODEL_SUGGESTIONS = [
    "Pro/deepseek-ai/DeepSeek-V3.2",
    "deepseek-ai/DeepSeek-V3.2-Exp",
    "deepseek-ai/DeepSeek-V3",
    "Pro/deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-R1",
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "moonshotai/Kimi-K2-Instruct",
    "THUDM/glm-4.5",
]


class ApiError(Exception):
    """带中文说明的接口错误。"""


def endpoint_of(base: str) -> str:
    b = (base or DEFAULT_BASE).strip().rstrip("/")
    if not b:
        b = DEFAULT_BASE
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/v1"):
        return b + "/chat/completions"
    return b + "/v1/chat/completions"


def _ssl_ctx(verify: bool = True):
    if verify:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _friendly(code: int, detail: str) -> str:
    d = detail.lower()
    if code == 401:
        return "API Key 无效或没填（硅基流动控制台 → API 密钥）"
    if code == 402:
        return "余额不足 / 该模型需要付费权限"
    if code == 403:
        return "没有权限访问该模型或该接口"
    if code == 404:
        return "接口地址或模型名不存在（检查 base_url 和 model 拼写）"
    if code == 422:
        return "请求参数不合法（模型名/参数格式）"
    if code == 429:
        return "触发限流或并发上限，稍后再试"
    if code >= 500:
        return "服务端错误，稍后再试或换一个模型"
    if "content filter" in d or "risk" in d:
        return "内容被安全策略拦截，换一种描述再试"
    return "接口返回错误"


def chat_completion(base: str, api_key: str, model: str, messages: list,
                    temperature: float = DEFAULT_TEMPERATURE,
                    max_tokens: int = DEFAULT_MAX_TOKENS,
                    timeout: int = DEFAULT_TIMEOUT,
                    extra_payload: dict | None = None,
                    verify_ssl: bool = True) -> tuple[str, dict]:
    """发一次 chat/completions，返回 (正文, usage)。失败抛 ApiError。"""
    if not (api_key or "").strip():
        raise ApiError("还没有填 API Key。请打开“API 设置”页，粘贴硅基流动的密钥。")
    if not (model or "").strip():
        raise ApiError("还没有填模型名。")

    payload = {
        "model": model.strip(),
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "stream": False,
    }
    if extra_payload:
        for k, v in extra_payload.items():
            payload[k] = v

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint_of(base),
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key.strip(),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "H3-Prompt-Builder/0.1",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(verify_ssl)) as resp:
            raw = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:600]
        except Exception:
            pass
        raise ApiError("HTTP %s：%s\n%s" % (e.code, _friendly(e.code, detail), detail)) from None
    except urllib.error.URLError as e:
        raise ApiError("网络错误：%s\n（检查网络/代理，或接口地址是否为 %s）"
                       % (getattr(e, "reason", e), endpoint_of(base))) from None
    except TimeoutError:
        raise ApiError("请求超时（%s 秒）。可在 API 设置里把超时调大，或换更快的模型。" % timeout) from None
    except Exception as e:  # noqa: BLE001
        raise ApiError("请求失败：%s" % e) from None

    try:
        body = json.loads(raw)
    except Exception:
        raise ApiError("接口返回不是合法 JSON：\n%s" % raw[:400]) from None

    choices = body.get("choices") or []
    if not choices:
        raise ApiError("接口没有返回 choices：\n%s" % json.dumps(body, ensure_ascii=False)[:400])
    msg = choices[0].get("message") or {}
    text = msg.get("content")
    if isinstance(text, list):  # 有些网关返回分段
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    text = (text or "").strip()

    reasoning = (msg.get("reasoning_content") or "").strip()
    note = ""
    if not text and reasoning:
        text = reasoning
        note = "（正文为空，已退回使用 reasoning_content；建议改用非推理模型）"
    if not text:
        raise ApiError("接口返回的正文是空的。\n%s" % json.dumps(body, ensure_ascii=False)[:400])

    usage = dict(body.get("usage") or {})
    if note:
        usage["note"] = note
    finish = choices[0].get("finish_reason")
    if finish == "length":
        usage["note"] = (usage.get("note", "") + " 输出被 max_tokens 截断，请把它调大。").strip()
    return text, usage


def list_models(base: str, api_key: str, timeout: int = 30, verify_ssl: bool = True) -> list[str]:
    b = (base or DEFAULT_BASE).strip().rstrip("/")
    url = b + "/models" if b.endswith("/v1") else b + "/v1/models"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + (api_key or "").strip()},
                                 method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(verify_ssl)) as resp:
            body = json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        raise ApiError("拉取模型列表失败 HTTP %s：%s" % (e.code, _friendly(e.code, ""))) from None
    except Exception as e:  # noqa: BLE001
        raise ApiError("拉取模型列表失败：%s" % e) from None

    ids = [m.get("id") for m in (body.get("data") or []) if isinstance(m, dict) and m.get("id")]
    return sorted(set(ids))
