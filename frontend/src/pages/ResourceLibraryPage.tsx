import { useCallback, useEffect, useState, type ComponentType } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';
import {
  BarChart3,
  BookOpen,
  Bookmark,
  BookmarkCheck,
  Boxes,
  CheckCircle2,
  ExternalLink,
  FileText,
  Filter,
  GraduationCap,
  Grid2X2,
  Layers3,
  LoaderCircle,
  PlayCircle,
  RefreshCw,
  Search,
  Sparkles,
  Tags,
  TriangleAlert,
} from 'lucide-react';
import type { LayoutOutletContext } from '../components/Layout';
import { getErrorMessage } from '../api/request';
import {
  resourcesApi,
  type ResourceDetailResponse,
  type ResourceDisplayType,
  type ResourceItem,
  type ResourceSemanticSearchResponse,
  type ResourceStatsResponse,
  type ResourceTag,
} from '../api/resources';

const PAGE_SIZE = 12;

const TYPE_TABS: Array<{ value: 'ALL' | ResourceDisplayType; label: string; icon: ComponentType<{ className?: string }> }> = [
  { value: 'ALL', label: '全部资源', icon: Grid2X2 },
  { value: 'COURSE', label: '课程', icon: GraduationCap },
  { value: 'DOCUMENT', label: '文档', icon: FileText },
  { value: 'VIDEO', label: '视频', icon: PlayCircle },
  { value: 'CASE', label: '案例', icon: Boxes },
  { value: 'NOTE', label: '笔记', icon: BookOpen },
];

const DIFFICULTIES = [
  { value: '', label: '全部难度' },
  { value: 'BASIC', label: '基础' },
  { value: 'INTERMEDIATE', label: '进阶' },
  { value: 'ADVANCED', label: '高级' },
  { value: 'MIXED', label: '综合' },
];

const SORT_OPTIONS = [
  { value: 'comprehensive', label: '综合排序' },
  { value: 'latest', label: '最新' },
  { value: 'popular', label: '最热' },
  { value: 'progress', label: '学习进度' },
  { value: 'quality', label: '质量优先' },
];

const CS_CATEGORIES = [
  { value: '', label: '全部方向' },
  { value: 'PROGRAMMING_LANGUAGES', label: '编程语言' },
  { value: 'DATA_STRUCTURES_ALGORITHMS', label: '数据结构/算法' },
  { value: 'OPERATING_SYSTEMS', label: '操作系统' },
  { value: 'COMPUTER_NETWORKS', label: '计算机网络' },
  { value: 'DATABASES', label: '数据库' },
  { value: 'SOFTWARE_ENGINEERING', label: '软件工程' },
  { value: 'COMPILERS', label: '编译器' },
  { value: 'COMPUTER_ARCHITECTURE', label: '计算机体系结构' },
  { value: 'AI_ML', label: 'AI/ML' },
  { value: 'SECURITY', label: '安全' },
  { value: 'DISTRIBUTED_CLOUD', label: '分布式/云原生' },
  { value: 'FRONTEND_WEB', label: '前端 Web' },
  { value: 'BACKEND_SYSTEMS', label: '后端系统' },
  { value: 'MATH_FOUNDATIONS', label: '数学基础' },
  { value: 'DEV_TOOLS', label: '开发工具' },
];

const CATEGORY_LABELS = new Map(CS_CATEGORIES.map((item) => [item.value, item.label]));

const TYPE_STYLE: Record<string, { label: string; className: string; icon: ComponentType<{ className?: string }> }> = {
  COURSE: { label: '课程', icon: GraduationCap, className: 'bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300' },
  DOCUMENT: { label: '文档', icon: FileText, className: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300' },
  VIDEO: { label: '视频', icon: PlayCircle, className: 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300' },
  CASE: { label: '案例', icon: Boxes, className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300' },
  NOTE: { label: '笔记', icon: BookOpen, className: 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300' },
};

export default function ResourceLibraryPage() {
  const { isAuthenticated, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState('');
  const [submittedKeyword, setSubmittedKeyword] = useState('');
  const [semanticQuery, setSemanticQuery] = useState('');
  const [activeType, setActiveType] = useState<'ALL' | ResourceDisplayType>('ALL');
  const [difficulty, setDifficulty] = useState('');
  const [source, setSource] = useState('');
  const [category, setCategory] = useState('');
  const [subcategory, setSubcategory] = useState('');
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [sort, setSort] = useState('comprehensive');
  const [page, setPage] = useState(0);
  const [items, setItems] = useState<ResourceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [stats, setStats] = useState<ResourceStatsResponse | null>(null);
  const [tags, setTags] = useState<ResourceTag[]>([]);
  const [recommendations, setRecommendations] = useState<ResourceItem[]>([]);
  const [detail, setDetail] = useState<ResourceDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [savingId, setSavingId] = useState('');
  const [semantic, setSemantic] = useState<ResourceSemanticSearchResponse | null>(null);
  const [semanticLoading, setSemanticLoading] = useState(false);

  const loadResources = useCallback(async (nextPage = 0, replace = nextPage === 0) => {
    if (!isAuthenticated) {
      setItems([]);
      setTotal(0);
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await resourcesApi.list({
        keyword: submittedKeyword || undefined,
        type: activeType === 'ALL' ? undefined : activeType,
        difficulty: difficulty || undefined,
        source: source || undefined,
        category: category || undefined,
        subcategory: subcategory || undefined,
        favoriteOnly,
        sort,
        page: nextPage,
        size: PAGE_SIZE,
      });
      setTotal(response.total);
      setItems((current) => {
        if (replace) {
          return response.items;
        }
        const seen = new Set(current.map((item) => item.id));
        return [...current, ...response.items.filter((item) => !seen.has(item.id))];
      });
    } catch (loadError) {
      setError(getErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [activeType, category, difficulty, favoriteOnly, isAuthenticated, sort, source, subcategory, submittedKeyword]);

  const loadSidebars = useCallback(async () => {
    if (!isAuthenticated) {
      setStats(null);
      setTags([]);
      setRecommendations([]);
      return;
    }
    const [statsResult, tagsResult, recommendationsResult] = await Promise.allSettled([
      resourcesApi.stats(),
      resourcesApi.tags(16),
      resourcesApi.recommendations(5),
    ]);
    if (statsResult.status === 'fulfilled') {
      setStats(statsResult.value);
    } else {
      console.error('Failed to load resource stats:', statsResult.reason);
    }
    if (tagsResult.status === 'fulfilled') {
      setTags(tagsResult.value);
    } else {
      console.error('Failed to load resource tags:', tagsResult.reason);
    }
    if (recommendationsResult.status === 'fulfilled') {
      setRecommendations(recommendationsResult.value);
    } else {
      console.error('Failed to load resource recommendations:', recommendationsResult.reason);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    setPage(0);
    setItems([]);
    setTotal(0);
    void loadResources(0, true);
  }, [activeType, category, difficulty, favoriteOnly, loadResources, sort, source, subcategory, submittedKeyword]);

  useEffect(() => {
    void loadSidebars();
  }, [loadSidebars]);

  const hasMore = items.length < total;
  const activeTypeMeta = TYPE_TABS.find((tab) => tab.value === activeType);
  const activeTypeLabel = activeTypeMeta?.label ?? '全部资源';

  const handleSearchSubmit = () => {
    setSubmittedKeyword(keyword.trim());
  };

  const handleOpenDetail = async (resource: ResourceItem) => {
    const noteId = readMetadataString(resource.metadata, 'noteId');
    if (resource.displayType === 'NOTE' && noteId) {
      navigate(`/notes?noteId=${encodeURIComponent(noteId)}`);
      return;
    }
    setDetailLoading(true);
    setDetail({ resource, ragReady: false, chunkCount: 0, previewChunks: [] });
    try {
      setDetail(await resourcesApi.detail(resource.id));
    } catch (detailError) {
      setError(getErrorMessage(detailError));
    } finally {
      setDetailLoading(false);
    }
  };

  const updateResourceState = (state: { resourceId: string; favorite?: boolean; progress?: number; completed?: boolean; lastStudyAt?: string }) => {
    setItems((current) => current.map((item) =>
      item.id === state.resourceId
        ? {
            ...item,
            favorite: state.favorite ?? item.favorite,
            progress: state.progress ?? item.progress,
            completed: state.completed ?? item.completed,
            lastStudyAt: state.lastStudyAt ?? item.lastStudyAt,
          }
        : item
    ));
    setRecommendations((current) => current.map((item) =>
      item.id === state.resourceId
        ? { ...item, favorite: state.favorite ?? item.favorite, progress: state.progress ?? item.progress, completed: state.completed ?? item.completed }
        : item
    ));
    setDetail((current) => current && current.resource.id === state.resourceId
      ? {
          ...current,
          resource: {
            ...current.resource,
            favorite: state.favorite ?? current.resource.favorite,
            progress: state.progress ?? current.resource.progress,
            completed: state.completed ?? current.resource.completed,
            lastStudyAt: state.lastStudyAt ?? current.resource.lastStudyAt,
          },
        }
      : current);
  };

  const handleToggleFavorite = async (resource: ResourceItem) => {
    if (!isAuthenticated) {
      openAuthModal('login', '登录后收藏学习资源');
      return;
    }
    setSavingId(resource.id);
    try {
      const state = resource.favorite
        ? await resourcesApi.unfavorite(resource.id)
        : await resourcesApi.favorite(resource.id);
      updateResourceState({ resourceId: state.resourceId, favorite: state.favorite });
      void loadSidebars();
    } catch (saveError) {
      setError(getErrorMessage(saveError));
    } finally {
      setSavingId('');
    }
  };

  const handleStartLearning = async (resource: ResourceItem) => {
    if (!isAuthenticated) {
      openAuthModal('login', '登录后记录学习进度');
      return;
    }
    setSavingId(resource.id);
    try {
      const currentProgress = Math.max(resource.progress ?? 0, 10);
      const state = await resourcesApi.progress(resource.id, { progress: currentProgress, completed: currentProgress >= 100 });
      updateResourceState({
        resourceId: state.resourceId,
        progress: state.progress,
        completed: state.completed,
        lastStudyAt: state.lastStudyAt,
      });
      void loadSidebars();
      if (resource.sourceUrl) {
        if (resource.sourceUrl.startsWith('/')) {
          navigate(resource.sourceUrl);
        } else {
          window.open(resource.sourceUrl, '_blank', 'noopener,noreferrer');
        }
      } else {
        await handleOpenDetail(resource);
      }
    } catch (saveError) {
      setError(getErrorMessage(saveError));
    } finally {
      setSavingId('');
    }
  };

  const handleSemanticSearch = async () => {
    const query = semanticQuery.trim();
    if (!query) {
      return;
    }
    if (!isAuthenticated) {
      openAuthModal('login', '登录后使用语义搜索');
      return;
    }
    setSemanticLoading(true);
    setSemantic(null);
    try {
      setSemantic(await resourcesApi.semantic(query, 8));
    } catch (semanticError) {
      setSemantic({
        query,
        available: false,
        message: getErrorMessage(semanticError),
        results: [],
      });
    } finally {
      setSemanticLoading(false);
    }
  };

  if (!isAuthenticated) {
    return (
      <ResourceShell>
        <AccessState onLogin={() => openAuthModal('login', '登录后进入资源库')} />
      </ResourceShell>
    );
  }

  return (
    <ResourceShell>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <main className="min-w-0 space-y-5">
          <section className="overflow-hidden rounded-[28px] bg-white/76 p-5 shadow-[0_18px_56px_rgba(59,97,155,0.09)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <h1 className="text-2xl font-bold tracking-tight text-slate-950 dark:text-white">资源库</h1>
                <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">搜索、收藏和继续学习，都集中在这里。</p>
              </div>
              <Link
                to="/resources/generation"
                className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary-50 px-4 text-sm font-semibold text-primary-700 transition hover:bg-primary-100 dark:bg-primary-500/10 dark:text-primary-200 dark:hover:bg-primary-500/20"
              >
                <Layers3 className="h-4 w-4" />
                资源生成
              </Link>
            </div>
            <div className="mt-5 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
              <label className="flex min-h-11 items-center rounded-xl bg-white/88 px-3 shadow-sm shadow-slate-200/24 transition focus-within:bg-white focus-within:shadow-md focus-within:shadow-primary-100/32 dark:bg-slate-950/72 dark:shadow-none dark:focus-within:bg-slate-950">
                <Search className="mr-2 h-4 w-4 text-slate-400" />
                <input
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      handleSearchSubmit();
                    }
                  }}
                  placeholder="搜索课程、文档、视频、案例或自己的笔记..."
                  className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-200"
                />
              </label>
              <button
                type="button"
                onClick={handleSearchSubmit}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-primary-600 px-5 text-sm font-semibold text-white transition hover:bg-primary-700"
              >
                <Search className="h-4 w-4" />
                搜索
              </button>
            </div>
            <div className="mt-4 flex gap-2 overflow-x-auto pb-1">
              {TYPE_TABS.map((tab) => {
                const Icon = tab.icon;
                const active = activeType === tab.value;
                return (
                  <button
                    key={tab.value}
                    type="button"
                    onClick={() => setActiveType(tab.value)}
                    className={`inline-flex h-10 shrink-0 items-center gap-2 rounded-xl px-4 text-sm font-semibold transition ${
                      active
                        ? 'bg-primary-600 text-white shadow-sm shadow-primary-500/20'
                        : 'bg-white/72 text-slate-600 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-950/70 dark:text-slate-300 dark:hover:bg-primary-500/10'
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                    {tab.label}
                  </button>
                );
              })}
            </div>

            <div className="mt-5 grid gap-4 pt-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex flex-wrap items-center gap-2">
                  <Filter className="h-4 w-4 text-slate-400" />
                  <Select value={category} onChange={setCategory} options={CS_CATEGORIES} />
                  <input
                    value={subcategory}
                    onChange={(event) => setSubcategory(event.target.value)}
                    placeholder="细分方向"
                    className="h-10 w-32 rounded-xl bg-white/84 px-3 text-sm text-slate-700 outline-none shadow-sm shadow-slate-200/20 transition focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-950/68 dark:text-slate-200 dark:shadow-none dark:focus:bg-slate-950"
                  />
                  <Select value={difficulty} onChange={setDifficulty} options={DIFFICULTIES} />
                  <input
                    value={source}
                    onChange={(event) => setSource(event.target.value)}
                    placeholder="平台"
                    className="h-10 w-32 rounded-xl bg-white/84 px-3 text-sm text-slate-700 outline-none shadow-sm shadow-slate-200/20 transition focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-950/68 dark:text-slate-200 dark:shadow-none dark:focus:bg-slate-950"
                  />
                  <label className="inline-flex h-10 items-center gap-2 rounded-xl bg-white/72 px-3 text-sm font-medium text-slate-600 dark:bg-slate-950/70 dark:text-slate-300">
                    <input
                      type="checkbox"
                      checked={favoriteOnly}
                      onChange={(event) => setFavoriteOnly(event.target.checked)}
                      className="h-4 w-4 accent-primary-600"
                    />
                    仅看收藏
                  </label>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-slate-500 dark:text-slate-400">{categoryLabel(category) || activeTypeLabel} · {total} 条</span>
                  <Select value={sort} onChange={setSort} options={SORT_OPTIONS} />
                </div>
              </div>

              <div className="rounded-[20px] bg-slate-50/70 p-3 dark:bg-slate-950/32">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
                  <div className="flex min-h-11 flex-1 items-center rounded-xl bg-white/88 px-3 shadow-sm shadow-slate-200/22 transition focus-within:bg-white focus-within:shadow-md focus-within:shadow-primary-100/30 dark:bg-slate-950/72 dark:shadow-none dark:focus-within:bg-slate-950">
                    <Sparkles className="mr-2 h-4 w-4 text-primary-500" />
                    <input
                      value={semanticQuery}
                      onChange={(event) => setSemanticQuery(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          void handleSemanticSearch();
                        }
                      }}
                      placeholder="语义搜索：例如“适合入门 PyTorch 的项目实战资料”"
                      className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-200"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleSemanticSearch()}
                    disabled={semanticLoading}
                    className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-70 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
                  >
                    {semanticLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                    语义搜索
                  </button>
                </div>
                {semantic ? (
                  <SemanticResults response={semantic} onOpenDetail={handleOpenDetail} />
                ) : null}
              </div>
            </div>
          </section>

          {error ? (
            <div className="flex items-start gap-2 rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">
              <TriangleAlert className="mt-0.5 h-4 w-4" />
              <span>{error}</span>
            </div>
          ) : null}

          {loading && !items.length ? (
            <LoadingState />
          ) : items.length ? (
            <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {items.map((resource) => (
                <ResourceCard
                  key={resource.id}
                  resource={resource}
                  saving={savingId === resource.id}
                  onOpenDetail={handleOpenDetail}
                  onToggleFavorite={handleToggleFavorite}
                  onStartLearning={handleStartLearning}
                />
              ))}
            </div>
          ) : (
            <EmptyState onReload={() => void loadResources(0, true)} />
          )}

          {hasMore ? (
            <div className="flex justify-center">
              <button
                type="button"
                onClick={() => {
                  const nextPage = page + 1;
                  setPage(nextPage);
                  void loadResources(nextPage, false);
                }}
                disabled={loading}
                className="inline-flex h-11 items-center gap-2 rounded-xl bg-white/88 px-5 text-sm font-semibold text-primary-700 shadow-sm shadow-blue-100/30 transition hover:bg-primary-50 disabled:opacity-70 dark:bg-slate-900/80 dark:text-primary-300"
              >
                {loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                加载更多
              </button>
            </div>
          ) : null}
        </main>

        <aside className="space-y-4">
          <StatsPanel stats={stats} />
          <CoveragePanel stats={stats} activeCategory={category} onPick={setCategory} />
          <RecommendationPanel items={recommendations} onOpenDetail={handleOpenDetail} />
          <TagsPanel tags={tags} onPick={(tag) => {
            setKeyword(tag);
            setSubmittedKeyword(tag);
          }} />
        </aside>
      </div>

      {detail ? (
        <DetailDrawer
          detail={detail}
          loading={detailLoading}
          saving={savingId === detail.resource.id}
          onClose={() => setDetail(null)}
          onToggleFavorite={handleToggleFavorite}
          onStartLearning={handleStartLearning}
        />
      ) : null}
    </ResourceShell>
  );
}

function ResourceShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="resource-page mx-auto max-w-[1440px] px-4 py-6 sm:px-6 sm:py-8">
      {children}
    </div>
  );
}

function AccessState({ onLogin }: { onLogin: () => void }) {
  return (
    <div className="flex min-h-[520px] items-center justify-center rounded-[28px] bg-white/76 p-6 text-center shadow-[0_18px_56px_rgba(59,97,155,0.09)] backdrop-blur-xl dark:bg-slate-900/68">
      <div className="max-w-md">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
          <BookOpen className="h-6 w-6" />
        </div>
        <h1 className="mt-4 text-xl font-bold text-slate-950 dark:text-white">登录后进入资源库</h1>
        <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">资源收藏、学习进度和个性化推荐需要关联到你的学习账号。</p>
        <button
          type="button"
          onClick={onLogin}
          className="mt-5 inline-flex h-11 items-center justify-center rounded-xl bg-primary-600 px-5 text-sm font-semibold text-white transition hover:bg-primary-700"
        >
          去登录
        </button>
      </div>
    </div>
  );
}

function Select({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-10 rounded-xl bg-white/84 px-3 text-sm text-slate-700 outline-none shadow-sm shadow-slate-200/20 transition focus:bg-white focus:shadow-md focus:shadow-primary-100/30 dark:bg-slate-950/68 dark:text-slate-200 dark:shadow-none dark:focus:bg-slate-950"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>{option.label}</option>
      ))}
    </select>
  );
}

function ResourceCard({
  resource,
  saving,
  onOpenDetail,
  onToggleFavorite,
  onStartLearning,
}: {
  resource: ResourceItem;
  saving: boolean;
  onOpenDetail: (resource: ResourceItem) => void;
  onToggleFavorite: (resource: ResourceItem) => void;
  onStartLearning: (resource: ResourceItem) => void;
}) {
  const style = typeStyle(resource.displayType);
  const Icon = style.icon;
  const progress = Math.max(0, Math.min(100, resource.progress ?? 0));
  return (
    <article className="group flex min-h-[286px] flex-col rounded-2xl bg-white/82 p-4 shadow-sm shadow-blue-100/28 backdrop-blur transition hover:-translate-y-0.5 hover:bg-white/92 hover:shadow-lg hover:shadow-blue-100/42 dark:bg-slate-900/78 dark:shadow-slate-950/18">
      <div className="relative overflow-hidden rounded-xl bg-slate-100 dark:bg-slate-800">
        {resource.coverUrl ? (
          <img src={resource.coverUrl} alt="" className="h-32 w-full object-cover" />
        ) : (
          <div className="flex h-32 items-center justify-center bg-[linear-gradient(135deg,#eaf2ff,#f6f9ff)] dark:bg-[linear-gradient(135deg,#1e293b,#0f172a)]">
            <Icon className="h-10 w-10 text-primary-500/80" />
          </div>
        )}
        <span className={`absolute left-3 top-3 inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-semibold ${style.className}`}>
          <Icon className="h-3.5 w-3.5" />
          {style.label}
        </span>
      </div>
      <div className="mt-3 flex flex-1 flex-col">
        <div className="flex items-start justify-between gap-3">
          <button
            type="button"
            onClick={() => onOpenDetail(resource)}
            className="line-clamp-2 text-left text-base font-bold leading-6 text-slate-950 transition hover:text-primary-700 dark:text-white dark:hover:text-primary-300"
          >
            {resource.title}
          </button>
          <button
            type="button"
            onClick={() => onToggleFavorite(resource)}
            disabled={saving}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-slate-400 transition hover:bg-primary-50 hover:text-primary-600 disabled:opacity-60 dark:hover:bg-primary-500/10"
            title={resource.favorite ? '取消收藏' : '收藏'}
          >
            {resource.favorite ? <BookmarkCheck className="h-4 w-4 text-amber-500" /> : <Bookmark className="h-4 w-4" />}
          </button>
        </div>
        <p className="mt-2 line-clamp-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{resource.summaryText || '暂无摘要'}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {resource.csCategory ? (
            <span className="rounded-lg bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
              {categoryLabel(resource.csCategory)}
            </span>
          ) : null}
          {resource.tags.slice(0, 3).map((tag) => (
            <span key={tag} className="rounded-lg bg-slate-100 px-2 py-1 text-xs font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">{tag}</span>
          ))}
        </div>
        <div className="mt-auto pt-4">
          <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
            <span>{difficultyLabel(resource.difficultyLevel)}</span>
            <span>{resource.durationMinutes ? `${resource.durationMinutes} 分钟` : resourcePlatform(resource)}</span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div className="h-full rounded-full bg-primary-600 transition-all" style={{ width: `${progress}%` }} />
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => onStartLearning(resource)}
              disabled={saving}
              className="inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-xl bg-primary-600 px-3 text-sm font-semibold text-white transition hover:bg-primary-700 disabled:opacity-70"
            >
              {saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
              开始学习
            </button>
            <button
              type="button"
              onClick={() => onOpenDetail(resource)}
              className="inline-flex h-10 items-center justify-center rounded-xl px-3 text-sm font-semibold text-slate-600 transition hover:bg-primary-50 hover:text-primary-700 dark:text-slate-300 dark:hover:bg-primary-500/10"
            >
              详情
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}

function SemanticResults({ response, onOpenDetail }: { response: ResourceSemanticSearchResponse; onOpenDetail: (resource: ResourceItem) => void }) {
  if (!response.available) {
    return (
      <div className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
        {response.message}
      </div>
    );
  }
  if (!response.results.length) {
    return <div className="mt-3 text-sm text-slate-500 dark:text-slate-400">没有匹配到资源。</div>;
  }
  return (
    <div className="mt-3 space-y-2">
      {response.results.slice(0, 4).map((result) => (
        <button
          key={result.resourceId}
          type="button"
          onClick={() => result.resource && onOpenDetail(result.resource)}
          className="w-full rounded-xl bg-white/78 px-3 py-2 text-left transition hover:bg-primary-50 dark:bg-slate-950/72 dark:hover:bg-primary-500/10"
        >
          <div className="flex items-center justify-between gap-3">
            <span className="line-clamp-1 text-sm font-semibold text-slate-800 dark:text-slate-100">{result.resource?.title || '推荐资源'}</span>
            <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-300">适合学习</span>
          </div>
          <p className="mt-1 line-clamp-1 text-xs text-slate-500 dark:text-slate-400">{result.hits[0]?.content || result.resource?.summaryText || '找到与你的问题相关的学习内容'}</p>
        </button>
      ))}
    </div>
  );
}

function StatsPanel({ stats }: { stats: ResourceStatsResponse | null }) {
  return (
    <section className="rounded-[22px] bg-white/68 p-4 shadow-sm shadow-blue-100/24 backdrop-blur dark:bg-slate-900/60">
      <div className="flex items-center justify-between">
        <h2 className="font-bold text-slate-950 dark:text-white">学习数据</h2>
        <BarChart3 className="h-4 w-4 text-primary-500" />
      </div>
      <div className="mt-4 grid grid-cols-3 gap-3">
        <Metric label="资源" value={stats?.totalResources ?? 0} />
        <Metric label="收藏" value={stats?.favoriteResources ?? 0} />
        <Metric label="完成率" value={`${Math.round(stats?.averageProgress ?? 0)}%`} />
      </div>
      <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-full rounded-full bg-primary-600" style={{ width: `${Math.min(100, Math.round(stats?.averageProgress ?? 0))}%` }} />
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-lg font-bold text-primary-600 dark:text-primary-300">{value}</div>
      <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{label}</div>
    </div>
  );
}

function CoveragePanel({
  stats,
  activeCategory,
  onPick,
}: {
  stats: ResourceStatsResponse | null;
  activeCategory: string;
  onPick: (category: string) => void;
}) {
  const entries = Object.entries(stats?.categoryCounts ?? {})
    .sort((left, right) => right[1] - left[1])
    .slice(0, 8);
  return (
    <section className="rounded-[22px] bg-white/68 p-4 shadow-sm shadow-blue-100/24 backdrop-blur dark:bg-slate-900/60">
      <div className="flex items-center justify-between">
        <h2 className="font-bold text-slate-950 dark:text-white">方向覆盖</h2>
        <BarChart3 className="h-4 w-4 text-primary-500" />
      </div>
      <div className="mt-3 space-y-2">
        {entries.length ? entries.map(([key, count]) => {
          const active = activeCategory === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onPick(active ? '' : key)}
              className={`flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-sm transition ${
                active
                  ? 'bg-primary-600 text-white'
                  : 'bg-white/72 text-slate-700 hover:bg-primary-50 dark:bg-slate-950/70 dark:text-slate-200 dark:hover:bg-primary-500/10'
              }`}
            >
              <span className="line-clamp-1 font-semibold">{categoryLabel(key)}</span>
              <span className={active ? 'text-white' : 'text-slate-500 dark:text-slate-400'}>{count}</span>
            </button>
          );
        }) : <p className="text-sm text-slate-500 dark:text-slate-400">暂无覆盖统计。</p>}
      </div>
    </section>
  );
}

function RecommendationPanel({ items, onOpenDetail }: { items: ResourceItem[]; onOpenDetail: (resource: ResourceItem) => void }) {
  return (
    <section className="rounded-[22px] bg-white/68 p-4 shadow-sm shadow-blue-100/24 backdrop-blur dark:bg-slate-900/60">
      <div className="flex items-center justify-between">
        <h2 className="font-bold text-slate-950 dark:text-white">为你推荐</h2>
        <Sparkles className="h-4 w-4 text-primary-500" />
      </div>
      <div className="mt-3 space-y-3">
        {items.length ? items.map((item) => (
          <button key={item.id} type="button" onClick={() => onOpenDetail(item)} className="flex w-full gap-3 rounded-xl p-2 text-left transition hover:bg-primary-50 dark:hover:bg-primary-500/10">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
              <Sparkles className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="line-clamp-1 text-sm font-semibold text-slate-800 dark:text-slate-100">{item.title}</div>
              <div className="mt-1 text-xs text-emerald-600 dark:text-emerald-300">适合继续学习</div>
            </div>
          </button>
        )) : <p className="text-sm text-slate-500 dark:text-slate-400">暂无推荐资源。</p>}
      </div>
    </section>
  );
}

function TagsPanel({ tags, onPick }: { tags: ResourceTag[]; onPick: (tag: string) => void }) {
  return (
    <section className="rounded-[22px] bg-white/68 p-4 shadow-sm shadow-blue-100/24 backdrop-blur dark:bg-slate-900/60">
      <div className="flex items-center justify-between">
        <h2 className="font-bold text-slate-950 dark:text-white">热门标签</h2>
        <Tags className="h-4 w-4 text-primary-500" />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {tags.length ? tags.map((tag) => (
          <button key={tag.tag} type="button" onClick={() => onPick(tag.tag)} className="rounded-lg bg-primary-50 px-2.5 py-1.5 text-xs font-semibold text-primary-700 transition hover:bg-primary-100 dark:bg-primary-500/10 dark:text-primary-300">
            {tag.tag}
          </button>
        )) : <p className="text-sm text-slate-500 dark:text-slate-400">暂无标签。</p>}
      </div>
    </section>
  );
}

function DetailDrawer({
  detail,
  loading,
  saving,
  onClose,
  onToggleFavorite,
  onStartLearning,
}: {
  detail: ResourceDetailResponse;
  loading: boolean;
  saving: boolean;
  onClose: () => void;
  onToggleFavorite: (resource: ResourceItem) => void;
  onStartLearning: (resource: ResourceItem) => void;
}) {
  const resource = detail.resource;
  const style = typeStyle(resource.displayType);
  const Icon = style.icon;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/35 backdrop-blur-sm" onClick={onClose}>
      <aside className="h-full w-full max-w-xl overflow-y-auto bg-white p-5 shadow-2xl dark:bg-slate-950" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <span className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-semibold ${style.className}`}>
              <Icon className="h-3.5 w-3.5" />
              {style.label}
            </span>
            <h2 className="mt-3 text-xl font-bold leading-7 text-slate-950 dark:text-white">{resource.title}</h2>
            <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{resource.summaryText || '暂无摘要'}</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-xl px-3 py-1.5 text-sm text-slate-500 transition hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-900">关闭</button>
        </div>
        <div className="mt-5 flex flex-wrap gap-2">
          {resource.tags.map((tag) => <span key={tag} className="rounded-lg bg-slate-100 px-2.5 py-1 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">{tag}</span>)}
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3">
          <Info label="CS 方向" value={categoryLabel(resource.csCategory)} />
          <Info label="子方向" value={resource.csSubcategory} />
          <Info label="难度" value={difficultyLabel(resource.difficultyLevel)} />
          <Info label="平台" value={resourcePlatform(resource)} />
        </div>
        {loading || detail.previewChunks.length ? (
          <div className="mt-5 rounded-2xl bg-blue-50/50 p-4 dark:bg-slate-900/70">
            <h3 className="text-sm font-bold text-slate-900 dark:text-white">内容预览</h3>
            <div className="mt-3 space-y-3">
              {loading ? (
                <p className="text-sm leading-6 text-slate-500 dark:text-slate-400">正在整理内容预览...</p>
              ) : detail.previewChunks.map((chunk, index) => (
                <p key={`${index}-${chunk.slice(0, 16)}`} className="line-clamp-3 text-sm leading-6 text-slate-600 dark:text-slate-300">{chunk}</p>
              ))}
            </div>
          </div>
        ) : null}
        <div className="mt-6 flex gap-3">
          <button
            type="button"
            onClick={() => onStartLearning(resource)}
            disabled={saving}
            className="inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white transition hover:bg-primary-700 disabled:opacity-70"
          >
            {saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
            开始学习
          </button>
          <button
            type="button"
            onClick={() => onToggleFavorite(resource)}
            disabled={saving}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold text-slate-600 transition hover:bg-primary-50 hover:text-primary-700 disabled:opacity-70 dark:text-slate-300"
          >
            {resource.favorite ? <BookmarkCheck className="h-4 w-4 text-amber-500" /> : <Bookmark className="h-4 w-4" />}
            {resource.favorite ? '已收藏' : '收藏'}
          </button>
        </div>
      </aside>
    </div>
  );
}

function Info({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded-xl bg-slate-50/80 px-3 py-2 dark:bg-slate-900/80">
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-1 line-clamp-1 text-sm font-semibold text-slate-800 dark:text-slate-100">{value || '-'}</div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex min-h-[360px] items-center justify-center rounded-[24px] bg-white/64 text-sm text-slate-500 shadow-sm shadow-blue-100/24 dark:bg-slate-900/60 dark:text-slate-400">
      <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-primary-500" />
      正在加载资源库
    </div>
  );
}

function EmptyState({ onReload }: { onReload: () => void }) {
  return (
    <div className="flex min-h-[360px] items-center justify-center rounded-[24px] bg-white/64 p-6 text-center shadow-sm shadow-blue-100/24 dark:bg-slate-900/60">
      <div>
        <CheckCircle2 className="mx-auto h-8 w-8 text-slate-300" />
        <h2 className="mt-3 text-base font-bold text-slate-900 dark:text-white">没有匹配的资源</h2>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">调整搜索词或筛选条件后再试。</p>
        <button type="button" onClick={onReload} className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl px-4 text-sm font-semibold text-primary-700 hover:bg-primary-50 dark:text-primary-300">
          <RefreshCw className="h-4 w-4" />
          重新加载
        </button>
      </div>
    </div>
  );
}

function typeStyle(type?: string) {
  return TYPE_STYLE[String(type || 'DOCUMENT').toUpperCase()] ?? TYPE_STYLE.DOCUMENT;
}

function categoryLabel(value?: string) {
  if (!value) {
    return '';
  }
  return CATEGORY_LABELS.get(value) ?? value;
}

function resourcePlatform(resource: ResourceItem): string {
  return resource.sourceName || '学习资源';
}

function difficultyLabel(value?: string) {
  switch (value) {
    case 'BASIC':
      return '基础';
    case 'INTERMEDIATE':
      return '进阶';
    case 'ADVANCED':
      return '高级';
    case 'MIXED':
      return '综合';
    default:
      return value || '综合';
  }
}

function readMetadataString(metadata: Record<string, unknown> | undefined, key: string) {
  const value = metadata?.[key];
  return typeof value === 'string' ? value.trim() : '';
}
