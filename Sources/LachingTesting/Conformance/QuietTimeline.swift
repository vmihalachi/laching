import Foundation
import LachingCompanion
import LachingCore

/// `quiet/*.json`: a timeline driven through `CompanionCoordinator` (ticks, pokes, Shh, wake, events) with a snapshot
/// per step, and what came out of each: the deliveries (phrase id, trigger, channel plan), the mood and whether the
/// character is shushed. Pins `QuietRules`, `CompanionState`, the cadence wiring and the seeded picks together, the
/// way the apps use them. Runs over the app's bank and defaults; the settings a case changes are in the file.
public enum QuietTimeline {
    public static let directory = "quiet"

    /// The settings a timeline can set; everything else comes from the app's `CompanionDefaults`.
    public struct Settings: Codable, Equatable, Sendable {
        public var channels: [String]
        public var frequency: String
        public var activeStartMinute: Int
        public var activeEndMinute: Int
        public var activeWeekdays: [Int]
        public var quietHours: [Int]
        public var hushInFullScreen: Bool
        public var greetOnReturn: Bool
        public var commentOnPower: Bool
        public var commentOnAppearance: Bool
        public var shushedUntil: String?

        public init(_ d: CompanionDefaults) {
            channels = d.channels.names
            frequency = d.frequency.rawValue
            activeStartMinute = d.activeStartMinute
            activeEndMinute = d.activeEndMinute
            activeWeekdays = d.activeWeekdays.sorted()
            quietHours = d.quietHours.sorted()
            hushInFullScreen = d.hushInFullScreen
            greetOnReturn = d.greetOnReturn
            commentOnPower = d.commentOnPower
            commentOnAppearance = d.commentOnAppearance
            shushedUntil = nil
        }

        @MainActor
        public func apply(to settings: CompanionSettings) throws {
            settings.channels = Channels(names: channels)
            guard let frequency = Frequency(rawValue: frequency) else {
                throw ConformanceSupport.VectorError("unknown frequency \(frequency)")
            }
            settings.frequency = frequency
            settings.activeStartMinute = activeStartMinute
            settings.activeEndMinute = activeEndMinute
            settings.activeWeekdays = Set(activeWeekdays)
            settings.quietHours = Set(quietHours)
            settings.hushInFullScreen = hushInFullScreen
            settings.greetOnReturn = greetOnReturn
            settings.commentOnPower = commentOnPower
            settings.commentOnAppearance = commentOnAppearance
            settings.shushedUntil = try shushedUntil.map(ConformanceSupport.date)
        }
    }

    /// One step of the timeline. `advanceSeconds` passes before each of the `repeat` actions.
    public struct Step: Codable, Equatable, Sendable {
        public var advanceSeconds: Int
        /// "tick", "poke", "wake", "shush" (`argument`: hour, restOfToday, untilISay), "event" (`argument`: a
        /// trigger), "setFrequency" (`argument`: a preset) or "setChannels" (`argument`: comma-separated names).
        public var action: String
        public var argument: String?
        public var `repeat`: Int?
        public var snapshot: SnapshotData
        // Outputs
        public var deliveries: [DeliveryData]
        public var mood: String
        public var isShushed: Bool
        public var shushedUntil: String?
    }

    public struct DeliveryData: Codable, Equatable, Sendable {
        /// Which of the repeated actions produced it, from 0.
        public var index: Int
        public var at: String
        public var id: String
        public var trigger: String
        public var speak: Bool
        public var chime: Bool
        public var notify: Bool
    }

    public struct Vector: Codable, Equatable, Sendable {
        public var name: String
        public var timeZone: String
        public var start: String
        public var seed: UInt64
        /// The app language; absent means "en".
        public var language: String?
        public var settings: Settings
        public var steps: [Step]
    }

    /// One action of a case, before it runs.
    public struct Input: Sendable {
        public var advance: Int
        public var action: String
        public var argument: String?
        public var `repeat`: Int
        public var snapshot: Snapshot

        public init(advance: Int, action: String, argument: String? = nil, repeat: Int = 1, snapshot: Snapshot = .calm) {
            self.advance = advance
            self.action = action
            self.argument = argument
            self.repeat = `repeat`
            self.snapshot = snapshot
        }
    }

    public struct Case: Sendable {
        public var name: String
        public var timeZone: String
        /// Local date components: year, month, day, hour, minute.
        public var start: (Int, Int, Int, Int, Int)
        public var seed: UInt64
        public var language: String
        public var settings: Settings
        public var steps: [Input]

        public init(
            name: String, timeZone: String = "UTC", start: (Int, Int, Int, Int, Int), seed: UInt64 = 42,
            language: String = "en", settings: Settings, steps: [Input]
        ) {
            self.name = name
            self.timeZone = timeZone
            self.start = start
            self.seed = seed
            self.language = language
            self.settings = settings
            self.steps = steps
        }
    }

    public static let minute = 60

    // MARK: Run

    /// Runs the timeline over `bank` and `defaults`, returning the steps with their outputs filled in.
    @MainActor
    public static func run(
        timeZone: String, start: Date, seed: UInt64, language: String, settings settingsData: Settings, steps inputs: [Input],
        bank: PhraseBank, defaults: CompanionDefaults
    ) throws -> [Step] {
        let calendar = try ConformanceSupport.calendar(timeZone)
        let clock = MockClock(start)
        let idle = MockIdle()
        let busy = MockBusy()
        let settings = CompanionSettings(store: InMemorySettingsStore(), defaults: defaults)
        try settingsData.apply(to: settings)
        let coordinator = CompanionCoordinator(
            settings: settings, bank: bank, language: language, clock: clock, idle: idle, busy: busy, calendar: calendar,
            seed: seed)
        var steps: [Step] = []
        for input in inputs {
            var deliveries: [DeliveryData] = []
            for index in 0..<input.repeat {
                clock.now = clock.now.addingTimeInterval(TimeInterval(input.advance))
                idle.idle = input.snapshot.idleSeconds
                busy.reasons = input.snapshot.busy
                let delivery: Delivery?
                switch input.action {
                case "tick": delivery = coordinator.tick(snapshot: input.snapshot)
                case "poke": delivery = coordinator.poke(snapshot: input.snapshot)
                case "wake": delivery = coordinator.wake(snapshot: input.snapshot)
                case "shush":
                    guard let kind = input.argument.flatMap(ChatterCadence.Shush.init(rawValue:)) else {
                        throw ConformanceSupport.VectorError("unknown shush \(input.argument ?? "nil")")
                    }
                    coordinator.shush(kind)
                    delivery = nil
                case "event":
                    guard let trigger = input.argument.flatMap(Trigger.init(rawValue:)) else {
                        throw ConformanceSupport.VectorError("unknown trigger \(input.argument ?? "nil")")
                    }
                    delivery = coordinator.event(trigger, snapshot: input.snapshot)
                case "setFrequency":
                    guard let frequency = input.argument.flatMap(Frequency.init(rawValue:)) else {
                        throw ConformanceSupport.VectorError("unknown frequency \(input.argument ?? "nil")")
                    }
                    settings.frequency = frequency
                    coordinator.settingsChanged()
                    delivery = nil
                case "setChannels":
                    settings.channels = Channels(names: (input.argument ?? "").split(separator: ",").map(String.init))
                    coordinator.settingsChanged()
                    delivery = nil
                default:
                    throw ConformanceSupport.VectorError("unknown action \(input.action)")
                }
                if let delivery {
                    deliveries.append(
                        DeliveryData(
                            index: index, at: ConformanceSupport.string(clock.now, in: calendar.timeZone),
                            id: delivery.phrase.id, trigger: delivery.trigger.rawValue, speak: delivery.plan.speak,
                            chime: delivery.plan.chime, notify: delivery.plan.notify))
                }
            }
            steps.append(
                Step(
                    advanceSeconds: input.advance, action: input.action, argument: input.argument,
                    repeat: input.repeat == 1 ? nil : input.repeat, snapshot: SnapshotData(input.snapshot),
                    deliveries: deliveries, mood: coordinator.mood(snapshot: input.snapshot).rawValue,
                    isShushed: coordinator.isShushed,
                    shushedUntil: settings.shushedUntil.map { ConformanceSupport.string($0, in: calendar.timeZone) }))
        }
        return steps
    }

    @MainActor
    public static func vector(_ c: Case, bank: PhraseBank, defaults: CompanionDefaults) throws -> Vector {
        let calendar = try ConformanceSupport.calendar(c.timeZone)
        let start = ConformanceSupport.local(calendar, c.start.0, c.start.1, c.start.2, c.start.3, c.start.4)
        let steps = try run(
            timeZone: c.timeZone, start: start, seed: c.seed, language: c.language, settings: c.settings, steps: c.steps,
            bank: bank, defaults: defaults)
        return Vector(
            name: c.name, timeZone: c.timeZone, start: ConformanceSupport.string(start, in: calendar.timeZone),
            seed: c.seed, language: c.language == "en" ? nil : c.language, settings: c.settings, steps: steps)
    }

    @MainActor
    public static func files(cases: [Case], bank: PhraseBank, defaults: CompanionDefaults) throws -> [String: Data] {
        try ConformanceSupport.files(in: directory, cases, name: \.name) { try vector($0, bank: bank, defaults: defaults) }
    }

    @MainActor
    public static func verify(_ data: Data, file: String, bank: PhraseBank, defaults: CompanionDefaults) throws -> [String] {
        let vector = try ConformanceSupport.decode(Vector.self, from: data)
        let inputs = vector.steps.map {
            Input(
                advance: $0.advanceSeconds, action: $0.action, argument: $0.argument, repeat: $0.repeat ?? 1,
                snapshot: $0.snapshot.snapshot)
        }
        let steps = try run(
            timeZone: vector.timeZone, start: try ConformanceSupport.date(vector.start), seed: vector.seed,
            language: vector.language ?? "en", settings: vector.settings, steps: inputs, bank: bank, defaults: defaults)
        var problems: [String] = []
        if steps.count != vector.steps.count { problems.append("\(file): \(steps.count) steps, expected \(vector.steps.count)") }
        for (index, (expected, actual)) in zip(vector.steps, steps).enumerated() where expected != actual {
            problems.append("\(file): step \(index) (\(expected.action)) differs: \(actual.deliveries.map(\.id)) \(actual.mood)")
        }
        return problems
    }
}
