import { useMemo, useState } from 'react';

/**
 * Анкета по спецификации (ChatSmart - Application).
 * Все «обязательные» поля верхнего уровня показаны сразу. Подполя раскрываются по answer
 * родительского радио/чекбокса (есть кредиты → блок долгов; авто → блок авто; и т.д.).
 *
 * Состояние формы — плоский dict, который потом маппится в server-friendly anketa
 * с теми же ключами, что используются в state.seed_current_facts.
 */
const CREDIT_TYPES = [
  'Потребительский кредит',
  'Автокредит',
  'Ипотека',
  'Кредитная карта',
  'Микрозайм / МФО',
];

const MARITAL_OPTIONS = ['Холост', 'Женат / Замужем', 'Разведён', 'Вдов'];
const EMPLOYMENT_OPTIONS = ['Найм', 'ИП', 'Самозанятый', 'Пенсионер', 'Безработный'];
const OVERDUE_OPTIONS = ['Нет', '1–30 дней', '31–60 дней', '61–90 дней', 'Более 90 дней'];
const REALTY_KINDS = ['Квартира', 'Дом', 'Апартаменты', 'Таунхаус', 'Коммерческая'];
const OWNERSHIP_FORMS = ['Индивидуальная', 'Долевая', 'Совместная'];
const SHARE_WITH = ['С супругом / супругой', 'С родителями', 'С детьми', 'С другими родственниками', 'С третьими лицами'];
const ENCUMBRANCE_OPTIONS = ['Нет', 'Ипотека', 'Арест', 'Залог по другому займу'];

const currentYear = new Date().getFullYear();
const VEHICLE_YEARS = Array.from({ length: 36 }, (_, i) => String(currentYear - i));

const initialForm = {
  // Top-level
  desired_amount: '',
  full_name: '',
  phone: '',
  birth_date: '',
  registration_address: '',
  addresses_match: true,
  living_address: '',
  marital_status: '',
  has_dependents: null, // true | false
  dependents_count: '',
  employment_type: '',
  organization_name: '',
  position_type: '',
  work_start_year: '',
  monthly_income: '',
  has_current_loans: null,
  debts_total: '',
  monthly_debt_payments: '',
  credit_types: [],
  overdue_duration: '',
  has_car: null,
  vehicle_make: '',
  vehicle_model: '',
  vehicle_year: '',
  vehicle_pledged: null,
  vehicle_loan_balance: '',
  asset_type: '', // 'Недвижимость' | 'Нет активов'
  realty_kind: '',
  realty_region: '',
  realty_ownership: '',
  realty_share_with: '',
  realty_encumbrance: '',
  realty_loan_balance: '',
  rent_expenses: '',
};

export default function AnketaForm({ onCancel, onSubmit, submitError, isSubmitting = false }) {
  const [form, setForm] = useState(initialForm);
  const [touched, setTouched] = useState(false);

  const set = (key, value) => setForm((p) => ({ ...p, [key]: value }));
  const toggleCreditType = (t) =>
    setForm((p) => ({
      ...p,
      credit_types: p.credit_types.includes(t)
        ? p.credit_types.filter((x) => x !== t)
        : [...p.credit_types, t],
    }));

  const errors = useMemo(() => validate(form), [form]);
  const isValid = Object.keys(errors).length === 0;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (isSubmitting) return;
    setTouched(true);
    if (!isValid) {
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    onSubmit(toServerAnketa(form));
  };

  const err = (k) => (touched ? errors[k] : null);

  return (
    <div className="h-full overflow-y-auto">
      <form onSubmit={handleSubmit} className="mx-auto max-w-2xl px-4 py-6 sm:py-10 space-y-5">
        <div className="text-center">
          <h1 className="text-xl sm:text-2xl font-semibold text-slate-900">Анкета</h1>
          <p className="mt-1 text-sm text-slate-600">
            Поля со звёздочкой — обязательны. Чем точнее заполните, тем быстрее подберём вариант.
          </p>
        </div>

        {submitError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {submitError}
          </div>
        )}

        {touched && !isValid && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            Проверьте подсвеченные поля.
          </div>
        )}

        {/* === Сумма === */}
        <Card title="Запрос">
          <Field label="Желаемая сумма, ₽" required error={err('desired_amount')}>
            <input type="number" min="0" step="10000" inputMode="numeric"
                   className="input-base" placeholder="например, 500 000"
                   value={form.desired_amount}
                   onChange={(e) => set('desired_amount', e.target.value)} />
          </Field>
        </Card>

        {/* === Личные данные === */}
        <Card title="Личные данные">
          <Field label="ФИО полностью" required error={err('full_name')}>
            <input type="text" className="input-base" placeholder="Иванов Иван Иванович"
                   value={form.full_name} onChange={(e) => set('full_name', e.target.value)} />
          </Field>
          <Field label="Телефон" required error={err('phone')}>
            <input type="tel" inputMode="tel" className="input-base" placeholder="+7 999 123-45-67"
                   value={form.phone} onChange={(e) => set('phone', e.target.value)} />
          </Field>
          <Field label="Дата рождения" required error={err('birth_date')}>
            <input type="date" className="input-base"
                   value={form.birth_date} onChange={(e) => set('birth_date', e.target.value)} />
          </Field>
          <Field label="Адрес регистрации (до населённого пункта)" required error={err('registration_address')}>
            <input type="text" className="input-base" placeholder="г. Москва, ул. Тверская"
                   value={form.registration_address}
                   onChange={(e) => set('registration_address', e.target.value)} />
          </Field>
          <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
            <input type="checkbox" checked={form.addresses_match}
                   onChange={(e) => set('addresses_match', e.target.checked)}
                   className="rounded border-slate-300" />
            Адрес проживания совпадает с адресом регистрации
          </label>
          {!form.addresses_match && (
            <Field label="Адрес проживания (до населённого пункта)" required error={err('living_address')}>
              <input type="text" className="input-base"
                     value={form.living_address}
                     onChange={(e) => set('living_address', e.target.value)} />
            </Field>
          )}
        </Card>

        {/* === Семья === */}
        <Card title="Семья">
          <Field label="Семейное положение" required error={err('marital_status')}>
            <RadioPills value={form.marital_status} options={MARITAL_OPTIONS}
                        onChange={(v) => set('marital_status', v)} />
          </Field>
          <Field label="Есть иждивенцы?" required error={err('has_dependents')}>
            <YesNoPills value={form.has_dependents} onChange={(v) => set('has_dependents', v)} />
          </Field>
          {form.has_dependents === true && (
            <Field label="Количество иждивенцев" required error={err('dependents_count')}>
              <input type="number" min="1" max="20" className="input-base w-32"
                     value={form.dependents_count}
                     onChange={(e) => set('dependents_count', e.target.value)} />
            </Field>
          )}
        </Card>

        {/* === Занятость и доход === */}
        <Card title="Занятость">
          <Field label="Тип занятости" required error={err('employment_type')}>
            <RadioPills value={form.employment_type} options={EMPLOYMENT_OPTIONS}
                        onChange={(v) => set('employment_type', v)} />
          </Field>
          {form.employment_type && form.employment_type !== 'Безработный' && (
            <>
              <Field label="Официальный доход в месяц, ₽" required error={err('monthly_income')}>
                <input type="number" min="0" step="1000" inputMode="numeric"
                       className="input-base" placeholder="например, 80 000"
                       value={form.monthly_income}
                       onChange={(e) => set('monthly_income', e.target.value)} />
              </Field>
              <Field label="Название организации" hint="необязательно">
                <input type="text" className="input-base"
                       value={form.organization_name}
                       onChange={(e) => set('organization_name', e.target.value)} />
              </Field>
              <Field label="Должность" hint="необязательно">
                <input type="text" className="input-base"
                       value={form.position_type}
                       onChange={(e) => set('position_type', e.target.value)} />
              </Field>
              <Field label="Год начала работы у текущего работодателя" hint="необязательно">
                <input type="number" min="1980" max={currentYear} className="input-base w-32"
                       value={form.work_start_year}
                       onChange={(e) => set('work_start_year', e.target.value)} />
              </Field>
            </>
          )}
        </Card>

        {/* === Кредиты === */}
        <Card title="Действующие кредиты">
          <Field label="Есть ли сейчас кредиты или займы?" required error={err('has_current_loans')}>
            <YesNoPills value={form.has_current_loans} onChange={(v) => set('has_current_loans', v)} />
          </Field>
          {form.has_current_loans === true && (
            <>
              <Field label="Общая задолженность по всем кредитам, ₽" required error={err('debts_total')}>
                <input type="number" min="0" step="10000" inputMode="numeric"
                       className="input-base"
                       value={form.debts_total}
                       onChange={(e) => set('debts_total', e.target.value)} />
              </Field>
              <Field label="Общие ежемесячные выплаты по кредитам, ₽" required error={err('monthly_debt_payments')}>
                <input type="number" min="0" step="1000" inputMode="numeric"
                       className="input-base"
                       value={form.monthly_debt_payments}
                       onChange={(e) => set('monthly_debt_payments', e.target.value)} />
              </Field>
              <Field label="Виды активных кредитов" required error={err('credit_types')}>
                <div className="flex flex-wrap gap-2">
                  {CREDIT_TYPES.map((t) => (
                    <button type="button" key={t}
                            onClick={() => toggleCreditType(t)}
                            className={`pill-radio ${form.credit_types.includes(t) ? 'pill-radio-checked' : ''}`}>
                      {t}
                    </button>
                  ))}
                </div>
              </Field>
              <Field label="Текущая просрочка" required error={err('overdue_duration')}>
                <select className="input-base"
                        value={form.overdue_duration}
                        onChange={(e) => set('overdue_duration', e.target.value)}>
                  <option value="">— выберите —</option>
                  {OVERDUE_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </Field>
            </>
          )}
        </Card>

        {/* === Авто === */}
        <Card title="Автомобиль">
          <Field label="Есть ли автомобиль в собственности?" required error={err('has_car')}>
            <YesNoPills value={form.has_car} onChange={(v) => set('has_car', v)} />
          </Field>
          {form.has_car === true && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Марка" required error={err('vehicle_make')}>
                  <input type="text" className="input-base" placeholder="Toyota"
                         value={form.vehicle_make}
                         onChange={(e) => set('vehicle_make', e.target.value)} />
                </Field>
                <Field label="Модель" required error={err('vehicle_model')}>
                  <input type="text" className="input-base" placeholder="RAV4"
                         value={form.vehicle_model}
                         onChange={(e) => set('vehicle_model', e.target.value)} />
                </Field>
              </div>
              <Field label="Год выпуска" required error={err('vehicle_year')}>
                <select className="input-base w-32"
                        value={form.vehicle_year}
                        onChange={(e) => set('vehicle_year', e.target.value)}>
                  <option value="">—</option>
                  {VEHICLE_YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
                </select>
              </Field>
              <Field label="Авто в залоге у банка?" required error={err('vehicle_pledged')}>
                <YesNoPills value={form.vehicle_pledged} onChange={(v) => set('vehicle_pledged', v)} />
              </Field>
              {form.vehicle_pledged === true && (
                <Field label="Остаток по кредиту на авто, ₽" required error={err('vehicle_loan_balance')}>
                  <input type="number" min="0" step="10000" inputMode="numeric"
                         className="input-base"
                         value={form.vehicle_loan_balance}
                         onChange={(e) => set('vehicle_loan_balance', e.target.value)} />
                </Field>
              )}
            </>
          )}
        </Card>

        {/* === Активы === */}
        <Card title="Имущество и расходы">
          <Field label="Есть ли недвижимость в собственности?" required error={err('asset_type')}>
            <RadioPills value={form.asset_type} options={['Недвижимость', 'Нет активов']}
                        onChange={(v) => set('asset_type', v)} />
          </Field>
          {form.asset_type === 'Недвижимость' && (
            <>
              <Field label="Тип недвижимости" required error={err('realty_kind')}>
                <RadioPills value={form.realty_kind} options={REALTY_KINDS}
                            onChange={(v) => set('realty_kind', v)} />
              </Field>
              <Field label="Регион (до населённого пункта)" required error={err('realty_region')}>
                <input type="text" className="input-base" placeholder="г. Москва"
                       value={form.realty_region}
                       onChange={(e) => set('realty_region', e.target.value)} />
              </Field>
              <Field label="Форма собственности" required error={err('realty_ownership')}>
                <RadioPills value={form.realty_ownership} options={OWNERSHIP_FORMS}
                            onChange={(v) => set('realty_ownership', v)} />
              </Field>
              {(form.realty_ownership === 'Долевая' || form.realty_ownership === 'Совместная') && (
                <Field label="С кем доля" required error={err('realty_share_with')}>
                  <RadioPills value={form.realty_share_with} options={SHARE_WITH}
                              onChange={(v) => set('realty_share_with', v)} />
                </Field>
              )}
              <Field label="Обременение / ограничение" required error={err('realty_encumbrance')}>
                <RadioPills value={form.realty_encumbrance} options={ENCUMBRANCE_OPTIONS}
                            onChange={(v) => set('realty_encumbrance', v)} />
              </Field>
              {(form.realty_encumbrance === 'Ипотека' ||
                form.realty_encumbrance === 'Залог по другому займу') && (
                <Field label="Остаток по кредиту, ₽" required error={err('realty_loan_balance')}>
                  <input type="number" min="0" step="10000" inputMode="numeric"
                         className="input-base"
                         value={form.realty_loan_balance}
                         onChange={(e) => set('realty_loan_balance', e.target.value)} />
                </Field>
              )}
            </>
          )}
          <Field label="Расходы на аренду жилья, ₽/мес" required error={err('rent_expenses')}
                 hint="если не снимаете — поставьте 0">
            <input type="number" min="0" step="1000" inputMode="numeric"
                   className="input-base"
                   value={form.rent_expenses}
                   onChange={(e) => set('rent_expenses', e.target.value)} />
          </Field>
        </Card>

        <div className="flex items-center justify-between pt-2 pb-8">
          <button type="button" onClick={onCancel} disabled={isSubmitting}
                  className="btn-secondary">← Назад</button>
          <button type="submit" disabled={isSubmitting} className="btn-primary">
            {isSubmitting ? 'Открываю чат…' : 'Перейти в чат'}
          </button>
        </div>
      </form>
    </div>
  );
}

// === Вспомогательные компоненты ===
function Card({ title, children }) {
  return (
    <section className="card p-4 sm:p-5 space-y-4">
      <h2 className="text-base font-semibold text-slate-800">{title}</h2>
      {children}
    </section>
  );
}

function Field({ label, required, hint, error, children }) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <label className="label-base">
          {label} {required && <span className="text-brand-600">*</span>}
        </label>
        {hint && <span className="text-xs text-slate-400">{hint}</span>}
      </div>
      {children}
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}

function RadioPills({ value, options, onChange }) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button type="button" key={o}
                onClick={() => onChange(o)}
                className={`pill-radio ${value === o ? 'pill-radio-checked' : ''}`}>
          {o}
        </button>
      ))}
    </div>
  );
}

function YesNoPills({ value, onChange }) {
  return (
    <div className="flex gap-2">
      <button type="button" onClick={() => onChange(true)}
              className={`pill-radio ${value === true ? 'pill-radio-checked' : ''}`}>Да</button>
      <button type="button" onClick={() => onChange(false)}
              className={`pill-radio ${value === false ? 'pill-radio-checked' : ''}`}>Нет</button>
    </div>
  );
}

// === Валидация ===
function validate(f) {
  const e = {};
  if (!f.desired_amount || Number(f.desired_amount) <= 0) e.desired_amount = 'Укажите сумму';
  if (!f.full_name || f.full_name.trim().length < 3) e.full_name = 'Укажите ФИО полностью';
  if (!f.phone || !/^[+]?[\d\s\-()]{10,}$/.test(f.phone)) e.phone = 'Укажите корректный телефон';
  if (!f.birth_date) e.birth_date = 'Укажите дату рождения';
  if (!f.registration_address || f.registration_address.trim().length < 3) e.registration_address = 'Укажите адрес';
  if (!f.addresses_match && (!f.living_address || f.living_address.trim().length < 3))
    e.living_address = 'Укажите адрес проживания';
  if (!f.marital_status) e.marital_status = 'Выберите';
  if (f.has_dependents === null) e.has_dependents = 'Выберите';
  if (f.has_dependents === true && (!f.dependents_count || Number(f.dependents_count) < 1))
    e.dependents_count = 'Укажите количество';
  if (!f.employment_type) e.employment_type = 'Выберите';
  if (f.employment_type && f.employment_type !== 'Безработный'
      && (!f.monthly_income || Number(f.monthly_income) <= 0))
    e.monthly_income = 'Укажите доход';
  if (f.has_current_loans === null) e.has_current_loans = 'Выберите';
  if (f.has_current_loans === true) {
    if (!f.debts_total || Number(f.debts_total) <= 0) e.debts_total = 'Укажите сумму долгов';
    if (!f.monthly_debt_payments || Number(f.monthly_debt_payments) < 0) e.monthly_debt_payments = 'Укажите платежи';
    if (!f.credit_types.length) e.credit_types = 'Выберите хотя бы один тип';
    if (!f.overdue_duration) e.overdue_duration = 'Выберите';
  }
  if (f.has_car === null) e.has_car = 'Выберите';
  if (f.has_car === true) {
    if (!f.vehicle_make) e.vehicle_make = 'Укажите';
    if (!f.vehicle_model) e.vehicle_model = 'Укажите';
    if (!f.vehicle_year) e.vehicle_year = 'Укажите';
    if (f.vehicle_pledged === null) e.vehicle_pledged = 'Выберите';
    if (f.vehicle_pledged === true && (!f.vehicle_loan_balance || Number(f.vehicle_loan_balance) < 0))
      e.vehicle_loan_balance = 'Укажите остаток';
  }
  if (!f.asset_type) e.asset_type = 'Выберите';
  if (f.asset_type === 'Недвижимость') {
    if (!f.realty_kind) e.realty_kind = 'Выберите';
    if (!f.realty_region) e.realty_region = 'Укажите регион';
    if (!f.realty_ownership) e.realty_ownership = 'Выберите';
    if ((f.realty_ownership === 'Долевая' || f.realty_ownership === 'Совместная')
        && !f.realty_share_with) e.realty_share_with = 'Выберите';
    if (!f.realty_encumbrance) e.realty_encumbrance = 'Выберите';
    if ((f.realty_encumbrance === 'Ипотека' || f.realty_encumbrance === 'Залог по другому займу')
        && (!f.realty_loan_balance || Number(f.realty_loan_balance) < 0))
      e.realty_loan_balance = 'Укажите остаток';
  }
  if (f.rent_expenses === '' || Number(f.rent_expenses) < 0) e.rent_expenses = 'Укажите (можно 0)';
  return e;
}

// === Маппинг формы во вложенный объект анкеты, который сядет в seed_current_facts ===
function toServerAnketa(f) {
  const a = {
    desired_amount: Number(f.desired_amount),
    full_name: f.full_name.trim(),
    phone: f.phone.trim(),
    birth_date: f.birth_date,
    registration_address: f.registration_address.trim(),
    addresses_match: f.addresses_match,
    living_address: f.addresses_match ? f.registration_address.trim() : f.living_address.trim(),
    marital_status: f.marital_status,
    has_dependents: !!f.has_dependents,
    employment_type: f.employment_type,
    has_current_loans: !!f.has_current_loans,
    has_car: !!f.has_car,
    asset_type: f.asset_type,
    rent_expenses: Number(f.rent_expenses) || 0,
  };
  if (f.has_dependents) a.dependents_count = Number(f.dependents_count);
  if (f.employment_type && f.employment_type !== 'Безработный') {
    a.monthly_income = Number(f.monthly_income);
    if (f.organization_name) a.organization_name = f.organization_name.trim();
    if (f.position_type) a.position_type = f.position_type.trim();
    if (f.work_start_year) a.work_start_year = Number(f.work_start_year);
  }
  if (f.has_current_loans) {
    a.debts_total = Number(f.debts_total);
    a.monthly_debt_payments = Number(f.monthly_debt_payments);
    a.credit_types = f.credit_types;
    a.overdue_duration = f.overdue_duration;
  }
  if (f.has_car) {
    a.vehicle = {
      make: f.vehicle_make.trim(),
      model: f.vehicle_model.trim(),
      year: Number(f.vehicle_year),
      pledged: !!f.vehicle_pledged,
    };
    if (f.vehicle_pledged) a.vehicle.loan_balance = Number(f.vehicle_loan_balance);
  }
  if (f.asset_type === 'Недвижимость') {
    a.realty = {
      kind: f.realty_kind,
      region: f.realty_region.trim(),
      ownership: f.realty_ownership,
      encumbrance: f.realty_encumbrance,
    };
    if (f.realty_ownership === 'Долевая' || f.realty_ownership === 'Совместная') {
      a.realty.share_with = f.realty_share_with;
    }
    if (f.realty_encumbrance === 'Ипотека' || f.realty_encumbrance === 'Залог по другому займу') {
      a.realty.loan_balance = Number(f.realty_loan_balance);
    }
  }
  return a;
}
