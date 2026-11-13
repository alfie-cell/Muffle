import SwiftUI
import SafariServices

@main
struct MuffleApp: App {
    init() {
        // `Muffle --reload` (from Terminal or `make reload`): ask Safari to reload the
        // content blocker rules, print the outcome, and exit. Used for testing rule lists.
        if CommandLine.arguments.contains("--reload") {
            MuffleApp.reloadFromCommandLine()
        }
        if CommandLine.arguments.contains("--status") {
            MuffleApp.statusFromCommandLine()
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }

    /// `Muffle --status`: print whether Safari has each extension enabled, then exit.
    private static func statusFromCommandLine() -> Never {
        let base = Bundle.main.bundleIdentifier ?? "com.example.muffle"
        var pending = 2
        SFContentBlockerManager.getStateOfContentBlocker(withIdentifier: base + ".blocker") { state, error in
            print("blocker: " + (error.map { "error \($0)" } ?? (state?.isEnabled == true ? "enabled" : "disabled")))
            pending -= 1
        }
        SFSafariExtensionManager.getStateOfSafariExtension(withIdentifier: base + ".youtube") { state, error in
            print("youtube: " + (error.map { "error \($0)" } ?? (state?.isEnabled == true ? "enabled" : "disabled")))
            pending -= 1
        }
        while pending > 0 {
            RunLoop.main.run(mode: .default, before: Date(timeIntervalSinceNow: 0.1))
        }
        exit(0)
    }

    private static func reloadFromCommandLine() -> Never {
        let identifier = (Bundle.main.bundleIdentifier ?? "com.example.muffle") + ".blocker"
        let start = Date()
        var finished = false
        var exitCode: Int32 = 0
        SFContentBlockerManager.reloadContentBlocker(withIdentifier: identifier) { error in
            let secs = String(format: "%.1f", Date().timeIntervalSince(start))
            if let error = error {
                print("reload FAILED after \(secs) s: \(error)")
                exitCode = 1
            } else {
                print("reload ok in \(secs) s")
            }
            finished = true
        }
        while !finished {
            RunLoop.main.run(mode: .default, before: Date(timeIntervalSinceNow: 0.1))
        }
        exit(exitCode)
    }
}
