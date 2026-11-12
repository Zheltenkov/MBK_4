import { useEffect, useRef, useState } from 'react';
import AuthScreen from './components/AuthScreen.jsx';
import LandingScreen from './components/LandingScreen.jsx';
import AnketaForm from './components/AnketaForm.jsx';
import ChatView from './components/ChatView.jsx';
import {
  clearSessionId,
  createSession,
  getAuthStatus,
  getSessionState,
  loadSessionId,
  login,
  logout,
  saveSessionId,
} from './api.js';

const SCREEN = { LANDING: 'landing', ANKETA: 'anketa', CHAT: 'chat' };

export default function App() {
  const [screen, setScreen] = useState(SCREEN.LANDING);
  const [sessionId, setSessionId] = useState(null);
  const [initialState, setInitialState] = useState(null);
  const [bootError, setBootError] = useState(null);
  const [booting, setBooting] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  // Посетитель без пароля, пришедший по ссылке. Кнопки «выйти» у него нет:
  // выходить некуда, а форма входа для него бессмысленна.
  const [isPublicChat, setIsPublicChat] = useState(false);
  const [authError, setAuthError] = useState(null);
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [chatStarting, setChatStarting] = useState(false);
  // State обновляется только на следующем render. Ref закрывает окно, в котором
  // быстрый повторный клик успевал отправить второй POST /api/session.
  const chatStartInFlightRef = useRef(false);

  // На старте сначала проверяем cookie-сессию, затем восстанавливаем чат.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const auth = await getAuthStatus();
        // В открытом режиме клиент приходит по кнопке с сайта МБК и пароля не
        // знает: форму входа ему показывать нельзя, войти через неё некому.
        if (!auth.ok && !auth.public_chat) throw new Error('not authenticated');
        if (cancelled) return;
        setIsPublicChat(Boolean(auth.public_chat) && !auth.ok);
        setIsAuthenticated(true);
        await restoreSession(cancelled);
      } catch {
        clearSessionId();
        if (!cancelled) setIsAuthenticated(false);
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const restoreSession = async (cancelled) => {
    const stored = loadSessionId();
    if (!stored) return;
    try {
      const { state } = await getSessionState(stored);
      if (cancelled) return;
      setSessionId(stored);
      setInitialState(state);
      setScreen(SCREEN.CHAT);
    } catch {
      // 404 или другая ошибка — сессию серверная сторона не помнит, начинаем с нуля.
      clearSessionId();
    }
  };

  const handleLogin = async (username, password) => {
    setAuthError(null);
    setAuthSubmitting(true);
    try {
      await login(username, password);
      setIsAuthenticated(true);
      await restoreSession(false);
    } catch (e) {
      setAuthError(e.message || String(e));
    } finally {
      setAuthSubmitting(false);
    }
  };

  const startChat = async (anketa) => {
    if (chatStartInFlightRef.current) return;
    chatStartInFlightRef.current = true;
    setChatStarting(true);
    setBootError(null);
    try {
      const { session_id, state } = await createSession(anketa);
      saveSessionId(session_id);
      setSessionId(session_id);
      setInitialState(state);
      setScreen(SCREEN.CHAT);
    } catch (e) {
      setBootError(e.message || String(e));
    } finally {
      chatStartInFlightRef.current = false;
      setChatStarting(false);
    }
  };

  const resetSession = () => {
    clearSessionId();
    setSessionId(null);
    setInitialState(null);
    setScreen(SCREEN.LANDING);
  };

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      // Даже если сервер не ответил, локально закрываем UI-сессию.
    }
    clearSessionId();
    setSessionId(null);
    setInitialState(null);
    setScreen(SCREEN.LANDING);
    setIsAuthenticated(false);
    setAuthError(null);
  };

  if (booting) {
    return <FullPageSpinner label="Открываем сессию…" />;
  }

  if (!isAuthenticated) {
    return <AuthScreen onLogin={handleLogin} error={authError} isSubmitting={authSubmitting} />;
  }

  return (
    <div className="h-full flex flex-col">
      <Header
        onReset={screen === SCREEN.CHAT ? resetSession : null}
        onLogout={isPublicChat ? null : handleLogout}
      />
      <main className="flex-1 overflow-hidden">
        {screen === SCREEN.LANDING && (
          <LandingScreen
            onChooseAnketa={() => setScreen(SCREEN.ANKETA)}
            onSkipAnketa={() => startChat(null)}
            error={bootError}
            isStarting={chatStarting}
          />
        )}
        {screen === SCREEN.ANKETA && (
          <AnketaForm
            onCancel={() => setScreen(SCREEN.LANDING)}
            onSubmit={(anketa) => startChat(anketa)}
            submitError={bootError}
            isSubmitting={chatStarting}
          />
        )}
        {screen === SCREEN.CHAT && sessionId && (
          <ChatView
            key={sessionId}
            sessionId={sessionId}
            initialState={initialState}
            onReset={resetSession}
          />
        )}
      </main>
    </div>
  );
}

function Header({ onReset, onLogout }) {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-xl">💬</span>
          <span className="font-semibold text-slate-800">МБК — Ассистент</span>
        </div>
        <div className="flex items-center gap-3">
          {onReset && (
            <button onClick={onReset} className="text-sm text-slate-500 hover:text-slate-700">
              Новая сессия
            </button>
          )}
          {onLogout && (
            <button onClick={onLogout} className="text-sm text-slate-500 hover:text-slate-700">
              Выйти
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

function FullPageSpinner({ label }) {
  return (
    <div className="h-full flex items-center justify-center text-slate-500">
      <div className="flex items-center gap-2">
        <Spinner />
        <span>{label}</span>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-5 w-5 animate-spin text-brand-600" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
    </svg>
  );
}
