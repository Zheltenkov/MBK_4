export default function LandingScreen({ onChooseAnketa, onSkipAnketa, error, isStarting = false }) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-4 py-10 sm:py-16">
        <div className="text-center">
          <h1 className="text-2xl sm:text-3xl font-semibold text-slate-900">
            Ассистент МБК
          </h1>
        </div>

        {error && (
          <div className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          <button
            onClick={onChooseAnketa}
            disabled={isStarting}
            className="card group cursor-pointer p-6 text-left hover:border-brand-500 hover:shadow-md transition"
          >
            <div className="text-2xl">📋</div>
            <h2 className="mt-3 text-lg font-semibold text-slate-900">С анкетой</h2>
            <p className="mt-1 text-sm text-slate-600">
              Заполнить короткую анкету. Это ускорит подбор — не придётся переспрашивать
              базовые вещи в чате.
            </p>
            <div className="mt-4 text-sm font-medium text-brand-600 group-hover:text-brand-700">
              Заполнить анкету →
            </div>
          </button>

          <button
            onClick={onSkipAnketa}
            disabled={isStarting}
            className="card group cursor-pointer p-6 text-left hover:border-brand-500 hover:shadow-md transition"
          >
            <div className="text-2xl">💬</div>
            <h2 className="mt-3 text-lg font-semibold text-slate-900">Сразу в чат</h2>
            <p className="mt-1 text-sm text-slate-600">
              Опишу ситуацию словами, ассистент сам задаст нужные вопросы. Подойдёт,
              если деталей сейчас под рукой нет.
            </p>
            <div className="mt-4 text-sm font-medium text-brand-600 group-hover:text-brand-700">
              {isStarting ? 'Открываю чат…' : 'Начать без анкеты →'}
            </div>
          </button>
        </div>
      </div>
    </div>
  );
}
