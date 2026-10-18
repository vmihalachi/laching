import Foundation
import LachingCompanion
import LachingCore

/// `cadence/*.json`: a frequency preset plus the timing settings → the `SchedulerConfig` `ChatterCadence` builds, and
/// the minutes at which a scheduler seeded with `seed` fires when ticked once a minute from `start` with nobody busy
/// or away. Pins the preset table and the jitter wiring; no app data.
public enum CadenceVector {
    public static let directory = "cadence"

    public struct Settings: Codable, Equatable, Sendable {
        public var frequency: String
        public var activeStartMinute: Int
        public var activeEndMinute: Int
        public var activeWeekdays: [Int]
        public var quietHours: [Int]
        public var hushInFullScreen: Bool

        public init(
            frequency: String, activeStartMinute: Int, activeEndMinute: Int, activeWeekdays: [Int], quietHours: [Int],
            hushInFullScreen: Bool
        ) {
            self.frequency = frequency
            self.activeStartMinute = activeStartMinute
            self.activeEndMinute = activeEndMinute
            self.activeWeekdays = activeWeekdays
            self.quietHours = quietHours
            self.hushInFullScreen = hushInFullScreen
        }
    }

    public struct Config: Codable, Equatable, Sendable {
        public var intervalSeconds: Int
        public var intervalJitterSeconds: Int
        public var activeStartMinute: Int
        public var activeEndMinute: Int
        public var activeWeekdays: [Int]
        public var quietHours: [Int]
        public var idleThresholdSeconds: Int
        public var postponeDuringCalls: Bool
        public var postponeDuringFullScreen: Bool
        public var busyGraceSeconds: Int
        public var afterCallFallbackSeconds: Int

        public init(_ c: SchedulerConfig) {
            intervalSeconds = Int(c.interval)
            intervalJitterSeconds = Int(c.intervalJitter)
            activeStartMinute = c.activeStartMinute
            activeEndMinute = c.activeEndMinute
            activeWeekdays = c.activeWeekdays.sorted()
            quietHours = c.quietHours.sorted()
            idleThresholdSeconds = Int(c.idleThreshold)
            postponeDuringCalls = c.postponeDuringCalls
            postponeDuringFullScreen = c.postponeDuringFullScreen
            busyGraceSeconds = Int(c.busyGrace)
            afterCallFallbackSeconds = Int(c.afterCallFallback)
        }
    }

    public struct Vector: Codable, Equatable, Sendable {
        public var name: String
        public var settings: Settings
        public var config: Config
        public var timeZone: String
        public var start: String
        public var seed: UInt64
        public var minutes: Int
        /// Minutes after `start` at which `tick()` returned `.remind`.
        public var fireMinutes: [Int]
    }

    public struct Case: Sendable {
        public var name: String
        public var settings: Settings
        public var timeZone: String
        /// Local date components: year, month, day, hour, minute.
        public var start: (Int, Int, Int, Int, Int)
        public var seed: UInt64
        public var minutes: Int

        public init(
            name: String, settings: Settings, timeZone: String = "UTC", start: (Int, Int, Int, Int, Int), seed: UInt64 = 42,
            minutes: Int = 240
        ) {
            self.name = name
            self.settings = settings
            self.timeZone = timeZone
            self.start = start
            self.seed = seed
            self.minutes = minutes
        }
    }

    static func config(_ settings: Settings) throws -> SchedulerConfig {
        guard let frequency = Frequency(rawValue: settings.frequency) else {
            throw ConformanceSupport.VectorError("unknown frequency \(settings.frequency)")
        }
        return ChatterCadence.config(
            frequency: frequency, activeStartMinute: settings.activeStartMinute, activeEndMinute: settings.activeEndMinute,
            activeWeekdays: Set(settings.activeWeekdays), quietHours: Set(settings.quietHours),
            hushInFullScreen: settings.hushInFullScreen)
    }

    @MainActor
    public static func vector(_ c: Case) throws -> Vector {
        let calendar = try ConformanceSupport.calendar(c.timeZone)
        let start = ConformanceSupport.local(calendar, c.start.0, c.start.1, c.start.2, c.start.3, c.start.4)
        let config = try config(c.settings)
        return Vector(
            name: c.name, settings: c.settings, config: Config(config), timeZone: c.timeZone,
            start: ConformanceSupport.string(start, in: calendar.timeZone), seed: c.seed, minutes: c.minutes,
            fireMinutes: fires(config: config, start: start, calendar: calendar, seed: c.seed, minutes: c.minutes))
    }

    @MainActor
    static func fires(config: SchedulerConfig, start: Date, calendar: Calendar, seed: UInt64, minutes: Int) -> [Int] {
        let clock = MockClock(start)
        let scheduler = ReminderScheduler(
            config: config, clock: clock, idle: MockIdle(), busy: MockBusy(), calendar: calendar,
            random: SplitMix64(seed: seed))
        var fires: [Int] = []
        for minute in 1...minutes {
            clock.advance(minutes: 1)
            if scheduler.tick() == .remind { fires.append(minute) }
        }
        return fires
    }

    @MainActor
    public static func files(cases: [Case]) throws -> [String: Data] {
        try ConformanceSupport.files(in: directory, cases, name: \.name) { try vector($0) }
    }

    @MainActor
    public static func verify(_ data: Data, file: String) throws -> [String] {
        let vector = try ConformanceSupport.decode(Vector.self, from: data)
        var problems: [String] = []
        let config = try config(vector.settings)
        if Config(config) != vector.config { problems.append("\(file): config differs") }
        let calendar = try ConformanceSupport.calendar(vector.timeZone)
        let fires = fires(
            config: config, start: try ConformanceSupport.date(vector.start), calendar: calendar, seed: vector.seed,
            minutes: vector.minutes)
        if fires != vector.fireMinutes { problems.append("\(file): fires \(fires) != \(vector.fireMinutes)") }
        return problems
    }
}
