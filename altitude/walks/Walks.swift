// The native walks of a macOS validation run's Simulator iPhone: Apple's UI testing taps what the relay cannot reach,
// Safari's own menus, the Home Screen and Settings. altd builds this bundle from its own code and runs one walk at a
// time on the run's private phone (altitude/simulator.py). Every step is written to walk.json as it ends, completed
// or not reachable with the reason, with a screenshot; a step that is not reached ends the walk. Waits are bounded
// and nothing is force-unwrapped, so the runner ends its walk instead of crashing.
// See docs/DEVELOPMENT.md#ios-simulator-runs.
import XCTest

struct NotReachable: Error {
    let reason: String
}

final class Walks: XCTestCase {
    let env = ProcessInfo.processInfo.environment
    let safari = XCUIApplication(bundleIdentifier: "com.apple.mobilesafari")
    let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
    let webApp = XCUIApplication(bundleIdentifier: "com.apple.webapp")
    var steps: [[String: String]] = []
    var seen: [String: String] = [:]

    func testHomeScreen() { walk(homeScreen) }
    func testProfile() { walk(profile) }

    // MARK: Add to Home Screen

    /// Safari's Add to Home Screen for the page at WALK_URL, the icon it puts on the Home Screen and the app it opens,
    /// paired with the cookies iOS copies from Safari when it adds the app.
    func homeScreen() throws {
        let url = try address()
        try step("safari") {
            XCUIDevice.shared.system.open(url)
            guard safari.wait(for: .runningForeground, timeout: 30) else { throw NotReachable(reason: "Safari did not open") }
            guard find(safari, "MoreMenuButton", 30) != nil else { throw NotReachable(reason: "Safari shows no page menu") }
            sleep(3)  // for the page to finish loading its title and icon
            return "Safari shows \(url.absoluteString)"
        }
        try step("share") {
            dismissKeyboard(safari)
            try tap(safari, "MoreMenuButton", "Safari's page menu did not open")
            try tap(safari, "ShareButton", "the page menu has no Share")
            if find(safari, "Add to Home Screen", 3) == nil, let more = find(safari, "View More", 3) { more.tap() }
            try tap(safari, "Add to Home Screen", "the share sheet offers no Add to Home Screen")
            guard find(safari, "AddToHomeScreenAddButton") != nil else {
                throw NotReachable(reason: "Add to Home Screen opened no sheet")
            }
            return "Safari's Add to Home Screen sheet"
        }
        try step("add") {
            let sheet = safari.otherElements["AddToHomeScreenView"]
            seen["sheet title"] = "\(sheet.textFields.firstMatch.value ?? "")"
            let standalone = sheet.switches["Open as Web App"]
            seen["open as web app"] = standalone.exists ? "\(standalone.value ?? "")" : "absent"
            try tap(safari, "AddToHomeScreenAddButton", "the sheet has no Add")
            if !springboard.wait(for: .runningForeground, timeout: 10) { XCUIDevice.shared.press(.home) }
            return "added as \(seen["sheet title"] ?? "")"
        }
        var icon: XCUIElement?
        try step("home-screen") {
            let icons = springboard.icons.matching(identifier: "Altitude")
            guard icons.firstMatch.waitForExistence(timeout: 15) else {
                throw NotReachable(reason: "the Home Screen shows no Altitude icon")
            }
            let added = icons.element(boundBy: max(0, icons.count - 1))  // the newest; an earlier walk may have added one
            try? added.screenshot().pngRepresentation.write(to: output("home-screen-icon.png"))
            seen["icons"] = "\(icons.count)"
            icon = added
            return "the Altitude icon on the Home Screen"
        }
        try step("standalone") {
            icon?.tap()
            guard webApp.wait(for: .runningForeground, timeout: 30) else {
                throw NotReachable(reason: "the icon did not open the web app on its own")
            }
            seen["safari"] = safari.state == .runningForeground ? "in front" : "not in front"
            sleep(3)
            return "the icon opens Altitude as a web app, without Safari"
        }
        try step("paired") {
            guard find(webApp, "Work", 20) != nil, !webApp.textFields["Pairing code"].exists else {
                throw NotReachable(reason: "the web app asks to be paired")
            }
            return "the web app shows Altitude's tabs, paired as Safari is"
        }
    }

    // MARK: Profile

    /// The device setup page at WALK_URL: Download the profile, Allow, Settings' Profile Downloaded with the profile's
    /// name and certificate, Install, and full trust under Certificate Trust Settings.
    func profile() throws {
        let url = try address()
        try step("download") {
            XCUIDevice.shared.system.open(url)
            guard safari.wait(for: .runningForeground, timeout: 30) else { throw NotReachable(reason: "Safari did not open") }
            try tap(safari, "Download the profile", "the setup page shows no Download the profile")
            let prompt = safari.textViews.matching(NSPredicate(format: "label CONTAINS 'configuration profile'")).firstMatch
            guard prompt.waitForExistence(timeout: 20) else {
                throw NotReachable(reason: "Safari asked nothing about a configuration profile")
            }
            seen["prompt"] = prompt.label
            return "Safari asks whether to allow the profile"
        }
        try step("allow") {
            try tap(safari, "Allow", "Safari's prompt has no Allow")
            if let close = find(safari, "Close", 5) { close.tap() }  // a notice that the profile is downloaded
            return "the download allowed"
        }
        try step("profile-downloaded") {
            settings.activate()
            guard settings.wait(for: .runningForeground, timeout: 20) else { throw NotReachable(reason: "Settings did not open") }
            let sheet = settings.navigationBars["Install Profile"]
            if !sheet.waitForExistence(timeout: 5) {
                // Settings lists it as Profile Downloaded at its top, or under VPN & Device Management.
                let downloaded = settings.cells.matching(NSPredicate(format: "identifier BEGINSWITH 'local.altitude.ca.'"))
                if let row = find(settings, "Profile Downloaded", 5) {
                    row.tap()
                } else if downloaded.firstMatch.waitForExistence(timeout: 5) {
                    downloaded.firstMatch.tap()
                } else {
                    throw NotReachable(reason: "Settings shows no Profile Downloaded")
                }
            }
            guard sheet.waitForExistence(timeout: 10) else { throw NotReachable(reason: "Settings opened no Install Profile") }
            seen["profile"] = labels(settings).joined(separator: "\n")
            return "Settings › Profile Downloaded opens Install Profile"
        }
        try step("certificate") {
            try tap(settings, "More Details", "Install Profile has no More Details")
            let certificate = settings.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Issued by'")).firstMatch
            guard certificate.waitForExistence(timeout: 10) else { throw NotReachable(reason: "More Details lists no certificate") }
            certificate.tap()
            guard find(settings, "SHA-256", 10) != nil || scrollTo(settings, "SHA-256") else {
                throw NotReachable(reason: "the certificate shows no SHA-256 fingerprint")
            }
            var shown = labels(settings)
            if scrollTo(settings, "Fingerprints") { shown += labels(settings) }
            seen["certificate"] = shown.joined(separator: "\n")
            for _ in 0..<3 where !settings.navigationBars["Install Profile"].exists {
                settings.navigationBars.buttons["BackButton"].firstMatch.tap()
                _ = settings.navigationBars["Install Profile"].waitForExistence(timeout: 3)
            }
            return "the profile's certificate and its SHA-256"
        }
        try step("install") {
            try tapInBar("Install Profile", "Install")
            try tapInBar("Warning", "Install")
            let confirm = settings.alerts.firstMatch.buttons["Install"]
            guard confirm.waitForExistence(timeout: 10) else { throw NotReachable(reason: "Settings did not ask to confirm") }
            confirm.tap()
            guard settings.navigationBars["Profile Installed"].waitForExistence(timeout: 20) else {
                throw NotReachable(reason: "Settings did not show Profile Installed")
            }
            sleep(1)
            return "Profile Installed"
        }
        try step("trust") {
            try tapInBar("Profile Installed", "Done")
            for _ in 0..<4 where find(settings, "com.apple.settings.general.about", 2) == nil {
                let back = settings.navigationBars.buttons["BackButton"].firstMatch
                guard back.exists else { break }
                back.tap()
            }
            if find(settings, "com.apple.settings.general.about", 2) == nil { try tap(settings, "General", "Settings has no General") }
            try tap(settings, "com.apple.settings.general.about", "General has no About")
            try tap(settings, "CERT_TRUST_SETTINGS", "About has no Certificate Trust Settings")
            guard settings.navigationBars["Certificate Trust Settings"].waitForExistence(timeout: 10) else {
                throw NotReachable(reason: "Certificate Trust Settings did not open")
            }
            sleep(1)
            // The installed profile's CA is the one not yet trusted.
            guard let toggle = settings.switches.allElementsBoundByIndex.first(where: {
                !$0.identifier.isEmpty && "\($0.value ?? "")" == "0"
            }) else { throw NotReachable(reason: "Certificate Trust Settings lists no certificate to turn on") }
            seen["trusted"] = toggle.label
            toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
            let proceed = settings.alerts["Root Certificate"].buttons["Continue"]
            guard proceed.waitForExistence(timeout: 10) else { throw NotReachable(reason: "Settings did not warn about a root certificate") }
            proceed.tap()
            let enabled = settings.switches[toggle.identifier].firstMatch
            guard enabled.waitForExistence(timeout: 5), holds(enabled, value: "1", seconds: 10) else {
                throw NotReachable(reason: "the certificate's switch did not stay on")
            }
            return "full trust turned on for \(toggle.label)"
        }
    }

    // MARK: Steps and evidence

    func walk(_ body: () throws -> Void) {
        do {
            try body()
            seen["finished"] = "yes"
        } catch let stop as NotReachable {
            seen["stopped"] = stop.reason
        } catch {
            seen["stopped"] = "\(error)"
        }
        save()
    }

    /// One step: its screenshot, and its outcome written at once, so a walk cut short keeps the steps it reached.
    func step(_ name: String, _ body: () throws -> String) throws {
        var row = ["step": name, "screenshot": "\(name).png"]
        do {
            row["detail"] = try body()
            row["status"] = "completed"
        } catch let stop as NotReachable {
            row["status"] = "not reachable"
            row["reason"] = stop.reason
        }
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: output("\(name).png"))
        steps.append(row)
        save()
        if let reason = row["reason"] { throw NotReachable(reason: reason) }
    }

    func save() {
        let record: [String: Any] = ["steps": steps, "seen": seen]
        if let data = try? JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: output("walk.json"), options: .atomic)
        }
    }

    func output(_ name: String) -> URL {
        URL(fileURLWithPath: env["WALK_OUT"] ?? NSTemporaryDirectory()).appendingPathComponent(name)
    }

    func address() throws -> URL {
        guard let text = env["WALK_URL"], let url = URL(string: text) else {
            throw NotReachable(reason: "the walk was given no address")
        }
        return url
    }

    // MARK: Finding and tapping

    func find(_ app: XCUIApplication, _ name: String, _ seconds: Double = 10) -> XCUIElement? {
        let found = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == %@ OR label == %@", name, name)).firstMatch
        return found.waitForExistence(timeout: seconds) ? found : nil
    }

    func tap(_ app: XCUIApplication, _ name: String, _ missing: String) throws {
        guard let found = find(app, name) else { throw NotReachable(reason: missing) }
        found.tap()
    }

    func tapInBar(_ bar: String, _ button: String) throws {
        let found = settings.navigationBars[bar].buttons[button]
        guard found.waitForExistence(timeout: 15) else { throw NotReachable(reason: "\(bar) has no \(button)") }
        found.tap()
    }

    func dismissKeyboard(_ app: XCUIApplication) {
        guard app.keyboards.firstMatch.waitForExistence(timeout: 2) else { return }
        if let tip = find(app, "Continue", 2) { tip.tap() }
        if let done = find(app, "Done", 2) { done.tap() }
    }

    func labels(_ app: XCUIApplication) -> [String] {
        app.staticTexts.allElementsBoundByIndex.map(\.label).filter { !$0.isEmpty }
    }

    func scrollTo(_ app: XCUIApplication, _ label: String) -> Bool {
        for _ in 0..<4 {
            if app.staticTexts[label].exists && app.staticTexts[label].isHittable { return true }
            app.swipeUp()
        }
        return app.staticTexts[label].exists
    }

    func holds(_ element: XCUIElement, value: String, seconds: Double) -> Bool {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", value), object: element)
        return XCTWaiter().wait(for: [expectation], timeout: seconds) == .completed
    }
}
