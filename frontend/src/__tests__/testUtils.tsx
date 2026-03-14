import React from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../components/ui/Toast";
import { AuthProvider, getStorage } from "../hooks/useAuth";

export interface AuthTestOptions {
  role?: "analyst" | "senior_analyst" | "admin";
  authenticated?: boolean;
}

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function setupAuth(auth?: AuthTestOptions) {
  const storage = getStorage();

  // Clear any previous auth state
  storage.removeItem("rf_admin_token");
  storage.removeItem("rf_analyst_info");

  if (auth?.authenticated !== false && auth?.role) {
    storage.setItem("rf_admin_token", "test-token");
    storage.setItem(
      "rf_analyst_info",
      JSON.stringify({
        analyst_id: 1,
        username: "testuser",
        display_name: "Test User",
        role: auth.role,
      })
    );
  } else if (auth?.authenticated) {
    storage.setItem("rf_admin_token", "test-token");
    storage.setItem(
      "rf_analyst_info",
      JSON.stringify({
        analyst_id: 1,
        username: "testuser",
        display_name: "Test User",
        role: "analyst",
      })
    );
  }
}

function createWrapper(initialEntries: string[] = ["/"]) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    const queryClient = createTestQueryClient();
    return (
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <AuthProvider>
            <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
          </AuthProvider>
        </ToastProvider>
      </QueryClientProvider>
    );
  };
}

export function renderWithProviders(
  ui: React.ReactElement,
  options?: RenderOptions & { initialEntries?: string[]; auth?: AuthTestOptions }
) {
  const { initialEntries, auth, ...renderOptions } = options ?? {};
  setupAuth(auth);
  return render(ui, {
    wrapper: createWrapper(initialEntries),
    ...renderOptions,
  });
}
