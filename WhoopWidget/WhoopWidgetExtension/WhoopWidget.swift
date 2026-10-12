import WidgetKit
import SwiftUI

struct WhoopEntry: TimelineEntry {
    let date: Date
    let dash: Dash?
    let error: String?
    static var placeholder: WhoopEntry { WhoopEntry(date: Date(), dash: Dash.sample, error: nil) }
}

struct WhoopProvider: TimelineProvider {
    func placeholder(in context: Context) -> WhoopEntry { .placeholder }

    func getSnapshot(in context: Context, completion: @escaping (WhoopEntry) -> Void) {
        if context.isPreview { completion(.placeholder); return }
        completion(load())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<WhoopEntry>) -> Void) {
        // dash.json changes once a day (after the night lands) plus on each logged tag; re-read every 15 minutes.
        completion(Timeline(entries: [load()], policy: .after(Date().addingTimeInterval(15 * 60))))
    }

    private func load() -> WhoopEntry {
        do {
            return WhoopEntry(date: Date(), dash: try Dash.load(), error: nil)
        } catch {
            return WhoopEntry(date: Date(), dash: nil, error: error.localizedDescription)
        }
    }
}

struct WhoopWidgetView: View {
    @Environment(\.widgetFamily) var family
    let entry: WhoopEntry

    var body: some View {
        DashboardView(dash: entry.dash, error: entry.error, family: kind)
            .containerBackground(.fill.tertiary, for: .widget)
    }

    var kind: DashboardView.WidgetFamilyKind {
        switch family {
        case .systemSmall: return .small
        case .systemLarge, .systemExtraLarge: return .large
        default: return .medium
        }
    }
}

struct WhoopWidget: Widget {
    let kind = "WhoopWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: WhoopProvider()) { entry in
            WhoopWidgetView(entry: entry)
        }
        .configurationDisplayName("WHOOP · bedtime regularity")
        .description("Nights asleep by your target, the last 14 nights as a sleep timeline, and HRV / resting HR as 7-night averages inside your own 60-night range.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

#Preview("Medium", as: .systemMedium) {
    WhoopWidget()
} timeline: {
    WhoopEntry.placeholder
}

#Preview("Large", as: .systemLarge) {
    WhoopWidget()
} timeline: {
    WhoopEntry.placeholder
}
