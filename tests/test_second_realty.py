"""Второй объект недвижимости: ветвитель диалога, а не часть квалификации.

Андрей 12.08.2026: `realty-qualification` отвечает на один вопрос — стоит ли
спрашивать клиента про второй объект. На категорию из `first-qualification` он
не влияет вообще.

Смысл конверсионный. У клиента дом в деревне — под залог не годится, — но может
быть ещё квартира в Москве. Спросили: лид уходит первой категорией, самой
дорогой. Не спросили: проваливается в потреб или БФЛ.

Отправка второго объекта в квалификацию была написана давно
(`_qualifying_realty_root` отдаёт ему приоритет), но полей в матрице не было, и
код не срабатывал ни разу.
"""
from __future__ import annotations

from copy import deepcopy

import analytics
from analytics import AnalyticsRecorder, MemorySink
from crm_dialog_workflow import CrmDialogWorkflow
from delivery.crm import CrmApiError
from delivery.crm_contracts import RealtyQualificationResponse
from delivery.crm_submission_gate import AllowAllSubmissionGate
from routing import get_matrix, plan as build_plan
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


FIRST_OBJECT = {
    "client": {"name": "Олег", "age": 46, "region_class": "moscow_mo"},
    "request": {"amount": 900_000},
    "debts": {"has_active_credits": False, "total": 0},
    "employment": {"type": "hired", "income": 110_000},
    "assets": {
        "has_realty": True,
        "realty": {
            "kind": "house",
            # Город текстом заполняет DaData в рантайме: CRM ждёт название, а не
            # наш внутренний класс региона.
            "city": "Воронеж",
            "region_class": "rf_other",
            "ownership_form": "private",
            "owners": "client_only",
        },
    },
}


class FakeRealtyClient:
    def __init__(self, *, is_first_category: bool = True, fails: bool = False):
        self.is_first_category = is_first_category
        self.fails = fails
        self.calls: list[object] = []

    def realty_qualification(self, payload) -> RealtyQualificationResponse:
        self.calls.append(payload)
        if self.fails:
            raise CrmApiError("CRM недоступна", retryable=True)
        return RealtyQualificationResponse.model_validate(
            {"success": True, "result": {"is_first_category": self.is_first_category}}
        )


def _session(facts: dict) -> Session:
    state = init_state()
    # Копия обязательна: проверка пишет needs_second прямо в факты, и общий на
    # весь модуль словарь после первого же теста считался бы уже проверенным.
    state["facts"] = deepcopy(facts)
    return Session(
        id="abc123", state=state, anketa=None, usage=SessionUsage(session_id="abc123")
    )


def _workflow(client, *, enabled: bool = True) -> CrmDialogWorkflow:
    return CrmDialogWorkflow(
        client,
        None,
        AllowAllSubmissionGate(),
        persist=lambda session: None,
        enabled=True,
        second_realty_enabled=enabled,
    )


def test_disabled_flag_makes_no_call(monkeypatch):
    """Ветку можно не включать: квалификация корректно отработает по одному объекту."""
    client = FakeRealtyClient()
    session = _session(FIRST_OBJECT)

    _workflow(client, enabled=False).check_second_realty(session)

    assert client.calls == []


def test_incomplete_first_object_is_not_checked():
    """Проверять нечего, пока клиент не назвал объект целиком."""
    facts = {**FIRST_OBJECT, "assets": {"has_realty": True, "realty": {"kind": "house"}}}
    client = FakeRealtyClient()

    _workflow(client).check_second_realty(_session(facts))

    assert client.calls == []


def test_qualifying_first_object_closes_the_question():
    """is_first_category=true — второй объект не спрашиваем, анкета короче."""
    client = FakeRealtyClient(is_first_category=True)
    session = _session(FIRST_OBJECT)

    _workflow(client).check_second_realty(session)

    assert len(client.calls) == 1
    assert session.state["facts"]["assets"]["realty"]["needs_second"] is False


def test_failing_first_object_opens_the_question():
    client = FakeRealtyClient(is_first_category=False)
    session = _session(FIRST_OBJECT)

    _workflow(client).check_second_realty(session)

    assert session.state["facts"]["assets"]["realty"]["needs_second"] is True


def test_the_check_runs_only_once():
    """Каждый ход дёргать CRM незачем: ответ по одному объекту не меняется."""
    client = FakeRealtyClient(is_first_category=False)
    session = _session(FIRST_OBJECT)
    workflow = _workflow(client)

    workflow.check_second_realty(session)
    workflow.check_second_realty(session)

    assert len(client.calls) == 1


def test_crm_failure_does_not_lengthen_the_questionnaire():
    """Отказ CRM — не повод задавать клиенту лишние вопросы.

    Fail-closed здесь означает «не спрашивать»: потерять редкого залогового
    клиента дешевле, чем удлинять анкету всем из-за нашей аварии.
    """
    client = FakeRealtyClient(fails=True)
    session = _session(FIRST_OBJECT)

    _workflow(client).check_second_realty(session)

    assert session.state["facts"]["assets"]["realty"]["needs_second"] is False


def test_crm_failure_is_visible_in_analytics():
    """Молчаливый `logger.warning` раньше был единственным следом сбоя.

    Ветвитель по-прежнему не роняет анкету, но факт отказа должен быть виден
    так же, как и у основной квалификации — иначе разбор инцидента снова
    упирается в общий лог, а не в цифру.
    """
    sink = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(sink))
    try:
        client = FakeRealtyClient(fails=True)
        session = _session(FIRST_OBJECT)

        _workflow(client).check_second_realty(session)

        (event,) = [e for e in sink.events if e.event == "crm_call_failed"]
        assert event.session_id == session.id
        assert event.payload == {
            "stage": "realty_qualification",
            "reason": "http_error",
            "retryable": True,
            "error_class": "CrmApiError",
        }
    finally:
        analytics.set_recorder(None)


def test_planner_asks_about_the_second_object_only_when_needed(matrix):
    """Ветка живёт в матрице: планировщик открывает её сам по факту."""
    closed = {**FIRST_OBJECT}
    closed["assets"] = {
        **FIRST_OBJECT["assets"],
        "realty": {**FIRST_OBJECT["assets"]["realty"], "needs_second": False},
        "has_vehicle": False,
    }
    opened = {**FIRST_OBJECT}
    opened["assets"] = {
        **FIRST_OBJECT["assets"],
        "realty": {**FIRST_OBJECT["assets"]["realty"], "needs_second": True},
        "has_vehicle": False,
    }

    assert "assets.has_second_realty" not in build_plan(matrix, closed).field_paths
    assert "assets.has_second_realty" in build_plan(matrix, opened).field_paths


SECOND_OBJECT_FACTS = {
    **FIRST_OBJECT,
    "client": {**FIRST_OBJECT["client"], "phone": "+79991234567"},
    "assets": {
        **FIRST_OBJECT["assets"],
        "has_second_realty": True,
        "realty_2": {
            "kind": "flat",
            "city": "Москва",
            "region_class": "moscow_mo",
            "ownership_form": "private",
            "owners": "client_and_spouse",
        },
        "has_vehicle": False,
    },
}


def test_lead_intake_sends_the_qualifying_object_in_the_first_keys():
    """Отдельных ключей под второй объект в CRM нет.

    Андрей 12.08.2026: «если первый не подошёл, шлётся второй в ключах
    первого». Квалифицирующим стал второй — значит именно он занимает
    UF_CRM_1452154011, CHAT_REALTY_REGION и CHAT_OWNER. Иначе категорию
    определит один объект, а в лиде окажется другой.
    """
    from delivery.crm_mapping import map_lead_intake

    mapping = map_lead_intake(
        SECOND_OBJECT_FACTS,
        form_filled_out=True,
        site_consent_shown=False,
        site_consent_answer=None,
    )

    assert mapping.request is not None
    assert mapping.request.realty_type.value == 190  # квартира, а не дом
    assert "Москва" in (mapping.request.realty_region or "")
    assert mapping.request.chat_owner == "BOTH"


def test_lead_intake_keeps_the_first_object_when_there_is_no_second():
    from delivery.crm_mapping import map_lead_intake

    facts = deepcopy(FIRST_OBJECT)
    facts["client"]["phone"] = "+79991234567"
    facts["assets"]["has_vehicle"] = False

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
        site_consent_answer=None,
    )

    assert mapping.request is not None
    assert mapping.request.realty_type.value == 191  # дом
    assert mapping.request.chat_owner == "CLIENT"


def _root(facts: dict) -> str:
    from delivery.crm_mapping import _qualifying_realty_root

    return _qualifying_realty_root(facts)


def test_second_object_of_the_same_type_is_ignored():
    """Правило Андрея: тип второго объекта всегда отличается от первого.

    Сайт гарантирует это формой. Смысл прямой: если дом под залог не прошёл,
    второй дом тоже не пройдёт, а полями он первый перезапишет — и в лиде
    останется объект, который ничего не даёт.
    """
    facts = deepcopy(SECOND_OBJECT_FACTS)
    facts["assets"]["realty_2"]["kind"] = facts["assets"]["realty"]["kind"]

    assert _root(facts) == "assets.realty"


def test_second_object_without_a_region_is_ignored():
    """Регион обязателен: без него CRM не отнесёт объект ни к какой категории."""
    facts = deepcopy(SECOND_OBJECT_FACTS)
    facts["assets"]["realty_2"].pop("city")
    facts["assets"]["realty_2"].pop("region_class")

    assert _root(facts) == "assets.realty"


def test_second_object_without_an_owner_is_ignored():
    """Собственник идёт вместе с регионом — половина объекта хуже, чем ничего."""
    facts = deepcopy(SECOND_OBJECT_FACTS)
    facts["assets"]["realty_2"].pop("owners")

    assert _root(facts) == "assets.realty"


def test_complete_and_different_second_object_wins():
    assert _root(SECOND_OBJECT_FACTS) == "assets.realty_2"


def test_details_of_a_rejected_object_are_asked_while_the_branch_is_on():
    """Пока ветку решает CRM, наша матрица не вправе закрывать тему объекта.

    `skip_when_settled` (решение заказчика 30.07.2026) экономил вопросы там, где
    исход уже ясен нашей матрице. Но у второго объекта исход решает не она:
    `realty-qualification` спрашивают ровно тогда, когда первый объект наши
    маршруты отвергли. Экономия срезала два поля, без которых ветвитель не
    запускался вовсе — и клиент с домом в деревне не получал вопроса про
    московскую квартиру.
    """
    matrix = get_matrix()
    facts = deepcopy(FIRST_OBJECT)
    facts["assets"]["realty"].pop("ownership_form")
    facts["assets"]["realty"].pop("owners")

    asked = build_plan(matrix, facts, skip_settled=False).field_paths

    assert "assets.realty.ownership_form" in asked


def test_without_the_branch_the_saved_questions_stay_saved():
    """Выключенный флаг ничего не меняет: экономия вопросов остаётся в силе."""
    matrix = get_matrix()
    facts = deepcopy(FIRST_OBJECT)
    facts["assets"]["realty"].pop("ownership_form")
    facts["assets"]["realty"].pop("owners")

    asked = build_plan(matrix, facts).field_paths

    assert "assets.realty.ownership_form" not in asked


def test_the_flag_reaches_the_planner_through_the_turn(monkeypatch):
    """Флаг живёт в env, планировщик — чистая функция. Проверяем стык.

    Без этого теста ветка «включена» на бумаге: конфигурация читается,
    workflow её видит, а анкета продолжает срезать вопросы, и второй объект
    так и не спрашивается.
    """
    import core
    from config import AppConfig
    from state import init_state

    # Гео отключено намеренно: иначе тест идёт в DaData по сети и тратит квоту,
    # а неоднозначный город обнуляет регион и подменяет проверяемый вопрос.
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: facts)

    facts = deepcopy(FIRST_OBJECT)
    facts["assets"]["realty"].pop("ownership_form")
    facts["assets"]["realty"].pop("owners")
    state = init_state()
    state["facts"] = facts
    config = AppConfig(openrouter_api_key="sk-or-test", second_realty_enabled=True)

    # Пустая реплика намеренно: извлекатель не вызывается, проверяется только
    # решение планировщика.
    prepared = core.prepare_turn(state, "", config, submit_delivery=False)

    assert "assets.realty.ownership_form" in prepared["plan"]["fields"]


def test_config_reads_the_branch_flag(monkeypatch):
    from config import load_config

    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("MBK_CRM_SECOND_REALTY_ENABLED", "1")

    assert load_config().second_realty_enabled is True


def test_unresolved_region_does_not_block_the_branch():
    """Класс региона — наш внутренний артефакт, а CRM нужен текст.

    Живой прогон 12.08.2026: клиент назвал «деревню под Тверью», DaData её не
    разобрала, `region_class` остался пустым — и ветвитель не запустился, хотя
    название населённого пункта у нас было и в лид оно уехало. Проверять надо
    то, что действительно нужно запросу, а не то, что удобно маршрутизатору.
    """
    facts = deepcopy(FIRST_OBJECT)
    facts["assets"]["realty"].pop("region_class")
    facts["assets"]["realty"]["city"] = "деревня под Тверью"
    client = FakeRealtyClient(is_first_category=False)
    session = _session(facts)

    _workflow(client).check_second_realty(session)

    assert len(client.calls) == 1
    assert session.state["facts"]["assets"]["realty"]["needs_second"] is True


def test_an_object_without_a_region_at_all_is_not_settled_forever():
    """Пока объект неполон, вывод «второй не нужен» делать рано.

    Ответ пишется навсегда — по одному объекту CRM не переспрашивают. Записать
    его до того, как клиент назвал место, значит закрыть ветку из-за порядка
    вопросов, а не из-за сути.
    """
    facts = deepcopy(FIRST_OBJECT)
    facts["assets"]["realty"].pop("region_class")
    facts["assets"]["realty"].pop("city")
    client = FakeRealtyClient(is_first_category=False)
    session = _session(facts)

    _workflow(client).check_second_realty(session)

    assert client.calls == []
    assert "needs_second" not in session.state["facts"]["assets"]["realty"]


def test_the_second_object_is_asked_before_the_phone_not_after():
    """Ответ CRM приходит после того, как вопрос хода уже выбран.

    Живой прогон 13.08.2026: бот попросил телефон, следом спросил «есть ли ещё
    недвижимость», потом снова телефон. Связность 55 из 100 — разговор выглядит
    так, будто у собеседника сбой памяти. Открыв ветку, план хода надо
    пересчитать, а не ждать следующего хода.
    """
    facts = deepcopy(FIRST_OBJECT)
    facts["client"]["phone"] = None
    facts["assets"]["has_vehicle"] = False
    session = _session(facts)
    # План уже посчитан: анкета пройдена, спрашиваем телефон.
    session.state["plan"] = {"action": "contacts", "fields": ["client.phone"]}
    replanned: list[str] = []

    workflow = CrmDialogWorkflow(
        FakeRealtyClient(is_first_category=False),
        None,
        AllowAllSubmissionGate(),
        persist=lambda session: None,
        enabled=True,
        second_realty_enabled=True,
        replan=lambda state: replanned.append(str(state.get("plan"))),
    )
    workflow.check_second_realty(session)

    assert replanned, (
        "План хода не пересчитан: вопрос про второй объект прозвучит после "
        "просьбы оставить телефон."
    )


def test_replan_tells_the_next_turn_what_was_really_asked(monkeypatch):
    """Пересчитав вопрос хода, надо пересчитать и память о нём.

    `script["asked"]` — не диагностика: быстрый разбор
    (`interpretation/fastpath.py`) кладёт короткий ответ клиента прямо в это
    поле, минуя модель, и переписать ошибку потом некому. `replan` менял план,
    но не память, — и следующий ход разбирал ответ по вопросу, которого бот не
    задавал.

    Стоит это залога: бот спрашивает «есть ли ещё какая-то недвижимость»,
    клиент отвечает «нет», а «нет» ложится в `assets.has_vehicle` — машины у
    клиента больше нет, второй объект так и остался неспрошенным.
    """
    import core
    from config import AppConfig
    from interpretation.fastpath import parse_expected_answer
    from state import init_state

    # Гео отключено намеренно: иначе тест уходит в DaData по сети. Извлекатель
    # тоже: ход идёт с настоящей репликой, и без заглушки он звал бы модель.
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: facts)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )

    state = init_state()
    state["facts"] = deepcopy(FIRST_OBJECT)
    config = AppConfig(
        openrouter_api_key="sk-or-test",
        second_realty_enabled=True,
        fact_interpretation_mode="legacy",
    )
    message = "давайте дальше"

    # Ход считается обычным путём: анкета спрашивает про машину.
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    assert prepared["plan"]["fields"] == ["assets.has_vehicle"], "предпосылка теста"

    # Ответ `realty-qualification` приходит уже после разбора и меняет вопрос.
    prepared["facts"]["assets"]["realty"]["needs_second"] = True
    core.replan(prepared, config)
    assert prepared["plan"]["fields"] == ["assets.has_second_realty"], "предпосылка теста"

    # Модель спрашивает по пересчитанному плану; ход фиксируется как обычно.
    committed = core.commit_turn(
        prepared, message, "А есть ли у вас ещё какая-то недвижимость?"
    )["current_state"]

    assert committed["script"]["asked"] == prepared["plan"]["fields"], (
        "Память о заданном вопросе разошлась с планом: короткое «нет» "
        "следующего хода уедет в поле, о котором бот не спрашивал."
    )
    assert parse_expected_answer(
        "нет", committed["script"]["asked"], get_matrix()
    ) == [{"path": "assets.has_second_realty", "value": False, "confidence": "high"}]
    # Попытка засчитана тому вопросу, который прозвучал, — про второй объект.
    # Про машину бот на этом ходу не спрашивал: план передумали раньше, чем
    # модель заговорила, и её счётчик расти не должен — иначе поле упрётся в
    # потолок переспросов раньше времени.
    assert committed["script"]["attempts"].get("assets.has_second_realty") == 1
    assert committed["script"]["attempts"].get("assets.has_vehicle") is None
