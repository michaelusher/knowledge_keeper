"""Wire configured providers into concrete components."""
from __future__ import annotations

from typing import Optional

from .config import Config
from .llm import LlmClient, build_llm
from .stores.base import Embedder, VectorStore


def build_store_and_embedder(cfg: Config) -> tuple[VectorStore, Embedder]:
    provider = cfg.provider.lower()
    if provider == "local":
        from .stores.local_store import HashingEmbedder, LocalVectorStore

        return LocalVectorStore(cfg.provider_data_path), HashingEmbedder()
    if provider == "azure":
        from .stores.azure_store import AzureOpenAIEmbedder, AzureSearchStore

        return AzureSearchStore(cfg.azure), AzureOpenAIEmbedder(cfg.azure)
    if provider == "aws":
        from .stores.aws_store import BedrockEmbedder, OpenSearchStore

        return OpenSearchStore(cfg.aws), BedrockEmbedder(cfg.aws)
    raise ValueError(f"Unknown provider: {cfg.provider}")


def build_all(cfg: Config) -> tuple[VectorStore, Embedder, Optional[LlmClient]]:
    store, embedder = build_store_and_embedder(cfg)
    llm = build_llm(cfg)
    return store, embedder, llm
