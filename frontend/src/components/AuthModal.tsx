import { FormEvent, useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { LoaderCircle, Sparkles, X } from 'lucide-react';
import { authApi, type AuthResponse, type AuthUser } from '../api/auth';
import { AUTH_USER_STORAGE_KEY, getErrorMessage, persistAuthSession } from '../api/request';

type AuthTab = 'login' | 'register';

interface AuthModalProps {
  open: boolean;
  defaultTab: AuthTab;
  hint?: string;
  onClose: () => void;
  onSuccess: (user: AuthUser, authMode: AuthTab) => void;
}

export default function AuthModal(props: AuthModalProps) {
  const [tab, setTab] = useState<AuthTab>(props.defaultTab);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const [loginId, setLoginId] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [majorCode, setMajorCode] = useState('');

  useEffect(() => {
    if (!props.open) {
      return;
    }
    setTab(props.defaultTab);
    setError('');
  }, [props.defaultTab, props.open]);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedLoginId = loginId.trim();
    const hasPasswordEdgeWhitespace = password !== password.trim();
    if (!normalizedLoginId || !password.trim()) {
      setError('请填写账号和密码');
      return;
    }
    if (hasPasswordEdgeWhitespace) {
      setError('密码首尾不能包含空格，请确认后重试');
      return;
    }
    if (tab === 'register' && password.trim().length < 6) {
      setError('密码长度至少为6位');
      return;
    }
    if (tab === 'register' && !fullName.trim()) {
      setError('请填写姓名');
      return;
    }
    if (tab === 'register' && password !== confirmPassword) {
      setError('两次输入的密码不一致');
      return;
    }

    setSubmitting(true);
    setError('');
    try {
      const result =
        tab === 'login'
          ? await authApi.login({
              loginId: normalizedLoginId,
              password,
            })
          : await authApi.register({
              loginId: normalizedLoginId,
              password,
              fullName: fullName.trim(),
              majorCode: majorCode.trim() || undefined,
            });

      const user = normalizeUser(result);
      window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify(user));
      persistAuthSession({
        token: result.token,
      });
      props.onSuccess(user, tab);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const inputClass = "auth-field";

  return (
    <AnimatePresence>
      {props.open ? (
        <div className="fixed inset-0 z-[120] flex items-center justify-center px-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.36, ease: [0.32, 0.72, 0, 1] }}
            className="absolute inset-0 bg-slate-950/42 backdrop-blur-sm"
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 10 }}
            transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
            className="auth-modal-shell"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4">
              <div className="flex items-center gap-2.5">
                <div className="auth-brand-mark">
                  <Sparkles className="h-4 w-4 text-white" />
                </div>
                <h3 className="text-base font-semibold text-slate-800 dark:text-white">智学引擎</h3>
              </div>
              <button
                type="button"
                onClick={props.onClose}
                className="auth-close-button"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Hint */}
            {props.hint ? (
              <div className="auth-hint">
                {props.hint}
              </div>
            ) : null}

            {/* Tab Switcher */}
            <div className="auth-tab-shell">
              <button
                type="button"
                onClick={() => setTab('login')}
                className={`auth-tab-button ${
                  tab === 'login'
                    ? 'is-active'
                    : ''
                }`}
              >
                登录
              </button>
              <button
                type="button"
                onClick={() => setTab('register')}
                className={`auth-tab-button ${
                  tab === 'register'
                    ? 'is-active'
                    : ''
                }`}
              >
                注册
              </button>
            </div>

            {/* Form */}
            <form onSubmit={onSubmit} className="space-y-3.5 px-5 py-4">
              <label className="block">
                <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">登录账号</div>
                <input
                  value={loginId}
                  onChange={(e) => setLoginId(e.target.value)}
                  className={inputClass}
                  placeholder="请输入登录账号"
                  autoComplete="username"
                />
              </label>
              <label className="block">
                <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">密码</div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={inputClass}
                  placeholder="请输入密码"
                  autoComplete={tab === 'login' ? 'current-password' : 'new-password'}
                />
              </label>

              {tab === 'register' ? (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  transition={{ duration: 0.42, ease: [0.32, 0.72, 0, 1] }}
                  className="space-y-3.5 overflow-hidden"
                >
                  <label className="block">
                    <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">确认密码</div>
                    <input
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      className={inputClass}
                      placeholder="请再次输入密码"
                      autoComplete="new-password"
                    />
                  </label>
                  <label className="block">
                    <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">姓名</div>
                    <input
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      className={inputClass}
                      placeholder="请输入姓名"
                    />
                  </label>
                  <label className="block">
                    <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">专业方向（可选）</div>
                    <input
                      value={majorCode}
                      onChange={(e) => setMajorCode(e.target.value)}
                      className={inputClass}
                      placeholder="例如：计算机科学"
                    />
                  </label>
                </motion.div>
              ) : null}

              {error ? (
                <motion.div
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                className="auth-error"
                >
                  {error}
                </motion.div>
              ) : null}

              <button
                type="submit"
                disabled={submitting}
                className="auth-submit-button"
              >
                {submitting ? (
                  <>
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                    提交中...
                  </>
                ) : tab === 'login' ? (
                  '登录'
                ) : (
                  '注册'
                )}
              </button>
            </form>
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>
  );
}

function normalizeUser(input: AuthResponse): AuthUser {
  if (input.user) {
    const resolvedId = input.user.userId ?? input.user.id ?? input.userId ?? input.id;
    if (resolvedId === undefined || resolvedId === null || String(resolvedId).trim() === '') {
      throw new Error('登录响应缺少用户标识，请稍后重试');
    }
    return {
      id: resolvedId,
      userId: resolvedId,
      loginId: input.user.loginId ?? input.user.username,
      fullName: input.user.fullName ?? input.user.username,
      majorCode: input.user.majorCode,
      username: input.user.username ?? input.user.loginId,
    };
  }
  if (input.userId === undefined && input.id === undefined) {
    throw new Error('登录响应缺少用户标识，请稍后重试');
  }
  const resolvedId = input.userId ?? input.id;
  if (resolvedId === undefined || resolvedId === null) {
    throw new Error('登录响应缺少用户标识，请稍后重试');
  }
  return {
    id: resolvedId,
    userId: resolvedId,
    loginId: input.loginId,
    fullName: input.fullName ?? input.loginId ?? '用户',
    majorCode: input.majorCode,
  };
}
