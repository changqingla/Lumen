import { Suspense, lazy } from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import { ToastProvider } from "@/shared/hooks/ToastProvider";
import ProtectedRoute from "@/app/components/ProtectedRoute";
import AdminRoute from "@/app/components/RouteGuards/AdminRoute";
import GuestLoginPrompt from "@/shared/components/GuestLoginPrompt/GuestLoginPrompt";

const Home = lazy(() => import("@/features/chat/pages/HomePage"));
const ChatDetail = lazy(() => import("@/features/chat/pages/ChatDetailPage"));
const Knowledge = lazy(() => import("@/features/knowledge/pages/KnowledgePage"));
const KnowledgeDetail = lazy(() => import("@/features/knowledge/pages/KnowledgeDetailPage"));
const Auth = lazy(() => import("@/features/auth/pages/AuthPage"));
const Favorites = lazy(() => import("@/features/favorites/pages/FavoritesPage"));
const Notes = lazy(() => import("@/features/notes/pages/NotesPage"));
const AdminPanel = lazy(() => import("@/features/admin/pages/AdminPanelPage"));

export default function App() {
  return (
    <ToastProvider>
      <Router>
        <GuestLoginPrompt />
        <Suspense fallback={<div className="min-h-screen flex items-center justify-center text-sm text-gray-500">Loading...</div>}>
          <Routes>
            {/* 公开路由 */}
            <Route path="/auth" element={<Auth />} />

            {/* 受保护的路由 - 需要登录 */}
            <Route path="/" element={<ProtectedRoute><Home /></ProtectedRoute>} />
            <Route path="/chat/:chatId" element={<ProtectedRoute><ChatDetail /></ProtectedRoute>} />
            <Route path="/knowledge" element={<ProtectedRoute><Knowledge /></ProtectedRoute>} />
            <Route path="/knowledge/:kbId" element={<ProtectedRoute><KnowledgeDetail /></ProtectedRoute>} />
            <Route path="/favorites" element={<ProtectedRoute><Favorites /></ProtectedRoute>} />
            <Route path="/notes" element={<ProtectedRoute><Notes /></ProtectedRoute>} />

            {/* 管理员路由 - 需要管理员权限 */}
            <Route path="/admin" element={<ProtectedRoute><AdminRoute><AdminPanel /></AdminRoute></ProtectedRoute>} />
          </Routes>
        </Suspense>
      </Router>
    </ToastProvider>
  );
}
