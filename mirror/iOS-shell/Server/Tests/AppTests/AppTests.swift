import XCTVapor
@testable import App

final class AppTests: XCTestCase {
    private func makeApp() async throws -> Application {
        let app = try await Application.make(.testing)
        try await configure(app)
        return app
    }

    /// The manifest is always well-formed and complete, even with no API keys
    /// (providers fall back to stub data via the last-good cache miss path).
    func testDashboardManifestStructure() async throws {
        let app = try await makeApp()
        try await app.test(.GET, "dashboard", afterResponse: { res async throws in
            XCTAssertEqual(res.status, .ok)
            let manifest = try res.content.decode(Manifest.self)
            XCTAssertEqual(manifest.schemaVersion, Manifest.currentSchemaVersion)

            guard case .object(let data) = manifest.data else {
                return XCTFail("data should be an object")
            }
            XCTAssertNotNil(data["weather"], "weather card present")
            XCTAssertNotNil(data["stocks"], "stocks card present")
            XCTAssertNotNil(data["mortgage"], "mortgage card present")
            XCTAssertNotNil(data["property"], "property card present")
            XCTAssertNotNil(data["meta"], "meta present")

            guard case .object = manifest.screen else {
                return XCTFail("screen should be an object")
            }
            guard case .object = manifest.theme else {
                return XCTFail("theme should be an object")
            }
        })
        try await app.asyncShutdown()
    }

    /// The BigQuery page renders even with the sidecar down (provider falls back
    /// to stub data via the cache-miss path).
    func testBigQueryPage() async throws {
        let app = try await makeApp()
        try await app.test(.GET, "screen/bigquery", afterResponse: { res async throws in
            XCTAssertEqual(res.status, .ok)
            let manifest = try res.content.decode(Manifest.self)
            guard case .object(let data) = manifest.data else { return XCTFail("data object") }
            XCTAssertNotNil(data["bq"], "bq data present")
            guard case .object = manifest.screen else { return XCTFail("screen object") }
        })
        try await app.asyncShutdown()
    }

    /// The tab pages render (sidecar down -> stub data) and carry nav. The keyed
    /// data bag each page expects is present so its bindings resolve.
    func testTabPages() async throws {
        let app = try await makeApp()
        let pages: [(path: String, dataKey: String?)] = [
            ("screen/afm", "bq"),
            ("screen/afm48", "afm48"),
            ("screen/afm_health", "afmhealth"),
            ("screen/config", "config"),
            ("screen/balances", "balances"),
            ("screen/budget", "budget"),
            ("screen/ynab", "ynab"),
        ]
        for page in pages {
            try await app.test(.GET, page.path, afterResponse: { res async throws in
                XCTAssertEqual(res.status, .ok, "GET /\(page.path)")
                let manifest = try res.content.decode(Manifest.self)
                XCTAssertNotNil(manifest.nav, "nav present on \(page.path)")
                guard case .object(let data) = manifest.data else { return XCTFail("data object") }
                if let key = page.dataKey {
                    XCTAssertNotNil(data[key], "\(key) data present on \(page.path)")
                }
                guard case .object = manifest.screen else { return XCTFail("screen object") }
            })
        }
        try await app.asyncShutdown()
    }

    func testHealthEndpoint() async throws {
        let app = try await makeApp()
        try await app.test(.GET, "healthz", afterResponse: { res async throws in
            XCTAssertEqual(res.status, .ok)
            let health = try res.content.decode(HealthResponse.self)
            XCTAssertEqual(health.status, "ok")
            XCTAssertEqual(health.schemaVersion, Manifest.currentSchemaVersion)
        })
        try await app.asyncShutdown()
    }

    /// Templates must parse into JSONValue (guards malformed template edits).
    func testTemplatesLoad() throws {
        let templates = try Templates.load()
        guard case .object = templates.screen else {
            return XCTFail("screen template should be an object")
        }
        guard case .object = templates.theme else {
            return XCTFail("theme template should be an object")
        }
    }

    func testJSONValueRoundTrip() throws {
        let original = JSONValue.obj([
            ("s", .string("x")),
            ("i", .int(3)),
            ("d", .double(1.5)),
            ("b", .bool(true)),
            ("n", .null),
            ("a", .array([.int(1), .string("y")])),
        ])
        let encoded = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(JSONValue.self, from: encoded)
        XCTAssertEqual(original, decoded)
    }
}
