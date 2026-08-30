"""LLM abstraction used by RAG answering and gap analysis.

Providers: anthropic | azure_openai | bedrock | none.
`none` disables LLM features: RAG returns raw retrieved passages, and gap
analysis runs heuristics only.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Optional

from .config import Config


class LlmClient(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        ...

    def complete_json(self, system: str, user: str) -> Optional[dict | list]:
        """Ask for JSON and parse defensively (strips markdown fences)."""
        raw = self.complete(system + "\nRespond with valid JSON only. No prose, no markdown fences.", user)
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"[\[{].*[\]}]", cleaned, flags=re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
            return None


class AnthropicClient(LlmClient):
    def __init__(self, cfg: Config):
        import anthropic

        self.cfg = cfg
        key = cfg.anthropic.api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=key)

    def complete(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.cfg.anthropic.model,
            max_tokens=self.cfg.llm.max_tokens,
            temperature=self.cfg.llm.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")


class AzureOpenAIClient(LlmClient):
    def __init__(self, cfg: Config):
        from openai import AzureOpenAI

        self.cfg = cfg
        self.client = AzureOpenAI(
            azure_endpoint=cfg.azure.openai_endpoint,
            api_key=cfg.azure.openai_api_key or None,
            api_version=cfg.azure.openai_api_version,
        )

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.cfg.azure.chat_deployment,
            max_tokens=self.cfg.llm.max_tokens,
            temperature=self.cfg.llm.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


class BedrockClient(LlmClient):
    def __init__(self, cfg: Config):
        import boto3

        self.cfg = cfg
        self.client = boto3.client("bedrock-runtime", region_name=cfg.aws.region)

    def complete(self, system: str, user: str) -> str:
        resp = self.client.converse(
            modelId=self.cfg.aws.chat_model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={
                "maxTokens": self.cfg.llm.max_tokens,
                "temperature": self.cfg.llm.temperature,
            },
        )
        parts = resp["output"]["message"]["content"]
        return "".join(p.get("text", "") for p in parts)


class GeminiClient(LlmClient):
    """Google Gemini via the REST API. Uses only the standard library, so it
    needs no extra dependencies — get a free API key at https://aistudio.google.com
    and set GEMINI_API_KEY (or gemini.api_key in config.yaml)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.api_key = cfg.gemini.api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "Gemini selected but no API key found. Get a free key at "
                "https://aistudio.google.com and `export GEMINI_API_KEY=...`"
            )

    def complete(self, system: str, user: str) -> str:
        import time
        import urllib.error
        import urllib.request

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.cfg.gemini.model}:generateContent"
        )
        body = json.dumps({
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "maxOutputTokens": self.cfg.llm.max_tokens,
                "temperature": self.cfg.llm.temperature,
            },
        }).encode()

        attempts = 5
        payload = None
        for attempt in range(attempts):
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                # 503 = overloaded, 429 = rate limited: transient, retry with backoff.
                if e.code in (503, 429) and attempt < attempts - 1:
                    wait = 2 ** (attempt + 1)  # 2, 4, 8, 16 seconds
                    print(f"  Gemini busy ({e.code}), retrying in {wait}s "
                          f"(attempt {attempt + 2}/{attempts})...")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"Gemini API error {e.code}: {detail}") from e
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError):
            block = payload.get("promptFeedback", {}).get("blockReason")
            if block:
                return f"[Gemini declined to respond: {block}]"
            raise RuntimeError(f"Unexpected Gemini response shape: {str(payload)[:300]}")


def build_llm(cfg: Config) -> Optional[LlmClient]:
    provider = cfg.llm.provider.lower()
    if provider in ("", "none"):
        return None
    if provider == "anthropic":
        return AnthropicClient(cfg)
    if provider == "gemini":
        return GeminiClient(cfg)
    if provider == "azure_openai":
        return AzureOpenAIClient(cfg)
    if provider == "bedrock":
        return BedrockClient(cfg)
    raise ValueError(f"Unknown llm.provider: {cfg.llm.provider} (choose: none, gemini, anthropic, azure_openai, bedrock)")
