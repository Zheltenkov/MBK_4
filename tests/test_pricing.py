"""Стоимость неизвестной модели не должна тихо оказываться нулём.

31.07.2026 при подборе более быстрого извлекателя прогон на
`deepseek/deepseek-v4-flash` показал стоимость $0.0000. Модель просто
отсутствовала в тарифной таблице, и `_compute_cost_usd` вернул ноль — то есть
смена модели выглядела бы бесплатной, а учёт расходов молча сломался бы.
"""
from __future__ import annotations

import pytest

from usage_tracker import PRICING_PER_MILLION_TOKENS, _compute_cost_usd


@pytest.mark.parametrize(
    "model",
    ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash", "qwen/qwen3.7-max"],
)
def test_models_we_actually_run_have_a_tariff(model):
    assert model in PRICING_PER_MILLION_TOKENS, model


def test_flash_is_cheaper_than_pro():
    pro = PRICING_PER_MILLION_TOKENS["deepseek/deepseek-v4-pro"]
    flash = PRICING_PER_MILLION_TOKENS["deepseek/deepseek-v4-flash"]

    assert flash["input"] < pro["input"]
    assert flash["output"] < pro["output"]


def test_unknown_model_is_reported_not_silently_free(caplog):
    """Иначе новая модель выглядит бесплатной, и это замечают на счёте."""
    cost = _compute_cost_usd("nobody/unknown-model", 1000, 100)

    assert cost == 0.0
    assert any("nobody/unknown-model" in record.message for record in caplog.records)


def test_known_model_still_costs_what_it_costs():
    cost = _compute_cost_usd("deepseek/deepseek-v4-flash", 1_000_000, 1_000_000)

    assert cost == pytest.approx(0.14 + 0.28)


def test_semantic_extractor_default_is_pinned_in_code(monkeypatch):
    """Умолчание живёт в коде, а не в прод-.env: так его видно в истории.

    31.07.2026 по корпусу из 50 одиночных реплик flash выглядел строго лучше
    pro и был поставлен умолчанием. Живой диалоговый прогон это опроверг: один
    провал маршрута и связность 100 -> 70..85 в шести сценариях из-за
    повторных вопросов о том, что клиент уже сказал. Модель возвращена.

    25.09.2026 deepseek-v4.1-flash прошла и корпус, и весь ролевой набор
    (16/16, связность 100) — теперь умолчание она, втрое быстрее pro.
    """
    from config import DEFAULT_FACT_INTERPRETATION_MODEL, load_config

    monkeypatch.delenv("MBK_FACT_INTERPRETATION_MODEL", raising=False)

    assert DEFAULT_FACT_INTERPRETATION_MODEL == "deepseek/deepseek-v4.1-flash"
    assert load_config().fact_interpretation_model == DEFAULT_FACT_INTERPRETATION_MODEL


def test_extractor_model_can_still_be_overridden(monkeypatch):
    from config import load_config

    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODEL", "some/other-model")

    assert load_config().fact_interpretation_model == "some/other-model"


# --------------------------------------------------------------------------- #
# Новая цена из окружения
# --------------------------------------------------------------------------- #
@pytest.fixture()
def pricing_table():
    """Таблица цен глобальная — возвращаем её после теста в прежний вид."""
    from usage_tracker import PRICING_PER_MILLION_TOKENS as table

    before = {model: dict(prices) for model, prices in table.items()}
    yield table
    table.clear()
    table.update(before)


def test_env_price_reaches_the_model_it_names(monkeypatch, pricing_table):
    """Провайдер поднял цену — её меняют переменной окружения, без выкатки.

    Имя модели в переменной окружения приходится писать подчёркиваниями, и
    точку из имени `qwen/qwen3.7-max` подчёркивание раньше не отыгрывало: в
    таблице заводилась вторая, несуществующая модель, а настоящая оставалась
    со старой ценой. Учёт расхода при этом не жаловался — просто считал по
    прежнему тарифу, и расхождение вылезало на счёте от провайдера.
    """
    from usage_tracker import override_pricing_from_env

    monkeypatch.setenv("PRICE_qwen_qwen3_7_max_INPUT", "3.0")
    monkeypatch.setenv("PRICE_qwen_qwen3_7_max_OUTPUT", "9.0")

    override_pricing_from_env()

    assert pricing_table["qwen/qwen3.7-max"]["input"] == 3.0
    assert pricing_table["qwen/qwen3.7-max"]["output"] == 9.0
    assert _compute_cost_usd("qwen/qwen3.7-max", 1_000_000, 1_000_000) == 12.0


def test_env_price_does_not_invent_a_second_model(monkeypatch, pricing_table):
    """Лишний ключ в таблице — это ещё и «модель без тарифа» в отчёте."""
    from usage_tracker import override_pricing_from_env

    known = set(pricing_table)
    monkeypatch.setenv("PRICE_qwen_qwen3_7_max_INPUT", "3.0")

    override_pricing_from_env()

    assert set(pricing_table) == known


def test_env_price_still_opens_a_tariff_for_a_new_model(monkeypatch, pricing_table):
    """Модель, которой в таблице нет, должна заводиться из окружения."""
    from usage_tracker import override_pricing_from_env

    monkeypatch.setenv("PRICE_vendor_super_model_INPUT", "1.5")
    monkeypatch.setenv("PRICE_vendor_super_model_OUTPUT", "2.5")

    override_pricing_from_env()

    assert pricing_table["vendor/super-model"] == {"input": 1.5, "output": 2.5}


def test_default_extractor_has_a_price():
    """Без цены в таблице расход по новой модели молча перестал бы считаться."""
    from config import DEFAULT_FACT_INTERPRETATION_MODEL
    from usage_tracker import PRICING_PER_MILLION_TOKENS

    assert DEFAULT_FACT_INTERPRETATION_MODEL in PRICING_PER_MILLION_TOKENS
