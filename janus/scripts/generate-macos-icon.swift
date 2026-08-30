import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let size = 1024
let outputURL = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? "build/icon.png")
let colorSpace = CGColorSpaceCreateDeviceRGB()
let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)

guard let context = CGContext(
  data: nil,
  width: size,
  height: size,
  bitsPerComponent: 8,
  bytesPerRow: size * 4,
  space: colorSpace,
  bitmapInfo: bitmapInfo.rawValue
) else {
  fatalError("Could not create icon bitmap context")
}

// CGContext starts with zeroed transparent pixels. Drawing directly into that
// buffer avoids the white RGB fringe produced by thumbnail-based SVG renders.
context.setAllowsAntialiasing(true)
context.setShouldAntialias(true)
context.setFillColor(CGColor(red: 0x11 / 255, green: 0x12 / 255, blue: 0x14 / 255, alpha: 1))
context.addPath(CGPath(
  roundedRect: CGRect(x: 32, y: 32, width: 960, height: 960),
  cornerWidth: 216,
  cornerHeight: 216,
  transform: nil
))
context.fillPath()

// 괄호와 축 { | } — DESIGN_SYSTEM.md §13. janus-symbol.svg와 같은 geometry,
// 아이콘에선 광학 보정으로 스트로크만 살짝 얇게(앱 96 → 아이콘 76).
func stroke(_ build: (CGMutablePath) -> Void) {
  let path = CGMutablePath()
  build(path)
  context.addPath(path)
  context.setStrokeColor(CGColor(red: 0xE6 / 255, green: 0xE8 / 255, blue: 0xEA / 255, alpha: 1))
  context.setLineWidth(76)
  context.setLineCap(.butt)
  context.setLineJoin(.miter)
  context.strokePath()
}

stroke { path in
  path.move(to: CGPoint(x: 384, y: 176))
  path.addLine(to: CGPoint(x: 208, y: 176))
  path.addLine(to: CGPoint(x: 208, y: 848))
  path.addLine(to: CGPoint(x: 384, y: 848))
}

stroke { path in
  path.move(to: CGPoint(x: 640, y: 176))
  path.addLine(to: CGPoint(x: 816, y: 176))
  path.addLine(to: CGPoint(x: 816, y: 848))
  path.addLine(to: CGPoint(x: 640, y: 848))
}

stroke { path in
  path.move(to: CGPoint(x: 512, y: 352))
  path.addLine(to: CGPoint(x: 512, y: 672))
}

guard let image = context.makeImage(),
      let destination = CGImageDestinationCreateWithURL(
        outputURL as CFURL,
        UTType.png.identifier as CFString,
        1,
        nil
      ) else {
  fatalError("Could not create icon output")
}

CGImageDestinationAddImage(destination, image, nil)
guard CGImageDestinationFinalize(destination) else {
  fatalError("Could not write icon PNG")
}
