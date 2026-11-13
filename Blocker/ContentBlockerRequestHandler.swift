import Foundation
import AppKit  // PlugInKit needs AppKit linked in a macOS app extension, or the host never gets a reply
import os.log

/// Safari calls this once when it (re)loads the content blocker. It hands over a JSON
/// rule list; Safari compiles it and the extension process exits.
///
/// Rules come from one of two places, newest wins:
///   1. An updated list written by `make update` into this extension's sandbox container:
///      ~/Library/Containers/<bundle id>/Data/Library/Application Support/Muffle/blockerList.json
///   2. The list bundled at build time.
/// The updated file is parsed once before use so a truncated download can never take the
/// blocker down; a bad file is ignored and the bundled list is used instead.
final class ContentBlockerRequestHandler: NSObject, NSExtensionRequestHandling {
    private static let log = OSLog(subsystem: "com.example.muffle", category: "blocker")

    func beginRequest(with context: NSExtensionContext) {
        guard let url = Self.rulesURL(), let attachment = NSItemProvider(contentsOf: url) else {
            os_log("no rule list available", log: Self.log, type: .error)
            context.cancelRequest(withError: NSError(domain: "Muffle", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "blockerList.json is missing from the extension bundle. Run `make rules` and rebuild."
            ]))
            return
        }
        let item = NSExtensionItem()
        item.attachments = [attachment]
        context.completeRequest(returningItems: [item], completionHandler: nil)
        os_log("rules handed to Safari from %{public}@", log: Self.log, type: .default, url.path)
    }

    private static func rulesURL() -> URL? {
        let bundled = Bundle.main.url(forResource: "blockerList", withExtension: "json")
        guard let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return bundled
        }
        let updated = support.appendingPathComponent("Muffle/blockerList.json")
        guard FileManager.default.fileExists(atPath: updated.path) else { return bundled }
        if let bundled = bundled, isNewer(bundled, than: updated) {
            os_log("bundled list is newer than the updated one; using bundled", log: log, type: .default)
            return bundled
        }
        guard let data = try? Data(contentsOf: updated),
              let rules = try? JSONSerialization.jsonObject(with: data) as? [Any], !rules.isEmpty else {
            os_log("updated list is unreadable or invalid; using bundled", log: log, type: .error)
            return bundled
        }
        return updated
    }

    private static func isNewer(_ a: URL, than b: URL) -> Bool {
        let fm = FileManager.default
        guard let da = try? fm.attributesOfItem(atPath: a.path)[.modificationDate] as? Date,
              let db = try? fm.attributesOfItem(atPath: b.path)[.modificationDate] as? Date else { return false }
        return da > db
    }
}
