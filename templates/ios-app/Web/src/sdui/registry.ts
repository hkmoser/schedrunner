// SF Symbol-style icon names -> emoji glyphs for the web renderer.
// The native app uses the same names directly as SF Symbols. Unknown names fall
// back to a neutral glyph so an unrecognized icon never breaks layout.
const ICONS: Record<string, string> = {
  "sun.max.fill": "☀️",
  "cloud.sun.fill": "⛅",
  "cloud.fill": "☁️",
  "cloud.rain.fill": "🌧️",
  "cloud.bolt.fill": "⛈️",
  "cloud.snow.fill": "🌨️",
  "cloud.fog.fill": "🌫️",
  "moon.fill": "🌙",
  "moon.stars.fill": "🌌",
  "wind": "💨",
  "chart.line.uptrend.xyaxis": "📈",
  "chart.line.downtrend.xyaxis": "📉",
  "chart.bar.fill": "📊",
  "house.fill": "🏠",
  "gearshape.fill": "⚙️",
  "tablecells": "▦",
  "calendar": "📅",
  "figure.walk": "🚶",
  "car.fill": "🚗",
  "airplane": "✈️",
  "mappin.circle.fill": "📍",
  "mappin": "📍",
  "bolt.fill": "⚡️",
  "powerplug.fill": "🔌",
  "arrow.up": "▲",
  "arrow.down": "▼",
  "folder.fill": "📁",
  "doc.text.fill": "📄",
  "doc.text": "📄",
  "chevron.right": "›",
  "chevron.left.forwardslash.chevron.right": "⟨/⟩",
  "wrench.and.screwdriver.fill": "🛠️",
  "lightbulb.fill": "💡",
  "sensor.tag.radiowaves.forward.fill": "📡",
  "star.fill": "⭐",
  "tag.fill": "🏷️",
  "tag": "🏷️",
  "wifi": "📶",
  "message.fill": "💬",
  "paperplane.fill": "📤",
  "envelope.fill": "✉️",
};

export function iconGlyph(name: string | undefined): string {
  if (!name) return "•";
  return ICONS[name] ?? "•";
}
