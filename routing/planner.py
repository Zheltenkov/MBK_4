"""Планировщик вопросов: что спросить следующим ходом.

Принцип с созвона 28.07.2026 — фиксированная анкета вместо адаптивного опроса по
приоритетам маршрутов. Заказчик дословно: «забей на приоритеты, собирай хардкор,
анкету». Причина: приоритетная логика уводила разговор в недвижимость первым же
содержательным вопросом и раздражала как раз тех клиентов, ради которых делалась.

Отсюда роли:

  сценарий (`matrix.script`) решает, ЧТО и в каком порядке спрашивать;
  `after`/`after_in` у полей решают, какие подвопросы вообще открыть;
  классификатор (`engine`) вызывается один раз, когда анкета пройдена.

Возражения клиента («не хочу под залог») отрабатываются словами и состав вопросов
не меняют. Единственное, на что влияет явный отказ от залога, — финальный выбор
маршрута: закладывать имущество против воли клиента нельзя.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .engine import RouteStatus, evaluate, get_fact
from .schema import FieldSpec, Matrix, ScriptStep
from utils import client_display_name


Action = Literal["ask", "contacts", "route_found", "no_fit"]

DEFAULT_MAX_FIELDS = 3


@dataclass
class PlannedField:
    path: str
    spec: FieldSpec
    # Клиент уже отвечал, но приблизительно («около двух месяцев»), и reducer
    # придержал значение. Спрашивать надо не то же самое, а уточнение — иначе
    # выходит глухой повтор вопроса, на который человек уже ответил.
    needs_confirmation: bool = False
    # Сколько раз это поле уже спрашивали. Ноль — вопрос новый. Больше нуля
    # означает, что предыдущий ответ не лёг: либо клиент ответил не о том, либо
    # ответ придержан. В обоих случаях повторять вопрос дословно нельзя.
    attempt: int = 0

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def ask(self) -> str:
        return self.spec.ask

    @property
    def why(self) -> str | None:
        return self.spec.why


@dataclass
class Plan:
    """Решение планировщика на ход.

      fields     — что спросить сейчас (обычно ровно один вопрос анкеты);
      forbidden  — что спрашивать нельзя: подвопросы ещё не открытой ветки;
      transition — дословная фраза заказчика перед этим шагом, если она задана;
      step       — индекс шага сценария, нужен для диагностики и метрики отвала;
      matched    — маршрут, выбранный классификатором после прохождения анкеты.

    Жёстким остаётся запрет: именно он не даёт спросить «а какая недвижимость?»
    в том же ходу, что и «есть ли недвижимость».
    """

    action: Action
    statuses: list[RouteStatus] = field(default_factory=list)
    fields: list[PlannedField] = field(default_factory=list)
    forbidden: list[PlannedField] = field(default_factory=list)
    transition: str | None = None
    step: int | None = None
    # Маршрут, выбранный после прохождения анкеты (action=contacts/route_found).
    # None означает, что подходящего решения нет — это меняет тон финальной фразы.
    matched: RouteStatus | None = None

    @property
    def field_paths(self) -> list[str]:
        return [item.path for item in self.fields]

    @property
    def forbidden_paths(self) -> list[str]:
        return [item.path for item in self.forbidden]


def plan(
    matrix: Matrix,
    facts: dict[str, Any],
    max_fields: int = DEFAULT_MAX_FIELDS,
    *,
    fact_statuses: dict[str, Any] | None = None,
    attempts: dict[str, int] | None = None,
    skip_settled: bool = True,
) -> Plan:
    """Все дополнительные параметры необязательны: без них поведение прежнее.

    `fact_statuses` превращает придержанный reducer'ом ответ в уточняющий
    вопрос. `attempts` — страховка: поле, которое спросили слишком много раз,
    признаётся непроходимым, и анкета идёт дальше. Без неё один придержанный
    факт останавливает разговор навсегда, и клиент не доходит даже до телефона.

    `skip_settled=False` отключает экономию вопросов по объекту недвижимости.
    Нужно при включённой ветке второго объекта: там исход решает не наша
    матрица, а `realty-qualification`, и закрывать тему раньше времени нельзя.
    Решение принимает вызывающая сторона — планировщик остаётся чистой
    функцией и env не читает.
    """
    statuses = evaluate(matrix, facts)

    # Знакомство. Спрашиваем один раз: имя, из которого не удалось ничего
    # извлечь, останавливало анкету навсегда — 17 обрывов в бою за три недели.
    # Для CRM обязателен только телефон, а `contact: true` вернёт вопрос об
    # имени в конце, вместе с номером.
    initial = [
        path
        for path, spec in matrix.fields.items()
        if spec.initial
        and get_fact(facts, path) in (None, "")
        and not (attempts or {}).get(path)
    ]
    if initial:
        required = initial[:max_fields]
        return Plan(
            action="ask",
            statuses=statuses,
            fields=_wrap(matrix, required),
            forbidden=_wrap(matrix, _forbidden_fields(matrix, facts, required)),
        )

    settled = _settled_fields(matrix, statuses) if skip_settled else set()
    step_index, step = _next_step(matrix, facts, attempts, settled)
    if step is not None:
        required = _open_fields(matrix, facts, step, attempts, settled)[:max_fields]
        return Plan(
            action="ask",
            statuses=statuses,
            fields=_wrap(matrix, required, fact_statuses, attempts),
            forbidden=_wrap(matrix, _forbidden_fields(matrix, facts, required)),
            transition=_render_transition(step.transition, facts),
            step=step_index,
        )

    # Анкета пройдена — только теперь вызываем классификатор.
    matched = _classify(statuses, facts)

    missing_post_match = (
        _missing_post_match_fields(matrix, matched, facts) if matched else []
    )
    if missing_post_match:
        required = missing_post_match[:max_fields]
        return Plan(
            action="ask",
            statuses=statuses,
            fields=_wrap(matrix, required),
            forbidden=_wrap(matrix, _forbidden_fields(matrix, facts, required)),
            matched=matched,
        )

    missing_contacts = _missing_contacts(matrix, facts, attempts)
    if missing_contacts:
        # matched=None здесь не ошибка, а сигнал тона: решения нет, но телефон
        # всё равно нужен — заказчик просил не терять таких клиентов.
        return Plan(
            action="contacts",
            statuses=statuses,
            fields=missing_contacts,
            matched=matched,
        )

    if matched is None:
        return Plan(action="no_fit", statuses=statuses)
    return Plan(action="route_found", statuses=statuses, matched=matched)


def _settled_fields(matrix: Matrix, statuses: list[RouteStatus]) -> set[str]:
    """Поля, ответ на которые уже не может изменить исход.

    Заказчик 30.07.2026: «можно временно, пока по недвиге: если не подходит под
    наши критерии, то можно не спрашивать дополнительные вопросы». Сказал «дом» —
    и три оставшихся вопроса про объект теряют смысл, потому что все залоговые
    маршруты по недвижимости уже отпали.

    Область объявлена флагом `skip_when_settled` в матрице, а не выведена из
    графа условий. Общая версия правила срезала `employment.type` в 8 сценариях
    корпуса из 15: формально ни один живой маршрут на него уже не опирался, но
    партнёр ждёт сведения о занятости независимо от того, какое решение выбрано.
    Пропуск вопроса и потеря данных для CRM — разные вещи, и различить их
    графом маршрутов нельзя.
    """
    candidates = {
        path for path, spec in matrix.fields.items() if spec.skip_when_settled
    }
    if not candidates:
        return set()

    referenced: dict[str, list[str]] = {}
    for status in statuses:
        for condition in status.route.conditions:
            if condition.field in candidates:
                referenced.setdefault(condition.field, []).append(status.state)
    return {
        field
        for field, states in referenced.items()
        if states and all(state == "FAIL" for state in states)
    }


def _next_step(
    matrix: Matrix,
    facts: dict[str, Any],
    attempts: dict[str, int] | None = None,
    settled: set[str] | None = None,
) -> tuple[int | None, ScriptStep | None]:
    """Первый шаг сценария, у которого есть хоть одно открытое поле.

    Шаг, все поля которого отвечены или закрыты предусловием, просто пропускается.
    Поэтому «нет недвижимости» само проваливает обход к блоку авто — ветвление
    живёт в `after`, а не в сценарии.
    """
    for index, step in enumerate(matrix.script):
        if _open_fields(matrix, facts, step, attempts, settled):
            return index, step
    return None, None


def _open_fields(
    matrix: Matrix,
    facts: dict[str, Any],
    step: ScriptStep,
    attempts: dict[str, int] | None = None,
    settled: set[str] | None = None,
) -> list[str]:
    return [
        path
        for path in step.ask
        if get_fact(facts, path) in (None, "")
        and _prerequisite_met(matrix, path, facts)
        and not _unanswerable(facts, path)
        and not _asked_too_many_times(attempts, path)
        and path not in (settled or set())
    ]


# Сколько раз уточняем одноимённый населённый пункт, прежде чем признать шаг
# непроходимым. Должно совпадать с порогом передачи оператору в core.py.
_MAX_LOCATION_ATTEMPTS = 3

# Сколько раз спрашиваем любое другое поле, прежде чем признать его
# непроходимым. Три — это исходный вопрос плюс два уточнения; дальше человек
# либо не понимает вопрос, либо отвечает так, что reducer не принимает ответ.
# Продолжать бессмысленно, а вставший курсор стоит целого обращения.
MAX_FIELD_ATTEMPTS = 3


def _asked_too_many_times(attempts: dict[str, int] | None, path: str) -> bool:
    if not attempts:
        return False
    return int(attempts.get(path) or 0) >= MAX_FIELD_ATTEMPTS

# Поля, чей ответ выясняется через уточнение адреса. Только для них имеет смысл
# счётчик попыток: остальные вопросы клиент либо отвечает, либо нет.
_LOCATION_FIELDS = {
    "client.region_class": "client.location",
    "assets.realty.region_class": "assets.realty.location",
}


def _unanswerable(facts: dict[str, Any], path: str) -> bool:
    """Шаг, который спрашивать дальше бессмысленно.

    Регион с одноимёнными населёнными пунктами клиент может так и не уточнить.
    При фиксированной анкете курсор встал бы на нём навсегда: человек не дошёл
    бы до телефона, и обращение не попало бы в CRM вообще. Раньше это
    маскировалось приоритетами — планировщик просто уходил в другую ветку.
    """
    location_path = _LOCATION_FIELDS.get(path)
    if location_path is None:
        return False
    location = get_fact(facts, location_path)
    if not isinstance(location, dict):
        return False
    attempts = location.get("attempts")
    return isinstance(attempts, int) and attempts >= _MAX_LOCATION_ATTEMPTS


def _render_transition(template: str | None, facts: dict[str, Any]) -> str | None:
    """Подставляет имя в дословную фразу заказчика.

    Имя может быть ещё не известно — тогда убираем обращение целиком, чтобы не
    получилось «, сейчас соберу данные».
    """
    if not template:
        return None
    name = client_display_name(facts)
    if name:
        return template.replace("{name}", name)
    cleaned = template.replace("{name}, ", "").replace("{name} ", "").replace("{name}", "")
    return cleaned[:1].upper() + cleaned[1:] if cleaned else None


def _classify(
    statuses: list[RouteStatus], facts: dict[str, Any]
) -> RouteStatus | None:
    """Первый подошедший маршрут по приоритету, с учётом явного отказа от залога.

    Порядок вопросов это не меняет: анкета собирается целиком в любом случае.
    Но отправлять человека на залог недвижимости после его «квартиру закладывать
    не хочу» нельзя — поэтому отказ исключает соответствующие семьи маршрутов.
    """
    allowed = [
        status
        for status in statuses
        if status.state == "PASS" and _collateral_allows(status, facts)
    ]
    return allowed[0] if allowed else None


def _collateral_allows(status: RouteStatus, facts: dict[str, Any]) -> bool:
    family = status.route.family
    if get_fact(facts, "preferences.collateral_allowed") is False and family in {
        "realty",
        "vehicle",
    }:
        return False
    if family == "realty" and get_fact(facts, "preferences.realty_collateral_allowed") is False:
        return False
    if family == "vehicle" and get_fact(facts, "preferences.vehicle_collateral_allowed") is False:
        return False
    return True


def _wrap(
    matrix: Matrix,
    paths: list[str],
    fact_statuses: dict[str, Any] | None = None,
    attempts: dict[str, int] | None = None,
) -> list[PlannedField]:
    statuses = fact_statuses or {}
    counts = attempts or {}
    return [
        PlannedField(
            path=path,
            spec=matrix.fields[path],
            needs_confirmation=str(statuses.get(path) or "") == "needs_confirmation",
            attempt=int(counts.get(path) or 0),
        )
        for path in paths
    ]


def _forbidden_fields(
    matrix: Matrix, facts: dict[str, Any], asking: list[str]
) -> list[str]:
    """Чего спрашивать нельзя. Единственная жёсткая часть плана.

    Перечисляем не всё подряд, а только то, что реально рискует всплыть на этом ходу:
    подвопросы ветки, родитель которой спрашивается прямо сейчас. Это и есть момент
    забегания вперёд: «есть ли недвижимость?» и следом «а какая именно?» — клиент
    ещё не сказал «да», а его уже допрашивают.

    Гнать в запрет весь остаток матрицы бессмысленно: те поля на этом ходу и так не
    возникнут, а инструкция раздувается и теряет вес.
    """
    asking_now = set(asking)
    blocked = [
        path
        for path, spec in matrix.fields.items()
        if spec.after in asking_now and get_fact(facts, path) in (None, "")
    ]
    deferred = [
        path
        for path, spec in matrix.fields.items()
        if spec.deferred and get_fact(facts, path) in (None, "")
    ]
    return blocked + deferred


def _prerequisite_met(matrix: Matrix, path: str, facts: dict[str, Any]) -> bool:
    """Поле с `after` спрашиваем только когда предусловие уже подтверждено.

    Явный `False` считается невыполненным предусловием: «недвижимости нет» должно
    закрывать весь блок вопросов о ней, а не открывать его.

    `after_in` дополнительно сужает предусловие до конкретных значений родителя —
    без этого безработного спросили бы про официальный доход, ведь «unemployed»
    само по себе непустое значение.
    """
    spec = matrix.fields[path]
    if not spec.after:
        return True
    value = get_fact(facts, spec.after)
    if value is None or value is False or value == "":
        return False
    if spec.after_in is not None:
        return value in spec.after_in
    return True


def _missing_contacts(
    matrix: Matrix,
    facts: dict[str, Any],
    attempts: dict[str, int] | None = None,
) -> list[PlannedField]:
    """Контакты перед выдачей решения.

    Имя собрано в начале, поэтому здесь обычно остаётся только телефон — тот самый
    финальный шаг «давай телефон, дам ссылку». Проверка fail-closed: микросервис
    нельзя вызвать без обязательных данных.

    Потолок попыток тот же, что у полей сценария. Без него контакты шли мимо
    страховки: ролевой прогон 11.08.2026 показал клиента, который просто не даёт
    номер, — бот повторял один и тот же запрос двадцать три хода подряд. Человек,
    трижды не ответивший на просьбу, отвечать не собирается, и настаивать дальше
    значит только злить.
    """
    return [
        PlannedField(path=path, spec=spec)
        for path, spec in matrix.fields.items()
        if spec.contact
        and get_fact(facts, path) in (None, "")
        and not _contact_asked_enough(attempts, path)
    ]


# Имя для CRM не обязательно, и на контактном шаге оно звучит на раз меньше.
# Третий заход был не вопросом, а фразой «не хотите называть имя — ничего
# страшного»: клиент, уже давший телефон, считал разговор законченным и
# уходил, а заявка ждала его следующей реплики и не создавалась (бой
# 02.10.2026, сессия 7180dbf55364). Теперь после второй просьбы план идёт
# дальше тем же ходом.
_MAX_NAME_ATTEMPTS = MAX_FIELD_ATTEMPTS - 1


def _contact_asked_enough(attempts: dict[str, int] | None, path: str) -> bool:
    if path == "client.name":
        return int((attempts or {}).get(path) or 0) >= _MAX_NAME_ATTEMPTS
    return _asked_too_many_times(attempts, path)


def _missing_post_match_fields(
    matrix: Matrix, matched: RouteStatus, facts: dict[str, Any]
) -> list[str]:
    """Возвращает настроенные маршрутные уточнения, которые ещё не собраны."""
    return [
        path
        for path in matched.route.post_match_fields
        if path in matrix.fields and get_fact(facts, path) in (None, "")
    ]
