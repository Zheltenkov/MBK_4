"""Independent HTTP acceptance for changing the scenario inside an existing chat."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sse_starlette.sse import AppStatus

import core
import debt_policy
import entry_routing
import main
import session_store
from config import AppConfig
from interpretation import FactCandidate, FactCandidateExtraction
from session_store import Session, SessionStore


@pytest.fixture
def existing(monkeypatch, tmp_path):
    for key, value in {"MBK_PUBLIC_CHAT":"1", "MBK_AUTH_USERNAME":"synthetic-existing",
        "MBK_AUTH_PASSWORD":"synthetic-existing-password", "MBK_AUTH_COOKIE_SECURE":"0",
        "MBK_CRM_LEAD_INTAKE_ENABLED":"0", "MBK_ROUTER_MOCK":"1", "MBK_DEBT_POLICY_ENABLED":"1"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(AppStatus,"should_exit_event",None)
    monkeypatch.setattr(session_store,"SESSIONS_DIR",tmp_path)
    main.reset_session_rate_limit()
    store = SessionStore(max_extract_workers=1)
    configs = [AppConfig(openrouter_api_key="synthetic-unused",debt_policy_enabled=True)]
    model_calls = []
    def normal_reply(payload, *args, **kwargs):
        model_calls.append(True)
        planned = re.search(r"НУЖНО ЗАКРЫТЬ на этом ходу:\s*([^\n]+)",payload.get("turn_instruction", ""))
        yield planned.group(1).rstrip(". ") + "?" if planned else "Как к вам обращаться?"
    def legacy_extract(payload, *args, **kwargs):
        updates = ([{"path":"debts.has_active_credits","value":False}]
            if payload.get("latest_user_message") == "Нет действующих кредитов" else [])
        return {"fact_updates":updates,"internal_summary":""}
    def semantic_extract(payload, *args, **kwargs):
        candidates = ([FactCandidate(path="debts.has_active_credits",value=False,evidence="Нет действующих кредитов")]
            if payload.get("latest_user_message") == "Нет действующих кредитов" else [])
        return FactCandidateExtraction(candidates=candidates)
    def no_delivery(*args, **kwargs):
        pytest.fail("Scenario transition must retain the delivery hold")
    monkeypatch.setattr(main,"store",store)
    monkeypatch.setattr(main,"load_config",lambda:configs[0])
    monkeypatch.setattr(main,"lead_intake_coordinator",None)
    monkeypatch.setattr(main,"crm_dialog_workflow",None)
    monkeypatch.setattr(main,"current_context_service",None)
    monkeypatch.setattr(main,"current_context_lookup",None)
    monkeypatch.setattr(core.llm_agent,"extract_facts",legacy_extract)
    monkeypatch.setattr(core.llm_agent,"extract_fact_candidates",semantic_extract)
    monkeypatch.setattr(core.llm_agent,"stream_conversation",normal_reply)
    monkeypatch.setattr(core.geo,"enrich_locations",lambda facts,*a,**k:deepcopy(facts))
    monkeypatch.setattr(core.delivery,"submit",no_delivery)
    client = TestClient(main.app)
    yield SimpleNamespace(client=client,store=store,configs=configs,model_calls=model_calls)
    client.close()
    store.executor.shutdown(wait=True)


def session(env, sid):
    value = asyncio.run(env.store.get(sid))
    assert value is not None
    return value


def sse(env, sid, text):
    AppStatus.should_exit_event = None
    response = env.client.get(f"/api/session/{sid}/stream",params={"message":text})
    assert response.status_code == 200 and "event: error" not in response.text,response.text
    assert "set-cookie" not in response.headers
    return response


def configure(env, mode, entry_enabled):
    env.configs[0] = replace(env.configs[0],fact_interpretation_mode=mode,entry_routing_enabled=entry_enabled)


def normal_chat(env, *, cursor="client.age", known_debt=True, known_region=True, region_name="Москва"):
    anketa = {"full_name":"Антон", "phone":"+70000000531", "age":37,
              "desired_amount":1000000, "region":region_name}
    if known_debt:
        anketa["debts_total"] = 400000
    if not known_region:
        anketa.pop("region")
    for path, key in (("client.name","full_name"),("client.age","age"),
                      ("request.amount","desired_amount"),("client.region_class","region")):
        if cursor == path:
            anketa.pop(key,None)
    response = env.client.post("/api/session",json={"anketa":anketa})
    assert response.status_code == 200
    sid = response.json()["session_id"]
    assert session(env,sid).state["facts"]["client"]["phone"] == "+70000000531"
    assert debt_policy.POLICY_KEY not in session(env,sid).state
    assert env.client.cookies.get("mbk_session_grant")
    sse(env,sid,"Нужен кредит")
    assert session(env,sid).state["script"]["asked"] == (
        ["debts.has_active_credits"] if cursor == "none" else [cursor])
    return sid


def assert_same_identity(env, sid, cookie, before_history):
    current = session(env,sid)
    assert len(env.store.all_sessions()) == 1
    assert current.id == sid and env.client.cookies.get("mbk_session_grant") == cookie
    assert env.client.cookies.get("mbk_debt_session_grant") is None
    assert current.state["chat_history"][:len(before_history)] == before_history
    assert debt_policy.debt_delivery_held(current.state) and entry_routing.lead_blocked(current.state)
    assert current.state["crm_lead"]["status"] == "not_sent"


@pytest.mark.parametrize("mode",["legacy","semantic"])
@pytest.mark.parametrize("entry_enabled",[False,True])
@pytest.mark.parametrize("cursor",["client.name","client.age","request.amount","client.region_class"])
def test_actual_normal_chat_changes_policy_with_same_sid_history_contacts_and_cookie(existing,mode,entry_enabled,cursor):
    configure(existing,mode,entry_enabled)
    sid = normal_chat(existing,cursor=cursor)
    original = session(existing,sid)
    before_history = deepcopy(original.state["chat_history"])
    before_facts = deepcopy(original.state["facts"])
    cookie = existing.client.cookies.get("mbk_session_grant")
    normal_model_calls = len(existing.model_calls)
    response = sse(existing,sid,"Хочу списать свои долги.")
    updated = session(existing,sid)
    assert updated.state[debt_policy.POLICY_KEY]["mode"] == "debt"
    assert updated.state["facts"]["client"]["phone"] == before_facts["client"]["phone"]
    assert updated.state["facts"]["debts"]["total"] == 400000
    assert updated.state["plan"]["fields"] == (
        ["client.name"] if cursor == "client.name" else ["client.region_class"] if cursor == "client.region_class" else [])
    assert not {"client.age","request.amount","client.phone","assets.has_vehicle"}.intersection(updated.state["plan"]["fields"])
    assert len(existing.model_calls) == normal_model_calls
    assert "На какой номер" not in response.text
    assert_same_identity(existing,sid,cookie,before_history)
    if cursor == "client.name":
        assert updated.state["facts"].get("client",{}).get("name") is None
        sse(existing,sid,"Антон")
    elif cursor == "client.region_class":
        sse(existing,sid,"Москва")
    assert session(existing,sid).state["dialog_phase"] == "collected_held"
    assert_same_identity(existing,sid,cookie,before_history)


@pytest.mark.parametrize("mode",["legacy","semantic"])
@pytest.mark.parametrize("entry_enabled",[False,True])
def test_legacy_derived_no_active_loan_zero_does_not_finish_new_debt_scenario(existing,mode,entry_enabled):
    configure(existing,mode,entry_enabled)
    sid = normal_chat(existing,cursor="none",known_debt=False)
    sse(existing,sid,"Нет действующих кредитов")
    previous = session(existing,sid)
    assert previous.state["facts"]["debts"]["has_active_credits"] is False
    assert previous.state["facts"]["debts"]["total"] == 0
    assert previous.state["facts"]["debts"]["credit_history"] == "no_overdue"
    sse(existing,sid,"Хочу списать свои долги.")
    current = session(existing,sid)
    assert current.state["plan"]["fields"] == ["debts.total"]
    assert current.state["dialog_phase"] != "below_local_floor_held"
    sse(existing,sid,"400000 рублей")
    assert current.state["facts"]["debts"]["total"] == 400000
    assert current.state["dialog_phase"] == "collected_held"


@pytest.mark.parametrize("mode",["legacy","semantic"])
@pytest.mark.parametrize("entry_enabled",[False,True])
def test_clear_volunteered_region_and_debt_on_the_switch_are_not_asked_again(existing,mode,entry_enabled):
    configure(existing,mode,entry_enabled)
    sid = normal_chat(existing,cursor="client.name",known_debt=False,known_region=False)
    sse(existing,sid,"Хочу списать свои долги. Живу в Москве. Общий долг400000 рублей.")
    current = session(existing,sid)
    assert current.state["facts"]["client"].get("region_class") == "moscow_mo"
    assert current.state["facts"]["debts"]["total"] == 400000
    assert current.state["facts"]["client"].get("name") is None
    assert current.state["plan"]["fields"] == ["client.name"]
    sse(existing,sid,"Антон")
    assert current.state["dialog_phase"] == "collected_held"


@pytest.mark.parametrize("mode",["legacy","semantic"])
def test_old_region_cursor_does_not_downgrade_a_previously_known_region_on_intent_change(existing,mode):
    configure(existing,mode,True)
    sid = normal_chat(existing,cursor="client.age")
    current = session(existing,sid)
    current.state.setdefault("script",{})["asked"] = ["client.region_class"]
    current.state.setdefault("fact_statuses",{})["client.region_class"] = "known"
    sse(existing,sid,"Хочу списать свои долги.")
    assert current.state["facts"]["client"].get("region_class") == "moscow_mo"
    assert current.state["fact_statuses"].get("client.region_class") != "needs_confirmation"
    assert current.state["plan"]["fields"] == []
    assert current.state["dialog_phase"] == "collected_held"


@pytest.mark.parametrize("mode",["legacy","semantic"])
def test_new_debt_question_budget_is_independent_of_old_normal_attempts(existing,mode):
    configure(existing,mode,True)
    sid = normal_chat(existing,cursor="client.age",known_debt=False)
    current = session(existing,sid)
    current.state.setdefault("script",{})["attempts"] = {"debts.total":3,"client.region_class":3,"client.phone":3}
    sse(existing,sid,"Хочу списать свои долги.")
    assert current.state["plan"]["fields"] == ["debts.total"]
    sse(existing,sid,"Не знаю")
    assert current.state["plan"]["fields"] == ["debts.total"]
    sse(existing,sid,"Не знаю")
    assert current.state["plan"]["fields"] == ["debts.total"]
    sse(existing,sid,"Не знаю")
    assert current.state["plan"]["fields"] == [] and current.state["dialog_phase"] == "incomplete_held"
    assert current.state["facts"].get("debts",{}).get("total") is None


@pytest.mark.parametrize("mode",["legacy","semantic"])
def test_json_state_restore_and_flag_off_continue_same_owned_chat(existing,mode):
    configure(existing,mode,False)
    sid = normal_chat(existing,cursor="client.age",known_debt=False)
    cookie = existing.client.cookies.get("mbk_session_grant")
    sse(existing,sid,"Хочу списать свои долги.")
    original = session(existing,sid)
    record = original.storage_record()
    restored = Session.from_record(replace(record,state=json.loads(json.dumps(record.state))))
    existing.store._sessions[sid] = restored
    existing.configs[0] = replace(existing.configs[0],debt_policy_enabled=False)
    response = existing.client.get(f"/api/session/{sid}")
    assert response.status_code == 200 and response.json()["session_id"] == sid
    assert response.json()["state"]["questionnaire_policy"]["mode"] == "debt"
    sse(existing,sid,"400000 рублей")
    assert restored.state["dialog_phase"] == "collected_held"
    assert restored.state["facts"]["client"]["phone"] == "+70000000531"
    assert existing.client.cookies.get("mbk_session_grant") == cookie
    assert len(existing.store.all_sessions()) == 1


@pytest.mark.parametrize("mode",["legacy","semantic"])
def test_new_explicit_debt_request_can_continue_after_a_terminal_normal_phase(existing,mode):
    configure(existing,mode,True)
    sid = normal_chat(existing,cursor="client.age")
    current = session(existing,sid)
    before = deepcopy(current.state["chat_history"])
    current.state["dialog_phase"] = "survey_completed"
    sse(existing,sid,"Хочу списать свои долги.")
    assert current.state["dialog_phase"] == "collected_held"
    assert current.state["chat_history"][:len(before)] == before
    assert current.state["chat_history"][-2]["content"] == "Хочу списать свои долги."
    assert debt_policy.debt_delivery_held(current.state)


@pytest.mark.parametrize("mode",["legacy","semantic"])
@pytest.mark.parametrize("location_text",["Я не из Москвы.","Мой брат живёт в Москве.",
    "Живу не в Москве, а в Казани.","Кредит был в Банке Москвы."])
def test_other_negated_or_bank_location_does_not_replace_known_own_region(existing,mode,location_text):
    configure(existing,mode,True)
    sid = normal_chat(existing,cursor="client.age",region_name="Казань")
    assert session(existing,sid).state["facts"]["client"]["region_class"] == "rf_other"
    sse(existing,sid,"Хочу списать свои долги. " + location_text)
    current = session(existing,sid)
    assert current.state[debt_policy.POLICY_KEY]["mode"] == "debt"
    assert current.state["facts"]["client"]["region_class"] == "rf_other"
    assert current.state["facts"]["client"]["phone"] == "+70000000531"
    assert debt_policy.debt_delivery_held(current.state)


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("provenance,needs_confirmation", [
    ("confirmed", False), ("form", False), ("explicit_history", False),
    ("mislabelled_no_active", True), ("system_derived_without_active_flag", True),
])
def test_existing_zero_debt_requires_evidence_but_preserves_an_explicit_known_zero(
    existing, mode, provenance, needs_confirmation,
):
    """The first migration distinguishes a known zero from a legacy inference."""
    configure(existing, mode, True)
    sid = normal_chat(existing, cursor="client.age", known_debt=False)
    current = session(existing, sid)
    cookie = existing.client.cookies.get("mbk_session_grant")
    current.state["facts"].setdefault("debts", {})["total"] = 0
    current.state["facts"]["debts"]["has_active_credits"] = False
    current.state.setdefault("fact_statuses", {})["debts.total"] = "known"
    current.state["fact_events"] = []
    if provenance == "confirmed":
        current.state["fact_statuses"]["debts.total"] = "confirmed"
    elif provenance == "form":
        current.state["fact_events"] = [
            {"path": "debts.total", "value": 0, "status": "known", "source": "form"},
        ]
    elif provenance == "explicit_history":
        current.state["chat_history"].append({"role": "user", "content": "Долгов нет"})
    elif provenance == "mislabelled_no_active":
        current.state["fact_events"] = [
            {"path": "debts.total", "value": 0, "status": "known", "source": "user_explicit",
             "evidence": "Нет действующих кредитов"},
        ]
    else:
        current.state["facts"]["debts"].pop("has_active_credits")
        current.state["fact_events"] = [
            {"path": "debts.total", "value": 0, "status": "known", "source": "system_derived"},
        ]
    history = deepcopy(current.state["chat_history"])
    sse(existing, sid, "Хочу списать свои долги.")
    assert current.state["facts"]["debts"]["total"] == 0
    assert current.state["plan"]["fields"] == (["debts.total"] if needs_confirmation else [])
    assert current.state["dialog_phase"] == (
        "collecting_debt_context" if needs_confirmation else "below_local_floor_held"
    )
    assert_same_identity(existing, sid, cookie, history)

