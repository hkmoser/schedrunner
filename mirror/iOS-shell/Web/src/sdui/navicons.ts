// Modern inline-SVG line icons for the bottom nav (replaces the emoji glyphs).
// Keyed by the SF Symbol names the manifest's nav uses, so native keeps real SF
// Symbols while web gets crisp vector icons.
const PATHS: Record<string, string> = {
  // home
  "house.fill": '<path d="M3 10.6 12 3l9 7.6"/><path d="M5.5 9.4V20a1 1 0 0 0 1 1H10v-6h4v6h3.5a1 1 0 0 0 1-1V9.4"/>',
  // map pin (activity / location)
  "map.fill": '<path d="M12 21s6.5-5.6 6.5-10.5a6.5 6.5 0 1 0-13 0C5.5 15.4 12 21 12 21z"/><circle cx="12" cy="10.5" r="2.4"/>',
  "location.fill": '<path d="M12 21s6.5-5.6 6.5-10.5a6.5 6.5 0 1 0-13 0C5.5 15.4 12 21 12 21z"/><circle cx="12" cy="10.5" r="2.4"/>',
  "chart.bar.fill": '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/>',
  // pie chart (budget)
  "chart.pie.fill": '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M12 3v9h9A9 9 0 0 0 12 3z"/>',
  // clock with a circular history arrow (48h activity)
  "clock.arrow.circlepath": '<path d="M20.5 7.5V4M20.5 7.5H17M20.4 8a8.5 8.5 0 1 0 .9 6"/><path d="M12 7.5V12l3 1.8"/>',
  // credit card (YNAB balances)
  "creditcard.fill": '<rect x="3" y="5.5" width="18" height="13" rx="2.4"/><path d="M3 9.5h18"/><path d="M6.5 14.5h3"/>',
  // sliders (config)
  "slider.horizontal.3": '<line x1="4" y1="8" x2="20" y2="8"/><circle cx="9" cy="8" r="2.2" fill="var(--c-bg,#0b1020)"/><line x1="4" y1="16" x2="20" y2="16"/><circle cx="15" cy="16" r="2.2" fill="var(--c-bg,#0b1020)"/>',
  "gearshape.fill": '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v2.5M12 18.5V21M4.2 7.5l2.2 1.3M17.6 15.2l2.2 1.3M4.2 16.5l2.2-1.3M17.6 8.8l2.2-1.3"/>',
  // dollar in a circle (balances)
  "dollarsign.circle.fill": '<circle cx="12" cy="12" r="9"/><path d="M12 6.8v10.4M14.6 9c-.5-.8-1.5-1.3-2.6-1.3-1.5 0-2.6.8-2.6 2s1.1 1.8 2.6 1.9c1.5.1 2.7.7 2.7 2s-1.2 2-2.7 2c-1.1 0-2.1-.5-2.6-1.3"/>',
  // house in a circle (smart home)
  "house.circle.fill": '<circle cx="12" cy="12" r="9.2"/><path d="M7.5 12 12 8.2l4.5 3.8"/><path d="M8.7 11.2V16h6.6v-4.8"/>',
  // wrench + screwdriver (System group)
  "wrench.and.screwdriver.fill": '<path d="M14.7 6.3a3.5 3.5 0 0 0-4.6 4.2L4 16.6 6.4 19l6.1-6.1a3.5 3.5 0 0 0 4.2-4.6l-2 2-1.9-1.9 2-2z"/>',
  // document with lines (logs)
  "doc.text.fill": '<path d="M6 3h7l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M13 3v5h5"/><path d="M8.5 12h7M8.5 15h7M8.5 18h4"/>',
  // angle brackets with a slash (repos / code)
  "chevron.left.forwardslash.chevron.right": '<path d="M8.5 8 4.5 12l4 4"/><path d="M15.5 8l4 4-4 4"/><path d="M13.5 6l-3 12"/>',
  // folder (docs)
  "folder.fill": '<path d="M3 7a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
  // timer (schedrunner)
  "timer": '<circle cx="12" cy="13" r="8"/><path d="M12 13V8.5"/><path d="M9 2h6"/>',
  // grid of cells (BQ tables)
  "tablecells": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M3.5 14.5h17M9 4.5v15M15 4.5v15"/>',
  // document with a magnifier (sched logs)
  "doc.text.magnifyingglass": '<path d="M5 3.5h7l3.5 3.5v5.5"/><path d="M12 3.5V7h3.5"/><path d="M7.5 8.5h4M7.5 11.5h3"/><circle cx="13" cy="15.5" r="3"/><path d="M15.2 17.7 18 20.5"/>',
  // speech bubble (messages)
  "message.fill": '<path d="M20 11.5a7.5 7 0 0 1-7.5 7 8.6 8.6 0 0 1-3-.5L5 19.5l1.1-3.2A6.7 6.7 0 0 1 5 11.5 7.5 7 0 0 1 12.5 4.5 7.5 7 0 0 1 20 11.5z"/>',
  // key (secrets & keys)
  "key.fill": '<circle cx="8.5" cy="8.5" r="4.5"/><path d="M11.7 11.7 19 19"/><path d="M15.5 15.5l2-2M18 18l2-2"/>',
};

export function navIconSVG(name: string | undefined): string {
  const inner = (name && PATHS[name]) || '<circle cx="12" cy="12" r="3"/>';
  return `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
}
