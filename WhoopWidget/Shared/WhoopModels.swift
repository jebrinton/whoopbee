import Foundation
import Darwin

// Mirrors ~/code/whoopbee/data/dash.json written by whoop_dash.py. Decoded with .convertFromSnakeCase.

enum DashLocation {
    static var home: URL {
        // Sandboxed widget extensions see a container as $HOME; ask passwd for the real one.
        if let passwd = getpwuid(getuid()) {
            return URL(fileURLWithPath: String(cString: passwd.pointee.pw_dir), isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser
    }
    static var url: URL { home.appendingPathComponent("code/whoopbee/data/dash.json") }
}

struct Night: Codable, Identifiable {
    var id: String { night }
    let night: String          // evening date, "2026-09-30"
    let asleep: String         // local "2026-10-01T00:14"
    let wake: String
    let offsetMin: Int         // minutes after the target (negative = before)
    let asleepM18: Int         // minutes after 18:00
    let wakeM18: Int
    let tstMin: Int?
    let inBedMin: Int?
    let hrv: Double?
    let rhr: Double?
    let tags: [String]
    let autoTags: [String]?
}

struct Regularity: Codable {
    let sri14: Double?
    let sriPairs: Int?
    let hits10: Int
    let n10: Int
    let medianAsleep7: String?
    let asleepIqr7Min: Int?
    let avgTst7Min: Int?
}

struct BandRow: Codable, Identifiable {
    var id: String { night }
    let night: String
    let v: Double?
    let avg7: Double?
    let lo: Double?
    let hi: Double?
    let med: Double?
}

struct BandBlock: Codable {
    let status: String          // in | below | above | collecting | none
    let avg7: Double?
    let lo: Double?
    let hi: Double?
    let med: Double?
    let nBaseline: Int?
    let lastNight: String?
    let series: [BandRow]
}

struct TagInfo: Codable {
    let key: String
    let label: String
    let symbol: String
}

struct Dash: Codable {
    let generatedAt: String
    let fetchedAt: String?
    let target: String
    let graceMin: Int
    let nightsShown: Int
    let scopes: [String: Bool]
    let tagsCatalog: [TagInfo]
    let logNight: String
    let nights: [Night]
    let regularity: Regularity
    let hrv: BandBlock
    let rhr: BandBlock

    static func load(from url: URL = DashLocation.url) throws -> Dash {
        let data = try Data(contentsOf: url)
        return try Dash.decode(data)
    }

    static func decode(_ data: Data) throws -> Dash {
        let dec = JSONDecoder()
        dec.keyDecodingStrategy = .convertFromSnakeCase
        return try dec.decode(Dash.self, from: data)
    }

    /// Target + grace as "23:30"
    var graceClock: String {
        let parts = target.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2 else { return target }
        let total = (parts[0] * 60 + parts[1] + graceMin) % 1440
        return String(format: "%02d:%02d", total / 60, total % 60)
    }

    var targetM18: Int {
        let parts = target.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2 else { return 5 * 60 }
        var m = parts[0] * 60 + parts[1] - 18 * 60
        if m < 0 { m += 1440 }
        return m
    }

    /// Fixed calendar slots ending at the latest night, so gaps stay visible.
    func slots(_ n: Int) -> [(day: Date, night: Night?)] {
        guard let lastStr = nights.last?.night, let last = Dash.day(lastStr) else { return [] }
        var by: [String: Night] = [:]
        for x in nights { by[x.night] = x }
        return (0..<n).map { i in
            let d = Calendar.current.date(byAdding: .day, value: -(n - 1 - i), to: last)!
            return (d, by[Dash.dayString(d)])
        }
    }

    static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func day(_ s: String) -> Date? { dayFormatter.date(from: s) }
    static func dayString(_ d: Date) -> String { dayFormatter.string(from: d) }

    static func clock(_ local: String) -> String {
        // "2026-10-01T00:14" -> "00:14"
        guard let t = local.split(separator: "T").last else { return local }
        return String(t.prefix(5))
    }

    static func duration(_ mins: Int?) -> String {
        guard let m = mins else { return "–" }
        return "\(m / 60) h \(String(format: "%02d", m % 60)) min"
    }

    static var sample: Dash {
        let json = """
        {"generated_at":"2026-10-01T15:30:00","fetched_at":"2026-10-01T15:00:00","target":"23:00","grace_min":30,
         "nights_shown":14,"scopes":{"sleep":true,"recovery":true,"workout":true},
         "tags_catalog":[{"key":"alcohol","label":"Alcohol","symbol":"wineglass"}],"log_night":"2026-10-01",
         "nights":[
          {"night":"2026-09-17","asleep":"2026-09-18T01:10","wake":"2026-09-18T08:05","offset_min":130,"asleep_m18":430,"wake_m18":845,"tst_min":390,"in_bed_min":415,"hrv":58,"rhr":55,"tags":[]},
          {"night":"2026-09-19","asleep":"2026-09-20T02:50","wake":"2026-09-20T10:10","offset_min":230,"asleep_m18":530,"wake_m18":970,"tst_min":410,"in_bed_min":440,"hrv":52,"rhr":58,"tags":[]},
          {"night":"2026-09-20","asleep":"2026-09-20T23:20","wake":"2026-09-21T06:25","offset_min":20,"asleep_m18":320,"wake_m18":745,"tst_min":398,"in_bed_min":425,"hrv":66,"rhr":53,"tags":[]},
          {"night":"2026-09-21","asleep":"2026-09-21T23:40","wake":"2026-09-22T05:30","offset_min":40,"asleep_m18":340,"wake_m18":690,"tst_min":330,"in_bed_min":350,"hrv":63,"rhr":54,"tags":[]},
          {"night":"2026-09-22","asleep":"2026-09-22T23:35","wake":"2026-09-23T06:00","offset_min":35,"asleep_m18":335,"wake_m18":720,"tst_min":360,"in_bed_min":385,"hrv":64,"rhr":54,"tags":[]},
          {"night":"2026-09-23","asleep":"2026-09-23T22:30","wake":"2026-09-24T05:20","offset_min":-30,"asleep_m18":270,"wake_m18":680,"tst_min":385,"in_bed_min":410,"hrv":70,"rhr":52,"tags":[]},
          {"night":"2026-09-24","asleep":"2026-09-24T23:45","wake":"2026-09-25T06:10","offset_min":45,"asleep_m18":345,"wake_m18":730,"tst_min":360,"in_bed_min":385,"hrv":61,"rhr":55,"tags":[]},
          {"night":"2026-09-26","asleep":"2026-09-27T00:30","wake":"2026-09-27T08:00","offset_min":90,"asleep_m18":390,"wake_m18":840,"tst_min":420,"in_bed_min":450,"hrv":57,"rhr":57,"tags":[]},
          {"night":"2026-09-27","asleep":"2026-09-27T23:35","wake":"2026-09-28T06:20","offset_min":35,"asleep_m18":335,"wake_m18":740,"tst_min":380,"in_bed_min":405,"hrv":55,"rhr":58,"tags":["alcohol"]},
          {"night":"2026-09-28","asleep":"2026-09-28T23:05","wake":"2026-09-29T06:00","offset_min":5,"asleep_m18":305,"wake_m18":720,"tst_min":395,"in_bed_min":415,"hrv":68,"rhr":53,"tags":[]},
          {"night":"2026-09-29","asleep":"2026-09-29T23:40","wake":"2026-09-30T06:10","offset_min":40,"asleep_m18":340,"wake_m18":730,"tst_min":365,"in_bed_min":390,"hrv":62,"rhr":55,"tags":["caffeine","late_meal"]},
          {"night":"2026-09-30","asleep":"2026-10-01T00:14","wake":"2026-10-01T07:55","offset_min":74,"asleep_m18":374,"wake_m18":835,"tst_min":424,"in_bed_min":461,"hrv":61.6,"rhr":57,"tags":[]}],
         "regularity":{"sri_14":82.4,"sri_pairs":10,"hits_10":5,"n_10":10,"median_asleep_7":"23:29","asleep_iqr_7_min":48,"avg_tst_7_min":393},
         "hrv":{"status":"in","avg7":63.0,"lo":46.8,"hi":70.2,"med":58.1,"n_baseline":55,"last_night":"2026-09-30","series":[
           {"night":"2026-09-17","v":58,"avg7":59,"lo":47,"hi":70},{"night":"2026-09-20","v":66,"avg7":60,"lo":47,"hi":70},
           {"night":"2026-09-23","v":70,"avg7":63,"lo":47,"hi":70},{"night":"2026-09-26","v":57,"avg7":62,"lo":47,"hi":70},
           {"night":"2026-09-30","v":61.6,"avg7":63,"lo":46.8,"hi":70.2}]},
         "rhr":{"status":"in","avg7":55.0,"lo":53.0,"hi":59.6,"med":56,"n_baseline":55,"last_night":"2026-09-30","series":[
           {"night":"2026-09-17","v":55,"avg7":56,"lo":53,"hi":60},{"night":"2026-09-20","v":53,"avg7":55.5,"lo":53,"hi":60},
           {"night":"2026-09-23","v":52,"avg7":54.5,"lo":53,"hi":60},{"night":"2026-09-26","v":57,"avg7":55,"lo":53,"hi":60},
           {"night":"2026-09-30","v":57,"avg7":55,"lo":53,"hi":59.6}]}}
        """
        // The sample is a compile-time constant; a decode failure here is a programming error.
        return try! Dash.decode(Data(json.utf8))
    }
}
