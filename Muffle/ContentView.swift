import SwiftUI
import SafariServices

/// The host app does one thing: show whether the two Safari extensions are on
/// and give a way to open Safari's extension settings. Safari runs the
/// extensions itself; this app can be quit afterwards.
struct ContentView: View {
    private let appID = Bundle.main.bundleIdentifier ?? "com.example.muffle"
    private var blockerID: String { appID + ".blocker" }
    private var youtubeID: String { appID + ".youtube" }

    @State private var blockerEnabled: Bool?
    @State private var youtubeEnabled: Bool?
    @State private var message = ""
    @State private var meta: RulesMeta?
    @State private var ytConfig: YTConfigMeta?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Muffle").font(.title2).bold()
            Text("Both parts run inside Safari. You can quit this app once they are enabled.")
                .font(.callout)
                .foregroundColor(.secondary)

            Divider()

            ExtensionRow(title: "Ads & trackers",
                         detail: rulesDescription,
                         enabled: blockerEnabled)
            ExtensionRow(title: "YouTube video ads",
                         detail: youtubeDescription,
                         enabled: youtubeEnabled)

            Divider()

            HStack {
                Button("Open Safari Extension Settings…") { openSafariSettings() }
                Button("Reload Rules") { reloadRules() }
                Spacer()
            }

            if !message.isEmpty {
                Text(message)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .textSelection(.enabled)
            }
        }
        .padding(20)
        .frame(width: 440)
        .onAppear {
            refresh()
            meta = RulesMeta.load(blockerID: blockerID)
            ytConfig = YTConfigMeta.load()
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            refresh()
        }
    }

    private var youtubeDescription: String {
        let base = "Runs only on youtube.com. Allow it for youtube.com when Safari asks."
        guard let c = ytConfig else { return base + " Ad data: built-in defaults." }
        return base + " Ad data: \(c.keys.count) fields, updated \(c.generated)."
    }

    private var rulesDescription: String {
        guard let meta = meta else { return "Content blocker rules compiled by Safari." }
        return "\(meta.count.formatted()) rules, built \(meta.generated)."
    }

    private func refresh() {
        SFContentBlockerManager.getStateOfContentBlocker(withIdentifier: blockerID) { state, error in
            DispatchQueue.main.async {
                blockerEnabled = state?.isEnabled
                if let error = error { message = error.localizedDescription }
            }
        }
        SFSafariExtensionManager.getStateOfSafariExtension(withIdentifier: youtubeID) { state, error in
            DispatchQueue.main.async {
                youtubeEnabled = state?.isEnabled
                if let error = error { message = error.localizedDescription }
            }
        }
    }

    private func reloadRules() {
        message = "Reloading…"
        SFContentBlockerManager.reloadContentBlocker(withIdentifier: blockerID) { error in
            DispatchQueue.main.async {
                if let error = error {
                    message = "Rule reload failed: \(error.localizedDescription)"
                } else {
                    message = "Rules reloaded."
                }
            }
        }
    }

    private func openSafariSettings() {
        SFSafariApplication.showPreferencesForExtension(withIdentifier: blockerID) { error in
            if let error = error {
                DispatchQueue.main.async { message = error.localizedDescription }
            }
        }
    }
}

struct ExtensionRow: View {
    let title: String
    let detail: String
    let enabled: Bool?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(color)
                .frame(width: 10, height: 10)
                .padding(.top, 4)
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(title).font(.body).bold()
                    Spacer()
                    Text(label).font(.caption).foregroundColor(.secondary)
                }
                Text(detail).font(.caption).foregroundColor(.secondary)
            }
        }
    }

    private var color: Color {
        switch enabled {
        case .some(true): return .green
        case .some(false): return .orange
        case .none: return .gray
        }
    }

    private var label: String {
        switch enabled {
        case .some(true): return "Enabled"
        case .some(false): return "Off in Safari"
        case .none: return "Unknown"
        }
    }
}

/// Written by tools/build_rules.py next to blockerList.json.
struct RulesMeta: Decodable {
    let count: Int
    let generated: String

    static func load(blockerID: String) -> RulesMeta? {
        // `make update` drops a copy of the meta file in this app's container; prefer it.
        if let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first,
           let data = try? Data(contentsOf: support.appendingPathComponent("Muffle/rules-meta.json")),
           let meta = try? JSONDecoder().decode(RulesMeta.self, from: data) {
            return meta
        }
        guard let plugins = Bundle.main.builtInPlugInsURL else { return nil }
        let appex = plugins.appendingPathComponent("Blocker.appex")
        guard let bundle = Bundle(url: appex),
              let url = bundle.url(forResource: "rules-meta", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(RulesMeta.self, from: data)
    }
}

/// Written by tools/update.sh next to rules-meta.json (a copy of the YouTube extension's config).
struct YTConfigMeta: Decodable {
    let keys: [String]
    let generated: String

    static func load() -> YTConfigMeta? {
        guard let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first,
              let data = try? Data(contentsOf: support.appendingPathComponent("Muffle/yt-config.json")) else { return nil }
        return try? JSONDecoder().decode(YTConfigMeta.self, from: data)
    }
}
