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
context.setFillColor(CGColor(red: 0x10 / 255, green: 0x11 / 255, blue: 0x12 / 255, alpha: 1))
context.addPath(CGPath(
  roundedRect: CGRect(x: 32, y: 32, width: 960, height: 960),
  cornerWidth: 216,
  cornerHeight: 216,
  transform: nil
))
context.fillPath()

func stroke(_ build: (CGMutablePath) -> Void) {
  let path = CGMutablePath()
  build(path)
  context.addPath(path)
  context.setStrokeColor(CGColor(red: 0xF0 / 255, green: 0xF1 / 255, blue: 0xF1 / 255, alpha: 1))
  context.setLineWidth(44)
  context.setLineCap(.round)
  context.setLineJoin(.round)
  context.strokePath()
}

stroke { path in
  path.move(to: CGPoint(x: 310, y: 220))
  path.addCurve(to: CGPoint(x: 270, y: 260), control1: CGPoint(x: 286, y: 220), control2: CGPoint(x: 270, y: 236))
  path.addLine(to: CGPoint(x: 270, y: 362))
  path.addCurve(to: CGPoint(x: 242, y: 438), control1: CGPoint(x: 270, y: 392), control2: CGPoint(x: 260, y: 417))
  path.addCurve(to: CGPoint(x: 270, y: 504), control1: CGPoint(x: 260, y: 451), control2: CGPoint(x: 270, y: 476))
  path.addCurve(to: CGPoint(x: 242, y: 578), control1: CGPoint(x: 270, y: 533), control2: CGPoint(x: 260, y: 557))
  path.addCurve(to: CGPoint(x: 270, y: 646), control1: CGPoint(x: 260, y: 591), control2: CGPoint(x: 270, y: 617))
  path.addLine(to: CGPoint(x: 270, y: 764))
  path.addCurve(to: CGPoint(x: 310, y: 804), control1: CGPoint(x: 270, y: 788), control2: CGPoint(x: 286, y: 804))
  path.addLine(to: CGPoint(x: 366, y: 804))
}

stroke { path in
  path.move(to: CGPoint(x: 714, y: 220))
  path.addCurve(to: CGPoint(x: 754, y: 260), control1: CGPoint(x: 738, y: 220), control2: CGPoint(x: 754, y: 236))
  path.addLine(to: CGPoint(x: 754, y: 362))
  path.addCurve(to: CGPoint(x: 782, y: 438), control1: CGPoint(x: 754, y: 392), control2: CGPoint(x: 764, y: 417))
  path.addCurve(to: CGPoint(x: 754, y: 504), control1: CGPoint(x: 764, y: 451), control2: CGPoint(x: 754, y: 476))
  path.addCurve(to: CGPoint(x: 782, y: 578), control1: CGPoint(x: 754, y: 533), control2: CGPoint(x: 764, y: 557))
  path.addCurve(to: CGPoint(x: 754, y: 646), control1: CGPoint(x: 764, y: 591), control2: CGPoint(x: 754, y: 617))
  path.addLine(to: CGPoint(x: 754, y: 764))
  path.addCurve(to: CGPoint(x: 714, y: 804), control1: CGPoint(x: 754, y: 788), control2: CGPoint(x: 738, y: 804))
  path.addLine(to: CGPoint(x: 658, y: 804))
}

stroke { path in
  path.move(to: CGPoint(x: 512, y: 384))
  path.addLine(to: CGPoint(x: 512, y: 640))
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
