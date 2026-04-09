/**
 * 路由守卫组件
 * 保护需要登录才能访问的路由
 */
import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';

import { readAuthToken, subscribeAuthSessionReset } from '@/shared/lib/auth-runtime';
import { useGuestMode } from '@/shared/hooks/useGuestMode';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const [token, setToken] = useState(() => readAuthToken());
  const { isGuestMode } = useGuestMode();

  useEffect(() => subscribeAuthSessionReset(() => {
    setToken(readAuthToken());
  }), []);

  if (!token && !isGuestMode) {
    // 未登录，重定向到登录页
    return <Navigate to="/auth" replace />;
  }
  
  // 已登录，渲染子组件
  return <>{children}</>;
}
