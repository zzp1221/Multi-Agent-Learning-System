import { useCallback, useEffect, useRef, useState } from 'react';

interface PPTistEditorProps {
  slidesJson: string;
  title: string;
  onClose: () => void;
}

export default function PPTistEditor({ slidesJson, title, onClose }: PPTistEditorProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [exporting, setExporting] = useState(false);

  const sendToIframe = useCallback((data: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage(data, window.location.origin);
  }, []);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      const { type } = event.data || {};

      if (type === 'PPTIST_READY') {
        setReady(true);
        try {
          const parsed = JSON.parse(slidesJson);
          sendToIframe({
            type: 'LOAD_SLIDES',
            slides: parsed.slides,
            theme: parsed.theme,
            title: parsed.title || title,
          });
        } catch {
          console.error('Failed to parse PPTist slides JSON');
        }
      }

      if (type === 'EXPORT_READY') {
        setExporting(false);
        const { blobUrl, fileName } = event.data;
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = fileName || `${title}.pptx`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(blobUrl);
      }

      if (type === 'EXPORT_ERROR') {
        setExporting(false);
        console.error('PPTist export error:', event.data.error);
      }
    }

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [slidesJson, title, sendToIframe]);

  const handleExport = useCallback(() => {
    setExporting(true);
    sendToIframe({ type: 'EXPORT_PPTX' });
  }, [sendToIframe]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-white dark:bg-gray-900">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium text-gray-700 dark:text-gray-200 truncate max-w-md">
            {title}
          </h3>
          {!ready && (
            <span className="text-xs text-gray-400 animate-pulse">
              加载编辑器中…
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleExport}
            disabled={!ready || exporting}
            className="px-3 py-1.5 text-xs font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {exporting ? '导出中…' : '导出 PPTX'}
          </button>
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs font-medium rounded-md bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-500 transition-colors"
          >
            关闭
          </button>
        </div>
      </div>

      {/* PPTist iframe */}
      <iframe
        ref={iframeRef}
        src="/pptist/?embed=1"
        className="flex-1 w-full border-0"
        title="PPTist Editor"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}
