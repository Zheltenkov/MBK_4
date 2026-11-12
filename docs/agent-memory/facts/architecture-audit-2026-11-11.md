---
name: architecture-audit-2026-11-11
description: Read-only аудит архитектуры: разрешения при pause, F admission, latency и runtime/storage gates
metadata:
  type: reference
---

Отчёт и предложенный порядок изменений — `docs/v5/2026-11-11-architecture-audit.md`.
Это рекомендации, а не принятый редизайн: рабочий код при аудите не менялся.
Артефакты агентов, probes и независимая критика —
`C:/Users/zvp/AppData/Local/MBK-architecture-audit-20261111/`.
Первый кандидат — согласование допуска контакта при pause; actual No и доставка
не объявлены обойдёнными. Локальные timing/probes не являются production SLA.

A1 реализован после аудита: `docs/v5/2026-11-11-paused-contact-fix.md` и уточнение
в ADR-0009. Остальные кандидаты аудита этим не объявлены исправленными.

A2: общий допуск утверждений и проверка практических сведений —
`docs/v5/2026-11-11-factual-admission-fix.md`, решение в ADR-0009.
Полный local/Polza replay отделён от синтетических branch proofs;
оставшиеся C/fallback/latency дефекты указаны в отчёте. Это не общий production PASS.

Продолжение трёх замечаний A2 —
`docs/v5/2026-11-11-phone-recovery-bankruptcy-fix.md` и ADR-0009:
phone-storage guard, узкая проекция подтверждённой суммы после сбоя и
source scope положительных утверждений о списании. Общая latency этим не закрыта.

Relevance/recovery continuation — `docs/v5/2026-11-11-relevance-recovery-fix.md`
и ADR-0009: closed-field requests, private known-contact proof, практический
unknown при сбое, contextual canonical fallback. R1 full17 сохранён отдельно
от R2 final full9; JEV оценён только как shadow candidate и в runtime не подключался.

Legal criteria + latency continuation —
`docs/v5/2026-11-11-legal-sources-latency-fix.md` и ADR-0009.
Source assessment evidence и безопасное исключение C для отброшенного G;
полный13 с No/restart, но human availability PARTIAL из-за C deadlines.
