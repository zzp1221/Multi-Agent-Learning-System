import { useEffect, useId, useState } from 'react';
import DOMPurify from 'dompurify';

interface MermaidDiagramProps {
  chart: string;
}

type MermaidApi = typeof import('mermaid').default;

let mermaidLoadPromise: Promise<MermaidApi> | null = null;
let mermaidInitialized = false;

async function loadMermaid(): Promise<MermaidApi> {
  if (!mermaidLoadPromise) {
    mermaidLoadPromise = import('mermaid').then((module) => module.default);
  }
  const mermaid = await mermaidLoadPromise;
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'loose',
      theme: 'default',
    });
    mermaidInitialized = true;
  }
  return mermaid;
}

function normalizeMermaidChart(chart: string): string {
  const trimmed = chart.trim();
  if (!trimmed) {
    return '';
  }
  const withoutFence = trimmed
    .replace(/^```mermaid\s*/i, '')
    .replace(/^```\s*/i, '')
    .replace(/\s*```$/i, '')
    .trim();
  const mindmapIndex = withoutFence.toLowerCase().indexOf('mindmap');
  if (mindmapIndex >= 0) {
    return withoutFence.slice(mindmapIndex).trim();
  }
  return withoutFence;
}

export default function MermaidDiagram({ chart }: MermaidDiagramProps) {
  const id = useId().replace(/:/g, '-');
  const [svg, setSvg] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function renderChart() {
      const normalizedChart = normalizeMermaidChart(chart);
      if (!normalizedChart) {
        setSvg('');
        setError('');
        return;
      }

      try {
        const mermaid = await loadMermaid();
        const result = await mermaid.render(`mermaid-${id}`, normalizedChart);
        if (!cancelled) {
          setSvg(DOMPurify.sanitize(result.svg, {
            USE_PROFILES: { svg: true, svgFilters: true },
            ADD_TAGS: ['foreignObject'],
            ADD_ATTR: ['class', 'style', 'xmlns', 'width', 'height', 'viewBox'],
          }));
          setError('');
        }
      } catch {
        if (!cancelled) {
          setSvg('');
          setError('图表暂时无法生成，请检查内容后重试。');
        }
      }
    }

    void renderChart();
    return () => {
      cancelled = true;
    };
  }, [chart, id]);

  if (error) {
    return (
      <div className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-700 shadow-sm shadow-amber-100/70 dark:bg-amber-500/10 dark:text-amber-300 dark:shadow-none">
        {error}
      </div>
    );
  }

  if (!svg) {
    return (
      <div className="rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-500 shadow-sm shadow-slate-200/60 dark:bg-slate-900/50 dark:text-slate-400 dark:shadow-none">
        正在生成图表...
      </div>
    );
  }

  return (
    <>
      <style>{`
        .mermaid-diagram svg {
          max-width: 100%;
          height: auto;
        }
        .mermaid-diagram svg text,
        .mermaid-diagram svg .nodeLabel,
        .mermaid-diagram svg .label,
        .mermaid-diagram svg foreignObject,
        .mermaid-diagram svg foreignObject div,
        .mermaid-diagram svg foreignObject span {
          color: #1e293b !important;
          fill: #1e293b !important;
          opacity: 1 !important;
        }
        .dark .mermaid-diagram svg text,
        .dark .mermaid-diagram svg .nodeLabel,
        .dark .mermaid-diagram svg .label,
        .dark .mermaid-diagram svg foreignObject,
        .dark .mermaid-diagram svg foreignObject div,
        .dark .mermaid-diagram svg foreignObject span {
          color: #e2e8f0 !important;
          fill: #e2e8f0 !important;
        }
      `}</style>
      <div
        className="mermaid-diagram overflow-x-auto rounded-xl bg-white/90 p-4 text-slate-800 shadow-sm shadow-slate-200/60 dark:bg-slate-950/86 dark:text-slate-100 dark:shadow-none"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    </>
  );
}
