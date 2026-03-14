import { describe, it, expect, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { RequireAuth } from "../components/RequireAuth";
import { renderWithProviders } from "./testUtils";
import { getStorage } from "../hooks/useAuth";

afterEach(() => {
  getStorage().clear();
});

describe("RequireAuth", () => {
  it('renders children when role="authenticated" and user is authenticated', () => {
    renderWithProviders(
      <RequireAuth role="authenticated">
        <div>Protected Content</div>
      </RequireAuth>,
      { auth: { role: "analyst" } }
    );
    expect(screen.getByText("Protected Content")).toBeInTheDocument();
  });

  it('renders children when role="admin" and user is admin', () => {
    renderWithProviders(
      <RequireAuth role="admin">
        <div>Admin Content</div>
      </RequireAuth>,
      { auth: { role: "admin" } }
    );
    expect(screen.getByText("Admin Content")).toBeInTheDocument();
  });

  it('renders children when role="senior_or_admin" and user is senior_analyst', () => {
    renderWithProviders(
      <RequireAuth role="senior_or_admin">
        <div>Senior Content</div>
      </RequireAuth>,
      { auth: { role: "senior_analyst" } }
    );
    expect(screen.getByText("Senior Content")).toBeInTheDocument();
  });

  it('shows "You need to log in" when unauthenticated with fallback="access-denied"', () => {
    renderWithProviders(
      <RequireAuth role="authenticated" fallback="access-denied">
        <div>Protected Content</div>
      </RequireAuth>
    );
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
    expect(screen.getByText(/You need to log in/)).toBeInTheDocument();
  });

  it('shows "You do not have permission" when authenticated with wrong role', () => {
    renderWithProviders(
      <RequireAuth role="admin" fallback="access-denied">
        <div>Admin Content</div>
      </RequireAuth>,
      { auth: { role: "analyst" } }
    );
    expect(screen.queryByText("Admin Content")).not.toBeInTheDocument();
    expect(screen.getByText(/You do not have permission/)).toBeInTheDocument();
  });

  it('renders null when unauthenticated with fallback="hidden"', () => {
    const { container } = renderWithProviders(
      <RequireAuth role="authenticated" fallback="hidden">
        <div>Protected Content</div>
      </RequireAuth>
    );
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
    expect(screen.queryByText("Access Denied")).not.toBeInTheDocument();
    // The RequireAuth component should render nothing
    expect(container.querySelector("[style]")?.children.length ?? 0).toBe(0);
  });
});
