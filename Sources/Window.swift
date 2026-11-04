import AppKit
import SwiftUI

private let ink = Color(red: 0.91, green: 0.94, blue: 0.98)
private let muted = Color(red: 0.53, green: 0.60, blue: 0.70)
private let teal = Color(red: 0.28, green: 0.87, blue: 0.73)
private let card = Color(red: 0.085, green: 0.11, blue: 0.15)

struct Quota: Identifiable {
    let id: String
    let label: String
    let used: Int
    let reset: Double?
}
final class MonitorState: ObservableObject {
    @Published var live = false
    @Published var stale = false
    @Published var demo = false
    @Published var fatal = false
    @Published var loaded = false
    @Published var plan = "Codex"
    @Published var updatedAt: Double?
    @Published var error: String?
    @Published var quotas: [Quota] = []
    @Published var balance: String?
    @Published var unlimited = false
    @Published var resetCredits: Int?
    @Published var resetExpiry: Double?
    @Published var refreshing = true
    @Published var available = false
    @Published var notice: String?
    @Published var sourceLabel: String?
    @Published var observedAt: Double?
    let provider: String
    var refresh: (() -> Void)?
    var resize: (() -> Void)?

    // Each provider has its own state; values are never shared between the two panels.
    init(provider: String) {
        self.provider = provider
        plan = provider == "claude" ? "Claude" : "Codex"
    }

    func apply(_ result: [String: Any]) {
        // A late line from another provider's helper must never be shown.
        if let source = result["provider"] as? String, source != provider { return }
        live = result["live"] as? Bool ?? false
        stale = result["stale"] as? Bool ?? false
        demo = result["demo"] as? Bool ?? false
        fatal = result["fatal"] as? Bool ?? false
        refreshing = false
        error = result["error"] as? String
        updatedAt = result["updatedAt"] as? Double
        if let windows = result["windows"] as? [[String: Any]] {
            loaded = true
            var seen = Set<String>()
            quotas = windows.enumerated().compactMap { (index, item) -> Quota? in
                guard let label = item["label"] as? String, let used = item["used"] as? Int else { return nil }
                var id = item["id"] as? String ?? label
                if seen.contains(id) { id = "\(id)-\(index)" }
                seen.insert(id)
                return Quota(id: id, label: label, used: min(100, max(0, used)), reset: item["reset"] as? Double)
            }
            resize?()
            plan = (result["plan"] as? String ?? (provider == "claude" ? "Claude" : "Codex")).capitalized
            available = result["available"] as? Bool ?? false
            notice = result["notice"] as? String
            sourceLabel = result["sourceLabel"] as? String
            observedAt = result["observedAt"] as? Double
            if provider == "codex" {
                let credits = result["credits"] as? [String: Any] ?? [:]
                balance = credits["balance"] as? String
                unlimited = credits["unlimited"] as? Bool ?? false
                resetCredits = result["resetCredits"] as? Int
                resetExpiry = result["resetCreditExpiry"] as? Double
            }
        }
    }
}

// All times use the Mac's current time zone and locale, and follow later changes to them.
private func localFormatter(_ template: String) -> DateFormatter {
    let formatter = DateFormatter()
    formatter.locale = Locale.autoupdatingCurrent
    formatter.timeZone = TimeZone.autoupdatingCurrent
    formatter.setLocalizedDateFormatFromTemplate(template)
    return formatter
}
func dateText(_ timestamp: Double?) -> String {
    guard let timestamp = timestamp else { return "Time unavailable" }
    return localFormatter("MMM d jmm").string(from: Date(timeIntervalSince1970: timestamp))
}
func clockText(_ timestamp: Double?) -> String {
    guard let timestamp = timestamp else { return "time unavailable" }
    return localFormatter("jmmss").string(from: Date(timeIntervalSince1970: timestamp))
}
func zoneLabel() -> String {
    let zone = TimeZone.autoupdatingCurrent
    return zone.abbreviation() ?? zone.identifier
}
// Compact display: always two fractional digits. The feed keeps the exact value (shown in the tooltip).
func balanceText(_ balance: String?) -> String {
    guard let balance = balance, let number = Decimal(string: balance, locale: Locale(identifier: "en_US_POSIX")) else { return "Unavailable" }
    let formatter = NumberFormatter()
    formatter.locale = Locale.autoupdatingCurrent
    formatter.numberStyle = .decimal
    formatter.minimumFractionDigits = 2
    formatter.maximumFractionDigits = 2
    return formatter.string(from: NSDecimalNumber(decimal: number)) ?? "Unavailable"
}

struct QuotaCard: View {
    let quota: Quota
    var remaining: Int { max(0, 100 - quota.used) }
    var accent: Color { remaining <= 10 ? Color(red: 1, green: 0.47, blue: 0.40) : remaining <= 25 ? Color.orange : teal }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(quota.label == "5H" ? "5-hour limit" : quota.label == "7D" ? "Weekly limit" : "Limit · \(quota.label)")
                    .font(.system(size: 13, weight: .medium)).foregroundColor(ink)
                Spacer()
                Text("\(remaining)%").font(.system(size: 28, weight: .semibold, design: .rounded)).monospacedDigit().foregroundColor(accent)
                Text("left").font(.system(size: 11)).foregroundColor(muted)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.07))
                    Capsule().fill(accent).frame(width: geo.size.width * CGFloat(remaining) / 100)
                }
            }.frame(height: 6)
            HStack {
                Text("\(quota.used)% used")
                Spacer()
                Text("Reset \(dateText(quota.reset))")
            }.font(.system(size: 10)).foregroundColor(muted)
        }.padding(16).background(card).cornerRadius(14)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.white.opacity(0.045), lineWidth: 1))
    }
}

final class WindowControls: ObservableObject {
    @Published var pinned = true
    var pin: ((Bool) -> Void)?
}

struct Dashboard: View {
    @ObservedObject var controls: WindowControls
    @ObservedObject var codex: MonitorState
    @ObservedObject var claude: MonitorState
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Image(systemName: "chart.bar.xaxis").font(.system(size: 21, weight: .medium)).foregroundColor(teal)
                    .frame(width: 42, height: 42).background(teal.opacity(0.10)).cornerRadius(12)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Codex & Claude").font(.system(size: 19, weight: .semibold)).foregroundColor(ink)
                    Text(codex.demo || claude.demo ? "DEMO DATA · synthetic values, not your accounts" : "Usage limits at a glance")
                        .font(.system(size: 11, weight: codex.demo || claude.demo ? .semibold : .regular))
                        .foregroundColor(codex.demo || claude.demo ? Color.orange : muted)
                }
                Spacer()
                Button { controls.pinned.toggle(); controls.pin?(controls.pinned) } label: {
                    Image(systemName: controls.pinned ? "pin.fill" : "pin").font(.system(size: 13)).foregroundColor(controls.pinned ? teal : muted)
                        .frame(width: 30, height: 30).background(Color.white.opacity(0.045)).cornerRadius(8)
                }.buttonStyle(.plain).help("Keep window on top")
            }
            ScrollView(.vertical, showsIndicators: true) {
                HStack(alignment: .top, spacing: 18) {
                    ProviderPanel(state: codex)
                    ProviderPanel(state: claude)
                }
            }
        }.padding(22).frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(red: 0.045, green: 0.063, blue: 0.089)).colorScheme(.dark)
    }
}

struct ProviderPanel: View {
    @ObservedObject var state: MonitorState
    private var isClaude: Bool { state.provider == "claude" }
    private var subtitle: String {
        if state.demo { return "DEMO DATA · synthetic values" }
        if isClaude {
            if state.stale { return "STALE · last received snapshot" }
            return state.available ? "\(state.sourceLabel ?? "Snapshot") · Claude Code usage" : "Claude Code · no usable snapshot"
        }
        if state.stale { return "STALE · last successful values" }
        return "\(state.plan) · Account usage"
    }
    private var statusText: String {
        if state.refreshing { return "Refreshing..." }
        if state.demo { return "Demo data · generated \(clockText(state.updatedAt))" }
        if isClaude {
            if !state.available { return "No snapshot" }
            let verb = state.sourceLabel == "Claude Code cache" ? "Cache updated" : "Snapshot received"
            return "\(verb) \(state.stale ? dateText(state.observedAt) : clockText(state.observedAt))"
        }
        if state.live { return "Live · updated \(clockText(state.updatedAt))" }
        if state.stale { return "Stale · last success \(dateText(state.updatedAt))" }
        return "Not connected"
    }
    private var healthy: Bool { isClaude ? (state.available && !state.stale) : state.live }
    private var footnote: String {
        if let error = state.error { return error }
        if isClaude { return "Reads local Claude Code snapshots only; refresh does not query Claude · Local time (\(zoneLabel()))" }
        return "Refreshes every 60 s · Times in local time (\(zoneLabel()))"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                Text(isClaude ? "Claude" : "Codex").font(.system(size: 16, weight: .semibold)).foregroundColor(ink)
                Text(subtitle).font(.system(size: 11, weight: state.demo || state.stale ? .semibold : .regular))
                    .foregroundColor(state.demo || state.stale ? Color.orange : muted)
            }
            if !isClaude {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("CREDIT BALANCE").font(.system(size: 10, weight: .semibold)).kerning(1.3).foregroundColor(muted)
                    Spacer()
                    Image(systemName: "circle.hexagongrid.fill").foregroundColor(teal.opacity(0.75))
                }
                Text(state.unlimited ? "Unlimited" : balanceText(state.balance))
                    .font(.system(size: state.balance == nil && !state.unlimited ? 25 : 36, weight: .semibold, design: .rounded))
                    .monospacedDigit().foregroundColor(ink).lineLimit(1).minimumScaleFactor(0.5)
                    .help(state.unlimited ? "" : state.balance.map { "Exact value from Codex: \($0)" } ?? "")
                Text("Credits for additional Codex usage")
                    .font(.system(size: 11)).foregroundColor(muted)
            }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
                .background(LinearGradient(colors: [Color(red: 0.08, green: 0.17, blue: 0.19), card], startPoint: .topLeading, endPoint: .bottomTrailing))
                .cornerRadius(16).overlay(RoundedRectangle(cornerRadius: 16).stroke(teal.opacity(0.16), lineWidth: 1))
                .opacity(state.stale ? 0.55 : 1)
            }
            VStack(spacing: 10) {
                ForEach(state.quotas) { QuotaCard(quota: $0) }
                if !state.loaded {
                    Text(state.error ?? "Loading limits...").font(.system(size: 12)).foregroundColor(muted).padding(16)
                } else if state.quotas.isEmpty {
                    Text(isClaude ? (state.notice ?? "Claude Code has not reported usage limits.") : "Codex did not report usage limits right now.")
                        .font(.system(size: 12)).foregroundColor(muted).padding(16)
                }
                if isClaude {
                    Text("Shows Claude Code usage snapshots (5-hour and weekly limits), not a live account query.")
                        .font(.system(size: 10)).foregroundColor(muted.opacity(0.75)).frame(maxWidth: .infinity, alignment: .leading)
                }
            }.opacity(state.stale ? 0.55 : 1)
            if !isClaude {
            HStack(spacing: 12) {
                Image(systemName: "arrow.counterclockwise").font(.system(size: 16, weight: .medium)).foregroundColor(muted)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Limit reset credits").font(.system(size: 12, weight: .medium)).foregroundColor(ink)
                    Text(state.resetExpiry == nil ? "Separate from your credit balance" : "Next expiry \(dateText(state.resetExpiry))")
                        .font(.system(size: 10)).foregroundColor(muted)
                }
                Spacer()
                Text(state.resetCredits.map(String.init) ?? "N/A").font(.system(size: 25, weight: .semibold, design: .rounded)).foregroundColor(ink)
            }.padding(14).background(card).cornerRadius(12)
                .opacity(state.stale ? 0.55 : 1)
            }
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 6) {
                    Circle().fill(healthy ? teal : Color.orange).frame(width: 5, height: 5)
                    Text(statusText)
                        .font(.system(size: 10)).foregroundColor(muted)
                    Spacer()
                    Button { state.refreshing = true; state.refresh?() } label: {
                        Image(systemName: "arrow.clockwise").foregroundColor(muted)
                    }.buttonStyle(.plain).disabled(state.refreshing).help("Refresh now")
                }
                Text(footnote)
                    .font(.system(size: 10)).foregroundColor(state.error == nil ? muted.opacity(0.75) : Color.orange)
            }
        }.frame(maxWidth: .infinity, alignment: .topLeading)
    }
}

// Finder starts apps with a minimal PATH and no shell profile, so interpreter lookup is explicit.
// CODEX_MONITOR_PYTHON, CODEX_BIN and CODEX_HOME are honored when set in the launching environment.
func findPython() -> URL? {
    let files = FileManager.default
    let environment = ProcessInfo.processInfo.environment
    // An explicit override is authoritative: if it is not usable, do not silently fall back to another Python.
    if let override = environment["CODEX_MONITOR_PYTHON"], !override.isEmpty {
        return files.isExecutableFile(atPath: override) ? URL(fileURLWithPath: override) : nil
    }
    // /usr/bin/python3 is a stub; without developer tools it opens an installer prompt, so skip it then.
    let developerPython = [
        "/Library/Developer/CommandLineTools/usr/bin/python3",
        "/Applications/Xcode.app/Contents/Developer/usr/bin/python3",
    ]
    let stubUsable = developerPython.contains(where: { files.isExecutableFile(atPath: $0) })
    let inherited = (environment["PATH"] ?? "").split(separator: ":").map(String.init)
        .filter { $0.hasPrefix("/") }
        .map { (URL(fileURLWithPath: $0).standardizedFileURL.path as NSString).appendingPathComponent("python3") }
        .filter { $0 != "/usr/bin/python3" }  // the Apple stub is deferred until after the Homebrew/python.org fallbacks
    let candidates = inherited + [
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
        "/usr/bin/python3",
    ]
    return candidates.first(where: { ($0 != "/usr/bin/python3" || stubUsable) && files.isExecutableFile(atPath: $0) })
        .map { URL(fileURLWithPath: $0) }
}

// One background helper (`feed.py --provider <id>`) and its lifecycle for one provider's state.
final class FeedRunner {
    var worker: Process?
    var input: FileHandle?
    var generation = 0
    var watchdog: DispatchWorkItem?
    let demo: Bool
    let state: MonitorState
    init(state: MonitorState, demo: Bool) {
        self.state = state
        self.demo = demo
        state.refresh = { [weak self] in self?.requestRefresh() }
    }
    func fail(_ message: String) {
        state.live = false; state.stale = state.loaded; state.refreshing = false; state.fatal = true; state.error = message
    }
    // Ask the running helper to refresh; if it is gone, start a new one instead of waiting forever.
    func requestRefresh() {
        if let worker = worker, worker.isRunning, let input = input {
            do { try input.write(contentsOf: Data("refresh\n".utf8)); armWatchdog(); return } catch {}
        }
        startFeed()
    }
    // The helper bounds its own requests; this only guards against a helper that hangs outright.
    func armWatchdog() {
        watchdog?.cancel()
        let item = DispatchWorkItem { [weak self] in
            guard let self = self, self.state.refreshing else { return }
            self.startFeed()
            self.state.error = "The helper did not respond in time and was restarted."
        }
        watchdog = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 90, execute: item)
    }
    func stopFeed() {
        try? input?.close(); input = nil
        worker?.terminate(); worker = nil
    }
    func startFeed() {
        // Invalidate the old watchdog and helper callbacks before stopping the helper, even if the launch below fails.
        watchdog?.cancel(); watchdog = nil
        generation += 1
        let current = generation
        stopFeed()
        state.live = false; state.stale = state.loaded
        state.refreshing = true
        state.fatal = false
        guard let resources = Bundle.main.resourceURL else { fail("App resources are missing. Rebuild or reinstall Codex Monitor."); return }
        guard let python = findPython() else {
            fail("Python 3.10 or newer was not found. Install it from python.org or Homebrew, then reopen Codex Monitor.")
            return
        }
        let process = Process()
        process.executableURL = python
        process.arguments = ["-B", resources.appendingPathComponent("feed.py").path, "--provider", state.provider] + (demo ? ["--demo"] : [])
        var environment = ProcessInfo.processInfo.environment  // keeps CODEX_HOME / CODEX_BIN overrides
        let search = ["/opt/homebrew/bin", "/usr/local/bin", NSHomeDirectory() + "/.local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        environment["PATH"] = search.joined(separator: ":") + ":" + (environment["PATH"] ?? "")
        environment["PYTHONPATH"] = resources.appendingPathComponent("vendor").path
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        let pipe = Pipe(), commands = Pipe()
        process.standardOutput = pipe; process.standardInput = commands; process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { fail("Could not start the Python helper. Check the Python installation."); return }
        input = commands.fileHandleForWriting
        worker = process
        armWatchdog()
        DispatchQueue.global(qos: .utility).async { [weak self] in
            var buffer = Data()
            while true {
                let data = pipe.fileHandleForReading.availableData
                if data.isEmpty { break }
                buffer.append(data)
                while let end = buffer.firstIndex(of: 10) {
                    let line = buffer.subdata(in: 0..<end); buffer.removeSubrange(0...end)
                    guard let result = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
                    DispatchQueue.main.async { if self?.generation == current { self?.state.apply(result) } }
                }
            }
            try? pipe.fileHandleForReading.close()
            DispatchQueue.main.async {
                // A newer helper may already be running; only report the stop of the current one.
                guard let self = self, self.generation == current else { return }
                self.state.live = false; self.state.stale = self.state.loaded; self.state.refreshing = false
                if !self.state.fatal { self.state.error = "The background helper stopped. Select Refresh to restart it." }
            }
        }
    }
}

final class Monitor: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let demo = CommandLine.arguments.contains("--demo") || ProcessInfo.processInfo.environment["CODEX_MONITOR_DEMO"] == "1"
    let codex = MonitorState(provider: "codex")
    let claude = MonitorState(provider: "claude")
    let controls = WindowControls()
    var runners: [FeedRunner] = []
    static let width: CGFloat = 840
    // Estimated content height of the taller panel; the panels scroll if the screen is shorter.
    private func wantedHeight() -> CGFloat {
        let codexHeight = 190 + 150 + 120 * CGFloat(max(1, codex.quotas.count)) + 120
        let claudeHeight = 150 + 115 * CGFloat(max(1, claude.quotas.count)) + 60
        return max(560, max(codexHeight, claudeHeight))
    }
    func resizeWindow() {
        guard let window = window else { return }
        let limit = (window.screen ?? NSScreen.main)?.visibleFrame.height ?? 900
        let oldTop = window.frame.maxY
        window.setContentSize(NSSize(width: Monitor.width, height: min(wantedHeight(), limit - 40)))
        window.setFrameOrigin(NSPoint(x: window.frame.minX, y: oldTop - window.frame.height))
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: Monitor.width, height: 560), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = demo ? "Codex & Claude · Demo data" : "Codex & Claude"
        window.titlebarAppearsTransparent = true
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(calibratedRed: 0.045, green: 0.063, blue: 0.089, alpha: 1)
        window.contentView = NSHostingView(rootView: Dashboard(controls: controls, codex: codex, claude: claude))
        window.contentMinSize = NSSize(width: Monitor.width, height: 480)
        window.level = .floating
        controls.pin = { [weak self] pinned in self?.window.level = pinned ? .floating : .normal }
        runners = [FeedRunner(state: codex, demo: demo), FeedRunner(state: claude, demo: demo)]
        for state in [codex, claude] { state.resize = { [weak self] in self?.resizeWindow() } }
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        resizeWindow()
        runners.forEach { $0.startFeed() }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { runners.forEach { $0.stopFeed() } }
}
signal(SIGPIPE, SIG_IGN)  // a dead helper must surface as a write error, not kill the app
let app = NSApplication.shared
let delegate = Monitor()
app.delegate = delegate
let menu = NSMenu()
let appItem = NSMenuItem()
let appMenu = NSMenu()
appMenu.addItem(withTitle: "Quit Codex Monitor", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
appItem.submenu = appMenu
menu.addItem(appItem)
app.mainMenu = menu
app.setActivationPolicy(.regular)
app.run()
