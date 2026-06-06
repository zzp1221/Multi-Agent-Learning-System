import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    hasError: false,
  };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('Application render failed', error, errorInfo);
  }

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
        <div className="max-w-md rounded-2xl bg-white/86 p-6 text-center shadow-lg shadow-slate-200/60 ring-1 ring-white/80 backdrop-blur">
          <h1 className="text-lg font-semibold text-slate-900">页面加载失败</h1>
          <p className="mt-2 text-sm text-slate-600">应用遇到了未处理错误。你可以刷新页面，或返回首页后重试。</p>
          <div className="mt-4 flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              刷新页面
            </button>
            <button
              type="button"
              onClick={() => {
                window.location.href = '/';
              }}
              className="rounded-lg px-4 py-2 text-sm text-slate-700 ring-1 ring-slate-200/80 hover:bg-slate-50"
            >
              返回首页
            </button>
          </div>
        </div>
      </div>
    );
  }
}
