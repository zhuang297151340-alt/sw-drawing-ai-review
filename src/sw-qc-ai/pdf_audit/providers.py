# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    pass


def _env(name: str, fallback: str = "") -> str:
    return (os.environ.get(name, fallback) or "").strip()


def provider_config() -> dict:
    provider = _env("PDF_AI_PROVIDER", _env("QC_AI_PROVIDER")).lower()
    base = _env("PDF_AI_BASE", _env("QC_AI_BASE"))
    model = _env("PDF_AI_MODEL", _env("QC_AI_MODEL"))
    key = _env("PDF_AI_KEY", _env("QC_AI_KEY"))

    if not base:
        base = {
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "moonshot": "https://api.moonshot.cn/v1",
            "kimi": "https://api.moonshot.cn/v1",
            "openai": "https://api.openai.com/v1",
            "chatgpt": "https://api.openai.com/v1",
        }.get(provider, "")
    if not model:
        model = {
            "qwen": "qwen-vl-max",
            "moonshot": "kimi-k3.1-pro",
            "kimi": "kimi-k3.1-pro",
            "openai": "gpt-4.1",
            "chatgpt": "gpt-4.1",
        }.get(provider, "")
    return {"provider": provider, "base": base.rstrip("/"), "model": model, "key": key}


def validate_vision_config(cfg: dict) -> None:
    if not cfg.get("key"):
        raise ProviderError("PDF视觉审核未配置 API Key。请配置 pdf_ai_key，或选择可用的视觉模型账号。")
    if not cfg.get("base") or not cfg.get("model"):
        raise ProviderError("PDF视觉审核缺少接口地址或模型名。")
    probe = (cfg.get("provider", "") + " " + cfg.get("base", "") + " " + cfg.get("model", "")).lower()
    # 已知纯文本模型直接拒绝，避免生成空白或伪造的视觉结论。
    if "deepseek" in probe or "text-" in probe:
        raise ProviderError(
            "当前配置是纯文本模型（%s），不能读取工程图。请为 PDF 通道配置千问视觉或 Kimi 视觉模型。"
            % cfg.get("model", "")
        )


def _data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return "data:%s;base64,%s" % (mime, data)


def _parse_json_text(value: object) -> dict:
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") in ("text", "output_text"):
                parts.append(str(item.get("text") or ""))
        value = "\n".join(parts)
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except Exception as exc:
        # 某些兼容接口会在 JSON 前后多写一句话，尝试提取最外层对象。
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError("模型没有返回合法 JSON：%s" % text[:240]) from exc
        try:
            obj = json.loads(text[start:end + 1])
        except Exception as exc2:
            raise ProviderError("模型返回的 JSON 无法解析：%s" % text[:240]) from exc2
    if not isinstance(obj, dict):
        raise ProviderError("模型返回值不是 JSON 对象。")
    return obj


class OpenAICompatibleVisionProvider:
    def __init__(self, timeout: int = 300):
        self.cfg = provider_config()
        validate_vision_config(self.cfg)
        self.timeout = max(30, int(timeout or 300))
        self.call_count = 0
        self.http_attempts = 0
        self.model_elapsed_sec = 0.0

    @property
    def description(self) -> str:
        return "%s/%s" % (self.cfg.get("provider") or "custom", self.cfg.get("model") or "")

    def call_json(self, system: str, user_text: str, images: list[str] | None = None,
                  reasoning_effort: str | None = None) -> dict:
        self.call_count += 1
        started = time.monotonic()
        try:
            return self._call_json(system, user_text, images, reasoning_effort)
        finally:
            self.model_elapsed_sec += time.monotonic() - started

    def _call_json(self, system: str, user_text: str, images: list[str] | None = None,
                   reasoning_effort: str | None = None) -> dict:
        content: list[dict] = [{"type": "text", "text": user_text}]
        for path in images or []:
            content.append({"type": "text", "text": "图像证据文件：" + os.path.basename(path)})
            content.append({
                "type": "image_url",
                "image_url": {"url": _data_url(path), "detail": "high"},
            })
        provider_name = str(self.cfg.get("provider") or "").lower()
        model_name = str(self.cfg.get("model") or "").lower()
        # K3/K3.1 的温度参数固定，官方要求省略；K2.6 非思考模式使用 0.6。
        # 其他兼容视觉提供商保持 temperature=0，便于结构化输出稳定复现。
        if model_name.startswith("kimi-k2.6"):
            temperature = 0.6
        elif provider_name in ("kimi", "moonshot") or model_name.startswith("kimi-") or model_name.startswith("k3"):
            temperature = 1
        else:
            temperature = 0
        payload = {
            "model": self.cfg["model"],
            # 工程图阶段需要稳定的结构化事实，不需要长链路思考。K2.6 若不显式
            # 关闭 thinking，复杂多图请求可能长时间不返回；同时限制最大输出，
            # 防止模型为一个页面预留过大的生成预算。
            "max_completion_tokens": 16384,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
        }
        if not model_name.startswith("kimi-k3") and model_name not in ("k3", "k3-256k"):
            payload["temperature"] = temperature
        if model_name.startswith("kimi-k3.1"):
            payload["max_completion_tokens"] = 32768
        if model_name.startswith("kimi-k2.6"):
            payload["thinking"] = {"type": "disabled"}
        elif model_name.startswith("kimi-k3") or model_name in ("k3", "k3-256k"):
            default_effort = "high" if model_name.startswith("kimi-k3.1") else "low"
            payload["reasoning_effort"] = (reasoning_effort if reasoning_effort in ("low", "high", "max")
                                           else default_effort)
            # K3 系列始终推理。流式接收能避免等待完整答案时的空闲读超时。
            payload["stream"] = True
        def _request(body: dict):
            return urllib.request.Request(
                self.cfg["base"] + "/chat/completions",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.cfg["key"],
                },
                method="POST",
            )
        def _read_stream(resp) -> dict:
            content: list[str] = []
            completed = False
            finish_reason = ""
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    completed = True
                    break
                if not data:
                    continue
                try:
                    chunk = json.loads(data)
                except ValueError as exc:
                    raise ProviderError("视觉模型返回了无效的流式数据。") from exc
                if chunk.get("error"):
                    raise ProviderError("视觉模型流式调用失败：%s" % str(chunk["error"])[:300])
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    fragment = delta.get("content")
                    if fragment:
                        content.append(str(fragment))
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
            if not completed:
                raise ProviderError("视觉模型流式响应中断，未收到完成标记；本页审核未完成。")
            if finish_reason == "length":
                raise ProviderError("视觉模型输出达到长度上限，本页审核未完成。")
            if not content:
                raise ProviderError("视觉模型没有返回审核内容，本页审核未完成。")
            return {"choices": [{"message": {"content": "".join(content)}}]}
        def _send(body: dict) -> dict:
            # 视觉服务短时过载时自动重试；额度耗尽等永久性 429 不重试。
            for attempt in range(4):
                try:
                    self.http_attempts += 1
                    with urllib.request.urlopen(_request(body), timeout=self.timeout) as resp:
                        if body.get("stream"):
                            return _read_stream(resp)
                        return json.loads(resp.read().decode("utf-8", "replace"))
                except urllib.error.HTTPError as exc:
                    if exc.code not in (429, 500, 502, 503, 504):
                        raise
                    detail = exc.read().decode("utf-8", "replace")[:500]
                    overloaded = "engine_overloaded" in detail.lower()
                    transient = overloaded or exc.code in (500, 502, 503, 504)
                    if not transient:
                        raise ProviderError("视觉模型 HTTP 429：调用频率或额度受限。%s" % detail) from exc
                    if attempt == 3:
                        raise ProviderError(
                            "视觉模型暂时过载，已自动重试 3 次仍未恢复。请稍后重新检测。HTTP %s：%s"
                            % (exc.code, detail)
                        ) from exc
                    delay = min(8, 2 ** (attempt + 1))
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    if retry_after:
                        try:
                            delay = min(20, max(delay, int(retry_after)))
                        except ValueError:
                            pass
                    time.sleep(delay)
            raise ProviderError("视觉模型重试次数已用尽。")
        try:
            try:
                body = _send(payload)
            except urllib.error.HTTPError as first:
                # 部分 OpenAI 兼容视觉接口暂不接受 response_format；保留严格 JSON 提示后重试一次。
                if first.code == 400 and "response_format" in payload:
                    try:
                        first.read()
                    except Exception:
                        pass
                    retry_payload = dict(payload)
                    retry_payload.pop("response_format", None)
                    body = _send(retry_payload)
                else:
                    raise
            message = body["choices"][0]["message"]
            return _parse_json_text(message.get("content"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            hint = {401: "API Key 无效", 403: "无模型权限或地区限制", 404: "接口地址或模型名错误",
                    413: "图像或输入过大", 429: "调用频率或额度受限"}.get(exc.code, "")
            raise ProviderError("视觉模型 HTTP %s：%s %s" % (exc.code, hint, detail)) from exc
        except TimeoutError as exc:
            raise ProviderError("视觉模型等待响应超时（%d 秒）；本页审核未完成。" % self.timeout) from exc
        except urllib.error.URLError as exc:
            raise ProviderError("视觉模型网络连接失败：%s" % exc.reason) from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("视觉模型调用失败：%s" % exc) from exc

