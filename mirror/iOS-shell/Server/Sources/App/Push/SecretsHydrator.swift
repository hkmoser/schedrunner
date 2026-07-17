import Foundation
import Vapor

/// At startup, pull app-entered settings from the sidecar's secrets store (Settings page →
/// Google Secret Manager) and inject any that the deploy's `.env` didn't set — so the Vapor
/// server's own config (APNs keys, market-data keys, intervals) can be entered in the app
/// instead of `.env`. Never overrides a real env var; best-effort and never throws.
enum SecretsHydrator {
    static func hydrate() async {
        let base = Environment.get("BQ_SIDECAR_URL").flatMap { $0.isEmpty ? nil : $0 } ?? "http://127.0.0.1:8099"
        guard let url = URL(string: "\(base)/settings_resolved") else { return }
        var req = URLRequest(url: url)
        req.timeoutInterval = 4
        guard let (data, response) = try? await URLSession.shared.data(for: req),
              let http = response as? HTTPURLResponse, http.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let values = obj["values"] as? [String: Any] else { return }
        for (k, v) in values {
            let s = "\(v)"
            if !s.isEmpty && (Environment.get(k) ?? "").isEmpty {
                setenv(k, s, 0)   // overwrite=0: a real .env value always wins
            }
        }
    }
}
