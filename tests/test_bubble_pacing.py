"""Финальные пузыри атомарно заменяют уже показанный поток токенов."""
from __future__ import annotations

import asyncio
import json

import main


def _collect(bubbles: list[str]) -> list[list[str]]:
    async def run():
        events = []
        async for event in main._paced_bubbles(bubbles):
            assert event["event"] == "bubbles"
            events.append(json.loads(event["data"])["bubbles"])
        return events

    return asyncio.run(run())


def test_all_bubbles_are_committed_in_one_event():
    events = _collect(["Первое сообщение.", "И сразу второе?"])

    assert events == [["Первое сообщение.", "И сразу второе?"]]


def test_empty_bubble_list_still_finishes_the_contract():
    assert _collect([]) == [[]]


def _payloads(bubbles, chips=None) -> list[dict]:
    async def run():
        return [
            json.loads(event["data"])
            async for event in main._paced_bubbles(bubbles, chips)
        ]

    return asyncio.run(run())


def test_chips_ride_the_same_event():
    chips = [{"label": "Получить деньги", "value": "Получить деньги"}]

    assert _payloads(["Добрый день!"], chips) == [
        {"bubbles": ["Добрый день!"], "quick_replies": chips}
    ]


def test_no_chips_means_no_field():
    """Старый виджет и любой ход после реплики клиента видят прежний контракт."""
    assert _payloads(["Слушаю."], []) == [{"bubbles": ["Слушаю."]}]
    assert _payloads(["Слушаю."]) == [{"bubbles": ["Слушаю."]}]
