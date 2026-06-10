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
      <div className="error-boundary-page flex min-h-screen items-center justify-center bg-slate-50 px-6">
        <div className="error-boundary-card max-w-md rounded-2xl bg-white/90 p-6 text-center shadow-xl shadow-slate-200/70 backdrop-blur">
          <h1 className="text-lg font-semibold text-slate-900">页面加载失败</h1>
          <p className="mt-2 text-sm text-slate-600">页面暂时没有加载成功。你可以刷新页面，或返回首页后重试。</p>
          <div className="mt-4 flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="error-boundary-primary rounded-lg bg-blue-600 px-4 py-2 text-sm text-white outline-none transition-all hover:bg-blue-700 focus-visible:shadow-[0_10px_24px_rgba(59,130,246,0.24)]"
            >
              刷新页面
            </button>
            <button
              type="button"
              onClick={() => {
                window.location.href = '/';
              }}
              className="error-boundary-secondary rounded-lg bg-slate-100/80 px-4 py-2 text-sm text-slate-700 shadow-sm shadow-slate-200/50 outline-none transition-all hover:bg-slate-100 focus-visible:shadow-[0_10px_24px_rgba(100,116,139,0.18)]"
            >
              返回首页
            </button>
          </div>
        </div>
      </div>
    );
  }
}
