"""LLM 客户端 —— OpenAI 兼容接口（DeepSeek/智谱/通义/OpenAI 均可，ADR 规划）。

配置（环境变量）：
  HSR_LLM_BASE_URL  如 https://api.deepseek.com/v1（默认）
  HSR_LLM_API_KEY   API Key
  HSR_LLM_MODEL     默认 deepseek-chat
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List


def _extract_json(text: str):
    """从文本中提取最大 JSON 块（content 空时兜底用 reasoning）。"""
    import re
    if not text:
        return None
    start, end = -1, -1
    for i, ch in enumerate(text):
        if ch == "{" and start < 0:
            start = i
        if ch == "}":
            end = i
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def default_config() -> Dict[str, str]:
    return {
        "base_url": os.environ.get("HSR_LLM_BASE_URL", "https://api.deepseek.com/v1"),
        "api_key": os.environ.get("HSR_LLM_API_KEY", ""),
        "model": os.environ.get("HSR_LLM_MODEL", "deepseek-chat"),
        "no_thinking": os.environ.get("HSR_LLM_NO_THINKING", ""),
    }


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 300,
                 disable_thinking: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.disable_thinking = disable_thinking

    @classmethod
    def from_env(cls) -> "LLMClient":
        cfg = default_config()
        if not cfg["api_key"]:
            raise RuntimeError(
                "未配置 HSR_LLM_API_KEY（或 --llm-config 指定）。"
                "支持 OpenAI 兼容接口：DeepSeek/智谱/通义/OpenAI。"
            )
        return cls(cfg["base_url"], cfg["api_key"], cfg["model"],
                   disable_thinking=bool(cfg.get("no_thinking")))

    def chat_json(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> Dict[str, Any]:
        """调用 /chat/completions 并解析 JSON 输出。

        默认带 thinking=disabled（推理模型提速）；若 API 不支持（400）自动降级重试。
        """
        base_body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            # 大胆给满：thinking 模式下 reasoning 与 content 共享 max_tokens 预算，
            # 给太小会截断（finish_reason=length）；上游不认大值时自动降级（见下）
            "max_tokens": 32768,
        }
        body = dict(base_body)
        if self.disable_thinking:
            body["thinking"] = {"type": "disabled"}
        try:
            return self._post(body)
        except urllib.error.HTTPError as e:
            if e.code == 400 and "thinking" in body:
                # API 不支持 thinking 参数，降级重试
                return self._post(base_body)
            if e.code == 400 and body.get("max_tokens", 0) > 8192:
                # 上游 max_tokens 上限更小（如 8192），降级重试
                body["max_tokens"] = 8192
                return self._post(body)
            raise

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """带重试的 POST：502/524/429/500 抖动、超时、输出截断/空 content 均退避重试。"""
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        import time
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"].get("content") or ""
                finish_reason = data["choices"][0].get("finish_reason")
                if finish_reason == "length":
                    # 输出被截断（max_tokens 用尽或上游截断）——重试，不当作非法 JSON 处理
                    last_err = RuntimeError("LLM 输出截断（finish_reason=length）")
                    time.sleep(3.0 * (attempt + 1))
                    continue
                if not content.strip():
                    # 兜底：content 偶发为空时，从 reasoning_content 提取 JSON
                    rc = data["choices"][0]["message"].get("reasoning_content") or ""
                    extracted = _extract_json(rc)
                    if extracted is not None:
                        return extracted
                    last_err = RuntimeError("LLM 返回空 content 且 reasoning 中无 JSON")
                    time.sleep(3.0 * (attempt + 1))
                    continue
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # 非法 JSON：先尝试提取合法块，失败则重试（截断/包装文本都归此类）
                    extracted = _extract_json(content)
                    if extracted is not None:
                        return extracted
                    last_err = RuntimeError("LLM 输出非合法 JSON 且无法提取")
                    time.sleep(3.0 * (attempt + 1))
                    continue
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (502, 524, 429, 500):
                    time.sleep(3.0 * (attempt + 1))
                    continue
                raise
            except (ConnectionError, urllib.error.URLError) as e:
                # 上游断连/网络抖动（RemoteDisconnected、DNS 等）——退避重试
                last_err = e
                time.sleep(3.0 * (attempt + 1))
                continue
            except TimeoutError as e:
                last_err = e
                time.sleep(3.0)
                continue
        raise RuntimeError(
            f"LLM 请求失败（重试 5 次后）：{last_err}. "
            "中转站/上游可能不稳定，可稍后重试。"
        )
