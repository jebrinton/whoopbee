import SwiftUI
import WidgetKit

/// Host app: exists so macOS registers the widget extension. Shows the same dashboard and a reload button.
struct ContentView: View {
    @State private var dash: Dash? = nil
    @State private var error: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            DashboardView(dash: dash, error: error, family: .large)
                .padding(16)
                .background(RoundedRectangle(cornerRadius: 16).fill(.quaternary))
            HStack {
                Button("Reload") { load(); WidgetCenter.shared.reloadAllTimelines() }
                Spacer()
                Text("Add the widget: right-click the desktop → Edit Widgets → search “WHOOP”.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("Data: ~/code/whoopbee/data/dash.json, refreshed hourly by whoop_dash.py. Log behaviors from the SwiftBar menu.")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(20)
        .frame(minWidth: 520, minHeight: 400)
        .onAppear(perform: load)
    }

    private func load() {
        do { dash = try Dash.load(); error = nil } catch { dash = nil; self.error = error.localizedDescription }
    }
}
