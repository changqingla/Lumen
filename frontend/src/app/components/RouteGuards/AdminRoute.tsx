/**
 * 管理员路由守卫
 * 只允许管理员访问特定路由
 */
import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { authAPI } from '@/shared/api/client';
import { useGuestMode } from '@/shared/hooks/useGuestMode';

interface AdminRouteProps {
  children: React.ReactNode;
}

export default function AdminRoute({ children }: AdminRouteProps) {
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const { isGuestMode } = useGuestMode();

  useEffect(() => {
    if (isGuestMode) {
      setLoading(false);
      setIsAdmin(true);
      return;
    }
    checkAdminStatus();
  }, [isGuestMode]);

  const checkAdminStatus = async () => {
    try {
      const profile = await authAPI.getMe();
      setIsAdmin(profile.is_admin);
    } catch (error) {
      console.error('Failed to check admin status:', error);
      setIsAdmin(false);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        color: '#64748b',
      }}>
        加载中...
      </div>
    );
  }

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
