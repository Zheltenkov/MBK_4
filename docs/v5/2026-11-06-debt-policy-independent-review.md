# Независимая проверка долговой policy, этап2

Дата: 06.11.2026. Scope: локальная read-only долговая ветка по текущему плануv2.2.
Критик читает код/контракты, проверяет adversarial cases и own report; runtime
native lab и production не меняет, внешние lookup/поставки не выполняет.

## Финальный независимый вердикт

**PASS для локального read-only этапа2 — долговой политики вопросов.**
Вход, отдельный planner/derive, точность суммы, сохранение неизвестного,
подтверждённые источники и запрет отправок проверены в actual core/HTTP paths.
Найденные first-pass и integration дефекты исправлены и независимо перепроверены.
Новых blocking findings в проверенной границе не осталось. Это не production
readiness, не присвоение категории CRM и не разрешение передать лид партнёрам.

Критик повторно запустил212 связанных тестов:212 passed,2.04s. Итоговый corpus
независимо сверён: baseline90 passed;23/23 targeted mutants killed, fixture/
collection errors0;103 copied live source/data SHA256 совпали, mismatch0.
Root завершил весь repository gate: RuffPASS, Pyright0, pytest2805 passed/
1 skipped,102.83s. Нативный lab и production в этом этапе не изменялись.

| Claim | Проверенный итог | Граница |
|---|---|---|
| Вход по просьбе/сигналу | Explicit request или sealed live AuthorizedApplication hint того же binding; неоднозначность уточняется | Не полное понимание любой русской формулировки; URL/UTM/modelgoal не authority |
| Сумма долга | Scalar/range/unknown/declined различаются, salary/newcredit/foreigncurrency не debtRUB | Валюта только RUB, без FX/юридической оценки |
| Короткая анкета | Debt→region→неизвестные контакты; amount/age/auto/creditcollateral не спрашиваются | Минимальная read-only политика, не специальный production БФЛ DTO |
| Truthfulness | No fake zero/history/assets/contacts; bounded incomplete не «собрано» | Raw server facts сохраняются приватно, model projection не анонимизирует storage |
| Restore/flagoff/отказ | Policy и hold сохраняются; обратно в обычный credit planner не уходят | Снятие hold и delivery owner — следующий отдельный контракт |
| HTTP/core | Four-turn SSE завершился collected_held/not_sent, source0; direct full/abandoned и callback блокируются | Модель/гео/CRM заменены spies на I/O boundary; production не проверен |

Ниже сохранены исторические checkpoints, включая отрицательные probes и их
закрытие. Они не заменяют этот итоговый вердикт.

## Предстартовый контракт

Это отдельная deterministic policy вопросов, ортогональная existing/off_topic
entry routing, а не новый CRM продукт или разрешение отправить лид.

1. Debt включается по явной просьбе самого клиента о списании/банкротстве/
   реструктуризации либо по типизированному подтверждённому current-source
   signal. Browser anketa/URL/UTM, старый lookup, сумма долга>0 или один
   model-extracted client.goal не дают такого полномочия.
2. Отрицание, цитата, чужой долг, общий вопрос, намерение взять новый кредит
   для погашения и конфликт cash/debt не превращаются в silent debt route.
   Неоднозначность требует короткого уточнения, результат — закрытый mode/reason.
3. Policy применяется до derive/planner и при каждом replan. Debt planner
   допускает только debt family и необходимые вопросы; не спрашивает новый
   request.amount, возраст/детали авто и не возвращается в кредит/залог из-за
   наличия имущества. Факты не заменяются synthetic amount0/age/noauto.
4. Known debt scalar сохраняется при «нет действующих кредитов». Unknown,
   declined, not_asked и interval не становятся0/false/exact lower bound.
   Повтор диапазона после уточнения не даёт права выбрать его нижнюю границу.
   Явная последующая точная коррекция сохраняется с provenance/conflict handling.
5. Не подставляются income0, has_overdue/9257 или юридическая пригодность только
   из интереса к БФЛ. Property count, ипотека и желание сохранить — разные facts.
6. Band189999/190000/299999/300000 — локальный консультационный диапазон по
   бизнес-контракту, а не accepted/qualified/покупатель/юридическая рекомендация.
   Это не override актуальных правил/кампаний production CRM.
7. Policy version/selection source/reason и durable debt-hold сохраняются в
   session state. Flagoff, restore/replan/смена намерения не снимают hold сами.
   Полная, abandoned, workflow, site-consent и локальная delivery проверяются
   в реальных sinks, не только в prompt/кнопках. Legacy standard без debt
   и выключенный feature сохраняют прежнее поведение.
8. Свободные тексты/история не становятся системными инструкциями; stage1
   privacy и immutable current-context guards сохраняются. Известный authorized
   телефон не спрашивается повторно, но наличие телефона не означает consent.

Это blocker-sized требования к новой policy/integration, не требование
переписать общий planner/reducer или до этапа2 построить production backend.

## Риски по текущим исходникам до патча

| Приоритет | Concrete source path | Требуемая узкая защита |
|---|---|---|
| P1 | routing/derive.py: has_active_credits=False заполняет отсутствующие debt.total0 и credit_history=no_overdue | Debt-specific bypass/derive policy; legacy positive control оставить |
| P1 | routing/planner.py plan оценивает все routes и fixed script, не читает goal/intent | Policy-specific plan/view на каждом prepare/replan, только debt routes |
| P1 | core.py при выключенном entry flag удаляет state.entry; такой же rollout pattern для debt снял бы запрет | Отдельный persisted debt marker/ever-debt hold, не удаляется при flagoff |
| P2 | Matrix comments58 обещают intent-aware amount/age skip, actual planner его не реализует | Проверять actual fields/routes, не комментарий или отсутствие одной кнопки |
| P2 | interpretation.policies RANGE/VAGUE→AMBIGUOUS; reducer после already_clarified принимает scalar candidate.value | Debt-only precision/range reconciliation; repeated interval не scalar |
| P2 | Assets маршруты недвижимости стоят выше БФЛ в общей матрице | Debt family filter независимо от property facts/общего приоритета |

Known positive debt derive не перезаписывает сам (_fill только пустое), но
unknown debt у debt-клиента он превращает в0. Этот нюанс важно сохранить в
тесте: нельзя заявлять, что любой false activecredits уже стирает known scalar.

## Независимые adversarial controls после интеграции

- Positive explicit request/trusted typed signal; negative URL/UTM/history/
  browsergoal/modelgoal/numberalone и cash-refinance request.
- Negation, quote, third-party и mixed intention; existing/off_topic не
  подменяются новым questionnaire без согласованной смены контекста.
- Unknown/scalar/zero/range/declined, activecreditsFalse, repeated range,
  exact later correction; boundary189999/190000/299999/300000.
- Prepare+replan+restore+flagoff: skipped questions действительно не появляются,
  original facts сохранены, debt не становится collateral/consumer.
- Full/abandoned/site-consent/workflow/core delivery held, включая malformed
  persisted marker и old sessions; legacy positive control подтверждает, что
  тест не «запретил вообще всё».
- Actual model messages и финальные replies: нет raw PII из backend, ложной
  отправки/одобрения/банкротства или бесплатной юридической гарантии.
- Mutation corpus: exact anchors, зелёный baseline, invalid fixture/collection
  не killed, source SHA совпадают; score не объявляется полнотой всех дефектов.

Реализация на этом checkpoint ещё не оценена; финальный PASS пока не выставлен.

## Первый проход debt_policy.py

Форма API подходит: strict frozen versioned DebtPolicyState, отдельный
DebtObservation(exact/range/unknown), sealed factory из live AuthorizedApplication,
policy-key presence как durable hold, отдельный Plan без CRM matched/statuses,
копирующий derive без activecredits shortcut, fixed replies без ссылок/одобрений.
Реальные negative probes выявили узкие дефекты до интеграции:

| Приоритет | Probe | Фактический first-pass результат | Требуемое поведение |
|---|---|---|---|
| P1 | «У меня есть долг, зарплата400тысяч» | exact debt400000 | Не брать income за долг |
| P1 | «Долги есть, хочу кредит300тысяч» | exact debt300000 | Desired credit amount отдельно от totaldebt |
| P1 | Trusted current debt hint + текущий отказ/cash conflict | Hint выбирает debt прежде проверки отказа | Clarify/cancelled-held, не принуждать текущего клиента |
| P2 | «Если я хочу банкротство, что это значит?» | explicit | Hypothetical/general question→clarify |
| P2 | «Он написал: хочу списать долги» | explicit | Reported third-party speech→clarify |
| P2 | «Не уверен, хочу ли я банкротство» | explicit | Неоднозначность→clarify |
| P2 | Active debt + «Банкротство мне не нужно» | Остаётся debt | Остановить/уточнить с сохранённым hold |
| P2 | Bare400000 после вопроса «одним числом в рублях» | unknown | Согласовать parser с указанными единицами или честно запросить обозначение единиц |

Отдельный interface guard: TrustedDebtSignal должен нести opaque verified
binding digest, чтобы hint из applicationA нельзя было случайно применить
к sessionB. Публичная атака через Python object не заявляется; root inline hook
должен фиксировать source signal на attach, не выводить его из mutablefacts.goal.

Pure-code legacy controls также воспроизведены: повтор RANGE candidate200000
после already_clarified сохранился как scalar200000; derive false/noamount
дал0/no_overdue, false/known400k оставил400k и добавилno_overdue. Нативный lab
и внешние API в этих probes не использовались. Исполнитель/root/QA уведомлены;
эти first-pass findings пока ожидают исправления и повторной проверки.

## Повторный review после исправлений

Все перечисленные first-pass probes повторены на actual module: salary400k и
desiredcredit300k дают unknown, а не debt; conditional/reported/uncertain/
relative-target/ASCIIquoted просьбы дают clarify. «Хочу списать задолженность»
распознаётся как explicit. Late refusal даёт cancelled-held. Asked bare400000
стал exact без множителя; маленькое число без понятных единиц остаётся uncertain.
Повреждённый persisted{} стал restored_invalid/clarify. Region value с conflict
или needs_confirmation требует нового вопроса. Эти findings **закрыты**.

Trusted signal теперь sealed и связан с opaque binding digest проверенной
current application. Root вызывает factory только на live authorized attach,
до dialogue updates; поздний mutable/model goal не переиздаёт trusted hint.
Incoming client refusal/cash conflict не подавляется прошлым hint.

Root workflow дополнительно просмотрен: новый policy marker публикуется и
persist-ится до background extraction; prepare и replan используют общий policy
gate. Debt derive/reconciliation восстанавливает original facts и принимает
новую сумму только из grounded DebtObservation. No active credits не заполняет
неизвестное0/no_overdue, interval не становится scalar после повторов; old
scalar остаётся историей, при uncertainty planner требует уточнение.

### Закрытые integration findings

- First-pass generic asked-copy принимал invented phone/name/region из модели.
  Root убрал эти копирования. Contacts принимаются только local PII parser,
  region — classify_region_confident без default RF_other. На «Не скажу»
  fake region теперь отсутствует, status needs_confirmation.
- Первое исправление ещё оставляло fake model contact status/event при пустых
  actual facts. Критик воспроизвёл; root теперь выставляет known только реально
  local-grounded accepted paths и не сохраняет отклонённые model events.
  Повтор: fake phone на «Номер не дам» — facts{}, status отсутствует, events0.
- Malformed raw questionnaire_policy мог попасть в diagnostic snapshot.
  Context projection теперь parse-ит его в закрытый DTO. Actual snapshot probe:
  private marker и phone отсутствуют, original server state не изменён.
- Owner/root дополнительно нашли foreign-currency multiplier. Новый narrow
  guard возвращает unknown для USD/EUR/доллар/евро/$/€, без конвертации;
  independently повторены400тысUSD/EUR→unknown и400тысRUB→exact400000.

Root renderer/stream/commit используют deterministic debt replies, без обычных
credit tails, offer URL или CRM matched. Conversation LLM в этом режиме не
придумывает вопросы/итог; существующий extractor остаётся под privacy boundary
и его неподтверждённые изменения отбрасываются. После ограниченного числа
неответов завершение incomplete-held не выдаётся за собранные контакты.

### Проверенный checkpoint тестов

Критик самостоятельно запустил tests/test_debt_policy.py,
tests/test_debt_policy_acceptance.py, tests/test_context_attachment_http.py:
**204 passed,2.76s** до последней узкой currency-only delta. После её принятия
independent pure currency probes также прошли; финальные gates/mutation corpus
проверяются по замороженному исходнику. Пока они не завершены, итоговый PASS
для всего этапа не выставлен.

## Финальная инструментальная сверка

После последней currency delta критик ещё раз выполнил actual unit90+
independent acceptance90+HTTP32: **212 passed,2.04s**. Прочитан и отдельно
исполнен положительный four-turn HTTP/SSE test: просьба+debt400k→Moscow→name→
phone→collected_held. Каждый следующий field проверяется, hold установлен на
каждом шаге, CRM status not_sent/source calls0. Conversation model и delivery
spies немедленно падали бы при вызове. Успешный test не использовал fake
workflow acceptance или заранее вставленный итог.

Latest mutation artifact logs/debt-policy-2026-11-06/guard-mutations-final.json
проверен независимо. Имена23 mutants точно соответствуют текущему MUTATIONS;
baseline90 exit0/errors0. Все23 имеют exit1, непустые test-body failures,
errors0. Exact anchors и смысл failed test names просмотрены: границы190/300,
boolean money, interval, corrupt restore, hold, defaultoff, extractor poisoning,
prepare/replan/derive, seal/binding/expiry, refusal, privacy и currency действительно
наблюдаются assertions/spy counters. Признаков false killed из-за collection/
fixture failures в этом corpus нет.

Runner наследует уже проверенный isolated-copy harness: green baseline,
anchor count1, pre-import network guard лишь на собственные loopback fixtures,
без .env/logs/DB checkout. Тип XML failure не классифицируется: общий вывод
«все будущие runtime failures эквивалентны assertion» недопустим. Score23/23
относится только к этим целевым мутациям и не является полнотой NLU/security.

103 live copied file SHA256 пересчитаны критиком:0mismatch. Два остальных
snapshot entries — generated sitecustomize/conftest, не checkout files.
Currency-замороженный debt_policy.py SHA
d67e6edbaef88504983d3cafc7157caea2a7bedaf3e6794378df9f6350308aea покрыт
финальным corpus; ранние14/21/22 mutation snapshots не выдаются за latest23.

Root сообщил полный зелёный repository run2805 passed/1 skipped102.83s,
RuffPASS/Pyright0. Критик не дублировал этот дорогой полный run: independently
проверены212 relevant tests, источники, negative controls и corpus integrity.
Старый stage1 mutation regression запускается отдельно на текущем core, без
перезаписи первоначального доказательства stage1.

До live остаются backend ownership/source/issuer/CAS условия ADR0004, интеграция
виджета/страницы и отдельные consent/sender/receipt. Наличие имущества, готовность
к процедуре или local band не снимают hold и не подтверждают CRM/юридическую
применимость. Необычная compound формулировка может потребовать повторного
уточнения; автоматическое угадывание вместо него намеренно не заявляется.
