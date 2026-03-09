/**
 * Token 用量数据获取 Hook
 * 
 * 用于 UsageModal 组件获取和转换 token 使用统计数据
 */
import { useState, useEffect, useCallback } from 'react';
import { tokenUsageAPI, HourlyUsageResponse, DailyUsageResponse } from '@/lib/api';

/** 图表数据点格式 */
export interface ChartDataPoint {
  date: string;        // 格式化的时间标签 (e.g., "3 PM" or "Jan 5")
  tokens: number;      // 总 token 数
  inputTokens: number; // 输入 token 数
  outputTokens: number;// 输出 token 数
}

/** Hook 返回值类型 */
export interface UseTokenUsageResult {
  data: ChartDataPoint[];
  totalTokens: number;
  isLoading: boolean;
  error: string | null;
  refetch: () => void;
}

/** 时间范围类型 */
export type TimeRange = '1d' | '7d' | '30d';

/**
 * 将后端数据转换为图表格式
 */
export function transformData(
  rawData: Array<{ time: string; input_tokens: number; output_tokens: number }>,
  range: TimeRange
): ChartDataPoint[] {
  if (!rawData || rawData.length === 0) {
    return [];
  }

  return rawData.map(item => {
    const date = new Date(item.time);
    let label: string;
    
    if (range === '1d') {
      // 1天范围：显示小时格式 (e.g., "3 PM")
      label = date.toLocaleTimeString('en-US', { hour: 'numeric', hour12: true });
    } else {
      // 7天/30天范围：显示日期格式 (e.g., "Jan 5")
      label = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }
    
    return {
      date: label,
      tokens: item.input_tokens + item.output_tokens,
      inputTokens: item.input_tokens,
      outputTokens: item.output_tokens,
    };
  });
}

/**
 * Token 用量数据获取 Hook
 * 
 * @param range 时间范围 ('1d' | '7d' | '30d')
 * @param customStartDate 自定义开始日期（可选）
 * @param customEndDate 自定义结束日期（可选）
 * @param enabled 是否启用数据获取（默认 true）
 * @returns 图表数据、总量、加载状态、错误信息和刷新函数
 */
export function useTokenUsage(
  range: TimeRange,
  customStartDate?: Date | null,
  customEndDate?: Date | null,
  enabled: boolean = true
): UseTokenUsageResult {
  const [data, setData] = useState<ChartDataPoint[]>([]);
  const [totalTokens, setTotalTokens] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    // 如果未启用，不发起请求
    if (!enabled) {
      return;
    }
    
    setIsLoading(true);
    setError(null);
    
    try {
      let response: HourlyUsageResponse | DailyUsageResponse;
      
      // 如果有自定义日期范围，转换为 ISO 格式
      const startTime = customStartDate?.toISOString();
      const endTime = customEndDate?.toISOString();
      
      if (range === '1d') {
        // 1天范围：调用小时聚合 API
        response = await tokenUsageAPI.getHourlyUsage(24, startTime, endTime);
      } else if (range === '7d') {
        // 7天范围：调用天聚合 API
        response = await tokenUsageAPI.getDailyUsage(7, startTime, endTime);
      } else {
        // 30天范围：调用天聚合 API
        response = await tokenUsageAPI.getDailyUsage(30, startTime, endTime);
      }
      
      // 转换数据格式
      const transformedData = transformData(response.data, range);
      setData(transformedData);
      setTotalTokens(response.total.total_tokens);
    } catch (err) {
      // 错误处理
      const errorMessage = err instanceof Error ? err.message : '获取数据失败';
      setError(errorMessage);
      // 错误时清空数据
      setData([]);
      setTotalTokens(0);
    } finally {
      setIsLoading(false);
    }
  }, [range, customStartDate, customEndDate, enabled]);

  // 当依赖变化时重新获取数据
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { 
    data, 
    totalTokens, 
    isLoading, 
    error, 
    refetch: fetchData 
  };
}
