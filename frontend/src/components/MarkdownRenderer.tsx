import { memo, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import CodeBlock from './CodeBlock';
import MermaidDiagram from './MermaidDiagram';

interface MarkdownRendererProps {
  content: string;
  isStreaming?: boolean;
}

function extractLanguage(className?: string): string {
  if (!className) return '';
  const match = className.match(/language-(\w+)/);
  return match ? match[1] : '';
}

function isMermaidLanguage(language: string): boolean {
  return language.toLowerCase() === 'mermaid';
}

function isStandaloneMermaidChart(content: string): boolean {
  const trimmed = content.trim();
  if (!trimmed) {
    return false;
  }
  if (/^```mermaid\b/i.test(trimmed)) {
    return true;
  }
  return /^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph|quadrantChart|requirementDiagram|C4Context|sankey-beta|xychart-beta|block-beta|packet-beta|architecture-beta)\b/i.test(trimmed);
}

const MarkdownRenderer = memo(function MarkdownRenderer({ content, isStreaming }: MarkdownRendererProps) {
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  // 修复不完整的 Markdown（流式输出时常见问题）
  const safeContent = useMemo(() => {
    if (!content) return '';
    // 闭合未完成的代码块
    const ticks = (content.match(/```/g) || []).length;
    if (ticks % 2 !== 0) {
      return isStreaming ? content + '\n```' : content;
    }
    return content;
  }, [content, isStreaming]);

  // 空内容显示思考动画
  if (!safeContent.trim() && isStreaming) {
    return (
      <div className="flex items-center gap-2 py-1">
        <span className="inline-block h-2 w-2 rounded-full bg-primary-400 animate-pulse" style={{ animationDelay: '0ms' }} />
        <span className="inline-block h-2 w-2 rounded-full bg-primary-400 animate-pulse" style={{ animationDelay: '150ms' }} />
        <span className="inline-block h-2 w-2 rounded-full bg-primary-400 animate-pulse" style={{ animationDelay: '300ms' }} />
      </div>
    );
  }

  if (isStandaloneMermaidChart(safeContent)) {
    return <MermaidDiagram chart={safeContent} />;
  }

  return (
    <>
      <div className="qna-markdown prose prose-slate max-w-none dark:prose-invert prose-headings:font-semibold prose-h1:text-xl prose-h2:text-lg prose-h3:text-base prose-p:text-[15px] prose-p:leading-7 prose-pre:my-0 prose-pre:bg-transparent prose-code:text-[13px] prose-code:before:content-none prose-code:after:content-none prose-a:text-primary-600 dark:prose-a:text-primary-400 prose-table:text-sm prose-table:overflow-x-auto prose-img:rounded-xl prose-li:text-[15px]">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={{
            code({ className, children, ...props }) {
              const language = extractLanguage(className);
              const codeText = String(children).replace(/\n$/, '');
              const isInline = !className && !String(children).includes('\n');

              if (isInline) {
                return (
                  <code
                    className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[13px] font-medium text-rose-600 dark:bg-slate-800 dark:text-rose-400"
                    {...props}
                  >
                    {children}
                  </code>
                );
              }

              if (isMermaidLanguage(language)) {
                return <MermaidDiagram chart={codeText} />;
              }

              return <CodeBlock language={language}>{codeText}</CodeBlock>;
            },
            table({ children }) {
              return (
                <div className="my-4 overflow-x-auto rounded-xl bg-white/88 shadow-sm shadow-slate-200/60 dark:bg-slate-900/80 dark:shadow-none">
                  <table className="min-w-full text-sm">{children}</table>
                </div>
              );
            },
            th({ children }) {
              return (
                <th className="bg-slate-50/86 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-600 dark:bg-slate-800/86 dark:text-slate-400">
                  {children}
                </th>
              );
            },
            td({ children }) {
              return (
                <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">
                  {children}
                </td>
              );
            },
            a({ href, children }) {
              return (
                <a href={href} target="_blank" rel="noopener noreferrer" className="underline decoration-primary-300 underline-offset-2 hover:decoration-primary-500 dark:decoration-primary-700">
                  {children}
                </a>
              );
            },
            img({ src, alt }) {
              return (
                <button type="button" className="my-3 block" onClick={() => setPreviewImage(src || '')}>
                  <img
                    src={src}
                    alt={alt || '图片'}
                    className="max-h-96 rounded-xl object-cover shadow-sm shadow-slate-200/70 dark:shadow-slate-950/30"
                    loading="lazy"
                  />
                </button>
              );
            },
            blockquote({ children }) {
              return (
                <blockquote className="my-3 rounded-xl bg-slate-50/70 px-4 py-2 text-[15px] text-slate-600 shadow-sm shadow-slate-200/35 dark:bg-slate-900/44 dark:text-slate-400 dark:shadow-none">
                  {children}
                </blockquote>
              );
            },
            hr() {
              return <hr className="my-6 h-px border-0 bg-slate-200 dark:bg-slate-700" />;
            },
            ul({ children }) {
              return <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>;
            },
            ol({ children }) {
              return <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>;
            },
          }}
        >
          {safeContent}
        </ReactMarkdown>
        {isStreaming && safeContent.trim() ? <span className="qna-stream-cursor" aria-label="正在生成" /> : null}
      </div>
      {previewImage ? (
        <button
          type="button"
          onClick={() => setPreviewImage(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4"
        >
          <img src={previewImage} alt="图片预览" className="max-h-[90vh] max-w-[90vw] rounded-2xl object-contain shadow-2xl" />
        </button>
      ) : null}
    </>
  );
});

export default MarkdownRenderer;
