// Encode the recorder's frames into an H.264 MP4 with AVFoundation (no ffmpeg needed on macOS).
// Usage: swift showcase/encode.swift MANIFEST OUT.mp4 [FPS] [BITRATE]   (bits per second, default 2.5 Mbit/s: UI
// screens are mostly still, and a 3-minute video should stay well under the 100 MB a static host takes per file)
// MANIFEST lines, tab-separated, paths relative to the manifest's folder:
//   frame <png> <count>   show this image for <count> frames
//   fade <png> <count>    cross-fade from the previous image to this one over <count> frames
import AVFoundation
import CoreGraphics
import Foundation
import ImageIO

let args = CommandLine.arguments
guard args.count >= 3 else { fatalError("usage: encode.swift MANIFEST OUT.mp4 [FPS] [BITRATE]") }
let manifest = URL(fileURLWithPath: args[1])
let out = URL(fileURLWithPath: args[2])
let fps = Int32(args.count > 3 ? Int(args[3])! : 30)
let bitrate = args.count > 4 ? Int(args[4])! : 2_500_000
let base = manifest.deletingLastPathComponent()

func load(_ name: String) -> CGImage {
    let url = base.appendingPathComponent(name)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { fatalError("cannot read \(url.path)") }
    return img
}

let lines = try! String(contentsOf: manifest, encoding: .utf8).split(separator: "\n").map { $0.split(separator: "\t").map(String.init) }
guard let first = lines.first else { fatalError("empty manifest") }
let probe = load(first[1])
let W = probe.width, H = probe.height

try? FileManager.default.removeItem(at: out)
let writer = try! AVAssetWriter(outputURL: out, fileType: .mp4)
let settings: [String: Any] = [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: W, AVVideoHeightKey: H,
    AVVideoCompressionPropertiesKey: [
        AVVideoAverageBitRateKey: bitrate,
        AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
        AVVideoMaxKeyFrameIntervalKey: Int(fps) * 2,
    ],
]
let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
    kCVPixelBufferWidthKey as String: W, kCVPixelBufferHeightKey as String: H,
])
writer.add(input)
writer.startWriting()
writer.startSession(atSourceTime: .zero)

var frameNo: Int64 = 0
func append(_ draw: (CGContext) -> Void) {
    var pb: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &pb)
    let buf = pb!
    CVPixelBufferLockBaseAddress(buf, [])
    let ctx = CGContext(data: CVPixelBufferGetBaseAddress(buf), width: W, height: H, bitsPerComponent: 8,
                        bytesPerRow: CVPixelBufferGetBytesPerRow(buf), space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
    draw(ctx)
    CVPixelBufferUnlockBaseAddress(buf, [])
    while !input.isReadyForMoreMediaData { usleep(2000) }
    adaptor.append(buf, withPresentationTime: CMTime(value: frameNo, timescale: fps))
    frameNo += 1
}

let rect = CGRect(x: 0, y: 0, width: W, height: H)
var prev: CGImage? = nil
for parts in lines where parts.count == 3 {
    let img = load(parts[1])
    let n = Int(parts[2])!
    if parts[0] == "fade", let a = prev {
        for i in 1...max(n, 1) {
            let t = CGFloat(i) / CGFloat(max(n, 1))
            let e = t * t * (3 - 2 * t)   // smoothstep
            append { ctx in
                ctx.draw(a, in: rect)
                ctx.setAlpha(e)
                ctx.draw(img, in: rect)
            }
        }
    } else {
        for _ in 0..<n { append { $0.draw(img, in: rect) } }
    }
    prev = img
}
input.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
if writer.status != .completed { fatalError("writer failed: \(String(describing: writer.error))") }
print("wrote \(out.path): \(frameNo) frames, \(Double(frameNo) / Double(fps)) s, \(W)x\(H)")
