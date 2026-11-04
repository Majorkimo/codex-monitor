import AppKit
import SwiftUI

// Semantic system colors only, so light, dark and increased-contrast settings are followed automatically.
private let codexAccent = Color(nsColor: .systemBlue)
private let claudeAccent = Color(nsColor: .systemOrange)
private let warning = Color(nsColor: .systemOrange)
private let critical = Color(nsColor: .systemRed)
private let healthy = Color(nsColor: .systemGreen)
private let cardFill = Color(nsColor: .controlBackgroundColor)
private let hairline = Color(nsColor: .separatorColor)

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
        refreshing = result["refreshing"] as? Bool ?? false  // the Claude helper shows files first, then refreshes
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

struct LimitBar: View {
    let fraction: Double
    let tint: Color
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color(nsColor: .quaternaryLabelColor))
                Capsule().fill(tint).frame(width: max(0, geo.size.width * CGFloat(min(1, max(0, fraction)))))
            }
        }.frame(height: 6).accessibilityHidden(true)
    }
}

// One limit window. A window the provider did not report says so; it is never drawn as 0% used or left.
struct QuotaSection: View {
    let title: String
    let quota: Quota?
    let accent: Color
    private var remaining: Int { max(0, 100 - (quota?.used ?? 0)) }
    private var tint: Color { remaining <= 10 ? critical : accent }
    private var summary: String {
        guard let quota = quota else { return "\(title): not reported" }
        let reset = quota.reset == nil ? "reset time not reported" : "resets \(dateText(quota.reset))"
        return "\(title): \(remaining) percent left, \(quota.used) percent used, \(reset)" + (remaining <= 10 ? ", low" : "")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(.subheadline.weight(.medium)).foregroundColor(.secondary)
                Spacer()
                if quota != nil && remaining <= 10 {
                    Label("Low", systemImage: "exclamationmark.circle.fill").font(.caption.weight(.semibold)).foregroundColor(tint)
                }
            }
            if let quota = quota {
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Text("\(remaining)%").font(.system(size: 34, weight: .semibold)).monospacedDigit()
                    Text("left").font(.callout).foregroundColor(.secondary)
                }.frame(height: 40, alignment: .leading)
                LimitBar(fraction: Double(remaining) / 100, tint: tint)
                HStack {
                    Text("\(quota.used)% used")
                    Spacer()
                    Text(quota.reset == nil ? "Reset time not reported" : "Resets \(dateText(quota.reset))")
                }.font(.caption).foregroundColor(.secondary)
            } else {
                Text("Not reported").font(.system(size: 22, weight: .semibold)).foregroundColor(.secondary)
                    .frame(height: 40, alignment: .leading)
                LimitBar(fraction: 0, tint: accent)
                Text("This limit is not available right now.").font(.caption).foregroundColor(.secondary)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(summary)
    }
}

struct DetailRow: View {
    let icon: String
    let title: String
    let value: String
    var caption: String? = nil
    var tip: String = ""
    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: icon).font(.system(size: 14, weight: .medium)).foregroundColor(.secondary).frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                if let caption = caption {
                    Text(caption).font(.caption).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            .layoutPriority(0)
            Spacer(minLength: 8)
            Text(value).font(.system(size: 15, weight: .semibold)).monospacedDigit()
                .lineLimit(1).truncationMode(.middle)
                .fixedSize(horizontal: false, vertical: true)
                .layoutPriority(1)
        }
        .padding(.vertical, 9)
        .frame(minHeight: 58, alignment: .center)
        .help(tip)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title): \(value)" + (caption.map { ", \($0)" } ?? ""))
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
    private var demo: Bool { codex.demo || claude.demo }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center, spacing: 10) {
                VStack(alignment: .leading, spacing: 1) {
                    Text("Usage").font(.system(size: 26, weight: .bold))
                    HStack(spacing: 8) {
                        Text("Codex & Claude").font(.subheadline).foregroundColor(.secondary)
                        if demo {
                            Text("Demo data").font(.caption.weight(.semibold)).padding(.horizontal, 7).padding(.vertical, 2)
                                .background(Capsule().fill(warning.opacity(0.22)))
                                .help("Synthetic values, not your accounts")
                        }
                    }
                }
                Spacer()
                Button { controls.pinned.toggle(); controls.pin?(controls.pinned) } label: {
                    Image(systemName: controls.pinned ? "pin.fill" : "pin").frame(width: 16, height: 16)
                }
                .buttonStyle(.bordered).controlSize(.regular)
                .help(controls.pinned ? "Window stays on top. Click to let it go behind other windows." : "Keep window on top")
                .accessibilityLabel("Keep window on top")
                .accessibilityValue(controls.pinned ? "On" : "Off")
            }
            ScrollView(.vertical, showsIndicators: true) {
                HStack(alignment: .top, spacing: 16) {
                    ProviderCard(state: codex)
                    ProviderCard(state: claude)
                }.padding(.bottom, 2)
            }
        }
        .padding(.horizontal, 20).padding(.top, 34).padding(.bottom, 18)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

struct ProviderCard: View {
    @ObservedObject var state: MonitorState
    @State private var showInfo = false
    private var isClaude: Bool { state.provider == "claude" }
    private var name: String { isClaude ? "Claude" : "Codex" }
    private var accent: Color { isClaude ? claudeAccent : codexAccent }
    private var subtitle: String {
        if state.demo { return "Demo data" }
        if isClaude { return "Claude Code" }
        return state.plan == "Codex" ? "ChatGPT account" : "\(state.plan) plan"
    }
    private var isCurrent: Bool { isClaude ? (state.available && !state.stale) : state.live }
    private var hasData: Bool { isClaude ? state.available : state.loaded && !state.quotas.isEmpty }
    // Updated means a read succeeded at that time; it never claims continuous live data.
    private var statusText: String {
        if state.refreshing { return "Refreshing…" }
        if state.demo { return "Demo data · generated \(clockText(state.updatedAt))" }
        if isClaude {
            if !state.available { return "No usage available" }
            return state.stale ? "Stale · last updated \(dateText(state.observedAt))" : "Updated \(clockText(state.observedAt))"
        }
        if state.live { return "Updated \(clockText(state.updatedAt))" }
        if state.stale { return "Stale · last updated \(dateText(state.updatedAt))" }
        return "Not connected"
    }
    private var statusIcon: String {
        if state.refreshing { return "arrow.triangle.2.circlepath" }
        return isCurrent || state.demo ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
    }
    private var problem: String? {
        if let error = state.error { return error }
        if isClaude && !state.available { return state.notice ?? (state.loaded ? "Claude Code has not reported usage limits." : nil) }
        return nil
    }
    private func quota(_ label: String) -> Quota? { state.quotas.first(where: { $0.label == label }) }
    private var extras: [Quota] { state.quotas.filter { $0.label != "5H" && $0.label != "7D" } }
    private var infoText: String {
        if isClaude {
            return "Usage comes from Claude Code's own cache, advanced by its built-in /usage command (no prompt is sent), or from the optional status line snapshot. Times are shown in local time (\(zoneLabel()))."
        }
        return "Read from your signed-in Codex account every 60 seconds. Times are shown in local time (\(zoneLabel()))."
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: isClaude ? "sparkles" : "chevron.left.forwardslash.chevron.right")
                    .font(.system(size: 15, weight: .semibold)).foregroundColor(accent)
                    .frame(width: 32, height: 32)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(accent.opacity(0.16)))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 1) {
                    Text(name).font(.headline)
                    Text(subtitle).font(.caption).foregroundColor(.secondary)
                }
                Spacer(minLength: 6)
                Button { state.refreshing = true; state.refresh?() } label: {
                    Image(systemName: "arrow.clockwise").frame(width: 14, height: 14)
                }
                .buttonStyle(.bordered).controlSize(.small).disabled(state.refreshing)
                .help("Refresh \(name) usage now")
                .accessibilityLabel("Refresh \(name) usage")
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: statusIcon).font(.caption.weight(.semibold))
                    .foregroundColor(state.refreshing ? .secondary : (isCurrent || state.demo ? healthy : warning))
                Text(statusText).font(.caption).foregroundColor(.primary).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(name) status: \(statusText)")
            Divider()
            if !state.loaded && state.error == nil {
                Text("Loading limits…").font(.subheadline).foregroundColor(.secondary).padding(.vertical, 8)
            }
            QuotaSection(title: "5-hour limit", quota: quota("5H"), accent: accent)
            Divider()
            QuotaSection(title: "Weekly limit", quota: quota("7D"), accent: accent)
            ForEach(extras) { extra in
                Divider()
                QuotaSection(title: "Limit · \(extra.label)", quota: extra, accent: accent)
            }
            Divider()
            VStack(spacing: 0) {
                if isClaude {
                    DetailRow(icon: "externaldrive", title: "Source", value: state.sourceLabel ?? "None")
                    Divider()
                    DetailRow(icon: "clock", title: "Last observed", value: state.observedAt == nil ? "Not reported" : dateText(state.observedAt))
                } else {
                    DetailRow(icon: "creditcard", title: "Credit balance", value: state.unlimited ? "Unlimited" : balanceText(state.balance),
                              caption: "For additional usage",
                              tip: state.unlimited ? "" : state.balance.map { "Exact value from Codex: \($0)" } ?? "")
                    Divider()
                    DetailRow(icon: "arrow.counterclockwise", title: "Limit reset credits", value: state.resetCredits.map(String.init) ?? "Not reported",
                              caption: state.resetExpiry == nil ? "Separate from credit balance" : "Next expiry \(dateText(state.resetExpiry))")
                }
            }
            if let problem = problem {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundColor(warning).font(.caption)
                    Text(problem).font(.caption).foregroundColor(.primary).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(warning.opacity(0.14)))
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(name) problem: \(problem)")
            }
            HStack {
                Spacer()
                Button { showInfo.toggle() } label: { Image(systemName: "info.circle") }
                    .buttonStyle(.borderless)
                    .help("About this data")
                    .accessibilityLabel("About \(name) data")
                    .popover(isPresented: $showInfo) {
                        Text(infoText).font(.callout).fixedSize(horizontal: false, vertical: true).frame(width: 260).padding(14)
                    }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(cardFill))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(hairline, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(name) usage")
    }
}

// Finder starts apps with a minimal PATH and no shell profile, so interpreter lookup is explicit.
// CODEX_MONITOR_PYTHON, CODEX_BIN, CODEX_HOME and CLAUDE_BIN are honored when set in the launching environment.
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
        var environment = ProcessInfo.processInfo.environment  // keeps CODEX_HOME / CODEX_BIN / CLAUDE_BIN overrides
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
    static let frameName = "CodexMonitorMainWindow"
    func applicationDidFinishLaunching(_ notification: Notification) {
        // The size is chosen once; later polls never resize the window, so the user's width and height stay.
        let screen = NSScreen.main?.visibleFrame.height ?? 900
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: min(640, screen - 60)),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = demo ? "Usage · Demo data" : "Usage"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.contentView = NSHostingView(rootView: Dashboard(controls: controls, codex: codex, claude: claude))
        window.contentMinSize = NSSize(width: 660, height: 380)
        window.level = .floating
        controls.pin = { [weak self] pinned in self?.window.level = pinned ? .floating : .normal }
        runners = [FeedRunner(state: codex, demo: demo), FeedRunner(state: claude, demo: demo)]
        if !window.setFrameUsingName(Monitor.frameName) { window.center() }
        window.setFrameAutosaveName(Monitor.frameName)
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
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
