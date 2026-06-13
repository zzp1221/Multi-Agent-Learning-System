import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useOutletContext, useSearchParams } from 'react-router-dom';
import {
  Bold,
  BookOpen,
  Brain,
  CheckCircle2,
  Clock3,
  Code2,
  ExternalLink,
  Eye,
  FileText,
  Folder,
  FolderPlus,
  GitFork,
  Heading2,
  History,
  Image,
  Italic,
  List,
  ListChecks,
  LoaderCircle,
  Link2,
  Maximize2,
  Minimize2,
  MessageCircle,
  Network,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Send,
  Sparkles,
  Square,
  Tags,
  Table2,
  Trash2,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import type { LayoutOutletContext } from '../components/Layout';
import { conversationApi } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { readConversationChunk } from '../api/sse';
import { notesApi, type NoteAnalysis, type NoteDetail, type NoteFolder, type NoteListItem, type NoteSemanticSearchResponse, type NoteTag, type NoteVersion } from '../api/notes';
import type { ResourceSemanticSearchResponse } from '../api/resources';

const PAGE_SIZE = 30;
const AUTOSAVE_DELAY_MS = 1200;
const NOTE_EXIT_FALLBACK_ROUTE = '/';
const NEW_NOTE_TEMPLATE = '# 未命名笔记\n\n记录一个概念、例题、疑问或复盘结论。';

type EditorPaneMode = 'write' | 'preview' | 'split';
type EditorInputMode = 'markdown' | 'rich';
type SaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

interface NoteDraftSnapshot {
  title: string;
  markdown: string;
  folderId: string;
  tags: string[];
}

export default function NotebookPage() {
  const { isAuthenticated, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [folders, setFolders] = useState<NoteFolder[]>([]);
  const [tags, setTags] = useState<NoteTag[]>([]);
  const [notes, setNotes] = useState<NoteListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [keyword, setKeyword] = useState('');
  const [submittedKeyword, setSubmittedKeyword] = useState('');
  const [activeFolderId, setActiveFolderId] = useState('');
  const [activeTag, setActiveTag] = useState('');
  const [selectedNoteId, setSelectedNoteId] = useState('');
  const [detail, setDetail] = useState<NoteDetail | null>(null);
  const [titleDraft, setTitleDraft] = useState('');
  const [markdownDraft, setMarkdownDraft] = useState('');
  const [folderDraftId, setFolderDraftId] = useState('');
  const [tagInput, setTagInput] = useState('');
  const [editorMode, setEditorMode] = useState<EditorPaneMode>('split');
  const [inputMode, setInputMode] = useState<EditorInputMode>('markdown');
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [sideLoading, setSideLoading] = useState(false);
  const [error, setError] = useState('');
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [saveError, setSaveError] = useState('');
  const [creating, setCreating] = useState(false);
  const [deletingId, setDeletingId] = useState('');
  const [folderName, setFolderName] = useState('');
  const [folderBusyId, setFolderBusyId] = useState('');
  const [versions, setVersions] = useState<NoteVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [restoringVersionId, setRestoringVersionId] = useState('');
  const [analysis, setAnalysis] = useState<NoteAnalysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [relatedResources, setRelatedResources] = useState<ResourceSemanticSearchResponse | null>(null);
  const [relatedLoading, setRelatedLoading] = useState(false);
  const [semanticQuery, setSemanticQuery] = useState('');
  const [semanticResults, setSemanticResults] = useState<NoteSemanticSearchResponse | null>(null);
  const [semanticLoading, setSemanticLoading] = useState(false);
  const [chatConversationId, setChatConversationId] = useState('');
  const [chatInput, setChatInput] = useState('');
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatStreaming, setChatStreaming] = useState(false);
  const [chatError, setChatError] = useState('');

  const loadRequestIdRef = useRef(0);
  const saveTimerRef = useRef<number | null>(null);
  const lastSavedSnapshotRef = useRef('');
  const currentSnapshotRef = useRef('');
  const saveSequenceRef = useRef(0);
  const chatAbortRef = useRef<AbortController | null>(null);

  const parsedTags = useMemo(() => parseTagInput(tagInput), [tagInput]);
  const plainTextDraft = useMemo(() => markdownToPlainText(markdownDraft), [markdownDraft]);
  const draftWordCount = useMemo(() => countWords(plainTextDraft), [plainTextDraft]);
  const draftReadingMinutes = Math.max(1, Math.ceil(draftWordCount / 320));
  const hasNotes = notes.length > 0;
  const requestedNoteId = searchParams.get('noteId') ?? '';
  const selectedFolderName = activeFolderId ? folders.find((item) => item.id === activeFolderId)?.name ?? '目录' : '全部笔记';
  const activeFilters = useMemo(() => {
    const filters: string[] = [];
    if (submittedKeyword) {
      filters.push(`搜索：${submittedKeyword}`);
    }
    if (activeFolderId) {
      filters.push(`目录：${selectedFolderName}`);
    }
    if (activeTag) {
      filters.push(`标签：${activeTag}`);
    }
    return filters;
  }, [activeFolderId, activeTag, selectedFolderName, submittedKeyword]);

  currentSnapshotRef.current = buildSnapshot({
    title: titleDraft,
    markdown: markdownDraft,
    folderId: folderDraftId,
    tags: parsedTags,
  });

  const loadSidebars = useCallback(async () => {
    if (!isAuthenticated) {
      setFolders([]);
      setTags([]);
      return;
    }
    setSideLoading(true);
    try {
      const [nextFolders, nextTags] = await Promise.all([
        notesApi.folders(),
        notesApi.tags(),
      ]);
      setFolders(nextFolders);
      setTags(nextTags);
    } catch (loadError) {
      setError(getErrorMessage(loadError));
    } finally {
      setSideLoading(false);
    }
  }, [isAuthenticated]);

  const loadNotes = useCallback(async () => {
    if (!isAuthenticated) {
      loadRequestIdRef.current += 1;
      setNotes([]);
      setTotal(0);
      setSelectedNoteId('');
      setDetail(null);
      setSaveStatus('idle');
      setError('');
      return;
    }
    const requestId = loadRequestIdRef.current + 1;
    loadRequestIdRef.current = requestId;
    setLoading(true);
    setError('');
    try {
      const response = await notesApi.list({
        keyword: submittedKeyword || undefined,
        folderId: activeFolderId || undefined,
        tag: activeTag || undefined,
        page: 0,
        size: PAGE_SIZE,
      });
      if (loadRequestIdRef.current !== requestId) {
        return;
      }
      setNotes(response.items);
      setTotal(response.total);
      setSelectedNoteId((current) => {
        if (current && response.items.some((item) => item.id === current)) {
          return current;
        }
        return response.items[0]?.id ?? '';
      });
    } catch (loadError) {
      if (loadRequestIdRef.current === requestId) {
        setError(getErrorMessage(loadError));
      }
    } finally {
      if (loadRequestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [activeFolderId, activeTag, isAuthenticated, submittedKeyword]);

  const loadVersions = useCallback(async (noteId: string) => {
    if (!noteId || !isAuthenticated) {
      setVersions([]);
      return;
    }
    setVersionsLoading(true);
    try {
      setVersions(await notesApi.versions(noteId));
    } catch (loadError) {
      setError(getErrorMessage(loadError));
    } finally {
      setVersionsLoading(false);
    }
  }, [isAuthenticated]);

  const loadDetail = useCallback(async (noteId: string) => {
    if (!noteId || !isAuthenticated) {
      setDetail(null);
      setVersions([]);
      setAnalysis(null);
      setRelatedResources(null);
      return;
    }
    setDetailLoading(true);
    setSaveError('');
    try {
      const nextDetail = await notesApi.detail(noteId);
      setDetail(nextDetail);
      setTitleDraft(nextDetail.title);
      setMarkdownDraft(nextDetail.markdownContent);
      setFolderDraftId(nextDetail.folderId ?? '');
      setTagInput(tagsToInput(nextDetail.tags.map((item) => item.name)));
      lastSavedSnapshotRef.current = buildSnapshot({
        title: nextDetail.title,
        markdown: nextDetail.markdownContent,
        folderId: nextDetail.folderId ?? '',
        tags: nextDetail.tags.map((item) => item.name),
      });
      setSaveStatus('saved');
      setAnalysis(null);
      setRelatedResources(null);
      void loadVersions(noteId);
    } catch (loadError) {
      setError(getErrorMessage(loadError));
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, [isAuthenticated, loadVersions]);

  useEffect(() => {
    void loadSidebars();
  }, [loadSidebars]);

  useEffect(() => {
    void loadNotes();
  }, [loadNotes]);

  useEffect(() => {
    void loadDetail(selectedNoteId);
  }, [loadDetail, selectedNoteId]);

  useEffect(() => {
    if (!requestedNoteId || !isAuthenticated) {
      return;
    }
    setSelectedNoteId(requestedNoteId);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete('noteId');
      return next;
    }, { replace: true });
  }, [isAuthenticated, requestedNoteId, setSearchParams]);

  useEffect(() => () => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
    }
    chatAbortRef.current?.abort();
  }, []);

  const updateNoteInList = useCallback((note: NoteDetail) => {
    const listItem = detailToListItem(note);
    setNotes((current) => {
      if (current.some((item) => item.id === note.id)) {
        return current.map((item) => item.id === note.id ? listItem : item);
      }
      return [listItem, ...current];
    });
  }, []);

  const saveDraft = useCallback(async () => {
    if (!detail || !isAuthenticated) {
      return;
    }
    const requestSnapshot = buildSnapshot({
      title: titleDraft,
      markdown: markdownDraft,
      folderId: folderDraftId,
      tags: parsedTags,
    });
    if (requestSnapshot === lastSavedSnapshotRef.current) {
      setSaveStatus('saved');
      setSaveError('');
      return;
    }
    const sequence = saveSequenceRef.current + 1;
    saveSequenceRef.current = sequence;
    setSaveStatus('saving');
    setSaveError('');
    try {
      const saved = await notesApi.update(detail.id, {
        title: titleDraft,
        markdownContent: markdownDraft,
        folderId: folderDraftId || null,
        clearFolder: !folderDraftId,
        tags: parsedTags,
      });
      if (saveSequenceRef.current !== sequence) {
        return;
      }
      lastSavedSnapshotRef.current = requestSnapshot;
      setDetail(saved);
      updateNoteInList(saved);
      setSaveStatus(currentSnapshotRef.current === requestSnapshot ? 'saved' : 'dirty');
      void loadSidebars();
    } catch (saveErrorValue) {
      if (saveSequenceRef.current === sequence) {
        setSaveStatus('error');
        setSaveError(getErrorMessage(saveErrorValue));
      }
    }
  }, [detail, folderDraftId, isAuthenticated, loadSidebars, markdownDraft, parsedTags, titleDraft, updateNoteInList]);

  useEffect(() => {
    if (!detail || detailLoading || !isAuthenticated) {
      return;
    }
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    const snapshot = currentSnapshotRef.current;
    if (snapshot === lastSavedSnapshotRef.current) {
      if (saveStatus === 'dirty') {
        setSaveStatus('saved');
      }
      return;
    }
    setSaveStatus('dirty');
    saveTimerRef.current = window.setTimeout(() => {
      void saveDraft();
    }, AUTOSAVE_DELAY_MS);
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
    };
  }, [detail, detailLoading, folderDraftId, isAuthenticated, markdownDraft, parsedTags, saveDraft, saveStatus, titleDraft]);

  const requireLogin = () => {
    if (!isAuthenticated) {
      openAuthModal('login', '登录后使用 AI 笔记本');
      return false;
    }
    return true;
  };

  const handleCreateNote = async () => {
    if (!requireLogin()) {
      return;
    }
    setCreating(true);
    setError('');
    try {
      const note = await notesApi.create({
        title: '未命名笔记',
        markdownContent: NEW_NOTE_TEMPLATE,
        folderId: activeFolderId || null,
        tags: activeTag ? [activeTag] : [],
      });
      setSelectedNoteId(note.id);
      updateNoteInList(note);
      setTotal((current) => current + 1);
      void loadSidebars();
    } catch (createError) {
      setError(getErrorMessage(createError));
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteNote = async (noteId: string) => {
    if (!requireLogin()) {
      return;
    }
    const note = notes.find((item) => item.id === noteId);
    if (!window.confirm(`确认删除「${note?.title ?? '这条笔记'}」？`)) {
      return;
    }
    setDeletingId(noteId);
    setError('');
    try {
      await notesApi.delete(noteId);
      setNotes((current) => current.filter((item) => item.id !== noteId));
      setTotal((current) => Math.max(0, current - 1));
      setSelectedNoteId((current) => current === noteId ? notes.find((item) => item.id !== noteId)?.id ?? '' : current);
      if (detail?.id === noteId) {
        setDetail(null);
      }
      void loadSidebars();
    } catch (deleteError) {
      setError(getErrorMessage(deleteError));
    } finally {
      setDeletingId('');
    }
  };

  const handleSelectNote = (noteId: string) => {
    if (noteId === selectedNoteId) {
      return;
    }
    if (saveStatus === 'dirty') {
      void saveDraft();
    }
    setSelectedNoteId(noteId);
  };

  const handleCreateFolder = async () => {
    if (!requireLogin()) {
      return;
    }
    const name = folderName.trim();
    if (!name) {
      return;
    }
    setFolderBusyId('new');
    try {
      const folder = await notesApi.createFolder({ name });
      setFolders((current) => [...current, folder].sort(sortFolders));
      setFolderName('');
      setActiveFolderId(folder.id);
    } catch (createError) {
      setError(getErrorMessage(createError));
    } finally {
      setFolderBusyId('');
    }
  };

  const handleRenameFolder = async (folder: NoteFolder) => {
    if (!requireLogin()) {
      return;
    }
    const nextName = window.prompt('重命名目录', folder.name)?.trim();
    if (!nextName || nextName === folder.name) {
      return;
    }
    setFolderBusyId(folder.id);
    try {
      const updated = await notesApi.updateFolder(folder.id, { name: nextName });
      setFolders((current) => current.map((item) => item.id === folder.id ? updated : item).sort(sortFolders));
    } catch (renameError) {
      setError(getErrorMessage(renameError));
    } finally {
      setFolderBusyId('');
    }
  };

  const handleDeleteFolder = async (folder: NoteFolder) => {
    if (!requireLogin()) {
      return;
    }
    if (!window.confirm(`删除目录「${folder.name}」？目录内笔记会移到未分类。`)) {
      return;
    }
    setFolderBusyId(folder.id);
    try {
      await notesApi.deleteFolder(folder.id);
      setFolders((current) => current.filter((item) => item.id !== folder.id));
      setActiveFolderId((current) => current === folder.id ? '' : current);
      setFolderDraftId((current) => current === folder.id ? '' : current);
      void loadNotes();
    } catch (deleteError) {
      setError(getErrorMessage(deleteError));
    } finally {
      setFolderBusyId('');
    }
  };

  const handleRestoreVersion = async (version: NoteVersion) => {
    if (!detail || !requireLogin()) {
      return;
    }
    if (!window.confirm(`恢复到版本 ${version.versionNo}？当前内容会先写入新的历史版本。`)) {
      return;
    }
    setRestoringVersionId(version.id);
    try {
      const restored = await notesApi.restoreVersion(detail.id, version.id);
      setDetail(restored);
      setTitleDraft(restored.title);
      setMarkdownDraft(restored.markdownContent);
      setFolderDraftId(restored.folderId ?? '');
      setTagInput(tagsToInput(restored.tags.map((item) => item.name)));
      lastSavedSnapshotRef.current = buildSnapshot({
        title: restored.title,
        markdown: restored.markdownContent,
        folderId: restored.folderId ?? '',
        tags: restored.tags.map((item) => item.name),
      });
      setSaveStatus('saved');
      updateNoteInList(restored);
      setAnalysis(null);
      setRelatedResources(null);
      void loadVersions(restored.id);
      void loadSidebars();
    } catch (restoreError) {
      setError(getErrorMessage(restoreError));
    } finally {
      setRestoringVersionId('');
    }
  };

  const handleAnalyze = async (force = false) => {
    if (!detail || !requireLogin()) {
      return;
    }
    if (saveStatus === 'dirty') {
      await saveDraft();
    }
    setAnalysisLoading(true);
    setError('');
    try {
      setAnalysis(await notesApi.analyze(detail.id, force));
    } catch (analyzeError) {
      setError(getErrorMessage(analyzeError));
    } finally {
      setAnalysisLoading(false);
    }
  };

  const handleRelatedResources = async () => {
    if (!detail || !requireLogin()) {
      return;
    }
    if (saveStatus === 'dirty') {
      await saveDraft();
    }
    setRelatedLoading(true);
    setError('');
    try {
      setRelatedResources(await notesApi.relatedResources(detail.id, 6));
    } catch (relatedError) {
      setError(getErrorMessage(relatedError));
    } finally {
      setRelatedLoading(false);
    }
  };

  const handleSemanticSearch = async () => {
    if (!requireLogin()) {
      return;
    }
    const query = semanticQuery.trim();
    if (!query) {
      return;
    }
    setSemanticLoading(true);
    setError('');
    try {
      setSemanticResults(await notesApi.semantic(query, 8));
    } catch (semanticError) {
      setError(getErrorMessage(semanticError));
    } finally {
      setSemanticLoading(false);
    }
  };

  const handleAskNoteAi = async () => {
    if (!detail || !requireLogin()) {
      return;
    }
    const message = chatInput.trim();
    if (!message || chatStreaming) {
      return;
    }
    if (saveStatus === 'dirty') {
      await saveDraft();
    }
    const userMessage: ChatMessage = {
      id: `user:${Date.now()}`,
      role: 'user',
      content: message,
    };
    const assistantId = `assistant:${Date.now()}`;
    setChatMessages((current) => [...current, userMessage, { id: assistantId, role: 'assistant', content: '' }]);
    setChatInput('');
    setChatError('');
    setChatStreaming(true);
    const abortController = new AbortController();
    chatAbortRef.current?.abort();
    chatAbortRef.current = abortController;
    try {
      const conversation = chatConversationId ? { conversationId: chatConversationId } : await conversationApi.createConversation();
      if (!chatConversationId) {
        setChatConversationId(conversation.conversationId);
      }
      await conversationApi.streamMessage(
        conversation.conversationId,
        {
          message,
          serviceType: 'TUTORING',
          webSearchEnabled: false,
          reasoningMode: 'NORMAL',
          voiceContext: {
            pageType: 'notes',
            pageTitle: 'AI 笔记本',
            currentPath: '/notes',
            source: 'notebook',
            conversationId: conversation.conversationId,
            noteId: detail.id,
            noteTitle: titleDraft.trim() || detail.title,
            noteExcerpt: buildNoteExcerpt(titleDraft, markdownDraft),
          },
        },
        {
          onEvent: (event) => {
            const chunk = readConversationChunk(event.data, event.event);
            if (!chunk) {
              return;
            }
            setChatMessages((current) => current.map((item) =>
              item.id === assistantId ? { ...item, content: item.content + chunk } : item
            ));
          },
          onDone: () => {
            chatAbortRef.current = null;
            setChatStreaming(false);
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
          onError: (streamError) => {
            chatAbortRef.current = null;
            setChatStreaming(false);
            setChatError(getErrorMessage(streamError));
          },
        },
        abortController.signal,
      );
    } catch (streamError) {
      if (!abortController.signal.aborted) {
        setChatError(getErrorMessage(streamError));
      }
      setChatStreaming(false);
    }
  };

  const handleStopChat = () => {
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;
    setChatStreaming(false);
  };

  const insertMarkdown = (before: string, after = '', placeholder = '内容') => {
    setMarkdownDraft((current) => `${current}${current.endsWith('\n') || !current ? '' : '\n'}${before}${placeholder}${after}`);
  };

  const insertTable = () => {
    insertMarkdown('| 项目 | 说明 |\n| --- | --- |\n| ', ' | 内容 |', '主题');
  };

  const insertMindMap = () => {
    insertMarkdown('```mermaid\nmindmap\n  root(主题)\n    ', '\n```', '分支');
  };

  const handleExitFullscreen = () => {
    if (saveStatus === 'dirty') {
      void saveDraft();
    }
    navigate(resolveNotebookReturnPath(location.state));
  };

  if (!isAuthenticated) {
    return (
      <div className="notebook-page flex min-h-screen items-center justify-center px-4 py-6">
        <div className="rounded-2xl bg-white/90 p-8 text-center shadow-sm shadow-slate-200/50 dark:bg-slate-900/80 dark:shadow-slate-950/20">
          <BookOpen className="mx-auto h-12 w-12 text-primary-500" />
          <h1 className="mt-4 text-xl font-semibold text-slate-950 dark:text-white">AI 笔记本</h1>
          <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">登录后创建笔记、自动保存，并围绕当前内容向 AI 提问。</p>
          <button
            type="button"
            onClick={() => openAuthModal('login', '登录后使用 AI 笔记本')}
            className="mt-5 inline-flex items-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-700"
          >
            <Sparkles className="h-4 w-4" />
            登录使用
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="notebook-page min-h-screen w-full space-y-4 px-3 py-3 sm:px-4 lg:px-5">
      <div className="flex flex-col gap-4 rounded-[1.4rem] border border-white/70 bg-white/92 px-5 py-4 shadow-[0_18px_48px_rgba(64,91,142,0.08)] dark:border-slate-800/70 dark:bg-slate-900/88 dark:shadow-slate-950/20 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={handleExitFullscreen}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-600 transition hover:bg-white hover:text-slate-900 hover:shadow-sm dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 sm:w-auto sm:px-3 sm:gap-2"
            title="退出全屏并返回工作台"
          >
            <Minimize2 className="h-4 w-4" />
            <span className="hidden text-sm font-medium sm:inline">退出全屏</span>
          </button>
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-primary-600 text-white shadow-lg shadow-primary-500/20">
            <BookOpen className="h-6 w-6" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold leading-tight text-slate-950 dark:text-white">AI 笔记本</h1>
            <p className="mt-1 truncate text-sm text-slate-500 dark:text-slate-400">目录、标签、编辑、检索和当前笔记问答集中处理</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <WorkbenchStat icon={<FileText className="h-3.5 w-3.5" />} label="笔记" value={String(total)} />
          <WorkbenchStat icon={<Folder className="h-3.5 w-3.5" />} label="目录" value={String(folders.length)} />
          <WorkbenchStat icon={<Tags className="h-3.5 w-3.5" />} label="标签" value={String(tags.length)} />
          <button
            type="button"
            onClick={() => void handleCreateNote()}
            disabled={creating}
            className="inline-flex h-11 items-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-lg shadow-primary-500/18 transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-70"
          >
            {creating ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            新建
          </button>
        </div>
      </div>

      {error ? (
        <div className="flex items-center gap-2 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          <TriangleAlert className="h-4 w-4" />
          {error}
        </div>
      ) : null}

      <div className="grid items-start gap-4 xl:grid-cols-[300px_minmax(0,1fr)_340px] 2xl:grid-cols-[320px_minmax(0,1fr)_380px]">
        <aside className="flex min-h-0 flex-col overflow-hidden rounded-[1.4rem] border border-white/70 bg-white/88 shadow-[0_18px_46px_rgba(64,91,142,0.08)] dark:border-slate-800/70 dark:bg-slate-900/86 dark:shadow-slate-950/20 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)]">
          <div className="px-5 py-5">
            <div className="flex items-center justify-between gap-2">
              <div>
                <div className="text-lg font-semibold text-slate-900 dark:text-slate-100">我的笔记</div>
                <div className="mt-1 text-sm text-slate-400">{selectedFolderName}</div>
              </div>
              <button
                type="button"
                onClick={() => void handleCreateNote()}
                disabled={creating}
                className="flex h-11 w-11 items-center justify-center rounded-xl bg-slate-900 text-white shadow-sm transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-100 dark:text-slate-950"
                title="新建笔记"
              >
                {creating ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              </button>
            </div>
            <label className="mt-4 flex h-11 items-center rounded-xl bg-slate-50/80 px-3 shadow-sm shadow-slate-200/25 transition focus-within:bg-white focus-within:shadow-md focus-within:shadow-primary-100/35 dark:bg-slate-950/60 dark:shadow-none dark:focus-within:bg-slate-900">
              <Search className="mr-2 h-4 w-4 shrink-0 text-slate-400" />
              <input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    setSubmittedKeyword(keyword.trim());
                  }
                }}
                placeholder="搜索标题或正文"
                className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400 dark:text-slate-200"
              />
              <button type="button" onClick={() => setSubmittedKeyword(keyword.trim())} className="rounded-md px-1.5 py-1 text-xs font-medium text-primary-600 hover:bg-primary-50 dark:text-primary-300 dark:hover:bg-primary-500/10">
                搜索
              </button>
            </label>
            {activeFilters.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {activeFilters.map((item) => (
                  <span key={item} className="max-w-full truncate rounded-full bg-white px-2 py-0.5 text-[11px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">{item}</span>
                ))}
                <button
                  type="button"
                  onClick={() => {
                    setKeyword('');
                    setSubmittedKeyword('');
                    setActiveFolderId('');
                    setActiveTag('');
                  }}
                  className="rounded-full px-2 py-0.5 text-[11px] font-medium text-primary-600 hover:bg-primary-50 dark:text-primary-300 dark:hover:bg-primary-500/10"
                >
                  清空
                </button>
              </div>
            ) : null}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            <section className="px-3 py-4">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
                <Folder className="h-4 w-4 text-primary-500" />
                笔记目录
              </div>
              {sideLoading ? <LoaderCircle className="h-4 w-4 animate-spin text-slate-400" /> : null}
            </div>
            <button
              type="button"
              onClick={() => setActiveFolderId('')}
              className={filterButtonClass(!activeFolderId)}
            >
              <span className="truncate">全部笔记</span>
              <span className="text-xs opacity-70">{total}</span>
            </button>
            <div className="mt-2 space-y-1">
              {folders.map((item) => (
                <div key={item.id} className={cn('group flex items-center gap-1 rounded-xl px-2 py-1.5', activeFolderId === item.id ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300' : 'text-slate-600 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800/80')}>
                  <button type="button" onClick={() => setActiveFolderId(item.id)} className="flex min-w-0 flex-1 items-center justify-between gap-2 text-left text-sm">
                    <span className="truncate">{item.name}</span>
                    <span className="text-xs opacity-70">{item.noteCount}</span>
                  </button>
                  <button type="button" onClick={() => void handleRenameFolder(item)} className="hidden h-7 w-7 items-center justify-center rounded-lg hover:bg-white/80 group-hover:flex dark:hover:bg-slate-900" title="重命名">
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" onClick={() => void handleDeleteFolder(item)} disabled={folderBusyId === item.id} className="hidden h-7 w-7 items-center justify-center rounded-lg text-rose-500 hover:bg-rose-50 group-hover:flex dark:hover:bg-rose-500/10" title="删除目录">
                    {folderBusyId === item.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                  </button>
                </div>
              ))}
            </div>
            <div className="mt-3 flex gap-2">
              <input
                value={folderName}
                onChange={(event) => setFolderName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    void handleCreateFolder();
                  }
                }}
                placeholder="新目录名称"
                className="min-w-0 flex-1 rounded-lg bg-white/82 px-3 py-2 text-sm outline-none shadow-sm shadow-slate-200/20 transition focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-900/68 dark:text-slate-200 dark:shadow-none dark:focus:bg-slate-900"
              />
              <button type="button" onClick={() => void handleCreateFolder()} disabled={!folderName.trim() || folderBusyId === 'new'} className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-900 text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-100 dark:text-slate-950">
                {folderBusyId === 'new' ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <FolderPlus className="h-4 w-4" />}
              </button>
            </div>
          </section>

          <section className="px-3 py-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
              <Tags className="h-4 w-4 text-primary-500" />
              标签
            </div>
            <button type="button" onClick={() => setActiveTag('')} className={filterButtonClass(!activeTag)}>
              <span>全部标签</span>
            </button>
            <div className="mt-2 flex flex-wrap gap-2">
              {tags.length > 0 ? tags.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setActiveTag(item.name)}
                  className={cn(
                    'inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors',
                    activeTag === item.name
                      ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
                      : 'bg-slate-50 text-slate-600 hover:bg-slate-100 dark:bg-slate-950 dark:text-slate-400 dark:hover:bg-slate-800',
                  )}
                >
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: item.color || '#64748b' }} />
                  <span className="truncate">{item.name}</span>
                  <span className="opacity-60">{item.count}</span>
                </button>
              )) : (
                <EmptyInline text="给笔记添加标签后会出现在这里。" />
              )}
            </div>
          </section>

          <section className="px-2 py-4">
            <div className="mb-2 flex items-center justify-between px-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">笔记列表</div>
              <span className="text-xs text-slate-400">{loading ? '同步中' : `${notes.length}/${total}`}</span>
            </div>
            {loading ? (
              <div className="flex h-36 items-center justify-center">
                <LoaderCircle className="h-6 w-6 animate-spin text-primary-500" />
              </div>
            ) : hasNotes ? notes.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => handleSelectNote(item.id)}
                className={cn(
                  'group mb-3 w-full rounded-2xl px-4 py-3 text-left transition-all',
                  selectedNoteId === item.id
                    ? 'bg-white text-primary-800 shadow-md shadow-primary-100/55 ring-1 ring-primary-100/80 dark:bg-primary-500/10 dark:text-primary-200 dark:shadow-none dark:ring-primary-500/15'
                    : 'bg-transparent text-slate-700 hover:bg-white dark:text-slate-300 dark:hover:bg-slate-900',
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">{item.title}</div>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{item.preview || '暂无正文'}</p>
                  </div>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleDeleteNote(item.id);
                    }}
                    disabled={deletingId === item.id}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 opacity-0 hover:bg-rose-50 hover:text-rose-500 group-hover:opacity-100 disabled:opacity-60 dark:hover:bg-rose-500/10"
                    title="删除笔记"
                  >
                    {deletingId === item.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {item.tags.slice(0, 2).map((tag) => (
                    <span key={tag.id} className="max-w-[92px] truncate rounded-full bg-white/80 px-2 py-0.5 text-[11px] text-slate-500 dark:bg-slate-900/70 dark:text-slate-400">{tag.name}</span>
                  ))}
                  <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-slate-400">
                    {item.ragIndexed ? <CheckCircle2 className="h-3 w-3 text-emerald-500" /> : <Clock3 className="h-3 w-3" />}
                    {item.wordCount} 字
                  </span>
                </div>
              </button>
            )) : (
              <div className="mx-2 flex h-64 flex-col items-center justify-center rounded-xl bg-white px-6 text-center dark:bg-slate-900">
                <FileText className="h-9 w-9 text-slate-300 dark:text-slate-600" />
                <div className="mt-3 text-sm font-semibold text-slate-700 dark:text-slate-200">暂无笔记</div>
                <p className="mt-1 text-xs leading-5 text-slate-400">创建第一条笔记后，可以用标签、目录和语义搜索组织知识。</p>
                <button type="button" onClick={() => void handleCreateNote()} className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700">
                  <Plus className="h-4 w-4" />
                  新建笔记
                </button>
              </div>
            )}
          </section>
          </div>
        </aside>

        <main className="flex min-h-[780px] min-w-0 flex-col overflow-hidden rounded-[1.4rem] border border-white/70 bg-white shadow-[0_22px_56px_rgba(64,91,142,0.1)] dark:border-slate-800/70 dark:bg-slate-900 dark:shadow-slate-950/22 xl:min-h-[calc(100vh-2rem)]">
          {detailLoading ? (
            <div className="flex min-h-[620px] flex-1 items-center justify-center">
              <LoaderCircle className="h-7 w-7 animate-spin text-primary-500" />
            </div>
          ) : detail ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-slate-100 px-7 py-6 dark:border-slate-800/80">
                <div className="flex flex-col gap-4 2xl:flex-row 2xl:items-start 2xl:justify-between">
                  <div className="min-w-0 flex-1">
                    <input
                      value={titleDraft}
                      onChange={(event) => setTitleDraft(event.target.value)}
                      className="w-full min-w-0 bg-transparent text-3xl font-semibold leading-tight text-slate-950 outline-none placeholder:text-slate-400 dark:text-white"
                      placeholder="笔记标题"
                    />
                    <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-slate-500 dark:text-slate-400">
                      <span>上次保存：{formatDate(detail.lastSavedAt ?? detail.updatedAt)}</span>
                      <span className="h-3 w-px bg-slate-200 dark:bg-slate-700" />
                      <span>{detail.ragIndexed ? '已可智能检索' : '正在同步内容'}</span>
                      <SaveStatusBadge status={saveStatus} error={saveError} />
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void saveDraft()}
                    disabled={saveStatus === 'saving'}
                    className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-900 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-100 dark:text-slate-950"
                  >
                    {saveStatus === 'saving' ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                    保存
                  </button>
                </div>

                <div className="mt-5 grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
                  <label className="flex min-w-0 items-center gap-2 rounded-xl bg-slate-50/80 px-4 py-3 shadow-sm shadow-slate-200/20 transition focus-within:bg-white focus-within:shadow-md focus-within:shadow-primary-100/30 dark:bg-slate-950/60 dark:shadow-none dark:focus-within:bg-slate-950/86">
                    <Tags className="h-4 w-4 shrink-0 text-slate-400" />
                    <input
                      value={tagInput}
                      onChange={(event) => setTagInput(event.target.value)}
                      placeholder="标签，用逗号分隔"
                      className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400 dark:text-slate-200"
                    />
                  </label>
                  <label className="flex items-center gap-2 rounded-xl bg-slate-50/80 px-4 py-3 shadow-sm shadow-slate-200/20 transition focus-within:bg-white focus-within:shadow-md focus-within:shadow-primary-100/30 dark:bg-slate-950/60 dark:shadow-none dark:focus-within:bg-slate-950/86">
                    <Folder className="h-4 w-4 shrink-0 text-slate-400" />
                    <select
                      value={folderDraftId}
                      onChange={(event) => setFolderDraftId(event.target.value)}
                      className="min-w-0 flex-1 bg-transparent text-sm outline-none dark:text-slate-200"
                    >
                      <option value="">未分类</option>
                      {folders.map((item) => (
                        <option key={item.id} value={item.id}>{item.name}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-slate-50/72 px-6 py-4 dark:border-slate-800/80 dark:bg-slate-950/30">
                <div className="flex flex-wrap items-center gap-2">
                  <SegmentButton active={inputMode === 'markdown'} onClick={() => setInputMode('markdown')} icon={<FileText className="h-4 w-4" />} label="Markdown" />
                  <SegmentButton active={inputMode === 'rich'} onClick={() => setInputMode('rich')} icon={<Pencil className="h-4 w-4" />} label="富文本" />
                  <span className="mx-1 h-6 w-px bg-slate-200 dark:bg-slate-700" />
                  <IconTool title="标题" onClick={() => insertMarkdown('## ', '', '小标题')} icon={<Heading2 className="h-4 w-4" />} />
                  <IconTool title="加粗" onClick={() => insertMarkdown('**', '**', '重点')} icon={<Bold className="h-4 w-4" />} />
                  <IconTool title="斜体" onClick={() => insertMarkdown('*', '*', '术语')} icon={<Italic className="h-4 w-4" />} />
                  <IconTool title="列表" onClick={() => insertMarkdown('- ', '', '条目')} icon={<List className="h-4 w-4" />} />
                  <IconTool title="待办" onClick={() => insertMarkdown('- [ ] ', '', '待办')} icon={<ListChecks className="h-4 w-4" />} />
                  <IconTool title="代码" onClick={() => insertMarkdown('```\n', '\n```', 'code')} icon={<Code2 className="h-4 w-4" />} />
                  <IconTool title="链接" onClick={() => insertMarkdown('[', '](https://)', '链接文字')} icon={<Link2 className="h-4 w-4" />} />
                  <IconTool title="图片" onClick={() => insertMarkdown('![', '](https://)', '图片说明')} icon={<Image className="h-4 w-4" />} />
                  <IconTool title="表格" onClick={insertTable} icon={<Table2 className="h-4 w-4" />} />
                  <IconTool title="思维导图" onClick={insertMindMap} icon={<GitFork className="h-4 w-4" />} />
                </div>
                <div className="flex items-center gap-2">
                  <SegmentButton active={editorMode === 'write'} onClick={() => setEditorMode('write')} icon={<Pencil className="h-4 w-4" />} label="编辑" />
                  <SegmentButton active={editorMode === 'split'} onClick={() => setEditorMode('split')} icon={<Square className="h-4 w-4" />} label="分栏" />
                  <SegmentButton active={editorMode === 'preview'} onClick={() => setEditorMode('preview')} icon={<Eye className="h-4 w-4" />} label="预览" />
                </div>
              </div>

              <div className={cn('grid min-h-0 flex-1 overflow-hidden bg-white dark:bg-slate-900', editorMode === 'split' ? 'lg:grid-cols-2' : 'grid-cols-1')}>
                {editorMode !== 'preview' ? (
                  <div className={cn('min-h-[620px] min-w-0', editorMode === 'split' ? 'lg:border-r lg:border-slate-100 lg:bg-slate-50/36 lg:dark:border-slate-800/80 lg:dark:bg-slate-950/20' : '')}>
                    {inputMode === 'markdown' ? (
                      <textarea
                        value={markdownDraft}
                        onChange={(event) => setMarkdownDraft(event.target.value)}
                        spellCheck={false}
                        className="h-full min-h-[620px] w-full resize-none bg-transparent px-8 py-7 font-mono text-[15px] leading-8 text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-200"
                        placeholder="记录概念、推导、例题、疑问和复盘。"
                      />
                    ) : (
                      <RichTextEditor markdown={markdownDraft} onChange={setMarkdownDraft} />
                    )}
                  </div>
                ) : null}
                {editorMode !== 'write' ? (
                  <div className="min-h-[620px] min-w-0 overflow-y-auto bg-white px-8 py-7 dark:bg-slate-900">
                    {markdownDraft.trim() ? (
                      <MarkdownRenderer content={markdownDraft} />
                    ) : (
                      <div className="flex h-full min-h-[460px] items-center justify-center rounded-2xl bg-slate-50 text-sm text-slate-400 dark:bg-slate-950/70 dark:text-slate-500">
                        预览区会实时展示笔记内容
                      </div>
                    )}
                  </div>
                ) : null}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 bg-slate-50/72 px-6 py-3 text-xs text-slate-500 dark:border-slate-800/80 dark:bg-slate-950/30 dark:text-slate-400">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill icon={<FileText className="h-3.5 w-3.5" />} label={`${draftWordCount} 字`} />
                  <StatusPill icon={<Clock3 className="h-3.5 w-3.5" />} label={`${draftReadingMinutes} 分钟`} />
                </div>
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <button type="button" onClick={() => void handleAnalyze(Boolean(analysis))} disabled={!detail || analysisLoading} className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 font-medium text-primary-600 shadow-sm shadow-slate-200/30 transition hover:bg-primary-50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-900 dark:text-primary-300 dark:shadow-none">
                    {analysisLoading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                    AI 总结
                  </button>
                  <button type="button" onClick={() => void handleRelatedResources()} disabled={!detail || relatedLoading} className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 font-medium text-slate-600 shadow-sm shadow-slate-200/30 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-900 dark:text-slate-300 dark:shadow-none">
                    {relatedLoading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Network className="h-3.5 w-3.5" />}
                    插入资源
                  </button>
                  <span className="truncate">内容会自动保存并写入版本历史</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex min-h-[620px] flex-1 items-center justify-center px-6 text-center">
              <div>
                <BookOpen className="mx-auto h-12 w-12 text-slate-300 dark:text-slate-600" />
                <div className="mt-3 text-sm font-semibold text-slate-700 dark:text-slate-200">选择或创建一条笔记</div>
                <p className="mt-1 text-xs leading-5 text-slate-400">保存后即可继续编辑，也可以让 AI 基于当前内容回答。</p>
              </div>
            </div>
          )}
        </main>

        <aside className="flex min-h-0 flex-col overflow-hidden rounded-[1.4rem] border border-white/70 bg-white/88 shadow-[0_18px_46px_rgba(64,91,142,0.08)] dark:border-slate-800/70 dark:bg-slate-900/86 dark:shadow-slate-950/20 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)]">
          <div className="border-b border-slate-100 px-5 py-5 dark:border-slate-800/80">
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
                  <Brain className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <div className="text-lg font-semibold text-slate-900 dark:text-slate-100">AI 助手</div>
                  <div className="truncate text-sm text-slate-400">{detail ? '基于当前笔记上下文' : '选择笔记后启用'}</div>
                </div>
              </div>
              <div className="flex items-center gap-2 text-slate-400">
                <button type="button" className="flex h-9 w-9 items-center justify-center rounded-xl hover:bg-slate-100 dark:hover:bg-slate-800" title="展开面板">
                  <Maximize2 className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2">
              <AiMiniStat label="字数" value={String(draftWordCount)} />
              <AiMiniStat label="阅读" value={`${draftReadingMinutes}分`} />
              <AiMiniStat label="版本" value={String(versions.length)} />
            </div>
          </div>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
            <section className="rounded-2xl bg-white p-4 shadow-sm shadow-slate-200/45 ring-1 ring-slate-100/80 dark:bg-slate-900 dark:shadow-none dark:ring-slate-800/80">
              <PanelHeader
                icon={<Sparkles className="h-4 w-4" />}
                title="自动摘要"
                action={(
                  <button type="button" onClick={() => void handleAnalyze(Boolean(analysis))} disabled={!detail || analysisLoading} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-primary-600 hover:bg-primary-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-primary-300 dark:hover:bg-primary-500/10">
                    {analysisLoading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                    {analysis ? '重写' : '生成'}
                  </button>
                )}
              />
              {analysis ? (
                <p className="mt-4 rounded-xl bg-slate-50 px-4 py-3 text-sm leading-7 text-slate-700 dark:bg-slate-950/70 dark:text-slate-300">{analysis.summary || '暂无摘要'}</p>
              ) : (
                <EmptyInline text="生成摘要、知识点和待办建议。" spacious />
              )}
            </section>

            <section className="rounded-2xl bg-white p-4 shadow-sm shadow-slate-200/45 ring-1 ring-slate-100/80 dark:bg-slate-900 dark:shadow-none dark:ring-slate-800/80">
              <PanelHeader icon={<ListChecks className="h-4 w-4" />} title="知识点与待办" />
              <div className="mt-4">
                <div className="mb-2 text-xs font-medium text-slate-500 dark:text-slate-400">知识点</div>
                <div className="flex flex-wrap gap-2">
                  {analysis?.keywords.length ? analysis.keywords.map((item) => (
                    <span key={item} className="rounded-full bg-primary-50 px-3 py-1.5 text-xs font-medium text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">{item}</span>
                  )) : <EmptyInline text="未提取到知识点。" spacious />}
                </div>
              </div>
              <div className="mt-4">
                <div className="mb-2 text-xs font-medium text-slate-500 dark:text-slate-400">待办建议</div>
                <div className="space-y-2">
                  {analysis?.todos.length ? analysis.todos.map((todo) => (
                    <div key={`${todo.title}-${todo.priority}`} className="flex items-start gap-3 rounded-xl bg-slate-50 px-3 py-3 text-sm dark:bg-slate-950/70">
                      <ListChecks className="mt-0.5 h-4 w-4 shrink-0 text-primary-500" />
                      <div className="min-w-0 flex-1">
                        <div className="line-clamp-2 text-slate-700 dark:text-slate-300">{todo.title}</div>
                        <div className="mt-1 text-[11px] text-slate-400">{todo.priority}</div>
                      </div>
                    </div>
                  )) : <EmptyInline text="暂无待办建议。" spacious />}
                </div>
              </div>
            </section>

            <section className="rounded-2xl bg-white p-4 shadow-sm shadow-slate-200/45 ring-1 ring-slate-100/80 dark:bg-slate-900 dark:shadow-none dark:ring-slate-800/80">
              <PanelHeader
                icon={<Network className="h-4 w-4" />}
                title="关联资源"
                action={(
                  <button type="button" onClick={() => void handleRelatedResources()} disabled={!detail || relatedLoading} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-primary-600 hover:bg-primary-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-primary-300 dark:hover:bg-primary-500/10">
                    {relatedLoading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                    推荐
                  </button>
                )}
              />
              <div className="mt-4 space-y-3">
                {relatedResources?.results.length ? relatedResources.results.slice(0, 5).map((item) => (
                  <a key={item.resourceId} href={`/resources?resourceId=${item.resourceId}`} className="block rounded-xl bg-slate-50 px-3 py-3 text-sm transition hover:bg-white hover:shadow-sm hover:shadow-slate-200/50 dark:bg-slate-950/70 dark:hover:bg-slate-900 dark:hover:shadow-none">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate font-medium text-slate-800 dark:text-slate-100">{item.resource?.title ?? '相关资源'}</div>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{item.reason || item.resource?.summaryText || '语义相关资源'}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                          <span>{item.resource?.displayType || item.resource?.resourceType || '资源'}</span>
                          {item.resource?.durationMinutes ? <span>{item.resource.durationMinutes} 分钟</span> : null}
                          <span>匹配 {Math.round(item.score * 100)}%</span>
                        </div>
                      </div>
                      <ExternalLink className="h-4 w-4 shrink-0 text-slate-400" />
                    </div>
                  </a>
                )) : (
                  <EmptyInline text={relatedResources?.message || '基于当前笔记语义推荐学习资源。'} spacious />
                )}
              </div>
            </section>

            <section className="rounded-2xl bg-white p-4 shadow-sm shadow-slate-200/45 ring-1 ring-slate-100/80 dark:bg-slate-900 dark:shadow-none dark:ring-slate-800/80">
              <PanelHeader icon={<Search className="h-4 w-4" />} title="语义搜索" />
              <div className="mt-4 flex gap-2">
                <input
                  value={semanticQuery}
                  onChange={(event) => setSemanticQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      void handleSemanticSearch();
                    }
                  }}
                  placeholder="用问题检索笔记"
                  className="min-w-0 flex-1 rounded-xl bg-slate-50/80 px-3 py-2.5 text-sm outline-none shadow-sm shadow-slate-200/18 transition focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-950/60 dark:text-slate-200 dark:shadow-none dark:focus:bg-slate-950/86"
                />
                <button type="button" onClick={() => void handleSemanticSearch()} disabled={semanticLoading || !semanticQuery.trim()} className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-900 text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-100 dark:text-slate-950">
                  {semanticLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                </button>
              </div>
              <div className="mt-4 space-y-2">
                {semanticResults?.results.length ? semanticResults.results.map((item) => (
                  <button key={item.note.id} type="button" onClick={() => handleSelectNote(item.note.id)} className="w-full rounded-xl bg-slate-50 px-3 py-3 text-left transition hover:bg-white dark:bg-slate-950/70 dark:hover:bg-slate-900">
                    <div className="flex items-center justify-between gap-2">
                      <div className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{item.note.title}</div>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{item.reason || item.hits[0]?.content || item.note.preview}</p>
                  </button>
                )) : (
                  <EmptyInline text={semanticResults?.message || '保存后可用问题快速找到相关笔记。'} spacious />
                )}
              </div>
            </section>

            <section className="rounded-2xl bg-white p-4 shadow-sm shadow-slate-200/45 ring-1 ring-slate-100/80 dark:bg-slate-900 dark:shadow-none dark:ring-slate-800/80">
              <PanelHeader
                icon={<History className="h-4 w-4" />}
                title="版本历史"
                action={(
                  <button type="button" onClick={() => detail && void loadVersions(detail.id)} disabled={!detail || versionsLoading} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-primary-600 hover:bg-primary-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-primary-300 dark:hover:bg-primary-500/10">
                    {versionsLoading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                    刷新
                  </button>
                )}
              />
              <div className="mt-4 max-h-64 space-y-2 overflow-y-auto">
                {versions.length > 0 ? versions.map((item) => (
                  <div key={item.id} className="rounded-xl bg-slate-50 px-3 py-3 dark:bg-slate-950/70">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-slate-800 dark:text-slate-100">版本 {item.versionNo}</div>
                        <div className="mt-0.5 truncate text-[11px] text-slate-400">{formatDate(item.createdAt)} · {item.changeSummary}</div>
                      </div>
                      <button type="button" onClick={() => void handleRestoreVersion(item)} disabled={Boolean(restoringVersionId)} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-primary-600 hover:bg-primary-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-primary-300 dark:hover:bg-primary-500/10">
                        {restoringVersionId === item.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                        恢复
                      </button>
                    </div>
                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{item.plainText || item.title}</p>
                  </div>
                )) : (
                  <EmptyInline text="保存后会产生版本历史。" spacious />
                )}
              </div>
            </section>
          </div>

          <section className="border-t border-slate-100 bg-white px-4 py-4 dark:border-slate-800/80 dark:bg-slate-900">
            <PanelHeader icon={<MessageCircle className="h-4 w-4" />} title="当前笔记问答" />
            <div className="mt-3 max-h-60 space-y-2 overflow-y-auto rounded-2xl bg-slate-50 p-3 dark:bg-slate-950/70">
              {chatMessages.length > 0 ? chatMessages.map((message) => (
                <div key={message.id} className={cn('rounded-xl px-3 py-2 text-sm leading-6', message.role === 'user' ? 'ml-8 bg-primary-600 text-white' : 'mr-8 bg-white text-slate-700 shadow-sm shadow-slate-200/40 dark:bg-slate-900 dark:text-slate-300 dark:shadow-none')}>
                  {message.role === 'assistant' ? <MarkdownRenderer content={message.content} isStreaming={chatStreaming && !message.content.trim()} /> : message.content}
                </div>
              )) : (
                <EmptyInline text="提问时会携带当前笔记标题和正文摘录。" spacious />
              )}
            </div>
            {chatError ? (
              <div className="mt-2 flex items-center gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600 dark:bg-rose-500/10 dark:text-rose-300">
                <XCircle className="h-3.5 w-3.5" />
                {chatError}
              </div>
            ) : null}
            <div className="mt-3 flex gap-2">
              <input
                value={chatInput}
                onChange={(event) => setChatInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    void handleAskNoteAi();
                  }
                }}
                placeholder="基于当前笔记提问"
                className="min-w-0 flex-1 rounded-xl bg-slate-50/80 px-3 py-2.5 text-sm outline-none shadow-sm shadow-slate-200/18 transition focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-950/60 dark:text-slate-200 dark:shadow-none dark:focus:bg-slate-950/86"
              />
              {chatStreaming ? (
                <button type="button" onClick={handleStopChat} className="flex h-11 w-11 items-center justify-center rounded-xl bg-rose-600 text-white hover:bg-rose-700" title="停止">
                  <Square className="h-4 w-4 fill-current" />
                </button>
              ) : (
                <button type="button" onClick={() => void handleAskNoteAi()} disabled={!detail || !chatInput.trim()} className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary-600 text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50" title="发送">
                  <Send className="h-4 w-4" />
                </button>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function RichTextEditor(props: { markdown: string; onChange: (markdown: string) => void }) {
  const editorRef = useRef<HTMLDivElement>(null);
  const focusedRef = useRef(false);
  const appliedMarkdownRef = useRef('');

  useEffect(() => {
    if (!editorRef.current) {
      return;
    }
    if (focusedRef.current && appliedMarkdownRef.current === props.markdown) {
      return;
    }
    editorRef.current.innerHTML = markdownToRichHtml(props.markdown);
    appliedMarkdownRef.current = props.markdown;
  }, [props.markdown]);

  const emitChange = () => {
    const nextMarkdown = htmlToMarkdown(editorRef.current?.innerHTML ?? '');
    appliedMarkdownRef.current = nextMarkdown;
    props.onChange(nextMarkdown);
  };

  const runCommand = (command: string) => {
    document.execCommand(command, false);
    emitChange();
  };

  return (
    <div className="flex h-full min-h-[560px] flex-col">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <IconTool title="加粗" onClick={() => runCommand('bold')} icon={<Bold className="h-4 w-4" />} />
        <IconTool title="斜体" onClick={() => runCommand('italic')} icon={<Italic className="h-4 w-4" />} />
        <IconTool title="无序列表" onClick={() => runCommand('insertUnorderedList')} icon={<List className="h-4 w-4" />} />
        <IconTool title="有序列表" onClick={() => runCommand('insertOrderedList')} icon={<ListChecks className="h-4 w-4" />} />
      </div>
      <div
        ref={editorRef}
        contentEditable
        suppressContentEditableWarning
        onFocus={() => {
          focusedRef.current = true;
        }}
        onBlur={() => {
          focusedRef.current = false;
          emitChange();
        }}
        onInput={emitChange}
        className="rich-note-editor min-h-0 flex-1 overflow-y-auto p-4 text-sm leading-7 text-slate-800 outline-none dark:text-slate-200 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:text-lg [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_p]:my-2"
      />
    </div>
  );
}

function StatusPill(props: { icon: React.ReactNode; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
      {props.icon}
      {props.label}
    </span>
  );
}

function WorkbenchStat(props: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <span className="inline-flex h-11 items-center gap-2 rounded-xl bg-slate-50 px-3 text-sm text-slate-500 shadow-sm shadow-slate-200/20 dark:bg-slate-950/70 dark:text-slate-400 dark:shadow-none">
      <span className="text-primary-500">{props.icon}</span>
      <span>{props.label}</span>
      <span className="font-semibold text-slate-800 dark:text-slate-100">{props.value}</span>
    </span>
  );
}

function AiMiniStat(props: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2 text-center dark:bg-slate-950/60">
      <div className="truncate text-[11px] text-slate-400">{props.label}</div>
      <div className="mt-0.5 truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{props.value}</div>
    </div>
  );
}

function PanelHeader(props: { icon: React.ReactNode; title: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-2 text-base font-semibold text-slate-800 dark:text-slate-100">
        <span className="text-primary-500">{props.icon}</span>
        <span className="truncate">{props.title}</span>
      </div>
      {props.action}
    </div>
  );
}

function SegmentButton(props: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      className={cn(
        'inline-flex h-9 items-center gap-1.5 rounded-xl px-3 text-sm font-medium transition-colors',
        props.active
          ? 'bg-primary-600 text-white'
          : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700',
      )}
    >
      {props.icon}
      {props.label}
    </button>
  );
}

function IconTool(props: { title: string; onClick: () => void; icon: React.ReactNode }) {
  return (
    <button
      type="button"
      title={props.title}
      onMouseDown={(event) => event.preventDefault()}
      onClick={props.onClick}
      className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-100 text-slate-600 transition hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
    >
      {props.icon}
    </button>
  );
}

function SaveStatusBadge(props: { status: SaveStatus; error: string }) {
  if (props.status === 'saving') {
    return <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700 dark:bg-blue-500/10 dark:text-blue-300"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />保存中</span>;
  }
  if (props.status === 'dirty') {
    return <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"><Clock3 className="h-3.5 w-3.5" />等待自动保存</span>;
  }
  if (props.status === 'error') {
    return <span className="inline-flex max-w-[220px] items-center gap-1.5 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 dark:bg-rose-500/10 dark:text-rose-300" title={props.error}><TriangleAlert className="h-3.5 w-3.5" />保存失败</span>;
  }
  if (props.status === 'saved') {
    return <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"><CheckCircle2 className="h-3.5 w-3.5" />已保存</span>;
  }
  return <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400"><Clock3 className="h-3.5 w-3.5" />未修改</span>;
}

function EmptyInline(props: { text: string; spacious?: boolean }) {
  return (
    <div className={cn(
      'rounded-xl bg-slate-50 px-3 text-xs leading-5 text-slate-400 dark:bg-slate-950/70 dark:text-slate-500',
      props.spacious ? 'py-3' : 'py-2',
    )}>
      {props.text}
    </div>
  );
}

function filterButtonClass(active: boolean): string {
  return cn(
    'mt-2 flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left text-sm transition-colors',
    active
      ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
      : 'text-slate-600 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800/80',
  );
}

function buildSnapshot(snapshot: NoteDraftSnapshot): string {
  return JSON.stringify({
    title: snapshot.title.trim(),
    markdown: snapshot.markdown,
    folderId: snapshot.folderId || '',
    tags: [...snapshot.tags].map((item) => item.trim()).filter(Boolean).sort((left, right) => left.localeCompare(right, 'zh-CN')),
  });
}

function parseTagInput(input: string): string[] {
  const seen = new Set<string>();
  const tags: string[] = [];
  input.split(/[,，\n]/).forEach((raw) => {
    const tag = raw.trim();
    if (!tag || seen.has(tag)) {
      return;
    }
    seen.add(tag);
    tags.push(tag.slice(0, 32));
  });
  return tags.slice(0, 12);
}

function tagsToInput(tags: string[]): string {
  return tags.join('，');
}

function markdownToPlainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*]\([^)]+\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]+\)/g, '$1')
    .replace(/[#>*_`~\-+[\\\]]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function countWords(text: string): number {
  if (!text.trim()) {
    return 0;
  }
  const cjkMatches = text.match(/[\u4e00-\u9fff]/g) ?? [];
  const latinMatches = text.replace(/[\u4e00-\u9fff]/g, ' ').match(/[A-Za-z0-9]+/g) ?? [];
  return cjkMatches.length + latinMatches.length;
}

function detailToListItem(note: NoteDetail): NoteListItem {
  return {
    id: note.id,
    folderId: note.folderId,
    title: note.title,
    preview: note.plainText.slice(0, 180),
    tags: note.tags,
    wordCount: note.wordCount,
    readingMinutes: note.readingMinutes,
    lastSavedAt: note.lastSavedAt,
    updatedAt: note.updatedAt,
    ragIndexed: note.ragIndexed,
  };
}

function sortFolders(left: NoteFolder, right: NoteFolder): number {
  if (left.sortOrder !== right.sortOrder) {
    return left.sortOrder - right.sortOrder;
  }
  return left.name.localeCompare(right.name, 'zh-CN');
}

function formatDate(value?: string): string {
  if (!value) {
    return '--';
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    return value;
  }
  return new Date(timestamp).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function buildNoteExcerpt(title: string, markdown: string): string {
  const text = markdownToPlainText(markdown);
  return [`标题：${title.trim() || '未命名笔记'}`, `正文摘录：${text.slice(0, 1800)}`].join('\n');
}

function markdownToRichHtml(markdown: string): string {
  if (!markdown.trim()) {
    return '<p><br></p>';
  }
  return markdown.split(/\r?\n/).map((line) => {
    if (!line.trim()) {
      return '<p><br></p>';
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length, 3);
      return `<h${level}>${inlineMarkdownToHtml(heading[2])}</h${level}>`;
    }
    const task = /^-\s+\[( |x)]\s+(.+)$/i.exec(line);
    if (task) {
      return `<p>${task[1].toLowerCase() === 'x' ? '☑' : '☐'} ${inlineMarkdownToHtml(task[2])}</p>`;
    }
    const bullet = /^[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      return `<ul><li>${inlineMarkdownToHtml(bullet[1])}</li></ul>`;
    }
    return `<p>${inlineMarkdownToHtml(line)}</p>`;
  }).join('');
}

function inlineMarkdownToHtml(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function htmlToMarkdown(html: string): string {
  if (typeof document === 'undefined') {
    return '';
  }
  const root = document.createElement('div');
  root.innerHTML = html;
  const lines: string[] = [];
  root.childNodes.forEach((node) => {
    const text = nodeToMarkdown(node).trimEnd();
    if (text) {
      lines.push(text);
    }
  });
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function nodeToMarkdown(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent ?? '';
  }
  if (!(node instanceof HTMLElement)) {
    return '';
  }
  const tag = node.tagName.toLowerCase();
  const children = Array.from(node.childNodes).map(nodeToMarkdown).join('');
  if (tag === 'br') {
    return '\n';
  }
  if (tag === 'strong' || tag === 'b') {
    return `**${children}**`;
  }
  if (tag === 'em' || tag === 'i') {
    return `*${children}*`;
  }
  if (tag === 'code') {
    return `\`${children}\``;
  }
  if (tag === 'h1') {
    return `# ${children}\n`;
  }
  if (tag === 'h2') {
    return `## ${children}\n`;
  }
  if (tag === 'h3') {
    return `### ${children}\n`;
  }
  if (tag === 'li') {
    return `- ${children.trim()}\n`;
  }
  if (tag === 'ul' || tag === 'ol') {
    return `${children}\n`;
  }
  if (tag === 'p' || tag === 'div') {
    return `${children}\n`;
  }
  return children;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ');
}

function resolveNotebookReturnPath(state: unknown): string {
  if (!state || typeof state !== 'object') {
    return NOTE_EXIT_FALLBACK_ROUTE;
  }
  const value = (state as { returnTo?: unknown }).returnTo;
  if (typeof value !== 'string') {
    return NOTE_EXIT_FALLBACK_ROUTE;
  }
  const trimmed = value.trim();
  if (!trimmed || !trimmed.startsWith('/') || trimmed.startsWith('//') || trimmed.startsWith('/notes')) {
    return NOTE_EXIT_FALLBACK_ROUTE;
  }
  return trimmed;
}
