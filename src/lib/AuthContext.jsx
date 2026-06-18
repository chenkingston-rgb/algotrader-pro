import React, { createContext, useState, useContext, useEffect } from 'react';
import { base44 } from '@/api/base44Client';

const AuthContext = createContext();

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [isLoadingAuth, setIsLoadingAuth] = useState(true);
  const [isLoadingPublicSettings, setIsLoadingPublicSettings] = useState(false);
  const [authError, setAuthError] = useState(null);

  useEffect(() => {
    base44.auth.me()
      .then(u => {
        setUser(u);
        setIsLoadingAuth(false);
      })
      .catch(err => {
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail || '';
        if (detail.includes('not registered')) {
          setAuthError({ type: 'user_not_registered' });
        } else if (status === 401 || status === 403) {
          setAuthError({ type: 'auth_required' });
        } else {
          setAuthError({ type: 'auth_required' });
        }
        setIsLoadingAuth(false);
      });
  }, []);

  const navigateToLogin = () => {
    base44.auth.redirectToLogin(window.location.href);
  };

  return (
    <AuthContext.Provider value={{ user, setUser, isLoadingAuth, isLoadingPublicSettings, authError, navigateToLogin }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};