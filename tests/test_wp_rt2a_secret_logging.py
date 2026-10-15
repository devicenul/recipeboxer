"""Tests verifying that httpx and google-genai loggers never leak secret keys (WP-RT2a Fix 2)."""

import io
import logging
from typing import Any
import pytest
import httpx
from google import genai
from google.genai import types

from recipeboxer.ai.client import GenAIClient, LLMRequest, LLMStage, TextPart


@pytest.mark.asyncio
async def test_secret_key_never_leaks_in_captured_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Runs a GenAIClient request through a stub transport and asserts the key never appears in captured logs."""
    secret_key = "AIzaSyD_TEST_SECRET_KEY_NEVER_LEAK_12345"

    # Enforce WARNING level on HTTP/SDK loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Extracted recipe text."}],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 15,
                    "candidatesTokenCount": 25,
                    "totalTokenCount": 40,
                },
            },
        )

    transport = httpx.MockTransport(mock_handler)
    stub_client = httpx.AsyncClient(transport=transport)

    sdk = genai.Client(
        api_key=secret_key,
        http_options=types.HttpOptions(httpx_async_client=stub_client),
    )
    client = GenAIClient(genai_client=sdk)

    with caplog.at_level(logging.DEBUG):
        req = LLMRequest(
            stage=LLMStage.TRANSCRIBE,
            model="gemini-2.5-flash",
            parts=[TextPart(text="Transcribe this recipe card.")],
        )
        resp = await client.generate(req)

    assert resp.raw_text == "Extracted recipe text."
    assert secret_key not in caplog.text
    # Double check all log records
    for record in caplog.records:
        assert secret_key not in record.getMessage()
