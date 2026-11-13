// Measures how long WebKit takes to compile a Safari content blocker JSON file
// on this machine. Safari gives an extension about 120 s; stay well under.
//   swiftc -O -framework WebKit tools/compile_time.swift -o /tmp/compile_time
//   /tmp/compile_time Blocker/blockerList.json [more.json ...]
import Foundation
import WebKit

let storeURL = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("muffle-compile-test")
try? FileManager.default.createDirectory(at: storeURL, withIntermediateDirectories: true)
let store = WKContentRuleListStore(url: storeURL)!

for path in CommandLine.arguments.dropFirst() {
    guard let json = try? String(contentsOfFile: path, encoding: .utf8) else { print("\(path): cannot read"); continue }
    let rules = (try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [Any])?.count ?? -1
    let start = Date()
    var done = false
    store.compileContentRuleList(forIdentifier: UUID().uuidString, encodedContentRuleList: json) { _, error in
        let secs = Date().timeIntervalSince(start)
        let status = error.map { "ERROR \($0.localizedDescription)" } ?? "ok"
        print("\((path as NSString).lastPathComponent): \(rules) rules, \(String(format: "%.1f", secs)) s, \(status)")
        fflush(stdout)
        done = true
    }
    while !done { RunLoop.main.run(mode: .default, before: Date(timeIntervalSinceNow: 0.1)) }
}
