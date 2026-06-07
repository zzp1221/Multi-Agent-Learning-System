import { useMemo } from 'react';
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart as RechartsRadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { normalizeScore } from '../utils/score';

interface RadarChartDatum {
  subject: string;
  score: number;
  fullMark: number;
  description?: string;
}

interface NormalizedRadarDatum {
  subject: string;
  score: number;
  fullMark: number;
  originalScore: number;
  originalFullMark: number;
  description: string;
}

interface RadarChartProps {
  data: RadarChartDatum[];
  height?: number;
  className?: string;
}

/**
 * 雷达图组件（自动归一化到统一比例）
 */
export default function RadarChart({ data, height = 320, className = '' }: RadarChartProps) {
  // 归一化数据：将所有维度归一化到最大满分
  const normalizedData = useMemo(() => {
    if (!data || data.length === 0) return [];

    const maxFullMark = Math.max(...data.map((item) => item.fullMark));

    // 计算所有归一化后的分数，找出最大值（可能超过maxFullMark）
    const normalizedScores = data.map((item) =>
      normalizeScore(item.score, item.fullMark, maxFullMark),
    );
    const maxNormalizedScore = Math.max(...normalizedScores, maxFullMark);

    // 使用实际的最大值作为domain，但至少是maxFullMark
    const chartMax = Math.max(maxFullMark, maxNormalizedScore);

    return data.map(item => ({
      subject: item.subject,
      score: normalizeScore(item.score, item.fullMark, maxFullMark),
      fullMark: chartMax,
      originalScore: item.score,
      originalFullMark: item.fullMark,
      description: item.description || '表示当前画像记录中的学习支持维度。',
    }));
  }, [data]);

  // 检测是否为深色模式
  const isDark = typeof window !== 'undefined' && document.documentElement.classList.contains('dark');

  const gridColor = isDark ? '#334155' : '#e2e8f0';
  const tickColor = isDark ? '#94a3b8' : '#64748b';
  const tooltipBg = isDark ? '#1e293b' : '#fff';
  const tooltipText = isDark ? '#cbd5e1' : '#475569';
  const tooltipTitle = isDark ? '#f8fafc' : '#0f172a';
  const tooltipMuted = isDark ? '#94a3b8' : '#64748b';

  return (
    <div className={className} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <RechartsRadarChart data={normalizedData}>
          <PolarGrid stroke={gridColor} />
          <PolarAngleAxis
            dataKey="subject"
            tick={{ fill: tickColor, fontSize: 12, fontWeight: 500 }}
          />
          <PolarRadiusAxis
            angle={90}
            domain={[0, normalizedData.length > 0 ? normalizedData[0].fullMark : 100]}
            tick={{ fill: tickColor, fontSize: 10 }}
            tickFormatter={(value) => value.toString()}
          />
          <Radar
            name="掌握状态"
            dataKey="score"
            stroke="#6366f1"
            fill="#6366f1"
            fillOpacity={0.6}
            strokeWidth={2}
          />
          <Tooltip
            content={(tooltipProps) => {
              const payload = tooltipProps.payload as Array<{ payload?: NormalizedRadarDatum }> | undefined;
              const item = payload?.[0]?.payload;
              if (!tooltipProps.active || !item) {
                return null;
              }
              const percentage = item.originalFullMark > 0
                ? Math.round((item.originalScore / item.originalFullMark) * 100)
                : 0;
              return (
                <div
                  style={{
                    maxWidth: 260,
                    borderRadius: 12,
                    backgroundColor: tooltipBg,
                    boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                    padding: '10px 12px',
                  }}
                >
                  <div style={{ color: tooltipTitle, fontSize: 13, fontWeight: 700 }}>{item.subject}</div>
                  <div style={{ marginTop: 4, color: tooltipMuted, fontSize: 12 }}>
                    掌握状态 {percentage}%
                  </div>
                  <div style={{ marginTop: 6, color: tooltipText, fontSize: 12, lineHeight: 1.6 }}>
                    {item.description}
                  </div>
                </div>
              );
            }}
          />
        </RechartsRadarChart>
      </ResponsiveContainer>
    </div>
  );
}
