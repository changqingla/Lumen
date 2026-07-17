export interface ProtectedRouteAccess {
  authToken: string | null | undefined;
  isGuestMode: boolean;
  allowGuest?: boolean;
}

export const canAccessProtectedRoute = ({
  authToken,
  isGuestMode,
  allowGuest = false,
}: ProtectedRouteAccess): boolean => (
  Boolean(authToken?.trim()) || (allowGuest && isGuestMode)
);
