/**
 * 用户资料管理 Hook
 * 提供用户资料相关的数据和操作方法
 */
import { useState, useEffect, useCallback } from 'react';
import { authAPI } from '@/shared/api/client';
import { useToast } from './useToast';

export interface UserProfile {
  name: string;
  email: string;
  avatar?: string;
  is_member: boolean;
  is_advanced_member: boolean;
  is_admin: boolean;
  member_expires_at?: string;
  organizations?: Array<{
    id: string;
    name: string;
    role: 'owner' | 'member';
  }>;
}

export function useUserProfile() {
  const toast = useToast();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * 加载用户资料
   */
  const fetchProfile = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await authAPI.getMe();
      
      // 计算is_member和is_advanced_member
      const is_member = data.user_level === 'member' || data.user_level === 'premium' || data.is_admin;
      const is_advanced_member = data.user_level === 'premium' || data.is_admin;
      
      const profile = {
        ...data,
        is_member,
        is_advanced_member,
      };
      
      setProfile(profile);
      
      // 更新 localStorage
      const storedProfile = localStorage.getItem('userProfile');
      if (storedProfile) {
        const parsed = JSON.parse(storedProfile);
        localStorage.setItem(
          'userProfile',
          JSON.stringify({ ...parsed, ...profile })
        );
      }
    } catch (err: any) {
      const errorMsg = err.message || '加载用户资料失败';
      setError(errorMsg);
      console.error('Failed to fetch profile:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * 更新用户资料
   */
  const updateProfile = useCallback(
    async (data: { name?: string; avatar?: string }) => {
      try {
        await authAPI.updateProfile(data);
        toast.success('资料更新成功');
        await fetchProfile();
      } catch (err: any) {
        const errorMsg = err.message || '更新资料失败';
        toast.error(errorMsg);
        throw err;
      }
    },
    [toast, fetchProfile]
  );

  /**
   * 上传头像
   */
  const uploadAvatar = useCallback(
    async (file: File) => {
      try {
        const result = await authAPI.uploadAvatar(file);
        toast.success('头像上传成功');
        await fetchProfile();
        return result.url;
      } catch (err: any) {
        const errorMsg = err.message || '头像上传失败';
        toast.error(errorMsg);
        throw err;
      }
    },
    [toast, fetchProfile]
  );

  /**
   * 激活会员
   */
  const activateMembership = useCallback(
    async (code: string) => {
      try {
        await authAPI.activate(code);
        toast.success('激活成功');
        await fetchProfile();
      } catch (err: any) {
        const errorMsg = err.message || '激活失败';
        toast.error(errorMsg);
        throw err;
      }
    },
    [toast, fetchProfile]
  );

  // 初次加载
  useEffect(() => {
    // 先尝试从 localStorage 读取
    const storedProfile = localStorage.getItem('userProfile');
    if (storedProfile) {
      try {
        setProfile(JSON.parse(storedProfile));
      } catch (err) {
        console.error('Failed to parse stored profile:', err);
      }
    }
    
    // 然后从服务器刷新
    fetchProfile();
  }, [fetchProfile]);

  return {
    profile,
    loading,
    error,
    fetchProfile,
    updateProfile,
    uploadAvatar,
    activateMembership,
  };
}

