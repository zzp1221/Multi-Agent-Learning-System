import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import ErrorBoundary from './components/ErrorBoundary';
import Layout from './components/Layout';

const LearningStudioDemoPage = lazy(() => import('./pages/LearningStudioDemoPage'));
const PersonalizedLearningPathPage = lazy(() => import('./pages/PersonalizedLearningPathPage'));
const MultiAgentResourceGenerationPage = lazy(() => import('./pages/MultiAgentResourceGenerationPage'));
const MistakeBookPage = lazy(() => import('./pages/MistakeBookPage'));
const ProfilePage = lazy(() => import('./pages/ProfilePage'));

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
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<LearningStudioDemoPage mode="qna" />} />
              <Route path="engine" element={<PersonalizedLearningPathPage />} />
              <Route path="resources" element={<MultiAgentResourceGenerationPage />} />
              <Route path="mistakes" element={<MistakeBookPage />} />
              <Route path="profile" element={<ProfilePage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  );
}

export default App;
