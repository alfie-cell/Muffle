// Renders Muffle's app icon from a single Material Symbols SVG glyph, with no SVG
// library: the file's <path d="…"> is parsed into an NSBezierPath (M L H V Q T C S Z,
// absolute and relative), which is all Material Symbols use.
//   swiftc -O tools/make_icon.swift -o /tmp/make_icon
//   /tmp/make_icon Muffle/Icon/shield.svg Muffle/Icon YouTube/Resources
// Produces Muffle/Icon/AppIcon.iconset (for iconutil), AppIcon-1024.png, and
// icon-{48,96,128}.png for the web extension. Look: macOS Big Sur style rounded
// square on Apple's 80 % grid, dark blue-grey gradient, white glyph.
import AppKit

let args = CommandLine.arguments
guard args.count == 4, let svg = try? String(contentsOfFile: args[1], encoding: .utf8) else {
    FileHandle.standardError.write("usage: make_icon glyph.svg iconOutDir webExtOutDir\n".data(using: .utf8)!)
    exit(1)
}
let iconDir = args[2], webDir = args[3]

// --- SVG path parsing -------------------------------------------------------------------

func attr(_ name: String, in text: String) -> String? {
    guard let r = text.range(of: "\(name)=\"") else { return nil }
    let rest = text[r.upperBound...]
    guard let end = rest.firstIndex(of: "\"") else { return nil }
    return String(rest[..<end])
}

guard let d = attr("d", in: svg) else { FileHandle.standardError.write("no <path d> in svg\n".data(using: .utf8)!); exit(1) }
let viewBox = (attr("viewBox", in: svg) ?? "0 0 24 24").split(separator: " ").compactMap { Double($0) }
guard viewBox.count == 4 else { exit(1) }
let vbX = CGFloat(viewBox[0]), vbY = CGFloat(viewBox[1]), vbW = CGFloat(viewBox[2]), vbH = CGFloat(viewBox[3])

func parsePath(_ d: String) -> NSBezierPath {
    let regex = try! NSRegularExpression(pattern: "[MmLlHhVvQqTtCcSsZz]|[-+]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?")
    let tokens = regex.matches(in: d, range: NSRange(d.startIndex..., in: d)).map { String(d[Range($0.range, in: d)!]) }
    let path = NSBezierPath()
    var i = 0
    var cmd: Character = "M"
    var cur = CGPoint.zero, start = CGPoint.zero
    var lastQ: CGPoint? = nil, lastC: CGPoint? = nil
    func num() -> CGFloat { defer { i += 1 }; return CGFloat(Double(tokens[i])!) }
    func pt(_ rel: Bool) -> CGPoint { let x = num(), y = num(); return rel ? CGPoint(x: cur.x + x, y: cur.y + y) : CGPoint(x: x, y: y) }
    func reflect(_ p: CGPoint?) -> CGPoint { guard let p = p else { return cur }; return CGPoint(x: 2 * cur.x - p.x, y: 2 * cur.y - p.y) }
    func quad(_ c: CGPoint, _ p: CGPoint) {
        let c1 = CGPoint(x: cur.x + 2 / 3 * (c.x - cur.x), y: cur.y + 2 / 3 * (c.y - cur.y))
        let c2 = CGPoint(x: p.x + 2 / 3 * (c.x - p.x), y: p.y + 2 / 3 * (c.y - p.y))
        path.curve(to: p, controlPoint1: c1, controlPoint2: c2)
    }
    while i < tokens.count {
        if let c = tokens[i].first, c.isLetter { cmd = c; i += 1 }
        let rel = cmd.isLowercase
        var q: CGPoint? = nil, cc: CGPoint? = nil
        switch cmd.uppercased() {
        case "M": let p = pt(rel); path.move(to: p); cur = p; start = p; cmd = rel ? "l" : "L"
        case "L": let p = pt(rel); path.line(to: p); cur = p
        case "H": let x = num(); cur = CGPoint(x: rel ? cur.x + x : x, y: cur.y); path.line(to: cur)
        case "V": let y = num(); cur = CGPoint(x: cur.x, y: rel ? cur.y + y : y); path.line(to: cur)
        case "Q": let c = pt(rel), p = pt(rel); quad(c, p); q = c; cur = p
        case "T": let c = reflect(lastQ); let p = pt(rel); quad(c, p); q = c; cur = p
        case "C": let c1 = pt(rel), c2 = pt(rel), p = pt(rel); path.curve(to: p, controlPoint1: c1, controlPoint2: c2); cc = c2; cur = p
        case "S": let c1 = reflect(lastC); let c2 = pt(rel), p = pt(rel); path.curve(to: p, controlPoint1: c1, controlPoint2: c2); cc = c2; cur = p
        case "Z": path.close(); cur = start
        default: FileHandle.standardError.write("unsupported path command \(cmd)\n".data(using: .utf8)!); exit(1)
        }
        lastQ = q; lastC = cc
    }
    return path
}
let glyphPath = parsePath(d)

// --- rendering -------------------------------------------------------------------------

func render(pixels: Int, fullBleed: Bool) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = ctx
    let cg = ctx.cgContext
    let s = CGFloat(pixels)
    cg.clear(CGRect(x: 0, y: 0, width: s, height: s))

    let inset = fullBleed ? 0 : s * 0.1
    let rect = CGRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let shape = NSBezierPath(roundedRect: rect, xRadius: rect.width * 0.2237, yRadius: rect.width * 0.2237)
    if !fullBleed {
        cg.saveGState()
        cg.setShadow(offset: CGSize(width: 0, height: -s * 0.012), blur: s * 0.025,
                     color: NSColor.black.withAlphaComponent(0.35).cgColor)
        NSColor(calibratedRed: 0.11, green: 0.14, blue: 0.22, alpha: 1).setFill()
        shape.fill()
        cg.restoreGState()
    }
    NSGradient(starting: NSColor(calibratedRed: 0.25, green: 0.33, blue: 0.50, alpha: 1),
               ending: NSColor(calibratedRed: 0.09, green: 0.12, blue: 0.21, alpha: 1))!
        .draw(in: shape, angle: -90)

    // Glyph: map the SVG viewBox (y down) onto a centred square (y up), fill white.
    let g = rect.width * 0.60
    let grect = CGRect(x: rect.midX - g / 2, y: rect.midY - g / 2 - rect.width * 0.015, width: g, height: g)
    let t = NSAffineTransform()
    t.translateX(by: grect.minX, yBy: grect.maxY)
    t.scaleX(by: g / vbW, yBy: -g / vbH)
    t.translateX(by: -vbX, yBy: -vbY)
    let p = glyphPath.copy() as! NSBezierPath
    p.transform(using: t as AffineTransform)
    p.windingRule = .nonZero
    NSColor.white.setFill()
    p.fill()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let iconset = iconDir + "/AppIcon.iconset"
try? FileManager.default.removeItem(atPath: iconset)
try! FileManager.default.createDirectory(atPath: iconset, withIntermediateDirectories: true)
for (points, scale) in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)] {
    let name = scale == 1 ? "icon_\(points)x\(points).png" : "icon_\(points)x\(points)@2x.png"
    try! render(pixels: points * scale, fullBleed: false).write(to: URL(fileURLWithPath: iconset + "/" + name))
}
for px in [48, 96, 128] {
    try! render(pixels: px, fullBleed: true).write(to: URL(fileURLWithPath: webDir + "/icon-\(px).png"))
}
try! render(pixels: 1024, fullBleed: false).write(to: URL(fileURLWithPath: iconDir + "/AppIcon-1024.png"))
try! render(pixels: 256, fullBleed: false).write(to: URL(fileURLWithPath: iconDir + "/preview-256.png"))
print("icons written")
