// Original stroke icon set (24px grid, 1.75 stroke). No third-party artwork.
import type { ReactNode } from "react";

function I({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <svg viewBox="0 0 24 24" width={16} height={16} fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden={label ? undefined : true} role={label ? "img" : undefined} aria-label={label}>
      {children}
    </svg>
  );
}

export const IconBoard = () => <I><path d="M3 12h4l3-8 4 16 3-8h4" /></I>;
export const IconStar = ({ filled }: { filled?: boolean }) => (
  <svg viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth={1.75} strokeLinejoin="round" aria-hidden>
    <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
  </svg>
);
export const IconWallet = () => <I><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3 10h18M16 14.5h2" /></I>;
export const IconChart = () => <I><path d="M4 19V5M4 19h16M8 15l3-4 3 2 5-6" /></I>;
export const IconFlask = () => <I><path d="M9 3h6M10 3v6L4.5 18.5A1.6 1.6 0 0 0 5.9 21h12.2a1.6 1.6 0 0 0 1.4-2.5L14 9V3" /><path d="M7.5 14h9" /></I>;
export const IconGear = () => <I><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></I>;
export const IconShield = () => <I><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z" /><path d="M9 12l2 2 4-4" /></I>;
export const IconUser = () => <I><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></I>;
export const IconBell = () => <I><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z" /><path d="M10 20a2 2 0 0 0 4 0" /></I>;
export const IconInfo = () => <I><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5v.5" /></I>;
export const IconAlert = () => <I><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17v.5" /></I>;
export const IconCheck = () => <I><path d="M5 12.5l4.5 4.5L19 7.5" /></I>;
export const IconX = () => <I><path d="M6 6l12 12M18 6L6 18" /></I>;
export const IconMinus = () => <I><path d="M6 12h12" /></I>;
export const IconLeft = () => <I><path d="M15 5l-7 7 7 7" /></I>;
export const IconRight = () => <I><path d="M9 5l7 7-7 7" /></I>;
export const IconRefresh = () => <I><path d="M20 11a8 8 0 0 0-14.6-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5L20 16M20 20v-4h-4" /></I>;
export const IconSearch = () => <I><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></I>;
export const IconLayers = () => <I><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></I>;
export const IconClock = () => <I><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></I>;
export const IconExplain = () => <I><path d="M4 5h16v11H9l-5 4z" /><path d="M8 9h8M8 12.5h5" /></I>;
export const IconEye = () => <I><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></I>;
export const IconPlay = () => <I><path d="M7 5l12 7-12 7z" /></I>;
export const IconPause = () => <I><path d="M8 5v14M16 5v14" /></I>;
export const IconStep = () => <I><path d="M6 5l9 7-9 7zM18 5v14" /></I>;

/** True Edge mark: an original "edge" glyph (two converging lines meeting a probability arc). */
export function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden>
      <rect x="1" y="1" width="30" height="30" rx="8" fill="#10181f" stroke="#2b3744" />
      <path d="M7 22c4-9 10-13 18-13" stroke="#34c3c9" strokeWidth="2.2" fill="none" strokeLinecap="round" />
      <path d="M7 22h18" stroke="#7d8a97" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="19.5" cy="11.2" r="2.4" fill="#7adfe3" />
    </svg>
  );
}
