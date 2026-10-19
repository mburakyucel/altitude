// Guests on Apple's Virtualization framework: macOS for scripts/installation_macos_vm.py, and Linux for
// scripts/linux_vm_mac.py.
//
//   macos_vm latest                                   Apple's newest restore image this Mac supports, as JSON
//   macos_vm install IPSW BUNDLE DISK_GIB             a new guest in BUNDLE from the restore image
//   macos_vm run BUNDLE SOCKET CPUS MEMORY_GIB         boots BUNDLE headless with a NAT network card
//   macos_vm linux KERNEL INITRD COMMAND_LINE DISK SEED CONSOLE SOCKET CPUS MEMORY_GIB
//                                                     boots a Linux kernel headless with DISK as its first disk,
//                                                     SEED as a read-only second disk, the console written to
//                                                     CONSOLE and a NAT network card
//   macos_vm relay PORT                               inside a macOS guest: host-guest socket PORT to sshd
//
// `run` and `linux` relay each connection to the Unix socket SOCKET to the guest's host-guest (vsock) port 22, so
// the host reaches the guest without a network. It reads commands on stdin: `unplug` disconnects the network
// card, `click X Y` clicks the guest's screen at point X, Y from its top left, `type TEXT` types TEXT (letters,
// digits, `-` and `_`) and Return on the guest's keyboard, as a person at its screen does, `stop` powers the guest
// off; end of input also powers it off. It prints `started <mac>`, `unplugged`, `clicked`, `typed`, and `stopped`
// or `failed <reason>` before exiting.
import AppKit
import Darwin
import Foundation
import Virtualization

setvbuf(stdout, nil, _IOLBF, 0)

func fail(_ message: String) -> Never {
    print("failed \(message)")
    exit(1)
}

func json(_ value: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

struct Bundle {
    let url: URL
    var disk: URL { url.appendingPathComponent("disk.img") }
    var aux: URL { url.appendingPathComponent("aux.img") }
    var model: URL { url.appendingPathComponent("hardware-model") }
    var machine: URL { url.appendingPathComponent("machine-id") }
}

func linuxConfiguration(kernel: URL, initrd: URL, commandLine: String, disk: URL, seed: URL, console: URL,
                        cpus: Int, memoryGiB: UInt64) throws -> VZVirtualMachineConfiguration {
    let boot = VZLinuxBootLoader(kernelURL: kernel)
    boot.initialRamdiskURL = initrd
    boot.commandLine = commandLine
    let config = VZVirtualMachineConfiguration()
    config.platform = VZGenericPlatformConfiguration()
    config.bootLoader = boot
    config.cpuCount = cpus
    config.memorySize = memoryGiB << 30
    // Host-cached, fsync-synchronized writes: with the automatic caching mode, a Linux guest read garbage from files it
    // never wrote ("bad marshal data" from a system Python module), the corruption Lima and vfkit also fix this way.
    let root = try VZDiskImageStorageDeviceAttachment(url: disk, readOnly: false, cachingMode: .cached,
                                                      synchronizationMode: .fsync)
    config.storageDevices = [
        VZVirtioBlockDeviceConfiguration(attachment: root),
        VZVirtioBlockDeviceConfiguration(attachment: try VZDiskImageStorageDeviceAttachment(url: seed, readOnly: true)),
    ]
    let serial = VZVirtioConsoleDeviceSerialPortConfiguration()
    serial.attachment = try VZFileSerialPortAttachment(url: console, append: true)
    config.serialPorts = [serial]
    let network = VZVirtioNetworkDeviceConfiguration()
    network.attachment = VZNATNetworkDeviceAttachment()
    network.macAddress = VZMACAddress.randomLocallyAdministered()
    config.networkDevices = [network]
    config.socketDevices = [VZVirtioSocketDeviceConfiguration()]
    // No entropy device: Ubuntu 24.04's arm64 kernel panicked reading it ("corrupted stack end detected inside
    // scheduler" in virtio_rng), and its random pool is ready at boot without it.
    try config.validate()
    return config
}

func configuration(_ bundle: Bundle, cpus: Int, memoryGiB: UInt64) throws -> VZVirtualMachineConfiguration {
    let platform = VZMacPlatformConfiguration()
    guard let model = VZMacHardwareModel(dataRepresentation: try Data(contentsOf: bundle.model)), model.isSupported,
          let machine = VZMacMachineIdentifier(dataRepresentation: try Data(contentsOf: bundle.machine)) else {
        fail("the guest's hardware model is not supported on this Mac")
    }
    platform.hardwareModel = model
    platform.machineIdentifier = machine
    platform.auxiliaryStorage = VZMacAuxiliaryStorage(url: bundle.aux)
    let config = VZVirtualMachineConfiguration()
    config.platform = platform
    config.bootLoader = VZMacOSBootLoader()
    config.cpuCount = cpus
    config.memorySize = memoryGiB << 30
    let graphics = VZMacGraphicsDeviceConfiguration()
    graphics.displays = [VZMacGraphicsDisplayConfiguration(widthInPixels: 1920, heightInPixels: 1200, pixelsPerInch: 80)]
    config.graphicsDevices = [graphics]
    config.keyboards = [VZUSBKeyboardConfiguration()]
    config.pointingDevices = [VZMacTrackpadConfiguration()]
    config.storageDevices = [VZVirtioBlockDeviceConfiguration(
        attachment: try VZDiskImageStorageDeviceAttachment(url: bundle.disk, readOnly: false))]
    let network = VZVirtioNetworkDeviceConfiguration()
    network.attachment = VZNATNetworkDeviceAttachment()
    network.macAddress = VZMACAddress.randomLocallyAdministered()
    config.networkDevices = [network]
    config.socketDevices = [VZVirtioSocketDeviceConfiguration()]
    config.entropyDevices = [VZVirtioEntropyDeviceConfiguration()]
    try config.validate()
    return config
}

func latest() {
    VZMacOSRestoreImage.fetchLatestSupported { result in
        switch result {
        case .failure(let error):
            fail("Apple's restore image catalog: \(error.localizedDescription)")
        case .success(let image):
            let version = image.operatingSystemVersion
            json(["url": image.url.absoluteString, "build": image.buildVersion,
                  "version": "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"])
            exit(0)
        }
    }
}

// Kept alive for the life of the process.
var machine: VZVirtualMachine?
var installer: VZMacOSInstaller?
var observation: NSKeyValueObservation?

func install(ipsw: URL, bundle: Bundle, diskGiB: UInt64) {
    VZMacOSRestoreImage.load(from: ipsw) { result in
        DispatchQueue.main.async {
            guard case .success(let image) = result else { fail("cannot read the restore image") }
            guard let requirements = image.mostFeaturefulSupportedConfiguration, requirements.hardwareModel.isSupported else {
                fail("this Mac cannot run macOS \(image.buildVersion)")
            }
            do {
                try FileManager.default.createDirectory(at: bundle.url, withIntermediateDirectories: false)
                _ = try VZMacAuxiliaryStorage(creatingStorageAt: bundle.aux, hardwareModel: requirements.hardwareModel)
                try requirements.hardwareModel.dataRepresentation.write(to: bundle.model)
                try VZMacMachineIdentifier().dataRepresentation.write(to: bundle.machine)
                // A sparse file: only what the guest writes takes space.
                guard FileManager.default.createFile(atPath: bundle.disk.path, contents: nil) else { fail("cannot create the disk") }
                let handle = try FileHandle(forWritingTo: bundle.disk)
                try handle.truncate(atOffset: diskGiB << 30)
                try handle.close()
                let vm = VZVirtualMachine(configuration: try configuration(
                    bundle, cpus: max(requirements.minimumSupportedCPUCount, 4),
                    memoryGiB: max(requirements.minimumSupportedMemorySize >> 30, 4)))
                machine = vm
                let job = VZMacOSInstaller(virtualMachine: vm, restoringFromImageAt: ipsw)
                installer = job
                var reported = -1
                observation = job.progress.observe(\.fractionCompleted) { progress, _ in
                    let percent = Int(progress.fractionCompleted * 100)
                    if percent / 10 != reported / 10 {
                        reported = percent
                        print("installing \(percent)%")
                    }
                }
                job.install { result in
                    if case .failure(let error) = result { fail("installation: \(error.localizedDescription)") }
                    print("installed")
                    exit(0)
                }
            } catch {
                fail("\(error.localizedDescription)")
            }
        }
    }
}

/// Copies one direction until end of input, then half-closes the other side.
func pump(from source: Int32, to target: Int32) {
    var buffer = [UInt8](repeating: 0, count: 65536)
    while true {
        let count = read(source, &buffer, buffer.count)
        if count <= 0 { break }
        var offset = 0
        while offset < count {
            let written = buffer[offset..<count].withUnsafeBytes { write(target, $0.baseAddress, count - offset) }
            if written <= 0 { shutdown(source, SHUT_RD); return }
            offset += written
        }
    }
    shutdown(target, SHUT_WR)
}

/// Joins two connected descriptors; `done` runs once both directions have ended.
func splice(_ a: Int32, _ b: Int32, done: @escaping () -> Void) {
    let group = DispatchGroup()
    for (source, target) in [(a, b), (b, a)] {
        group.enter()
        Thread.detachNewThread {
            pump(from: source, to: target)
            group.leave()
        }
    }
    group.notify(queue: .global()) { done() }
}

func listenUnix(_ path: String) -> Int32 {
    unlink(path)
    let server = socket(AF_UNIX, SOCK_STREAM, 0)
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(path.utf8)
    guard bytes.count < MemoryLayout.size(ofValue: address.sun_path) else { fail("socket path too long: \(path)") }
    withUnsafeMutableBytes(of: &address.sun_path) { raw in
        for (index, byte) in bytes.enumerated() { raw[index] = byte }
    }
    let bound = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(server, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
    }
    guard bound == 0, chmod(path, 0o600) == 0, listen(server, 16) == 0 else { fail("cannot listen on \(path)") }
    return server
}

final class Delegate: NSObject, VZVirtualMachineDelegate {
    func guestDidStop(_ virtualMachine: VZVirtualMachine) {
        print("stopped")
        exit(0)
    }

    func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        fail("the guest stopped: \(error.localizedDescription)")
    }

    func virtualMachine(_ virtualMachine: VZVirtualMachine, networkDevice: VZNetworkDevice,
                        attachmentWasDisconnectedWithError error: Error) {
        print("network-disconnected \(error.localizedDescription)")
    }
}
let delegate = Delegate()

func powerOff() {
    guard let vm = machine, vm.canStop else { print("stopped"); exit(0) }
    vm.stop { _ in
        print("stopped")
        exit(0)
    }
}

// Apple's US keyboard key codes; the guest reads keys, not characters.
let keyCodes: [Character: UInt16] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26,
    "-": 27, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "\r": 36, "l": 37, "j": 38, "k": 40, "n": 45,
    "m": 46]
let shift: UInt16 = 56
// Left Shift as a real keyboard reports it: the device-independent flag with the left key's own bit.
let shifted = NSEvent.ModifierFlags(rawValue: NSEvent.ModifierFlags.shift.rawValue | 0x2)
var screen: VZVirtualMachineView?
var holder: NSWindow?

/// The view a person types into and clicks, the size of the guest's display, inside a window far off every screen
/// that is never activated, so this Mac's own keyboard focus stays where it is.
func display(of vm: VZVirtualMachine) -> VZVirtualMachineView {
    if let view = screen { return view }
    _ = NSApplication.shared
    let view = VZVirtualMachineView(frame: NSRect(x: 0, y: 0, width: 1920, height: 1200))
    let window = NSWindow(contentRect: NSRect(x: -20000, y: -20000, width: 1920, height: 1200),
                          styleMask: [.borderless], backing: .buffered, defer: false)
    window.contentView = view
    window.orderFrontRegardless()
    window.makeFirstResponder(view)
    view.virtualMachine = vm
    screen = view
    holder = window
    return view
}

/// Moves the guest's pointer to X, Y, counted from the top left of its display, and clicks there.
func click(_ x: Double, _ y: Double, in vm: VZVirtualMachine) {
    let view = display(of: vm)
    let point = view.convert(NSPoint(x: x, y: view.bounds.height - y), to: nil)
    for kind in [NSEvent.EventType.mouseMoved, .leftMouseDown, .leftMouseUp] {
        guard let event = NSEvent.mouseEvent(with: kind, location: point, modifierFlags: [],
                                             timestamp: ProcessInfo.processInfo.systemUptime,
                                             windowNumber: holder?.windowNumber ?? 0, context: nil, eventNumber: 0,
                                             clickCount: 1, pressure: kind == .leftMouseDown ? 1 : 0) else {
            print("failed no mouse event")
            return
        }
        switch kind {
        case .mouseMoved: view.mouseMoved(with: event)
        case .leftMouseDown: view.mouseDown(with: event)
        default: view.mouseUp(with: event)
        }
        Thread.sleep(forTimeInterval: 0.2)
    }
    print("clicked")
}

/// Presses each key of TEXT and then Return through the view a person types into, holding Shift for capitals and
/// `_`, on the main queue.
func type(_ text: String, into vm: VZVirtualMachine) {
    let view = display(of: vm)
    func press(_ kind: NSEvent.EventType, _ code: UInt16, _ flags: NSEvent.ModifierFlags, _ character: String) -> Bool {
        guard let event = NSEvent.keyEvent(with: kind, location: .zero, modifierFlags: flags,
                                           timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: 0,
                                           context: nil, characters: character, charactersIgnoringModifiers: character,
                                           isARepeat: false, keyCode: code) else { return false }
        switch kind {
        case .keyDown: view.keyDown(with: event)
        case .keyUp: view.keyUp(with: event)
        default: view.flagsChanged(with: event)
        }
        return true
    }
    for character in text + "\r" {
        let capital = character.isUppercase || character == "_"
        let base = character == "_" ? "-" : Character(character.lowercased())
        guard let code = keyCodes[base] else { print("failed cannot type \(character)"); return }
        let flags: NSEvent.ModifierFlags = capital ? shifted : []
        let typed = (!capital || press(.flagsChanged, shift, shifted, ""))
            && press(.keyDown, code, flags, String(character)) && press(.keyUp, code, flags, String(character))
            && (!capital || press(.flagsChanged, shift, [], ""))
        guard typed else { print("failed no key event"); return }
        Thread.sleep(forTimeInterval: 0.05)
    }
    print("typed")
}

func run(socketPath: String, _ make: () throws -> VZVirtualMachineConfiguration) {
    let config: VZVirtualMachineConfiguration
    do { config = try make() } catch { fail("\(error.localizedDescription)") }
    let vm = VZVirtualMachine(configuration: config)
    machine = vm
    vm.delegate = delegate
    vm.start { result in
        if case .failure(let error) = result { fail("cannot start the guest: \(error.localizedDescription)") }
        let mac = config.networkDevices[0].macAddress.string
        let server = listenUnix(socketPath)
        Thread.detachNewThread {
            while true {
                let client = accept(server, nil, nil)
                if client < 0 { continue }
                DispatchQueue.main.async {
                    guard let device = vm.socketDevices.first as? VZVirtioSocketDevice else { close(client); return }
                    device.connect(toPort: 22) { result in
                        guard case .success(let connection) = result else { close(client); return }
                        // The connection object owns the guest end; it lives until both directions end.
                        splice(client, connection.fileDescriptor) {
                            close(client)
                            connection.close()
                        }
                    }
                }
            }
        }
        print("started \(mac)")
    }
    Thread.detachNewThread {
        while let line = readLine() {
            if line.hasPrefix("type ") {
                DispatchQueue.main.async { type(String(line.dropFirst(5)), into: vm) }
                continue
            }
            let words = line.split(separator: " ")
            if words.count == 3, words[0] == "click", let x = Double(words[1]), let y = Double(words[2]) {
                DispatchQueue.main.async { click(x, y, in: vm) }
                continue
            }
            switch line.trimmingCharacters(in: .whitespaces) {
            case "unplug":
                DispatchQueue.main.async {
                    vm.networkDevices.first?.attachment = nil
                    print("unplugged")
                }
            case "stop":
                DispatchQueue.main.async { powerOff() }
            default:
                print("unknown command \(line)")
            }
        }
        DispatchQueue.main.async { powerOff() }
    }
}

/// Inside the guest: each connection to host-guest PORT is joined to sshd on the guest's loopback.
func relay(port: UInt32) -> Never {
    let server = socket(AF_VSOCK, SOCK_STREAM, 0)
    guard server >= 0 else { fail("no host-guest sockets in this system") }
    var address = sockaddr_vm(svm_len: UInt8(MemoryLayout<sockaddr_vm>.size), svm_family: sa_family_t(AF_VSOCK),
                              svm_reserved1: 0, svm_port: port, svm_cid: UInt32.max)
    let bound = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(server, $0, socklen_t(MemoryLayout<sockaddr_vm>.size)) }
    }
    guard bound == 0, listen(server, 16) == 0 else { fail("cannot listen on host-guest port \(port)") }
    while true {
        let client = accept(server, nil, nil)
        if client < 0 { continue }
        let sshd = socket(AF_INET, SOCK_STREAM, 0)
        var target = sockaddr_in(sin_len: UInt8(MemoryLayout<sockaddr_in>.size), sin_family: sa_family_t(AF_INET),
                                 sin_port: UInt16(22).bigEndian, sin_addr: in_addr(s_addr: inet_addr("127.0.0.1")),
                                 sin_zero: (0, 0, 0, 0, 0, 0, 0, 0))
        let connected = withUnsafePointer(to: &target) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(sshd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        if connected != 0 {
            close(client)
            close(sshd)
            continue
        }
        splice(client, sshd) {
            close(client)
            close(sshd)
        }
    }
}

let arguments = CommandLine.arguments
signal(SIGPIPE, SIG_IGN)
switch (arguments.count > 1 ? arguments[1] : "", arguments.count) {
case ("latest", 2):
    latest()
case ("install", 5):
    install(ipsw: URL(fileURLWithPath: arguments[2]), bundle: Bundle(url: URL(fileURLWithPath: arguments[3])),
            diskGiB: UInt64(arguments[4]) ?? 64)
case ("run", 6):
    run(socketPath: arguments[3]) {
        try configuration(Bundle(url: URL(fileURLWithPath: arguments[2])), cpus: Int(arguments[4]) ?? 4,
                          memoryGiB: UInt64(arguments[5]) ?? 4)
    }
case ("linux", 11):
    run(socketPath: arguments[8]) {
        let file = { (index: Int) in URL(fileURLWithPath: arguments[index]) }
        return try linuxConfiguration(kernel: file(2), initrd: file(3), commandLine: arguments[4], disk: file(5),
                                      seed: file(6), console: file(7), cpus: Int(arguments[9]) ?? 2,
                                      memoryGiB: UInt64(arguments[10]) ?? 4)
    }
case ("relay", 3):
    relay(port: UInt32(arguments[2]) ?? 22)
default:
    FileHandle.standardError.write("usage: macos_vm latest | install IPSW BUNDLE DISK_GIB | run BUNDLE SOCKET CPUS MEMORY_GIB | linux KERNEL INITRD COMMAND_LINE DISK SEED CONSOLE SOCKET CPUS MEMORY_GIB | relay PORT\n".data(using: .utf8)!)
    exit(2)
}
RunLoop.main.run()  // the main thread itself serves the main queue, as AppKit requires
