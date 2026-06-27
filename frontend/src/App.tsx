import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Toaster } from 'sonner';
import ErrorBoundary from './components/ErrorBoundary';
import Layout from './components/Layout';

const LearningStudioDemoPage = lazy(() => import('./pages/LearningStudioDemoPage'));
const DailyStudyWorkbenchPage = lazy(() => import('./pages/DailyStudyWorkbenchPage'));
const PersonalizedLearningPathPage = lazy(() => import('./pages/PersonalizedLearningPathPage'));
const ResourceLibraryPage = lazy(() => import('./pages/ResourceLibraryPage'));
const MultiAgentResourceGenerationPage = lazy(() => import('./pages/MultiAgentResourceGenerationPage'));
const MistakeBookPage = lazy(() => import('./pages/MistakeBookPage'));
const NotebookPage = lazy(() => import('./pages/NotebookPage'));
const ProfilePage = lazy(() => import('./pages/ProfilePage'));
const KnowledgeGraphPage = lazy(() => import('./pages/KnowledgeGraphPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));

function PageLoader() {
  return (
    <div className="flex h-64 items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
    </div>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Toaster position="top-center" richColors closeButton />
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<DailyStudyWorkbenchPage />} />
              <Route path="chat" element={<LearningStudioDemoPage mode="qna" />} />
              <Route path="dashboard" element={<Navigate to="/" replace />} />
              <Route path="engine" element={<PersonalizedLearningPathPage />} />
              <Route path="resources" element={<ResourceLibraryPage />} />
              <Route path="resources/generation" element={<MultiAgentResourceGenerationPage />} />
              <Route path="mistakes" element={<MistakeBookPage />} />
              <Route path="notes" element={<NotebookPage />} />
              <Route path="profile" element={<ProfilePage />} />
              <Route path="knowledge-graph" element={<KnowledgeGraphPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  );
}

export default App;
