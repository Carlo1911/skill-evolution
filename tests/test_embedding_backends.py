"""Tests for embedding backends."""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock


def test_fastembed_backend_import_error():
    """Test that FastEmbedBackend raises ImportError when fastembed is not installed."""
    with patch.dict('sys.modules', {'fastembed': None}):
        # Force reimport
        import importlib
        import embedding_backends
        importlib.reload(embedding_backends)
        
        from embedding_backends import FastEmbedBackend
        
        with pytest.raises(ImportError, match="fastembed not installed"):
            FastEmbedBackend()


def test_fastembed_backend_initialization():
    """Test FastEmbedBackend initialization with default model."""
    pytest.importorskip("fastembed", reason="fastembed not installed; pip install -e .[embeddings]")
    with patch('fastembed.TextEmbedding') as mock_text_embedding:
        from embedding_backends import FastEmbedBackend
        
        backend = FastEmbedBackend()
        
        # Should use default model
        mock_text_embedding.assert_called_once_with(model_name="BAAI/bge-small-en-v1.5")


def test_fastembed_backend_custom_model():
    """Test FastEmbedBackend initialization with custom model."""
    pytest.importorskip("fastembed", reason="fastembed not installed; pip install -e .[embeddings]")
    with patch('fastembed.TextEmbedding') as mock_text_embedding:
        from embedding_backends import FastEmbedBackend
        
        backend = FastEmbedBackend(model_name="custom/model")
        
        mock_text_embedding.assert_called_once_with(model_name="custom/model")


def test_fastembed_backend_env_var_model():
    """Test FastEmbedBackend uses environment variable for model."""
    pytest.importorskip("fastembed", reason="fastembed not installed; pip install -e .[embeddings]")
    with patch.dict(os.environ, {"SKILL_EVOLUTION_FASTEMBED_MODEL": "env/model"}):
        with patch('fastembed.TextEmbedding') as mock_text_embedding:
            from embedding_backends import FastEmbedBackend
            
            backend = FastEmbedBackend()
            
            mock_text_embedding.assert_called_once_with(model_name="env/model")


def test_fastembed_backend_embed():
    """Test FastEmbedBackend embed method."""
    pytest.importorskip("fastembed", reason="fastembed not installed; pip install -e .[embeddings]")
    with patch('fastembed.TextEmbedding') as mock_text_embedding:
        mock_model = Mock()
        mock_model.embed.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_text_embedding.return_value = mock_model
        
        from embedding_backends import FastEmbedBackend
        
        backend = FastEmbedBackend()
        result = backend.embed(["text1", "text2"])
        
        mock_model.embed.assert_called_once_with(["text1", "text2"])
        assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


def test_ollama_backend_not_implemented():
    """Test that OllamaBackend raises NotImplementedError."""
    from embedding_backends import OllamaBackend
    
    backend = OllamaBackend()
    
    with pytest.raises(NotImplementedError, match="OllamaBackend is not yet implemented"):
        backend.embed(["text"])


def test_openai_backend_not_implemented():
    """Test that OpenAIBackend raises NotImplementedError."""
    from embedding_backends import OpenAIBackend
    
    backend = OpenAIBackend()
    
    with pytest.raises(NotImplementedError, match="OpenAIBackend is not yet implemented"):
        backend.embed(["text"])


def test_llama_cpp_backend_not_implemented():
    """Test that LlamaCppBackend raises NotImplementedError."""
    from embedding_backends import LlamaCppBackend
    
    backend = LlamaCppBackend()
    
    with pytest.raises(NotImplementedError, match="LlamaCppBackend is not yet implemented"):
        backend.embed(["text"])


def test_get_embedding_backend_default():
    """Test get_embedding_backend returns fastembed by default."""
    pytest.importorskip("fastembed", reason="fastembed not installed; pip install -e .[embeddings]")
    mock_backend = Mock()

    with patch('embedding_backends.FastEmbedBackend', return_value=mock_backend):
        from embedding_backends import get_embedding_backend
        
        backend = get_embedding_backend()
        
        assert backend == mock_backend


def test_get_embedding_backend_fastembed():
    """Test get_embedding_backend returns fastembed when specified."""
    pytest.importorskip("fastembed", reason="fastembed not installed; pip install -e .[embeddings]")
    mock_backend = Mock()

    with patch('embedding_backends.FastEmbedBackend', return_value=mock_backend):
        from embedding_backends import get_embedding_backend
        
        backend = get_embedding_backend("fastembed")
        
        assert backend == mock_backend


def test_get_embedding_backend_ollama():
    """Test get_embedding_backend returns OllamaBackend when specified."""
    from embedding_backends import get_embedding_backend, OllamaBackend
    
    backend = get_embedding_backend("ollama")
    
    assert isinstance(backend, OllamaBackend)


def test_get_embedding_backend_openai():
    """Test get_embedding_backend returns OpenAIBackend when specified."""
    from embedding_backends import get_embedding_backend, OpenAIBackend
    
    backend = get_embedding_backend("openai")
    
    assert isinstance(backend, OpenAIBackend)


def test_get_embedding_backend_llama_cpp():
    """Test get_embedding_backend returns LlamaCppBackend when specified."""
    from embedding_backends import get_embedding_backend, LlamaCppBackend
    
    backend = get_embedding_backend("llama_cpp")
    
    assert isinstance(backend, LlamaCppBackend)


def test_get_embedding_backend_llamacpp():
    """Test get_embedding_backend accepts 'llamacpp' as alias."""
    from embedding_backends import get_embedding_backend, LlamaCppBackend
    
    backend = get_embedding_backend("llamacpp")
    
    assert isinstance(backend, LlamaCppBackend)


def test_get_embedding_backend_env_var():
    """Test get_embedding_backend uses environment variable."""
    with patch.dict(os.environ, {"SKILL_EVOLUTION_EMBEDDING_BACKEND": "ollama"}):
        from embedding_backends import get_embedding_backend, OllamaBackend
        
        backend = get_embedding_backend()
        
        assert isinstance(backend, OllamaBackend)


def test_get_embedding_backend_unknown():
    """Test get_embedding_backend raises ValueError for unknown backend."""
    from embedding_backends import get_embedding_backend
    
    with pytest.raises(ValueError, match="Unknown embedding backend"):
        get_embedding_backend("unknown_backend")


def test_get_embedding_backend_case_insensitive():
    """Test get_embedding_backend is case insensitive."""
    pytest.importorskip("fastembed", reason="fastembed not installed; pip install -e .[embeddings]")
    mock_backend = Mock()

    with patch('embedding_backends.FastEmbedBackend', return_value=mock_backend):
        from embedding_backends import get_embedding_backend
        
        backend1 = get_embedding_backend("FASTEMBED")
        backend2 = get_embedding_backend("FastEmbed")
        backend3 = get_embedding_backend("fastembed")
        
        assert backend1 == mock_backend
        assert backend2 == mock_backend
        assert backend3 == mock_backend
