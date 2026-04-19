"""
Tests for Prompt-as-API feature.
Covers: PromptConfig validation, DB schema migration, proxy routing, portal creation.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from marketplace.prompt_engine import (
    PromptConfig,
    PromptResult,
    PromptEngineError,
    execute_prompt,
    sanitize_input,
    SUPPORTED_MODELS,
)


# --- PromptConfig tests ---

class TestPromptConfig:
    def test_valid_config(self):
        cfg = PromptConfig(
            model="claude-haiku-4-5",
            system_prompt="You are a translator.",
            temperature=0.5,
            max_tokens=512,
        )
        assert cfg.validate() == []

    def test_invalid_model(self):
        cfg = PromptConfig(model="gpt-4", system_prompt="test")
        errors = cfg.validate()
        assert any("Unsupported model" in e for e in errors)

    def test_empty_system_prompt(self):
        cfg = PromptConfig(model="claude-haiku-4-5", system_prompt="")
        errors = cfg.validate()
        assert any("system_prompt is required" in e for e in errors)

    def test_temperature_out_of_range(self):
        cfg = PromptConfig(
            model="claude-haiku-4-5", system_prompt="test", temperature=1.5
        )
        errors = cfg.validate()
        assert any("temperature" in e for e in errors)

    def test_max_tokens_out_of_range(self):
        cfg = PromptConfig(
            model="claude-haiku-4-5", system_prompt="test", max_tokens=10000
        )
        errors = cfg.validate()
        assert any("max_tokens" in e for e in errors)

    def test_from_dict(self):
        data = {
            "model": "claude-sonnet-4-6",
            "system_prompt": "You summarize text.",
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        cfg = PromptConfig.from_dict(data)
        assert cfg.model == "claude-sonnet-4-6"
        assert cfg.system_prompt == "You summarize text."
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 2048

    def test_to_dict_roundtrip(self):
        cfg = PromptConfig(
            model="claude-haiku-4-5",
            system_prompt="test prompt",
            temperature=0.7,
            max_tokens=1024,
        )
        data = cfg.to_dict()
        cfg2 = PromptConfig.from_dict(data)
        assert cfg == cfg2


# --- Input sanitization tests ---

class TestSanitizeInput:
    def test_normal_input(self):
        assert sanitize_input("Hello world") == "Hello world"

    def test_strips_control_chars(self):
        result = sanitize_input("Hello\x00\x01\x02 world")
        assert "\x00" not in result
        assert "Hello world" == result

    def test_collapses_newlines(self):
        result = sanitize_input("a\n\n\n\n\n\nb")
        assert result == "a\n\n\nb"

    def test_strips_whitespace(self):
        assert sanitize_input("  hello  ") == "hello"

    def test_non_string_input(self):
        assert sanitize_input(123) == "123"


# --- execute_prompt tests (mocked) ---

class TestExecutePrompt:
    @patch("marketplace.prompt_engine._get_client")
    def test_successful_execution(self, mock_get_client):
        # Mock Claude API response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Translation: Bonjour")]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        config = PromptConfig(
            model="claude-haiku-4-5",
            system_prompt="You are a French translator.",
        )
        result = execute_prompt(config, "Hello world", provider_api_key="sk-ant-test-key")

        assert result.content == "Translation: Bonjour"
        assert result.input_tokens == 50
        assert result.output_tokens == 10
        assert result.model == "claude-haiku-4-5"
        assert result.latency_ms >= 0

    def test_empty_input_raises(self):
        config = PromptConfig(
            model="claude-haiku-4-5", system_prompt="Test"
        )
        with pytest.raises(PromptEngineError, match="Input text is required"):
            execute_prompt(config, "", provider_api_key="sk-ant-test")

    def test_too_long_input_raises(self):
        config = PromptConfig(
            model="claude-haiku-4-5", system_prompt="Test"
        )
        with pytest.raises(PromptEngineError, match="exceeds maximum length"):
            execute_prompt(config, "x" * 20000, provider_api_key="sk-ant-test")

    def test_invalid_config_raises(self):
        config = PromptConfig(model="bad-model", system_prompt="")
        with pytest.raises(PromptEngineError, match="Invalid prompt config"):
            execute_prompt(config, "test", provider_api_key="sk-ant-test")

    def test_missing_api_key_raises(self):
        config = PromptConfig(
            model="claude-haiku-4-5", system_prompt="Test prompt"
        )
        with pytest.raises(PromptEngineError, match="Provider API key is required"):
            execute_prompt(config, "test input")


# --- DB schema tests ---

class TestDBPromptColumns:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        """Create a temporary DB file (in-memory SQLite creates separate DBs per connection)."""
        from marketplace.db import Database
        self.db = Database(tmp_path / "test.db")

    def test_service_type_column_exists(self):
        with self.db.connect() as conn:
            row = conn.execute(
                "PRAGMA table_info(services)"
            ).fetchall()
            col_names = [r["name"] for r in row]
            assert "service_type" in col_names
            assert "prompt_config" in col_names

    def test_insert_prompt_service(self):
        now = datetime.now(timezone.utc).isoformat()
        svc_id = str(uuid.uuid4())
        prompt_cfg = {
            "model": "claude-haiku-4-5",
            "system_prompt": "You are a translator.",
            "temperature": 0.5,
            "max_tokens": 512,
        }
        service = {
            "id": svc_id,
            "provider_id": "test-provider",
            "name": "Test Translator",
            "description": "Translates text",
            "endpoint": "prompt://internal",
            "price_per_call": 0.005,
            "service_type": "prompt",
            "prompt_config": prompt_cfg,
            "created_at": now,
            "updated_at": now,
        }
        self.db.insert_service(service)

        retrieved = self.db.get_service(svc_id)
        assert retrieved is not None
        assert retrieved["service_type"] == "prompt"
        assert retrieved["prompt_config"]["model"] == "claude-haiku-4-5"
        assert retrieved["prompt_config"]["system_prompt"] == "You are a translator."

    def test_insert_external_service_default_type(self):
        now = datetime.now(timezone.utc).isoformat()
        svc_id = str(uuid.uuid4())
        service = {
            "id": svc_id,
            "provider_id": "test-provider",
            "name": "External API",
            "description": "Some API",
            "endpoint": "https://api.example.com",
            "price_per_call": 0.05,
            "created_at": now,
            "updated_at": now,
        }
        self.db.insert_service(service)

        retrieved = self.db.get_service(svc_id)
        assert retrieved["service_type"] == "external"
        assert retrieved["prompt_config"] == {}


# --- Supported models ---

class TestSupportedModels:
    def test_haiku_in_supported(self):
        assert "claude-haiku-4-5" in SUPPORTED_MODELS

    def test_sonnet_in_supported(self):
        assert "claude-sonnet-4-6" in SUPPORTED_MODELS

    def test_min_price_set(self):
        for model, info in SUPPORTED_MODELS.items():
            assert info["min_price"] > info["base_cost"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
