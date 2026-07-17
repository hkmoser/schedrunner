import Foundation
import EventKit

/// Reads today's events from the on-device calendar (EventKit) and exposes them
/// under the reserved `local.calendar.*` binding namespace. This data NEVER leaves
/// the device — it's injected into the render scope locally, not uploaded.
struct CalendarProvider {
    private let store = EKEventStore()

    func load() async -> JSONValue {
        let granted = await requestAccess()
        guard granted else {
            return .object(["summary": .string("Calendar access not granted")])
        }

        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        guard let end = cal.date(byAdding: .day, value: 1, to: start) else {
            return .object(["summary": .string("No events")])
        }
        let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
        let events = store.events(matching: predicate).sorted { $0.startDate < $1.startDate }

        let allDay = events.filter { $0.isAllDay }
        let timed = events.filter { !$0.isAllDay && $0.startDate >= Date() }

        let df = DateFormatter()
        df.timeStyle = .short
        df.dateStyle = .none

        var parts: [String] = []
        if !allDay.isEmpty { parts.append("\(allDay.count) all-day") }
        if let next = timed.first {
            parts.append("Next: \(next.title ?? "event") at \(df.string(from: next.startDate))")
        }
        let summary = parts.isEmpty ? "No events today" : parts.joined(separator: " · ")

        let allDayTitles = allDay.map { JSONValue.string($0.title ?? "event") }
        var nextTimed: JSONValue = .null
        if let next = timed.first {
            nextTimed = .object([
                "title": .string(next.title ?? "event"),
                "timeFormatted": .string(df.string(from: next.startDate)),
            ])
        }

        return .object([
            "summary": .string(summary),
            "allDayToday": .array(allDayTitles),
            "nextTimed": nextTimed,
        ])
    }

    private func requestAccess() async -> Bool {
        await withCheckedContinuation { continuation in
            store.requestFullAccessToEvents { granted, _ in
                continuation.resume(returning: granted)
            }
        }
    }
}
