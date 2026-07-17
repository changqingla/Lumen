/**
 * 路由守卫组件
 * 保护需要登录才能访问的路由
 */
import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';

import { canAccessProtectedRoute } from '@/app/lib/route-access';
import { readAuthToken, subscribeAuthSessionReset } from '@/shared/lib/auth-runtime';
import { useGuestMode } from '@/shared/hooks/useGuestMode';

interface ProtectedRouteProps {
  children: React.ReactNode;
  allowGuest?: boolean;
}

export default function ProtectedRoute({ children, allowGuest = false }: ProtectedRouteProps) {
  const [token, setToken] = useState(() => readAuthToken());
  const { isGuestMode } = useGuestMode();

  useEffect(() => subscribeAuthSessionReset(() => {
    setToken(readAuthToken());
  }), []);

  if (!canAccessProtectedRoute({ authToken: token, isGuestMode, allowGuest })) {
    // 未登录，重定向到登录页
    return <Navigate to="/auth" replace />;
  }
  
  // 已登录，渲染子组件
  return <>{children}</>;
}
