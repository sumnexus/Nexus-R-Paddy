// Small line icons for the actions, drawn for this project (24 × 24, stroke = currentColor).
const P = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round' };

const paths = {
  irrigate: <><path d="M12 3c3.5 4.4 5.5 7.6 5.5 10.2A5.5 5.5 0 0 1 6.5 13.2C6.5 10.6 8.5 7.4 12 3z" /><path d="M9.5 14.5a2.6 2.6 0 0 0 2.5 2.3" /></>,
  drain: <><path d="M4 7h16" /><path d="M7 11h10" /><path d="M10 15h4" /><path d="M12 15v6" /><path d="m9.5 18.5 2.5 2.5 2.5-2.5" /></>,
  transplant: <><path d="M12 21v-8" /><path d="M12 13c0-4 3-6.5 7-6.5 0 4-3 6.5-7 6.5z" /><path d="M12 15c0-3-2.4-5-5.5-5 0 3 2.4 5 5.5 5z" /><path d="M5 21h14" /></>,
  puddle: <><path d="M3 15c2 0 2-1.5 4-1.5s2 1.5 4 1.5 2-1.5 4-1.5 2 1.5 4 1.5 2-1.5 2-1.5" /><path d="M3 19c2 0 2-1.5 4-1.5s2 1.5 4 1.5 2-1.5 4-1.5 2 1.5 4 1.5 2-1.5 2-1.5" /><path d="M12 4v7" /><path d="m9 8 3 3 3-3" /></>,
  fertilise: <><path d="M7 8h10l1.5 12h-13z" /><path d="M9 8V6a3 3 0 0 1 6 0v2" /><path d="M10 14h4" /></>,
  spray: <><path d="M8 10h6v11H8z" /><path d="M9 10V7h4v3" /><path d="M13 7h3" /><path d="M18.5 4.5v.01M20.5 7v.01M18.5 9.5v.01M21 4v.01" /></>,
  scout: <><circle cx="10.5" cy="10.5" r="6" /><path d="m15 15 5.5 5.5" /></>,
  soil_check: <><path d="M12 3v11" /><path d="M9.5 14h5l-2.5 7z" /><path d="M4 9h4M16 9h4" /></>,
  amend: <><path d="M5 20 15 10" /><path d="M14 4l6 6-3 3-6-6z" /><path d="M4 21h4" /></>,
  harvest: <><path d="M6 20c5-1.5 9-5.5 10-12" /><path d="M16 8c1.5-2 3.5-3 5-3-0.3 2.5-1.5 4.3-3.5 5.5" /><path d="M5 21l2-2" /></>,
  hold: <><rect x="6.5" y="5" width="3.5" height="14" rx="1" /><rect x="14" y="5" width="3.5" height="14" rx="1" /></>,
  wait: <><circle cx="12" cy="12" r="8" /><path d="M12 7.5V12l3 2" /></>,
  play: <path d="M7 5l12 7-12 7z" />,
  pause: <><path d="M8 5v14" /><path d="M16 5v14" /></>,
  step: <><path d="M6 5l9 7-9 7z" /><path d="M18 5v14" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />,
};

export function Icon({ name, className = 'icon', title }) {
  const body = paths[name] || paths.hold;
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden={title ? undefined : true} role={title ? 'img' : undefined} {...P}>
      {title ? <title>{title}</title> : null}
      {body}
    </svg>
  );
}
