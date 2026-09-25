// Inline stroke icons (24px grid) so the app works fully offline.
const PATHS = {
  chat: ["M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"],
  file: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z", "M14 3v5h5", "M9 13h6", "M9 17h6"],
  insight: ["M3 20h18", "M7 16v-5", "M12 16V6", "M17 16v-8"],
  sliders: ["M4 6h9", "M17 6h3", "M4 12h3", "M11 12h9", "M4 18h11", "M19 18h1",
    { circle: [15, 6, 2] }, { circle: [9, 12, 2] }, { circle: [17, 18, 2] }],
  power: ["M12 3v8", "M6.3 6.3a8 8 0 1 0 11.4 0"],
  filter: ["M3 5h18l-7 8v6l-4-2v-4z"],
  send: ["M4 12l16-8-6 16-3-7z", "M11 13l9-9"],
  upload: ["M12 16V4", "M7 9l5-5 5 5", "M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"],
  folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  refresh: ["M20 11a8 8 0 0 0-14.3-4.9L4 8", "M4 3v5h5", "M4 13a8 8 0 0 0 14.3 4.9L20 16", "M20 21v-5h-5"],
  external: ["M14 4h6v6", "M20 4l-9 9", "M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"],
  sparkle: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z", "M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"],
  play: ["M7 4l13 8-13 8z"],
  docPlus: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z", "M14 3v5h5", "M12 11v6", "M9 14h6"],
  download: ["M12 4v12", "M7 11l5 5 5-5", "M4 20h16"],
  pin: ["M12 17v5", "M9 3h6l-1 6 3 3v2H7v-2l3-3z"],
  x: ["M6 6l12 12", "M18 6L6 18"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"],
  check: ["M5 12l5 5 9-10"],
  alert: ["M12 3l10 18H2z", "M12 10v4", "M12 17.5v.5"],
  search: [{ circle: [11, 11, 7] }, "M20 20l-3.5-3.5"],
  key: [{ circle: [8, 15, 4] }, "M11 12l9-9", "M17 6l3 3", "M15 8l2 2"],
  cloud: ["M7 18a4 4 0 0 1-.5-8A6 6 0 0 1 18 9a4.5 4.5 0 0 1 0 9z"],
  laptop: ["M5 5h14v10H5z", "M2 19h20"],
  share: [{ circle: [18, 5, 3] }, { circle: [6, 12, 3] }, { circle: [18, 19, 3] }, "M8.6 13.5l6.8 4", "M15.4 6.5l-6.8 4"],
};

export default function Icon({ name, size = 18, className = "" }) {
  const parts = PATHS[name] || [];
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {parts.map((p, i) =>
        typeof p === "string" ? <path key={i} d={p} /> : <circle key={i} cx={p.circle[0]} cy={p.circle[1]} r={p.circle[2]} />,
      )}
    </svg>
  );
}
