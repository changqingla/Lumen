import { useState, useEffect } from 'react';
import { X, ChevronDown, ChevronLeft, ChevronRight, Loader2, AlertCircle } from 'lucide-react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts';
import styles from './UsageModal.module.css';
import { useTheme } from '@/hooks/useTheme';
import { useTokenUsage, TimeRange } from '@/hooks/useTokenUsage';
import { tokenUsageAPI, QuotaStatusResponse } from '@/lib/api';

interface UsageModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function UsageModal({ isOpen, onClose }: UsageModalProps) {
  const { isDark } = useTheme();
  const [activeRange, setActiveRange] = useState<TimeRange>('7d');
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [currentMonth, setCurrentMonth] = useState(new Date());
  
  // 默认显示当天及前6天的数据
  const [selectedStartDate, setSelectedStartDate] = useState<Date | null>(() => {
    const today = new Date();
    const startDate = new Date(today);
    startDate.setDate(today.getDate() - 6);
    startDate.setHours(0, 0, 0, 0);
    return startDate;
  });
  
  const [selectedEndDate, setSelectedEndDate] = useState<Date | null>(() => {
    const today = new Date();
    today.setHours(23, 59, 59, 999);
    return today;
  });

  // 使用 useTokenUsage hook 获取真实数据
  // 只有当 Modal 打开时才获取数据
  const { data: chartData, totalTokens, isLoading, error } = useTokenUsage(
    activeRange,
    selectedStartDate,
    selectedEndDate,
    isOpen  // 传递 enabled 参数
  );

  // 配额状态
  const [quotaStatus, setQuotaStatus] = useState<QuotaStatusResponse | null>(null);
  const [quotaLoading, setQuotaLoading] = useState(false);

  // 获取配额状态
  useEffect(() => {
    if (isOpen) {
      setQuotaLoading(true);
      tokenUsageAPI.getQuotaStatus()
        .then(setQuotaStatus)
        .catch(console.error)
        .finally(() => setQuotaLoading(false));
    }
  }, [isOpen]);

  if (!isOpen) return null;

  // 格式化 Token 数量
  const formatTokens = (tokens: number): string => {
    if (tokens >= 1_000_000) {
      return `${(tokens / 1_000_000).toFixed(1)}M`;
    }
    if (tokens >= 1_000) {
      return `${(tokens / 1_000).toFixed(1)}K`;
    }
    return tokens.toLocaleString();
  };

  // 格式化日期
  const formatResetDate = (isoDate: string): string => {
    const date = new Date(isoDate);
    return date.toLocaleDateString('zh-CN', {
      month: 'long',
      day: 'numeric',
    });
  };

  // 获取用户等级名称
  const getUserLevelName = (level: string): string => {
    switch (level) {
      case 'basic': return '普通用户';
      case 'member': return '白银会员';
      case 'premium': return '白金会员';
      default: return level;
    }
  };

  // 计算配额使用百分比
  const quotaPercent = quotaStatus 
    ? Math.min(100, (quotaStatus.used_tokens / quotaStatus.quota_limit) * 100)
    : 0;

  // Theme-aware colors
  const mainLineColor = isDark ? '#67e8f9' : '#0891b2';
  const gridColor = isDark ? '#262626' : '#e5e7eb';
  const axisTextColor = isDark ? '#737373' : '#9ca3af';
  const tooltipBg = isDark ? '#171717' : '#ffffff';
  const tooltipBorder = isDark ? '#262626' : '#e5e7eb';
  const tooltipText = isDark ? '#f5f5f5' : '#171717';

  // Date range text
  const getDateRangeText = () => {
    if (selectedStartDate && selectedEndDate) {
      if (activeRange === '1d') {
        return selectedStartDate.toLocaleDateString('zh-CN', { month: 'short', day: '2-digit' });
      }
      return `${selectedStartDate.toLocaleDateString('zh-CN', { month: 'short', day: '2-digit' })} - ${selectedEndDate.toLocaleDateString('zh-CN', { month: 'short', day: '2-digit' })}`;
    }
    
    if (activeRange === '1d') return '选择日期';
    if (activeRange === '7d') return '选择起始日期 (7天)';
    return '选择起始日期 (30天)';
  };

  // Custom tooltip
  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div style={{
          backgroundColor: tooltipBg,
          border: `1px solid ${tooltipBorder}`,
          borderRadius: '8px',
          padding: '12px',
          boxShadow: '0 4px 12px rgba(0, 0, 0, 0.1)',
        }}>
          <p style={{ 
            margin: '0 0 8px 0', 
            color: axisTextColor, 
            fontSize: '12px',
            fontWeight: 500
          }}>
            {label}
          </p>
          <p style={{ 
            margin: '4px 0', 
            color: tooltipText, 
            fontSize: '13px',
            fontWeight: 600
          }}>
            总Token: {data.tokens.toLocaleString()}
          </p>
          <p style={{ 
            margin: '2px 0', 
            color: '#10b981', 
            fontSize: '12px' 
          }}>
            输入: {data.inputTokens.toLocaleString()}
          </p>
          <p style={{ 
            margin: '2px 0', 
            color: '#f59e0b', 
            fontSize: '12px' 
          }}>
            输出: {data.outputTokens.toLocaleString()}
          </p>
        </div>
      );
    }
    return null;
  };

  // 处理范围切换
  const handleRangeChange = (range: TimeRange) => {
    setActiveRange(range);
    
    // 智能保持结束日期，如果没有选择则使用今天
    const endDate = selectedEndDate ? new Date(selectedEndDate) : new Date();
    endDate.setHours(23, 59, 59, 999);
    
    // 根据新的范围计算起始日期
    const startDate = new Date(endDate);
    if (range === '1d') {
      // 1天：起始日期就是结束日期
      startDate.setHours(0, 0, 0, 0);
    } else if (range === '7d') {
      // 7天：往前推6天
      startDate.setDate(endDate.getDate() - 6);
      startDate.setHours(0, 0, 0, 0);
    } else if (range === '30d') {
      // 30天：往前推29天
      startDate.setDate(endDate.getDate() - 29);
      startDate.setHours(0, 0, 0, 0);
    }
    
    setSelectedStartDate(startDate);
    setSelectedEndDate(endDate);
  };

  // 处理日期选择
  const handleDateSelect = (date: Date) => {
    // 将选中的日期作为结束日期
    const endDate = new Date(date);
    endDate.setHours(23, 59, 59, 999);
    
    // 根据范围计算起始日期
    const startDate = new Date(date);
    if (activeRange === '1d') {
      // 1天：起始日期就是选中的日期
      startDate.setHours(0, 0, 0, 0);
    } else if (activeRange === '7d') {
      // 7天：往前推6天
      startDate.setDate(date.getDate() - 6);
      startDate.setHours(0, 0, 0, 0);
    } else if (activeRange === '30d') {
      // 30天：往前推29天
      startDate.setDate(date.getDate() - 29);
      startDate.setHours(0, 0, 0, 0);
    }
    
    setSelectedStartDate(startDate);
    setSelectedEndDate(endDate);
    setShowDatePicker(false);
  };

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={e => e.stopPropagation()}>
        
        {/* Header Section with Date Filter */}
        <div className={styles.header}>
            <div className={styles.filtersRow}>
                <div style={{ position: 'relative' }}>
                    <button 
                        className={`${styles.dateRangeBtn} ${showDatePicker ? styles.active : ''}`}
                        onClick={() => {
                            if (!showDatePicker && selectedStartDate) {
                                setCurrentMonth(new Date(selectedStartDate));
                            }
                            setShowDatePicker(!showDatePicker);
                        }}
                    >
                        {getDateRangeText()}
                        <ChevronDown size={14} className={styles.chevronIcon} />
                    </button>
                    {showDatePicker && (
                        <div className={styles.datePickerDropdown} onClick={(e) => e.stopPropagation()}>
                            <div className={styles.calendarMock}>
                                <div className={styles.calendarHeader}>
                                    <button 
                                        className={styles.calendarNavBtn}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            const newMonth = new Date(currentMonth);
                                            newMonth.setMonth(newMonth.getMonth() - 1);
                                            setCurrentMonth(newMonth);
                                        }}
                                    >
                                        <ChevronLeft size={16} />
                                    </button>
                                    <span>
                                        {currentMonth.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
                                    </span>
                                    <button 
                                        className={styles.calendarNavBtn}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            const newMonth = new Date(currentMonth);
                                            newMonth.setMonth(newMonth.getMonth() + 1);
                                            setCurrentMonth(newMonth);
                                        }}
                                    >
                                        <ChevronRight size={16} />
                                    </button>
                                </div>
                                <div className={styles.calendarGrid}>
                                    {Array.from({length: new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 0).getDate()}).map((_, i) => {
                                        const date = new Date(currentMonth.getFullYear(), currentMonth.getMonth(), i + 1);
                                        date.setHours(0, 0, 0, 0);
                                        
                                        let isInRange = false;
                                        if (selectedStartDate && selectedEndDate) {
                                            const dateTime = date.getTime();
                                            const startTime = selectedStartDate.getTime();
                                            const endTime = selectedEndDate.getTime();
                                            isInRange = dateTime >= startTime && dateTime <= endTime;
                                        }
                                        
                                        return (
                                            <div 
                                                key={i} 
                                                className={`${styles.calendarDay} ${isInRange ? styles.selectedDay : ''}`}
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleDateSelect(date);
                                                }}
                                            >
                                                {i + 1}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
                
                <div className={styles.periodTabs}>
                    <button 
                        className={`${styles.periodTab} ${activeRange === '1d' ? styles.active : ''}`}
                        onClick={() => handleRangeChange('1d')}
                    >1d</button>
                    <button 
                        className={`${styles.periodTab} ${activeRange === '7d' ? styles.active : ''}`}
                        onClick={() => handleRangeChange('7d')}
                    >7d</button>
                    <button 
                        className={`${styles.periodTab} ${activeRange === '30d' ? styles.active : ''}`}
                        onClick={() => handleRangeChange('30d')}
                    >30d</button>
                </div>
            </div>
            
            <button className={styles.closeBtn} onClick={onClose}>
                <X size={18} />
            </button>
        </div>

        <div className={styles.content}>
          <div className={styles.titleSection}>
              <h2 className={styles.sectionTitle}>模型用量统计</h2>
          </div>

          {/* 配额状态卡片 */}
          {!quotaLoading && quotaStatus && (
            <div className={styles.quotaSection}>
              <div className={styles.quotaHeader}>
                <span className={styles.quotaTitle}>本月配额</span>
                <span className={styles.quotaLevel}>{getUserLevelName(quotaStatus.user_level)}</span>
              </div>
              <div className={styles.quotaStats}>
                <div className={styles.quotaStat}>
                  <span className={styles.quotaStatLabel}>已使用</span>
                  <span className={styles.quotaStatValue}>{formatTokens(quotaStatus.used_tokens)}</span>
                </div>
                <div className={styles.quotaStat}>
                  <span className={styles.quotaStatLabel}>配额上限</span>
                  <span className={styles.quotaStatValue}>{formatTokens(quotaStatus.quota_limit)}</span>
                </div>
                <div className={styles.quotaStat}>
                  <span className={styles.quotaStatLabel}>重置日期</span>
                  <span className={styles.quotaStatValue}>{formatResetDate(quotaStatus.reset_date)}</span>
                </div>
              </div>
              <div className={styles.quotaProgressContainer}>
                <div className={styles.quotaProgressInfo}>
                  <span>使用进度</span>
                  <span className={`${styles.quotaProgressText} ${quotaPercent >= 90 ? styles.warning : ''}`}>
                    {quotaPercent.toFixed(0)}%
                  </span>
                </div>
                <div className={styles.quotaProgressBar}>
                  <div 
                    className={`${styles.quotaProgressFill} ${quotaPercent >= 90 ? styles.warning : ''}`}
                    style={{ width: `${quotaPercent}%` }}
                  />
                </div>
              </div>
            </div>
          )}

          {/* 加载状态 */}
          {isLoading && (
            <div className={styles.loadingContainer}>
              <Loader2 className={styles.spinner} size={32} />
              <p>加载中...</p>
            </div>
          )}

          {/* 错误状态 */}
          {error && !isLoading && (
            <div className={styles.errorContainer}>
              <AlertCircle size={32} className={styles.errorIcon} />
              <p>{error}</p>
            </div>
          )}

          {/* 正常显示数据 */}
          {!isLoading && !error && (
            <>
              <div className={styles.statsRow}>
                <div className={`${styles.statCard} ${styles.primaryCard}`} style={{ maxWidth: '100%' }}>
                  <div className={styles.statHeader}>
                    <span className={styles.statLabel}>选中时段总用量</span>
                  </div>
                  <div className={styles.statMainValue}>
                    {totalTokens.toLocaleString()}
                  </div>
                </div>
              </div>

              <div className={styles.chartContainer}>
                {chartData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData} margin={{ top: 20, right: 20, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="0" stroke={gridColor} vertical={true} horizontal={true} />
                      <XAxis 
                        dataKey="date" 
                        stroke={axisTextColor} 
                        fontSize={12} 
                        tickLine={false}
                        axisLine={false}
                        tickMargin={12}
                        interval={activeRange === '1d' ? 3 : 'preserveStartEnd'}
                      />
                      <YAxis 
                        stroke={axisTextColor} 
                        fontSize={12} 
                        tickLine={false}
                        axisLine={false}
                        tickFormatter={(value) => value >= 1000 ? `${(value / 1000).toFixed(1)}k` : value}
                      />
                      <Tooltip 
                        content={<CustomTooltip />}
                        cursor={{ stroke: axisTextColor, strokeWidth: 1, strokeDasharray: '4 4' }}
                      />
                      <Line 
                        type="monotone" 
                        dataKey="tokens" 
                        stroke={mainLineColor} 
                        strokeWidth={2}
                        dot={activeRange !== '1d' ? { r: 3, fill: mainLineColor, strokeWidth: 0 } : false}
                        activeDot={{ r: 5, fill: mainLineColor, stroke: tooltipBg, strokeWidth: 2 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className={styles.emptyContainer}>
                    <p>暂无数据</p>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
