import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { BookOpenCheck, Clock3, Compass, History, LayoutGrid, Menu, MessageCirclePlus, Search, Sparkles, UserRoundSearch } from 'lucide-react';
import AuthModal from './AuthModal';
import FloatingVoiceAssistant from './FloatingVoiceAssistant';
import ThemeToggle from './ThemeToggle';

import { authApi, type AuthUser } from '../api/auth';
import { conversationApi, type ConversationHistoryItem } from '../api/conversation';
import { AUTH_USER_STORAGE_KEY, clearAuthSession, getAuthToken, isUnauthorizedError } from '../api/request';

type AuthTab = 'login' | 'register';

export interface LayoutOutletContext {
  isAuthenticated: boolean;
  currentUser: AuthUser | null;
  openAuthModal: (tab?: AuthTab, hint?: string) => void;
}

const SELECTED_CONVERSATION_STORAGE_KEY = 'learning_studio_selected_conversation';
const ACTIVE_CONVERSATION_ID_STORAGE_KEY = 'learning_studio_active_conversation_id';
const ENGINE_TASK_STORAGE_KEY = 'learning_studio_engine_tasks';
const QNA_SNAPSHOT_STORAGE_KEY = 'learning_studio_qna_snapshot';

function normalizeAuthUser(input: Awaited<ReturnType<typeof authApi.me>>): AuthUser | null {
  if (input.user) {
    const resolvedId = input.user.userId ?? input.user.id ?? input.userId ?? input.id;
    if (resolvedId === undefined || resolvedId === null) {
      return null;
    }
    return {
      id: resolvedId,
      userId: resolvedId,
      loginId: input.user.loginId ?? input.loginId ?? input.user.username,
      fullName: input.user.fullName ?? input.fullName ?? input.user.username,
      majorCode: input.user.majorCode ?? input.majorCode,
      username: input.user.username ?? input.user.loginId,
    };
  }

  const fallbackId = input.userId ?? input.id;
  if (fallbackId === undefined || fallbackId === null) {
    return null;
  }
  return {
    id: fallbackId,
    userId: fallbackId,
    loginId: input.loginId,
    fullName: input.fullName ?? input.loginId ?? `用户${fallbackId}`,
    majorCode: input.majorCode,
  };
}

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const inEngine = location.pathname.startsWith('/engine');
  const inMistakes = location.pathname.startsWith('/mistakes');
  const inProfile = location.pathname.startsWith('/profile');
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [defaultTab, setDefaultTab] = useState<AuthTab>('login');
  const [authHint, setAuthHint] = useState('');
  const [conversationHistory, setConversationHistory] = useState<ConversationHistoryItem[]>([]);
  const [lastSyncAt, setLastSyncAt] = useState('');
  const [activeConversationId, setActiveConversationId] = useState('');
  const [historySearch, setHistorySearch] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  const moreMenuRef = useRef<HTMLDivElement>(null);

  const isAuthenticated = Boolean(currentUser);

  useEffect(() => {
    const token = getAuthToken();
    if (!token || typeof window === 'undefined') {
      return;
    }
    const rawAuthUser = window.localStorage.getItem(AUTH_USER_STORAGE_KEY);
    if (!rawAuthUser) {
      return;
    }
    try {
      const parsed = JSON.parse(rawAuthUser) as AuthUser;
      if (parsed?.id !== undefined && parsed?.id !== null) {
        setCurrentUser(parsed);
      }
    } catch {
      // 忽略损坏的本地缓存，由 bootstrapAuth 重新解析。
    }
  }, []);

  const loadRecentConversations = async () => {
    if (!getAuthToken()) {
      setConversationHistory([]);
      setLastSyncAt('');
      return;
    }
    try {
      const items = await conversationApi.listRecentConversations();
      const sorted = [...items].sort((left, right) => {
        const leftTime = Date.parse(left.lastMessageAt || left.updatedAt || '') || 0;
        const rightTime = Date.parse(right.lastMessageAt || right.updatedAt || '') || 0;
        return rightTime - leftTime;
      });
      setConversationHistory(sorted);
      setLastSyncAt(new Date().toISOString());
    } catch (error) {
      console.error('Failed to load conversation history:', error);
      if (isUnauthorizedError(error)) {
        setConversationHistory([]);
        setLastSyncAt('');
      }
    }
  };

  useEffect(() => {
    const bootstrapAuth = async () => {
      const token = getAuthToken();
      if (!token) {
        setCurrentUser(null);
        return;
      }

      try {
        const me = await authApi.me();
        const resolved = normalizeAuthUser(me);
        if (!resolved) {
          clearAuthSession();
          setCurrentUser(null);
          return;
        }
        window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify(resolved));
        setCurrentUser(resolved);
        await loadRecentConversations();
      } catch (error) {
        if (isUnauthorizedError(error)) {
          clearAuthSession();
          setCurrentUser(null);
          setConversationHistory([]);
          setLastSyncAt('');
          return;
        }
      }
    };

    bootstrapAuth();
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    setActiveConversationId(window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY) ?? '');
    const handleActiveConversationChanged = (event: Event) => {
      const customEvent = event as CustomEvent<{ conversationId?: string }>;
      const nextId = customEvent.detail?.conversationId?.trim() ?? '';
      setActiveConversationId(nextId);
      if (nextId) {
        window.sessionStorage.setItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY, nextId);
      } else {
        window.sessionStorage.removeItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY);
      }
    };
    window.addEventListener('app:active-conversation-changed', handleActiveConversationChanged as EventListener);
    return () => {
      window.removeEventListener('app:active-conversation-changed', handleActiveConversationChanged as EventListener);
    };
  }, []);

  useEffect(() => {
    const handleConversationUpdated = () => {
      void loadRecentConversations();
    };
    window.addEventListener('app:conversation-updated', handleConversationUpdated);
    return () => {
      window.removeEventListener('app:conversation-updated', handleConversationUpdated);
    };
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (moreMenuRef.current && !moreMenuRef.current.contains(event.target as Node)) {
        setMoreMenuOpen(false);
      }
    };
    if (moreMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [moreMenuOpen]);

  const handleOpenProfilePage = useCallback(() => {
    setMoreMenuOpen(false);
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后查看个人画像');
      return;
    }
    navigate('/profile');
  }, [isAuthenticated, navigate]);

  const handleOpenServicePage = useCallback(() => {
    setMoreMenuOpen(false);
    closeSidebar();
    navigate('/engine');
  }, [navigate]);

  const handleOpenMistakeBook = useCallback(() => {
    setMoreMenuOpen(false);
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后查看错题本');
      return;
    }
    navigate('/mistakes');
  }, [isAuthenticated, navigate]);

  function openAuthModal(tab: AuthTab = 'login', hint = '请先登录') {
    setDefaultTab(tab);
    setAuthHint(hint);
    setModalOpen(true);
  }

  const handleLogout = async () => {
    try {
      await authApi.logout();
    } catch {
      // 忽略退出登录时的网络错误
    } finally {
      clearAuthSession();
      setCurrentUser(null);
      setConversationHistory([]);
      setLastSyncAt('');
    }
  };

  const userDisplayName = useMemo(() => {
    if (!currentUser) {
      return '';
    }
    return currentUser.fullName || currentUser.loginId || currentUser.username || `用户${currentUser.id}`;
  }, [currentUser]);

  const filteredConversationHistory = useMemo(() => {
    const keyword = historySearch.trim().toLowerCase();
    if (!keyword) {
      return conversationHistory;
    }
    return conversationHistory.filter((item) => {
      const title = item.title?.toLowerCase() ?? '';
      const preview = item.lastMessagePreview?.toLowerCase() ?? '';
      return title.includes(keyword) || preview.includes(keyword);
    });
  }, [conversationHistory, historySearch]);

  const handleCreateNewChat = () => {
    if (!isAuthenticated) {
      openAuthModal('login', '登录后即可创建和保存新对话');
      return;
    }
    if (typeof window !== 'undefined') {
      window.sessionStorage.removeItem(SELECTED_CONVERSATION_STORAGE_KEY);
      window.sessionStorage.removeItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY);
      window.sessionStorage.removeItem(ENGINE_TASK_STORAGE_KEY);
      window.sessionStorage.removeItem(QNA_SNAPSHOT_STORAGE_KEY);
    }
    setActiveConversationId('');
    window.dispatchEvent(new CustomEvent('app:active-conversation-changed', { detail: { conversationId: '' } }));
    window.dispatchEvent(new Event('app:new-chat'));
    setSidebarOpen(false);
  };

  const handleOpenConversation = (item: ConversationHistoryItem) => {
    if (item.conversationId === activeConversationId && location.pathname === '/') {
      setSidebarOpen(false);
      return;
    }
    if (typeof window !== 'undefined') {
      window.sessionStorage.setItem(
        SELECTED_CONVERSATION_STORAGE_KEY,
        JSON.stringify({
          conversationId: item.conversationId,
          title: item.title,
          lastMessagePreview: item.lastMessagePreview ?? '',
        }),
      );
      window.sessionStorage.setItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY, item.conversationId);
    }
    setActiveConversationId(item.conversationId);
    navigate('/');
    window.dispatchEvent(
      new CustomEvent('app:open-conversation', {
        detail: {
          conversationId: item.conversationId,
          title: item.title,
          lastMessagePreview: item.lastMessagePreview ?? '',
        },
      }),
    );
    window.dispatchEvent(
      new CustomEvent('app:active-conversation-changed', {
        detail: {
          conversationId: item.conversationId,
        },
      }),
    );
    setSidebarOpen(false);
  };

  const closeSidebar = () => setSidebarOpen(false);

  const sidebarContent = (
    <div className="app-sidebar-content">
      {/* Logo */}
      <div className="app-sidebar-logo">
        <NavLink to="/" onClick={closeSidebar} className="flex items-center gap-3">
          <div className="app-brand-mark">
            <Sparkles className="h-4 w-4" />
          </div>
          <div>
            <p className="text-base font-semibold text-slate-900 dark:text-white">智学引擎</p>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">AI学习智能体平台</p>
          </div>
        </NavLink>
      </div>

      {/* Navigation */}
      <div className="app-sidebar-nav">
        <NavLink
          to="/"
          onClick={() => {
            handleCreateNewChat();
          }}
          className={({ isActive }) =>
            `app-sidebar-nav-item ${
              isActive
                ? 'is-active'
                : ''
            }`
          }
        >
          <MessageCirclePlus className="h-4 w-4" />
          新对话
        </NavLink>
        <div className="relative" ref={moreMenuRef}>
          <button
            type="button"
            onClick={() => setMoreMenuOpen((prev) => !prev)}
            className={`app-sidebar-nav-item ${moreMenuOpen || inEngine || inMistakes || inProfile ? 'is-active' : ''}`}
          >
            <LayoutGrid className="h-4 w-4" />
            更多功能
          </button>
          <AnimatePresence>
            {moreMenuOpen ? (
              <motion.div
                initial={{ opacity: 0, y: -4, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -4, scale: 0.96 }}
                transition={{ duration: 0.15 }}
                className="absolute left-0 top-full z-50 mt-2 w-full min-w-[210px] overflow-hidden rounded-2xl border border-blue-100/80 bg-white/95 py-1.5 shadow-xl shadow-blue-100/70 backdrop-blur-xl dark:border-slate-700 dark:bg-slate-900/95 dark:shadow-slate-900/50"
              >
                <button
                  type="button"
                  onClick={handleOpenProfilePage}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-900/50 dark:hover:text-primary-300"
                >
                  <UserRoundSearch className="h-4 w-4" />
                  查看个人画像
                </button>
                <button
                  type="button"
                  onClick={handleOpenServicePage}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-900/50 dark:hover:text-primary-300"
                >
                  <Sparkles className="h-4 w-4" />
                  学习服务
                </button>
                <button
                  type="button"
                  onClick={handleOpenMistakeBook}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-900/50 dark:hover:text-primary-300"
                >
                  <BookOpenCheck className="h-4 w-4" />
                  错题本
                </button>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>

      {/* Search */}
      <div className="px-5 pb-4">
        <label className="flex items-center rounded-2xl border border-blue-100/80 bg-white/70 px-3 py-2 transition-all focus-within:border-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-500/15 dark:border-slate-700 dark:bg-slate-900 dark:focus-within:border-primary-600">
          <Search className="mr-2 h-3.5 w-3.5 shrink-0 text-slate-400" />
          <input
            value={historySearch}
            onChange={(event) => setHistorySearch(event.target.value)}
            placeholder="搜索历史对话"
            className="w-full bg-transparent text-xs text-slate-700 outline-none placeholder:text-slate-400 dark:text-slate-300 dark:placeholder:text-slate-500"
          />
        </label>
      </div>

      {/* Conversation List */}
      <div className="mt-1 flex-1 overflow-y-auto scrollbar-thin px-4 pb-4">
        <div className="mb-2 flex items-center justify-between gap-2 px-1 text-xs font-medium text-slate-500 dark:text-slate-400">
          <span className="inline-flex items-center gap-2">
          <History className="h-3.5 w-3.5" />
          最近对话
          </span>
        </div>
        <div className="space-y-1">
          <AnimatePresence mode="popLayout">
            {filteredConversationHistory.length > 0 ? filteredConversationHistory.map((item, index) => (
              <motion.button
                key={item.conversationId}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(index * 0.02, 0.3) }}
                type="button"
                onClick={() => handleOpenConversation(item)}
                className={`group w-full truncate rounded-xl px-3 py-2 text-left text-sm transition-all duration-200 ${
                  item.conversationId === activeConversationId
                    ? 'bg-white text-primary-700 shadow-sm shadow-blue-100/70 ring-1 ring-blue-100 dark:bg-primary-500/10 dark:text-primary-400 dark:ring-primary-500/20'
                    : 'text-slate-600 hover:bg-white/70 dark:text-slate-400 dark:hover:bg-slate-800'
                }`}
                title={item.lastMessagePreview || item.title}
              >
                <div className="truncate text-[13px] font-medium">{item.title}</div>
                {item.lastMessagePreview ? (
                  <div className="mt-0.5 truncate text-[11px] opacity-60">{item.lastMessagePreview}</div>
                ) : null}
              </motion.button>
            )) : (
              <div className="rounded-lg px-3 py-2 text-[13px] text-slate-400 dark:text-slate-500">
                {isAuthenticated
                  ? historySearch.trim()
                    ? '没有匹配的历史对话'
                    : '暂无最近对话'
                  : '登录后显示最近对话'}
              </div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Auth / User */}
      <div className="app-sidebar-auth">
        {!isAuthenticated ? (
          <button
            type="button"
            onClick={() => {
              closeSidebar();
              openAuthModal('login', '');
            }}
            className="w-full rounded-2xl bg-primary-600 px-3 py-2.5 text-sm font-medium text-white shadow-lg shadow-blue-500/20 transition-all hover:bg-primary-700 active:scale-[0.98]"
          >
            立即登录
          </button>
        ) : (
          <div className="rounded-2xl border border-blue-100/80 bg-white/70 px-3 py-2.5 shadow-sm shadow-blue-100/50 dark:border-slate-700 dark:bg-slate-800/50">
            <div className="text-[11px] text-slate-400 dark:text-slate-500">当前用户</div>
            <div className="mt-0.5 text-sm font-medium text-slate-800 dark:text-slate-200">{userDisplayName}</div>
            <button type="button" onClick={handleLogout} className="mt-1.5 text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300">
              退出登录
            </button>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-blue-100/70 px-5 py-4 dark:border-slate-700/60">
        <div className="flex items-center justify-between rounded-2xl bg-white/70 px-3 py-2 shadow-sm shadow-blue-100/50 dark:bg-slate-800/50">
          <div className="flex items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
            <Clock3 className="h-3.5 w-3.5" />
            同步 {lastSyncAt ? new Date(lastSyncAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '--'}
          </div>
          <button type="button" onClick={() => void loadRecentConversations()} className="text-[11px] text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300">
            刷新
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      {/* Desktop Sidebar */}
      <aside className="app-sidebar fixed left-0 top-0 z-40 hidden h-screen w-[302px] flex-col md:flex">
        {sidebarContent}
      </aside>

      {/* Mobile Sidebar Overlay */}
      <AnimatePresence>
        {sidebarOpen ? (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm md:hidden"
              onClick={closeSidebar}
            />
            <motion.aside
              initial={{ x: -300 }}
              animate={{ x: 0 }}
              exit={{ x: -300 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
              className="app-sidebar fixed left-0 top-0 z-50 flex h-screen w-[302px] max-w-[86vw] flex-col md:hidden"
            >
              {sidebarContent}
            </motion.aside>
          </>
        ) : null}
      </AnimatePresence>

      {/* Main Content */}
      <main className="app-main min-w-0 flex-1 md:ml-[302px]">
        {/* Top Header */}
        <header className="app-topbar sticky top-0 z-30 flex items-center justify-between gap-3 px-3 sm:px-4 md:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800 md:hidden"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="app-breadcrumb min-w-0">
              <Compass className="h-4 w-4 text-primary-500" />
              <span className="hidden sm:inline">{inProfile ? '个人画像' : inMistakes ? '错题本' : inEngine ? '学习服务' : '新对话'}</span>
              <span className="hidden text-slate-300 sm:inline">/</span>
              <span className="hidden sm:inline">{inProfile ? '真实学习画像' : inMistakes ? '自动错题复习' : inEngine ? '独立服务页面' : '智能学习与解题助手'}</span>
              <span className="sm:hidden">{inProfile ? '个人画像' : inMistakes ? '错题本' : inEngine ? '学习服务' : '智能对话'}</span>
            </div>
          </div>
          <div className="flex items-center gap-2 md:gap-3">
            <span className="app-api-chip hidden lg:inline-flex">API 调用</span>
            <ThemeToggle />
            {!isAuthenticated ? (
              <button
                type="button"
                onClick={() => openAuthModal('login', '')}
                className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 md:hidden"
              >
                登录
              </button>
            ) : null}
          </div>
        </header>

        {/* Page Content */}
        <div className={inEngine || inMistakes || inProfile ? 'px-3 py-4 sm:px-4 md:px-8 md:py-6' : ''}>
          <motion.div
            key={inProfile ? 'profile-shell' : inMistakes ? 'mistake-shell' : inEngine ? 'engine-shell' : 'qna-shell'}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.3 }}
          >
            <Outlet context={{ isAuthenticated, currentUser, openAuthModal } satisfies LayoutOutletContext} />
          </motion.div>
        </div>
      </main>

      <AuthModal
        open={modalOpen}
        defaultTab={defaultTab}
        hint={authHint}
        onClose={() => setModalOpen(false)}
        onSuccess={(user) => {
          setCurrentUser(user);
          setModalOpen(false);
          void loadRecentConversations();
        }}
      />
      <FloatingVoiceAssistant isAuthenticated={isAuthenticated} openAuthModal={openAuthModal} />
    </div>
  );
}
