import { useState } from 'react';

export default function AuthScreen({ onLogin, error, isSubmitting }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = (event) => {
    event.preventDefault();
    onLogin(username, password);
  };

  return (
    <div className="flex min-h-full items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg bg-brand-600 text-lg font-semibold text-white shadow-sm">
            MBK
          </div>
          <h1 className="mt-4 text-2xl font-semibold text-slate-900">Ассистент МБК</h1>
          <p className="mt-1 text-sm text-slate-500">Вход для сотрудников</p>
        </div>

        <form onSubmit={handleSubmit} className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <div className="space-y-4">
            <div>
              <label htmlFor="auth-username" className="label-base">Логин</label>
              <input
                id="auth-username"
                type="text"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="input-base"
                placeholder="Введите логин"
                disabled={isSubmitting}
                autoFocus
              />
            </div>

            <div>
              <label htmlFor="auth-password" className="label-base">Пароль</label>
              <input
                id="auth-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="input-base"
                placeholder="Введите пароль"
                disabled={isSubmitting}
              />
            </div>
          </div>

          {error && (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isSubmitting || !username.trim() || !password}
            className="btn-primary mt-5 w-full"
          >
            {isSubmitting ? 'Входим...' : 'Войти'}
          </button>
        </form>
      </div>
    </div>
  );
}
