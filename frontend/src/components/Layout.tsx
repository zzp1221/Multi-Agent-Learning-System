import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { BookOpenCheck, Clock3, Compass, History, Layers3, Menu, MessageCirclePlus, NotebookPen, Search, Settings, Sparkles, UserRoundSearch } from 'lucide-react';
import AuthModal from './AuthModal';
import FirstRunOnboardingModal, { type FirstRunOnboardingStep } from './FirstRunOnboardingModal';
import FloatingVoiceAssistant from './FloatingVoiceAssistant';
import FloatingPracticeAssistant from './FloatingPracticeAssistant';
import StageTestExamPage from '../pages/StageTestExamPage';
import ThemeToggle from './ThemeToggle';

import { authApi, type AuthUser } from '../api/auth';
import { conversationApi, type ConversationHistoryItem } from '../api/conversation';
import { smartEngineApi } from '../api/smartEngine';
import { llmSettingsApi } from '../api/settings';
import { AUTH_USER_STORAGE_KEY, clearAuthSession, getAuthToken, isUnauthorizedError } from '../api/request';
import { clearPracticeSession } from '../pages/practiceSessionStore';
import { clearStageTestSession } from '../pages/stageTestSessionStore';
import { isLlmSettingsReady } from '../utils/llmSettingsDraft';
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
  const inChat = location.pathname.startsWith('/chat');
  const inEngine = location.pathname.startsWith('/engine');
  const inResources = location.pathname.startsWith('/resources');
  const inMistakes = location.pathname.startsWith('/mistakes');
  const inNotes = location.pathname.startsWith('/notes');
  const inProfile = location.pathname.startsWith('/profile');
  const inSettings = location.pathname.startsWith('/settings');
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [defaultTab, setDefaultTab] = useState<AuthTab>('login');
  const [authHint, setAuthHint] = useState('');
  const [conversationHistory, setConversationHistory] = useState<ConversationHistoryItem[]>([]);
  const [lastSyncAt, setLastSyncAt] = useState('');
  const [activeConversationId, setActiveConversationId] = useState('');
  const [historySearch, setHistorySearch] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [llmOnboardingNeeded, setLlmOnboardingNeeded] = useState(false);
  const [profileOnboardingNeeded, setProfileOnboardingNeeded] = useState(false);
  const [firstRunOnboardingOpen, setFirstRunOnboardingOpen] = useState(false);
  const [firstRunOnboardingStep, setFirstRunOnboardingStep] = useState<FirstRunOnboardingStep>('llm');
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

  const refreshFirstRunOnboardingState = useCallback(async (user: AuthUser | null, preferredStep?: FirstRunOnboardingStep) => {
    const userId = user?.userId ?? user?.id;
    if (userId === undefined || userId === null) {
      setLlmOnboardingNeeded(false);
      setProfileOnboardingNeeded(false);
      setFirstRunOnboardingOpen(false);
      return;
    }
    const normalizedUserId = String(userId);
    let llmNeeded = false;
    let profileNeeded = false;
    try {
      const [llmSettings, profileResponse] = await Promise.all([
        llmSettingsApi.get(),
        smartEngineApi.getCurrentProfile(normalizedUserId),
      ]);
      llmNeeded = !isLlmSettingsReady(llmSettings);
      profileNeeded = !(profileResponse.profile && Object.keys(profileResponse.profile).length > 0);
      setLlmOnboardingNeeded(llmNeeded);
      setProfileOnboardingNeeded(profileNeeded);
    } catch (error) {
      console.error('Failed to check first-run onboarding:', error);
      return;
    }
    if (!llmNeeded && !profileNeeded) {
      setFirstRunOnboardingOpen(false);
      return;
    }
    const nextStep = preferredStep === 'profile' && !llmNeeded ? 'profile' : llmNeeded ? 'llm' : 'profile';
    setFirstRunOnboardingStep(nextStep);
    setFirstRunOnboardingOpen(true);
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
        void refreshFirstRunOnboardingState(resolved);
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
  }, [applyAuthenticatedUser, refreshFirstRunOnboardingState]);

  useEffect(() => {
    if (!isAuthenticated) {
      setFirstRunOnboardingOpen(false);
    }
  }, [isAuthenticated]);

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

  const handleOpenProfilePage = useCallback(() => {
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后查看个人画像');
      return;
    }
    navigate('/profile');
  }, [isAuthenticated, navigate]);

  const handleOpenServicePage = useCallback(() => {
    closeSidebar();
    navigate('/engine');
  }, [navigate]);

  const handleOpenResourceGenerationPage = useCallback(() => {
    closeSidebar();
    navigate('/resources');
  }, [navigate]);

  const handleOpenResourceGenerationTool = useCallback(() => {
    closeSidebar();
    navigate('/resources/generation');
  }, [navigate]);

  const handleOpenMistakeBook = useCallback(() => {
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后查看错题本');
      return;
    }
    navigate('/mistakes');
  }, [isAuthenticated, navigate]);

  const handleOpenNotebook = useCallback(() => {
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后使用 AI 笔记本');
      return;
    }
    navigate('/notes', { state: { returnTo: location.pathname + location.search } });
  }, [isAuthenticated, location.pathname, location.search, navigate]);

  const handleOpenSettings = useCallback(() => {
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后才能配置个人 LLM');
      return;
    }
    navigate('/settings');
  }, [isAuthenticated, navigate]);

  const handleOpenDashboard = useCallback(() => {
    closeSidebar();
    if (!isAuthenticated) {
      openAuthModal('login', '登录后查看每日学习工作台');
      return;
    }
    navigate('/');
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
      setFirstRunOnboardingOpen(false);
      setLlmOnboardingNeeded(false);
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

  const breadcrumbTitle = inSettings
    ? 'LLM 设置'
    : inProfile
      ? '个人画像'
      : inMistakes
        ? '错题本'
        : inResources
          ? '资源库'
          : inEngine
            ? '个性化学习路径'
            : inChat
              ? '问答辅导'
              : '今日工作台';
  const breadcrumbSubtitle = inSettings
    ? '用户 API Key 与模型路由'
    : inProfile
      ? '学习节奏与能力概览'
      : inMistakes
        ? '自动错题复习'
        : inResources
          ? '搜索、收藏与学习进度'
          : inEngine
            ? '阶段路径与资源推送'
            : inChat
              ? '智能学习与解题助手'
              : '从今天最该做的一件事开始';
  const pageShellClass = inNotes ? '' : !inChat ? 'app-page-shell' : '';
  const pageMotionKey = !inChat
    ? inSettings
      ? 'settings-shell'
      : inProfile
        ? 'profile-shell'
        : inNotes
          ? 'notes-shell'
          : inMistakes
            ? 'mistake-shell'
            : inResources
              ? 'resource-shell'
              : inEngine
                ? 'engine-shell'
                : 'workbench-shell'
    : 'qna-shell';

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
    navigate('/chat');
  };

  const handleOpenConversation = (item: ConversationHistoryItem) => {
    if (item.conversationId === activeConversationId && location.pathname === '/chat') {
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
    navigate('/chat');
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
        <button
          type="button"
          onClick={handleOpenDashboard}
          className={`app-sidebar-nav-item ${!inChat && !inEngine && !inResources && !inMistakes && !inNotes && !inProfile && !inSettings ? 'is-active' : ''}`}
        >
          <Compass className="h-4 w-4" />
          今日工作台
        </button>
        <NavLink
          to="/chat"
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
          问答辅导
        </NavLink>
        <NavButton active={inEngine} icon={<Sparkles className="h-4 w-4" />} label="学习路径" onClick={handleOpenServicePage} />
        <NavButton active={inResources && !location.pathname.startsWith('/resources/generation')} icon={<Layers3 className="h-4 w-4" />} label="资源库" onClick={handleOpenResourceGenerationPage} />
        <NavButton active={location.pathname.startsWith('/resources/generation')} icon={<Sparkles className="h-4 w-4" />} label="资源生成" onClick={handleOpenResourceGenerationTool} />
        <NavButton active={inMistakes} icon={<BookOpenCheck className="h-4 w-4" />} label="错题本" onClick={handleOpenMistakeBook} />
        <NavButton active={inNotes} icon={<NotebookPen className="h-4 w-4" />} label="AI 笔记本" onClick={handleOpenNotebook} />
        <NavButton active={inProfile} icon={<UserRoundSearch className="h-4 w-4" />} label="学习画像" onClick={handleOpenProfilePage} />
        <NavButton active={inSettings} icon={<Settings className="h-4 w-4" />} label="LLM 设置" onClick={handleOpenSettings} />
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
              <div className={`app-breadcrumb min-w-0 ${inSettings ? 'is-settings-breadcrumb' : ''}`}>
                <Compass className="h-4 w-4 text-primary-500" />
                {inSettings ? (
                  <>
                    <span className="settings-breadcrumb-label hidden sm:inline">{breadcrumbTitle}</span>
                    <span className="settings-breadcrumb-label hidden text-slate-300 sm:inline">/</span>
                    <span className="settings-breadcrumb-label hidden sm:inline">{breadcrumbSubtitle}</span>
                    <span className="settings-breadcrumb-label sm:hidden">{breadcrumbTitle}</span>
                  </>
                ) : null}
                <span className="hidden sm:inline">{breadcrumbTitle}</span>
                <span className="hidden text-slate-300 sm:inline">/</span>
                <span className="hidden sm:inline">{breadcrumbSubtitle}</span>
                <span className="sm:hidden">{inProfile ? '个人画像' : inMistakes ? '错题本' : inResources ? '资源总览' : inEngine ? '学习路径' : inChat ? '问答辅导' : '工作台'}</span>
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
        <div className={pageShellClass}>
          <motion.div
            key={pageMotionKey}
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
        onSuccess={(user, authMode) => {
          applyAuthenticatedUser(user);
          setModalOpen(false);
          void refreshFirstRunOnboardingState(user, authMode === 'register' ? 'llm' : undefined);
          void loadRecentConversations();
        }}
      />
      {isAuthenticated ? (
        <FirstRunOnboardingModal
          open={firstRunOnboardingOpen && (llmOnboardingNeeded || profileOnboardingNeeded)}
          step={firstRunOnboardingStep}
          currentUser={currentUser}
          onStepChange={setFirstRunOnboardingStep}
          onLlmCompleted={() => {
            setLlmOnboardingNeeded(false);
            if (!profileOnboardingNeeded) {
              setFirstRunOnboardingOpen(false);
            }
          }}
          onProfileCompleted={() => {
            setProfileOnboardingNeeded(false);
            setFirstRunOnboardingOpen(false);
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

function NavButton(props: { active: boolean; icon: ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      className={`app-sidebar-nav-item ${props.active ? 'is-active' : ''}`}
    >
      {props.icon}
      {props.label}
    </button>
  );
}
