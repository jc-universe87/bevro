import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../lib/api";
import CommandLauncher from "./CommandLauncher";
import Icon, { type IconName } from "./Icon";
import Logo from "./Logo";

/** `short` is what fits under an icon on a phone; screen readers get the full label. */
export const NAV: { to: string; label: string; short?: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Home", icon: "home", end: true },
  { to: "/recent", label: "Recent", icon: "recent" },
  { to: "/scheduled", label: "Scheduled", icon: "schedule" },
  { to: "/apps", label: "Apps & agents", short: "Apps", icon: "apps" },
  { to: "/create", label: "Create", icon: "create" },
  { to: "/connect", label: "Connect", icon: "connect" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

// On a phone: the places people go every day, then More.
const PRIMARY_MOBILE = [NAV[0], NAV[3], NAV[1]];
const MORE_MOBILE = NAV.filter((n) => !PRIMARY_MOBILE.includes(n));

/** How often Bevro looks for anything new to tell you. Quiet and cheap. */
const UNREAD_POLL_MS = 30_000;

function useUnread(pathname: string): number {
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    let alive = true;
    const look = () =>
      api
        .listNotifications(true)
        .then((list) => alive && setUnread(list.unread))
        .catch(() => null);
    void look();
    const timer = window.setInterval(look, UNREAD_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
    // Coming back from the notifications page is a good moment to look again.
  }, [pathname]);
  return unread;
}

function NotificationsButton({ unread, className = "" }: { unread: number; className?: string }) {
  return (
    <NavLink
      to="/notifications"
      className={({ isActive }) => `${navClass(isActive)} relative ${className}`}
      aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
    >
      <span className="relative">
        <Icon name="bell" />
        {unread > 0 && <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-accent" aria-hidden="true" />}
      </span>
      Notifications
    </NavLink>
  );
}

function navClass(isActive: boolean) {
  return [
    "relative flex items-center gap-3 rounded-md px-3 py-2 text-sm min-h-[44px] md:min-h-[40px] transition-colors",
    isActive ? "bg-[var(--bv-raised)] text-ink font-medium" : "text-muted hover:text-ink hover:bg-[var(--bv-raised)]",
  ].join(" ");
}

export default function Shell() {
  const [launcherOpen, setLauncherOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const location = useLocation();
  const unread = useUnread(location.pathname);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setLauncherOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => setMoreOpen(false), [location.pathname]);
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMoreOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  const isActivePath = (to: string, end?: boolean) =>
    end ? location.pathname === to : location.pathname.startsWith(to);
  const moreActive = MORE_MOBILE.some((n) => isActivePath(n.to));

  return (
    <div className="min-h-screen md:flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>

      {/* Desktop / tablet sidebar */}
      <aside className="hidden md:flex md:w-56 lg:w-60 shrink-0 flex-col border-r bv-sep bg-bg sticky top-0 h-screen">
        <NavLink to="/" className="inline-block rounded-md m-2" aria-label="Bevro home">
          <Logo variant="lockup" height={50} />
        </NavLink>
        <nav aria-label="Main" className="flex flex-col gap-0.5 px-3 mt-2">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => navClass(isActive)}>
              {({ isActive }) => (
                <>
                  {isActive && <span aria-hidden="true" className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full bg-accent" />}
                  <Icon name={item.icon} />
                  {item.label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-3 pb-4">
          <NotificationsButton unread={unread} className="mb-1" />
          <button type="button" onClick={() => setLauncherOpen(true)} className="bv-btn-quiet w-full justify-between text-sm">
            <span className="flex items-center gap-3">
              <Icon name="search" />
              Search
            </span>
            <span className="bv-kbd">⌘K</span>
          </button>
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="md:hidden sticky top-0 z-20 flex items-center justify-between border-b bv-sep bg-bg px-2 h-14">
        <div className="flex items-center gap-2 min-w-0">
          <NavLink to="/" className="rounded-md p-1" aria-label="Bevro home">
            <Logo variant="mark" height={28} />
          </NavLink>
        </div>
        <div className="flex items-center gap-1">
          <NavLink
            to="/notifications"
            className="bv-btn-quiet min-w-[44px] relative"
            aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
          >
            <Icon name="bell" />
            {unread > 0 && <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-accent" aria-hidden="true" />}
          </NavLink>
          <button type="button" onClick={() => setLauncherOpen(true)} className="bv-btn-quiet min-w-[44px]" aria-label="Search">
            <Icon name="search" />
          </button>
        </div>
      </header>

      <main id="main" className="flex-1 min-w-0 pb-20 md:pb-0">
        <Outlet />
      </main>

      {/* Mobile bottom navigation */}
      <nav aria-label="Main" className="md:hidden fixed bottom-0 inset-x-0 z-20 border-t bv-sep bg-bg">
        {moreOpen && (
          <div id="more-nav" className="border-b bv-sep px-2 py-2 grid grid-cols-2 gap-1">
            {MORE_MOBILE.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => navClass(isActive)}>
                <Icon name={item.icon} />
                {item.label}
              </NavLink>
            ))}
          </div>
        )}
        <div className="grid grid-cols-4 h-16" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
          {PRIMARY_MOBILE.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              aria-label={item.short ? item.label : undefined}
              className={({ isActive }) =>
                `flex flex-col items-center justify-center gap-1 text-xs ${isActive ? "text-ink font-medium" : "text-muted"}`
              }
            >
              <Icon name={item.icon} size={22} />
              <span aria-hidden={item.short ? true : undefined}>{item.short ?? item.label}</span>
            </NavLink>
          ))}
          <button
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            aria-expanded={moreOpen}
            aria-controls="more-nav"
            className={`flex flex-col items-center justify-center gap-1 text-xs ${moreActive ? "text-ink font-medium" : "text-muted"}`}
          >
            <Icon name="more" size={22} />
            More
          </button>
        </div>
      </nav>

      <CommandLauncher open={launcherOpen} onClose={() => setLauncherOpen(false)} />
    </div>
  );
}
