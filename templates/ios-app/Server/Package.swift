// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Server",
    platforms: [
        .macOS(.v13)
    ],
    dependencies: [
        .package(url: "https://github.com/vapor/vapor.git", from: "4.92.0"),
        // Already pulled in transitively by Vapor; declared explicitly so we can `import
        // Crypto` for the APNs ES256 JWT (P-256 signing). SwiftPM resolves a version
        // compatible with Vapor's own swift-crypto constraint.
        .package(url: "https://github.com/apple/swift-crypto.git", "2.5.0" ..< "5.0.0")
    ],
    targets: [
        .target(
            name: "App",
            dependencies: [
                .product(name: "Vapor", package: "vapor"),
                .product(name: "Crypto", package: "swift-crypto")
            ],
            // Templates (the editable UI tree + theme) ship beside the binary.
            resources: [
                .copy("Templates")
            ]
        ),
        .executableTarget(
            name: "Run",
            dependencies: [
                .target(name: "App"),
                .product(name: "Vapor", package: "vapor")
            ]
        ),
        .testTarget(
            name: "AppTests",
            dependencies: [
                .target(name: "App"),
                .product(name: "XCTVapor", package: "vapor")
            ]
        )
    ]
)
