import { useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { LoaderCircle, Play, PlayCircle, TriangleAlert } from 'lucide-react';
import { API_BASE_URL, getAuthHeaders } from '../api/request';

export type VideoCardStyle = 'talking_head' | 'animation' | 'hybrid';

export interface VideoCardProps {
  title: string;
  videoUrl: string;
  thumbnailUrl?: string;
  duration?: number;
  style?: VideoCardStyle;
  knowledgePoint?: string;
  expiresHint?: string;
  fileName?: string;
  renderStatus?: 'rendering' | 'ready' | 'failed';
  renderMessage?: string;
  onPlay?: () => void;
  onComplete?: () => void;
}

const styleLabelMap: Record<VideoCardStyle, { label: string; color: string }> = {
  talking_head: { label: '数字人讲解', color: 'bg-violet-50 text-violet-700 dark:bg-violet-500/10 dark:text-violet-400' },
  animation: { label: '动画演示', color: 'bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-400' },
  hybrid: { label: '混合讲解', color: 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-400' },
};

export default function VideoCard(props: VideoCardProps) {
  const [started, setStarted] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [resolvedVideoUrl, setResolvedVideoUrl] = useState('');
  const [resolvedThumbnailUrl, setResolvedThumbnailUrl] = useState('');
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const durationLabel = useMemo(() => {
    if (typeof props.duration !== 'number' || Number.isNaN(props.duration) || props.duration <= 0) {
      return '';
    }
    const minutes = Math.floor(props.duration / 60);
    const seconds = Math.floor(props.duration % 60);
    if (minutes <= 0) {
      return `${seconds} 秒`;
    }
    return `${minutes} 分 ${seconds.toString().padStart(2, '0')} 秒`;
  }, [props.duration]);

  const styleInfo = props.style ? styleLabelMap[props.style] : null;
  const mediaId = resolvedVideoUrl || props.videoUrl;
  const isPendingRender = props.renderStatus === 'rendering' || (!props.videoUrl && props.renderStatus !== 'failed');
  const isFailedRender = props.renderStatus === 'failed';

  useEffect(() => {
    let cancelled = false;
    let videoObjectUrl = '';
    let thumbnailObjectUrl = '';

    async function loadProtectedMedia(): Promise<void> {
      if (!props.videoUrl) {
        if (!cancelled) {
          setResolvedVideoUrl('');
          setResolvedThumbnailUrl('');
          setLoading(false);
        }
        return;
      }
      setLoading(true);

      if (/^(blob:|data:)/i.test(props.videoUrl)) {
        if (!cancelled) {
          setResolvedVideoUrl(props.videoUrl);
          setResolvedThumbnailUrl(props.thumbnailUrl || '');
          setLoading(false);
        }
        return;
      }

      const loadedVideoUrl = await fetchMediaBlobUrl(props.videoUrl);
      if (!cancelled) {
        videoObjectUrl = loadedVideoUrl.objectUrl;
        setResolvedVideoUrl(loadedVideoUrl.url);
      }

      if (props.thumbnailUrl) {
        const loadedThumbnailUrl = await fetchMediaBlobUrl(props.thumbnailUrl);
        if (!cancelled) {
          thumbnailObjectUrl = loadedThumbnailUrl.objectUrl;
          setResolvedThumbnailUrl(loadedThumbnailUrl.url);
        }
      } else if (!cancelled) {
        setResolvedThumbnailUrl('');
      }
    }

    void loadProtectedMedia().catch(() => {
      if (!cancelled) {
        setResolvedVideoUrl(resolveMediaUrl(props.videoUrl));
        setResolvedThumbnailUrl(props.thumbnailUrl ? resolveMediaUrl(props.thumbnailUrl) : '');
        setLoading(false);
      }
    });

    return () => {
      cancelled = true;
      if (videoObjectUrl) {
        URL.revokeObjectURL(videoObjectUrl);
      }
      if (thumbnailObjectUrl) {
        URL.revokeObjectURL(thumbnailObjectUrl);
      }
    };
  }, [props.thumbnailUrl, props.videoUrl]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    video.load();
  }, [resolvedVideoUrl, props.videoUrl]);

  return (
    <div className="overflow-hidden rounded-2xl bg-white/90 shadow-sm ring-1 ring-slate-200/80 dark:bg-slate-900/86 dark:ring-slate-700/70">
      {/* Video Player Area */}
      <div className="relative aspect-video bg-slate-900">
        {/* Thumbnail / Background */}
        {resolvedThumbnailUrl ? (
          <img
            src={resolvedThumbnailUrl}
            alt={props.title}
            className="absolute inset-0 h-full w-full object-cover"
            onLoad={() => setLoading(false)}
          />
        ) : (
          <div className="absolute inset-0 bg-gradient-to-br from-slate-800 via-slate-900 to-primary-950" />
        )}

        {/* Loading Overlay */}
        {(loading && !started) || isPendingRender ? (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-900/60">
            <div className="flex flex-col items-center gap-3 px-4 text-center">
              <LoaderCircle className="h-8 w-8 animate-spin text-white/70" />
              {isPendingRender ? (
                <span className="text-sm font-medium text-white/80">{props.renderMessage || '浏览器本地渲染中，请稍候'}</span>
              ) : null}
            </div>
          </div>
        ) : null}

        {isFailedRender ? (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-900/70 px-4 text-center">
            <div className="space-y-2 text-white">
              <TriangleAlert className="mx-auto h-8 w-8 text-amber-300" />
              <p className="text-sm font-semibold">浏览器本地渲染失败</p>
              <p className="text-xs text-white/70">{props.renderMessage || '请重新生成视频或换用支持 MediaRecorder 的浏览器。'}</p>
            </div>
          </div>
        ) : null}

        {/* Gradient Overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-slate-900/80 via-slate-900/20 to-transparent transition-opacity duration-300 group-hover:opacity-90" />

        {/* Top Badge */}
        <div className="pointer-events-none absolute left-4 top-4 z-10 inline-flex items-center gap-1.5 rounded-full bg-black/50 px-3 py-1.5 text-xs font-medium text-white backdrop-blur-sm">
          <PlayCircle className="h-3.5 w-3.5" />
          教学视频
        </div>

        {/* Duration Badge */}
        {durationLabel ? (
          <div className="pointer-events-none absolute bottom-4 right-4 z-10 rounded-full bg-primary-500/80 px-3 py-1.5 text-xs font-medium text-white backdrop-blur-sm">
            {durationLabel}
          </div>
        ) : null}

        {/* Play Button Overlay (when not started) */}
        {!started && !isPendingRender && !isFailedRender ? (
          <button
            type="button"
            onClick={() => {
              const video = videoRef.current;
              if (video) {
                void video.play();
              }
            }}
            className="absolute inset-0 z-20 flex items-center justify-center transition-opacity hover:opacity-80"
          >
            <motion.div
              whileHover={{ scale: 1.1 }}
              whileTap={{ scale: 0.95 }}
              className="flex h-16 w-16 items-center justify-center rounded-full bg-white/90 shadow-2xl backdrop-blur-sm"
            >
              <Play className="ml-1 h-6 w-6 text-primary-600" />
            </motion.div>
          </button>
        ) : null}

        {props.videoUrl ? (
          <video
            ref={videoRef}
            data-video-id={mediaId}
            controls
            preload="metadata"
            poster={resolvedThumbnailUrl}
            src={resolvedVideoUrl || resolveMediaUrl(props.videoUrl)}
            className="relative z-10 h-full w-full"
            onLoadedData={() => setLoading(false)}
            onError={() => setLoading(false)}
            onPlay={() => {
              if (!started) {
                setStarted(true);
                props.onPlay?.();
              }
            }}
            onEnded={() => {
              if (!completed) {
                setCompleted(true);
                props.onComplete?.();
              }
            }}
          >
            当前浏览器不支持视频播放，请直接下载后查看。
          </video>
        ) : null}
      </div>

      {/* Info Section */}
      <div className="space-y-3 p-4 md:p-5">
        <div>
          <h3 className="text-base font-semibold text-slate-800 dark:text-white">{props.title}</h3>
          {props.knowledgePoint ? (
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">知识点：{props.knowledgePoint}</p>
          ) : null}
        </div>

        {/* Tags */}
        <div className="flex flex-wrap gap-2">
          {styleInfo ? (
            <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${styleInfo.color}`}>
              {styleInfo.label}
            </span>
          ) : null}
          {started ? (
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400">
              已开始观看
            </span>
          ) : null}
          {completed ? (
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400">
              已观看完成
            </span>
          ) : null}
          {isPendingRender ? (
            <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">
              本地渲染中
            </span>
          ) : null}
          {isFailedRender ? (
            <span className="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
              渲染失败
            </span>
          ) : null}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 pt-3 shadow-[inset_0_1px_0_rgba(241,245,249,0.86)] dark:shadow-[inset_0_1px_0_rgba(30,41,59,0.86)]">
          <span className="text-xs text-slate-400 dark:text-slate-500">{props.expiresHint || '视频资源已生成'}</span>
          {props.videoUrl ? (
            <a
              href={resolvedVideoUrl || resolveMediaUrl(props.videoUrl)}
              target="_blank"
              rel="noreferrer"
              download={props.fileName || `${props.title || 'teaching-video'}.webm`}
              className="text-xs font-medium text-primary-600 transition-colors hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300"
            >
              下载视频
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function resolveMediaUrl(url: string): string {
  if (!url) {
    return '';
  }
  if (/^(blob:|data:|https?:)/i.test(url)) {
    return url;
  }
  return `${API_BASE_URL}${url.startsWith('/') ? url : `/${url}`}`;
}

async function fetchMediaBlobUrl(url: string): Promise<{ url: string; objectUrl: string }> {
  const resolvedUrl = resolveMediaUrl(url);
  const response = await fetch(resolvedUrl, {
    headers: {
      ...getAuthHeaders(),
    },
  });
  if (!response.ok) {
    throw new Error(`failed to load media: ${response.status}`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  return {
    url: objectUrl,
    objectUrl,
  };
}
