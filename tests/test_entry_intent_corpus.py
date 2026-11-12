"""Корпус первых реплик: живой поток, без контактов, и словарь его не портит.

Требование спеки одно и жёсткое: реплики, где человеку нужны деньги, не
уходят в ветки «уже оставлял заявку» и «не наш профиль». Для словаря это
проверяется здесь, без сети; для модели — `run_entry_intent_eval.py`.
"""
from __future__ import annotations

import json
import re

import pytest

from evaluation.entry_intent import load_entry_corpus
from routing.entry_intent import EntryIntent, classify_by_dictionary


# 10.09.2026: 226 диалогов, 194 уникальные первые реплики. Порог ниже на
# случай мелких правок разметки, но не даёт корпусу тихо усохнуть.
MIN_CASES = 190
_LONG_DIGIT_RUN = re.compile(r"(?:\d[\s\-()+]*){10,}")
_UNMASKED_FINGERPRINT = re.compile(r"\[телефон [^\]]+\]")


def test_corpus_is_the_live_stream_not_a_toy_set():
    cases = load_entry_corpus()

    assert len(cases) >= MIN_CASES
    assert {case.expected for case in cases} == set(EntryIntent)
    assert all(case.count >= 1 for case in cases)


def test_corpus_carries_no_contact_details():
    for case in load_entry_corpus():
        assert not _LONG_DIGIT_RUN.search(case.message), case.id
        assert "@" not in case.message, case.id
        assert not _UNMASKED_FINGERPRINT.search(case.message), case.id


def test_dictionary_never_takes_money_away_from_the_questionnaire():
    lost = [
        case.id
        for case in load_entry_corpus()
        if case.expected is EntryIntent.MONEY
        and classify_by_dictionary(case.message)
        in {EntryIntent.EXISTING, EntryIntent.OFF_TOPIC}
    ]

    assert lost == []


def test_dictionary_never_decides_off_topic():
    assert all(
        classify_by_dictionary(case.message) is not EntryIntent.OFF_TOPIC
        for case in load_entry_corpus()
    )


def test_dictionary_catches_at_least_half_of_the_obvious_existing_requests():
    existing = [
        case for case in load_entry_corpus() if case.expected is EntryIntent.EXISTING
    ]
    caught = [
        case
        for case in existing
        if classify_by_dictionary(case.message) is EntryIntent.EXISTING
    ]

    assert len(caught) * 2 >= len(existing)


@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        ({"id": "x", "message": "привет", "expected": "maybe"}, "maybe"),
        ({"id": "x", "message": "  ", "expected": "unclear"}, "пуст"),
        ({"id": "x", "message": "привет", "expected": "unclear", "extra": 1}, "лишн"),
        ({"id": "x", "message": "привет", "expected": "unclear", "count": 0}, "count"),
    ],
)
def test_loader_rejects_malformed_cases(tmp_path, broken, reason):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([broken], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match=reason):
        load_entry_corpus(path)


def test_loader_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "corpus.json"
    case = {"id": "x", "message": "привет", "expected": "unclear"}
    path.write_text(json.dumps([case, case], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="повтор"):
        load_entry_corpus(path)
