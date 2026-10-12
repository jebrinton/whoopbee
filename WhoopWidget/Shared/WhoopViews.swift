import SwiftUI

// Palette: dataviz reference palette, light/dark steps. Text uses system ink; marks carry the hue.
struct Palette {
    let blue, aqua, orange, ink2, muted, grid, axis, surface: Color
    static func forScheme(_ scheme: ColorScheme) -> Palette {
        scheme == .dark
            ? Palette(blue: Color(hex: 0x3987e5), aqua: Color(hex: 0x199e70), orange: Color(hex: 0xd95926),
                      ink2: Color(hex: 0xc3c2b7), muted: Color(hex: 0x898781), grid: Color(hex: 0x2c2c2a),
                      axis: Color(hex: 0x383835), surface: Color(hex: 0x1a1a19))
            : Palette(blue: Color(hex: 0x2a78d6), aqua: Color(hex: 0x1baf7a), orange: Color(hex: 0xeb6834),
                      ink2: Color(hex: 0x52514e), muted: Color(hex: 0x898781), grid: Color(hex: 0xe1e0d9),
                      axis: Color(hex: 0xc3c2b7), surface: Color(hex: 0xfcfcfb))
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255)
    }
}

// MARK: - Sleep timeline (one column per night, asleep -> awake, 20:00 at the top)

struct TimelineChart: View {
    let dash: Dash
    var nights: Int = 14
    var showLabels = true
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let p = Palette.forScheme(scheme)
        Canvas { ctx, size in
            let L: CGFloat = showLabels ? 22 : 4, R = size.width - 4
            let T: CGFloat = 8, B = size.height - (showLabels ? 14 : 4)
            let lo = 2.0 * 60, hi = 17.0 * 60   // 20:00 ... 11:00 as minutes after 18:00
            func y(_ m18: Double) -> CGFloat { T + CGFloat((min(max(m18, lo), hi) - lo) / (hi - lo)) * (B - T) }
            // hour gridlines
            for hr in stride(from: 20, through: 34, by: 2) {
                let yy = y(Double(hr * 60 - 18 * 60))
                ctx.fill(Path(CGRect(x: L, y: yy - 0.5, width: R - L, height: 1)), with: .color(p.grid))
                if showLabels {
                    ctx.draw(Text(String(format: "%02d", hr % 24)).font(.system(size: 8)).foregroundColor(p.muted),
                             at: CGPoint(x: L - 4, y: yy), anchor: .trailing)
                }
            }
            // target line
            let ty = y(Double(dash.targetM18))
            ctx.fill(Path(CGRect(x: L, y: ty - 0.75, width: R - L, height: 1.5)), with: .color(p.ink2.opacity(0.9)))
            let slots = dash.slots(nights)
            let slotW = (R - L) / CGFloat(max(1, slots.count))
            let barW = min(14, slotW * 0.55)
            for (i, slot) in slots.enumerated() {
                let cx = L + slotW * (CGFloat(i) + 0.5)
                if showLabels {
                    let dayNum = Calendar.current.component(.day, from: slot.day)
                    ctx.draw(Text(String(format: "%02d", dayNum)).font(.system(size: 8)).foregroundColor(p.muted),
                             at: CGPoint(x: cx, y: B + 7), anchor: .center)
                }
                guard let n = slot.night else { continue }
                var wk = Double(n.wakeM18)
                if wk < Double(n.asleepM18) { wk += 1440 }
                let ya = y(Double(n.asleepM18)), yw = max(y(wk), ya + 3)
                let rect = CGRect(x: cx - barW / 2, y: ya, width: barW, height: yw - ya)
                ctx.fill(Path(roundedRect: rect, cornerRadius: 3), with: .color(p.blue))
                if !n.tags.isEmpty {
                    ctx.fill(Path(ellipseIn: CGRect(x: cx - 2.5, y: T - 7, width: 5, height: 5)), with: .color(p.ink2))
                }
            }
        }
    }
}

// MARK: - Sparkline (fall-asleep time vs target; later = lower; hollow = something logged)

struct SparklineChart: View {
    let dash: Dash
    var nights: Int = 14
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let p = Palette.forScheme(scheme)
        Canvas { ctx, size in
            let lo = -60.0, hi = 180.0
            let top: CGFloat = 3, bot = size.height - 3
            func y(_ off: Double) -> CGFloat { top + CGFloat((min(max(off, lo), hi) - lo) / (hi - lo)) * (bot - top) }
            let y0 = y(0), yg = y(Double(dash.graceMin))
            ctx.fill(Path(CGRect(x: 1, y: y0, width: size.width - 2, height: yg - y0)), with: .color(p.ink2.opacity(0.14)))
            ctx.fill(Path(CGRect(x: 1, y: y0 - 0.5, width: size.width - 2, height: 1)), with: .color(p.ink2.opacity(0.6)))
            let slots = dash.slots(nights)
            let step = (size.width - 8) / CGFloat(max(1, slots.count - 1))
            for (i, slot) in slots.enumerated() {
                guard let n = slot.night else { continue }
                let x = 4 + CGFloat(i) * step, yy = y(Double(n.offsetMin))
                let last = i == slots.count - 1
                let r: CGFloat = last ? 3.2 : 2.5
                let rect = CGRect(x: x - r, y: yy - r, width: 2 * r, height: 2 * r)
                if n.tags.isEmpty {
                    ctx.fill(Path(ellipseIn: rect), with: .color(p.blue))
                } else {
                    ctx.stroke(Path(ellipseIn: rect.insetBy(dx: 0.8, dy: 0.8)), with: .color(p.blue), lineWidth: 1.6)
                }
            }
        }
    }
}

// MARK: - Band chart (7-night mean inside the 10th-90th percentile band of the previous 60 nights)

struct BandChart: View {
    let block: BandBlock
    let hue: KeyPath<Palette, Color>
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let p = Palette.forScheme(scheme)
        let color = p[keyPath: hue]
        Canvas { ctx, size in
            let rows = block.series
            guard rows.count >= 2 else { return }
            var vals: [Double] = []
            for r in rows {
                if let a = r.avg7 { vals.append(a) }
                if let l = r.lo { vals.append(l) }
                if let h = r.hi { vals.append(h) }
            }
            guard let vmin0 = vals.min(), let vmax0 = vals.max() else { return }
            let pad = max((vmax0 - vmin0) * 0.15, 0.5)
            let vmin = vmin0 - pad, vmax = vmax0 + pad
            let L: CGFloat = 2, R = size.width - 2, T: CGFloat = 3, B = size.height - 3
            guard let d0 = Dash.day(rows.first!.night), let d1 = Dash.day(rows.last!.night) else { return }
            let span = max(1.0, d1.timeIntervalSince(d0))
            func x(_ night: String) -> CGFloat {
                guard let d = Dash.day(night) else { return L }
                return L + CGFloat(d.timeIntervalSince(d0) / span) * (R - L)
            }
            func y(_ v: Double) -> CGFloat { B - CGFloat((v - vmin) / (vmax - vmin)) * (B - T) }
            // band
            let bandRows = rows.filter { $0.lo != nil && $0.hi != nil }
            if bandRows.count >= 2 {
                var path = Path()
                path.move(to: CGPoint(x: x(bandRows[0].night), y: y(bandRows[0].hi!)))
                for r in bandRows.dropFirst() { path.addLine(to: CGPoint(x: x(r.night), y: y(r.hi!))) }
                for r in bandRows.reversed() { path.addLine(to: CGPoint(x: x(r.night), y: y(r.lo!))) }
                path.closeSubpath()
                ctx.fill(path, with: .color(color.opacity(0.16)))
            }
            // 7-night mean
            let pts = rows.compactMap { r -> CGPoint? in
                guard let a = r.avg7 else { return nil }
                return CGPoint(x: x(r.night), y: y(a))
            }
            if pts.count >= 2 {
                var line = Path()
                line.move(to: pts[0])
                for pt in pts.dropFirst() { line.addLine(to: pt) }
                ctx.stroke(line, with: .color(color), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                let end = pts[pts.count - 1]
                ctx.fill(Path(ellipseIn: CGRect(x: end.x - 3.6, y: end.y - 3.6, width: 7.2, height: 7.2)), with: .color(p.surface))
                ctx.fill(Path(ellipseIn: CGRect(x: end.x - 2.6, y: end.y - 2.6, width: 5.2, height: 5.2)), with: .color(color))
            }
            ctx.fill(Path(CGRect(x: L, y: B, width: R - L, height: 0.6)), with: .color(p.axis))
        }
    }
}

// MARK: - Text helpers

extension BandBlock {
    func line(_ name: String, unit: String, digits: Int) -> String {
        let f = "%.\(digits)f"
        switch status {
        case "none": return "\(name): no data yet"
        case "collecting": return "\(name): collecting baseline (\(nBaseline ?? 0)/14 nights)"
        case "in":
            if let lo = lo, let hi = hi { return "\(name) 7-night avg in your range (\(String(format: f, lo))–\(String(format: f, hi)) \(unit))" }
            return "\(name): in your range"
        default:
            let word = status == "below" ? "below" : "above"
            if let a = avg7, let lo = lo, let hi = hi {
                return "\(name) 7-night avg \(String(format: f, a)) \(unit), \(word) your range \(String(format: f, lo))–\(String(format: f, hi))"
            }
            return "\(name): \(word) your range"
        }
    }
    var inRange: Bool { status == "in" }
}

struct StatusDot: View {
    let block: BandBlock
    let hue: KeyPath<Palette, Color>
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        let p = Palette.forScheme(scheme)
        Circle().fill(block.status == "in" || block.status == "collecting" || block.status == "none"
                      ? p[keyPath: hue] : p.ink2)
            .frame(width: 7, height: 7)
    }
}

// MARK: - Layouts

struct HeadlineBlock: View {
    let dash: Dash
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Asleep by \(dash.graceClock)").font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("\(dash.regularity.hits10)").font(.system(size: 30, weight: .semibold))
                Text("of \(dash.regularity.n10) nights").font(.caption).foregroundStyle(.secondary)
            }
            if let sri = dash.regularity.sri14 {
                Text("Regularity \(Int(sri.rounded())) · median \(dash.regularity.medianAsleep7 ?? "–")")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
    }
}

struct SmallDashboard: View {
    let dash: Dash
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HeadlineBlock(dash: dash)
            SparklineChart(dash: dash).frame(height: 34)
            Spacer(minLength: 0)
        }
    }
}

struct MediumDashboard: View {
    let dash: Dash
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HeadlineBlock(dash: dash)
                Spacer(minLength: 2)
                if dash.scopes["recovery"] == true {
                    HStack(spacing: 5) { StatusDot(block: dash.hrv, hue: \.aqua); Text("HRV " + shortStatus(dash.hrv)) }
                        .font(.caption2).foregroundStyle(.secondary)
                    HStack(spacing: 5) { StatusDot(block: dash.rhr, hue: \.orange); Text("RHR " + shortStatus(dash.rhr)) }
                        .font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text("HRV/RHR: add read:recovery").font(.caption2).foregroundStyle(.secondary)
                }
                if let last = dash.nights.last {
                    Text("Last: \(Dash.clock(last.asleep)) → \(Dash.clock(last.wake))").font(.caption2).foregroundStyle(.secondary)
                }
            }
            .frame(width: 118, alignment: .leading)
            TimelineChart(dash: dash)
        }
    }

    func shortStatus(_ b: BandBlock) -> String {
        switch b.status {
        case "in": return "in range"
        case "collecting": return "baseline \(b.nBaseline ?? 0)/14"
        case "none": return "no data"
        default: return "\(b.status) range" + (b.avg7.map { String(format: " (%.0f)", $0) } ?? "")
        }
    }
}

struct LargeDashboard: View {
    let dash: Dash
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                HeadlineBlock(dash: dash)
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    if let last = dash.nights.last {
                        Text("Last night \(Dash.clock(last.asleep)) → \(Dash.clock(last.wake))").font(.caption2).foregroundStyle(.secondary)
                        Text(Dash.duration(last.tstMin)).font(.caption2).foregroundStyle(.secondary)
                    }
                    Text("line = \(dash.target) · dot = logged").font(.caption2).foregroundStyle(.tertiary)
                }
            }
            TimelineChart(dash: dash).frame(maxHeight: .infinity)
            if dash.scopes["recovery"] == true {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 5) { StatusDot(block: dash.hrv, hue: \.aqua); Text(dash.hrv.line("HRV", unit: "ms", digits: 0)) }
                            .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        BandChart(block: dash.hrv, hue: \.aqua).frame(height: 44)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 5) { StatusDot(block: dash.rhr, hue: \.orange); Text(dash.rhr.line("Resting HR", unit: "bpm", digits: 0)) }
                            .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        BandChart(block: dash.rhr, hue: \.orange).frame(height: 44)
                    }
                }
            } else {
                Text("HRV and resting HR appear once the WHOOP app has the read:recovery scope.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
    }
}

struct DashboardView: View {
    let dash: Dash?
    let error: String?
    var family: WidgetFamilyKind = .medium

    enum WidgetFamilyKind { case small, medium, large }

    var body: some View {
        Group {
            if let d = dash {
                switch family {
                case .small: SmallDashboard(dash: d)
                case .medium: MediumDashboard(dash: d)
                case .large: LargeDashboard(dash: d)
                }
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Text("WHOOP").font(.headline)
                    Text(error ?? "Waiting for ~/code/whoopbee/data/dash.json").font(.caption).foregroundStyle(.secondary)
                    Text("Run: python3 ~/code/whoopbee/whoop_dash.py fetch").font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
    }
}
