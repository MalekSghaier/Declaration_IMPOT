import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "./AuthContext";
import "./DashboardLayout.css";

const LINKS = [
  { to: "/dashboard", label: "Accueil", end: true },
  { to: "/dashboard/info", label: "Info", end: false },
  { to: "/dashboard/periodes", label: "Périodes fiscales", end: false },
  { to: "/dashboard/nouveau-document", label: "Nouveau document", end: false },
  { to: "/dashboard/mes-travaux", label: "Mes travaux", end: false },
];

export default function DashboardLayout() {
  const { user, logout } = useAuth();

  return (
    <div className="db-shell">
      <aside className="db-sidebar">
        <span className="db-brand">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <rect x="1" y="1" width="12" height="12" stroke="currentColor" strokeWidth="1.2" />
            <rect x="5" y="5" width="4" height="4" fill="#14b8a6" />
          </svg>
          Déclaration TVA
        </span>
        <nav className="db-nav">
          {LINKS.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) => "db-link" + (isActive ? " active" : "")}
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="db-user">
          <span>{user?.email}</span>
          <button onClick={logout}>Déconnexion</button>
        </div>
      </aside>
      <main className="db-main">
        <Outlet />
      </main>
    </div>
  );
}