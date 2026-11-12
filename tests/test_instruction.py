"""Тесты инструкции хода — того, что реально доезжает до модели.

Проверяем не красоту текста, а свойства, от которых зависит человечность: есть ли у
модели пространство, назван ли запрет, объяснено ли «зачем».
"""
from __future__ import annotations

from prompts import ASSISTANT_NAME, CONVERSATION_SYSTEM_PROMPT
from prompts import OPENING_INSTRUCTION, OPENING_MESSAGE
from prompts import STYLE_EXAMPLES, turn_instruction
from routing import plan


def instruction_for(matrix, facts):
    return turn_instruction(plan(matrix, facts))


# Анкета доведена до блока активов: следующий вопрос — «есть ли недвижимость».
BRANCH_FACTS = {
    "request": {"amount": 500_000},
    "client": {"name": "Василий", "age": 40, "region_class": "moscow_mo"},
    "debts": {"has_active_credits": False},
    "employment": {"type": "official", "income": 150_000},
}

STARTED = {"client": {"name": "Василий"}}


def test_instruction_states_what_to_close(matrix):
    text = instruction_for(matrix, STARTED)
    assert "НУЖНО ЗАКРЫТЬ" in text
    assert "какая сумма нужна" in text


def test_instruction_explains_why(matrix):
    """«Назовите возраст» — анкета. С причиной модель формулирует по-человечески."""
    text = instruction_for(matrix, {"client": {"name": "Василий", "age": None}})
    assert "от суммы зависит" in text


def test_instruction_reads_as_one_sentence_not_a_bulleted_checklist(matrix):
    """Жалоба на «механический» тон: раньше движок слал голый bullet-list полей
    каждый ход, и модели приходилось самой разворачивать анкету в разговор.
    Рамка должна прийти уже связным предложением, а не списком с тире.
    """
    text = instruction_for(matrix, STARTED)
    required_paragraph = text.split("\n\n")[0]
    assert "\n-" not in required_paragraph
    # Причина с 30.07.2026 живёт отдельным фоновым блоком, а не в самом вопросе.
    assert required_paragraph == (
        "НУЖНО ЗАКРЫТЬ на этом ходу: какая сумма нужна; сколько лет клиенту."
    )


def test_instruction_forbids_unconfirmed_branch(matrix):
    text = instruction_for(matrix, BRANCH_FACTS)
    assert "НЕ СПРАШИВАЙ СЕЙЧАС" in text
    assert "тип недвижимости" in text
    assert "телефон" not in text


def test_instruction_warns_about_the_backend_transition(matrix):
    """Переход пишет backend дословно — модель не должна его пересказывать."""
    text = instruction_for(matrix, BRANCH_FACTS)
    assert "backend сам добавит" in text


def test_instruction_absorbs_objections_without_changing_the_question(matrix):
    """Ключевое правило разворота: возражение не меняет состав анкеты."""
    text = instruction_for(matrix, BRANCH_FACTS)
    assert "ВОЗРАЖЕНИЕ НЕ МЕНЯЕТ ВОПРОС" in text


def test_first_response_defers_contact_question_to_backend(matrix):
    """Модель отвечает на обращение, а точный контактный пузырь добавляет backend."""
    text = instruction_for(matrix, {})
    assert "НЕ задавай вопросов" in text
    assert "Backend присоединит" in text
    assert "одном сообщении" in text


def test_opening_message_invites_the_client_to_speak_first():
    assert OPENING_MESSAGE == (
        f"Добрый день! Я {ASSISTANT_NAME}, специалист МБК. Чем я могу вам помочь?"
    )
    assert OPENING_MESSAGE in OPENING_INSTRUCTION
    assert "Контакты сейчас не спрашивай" in OPENING_INSTRUCTION


def test_instruction_allows_answering_the_client_first(matrix):
    """Ход без вопроса разрешён явно — иначе бот игнорирует вопросы клиента."""
    text = instruction_for(matrix, BRANCH_FACTS)
    assert "сначала ответь ему" in text
    assert "не переспрашивай" in text


def test_instruction_no_longer_dictates_a_script(matrix):
    """Старая формулировка запрещала любой шаг в сторону — с ней разговор невозможен."""
    text = instruction_for(matrix, BRANCH_FACTS)
    assert "и больше ничего" not in text
    assert "Ничего сверх этого списка" not in text


def test_model_may_only_ask_fields_from_current_plan(matrix):
    text = instruction_for(matrix, BRANCH_FACTS)

    assert "«НУЖНО ЗАКРЫТЬ» этого хода" in text
    assert "не состава и не порядка" in text
    assert "не собирай «на всякий случай» ипотеку" in CONVERSATION_SYSTEM_PROMPT.lower()


def test_model_is_never_allowed_to_generate_a_link():
    assert "НИКОГДА не пиши URL" in CONVERSATION_SYSTEM_PROMPT
    assert "[ссылка]" in CONVERSATION_SYSTEM_PROMPT


def test_model_may_have_character_but_not_hint_at_route_logic():
    """Из сравнения с v3: жёсткость запрета «не называй продукт» не должна мешать
    боту иметь характер — но характер не должен превращаться в объяснение стоп-факторов.
    """
    prompt = CONVERSATION_SYSTEM_PROMPT.lower()
    assert "имей характер" in prompt
    assert "не объясняй, почему что-то может не подойти" in prompt
    assert "не называй конкретные факторы риска" in prompt


def test_initial_contacts_instruction_asks_for_phone_without_naming_product(matrix):
    facts = {
        "request": {"amount": 500_000},
        "client": {"goal": "cash", "age": 40, "region_class": "moscow_mo"},
        "assets": {"has_realty": True, "realty": {
            "region_class": "moscow_mo", "kind": "flat",
            "ownership_form": "private", "owners": "client_only"}},
    }
    text = instruction_for(matrix, facts)
    assert "телефон" in text
    assert "Залог недвижимости" not in text


def test_initial_contacts_instruction_leaves_missing_name_to_backend(matrix):
    facts = {
        "request": {"amount": 500_000},
        "client": {
            "goal": "cash",
            "age": 40,
            "region_class": "moscow_mo",
            "phone": "+79990000000",
        },
        "assets": {"has_realty": True, "realty": {
            "region_class": "moscow_mo", "kind": "flat",
            "ownership_form": "private", "owners": "client_only"}},
    }
    text = instruction_for(matrix, facts)

    assert "НЕ задавай вопросов" in text
    assert "Backend присоединит" in text


DEAD_END_FACTS = {
    "request": {"amount": 100_000},
    "client": {"name": "Василий", "age": 70, "region_class": "rf_other"},
    "debts": {"has_active_credits": False},
    "employment": {"type": "unemployed"},
    "assets": {"has_realty": False, "has_vehicle": False},
}


def test_no_fit_instruction_is_honest(matrix):
    facts = {**DEAD_END_FACTS}
    facts["client"] = {**facts["client"], "phone": "+79990000000"}

    text = instruction_for(matrix, facts)

    assert "РАБОЧЕГО ВАРИАНТА СЕЙЧАС НЕТ" in text
    assert "без обещаний" in text


def test_phone_request_stays_soft_when_nothing_matched(matrix):
    """Заказчик отдельно назвал риск: пообещать решение и не найти его."""
    text = instruction_for(matrix, DEAD_END_FACTS)

    assert "телефон" in text
    assert "решение для клиента есть" not in text
    assert "не обещай ссылку" in text.lower()


def test_style_examples_do_not_demonstrate_the_finale():
    """Образец стиля не должен показывать завершение диалога.

    В прогоне 20260729_123832 экстрактор потерял ответ про просрочку, анкета
    встала на шаге 5, а модель шесть ходов вела разговор по памяти и в конце
    воспроизвела финал прямо из этого примера: «по вашей ситуации решение
    есть» плюс запрос телефона. Клиент получил обещание ссылки, которой не
    будет: маршрут не выбран, delivery пуст, лид не создан.

    Терминальные формулировки модель получает из turn_instruction — там, где
    план действительно дошёл до конца. В образце стиля им делать нечего.
    """
    lowered = STYLE_EXAMPLES.lower()

    assert "решение есть" not in lowered
    assert "пришлю ссылку" not in lowered
    assert "номер телефона" not in lowered


def test_style_examples_still_cover_tone_and_objections():
    """Обрезка не должна унести то, ради чего пример существует."""
    lowered = STYLE_EXAMPLES.lower()

    assert "а зачем вам всё это" in lowered
    assert "переживать тут не о чем" in lowered
    assert "код я тут не пишу" in lowered


def test_question_arrives_without_a_ready_made_explanation(matrix):
    """Заказчик 30.07.2026: «после каждого вопроса пишет объяснение… можно это
    всё снести, пусть просто задаёт вопросы, покороче и попроще».

    Причина была в том, что `why` каждого поля уезжал в обязательную часть хода,
    и модель послушно разворачивала его во второй пузырь — все 17 вопросов
    анкеты подряд.
    """
    text = instruction_for(matrix, STARTED)
    required = text.split("\n\n")[0]

    assert "какая сумма нужна" in required
    assert "от суммы зависит" not in required


def test_reason_stays_available_for_a_client_who_asks_why(matrix):
    """Совсем выбрасывать причину нельзя: на «зачем вам это» бот должен
    ответить по существу, а не отговоркой. Она уходит фоном, не репликой."""
    text = instruction_for(matrix, STARTED)

    assert "ЕСЛИ СПРОСЯТ ЗАЧЕМ" in text
    assert "от суммы зависит" in text
    assert "не произноси" in text.lower()


def test_turn_rules_ask_for_brevity_not_explanation(matrix):
    """Заказчик: «покороче, попроще, где-то одним словом, не так официально».

    Свобода хода прямо разрешала «объяснить следующий вопрос» — с этим
    разрешением убранный из рамки `why` вернулся бы своими словами.
    """
    text = instruction_for(matrix, STARTED)

    assert "объяснить следующий вопрос" not in text
    assert "Короче — лучше" in text
    assert "только если клиент спросил" in text


def test_bot_question_gets_a_brief_honest_answer_and_continues_the_conversation():
    """The recorded evasive answer is replaced without a technical monologue."""
    assert "бот ли ты, ИИ или человек" in CONVERSATION_SYSTEM_PROMPT
    assert "AI-помощник МБК" in CONVERSATION_SYSTEM_PROMPT
    assert "коротко и честно" in CONVERSATION_SYSTEM_PROMPT
    assert "Не выдавай себя за человека" in CONVERSATION_SYSTEM_PROMPT
    assert "Затем продолжай текущий разговор" in CONVERSATION_SYSTEM_PROMPT
    # The example preserves a short reply and the existing conversational rhythm.
    assert "бот или человек" in STYLE_EXAMPLES
    assert "Я AI-помощник МБК." in STYLE_EXAMPLES
    assert "Так что с просрочками, сейчас есть?" in STYLE_EXAMPLES
    assert "немудрено спутать" not in STYLE_EXAMPLES
