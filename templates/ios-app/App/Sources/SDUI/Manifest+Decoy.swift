import Foundation

extension Manifest {
    /// Decoy __APP_NAME_LOWER__ shown for the all-even "guest" passcode: a __APP_NAME_LOWER__-shaped
    /// page of dummy content, built locally (no network, no real data). Mirrors
    /// Web/src/sdui/decoy.ts. Decoded from a literal so the tree stays readable.
    static func decoy() -> Manifest {
        let json = """
        {
          "schemaVersion": 1,
          "generatedAt": "",
          "theme": {
            "colors": {
              "bg": "#0b1020", "bgAccent": "#16204a", "cardBg": "#161c2e",
              "textPrimary": "#f2f5ff", "textSecondary": "#9aa4c4", "accent": "#6ea8fe",
              "up": "#43d18a", "down": "#ff6b6b", "divider": "#2a3350"
            },
            "fonts": { "family": "-apple-system, system-ui, sans-serif" },
            "spacing": 12, "radius": 20
          },
          "data": {
            "meta": { "updatedAtFormatted": "Updated just now", "stale": false },
            "weather": {
              "locationName": "Anytown", "tempFormatted": "72°", "condition": "Sunny",
              "icon": "sun.max.fill", "hiFormatted": "H:78°", "loFormatted": "L:60°"
            },
            "markets": {
              "asOfFormatted": "As of 2:00 PM", "timescaleFormatted": "Today · sample",
              "series": [
                { "symbol": "AAA", "name": "Sample One", "priceFormatted": "$100.00", "changePctFormatted": "+0.50%", "direction": "up", "color": "#6ea8fe", "points": [100, 100.2, 100.1, 100.5, 100.4, 100.6] },
                { "symbol": "BBB", "name": "Sample Two", "priceFormatted": "$50.00", "changePctFormatted": "-0.30%", "direction": "down", "color": "#ff6b6b", "points": [100, 99.9, 99.7, 99.8, 99.6, 99.7] }
              ]
            }
          },
          "screen": {
            "type": "screen",
            "style": { "background": "$bg" },
            "children": [
              { "type": "scroll", "children": [
                { "type": "vstack", "style": { "spacing": 16, "padding": 16 }, "children": [
                  { "type": "hstack", "style": { "align": "center" }, "children": [
                    { "type": "text", "props": { "text": "__APP_NAME__" }, "style": { "font": "largeTitle", "weight": "bold", "color": "$textPrimary" } },
                    { "type": "spacer" },
                    { "type": "text", "binding": "meta.updatedAtFormatted", "style": { "font": "caption", "color": "$textSecondary" } }
                  ] },
                  { "type": "card", "style": { "background": "$cardBg", "cornerRadius": 20, "padding": 16 }, "children": [
                    { "type": "hstack", "style": { "align": "center", "spacing": 12 }, "children": [
                      { "type": "image", "binding": "weather.icon", "style": { "font": "largeTitle", "color": "$accent" } },
                      { "type": "vstack", "children": [
                        { "type": "text", "binding": "weather.tempFormatted", "style": { "font": "largeTitle", "weight": "semibold", "color": "$textPrimary" } },
                        { "type": "text", "binding": "weather.condition", "style": { "font": "subhead", "color": "$textSecondary" } }
                      ] },
                      { "type": "spacer" },
                      { "type": "vstack", "style": { "align": "trailing" }, "children": [
                        { "type": "text", "binding": "weather.locationName", "style": { "font": "headline", "color": "$textPrimary" } },
                        { "type": "hstack", "style": { "spacing": 8 }, "children": [
                          { "type": "text", "binding": "weather.hiFormatted", "style": { "font": "caption", "color": "$textSecondary" } },
                          { "type": "text", "binding": "weather.loFormatted", "style": { "font": "caption", "color": "$textSecondary" } }
                        ] }
                      ] }
                    ] }
                  ] },
                  { "type": "card", "style": { "background": "$cardBg", "cornerRadius": 20, "padding": 16 }, "children": [
                    { "type": "hstack", "style": { "align": "center" }, "children": [
                      { "type": "text", "props": { "text": "Markets" }, "style": { "font": "headline", "weight": "semibold", "color": "$textPrimary" } },
                      { "type": "spacer" },
                      { "type": "text", "binding": "markets.asOfFormatted", "style": { "font": "caption", "color": "$textSecondary" } }
                    ] },
                    { "type": "lineChart", "binding": "markets.series", "style": { "height": 160 } },
                    { "type": "text", "binding": "markets.timescaleFormatted", "style": { "font": "caption", "color": "$textSecondary", "align": "center" } }
                  ] }
                ] }
              ] }
            ]
          }
        }
        """
        if let m = try? JSONDecoder().decode(Manifest.self, from: Data(json.utf8)) {
            return m
        }
        // Should never happen (literal is valid); keep a safe, empty fallback.
        return Manifest(
            schemaVersion: 1, generatedAt: "", theme: nil, data: nil, nav: nil,
            screen: Node(type: "screen", nodeID: nil, props: nil, style: nil,
                         binding: nil, action: nil, children: nil)
        )
    }
}
