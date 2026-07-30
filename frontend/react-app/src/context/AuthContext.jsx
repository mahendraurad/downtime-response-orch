import React, { createContext, useState, useContext, useCallback, useEffect } from 'react';
import { getToken, setToken, clearToken } from '../api/http';
import { getMe } from '../api/auth';

export const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  // user shape: { username, display_name, role, allowed_personas }
  const [user, setUser] = useState(null);
  // true while resolving an existing token on mount — prevents a flash of the login screen
  const [loading, setLoading] = useState(true);

  // On mount: try to rehydrate session from a token already in localStorage
  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    getMe()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  // Listen for 401 events dispatched by authedFetch
  useEffect(() => {
    const onExpired = () => { clearToken(); setUser(null); };
    window.addEventListener('dro-auth-expired', onExpired);
    return () => window.removeEventListener('dro-auth-expired', onExpired);
  }, []);

  const login = useCallback((data) => {
    setToken(data.access_token);
    setUser({
      username:        data.username,
      display_name:    data.display_name,
      role:            data.role,
      allowed_personas: data.allowed_personas,
    });
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
