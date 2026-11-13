import AppKit
import SafariServices
import os.log

/// Native side of the YouTube extension. The only message it answers is
/// `{ "type": "getConfig" }`: it returns the YouTube ad-removal data that
/// `make update` writes into this extension's sandbox container
/// (~/Library/Containers/<bundle id>/Data/Library/Application Support/Muffle/yt-config.json),
/// or null when there is none. The JavaScript side validates the result again.
final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    private static let log = OSLog(subsystem: "com.example.muffle", category: "youtube")

    func beginRequest(with context: NSExtensionContext) {
        let item = context.inputItems.first as? NSExtensionItem
        let message = item?.userInfo?[SFExtensionMessageKey] as? [String: Any]
        var reply: [String: Any] = ["ok": true]
        if message?["type"] as? String == "getConfig" {
            reply = ["config": Self.loadConfig() ?? NSNull()]
        }
        let response = NSExtensionItem()
        response.userInfo = [SFExtensionMessageKey: reply]
        context.completeRequest(returningItems: [response], completionHandler: nil)
    }

    private static func loadConfig() -> [String: Any]? {
        guard let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return nil
        }
        let url = support.appendingPathComponent("Muffle/yt-config.json")
        guard let data = try? Data(contentsOf: url) else {
            os_log("no yt-config.json in container; extension uses built-in defaults", log: log, type: .default)
            return nil
        }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["schema"] as? Int == 1,
              let keys = object["keys"] as? [String] else {
            os_log("yt-config.json is invalid; ignored", log: log, type: .error)
            return nil
        }
        os_log("yt-config served: %{public}d keys, generated %{public}@", log: log, type: .default,
               keys.count, (object["generated"] as? String) ?? "?")
        return object
    }
}
