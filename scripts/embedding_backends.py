"""Embedding backends for semantic similarity evaluation.

This module provides an abstract interface for embedding generation with multiple
backend implementations. The default backend is fastembed (lightweight, ONNX-based),
but the architecture supports other backends like Ollama, OpenAI, and llama.cpp.

Configuration:
    SKILL_EVOLUTION_EMBEDDING_BACKEND: Backend to use (default: fastembed)
        Options: fastembed, ollama, openai, llama_cpp
    
    Backend-specific configuration:
        SKILL_EVOLUTION_FASTEMBED_MODEL: Model name for fastembed (default: BAAI/bge-small-en-v1.5)
        SKILL_EVOLUTION_OLLAMA_BASE_URL: Ollama API URL (default: http://localhost:11434)
        SKILL_EVOLUTION_OLLAMA_MODEL: Ollama embedding model (default: nomic-embed-text)
        SKILL_EVOLUTION_OPENAI_MODEL: OpenAI embedding model (default: text-embedding-3-small)
        SKILL_EVOLUTION_LLAMACPP_BASE_URL: llama.cpp API URL (default: http://localhost:8080)
"""

import os
from abc import ABC, abstractmethod
from typing import List


class EmbeddingBackend(ABC):
    """Abstract base class for embedding backends."""
    
    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts.
        
        Args:
            texts: List of text strings to embed
            
        Returns:
            List of embedding vectors (each vector is a list of floats)
            
        Raises:
            ImportError: If required dependencies are not installed
            RuntimeError: If embedding generation fails
        """
        pass


class FastEmbedBackend(EmbeddingBackend):
    """FastEmbed backend using ONNX Runtime.
    
    Lightweight (~50MB), fast, no PyTorch dependency.
    Models are cached in ~/.cache/fastembed/
    
    Requires: pip install fastembed
    """
    
    def __init__(self, model_name: str = None):
        """Initialize FastEmbed backend.
        
        Args:
            model_name: Name of the embedding model (default: BAAI/bge-small-en-v1.5)
        """
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise ImportError(
                "fastembed not installed. Install with: "
                "pip install skill-evolution[embeddings] "
                "or: pip install fastembed"
            )
        
        if model_name is None:
            model_name = os.getenv(
                "SKILL_EVOLUTION_FASTEMBED_MODEL",
                "BAAI/bge-small-en-v1.5"
            )
        
        self.model = TextEmbedding(model_name=model_name)
    
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using fastembed.
        
        Args:
            texts: List of text strings to embed
            
        Returns:
            List of embedding vectors
        """
        # fastembed returns an iterator, convert to list
        return list(self.model.embed(texts))


class OllamaBackend(EmbeddingBackend):
    """Ollama backend using REST API.
    
    Requires Ollama server running with embedding model.
    Example: ollama pull nomic-embed-text
    
    Note: This is a stub implementation. To use Ollama, implement the REST API calls.
    """
    
    def __init__(self, base_url: str = None, model: str = None):
        """Initialize Ollama backend.
        
        Args:
            base_url: Ollama API base URL (default: http://localhost:11434)
            model: Embedding model name (default: nomic-embed-text)
        """
        self.base_url = base_url or os.getenv(
            "SKILL_EVOLUTION_OLLAMA_BASE_URL",
            "http://localhost:11434"
        )
        self.model = model or os.getenv(
            "SKILL_EVOLUTION_OLLAMA_MODEL",
            "nomic-embed-text"
        )
    
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using Ollama API.
        
        Note: This is a stub. Implement REST API calls to Ollama.
        
        Example implementation:
            import requests
            embeddings = []
            for text in texts:
                response = requests.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text}
                )
                response.raise_for_status()
                embeddings.append(response.json()["embedding"])
            return embeddings
        """
        raise NotImplementedError(
            "OllamaBackend is not yet implemented. "
            "See docstring for implementation example."
        )


class OpenAIBackend(EmbeddingBackend):
    """OpenAI backend using official API.
    
    Requires OPENAI_API_KEY environment variable.
    Cost: ~$0.02 per 1M tokens (text-embedding-3-small)
    
    Note: This is a stub implementation. To use OpenAI, implement the API calls.
    """
    
    def __init__(self, model: str = None):
        """Initialize OpenAI backend.
        
        Args:
            model: Embedding model name (default: text-embedding-3-small)
        """
        self.model = model or os.getenv(
            "SKILL_EVOLUTION_OPENAI_MODEL",
            "text-embedding-3-small"
        )
    
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using OpenAI API.
        
        Note: This is a stub. Implement OpenAI API calls.
        
        Example implementation:
            from openai import OpenAI
            client = OpenAI()
            response = client.embeddings.create(
                model=self.model,
                input=texts
            )
            return [item.embedding for item in response.data]
        """
        raise NotImplementedError(
            "OpenAIBackend is not yet implemented. "
            "See docstring for implementation example."
        )


class LlamaCppBackend(EmbeddingBackend):
    """llama.cpp backend using OpenAI-compatible API.
    
    Requires llama.cpp server running with GGUF embedding model.
    Example: ./llama-server -m model.gguf --port 8080
    
    Note: This is a stub implementation. To use llama.cpp, implement the API calls.
    """
    
    def __init__(self, base_url: str = None):
        """Initialize llama.cpp backend.
        
        Args:
            base_url: llama.cpp API base URL (default: http://localhost:8080)
        """
        self.base_url = base_url or os.getenv(
            "SKILL_EVOLUTION_LLAMACPP_BASE_URL",
            "http://localhost:8080"
        )
    
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using llama.cpp API.
        
        Note: This is a stub. Implement REST API calls to llama.cpp.
        
        Example implementation:
            import requests
            response = requests.post(
                f"{self.base_url}/v1/embeddings",
                json={"input": texts}
            )
            response.raise_for_status()
            return [item["embedding"] for item in response.json()["data"]]
        """
        raise NotImplementedError(
            "LlamaCppBackend is not yet implemented. "
            "See docstring for implementation example."
        )


def get_embedding_backend(backend_name: str = None) -> EmbeddingBackend:
    """Factory function to get the configured embedding backend.
    
    Args:
        backend_name: Name of the backend to use. If None, reads from
                     SKILL_EVOLUTION_EMBEDDING_BACKEND env var (default: fastembed)
    
    Returns:
        Initialized embedding backend instance
        
    Raises:
        ValueError: If backend_name is not recognized
        ImportError: If required dependencies are not installed
    """
    if backend_name is None:
        backend_name = os.getenv("SKILL_EVOLUTION_EMBEDDING_BACKEND", "fastembed")
    
    backend_name = backend_name.lower()
    
    if backend_name == "fastembed":
        return FastEmbedBackend()
    elif backend_name == "ollama":
        return OllamaBackend()
    elif backend_name == "openai":
        return OpenAIBackend()
    elif backend_name == "llama_cpp" or backend_name == "llamacpp":
        return LlamaCppBackend()
    else:
        raise ValueError(
            f"Unknown embedding backend: {backend_name}. "
            f"Available backends: fastembed, ollama, openai, llama_cpp"
        )
