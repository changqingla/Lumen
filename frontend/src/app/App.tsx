import { Suspense, lazy } from "react";
import { BrowserRouter as Router, Routes, Route, useLocation } from "react-router-dom";
import { ToastProvider } from "@/shared/hooks/ToastProvider";
import ApplicationErrorBoundary from "@/app/components/ApplicationErrorBoundary";
import ProtectedRoute from "@/app/components/ProtectedRoute";
import AdminRoute from "@/app/components/RouteGuards/AdminRoute";
import LegacyChatRedirect from "@/app/components/LegacyChatRedirect";
import NotFoundPage from "@/app/components/NotFoundPage";
import GuestLoginPrompt from "@/shared/components/GuestLoginPrompt/GuestLoginPrompt";

const Home = lazy(() => import("@/features/chat/pages/HomePage"));
const Knowledge = lazy(() => import("@/features/knowledge/pages/KnowledgePage"));
const KnowledgeDetail = lazy(() => import("@/features/knowledge/pages/KnowledgeDetailPage"));
const Auth = lazy(() => import("@/features/auth/pages/AuthPage"));
const Favorites = lazy(() => import("@/features/favorites/pages/FavoritesPage"));
const Notes = lazy(() => import("@/features/notes/pages/NotesPage"));
const CreativeWorkshop = lazy(() => import("@/features/creative-workshop/pages/CreativeWorkshopPage"));
const AdminPanel = lazy(() => import("@/features/admin/pages/AdminPanelPage"));

function RoutedApplication() {
  const location = useLocation();

  return (
    <ApplicationErrorBoundary resetKey={location.key}>
      <GuestLoginPrompt />
      <Suspense fallback={<div className="min-h-screen flex items-center justify-center text-sm text-gray-500">Loading...</div>}>
        <Routes>
          {/* 公开路由 */}
          <Route path="/auth" element={<Auth />} />

          {/* 受保护的路由 - 首页显式支持游客，其余路由需要登录 */}
          <Route path="/" element={<ProtectedRoute allowGuest><Home /></ProtectedRoute>} />
          <Route path="/chat/:chatId" element={<ProtectedRoute><LegacyChatRedirect /></ProtectedRoute>} />
          <Route path="/knowledge" element={<ProtectedRoute><Knowledge /></ProtectedRoute>} />
          <Route path="/knowledge/:kbId" element={<ProtectedRoute><KnowledgeDetail /></ProtectedRoute>} />
          <Route path="/favorites" element={<ProtectedRoute><Favorites /></ProtectedRoute>} />
          <Route path="/notes" element={<ProtectedRoute><Notes /></ProtectedRoute>} />
          <Route path="/creative-workshop/*" element={<ProtectedRoute><CreativeWorkshop /></ProtectedRoute>} />

          {/* 管理员路由 - 同时验证登录态与管理员权限 */}
          <Route path="/admin" element={<ProtectedRoute><AdminRoute><AdminPanel /></AdminRoute></ProtectedRoute>} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </ApplicationErrorBoundary>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <Router>
        <RoutedApplication />
      </Router>
    </ToastProvider>
  );
}
