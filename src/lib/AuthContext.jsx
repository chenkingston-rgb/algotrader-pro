import { createContext, useContext, useState, useEffect } from "react";
import { base44 } from "@/api/base44Client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [isLoadingAuth, setIsLoadingAuth] = useState(true);
  const [isLoadingPublicSettings, setIsLoadingPublicSettings] = useState(false);
  const [authError, setAuthError] = useState(null);

  const navigateToLogin = () => {
    window.location.href = "/login";
  };

  useEffect(() => {
    // The getLivePortfolio function is public (no auth needed)
    // so we just skip auth loading entirely for this dashboard
    setIsLoadingAuth(false);
  }, []);

  return (
    <AuthContext.Provider value={{ isLoadingAuth, isLoadingPublicSettings, authError, navigateToLogin }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
