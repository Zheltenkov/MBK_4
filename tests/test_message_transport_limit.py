"""Реплика, которую принимает приложение, должна доезжать до приложения.

Потолок длины реплики объявлен в приложении (MBK_MAX_MESSAGE_CHARS, по
умолчанию 2000 знаков) и там же аккуратно обрезается. Но едет реплика в
строке запроса: SSE открывается нативным EventSource, а он умеет только GET
(frontend/src/api.js). Строку запроса первым читает nginx нашего фронтенда, и
по умолчанию у него на неё 8 КБ — `large_client_header_buffers 4 8k`.

Русский текст в проценты-кодировке разбухает вшестеро, поэтому до приложения
не доезжает уже около полутора тысяч знаков: nginx отвечает 414, клиент видит
«Соединение прервано», а в наших логах не появляется ничего — запрос до
backend не дошёл. Проверяется согласие двух половин: объявленный потолок и
буфер прокси.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from main import DEFAULT_MAX_MESSAGE_CHARS


ROOT = Path(__file__).resolve().parent.parent
NGINX_CONF = ROOT / "frontend" / "nginx.conf"
API_JS = ROOT / "frontend" / "src" / "api.js"

# Худший случай процентов-кодировки: один символ Unicode — до четырёх байт,
# каждый байт — три знака («%D0»). Кириллица даёт шесть знаков на букву, эмодзи
# все двенадцать, и клиент вправе прислать что угодно.
WORST_CASE_BYTES_PER_CHAR = 12
# Запас на путь, идентификатор сессии и служебные заголовки в том же буфере.
HEADROOM_BYTES = 1024

_SIZE_UNITS = {"": 1, "k": 1024, "m": 1024 * 1024}


def _bytes(raw: str) -> int:
    match = re.fullmatch(r"(\d+)([kKmM]?)", raw)
    assert match, f"непонятный размер буфера: {raw}"
    return int(match.group(1)) * _SIZE_UNITS[match.group(2).lower()]


def _configured_request_line_buffer() -> int:
    """Размер одного большого буфера заголовков nginx.

    Умолчание не годится намеренно: если директивы нет, отвечаем ноль, и тест
    падает — правило должно быть записано, а не подразумеваться.
    """
    conf = NGINX_CONF.read_text(encoding="utf-8")
    match = re.search(r"large_client_header_buffers\s+(\d+)\s+(\w+)\s*;", conf)
    return _bytes(match.group(2)) if match else 0


def test_message_still_travels_in_the_query_string():
    """Если реплика переедет в тело запроса, ограничение снимется само."""
    assert "searchParams.set('message'" in API_JS.read_text(encoding="utf-8")


def test_proxy_accepts_the_longest_message_we_promise_to_accept():
    required = DEFAULT_MAX_MESSAGE_CHARS * WORST_CASE_BYTES_PER_CHAR + HEADROOM_BYTES

    assert _configured_request_line_buffer() >= required, (
        "nginx обрежет строку запроса раньше, чем приложение обрежет реплику: "
        f"нужно не меньше {required} байт на буфер"
    )


@pytest.mark.parametrize("chars", [1500, DEFAULT_MAX_MESSAGE_CHARS])
def test_a_real_russian_message_of_that_length_fits(chars):
    """Проверка на живом тексте, а не только на худшем случае."""
    from urllib.parse import quote

    text = ("Мне нужно полтора миллиона рублей на ремонт квартиры. " * 200)[:chars]
    request_line = f"GET /api/session/0123456789ab/stream?message={quote(text)} HTTP/1.1"

    assert len(request_line.encode("utf-8")) <= _configured_request_line_buffer()
