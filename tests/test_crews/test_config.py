"""Tests for CrewAI configuration module."""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch


def test_disabled_by_default():
    """CREWAI_ENABLED is False when env var is unset."""
    with patch.dict(os.environ, {}, clear=True):
        import jenn_mesh.crews.config as cfg

        mod = importlib.reload(cfg)
        assert mod.CREWAI_ENABLED is False


def test_enabled_when_true():
    """CREWAI_ENABLED is True when set to 'true'."""
    with patch.dict(os.environ, {"CREWAI_ENABLED": "true"}, clear=True):
        import jenn_mesh.crews.config as cfg

        mod = importlib.reload(cfg)
        assert mod.CREWAI_ENABLED is True


def test_enabled_when_one():
    """CREWAI_ENABLED accepts '1' as truthy."""
    with patch.dict(os.environ, {"CREWAI_ENABLED": "1"}, clear=True):
        import jenn_mesh.crews.config as cfg

        mod = importlib.reload(cfg)
        assert mod.CREWAI_ENABLED is True


def test_disabled_when_random_string():
    """CREWAI_ENABLED rejects arbitrary strings."""
    with patch.dict(os.environ, {"CREWAI_ENABLED": "maybe"}, clear=True):
        import jenn_mesh.crews.config as cfg

        mod = importlib.reload(cfg)
        assert mod.CREWAI_ENABLED is False


def test_default_llm_model():
    """Default LLM model is ollama/qwen3:4b."""
    with patch.dict(os.environ, {}, clear=True):
        import jenn_mesh.crews.config as cfg

        mod = importlib.reload(cfg)
        assert mod.CREWAI_LLM_MODEL == "ollama/qwen3:4b"


def test_custom_llm_model():
    """CREWAI_LLM_MODEL respects env override."""
    with patch.dict(os.environ, {"CREWAI_LLM_MODEL": "ollama/llama3:8b"}, clear=True):
        import jenn_mesh.crews.config as cfg

        mod = importlib.reload(cfg)
        assert mod.CREWAI_LLM_MODEL == "ollama/llama3:8b"


def test_verbose_default_false():
    """Verbose is off by default."""
    with patch.dict(os.environ, {}, clear=True):
        import jenn_mesh.crews.config as cfg

        mod = importlib.reload(cfg)
        assert mod.CREWAI_VERBOSE is False
