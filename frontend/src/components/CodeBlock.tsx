import { useState, lazy, Suspense } from 'react';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Check, Copy } from 'lucide-react';

let syntaxHighlighterLoader: Promise<{ default: any }> | null = null;

// 懒加载轻量版语法高亮器，仅注册项目中常用的语言。
const SyntaxHighlighter: any = lazy(() =>
  (syntaxHighlighterLoader ??= Promise.all([
    import('react-syntax-highlighter/dist/esm/prism-light'),
    import('react-syntax-highlighter/dist/esm/languages/prism/bash'),
    import('react-syntax-highlighter/dist/esm/languages/prism/java'),
    import('react-syntax-highlighter/dist/esm/languages/prism/javascript'),
    import('react-syntax-highlighter/dist/esm/languages/prism/json'),
    import('react-syntax-highlighter/dist/esm/languages/prism/markdown'),
    import('react-syntax-highlighter/dist/esm/languages/prism/python'),
    import('react-syntax-highlighter/dist/esm/languages/prism/sql'),
    import('react-syntax-highlighter/dist/esm/languages/prism/typescript'),
    import('react-syntax-highlighter/dist/esm/languages/prism/tsx'),
    import('react-syntax-highlighter/dist/esm/languages/prism/yaml'),
  ]).then(([
    syntaxModule,
    bashModule,
    javaModule,
    javascriptModule,
    jsonModule,
    markdownModule,
    pythonModule,
    sqlModule,
    typescriptModule,
    tsxModule,
    yamlModule,
  ]) => {
    const PrismLight = syntaxModule.default;
    PrismLight.registerLanguage('bash', bashModule.default);
    PrismLight.registerLanguage('shell', bashModule.default);
    PrismLight.registerLanguage('sh', bashModule.default);
    PrismLight.registerLanguage('java', javaModule.default);
    PrismLight.registerLanguage('javascript', javascriptModule.default);
    PrismLight.registerLanguage('js', javascriptModule.default);
    PrismLight.registerLanguage('json', jsonModule.default);
    PrismLight.registerLanguage('markdown', markdownModule.default);
    PrismLight.registerLanguage('md', markdownModule.default);
    PrismLight.registerLanguage('python', pythonModule.default);
    PrismLight.registerLanguage('py', pythonModule.default);
    PrismLight.registerLanguage('sql', sqlModule.default);
    PrismLight.registerLanguage('typescript', typescriptModule.default);
    PrismLight.registerLanguage('ts', typescriptModule.default);
    PrismLight.registerLanguage('tsx', tsxModule.default);
    PrismLight.registerLanguage('yaml', yamlModule.default);
    PrismLight.registerLanguage('yml', yamlModule.default);
    return { default: PrismLight };
  }))
);

interface CodeBlockProps {
  language?: string;
  children: string;
}

export default function CodeBlock({ language, children }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('复制失败:', err);
    }
  };

  // 清理代码内容
  const code = children?.trim() || '';

  return (
    <div className="relative group my-3">
      {/* 语言标签和复制按钮 */}
      <div className="flex items-center justify-between px-4 py-2 bg-slate-700 rounded-t-xl border-b border-slate-600">
        <span className="text-xs text-slate-400 font-mono">
          {language || '代码'}
        </span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-2 py-1 text-xs text-slate-400 hover:text-white hover:bg-slate-600 rounded transition-colors"
          title="复制代码"
        >
          {copied ? (
            <>
              <Check className="w-3.5 h-3.5 text-green-400" />
              <span className="text-green-400">已复制</span>
            </>
          ) : (
            <>
              <Copy className="w-3.5 h-3.5" />
              <span>复制</span>
            </>
          )}
        </button>
      </div>

      {/* 代码内容 */}
      <div className="bg-[#282c34] rounded-b-xl text-sm leading-6 overflow-x-auto scrollbar-thin">
        <Suspense fallback={
          <div className="p-4 text-slate-400 font-mono text-xs">代码加载中…</div>
        }>
          <SyntaxHighlighter
            language={language || 'text'}
            style={oneDark}
            customStyle={{
              margin: 0,
              borderTopLeftRadius: 0,
              borderTopRightRadius: 0,
              borderBottomLeftRadius: '0.75rem',
              borderBottomRightRadius: '0.75rem',
              fontSize: '0.875rem',
              lineHeight: '1.5',
            }}
            showLineNumbers={code.split('\n').length > 3}
            wrapLines
          >
            {code}
          </SyntaxHighlighter>
        </Suspense>
      </div>
    </div>
  );
}
