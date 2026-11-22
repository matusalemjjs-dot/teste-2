"use client";

import type { SupabaseClient, Session } from "@supabase/supabase-js";
import { createClient } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { AUTH_MODE, SUPABASE_ANON_KEY, SUPABASE_URL, TEST_TOKEN_KEY } from "./config";

type AuthState = {
  ready: boolean;
  token: string | null;
  email: string | null;
  testMode: boolean;
  configured: boolean;
  signIn: (email: string, password: string) => Promise<string | null>;
  signUp: (email: string, password: string) => Promise<string | null>;
  sendReset: (email: string) => Promise<string | null>;
  updatePassword: (password: string) => Promise<string | null>;
  /** Sai da conta. Com `redirectTo`, navega para lá sem que as rotas protegidas mandem para o login antes. */
  signOut: (redirectTo?: string) => Promise<void>;
  recovery: boolean;
  leaving: boolean;
};

const AuthContext = createContext<AuthState | null>(null);

let supabase: SupabaseClient | null = null;
function getSupabase(): SupabaseClient | null {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null;
  supabase ??= createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });
  return supabase;
}

// Mensagens do Supabase traduzidas para o usuário (sem expor detalhes técnicos).
function translate(message: string | undefined): string {
  const m = (message ?? "").toLowerCase();
  if (m.includes("invalid login credentials")) return "E-mail ou senha incorretos.";
  if (m.includes("email not confirmed")) return "Confirme seu e-mail antes de entrar. Verifique sua caixa de entrada.";
  if (m.includes("already registered")) return "Já existe uma conta com este e-mail.";
  if (m.includes("password should be at least")) return "A senha precisa ter pelo menos 6 caracteres.";
  if (m.includes("rate limit")) return "Muitas tentativas. Aguarde alguns minutos e tente novamente.";
  return "Não foi possível concluir a operação. Tente novamente.";
}

function decodeEmail(token: string): string | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.email === "string" ? payload.email : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [testToken, setTestToken] = useState<string | null>(null);
  const [recovery, setRecovery] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const testMode = AUTH_MODE === "test";
  const client = testMode ? null : getSupabase();

  useEffect(() => {
    if (testMode || !client) {
      Promise.resolve().then(() => {
        if (testMode) {
          try {
            setTestToken(window.localStorage.getItem(TEST_TOKEN_KEY));
          } catch {
            setTestToken(null);
          }
        }
        setReady(true);
      });
      return;
    }
    client.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data } = client.auth.onAuthStateChange((event, s) => {
      setSession(s);
      if (event === "PASSWORD_RECOVERY") setRecovery(true);
    });
    return () => data.subscription.unsubscribe();
  }, [client, testMode]);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!client) return "Autenticação não configurada.";
    const { error } = await client.auth.signInWithPassword({ email, password });
    return error ? translate(error.message) : null;
  }, [client]);

  const signUp = useCallback(async (email: string, password: string) => {
    if (!client) return "Autenticação não configurada.";
    const { error } = await client.auth.signUp({
      email, password, options: { emailRedirectTo: `${window.location.origin}/entrar` },
    });
    return error ? translate(error.message) : null;
  }, [client]);

  const sendReset = useCallback(async (email: string) => {
    if (!client) return "Autenticação não configurada.";
    const { error } = await client.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/redefinir-senha`,
    });
    return error ? translate(error.message) : null;
  }, [client]);

  const updatePassword = useCallback(async (password: string) => {
    if (!client) return "Autenticação não configurada.";
    const { error } = await client.auth.updateUser({ password });
    if (!error) setRecovery(false);
    return error ? translate(error.message) : null;
  }, [client]);

  const signOut = useCallback(async (redirectTo?: string) => {
    if (redirectTo) setLeaving(true);
    const go = () => { if (redirectTo) window.location.replace(redirectTo); };
    if (testMode) {
      try {
        window.localStorage.removeItem(TEST_TOKEN_KEY);
      } catch {
        /* ignorado */
      }
      setTestToken(null);
      go();
      return;
    }
    await client?.auth.signOut();
    go();
  }, [client, testMode]);

  const token = testMode ? testToken : session?.access_token ?? null;
  const value = useMemo<AuthState>(() => ({
    ready, token, testMode, recovery, leaving,
    configured: testMode || client !== null,
    email: testMode ? (token ? decodeEmail(token) : null) : session?.user.email ?? null,
    signIn, signUp, sendReset, updatePassword, signOut,
  }), [ready, token, testMode, recovery, leaving, client, session, signIn, signUp, sendReset, updatePassword, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth deve ser usado dentro de AuthProvider");
  return ctx;
}
