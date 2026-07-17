import Foundation
import Vapor

extension CharacterSet {
    /// Safe characters for a query *value* sent to the sidecar: alphanumerics, a few
    /// unreserved marks, and `/` (paths stay readable). Everything else — including the
    /// query delimiters & + = ? # ; and space — is percent-encoded so names containing
    /// them survive the round-trip through the sidecar's query parser.
    static let sidecarQueryValue: CharacterSet = {
        var set = CharacterSet.alphanumerics
        set.insert(charactersIn: "-._~/")
        return set
    }()
}

/// afm_now 24h summary — passes the sidecar's `/afm` JSON through as `bq`.
public struct AFMProvider: DataProvider {
    public let key = "bq"
    // Short TTL so switching the Activity device on the Config page reflects quickly.
    public let ttl: TimeInterval = 120
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String {
        "bq:afm:\(config.afmRange ?? "today")"
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        var url = "\(config.bqSidecarURL)/afm"
        if let range = config.afmRange,
           let enc = range.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) {
            url += "?range=\(enc)"
        }
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("afm")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("afm_now · last 24h")),
            ("subtitleFormatted", .string("sidecar unavailable · set BQ_DATASET")),
            ("columns", .array([.string("metric"), .string("value")])),
            ("rows", .array([
                .array([.string("rows"), .string("—")]),
            ])),
        ])
    }
}

/// Activity Log — the sidecar's `/afm_log` (each stop/move/charge as a newest-first event
/// line, minimal fields) passed through as `afmlog`. Honors the Today/Yesterday/Week range.
public struct AFMLogProvider: DataProvider {
    public let key = "afmlog"
    public let ttl: TimeInterval = 120
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String {
        "afmlog:\(config.afmRange ?? "today")"
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        var url = "\(config.bqSidecarURL)/afm_log"
        if let range = config.afmRange,
           let enc = range.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) {
            url += "?range=\(enc)"
        }
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("afm_log")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Activity Log")),
            ("subtitleFormatted", .string("sidecar unavailable · set BQ_DATASET")),
            ("ranges", .array([])),
            ("events", .array([])),
            ("rowCount", .int(0)),
            ("emptyFormatted", .string("Activity log unavailable.")),
        ])
    }
}

/// Smart Home Log — the sidecar's `/smarthome_log` (active state changes only: motion, door/
/// contact sensors, lights on/off) passed through as `shlog`, newest-first.
public struct SmartHomeLogProvider: DataProvider {
    public let key = "shlog"
    public let ttl: TimeInterval = 60
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String { "shlog" }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/smarthome_log", as: JSONValue.self)
        return try value.requireOK("smarthome_log")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Smart Home Log")),
            ("subtitleFormatted", .string("sidecar unavailable")),
            ("events", .array([])),
            ("rowCount", .int(0)),
            ("emptyFormatted", .string("Smart Home log unavailable.")),
        ])
    }
}

/// AFM Live health — the sidecar's `/afm_health`: a precise per-run success/failure timeline
/// of the afm_live job over the past day, with back-off phases, passed through as `afmhealth`.
public struct AFMHealthProvider: DataProvider {
    public let key = "afmhealth"
    public let ttl: TimeInterval = 45   // live-ish job status; cheap (reads a local log)
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String { "afmhealth" }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/afm_health", as: JSONValue.self)
        return try value.requireOK("afm_health")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Live Health")),
            ("subtitleFormatted", .string("sidecar unavailable")),
            ("statusFormatted", .string("—")),
            ("statusColor", .string("#9aa4c4")),
            ("summaryFormatted", .string("")),
            ("runChart", .obj([("bars", .array([])), ("hasData", .bool(false)),
                               ("captionFormatted", .string("")), ("emptyFormatted", .string(""))])),
            ("runs", .array([])),
            ("rowCount", .int(0)),
            ("emptyFormatted", .string("afm_live health unavailable.")),
        ])
    }
}

/// Last-48 page — two tabs over `afm48`: the `afm_now` materialized view (default) and
/// the raw 48h fix-history ('transaction') table. ?view=raw selects the second tab.
public struct AFM48Provider: DataProvider {
    public let key = "afm48"
    // The raw table is heavier and changes slowly; a longer TTL than the live tab.
    public let ttl: TimeInterval = 300
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String {
        "afm48:\(config.afm48View == "raw" ? "raw" : "now")"
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let endpoint = config.afm48View == "raw" ? "/afm_raw?hours=48" : "/afm_now"
        let value = try await client.getJSON("\(config.bqSidecarURL)\(endpoint)", as: JSONValue.self)
        return try value.requireOK("afm48")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("AFM · last 48h")),
            ("subtitleFormatted", .string("sidecar unavailable · set BQ_DATASET")),
            ("columns", .array([.string("date_time"), .string("deviceName"), .string("latitude"), .string("longitude")])),
            ("rows", .array([])),
            ("rowCount", .int(0)),
        ])
    }
}

/// BigQuery table browser — passes the sidecar's `/bqtables?table=` JSON through as
/// `bqtables`. The table being drilled into comes from the request (config.bqTable),
/// so the cache key varies by table.
public struct BQTablesProvider: DataProvider {
    public let key = "bqtables"
    public let ttl: TimeInterval = 120
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String {
        "bqtables:\(config.bqDataset ?? "")/\(config.bqTable ?? "")/\(config.bqView ?? "")"
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        func enc(_ s: String?) -> String {
            (s ?? "").addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) ?? ""
        }
        let url = "\(config.bqSidecarURL)/bqtables?dataset=\(enc(config.bqDataset))"
            + "&table=\(enc(config.bqTable))&view=\(enc(config.bqView))"
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("bqtables")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func ds(_ name: String) -> JSONValue {
            .obj([("name", .string(name)), ("kind", .string("Dataset")), ("icon", .string("tablecells")),
                  ("metaFormatted", .string("dataset")), ("navHref", .string("/screen/bqtables?dataset=" + name))])
        }
        return .obj([
            ("title", .string("BigQuery")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("kind", .string("list")),
            ("tables", .array([ds("home_afm"), ds("analytics"), ds("budget")])),
            ("fields", .array([])),
            ("rowCount", .int(3)),
        ])
    }
}

/// Editable key/value config — passes the sidecar's `/config` JSON through as
/// `config`. ttl 0 keeps it always-fresh so a save is reflected immediately.
public struct ConfigProvider: DataProvider {
    public let key = "config"
    public let ttl: TimeInterval = 0
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/config", as: JSONValue.self)
        return try value.requireOK("config")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Config")),
            ("subtitleFormatted", .string("sidecar unavailable · set BQ_DATASET")),
            ("items", .array([])),
        ])
    }
}

/// Known-location label screen — passes the sidecar's `/known_locs` JSON through as
/// `known`: a prefilled form (from the tapped stop) + the list of saved places. Always
/// fresh so a just-saved place shows immediately.
public struct KnownLocProvider: DataProvider {
    public let key = "known"
    public let ttl: TimeInterval = 0
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        var url = "\(config.bqSidecarURL)/known_locs"
        var query: [String] = []
        func enc(_ s: String) -> String { s.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) ?? "" }
        if let lat = config.knownLat { query.append("lat=\(enc(lat))") }
        if let lon = config.knownLon { query.append("lon=\(enc(lon))") }
        if let place = config.knownPlace { query.append("place=\(enc(place))") }
        if !query.isEmpty { url += "?" + query.joined(separator: "&") }
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("known_locs")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Label location")),
            ("subtitleFormatted", .string("sidecar unavailable · set BQ_DATASET")),
            ("fields", .array([])),
            ("locs", .array([])),
        ])
    }
}

/// __APP_NAME__ banner — a 0-or-1 `repoBanner.cards` list, present when the specific repo has an
/// open PR or unmerged branch. Cached (ttl) so it doesn't hit GitHub on every __APP_NAME_LOWER__ load.
public struct RepoBannerProvider: DataProvider {
    public let key = "repoBanner"
    public let ttl: TimeInterval = 300
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/repo_banner", as: JSONValue.self)
        return try value.requireOK("repo_banner")
    }

    public func stub(config: AppConfig) -> JSONValue { .obj([("cards", .array([]))]) }
}

/// Settings page — app-entered config/secrets, stored via the sidecar's secrets backend
/// (Google Secret Manager). Passes the sidecar's `/settings` JSON through as `settings`.
public struct SettingsProvider: DataProvider {
    public let key = "settings"
    public let ttl: TimeInterval = 0
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/settings", as: JSONValue.self)
        return try value.requireOK("settings")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Settings")),
            ("subtitleFormatted", .string("sidecar unavailable")),
            ("groups", .array([])),
        ])
    }
}

/// Ship-confirm screen — passes the repo/branch context to the sidecar's `/repos_ship`,
/// which returns the confirm fields + summary. Its submit posts to /repos_pr.
public struct ReposShipProvider: DataProvider {
    public let key = "reposShip"
    public let ttl: TimeInterval = 0
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String {
        "reposShip:\(config.shipOwner ?? "")/\(config.shipName ?? "")/\(config.shipBranch ?? "")"
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        var query: [String] = []
        func enc(_ s: String) -> String { s.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) ?? "" }
        if let v = config.shipOwner { query.append("owner=\(enc(v))") }
        if let v = config.shipName { query.append("name=\(enc(v))") }
        if let v = config.shipBranch { query.append("branch=\(enc(v))") }
        if let v = config.shipBase { query.append("base=\(enc(v))") }
        if let v = config.shipPR { query.append("pr=\(enc(v))") }
        var url = "\(config.bqSidecarURL)/repos_ship"
        if !query.isEmpty { url += "?" + query.joined(separator: "&") }
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("repos_ship")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Ship branch")),
            ("subtitleFormatted", .string("sidecar unavailable")),
            ("summaryFormatted", .string("")),
            ("warnFormatted", .string("")),
            ("fields", .array([])),
            ("backHref", .string("/screen/repos")),
        ])
    }
}

/// Current account balances — passes the sidecar's `/balances` JSON through as
/// `balances`. Short TTL so a sheet push shows up quickly.
public struct BalancesProvider: DataProvider {
    public let key = "balances"
    public let ttl: TimeInterval = 60
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/balances", as: JSONValue.self)
        return try value.requireOK("balances")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func acct(_ name: String, _ bal: String, _ dir: String, _ kind: String) -> JSONValue {
            .obj([("name", .string(name)), ("balanceFormatted", .string(bal)),
                  ("direction", .string(dir)), ("kind", .string(kind))])
        }
        return .obj([
            ("title", .string("Balances")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("netFormatted", .string("$48,250.00")),
            ("netDirection", .string("up")),
            ("accounts", .array([
                acct("Ally Savings", "$18,400.00", "up", "Asset"),
                acct("Savings", "$15,250.00", "up", "Asset"),
                acct("Ally Spending", "$9,200.00", "up", "Asset"),
                acct("Spending", "$4,100.00", "up", "Asset"),
                acct("Bills", "$2,000.00", "up", "Asset"),
                acct("Ally Spending Alt", "$1,600.00", "up", "Asset"),
                acct("Auto Loan", "-$2,300.00", "down", "Liability"),
            ])),
            ("rowCount", .int(7)),
        ])
    }
}

/// Average monthly spending per category over the last 6 full months, grouped into
/// budget buckets — passes the sidecar's `/budget` JSON through as `budget`. Slow-moving
/// (whole-month aggregates), so a longer TTL keeps the heavy query off the hot path.
public struct BudgetProvider: DataProvider {
    public let key = "budget"
    public let ttl: TimeInterval = 1800
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/budget", as: JSONValue.self)
        return try value.requireOK("budget")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func cat(_ name: String, _ avg: String, _ range: String) -> JSONValue {
            .obj([("name", .string(name)), ("avgFormatted", .string(avg)),
                  ("rangeFormatted", .string(range)), ("direction", .string("up"))])
        }
        func bucket(_ title: String, _ sub: String, _ count: String, _ cats: [JSONValue]) -> JSONValue {
            .obj([("title", .string(title)), ("subtotalFormatted", .string(sub)),
                  ("subtotalDirection", .string("up")), ("countFormatted", .string(count)),
                  ("categories", .array(cats))])
        }
        return .obj([
            ("title", .string("Budget")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("totalAvgFormatted", .string("$6,250.00/mo")),
            ("windowFormatted", .string("Jun 2025 – May 2026")),
            ("buckets", .array([
                bucket("Essential", "$3,900.00/mo", "3 categories", [
                    cat("Rent", "$2,000.00/mo", "$2,000.00 – $2,000.00"),
                    cat("Groceries", "$1,100.00/mo", "$820.00 – $1,440.00"),
                    cat("Utilities", "$800.00/mo", "$610.00 – $980.00"),
                ]),
                bucket("Lifestyle", "$1,450.00/mo", "2 categories", [
                    cat("Dining", "$900.00/mo", "$300.00 – $1,500.00"),
                    cat("Subscriptions", "$550.00/mo", "$520.00 – $560.00"),
                ]),
                bucket("Goals", "$900.00/mo", "1 category", [
                    cat("Vacation Fund", "$900.00/mo", "$0.00 – $1,800.00"),
                ]),
            ])),
            ("emptyFormatted", .string("")),
            ("rowCount", .int(6)),
        ])
    }
}

/// GCP billing cost summary — passes the sidecar's `/gcp_costs` JSON through as
/// `gcpCosts`. Billing data is invoiced monthly so a long TTL is fine; users who
/// want fresher data can pull-to-refresh. Returns a clean empty state when
/// BQ_BILLING_TABLE is not configured.
public struct GCPCostsProvider: DataProvider {
    public let key = "gcpCosts"
    public let ttl: TimeInterval = 3600
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/gcp_costs", as: JSONValue.self)
        return try value.requireOK("gcp_costs")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func svc(_ name: String, _ cost: String, _ pct: String) -> JSONValue {
            .obj([("name", .string(name)), ("costFormatted", .string(cost)), ("pctFormatted", .string(pct))])
        }
        return .obj([
            ("title", .string("GCP Costs")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("monthFormatted", .string("July 2026")),
            ("totalFormatted", .string("$142.37")),
            ("mtdLabelFormatted", .string("Month to date")),
            ("changeFormatted", .string("+$18.42 (+15%) vs June")),
            ("changeDirection", .string("down")),
            ("series", .array([
                .obj([("color", .string("#3B82F6")),
                      ("points", .array([45.2, 67.3, 89.1, 102.4, 110.0, 95.5, 88.0,
                                         98.0, 124.0, 123.95, 142.37].map { .double($0) })),
                      ("dashed", .bool(false))])
            ])),
            ("firstLabelFormatted", .string("Sep '25")),
            ("lastLabelFormatted", .string("Jul '26")),
            ("services", .array([
                svc("BigQuery", "$85.20", "60%"),
                svc("Cloud Storage", "$32.10", "23%"),
                svc("Compute Engine", "$18.05", "13%"),
                svc("Cloud Run", "$7.02", "5%"),
            ])),
            ("rowCount", .int(11)),
            ("emptyFormatted", .string("")),
        ])
    }
}

/// Smart-home event-log summary — passes the sidecar's `/smarthome` JSON through
/// as `smarthome`. Short TTL so the status/last-update header stays current.
public struct SmartHomeProvider: DataProvider {
    public let key = "smarthome"
    public let ttl: TimeInterval = 60
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String {
        // nil (defaults) and "" (none selected) must cache separately, per tier.
        "smarthome:t\(config.smartHomeTypes.map { "=\($0)" } ?? "default")"
            + ":s\(config.smartHomeSources.map { "=\($0)" } ?? "all")"
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        var url = "\(config.bqSidecarURL)/smarthome"
        var query: [String] = []
        if let types = config.smartHomeTypes {
            query.append("types=\(types.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) ?? "")")
        }
        if let sources = config.smartHomeSources {
            query.append("sources=\(sources.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) ?? "")")
        }
        if !query.isEmpty { url += "?" + query.joined(separator: "&") }
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("smarthome")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func src(_ label: String, _ icon: String, _ color: String, _ status: String, _ dir: String, _ meta: String, _ hr: String) -> JSONValue {
            .obj([("label", .string(label)), ("icon", .string(icon)), ("color", .string(color)),
                  ("status", .string(status)), ("direction", .string(dir)),
                  ("metaFormatted", .string(meta)), ("lastHourFormatted", .string(hr))])
        }
        func ev(_ title: String, _ detail: String, _ icon: String, _ color: String, _ time: String, _ type: String) -> JSONValue {
            .obj([("title", .string(title)), ("detail", .string(detail)), ("icon", .string(icon)),
                  ("color", .string(color)), ("timeFormatted", .string(time)), ("type", .string(type))])
        }
        func chip(_ key: String, _ label: String, _ icon: String, _ count: Int, _ active: Bool) -> JSONValue {
            .obj([("key", .string(key)), ("label", .string(label)), ("icon", .string(icon)),
                  ("count", .int(count)), ("labelFormatted", .string("\(label) · \(count)")),
                  ("active", .bool(active)), ("color", .string(active ? "$accent" : "$textSecondary")),
                  ("navHref", .string("/screen/smarthome?types=\(key)"))])
        }
        return .obj([
            ("title", .string("Smart Home")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("statusFormatted", .string("Online")),
            ("statusDirection", .string("up")),
            ("countsFormatted", .string("12 in the last hour · 340 in 24h · 5,000 total")),
            ("sources", .array([
                src("Home Assistant", "house.fill", "#6ea8fe", "Online", "up", "Online · just now", "12/hr"),
                src("Hue", "lightbulb.fill", "#ffd166", "No data", "down", "No data", "0/hr"),
                src("YoLink", "sensor.tag.radiowaves.forward.fill", "#43d18a", "No data", "down", "No data", "0/hr"),
            ])),
            ("types", .array([
                chip("light", "Light", "lightbulb.fill", 142, true),
                chip("binary_sensor", "Binary Sensor", "sensor.fill", 88, true),
                chip("lock", "Lock", "lock.fill", 12, true),
                chip("sensor", "Sensor", "thermometer", 980, false),
            ])),
            ("filterSummaryFormatted", .string("Showing 3 of 4 types · 3 of 1,222 events (24h)")),
            ("allHref", .string("/screen/smarthome?types=binary_sensor,light,lock,sensor")),
            ("defaultsHref", .string("/screen/smarthome")),
            ("events", .array([
                ev("Front Door", "state_changed · on", "lock.fill", "#f4a259", "2:23 PM", "lock"),
                ev("Kitchen Light", "off", "lightbulb.fill", "#ffd166", "2:21 PM", "light"),
                ev("Living Room Motion", "state_changed · detected", "sensor.fill", "#43d18a", "2:18 PM", "binary_sensor"),
            ])),
            ("emptyFormatted", .string("")),
            ("rowCount", .int(3)),
        ])
    }
}

/// Tail of the Mac's ~/log directory — passes the sidecar's `/logs` JSON through as
/// `logs`. Short TTL so recent activity stays current.
public struct LogsProvider: DataProvider {
    public let key = "logs"
    public let ttl: TimeInterval = 30
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String {
        "logs:\(config.logsStatus ?? "all")"
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        var url = "\(config.bqSidecarURL)/logs"
        if let status = config.logsStatus,
           let enc = status.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) {
            url += "?status=\(enc)"
        }
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("logs")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func file(_ name: String, _ meta: String, _ tail: String) -> JSONValue {
            .obj([("name", .string(name)), ("metaFormatted", .string(meta)), ("tail", .string(tail))])
        }
        return .obj([
            ("title", .string("Logs")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("files", .array([
                file("__APP_NAME_LOWER__.log", "12 KB · just now",
                     "12:00:01 server starting\n12:00:02 listening on :8080\n12:00:03 healthcheck ok"),
                file("schedrunner.out", "3 KB · 5m ago",
                     "run nightly-sync ok\nrun balances-push ok"),
            ])),
            ("rowCount", .int(2)),
        ])
    }
}

/// One log's 24h detail + flagged anomalies — passes the sidecar's `/logfile?file=`
/// JSON through as `logfile`. Drill-down behind each Logs card.
public struct LogFileProvider: DataProvider {
    public let key = "logfile"
    public let ttl: TimeInterval = 20
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String { "logfile:\(config.logFile ?? "")" }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        var url = "\(config.bqSidecarURL)/logfile"
        if let file = config.logFile,
           let enc = file.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) {
            url += "?file=\(enc)"
        }
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("logfile")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Log")),
            ("subtitleFormatted", .string("sidecar unavailable")),
            ("anomalies", .array([])),
            ("code", .string("")),
            ("backHref", .string("/screen/logs")),
        ])
    }
}

/// Git + deploy status of the repos schedrunner manages — passes the sidecar's
/// `/repos` JSON through as `repos`.
public struct ReposProvider: DataProvider {
    public let key = "repos"
    public let ttl: TimeInterval = 60
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/repos", as: JSONValue.self)
        return try value.requireOK("repos")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func repo(_ name: String, _ branch: String, _ git: String, _ gitDir: String,
                  _ deploy: String, _ deployDir: String, _ last: String) -> JSONValue {
            .obj([("name", .string(name)), ("branch", .string(branch)),
                  ("gitFormatted", .string(git)), ("gitDirection", .string(gitDir)),
                  ("deployFormatted", .string(deploy)), ("deployDirection", .string(deployDir)),
                  ("lastFormatted", .string(last))])
        }
        return .obj([
            ("title", .string("Repos")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("repos", .array([
                repo("schedrunner", "main", "clean", "up", "in sync", "up", "2h ago · tidy scheduler"),
                repo("__APP_NAME_LOWER__", "main", "2 uncommitted", "down", "uncommitted changes", "down", "10m ago · wip"),
                repo("findmypy", "main", "↑1", "down", "1 undeployed commit", "down", "1d ago · add device"),
            ])),
            ("rowCount", .int(3)),
        ])
    }
}

/// Markdown browser over Google Drive /Private — passes the sidecar's `/docs?path=`
/// JSON through as `docs`. The viewed path comes from the request (config.docsPath),
/// so the cache key varies by folder/file.
public struct DocsProvider: DataProvider {
    public let key = "docs"
    public let ttl: TimeInterval = 30
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String { "docs:\(config.docsPath ?? "")" }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let raw = config.docsPath ?? ""
        // .urlQueryAllowed leaves &, +, =, ? unescaped, which would corrupt names with
        // those characters when the sidecar parses the query — use a strict value set.
        let encoded = raw.addingPercentEncoding(withAllowedCharacters: .sidecarQueryValue) ?? ""
        let value = try await client.getJSON("\(config.bqSidecarURL)/docs?path=\(encoded)", as: JSONValue.self)
        return try value.requireOK("docs")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func entry(_ name: String, _ kind: String, _ icon: String, _ meta: String, _ href: String) -> JSONValue {
            .obj([("name", .string(name)), ("kind", .string(kind)), ("icon", .string(icon)),
                  ("metaFormatted", .string(meta)), ("navHref", .string(href))])
        }
        return .obj([
            ("title", .string("Docs")),
            ("subtitleFormatted", .string("/Private · sidecar unavailable")),
            ("kind", .string("dir")),
            ("markdown", .string("")),
            ("entries", .array([
                entry("Home", "folder", "folder.fill", "4 items", "/screen/docs?path=Home"),
                entry("Notes.md", "file", "doc.text.fill", "2h ago", "/screen/docs?path=Notes.md"),
            ])),
            ("rowCount", .int(2)),
        ])
    }
}

/// Recent iMessage/SMS (the Mac's chat.db) + latest Gmail headers — passes the sidecar's
/// `/messages` JSON through as `messages`. Each source degrades independently server-side.
public struct MessagesProvider: DataProvider {
    public let key = "messages"
    public let ttl: TimeInterval = 60
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/messages", as: JSONValue.self)
        return try value.requireOK("messages")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Messages")),
            ("subtitleFormatted", .string("sidecar unavailable")),
            ("sources", .array([])),
            ("groups", .array([])),
            ("emptyFormatted", .string("Connect on Tailscale to load Messages.")),
        ])
    }
}

/// Schedrunner job-status summary — passes the sidecar's `/schedrunner` JSON through
/// as `schedrunner`. Short TTL so a job's last/next run stays current.
public struct SchedrunnerProvider: DataProvider {
    public let key = "schedrunner"
    public let ttl: TimeInterval = 30
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/schedrunner", as: JSONValue.self)
        return try value.requireOK("schedrunner")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func job(_ name: String, _ status: String, _ dir: String, _ last: String, _ next: String, _ meta: String) -> JSONValue {
            .obj([("name", .string(name)), ("statusFormatted", .string(status)), ("direction", .string(dir)),
                  ("lastFormatted", .string(last)), ("nextFormatted", .string(next)), ("metaFormatted", .string(meta))])
        }
        return .obj([
            ("title", .string("Schedrunner")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("statusFormatted", .string("1 failing")),
            ("statusDirection", .string("down")),
            ("countsFormatted", .string("3 jobs · 2 ok · 1 failed")),
            ("jobs", .array([
                job("balances-push", "failed", "down", "ran 20m ago", "next in 39m", "*/30 * * * * · BigQuery 403"),
                job("afm-refresh", "ok", "up", "ran 5m ago", "", "5m"),
                job("nightly-sync", "ok", "up", "ran 2h ago", "next in 21h", "0 3 * * * · 12.4s"),
            ])),
            ("logLabel", .string("")),
            ("logTail", .string("")),
            ("rowCount", .int(3)),
        ])
    }
}

/// Per-script summary of schedrunner's log files — passes the sidecar's `/schedlogs`
/// JSON through as `schedlogs`. Short TTL so a script's last run stays current.
public struct SchedLogsProvider: DataProvider {
    public let key = "schedlogs"
    public let ttl: TimeInterval = 30
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/schedlogs", as: JSONValue.self)
        return try value.requireOK("schedlogs")
    }

    public func stub(config: AppConfig) -> JSONValue {
        func entry(_ name: String, _ meta: String, _ status: String, _ color: String, _ snippet: String) -> JSONValue {
            .obj([("name", .string(name)), ("metaFormatted", .string(meta)),
                  ("statusFormatted", .string(status)), ("statusColor", .string(color)),
                  ("snippetFormatted", .string(snippet))])
        }
        return .obj([
            ("title", .string("Sched Logs")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("entries", .array([
                entry("backup", "18m ago · 92s", "OK", "up", "syncing files...\ndone: 412 files"),
                entry("sync", "2h ago · 5s", "FAILED", "down", "ERROR: timeout\nFAILED (exit 1)"),
            ])),
            ("rowCount", .int(2)),
        ])
    }
}
