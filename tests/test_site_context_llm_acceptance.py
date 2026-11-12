"""Independent observation of the actual extraction/conversation model-message boundary."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import socket

import pytest
from pydantic import SecretStr

import core
import llm_agent
from config import AppConfig
from customer_context import (
    ApplicationReference, ContextCapabilityCodec, CurrentApplicationFields,
    CurrentApplicationSnapshot, CurrentContextService, ServerApplicationAdapter,
)
from state import init_state


ADDRESS = "PRIVATEADDRESSVECTORQZ"
FULL_NAME = "PRIVATENAMEVECTORQZ"
PHONE = "70000000123"


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Model acceptance uses a message spy, never a real model or CRM")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def trusted_state():
    def clock():
        return 1800000000.0
    reference = ApplicationReference(application_id="server-case-Z", subject_id="server-person-Z", revision="v7")
    fields = CurrentApplicationFields(full_name=FULL_NAME, registration_address=ADDRESS,
        living_address=ADDRESS, region=ADDRESS, birth_date="01.01.1990", debts_total=400000)
    source_snapshot = CurrentApplicationSnapshot(reference=reference, phone=SecretStr(PHONE), fields=fields,
                                               observed_at=datetime.fromtimestamp(clock(), timezone.utc))
    codec = ContextCapabilityCodec(b"independent-model-boundary-test-key-32", clock=clock)
    source = ServerApplicationAdapter(lambda _: source_snapshot)
    authorized = CurrentContextService(codec, source, enabled=True, clock=clock).resolve(
        codec.issue(reference, session_id="chat-Z"), session_id="chat-Z")
    state = init_state(authorized.to_anketa())
    state["customer_context"] = authorized.safe_context_state()
    return state


@pytest.mark.parametrize("boundary", ["conversation", "extraction"])
def test_authenticated_free_text_never_reaches_model_even_when_global_pii_flag_is_false(monkeypatch, boundary):
    state = trusted_state()
    before = deepcopy(state)
    # No street/house keywords: passing this test cannot rely on an address regex.
    payload = core.build_runtime_payload(state, f"Продолжим. Мой телефон {PHONE}")
    assert payload["facts"].get("debts", {}).get("total") == 400000, "Safe known facts must remain useful"
    captured = []

    def stream_spy(kind, messages, config, usage_collector=None):
        captured.append(messages)
        yield "Понял."

    def extraction_spy(config, model, messages, usage_collector, attempt):
        captured.append(messages)
        return {"fact_updates": [], "internal_summary": ""}

    config = AppConfig(openrouter_api_key="synthetic-not-used", enable_pii_masking=False)
    if boundary == "conversation":
        monkeypatch.setattr(llm_agent, "_stream", stream_spy)
        assert list(llm_agent.stream_conversation(payload, config)) == ["Понял."]
    else:
        monkeypatch.setattr(llm_agent, "_extract_attempt", extraction_spy)
        llm_agent.extract_facts(payload, "Понял.", config)
    serialized = json.dumps(captured, ensure_ascii=False)
    assert len(captured) == 1
    for private in (ADDRESS, FULL_NAME, PHONE, "server-case-Z", "server-person-Z", state["customer_context"]["binding_digest"]):
        assert private not in serialized, "Private authoritative source data crossed the model boundary"
    assert "400000" in serialized
    assert state == before, "Model-safe projection must not corrupt backend facts used to resume the session"
