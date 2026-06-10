import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { BookOpenCheck, Clock3, Compass, History, Layers3, LayoutGrid, LoaderCircle, Menu, MessageCirclePlus, NotebookPen, Search, Sparkles, UserRoundSearch } from 'lucide-react';
import AuthModal from './AuthModal';
import FloatingVoiceAssistant from './FloatingVoiceAssistant';
import FloatingPracticeAssistant from './FloatingPracticeAssistant';
import StageTestExamPage from '../pages/StageTestExamPage';
import ThemeToggle from './ThemeToggle';

import { authApi, type AuthUser } from '../api/auth';
import { conversationApi, type ConversationHistoryItem } from '../api/conversation';
import { smartEngineApi, type ProfileOnboardingPayload } from '../api/smartEngine';
import { AUTH_USER_STORAGE_KEY, clearAuthSession, getAuthToken, isUnauthorizedError } from '../api/request';
import { clearPracticeSession } from '../pages/practiceSessionStore';
import { clearStageTestSession } from '../pages/stageTestSessionStore';
import {
  ACTIVE_CONVERSATION_ID_STORAGE_KEY,
  ENGINE_TASK_STORAGE_KEY,
  QNA_CONVERSATION_CACHE_STORAGE_KEY,
  QNA_SNAPSHOT_STORAGE_KEY,
  SELECTED_CONVERSATION_STORAGE_KEY,
} from '../pages/LearningStudioDemoPage.model';

type AuthTab = 'login' | 'register';

export interface LayoutOutletContext {
  isAuthenticated: boolean;
  currentUser: AuthUser | null;
  openAuthModal: (tab?: AuthTab, hint?: string) => void;
}

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

function resolveAuthUserId(user: AuthUser | null): string {
  const rawId = user?.userId ?? user?.id;
  if (rawId === undefined || rawId === null) {
    return '';
  }
  return String(rawId).trim();
}

function clearUserScopedFrontendState(): void {
  clearPracticeSession();
  clearStageTestSession();
  if (typeof window === 'undefined') {
    return;
  }
  window.sessionStorage.removeItem(SELECTED_CONVERSATION_STORAGE_KEY);
  window.sessionStorage.removeItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY);
  window.sessionStorage.removeItem(ENGINE_TASK_STORAGE_KEY);
  window.sessionStorage.removeItem(QNA_SNAPSHOT_STORAGE_KEY);
  window.sessionStorage.removeItem(QNA_CONVERSATION_CACHE_STORAGE_KEY);
  window.dispatchEvent(new CustomEvent('app:active-conversation-changed', { detail: { conversationId: '' } }));
  window.dispatchEvent(new Event('app:new-chat'));
}

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const inDashboard = location.pathname.startsWith('/dashboard');
  const inEngine = location.pathname.startsWith('/engine');
  const inResources = location.pathname.startsWith('/resources');
  const inMistakes = location.pathname.startsWith('/mistakes');
  const inNotes = location.pathname.startsWith('/notes');
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
  const [profileOnboardingOpen, setProfileOnboardingOpen] = useState(false);
  const [profileOnboardingNeeded, setProfileOnboardingNeeded] = useState(false);
  const moreMenuRef = useRef<HTMLDivElement>(null);
  const currentUserIdRef = useRef('');

  const isAuthenticated = Boolean(currentUser);

  const applyAuthenticatedUser = useCallback((user: AuthUser) => {
    const previousUserId = currentUserIdRef.current;
    const nextUserId = resolveAuthUserId(user);
    if (previousUserId && nextUserId && previousUserId !== nextUserId) {
      clearUserScopedFrontendState();
      setConversationHistory([]);
      setLastSyncAt('');
      setActiveConversationId('');
    }
    currentUserIdRef.current = nextUserId;
    setCurrentUser(user);
  }, []);

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
        currentUserIdRef.current = resolveAuthUserId(parsed);
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

  const refreshProfileOnboardingState = useCallback(async (user: AuthUser | null) => {
    const userId = user?.userId ?? user?.id;
    if (userId === undefined || userId === null) {
      setProfileOnboardingNeeded(false);
      setProfileOnboardingOpen(false);
      return;
    }
    const normalizedUserId = String(userId);
    try {
      const response = await smartEngineApi.getCurrentProfile(normalizedUserId);
      const hasProfile = Boolean(response.profile && Object.keys(response.profile).length > 0);
      setProfileOnboardingNeeded(!hasProfile);
    } catch (error) {
      console.error('Failed to check profile onboarding:', error);
    }
  }, []);

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
        applyAuthenticatedUser(resolved);
        void refreshProfileOnboardingState(resolved);
        await loadRecentConversations();
      } catch (error) {
        if (isUnauthorizedError(error)) {
          clearAuthSession();
          clearUserScopedFrontendState();
          currentUserIdRef.current = '';
          setCurrentUser(null);
          setConversationHistory([]);
          setLastSyncAt('');
          setActiveConversationId('');
          return;
        }
      }
    };

    bootstrapAuth();
  }, [applyAuthenticatedUser, refreshProfileOnboardingState]);

  useEffect(() => {
    if (!isAuthenticated) {
      setProfileOnboardingOpen(false);
      return;
    }
    if (profileOnboardingNeeded && (inProfile || inEngine)) {
      setProfileOnboardingOpen(true);
    }
  }, [inEngine, inProfile, isAuthenticated, profileOnboardingNeeded]);

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

  const handleOpenResourceGenerationPage = useCallback(() => {
    setMoreMenuOpen(false);
    closeSidebar();
    navigate('/resources');
  }, [navigate]);

  const handleOpenResourceGenerationTool = useCallback(() => {
    setMoreMenuOpen(false);
    closeSidebar();
    navigate('/resources/generation');
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

  const handleOpenNotebook = useCallback(() => {
    setMoreMenuOpen(false);
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后使用 AI 笔记本');
      return;
    }
    navigate('/notes');
  }, [isAuthenticated, navigate]);

  const handleOpenDashboard = useCallback(() => {
    setMoreMenuOpen(false);
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后查看每日学习工作台');
      return;
    }
    navigate('/dashboard');
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
      clearUserScopedFrontendState();
      currentUserIdRef.current = '';
      setCurrentUser(null);
      setConversationHistory([]);
      setLastSyncAt('');
      setActiveConversationId('');
      setProfileOnboardingOpen(false);
      setProfileOnboardingNeeded(false);
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
            <p className="text-[11px] text-slate-500 dark:text-slate-400">智能学习工作台</p>
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
            className={`app-sidebar-nav-item ${moreMenuOpen || inDashboard || inEngine || inResources || inMistakes || inNotes || inProfile ? 'is-active' : ''}`}
          >
            <LayoutGrid className="h-4 w-4" />
            更多功能
          </button>
          <AnimatePresence>
            {moreMenuOpen ? (
              <motion.div
                initial={{ opacity: 0, y: 10, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 10, scale: 0.96 }}
                transition={{ duration: 0.42, ease: [0.32, 0.72, 0, 1] }}
                className="app-more-menu"
              >
                <button
                  type="button"
                  onClick={handleOpenDashboard}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-900/50 dark:hover:text-primary-300"
                >
                  <Compass className="h-4 w-4" />
                  每日学习工作台
                </button>
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
                  个性化学习路径
                </button>
                <button
                  type="button"
                  onClick={handleOpenResourceGenerationPage}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-900/50 dark:hover:text-primary-300"
                >
                  <Layers3 className="h-4 w-4" />
                  资源库
                </button>
                <button
                  type="button"
                  onClick={handleOpenResourceGenerationTool}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-900/50 dark:hover:text-primary-300"
                >
                  <Sparkles className="h-4 w-4" />
                  资源生成
                </button>
                <button
                  type="button"
                  onClick={handleOpenMistakeBook}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-900/50 dark:hover:text-primary-300"
                >
                  <BookOpenCheck className="h-4 w-4" />
                  错题本
                </button>
                <button
                  type="button"
                  onClick={handleOpenNotebook}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-900/50 dark:hover:text-primary-300"
                >
                  <NotebookPen className="h-4 w-4" />
                  AI 笔记本
                </button>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>

      {isAuthenticated ? (
        <div className="px-5 pb-4">
          <label className="app-sidebar-search">
            <Search className="mr-2 h-3.5 w-3.5 shrink-0 text-slate-400" />
            <input
              value={historySearch}
              onChange={(event) => setHistorySearch(event.target.value)}
              placeholder="搜索历史对话"
              className="w-full bg-transparent text-xs text-slate-700 outline-none placeholder:text-slate-400 dark:text-slate-300 dark:placeholder:text-slate-500"
            />
          </label>
        </div>
      ) : null}

      {isAuthenticated ? (
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
                  className={`app-conversation-item group w-full truncate px-3 py-2 text-left text-sm ${
                    item.conversationId === activeConversationId
                      ? 'bg-white text-primary-700 shadow-sm shadow-blue-100/70 dark:bg-primary-500/10 dark:text-primary-400'
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
                  {historySearch.trim() ? '没有匹配的历史对话' : '暂无最近对话'}
                </div>
              )}
            </AnimatePresence>
          </div>
        </div>
      ) : (
        <div className="flex-1" />
      )}

      {/* Auth / User */}
      <div className="app-sidebar-auth">
        {!isAuthenticated ? (
          <button
            type="button"
            onClick={() => {
              closeSidebar();
              openAuthModal('login', '');
            }}
            className="app-primary-action"
          >
            立即登录
          </button>
        ) : (
          <div className="app-user-card">
            <div className="text-[11px] text-slate-400 dark:text-slate-500">当前用户</div>
            <div className="mt-0.5 text-sm font-medium text-slate-800 dark:text-slate-200">{userDisplayName}</div>
            <button type="button" onClick={handleLogout} className="mt-1.5 text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300">
              退出登录
            </button>
          </div>
        )}
      </div>

      {isAuthenticated ? (
        <div className="px-5 pb-4 pt-2">
          <div className="app-sync-card">
            <div className="flex items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
              <Clock3 className="h-3.5 w-3.5" />
              同步 {lastSyncAt ? new Date(lastSyncAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '--'}
            </div>
            <button type="button" onClick={() => void loadRecentConversations()} className="text-[11px] text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300">
              刷新
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );

  return (
    <div className="flex min-h-[100dvh] bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      {/* Desktop Sidebar */}
      {!inNotes ? (
        <aside className="app-sidebar fixed bottom-4 left-4 top-4 z-40 hidden w-[286px] flex-col md:flex">
          {sidebarContent}
        </aside>
      ) : null}

      {/* Mobile Sidebar Overlay */}
      <AnimatePresence>
        {sidebarOpen ? (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.36, ease: [0.32, 0.72, 0, 1] }}
              className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm md:hidden"
              onClick={closeSidebar}
            />
            <motion.aside
              initial={{ x: -300 }}
              animate={{ x: 0 }}
              exit={{ x: -300 }}
              transition={{ duration: 0.48, ease: [0.32, 0.72, 0, 1] }}
              className="app-sidebar fixed bottom-3 left-3 top-3 z-50 flex w-[302px] max-w-[86vw] flex-col md:hidden"
            >
              {sidebarContent}
            </motion.aside>
          </>
        ) : null}
      </AnimatePresence>

      {/* Main Content */}
      <main className={inNotes ? 'app-main min-w-0 flex-1' : 'app-main min-w-0 flex-1 md:ml-[318px]'}>
        {/* Top Header */}
        {!inNotes ? (
          <header className="app-topbar sticky top-3 z-30 flex items-center justify-between gap-3 px-3 sm:px-4 md:px-8">
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
                <span className="hidden sm:inline">{inProfile ? '个人画像' : inMistakes ? '错题本' : inResources ? '资源库' : inEngine ? '个性化学习路径' : '新对话'}</span>
                <span className="hidden text-slate-300 sm:inline">/</span>
                <span className="hidden sm:inline">{inProfile ? '学习节奏与能力概览' : inMistakes ? '自动错题复习' : inResources ? '搜索、收藏与学习进度' : inEngine ? '阶段路径与资源推送' : '智能学习与解题助手'}</span>
                <span className="sm:hidden">{inProfile ? '个人画像' : inMistakes ? '错题本' : inResources ? '资源总览' : inEngine ? '学习路径' : '智能对话'}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 md:gap-3">
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
        ) : null}

        {/* Page Content */}
        <div className={inNotes ? '' : inDashboard || inEngine || inResources || inMistakes || inProfile ? 'app-page-shell' : ''}>
          <motion.div
            key={inDashboard ? 'dashboard-shell' : inProfile ? 'profile-shell' : inNotes ? 'notes-shell' : inMistakes ? 'mistake-shell' : inResources ? 'resource-shell' : inEngine ? 'engine-shell' : 'qna-shell'}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.48, ease: [0.32, 0.72, 0, 1] }}
            className={inNotes ? 'min-h-[100dvh]' : undefined}
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
          applyAuthenticatedUser(user);
          setModalOpen(false);
          void refreshProfileOnboardingState(user);
          void loadRecentConversations();
        }}
      />
      {isAuthenticated ? (
        <ProfileOnboardingModal
          open={profileOnboardingOpen}
          currentUser={currentUser}
          onCompleted={() => {
            setProfileOnboardingNeeded(false);
            setProfileOnboardingOpen(false);
            navigate('/profile');
          }}
        />
      ) : null}
      {!inNotes ? (
        <>
          <FloatingVoiceAssistant
            isAuthenticated={isAuthenticated}
            voiceUserId={currentUser?.id}
            openAuthModal={openAuthModal}
          />
          <FloatingPracticeAssistant
            isAuthenticated={isAuthenticated}
            currentUser={currentUser}
            openAuthModal={openAuthModal}
          />
        </>
      ) : null}
      <StageTestExamPage />
    </div>
  );
}

function ProfileOnboardingModal(props: {
  open: boolean;
  currentUser: AuthUser | null;
  onCompleted: () => void;
}) {
  const [majorCode, setMajorCode] = useState(props.currentUser?.majorCode ?? '');
  const [knowledgeBase, setKnowledgeBase] = useState('');
  const [learningGoal, setLearningGoal] = useState('');
  const [learningPreference, setLearningPreference] = useState('');
  const [resourcePreference, setResourcePreference] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (props.open) {
      setMajorCode(props.currentUser?.majorCode ?? '');
      setError('');
    }
  }, [props.currentUser?.majorCode, props.open]);

  if (!props.open) {
    return null;
  }

  const inputClass = 'w-full rounded-xl bg-slate-50/86 px-3.5 py-2.5 text-sm outline-none transition-all focus:bg-white focus:shadow-[0_10px_24px_rgba(59,130,246,0.14)] dark:bg-slate-950/70 dark:text-slate-200 dark:focus:bg-slate-950/90 dark:focus:shadow-[0_12px_28px_rgba(14,165,233,0.12)]';
  const optionsClass = 'w-full rounded-xl bg-slate-50/86 px-3.5 py-2.5 text-sm outline-none transition-all focus:bg-white focus:shadow-[0_10px_24px_rgba(59,130,246,0.14)] dark:bg-slate-950/70 dark:text-slate-200 dark:focus:bg-slate-950/90 dark:focus:shadow-[0_12px_28px_rgba(14,165,233,0.12)]';

  const submit = async () => {
    const payload: ProfileOnboardingPayload = {
      majorCode: majorCode.trim(),
      knowledgeBase: knowledgeBase.trim(),
      learningGoal: learningGoal.trim(),
      learningPreference: learningPreference.trim(),
      resourcePreference: resourcePreference.trim(),
    };
    if (Object.values(payload).some((value) => !value)) {
      setError('请先完成所有基础画像选择');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await smartEngineApi.completeProfileOnboarding(payload);
      window.dispatchEvent(new Event('app:profile-updated'));
      props.onCompleted();
    } catch (submitError) {
      console.error('Failed to complete profile onboarding:', submitError);
      setError('画像保存失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[130] flex items-center justify-center px-4 py-6">
      <div className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm" />
      <div className="relative max-h-[92dvh] w-full max-w-[560px] overflow-y-auto rounded-[24px] bg-white/94 shadow-2xl shadow-slate-950/12 backdrop-blur dark:bg-slate-900/94 dark:shadow-slate-950/40">
        <div className="px-5 pb-3 pt-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary-600 text-white">
              <UserRoundSearch className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-slate-900 dark:text-white">完成基础学习画像</h3>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">新用户需要先补全画像，系统会据此生成首版个性化学习路径。</p>
            </div>
          </div>
        </div>

        <div className="space-y-3.5 px-5 py-4">
          <label className="block">
            <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">专业方向</div>
            <input value={majorCode} onChange={(event) => setMajorCode(event.target.value)} className={inputClass} placeholder="例如：计算机科学、软件工程、数据科学" />
          </label>
          <label className="block">
            <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">当前基础</div>
            <select value={knowledgeBase} onChange={(event) => setKnowledgeBase(event.target.value)} className={optionsClass}>
              <option value="">请选择当前基础</option>
              <option value="零基础，刚开始学习">零基础，刚开始学习</option>
              <option value="有基础，但知识不系统">有基础，但知识不系统</option>
              <option value="中等基础，需要查漏补缺">中等基础，需要查漏补缺</option>
              <option value="基础较好，希望项目实战">基础较好，希望项目实战</option>
            </select>
          </label>
          <label className="block">
            <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">学习目标</div>
            <input value={learningGoal} onChange={(event) => setLearningGoal(event.target.value)} className={inputClass} placeholder="例如：两个月内掌握数据库索引并完成项目实战" />
          </label>
          <label className="block">
            <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">学习偏好</div>
            <select value={learningPreference} onChange={(event) => setLearningPreference(event.target.value)} className={optionsClass}>
              <option value="">请选择学习偏好</option>
              <option value="先讲概念，再给例题">先讲概念，再给例题</option>
              <option value="项目实战驱动">项目实战驱动</option>
              <option value="多做题巩固">多做题巩固</option>
              <option value="图文结合，步骤清晰">图文结合，步骤清晰</option>
            </select>
          </label>
          <label className="block">
            <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">资源偏好</div>
            <select value={resourcePreference} onChange={(event) => setResourcePreference(event.target.value)} className={optionsClass}>
              <option value="">请选择资源偏好</option>
              <option value="DOCUMENT">文档教程</option>
              <option value="VIDEO">视频讲解</option>
              <option value="QUIZ">练习题</option>
              <option value="CODE_CASE">代码案例</option>
            </select>
          </label>

          {error ? <div className="rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:bg-rose-500/10 dark:text-rose-400">{error}</div> : null}

          <button
            type="button"
            onClick={() => void submit()}
            disabled={submitting}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-70"
          >
            {submitting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            保存画像并生成学习路径
          </button>
        </div>
      </div>
    </div>
  );
}
