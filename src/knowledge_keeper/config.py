"""Configuration: YAML file + environment variable overrides.

Provider selection:
    provider: local | azure | aws        (vector store + embeddings)
    llm.provider: none | anthropic | azure_openai | bedrock

`local` + `llm.provider: none` runs fully offline (dev/demo mode) using a
deterministic hashing embedder and heuristic-only gap analysis.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

ENV_PREFIX = "KK_"


class AzureConfig(BaseModel):
    search_endpoint: str = ""          # https://<name>.search.windows.net
    search_index: str = "knowledge-keeper"
    search_api_key: str = ""           # or leave empty to use DefaultAzureCredential
    openai_endpoint: str = ""          # https://<name>.openai.azure.com
    openai_api_key: str = ""
    openai_api_version: str = "2024-06-01"
    embedding_deployment: str = "text-embedding-3-large"
    chat_deployment: str = "gpt-4o"
    embedding_dimensions: int = 1536


class AwsConfig(BaseModel):
    region: str = "us-east-1"
    opensearch_endpoint: str = ""      # https://<id>.<region>.aoss.amazonaws.com
    opensearch_index: str = "knowledge-keeper"
    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    chat_model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0"
    embedding_dimensions: int = 1024
    s3_bucket: str = ""                # optional: mirror ingested corpus to S3


class AnthropicConfig(BaseModel):
    api_key: str = ""                  # falls back to ANTHROPIC_API_KEY env var
    model: str = "claude-sonnet-4-6"


class GeminiConfig(BaseModel):
    api_key: str = ""                  # falls back to GEMINI_API_KEY env var
    model: str = "gemini-3.6-flash"    # free-tier model


class LlmConfig(BaseModel):
    provider: str = "none"             # none | anthropic | azure_openai | bedrock
    max_tokens: int = 2000
    temperature: float = 0.2


class ChunkingConfig(BaseModel):
    target_tokens: int = 400
    overlap_tokens: int = 60
    min_tokens: int = 40


class AnalysisConfig(BaseModel):
    stale_days: int = 540              # ~18 months
    bus_factor_threshold: int = 1      # topics with <= N authors are risky
    min_mentions_for_gap: int = 3      # mentioned this often but never explained -> gap
    use_llm: bool = True               # if an LLM provider is configured


class Config(BaseModel):
    provider: str = "local"            # local | azure | aws
    data_dir: str = "./kk_data"        # local store + knowledge map output
    llm: LlmConfig = Field(default_factory=LlmConfig)
    azure: AzureConfig = Field(default_factory=AzureConfig)
    aws: AwsConfig = Field(default_factory=AwsConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def provider_data_path(self) -> Path:
        """Per-provider state directory. Each backend gets its own manifest,
        document registry, knowledge map, and (for local) vector store —
        so switching providers never poisons another backend's state."""
        p = self.data_path / self.provider
        p.mkdir(parents=True, exist_ok=True)
        return p


_ACTIVE_PROVIDER_FILE = ".active_provider"


def get_active_provider(data_dir: str = "./kk_data") -> Optional[str]:
    f = Path(data_dir) / _ACTIVE_PROVIDER_FILE
    if f.exists():
        value = f.read_text().strip()
        if value in ("local", "azure", "aws"):
            return value
    return None


def set_active_provider(provider: str, data_dir: str = "./kk_data") -> None:
    if provider not in ("local", "azure", "aws"):
        raise ValueError(f"Unknown provider: {provider}")
    p = Path(data_dir)
    p.mkdir(parents=True, exist_ok=True)
    (p / _ACTIVE_PROVIDER_FILE).write_text(provider)


def _apply_env_overrides(data: dict) -> dict:
    """KK_AZURE__SEARCH_API_KEY=... -> data['azure']['search_api_key']"""
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().split("__")
        node = data
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value
    return data


def load_config(path: Optional[str] = None, provider: Optional[str] = None) -> Config:
    """Load config with provider precedence:
    1. explicit `provider` argument (CLI --provider flag)
    2. `kk use <provider>` persisted choice
    3. KK_PROVIDER env var / config.yaml value (via normal loading)
    """
    data: dict = {}
    candidate = Path(path) if path else Path("config.yaml")
    if candidate.exists():
        data = yaml.safe_load(candidate.read_text()) or {}
    data = _apply_env_overrides(data)
    cfg = Config(**data)

    if provider:
        cfg.provider = provider
    elif ENV_PREFIX + "PROVIDER" not in os.environ:
        persisted = get_active_provider(cfg.data_dir)
        if persisted:
            cfg.provider = persisted
    return cfg
