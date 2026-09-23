import type { CSSProperties } from "react";
export function Icon({ name, size = 19, className = "", style }: { name: string; size?: number; className?: string; style?: CSSProperties }) {
  const paths: Record<string, React.ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
    client: <><circle cx="10" cy="8" r="4"/><path d="M3 21v-2a7 7 0 0 1 12-5"/><circle cx="17" cy="17" r="3"/><path d="m19.3 19.3 2.2 2.2"/></>,
    graph: <><circle cx="5" cy="5" r="3"/><circle cx="19" cy="8" r="3"/><circle cx="10" cy="20" r="3"/><path d="m8 6 8 1M6 8l3 9m3-1 5-5"/></>,
    download: <><path d="M12 3v12m-4-4 4 4 4-4M4 17v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></>,
    search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
    upRight: <path d="M6 18 18 6M6 6h12v12"/>,
    chevron: <path d="m9 5 7 7-7 7"/>,
    left: <path d="m15 5-7 7 7 7"/>,
    filter: <><path d="M3 6h18M6 12h12M9 18h6"/><circle cx="8" cy="6" r="2" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="2" fill="currentColor" stroke="none"/></>,
    close: <path d="m6 6 12 12M18 6 6 18"/>,
    check: <path d="m5 12 4 4L19 6"/>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5M5.5 8a7 7 0 0 1 12-3L20 8M4 16l2.5 3A7 7 0 0 0 19 16"/></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
    layers: <><path d="m12 3 10 6-10 6L2 9l10-6Zm-8 11 8 5 8-5M4 18l8 5 8-5"/></>,
    shield: <><path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4Z"/><path d="m8 12 3 3 5-6"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} style={style}>{paths[name] || paths.info}</svg>;
}
