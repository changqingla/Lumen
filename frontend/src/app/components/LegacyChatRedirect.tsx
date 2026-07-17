import { Navigate, useParams } from 'react-router-dom';

import { buildLegacyChatRedirect } from '@/features/chat/lib/chat-route';

export default function LegacyChatRedirect() {
  const { chatId } = useParams();

  return <Navigate to={buildLegacyChatRedirect(chatId)} replace />;
}
