// window-record -- record ONE game window to an H.264 .mov with ScreenCaptureKit (macOS 12.3+).
//
//   window-record --out run.mov [--title-prefix Minecraft] [--owner java] [--wait 600] [--fps 30]
//   window-record --list
//
// Why not `screencapture -v`: that records the whole display, so a locked screen or a sleeping display records
// the lock screen (measured: a black file behind a locked Mac). A single-window ScreenCaptureKit stream gets the
// window's own pixels, which the game keeps rendering behind the lock as long as the display is awake
// (`caffeinate -d -u`). playtest.py --video compiles this file once (swiftc, cached by its hash) and uses it first.
//
// It waits up to --wait seconds for a window whose title starts with --title-prefix and whose owning process name
// contains --owner (both case-insensitive), records it until SIGINT/SIGTERM or until the window goes away, then
// finalises the file. Exit 0 when frames were written; 3 when no window appeared; 4 when capture was refused (no
// Screen Recording permission for the app this runs inside); 5 when no frame arrived. Standard frameworks only.

import AppKit
import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

func say(_ s: String) { FileHandle.standardError.write(("window-record: " + s + "\n").data(using: .utf8)!) }

var outPath: String?
var titlePrefix = "minecraft"
var owner = "java"
var waitSecs = 600.0
var fps = 30
var listOnly = false
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let a = args.removeFirst()
    func val() -> String { if args.isEmpty { say("\(a) needs a value"); exit(2) }; return args.removeFirst() }
    switch a {
    case "--out": outPath = val()
    case "--title-prefix": titlePrefix = val().lowercased()
    case "--owner": owner = val().lowercased()
    case "--wait": waitSecs = Double(val()) ?? 600
    case "--fps": fps = max(1, min(60, Int(val()) ?? 30))
    case "--list": listOnly = true
    default: say("unknown argument \(a)"); exit(2)
    }
}
if !listOnly && outPath == nil { say("usage: window-record --out FILE.mov [--title-prefix T] [--owner P] [--wait S] [--fps N] | --list"); exit(2) }

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let writer: AVAssetWriter
    let input: AVAssetWriterInput
    var started = false
    var frames = 0
    var stream: SCStream?
    let done = DispatchSemaphore(value: 0)
    var finished = false
    let lock = NSLock()

    init(url: URL, width: Int, height: Int) throws {
        try? FileManager.default.removeItem(at: url)
        writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width, AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 12_000_000,
                                              AVVideoExpectedSourceFrameRateKey: fps],
        ])
        input.expectsMediaDataInRealTime = true
        writer.add(input)
        super.init()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, sb.isValid, CMSampleBufferGetImageBuffer(sb) != nil else { return }
        // Only complete frames carry pixels; idle/blank ones repeat the last frame and have no image to write.
        if let att = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
           let raw = att.first?[.status] as? Int, let st = SCFrameStatus(rawValue: raw), st != .complete { return }
        lock.lock(); defer { lock.unlock() }
        if finished { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sb)
        if !started {
            guard writer.startWriting() else { say("writer failed: \(writer.error?.localizedDescription ?? "?")"); return }
            writer.startSession(atSourceTime: pts)
            started = true
        }
        if input.isReadyForMoreMediaData, input.append(sb) { frames += 1 }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        say("stream stopped: \(error.localizedDescription)")   // the window closed: the game quit
        finish()
    }

    func finish() {
        lock.lock()
        if finished { lock.unlock(); return }
        finished = true
        lock.unlock()
        let s = stream
        stream = nil
        s?.stopCapture { _ in }
        if started {
            input.markAsFinished()
            writer.finishWriting { self.done.signal() }
        } else {
            writer.cancelWriting()
            done.signal()
        }
    }
}

func shareable() async throws -> SCShareableContent {
    try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
}

func pick(_ c: SCShareableContent) -> SCWindow? {
    c.windows.filter { w in
        let t = (w.title ?? "").lowercased()
        let o = (w.owningApplication?.applicationName ?? "").lowercased()
        return t.hasPrefix(titlePrefix) && o.contains(owner) && w.frame.width >= 200 && w.frame.height >= 150
    }.max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }
}

func main() async -> Int32 {
    if listOnly {
        do {
            for w in try await shareable().windows where (w.title ?? "") != "" {
                print("\(w.windowID)\t\(w.owningApplication?.applicationName ?? "?")\t\"\(w.title ?? "")\"\t\(Int(w.frame.width))x\(Int(w.frame.height))")
            }
            return 0
        } catch { say("capture refused: \(error.localizedDescription)"); return 4 }
    }
    let deadline = Date().addingTimeInterval(waitSecs)
    var win: SCWindow?
    while win == nil {
        do { win = pick(try await shareable()) } catch { say("capture refused: \(error.localizedDescription)"); return 4 }
        if win != nil { break }
        if Date() > deadline { say("no window titled \(titlePrefix)* owned by *\(owner)* within \(Int(waitSecs))s"); return 3 }
        try? await Task.sleep(nanoseconds: 500_000_000)
    }
    guard let w = win else { return 3 }
    let filter = SCContentFilter(desktopIndependentWindow: w)
    let scale: CGFloat = 2   // Retina backing pixels; the encoder downsizes nothing, the review encode does
    let cfg = SCStreamConfiguration()
    cfg.width = Int(w.frame.width * scale) & ~1
    cfg.height = Int(w.frame.height * scale) & ~1
    cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
    cfg.showsCursor = false
    cfg.queueDepth = 6
    cfg.pixelFormat = kCVPixelFormatType_32BGRA
    let rec: Recorder
    do { rec = try Recorder(url: URL(fileURLWithPath: outPath!), width: cfg.width, height: cfg.height) }
    catch { say("cannot write \(outPath!): \(error.localizedDescription)"); return 2 }
    let stream = SCStream(filter: filter, configuration: cfg, delegate: rec)
    rec.stream = stream
    do {
        try stream.addStreamOutput(rec, type: .screen, sampleHandlerQueue: DispatchQueue(label: "frames"))
        try await stream.startCapture()
    } catch { say("capture refused: \(error.localizedDescription)"); return 4 }
    say("recording \"\(w.title ?? "")\" (\(cfg.width)x\(cfg.height) @\(fps)) -> \(outPath!)")
    for sig in [SIGINT, SIGTERM] {
        signal(sig, SIG_IGN)
        let src = DispatchSource.makeSignalSource(signal: sig, queue: .global())
        src.setEventHandler { rec.finish() }
        src.resume()
        sources.append(src)
    }
    await withCheckedContinuation { (k: CheckedContinuation<Void, Never>) in
        DispatchQueue.global().async { rec.done.wait(); k.resume() }
    }
    say("stopped: \(rec.frames) frames")
    return rec.frames > 0 ? 0 : 5
}

var sources: [DispatchSourceSignal] = []
// A window-server connection first: ScreenCaptureKit asserts (CGS_REQUIRE_INIT) in a bare command-line process.
NSApplication.shared.setActivationPolicy(.prohibited)
let code = await main()
exit(code)
