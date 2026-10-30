---
name: port-to-csharp
description: Use when a Swift change in Sources/ needs its C# twin in dotnet/, or when dotnet test fails on a conformance vector after a Swift change. Covers the file-for-file mapping, naming, JSON and AOT rules the port must follow.
---

# Port a Swift change to C#

The C# side mirrors Swift folder for folder and file for file. Port in the same change as the Swift edit.

## Where the twin lives

| Swift | C# |
|---|---|
| `Sources/LachingCore/<Folder>/<Name>.swift` | `dotnet/Laching.Core/<Folder>/<Name>.cs` |
| `Sources/LachingCompanion/<Folder>/<Name>.swift` | `dotnet/Laching.Companion/<Folder>/<Name>.cs` |
| `Sources/LachingTesting/<Folder>/<Name>.swift` | `dotnet/Laching.Testing/<Folder>/<Name>.cs` (generators stay Swift; .NET gets the replay bodies in `Conformance/Companion/`) |
| `Sources/LachingSystem/<Name>.swift` | `dotnet/Laching.Windows/System/<Name>.cs` |
| `Sources/LachingShell/<Name>.swift` | `dotnet/Laching.Windows/Shell/<Name>.cs` |
| `Sources/LachingStore/<Name>.swift` | `dotnet/Laching.Core/Store/<Name>.cs`; the platform backend is `dotnet/Laching.Windows/Shell/StoreContextBackend.cs` |
| `Sources/LachingTesting/MockStoreBackend.swift` | `dotnet/Laching.Core/Store/MockStoreBackend.cs` (apps use it in preview modes) |
| `Sources/LachingCompanionUI/…` | by role, not file for file: the logic in `dotnet/Laching.Companion.Shell/`, the WinUI 3 half in `dotnet/Laching.Windows.Companion/` (`plans/0.8/README.md` maps every file) |
| `Tests/LachingCoreTests/<Folder>/<Name>Tests.swift` | `dotnet/Laching.Core.Tests/<Folder>/<Name>Tests.cs` |
| `Tests/LachingCoreTests/Conformance/` | `dotnet/Laching.Core.Tests/Conformance/` |
| `Tests/LachingCompanionTests/<Folder>/<Name>Tests.swift` | `dotnet/Laching.Companion.Tests/<Folder>/<Name>Tests.cs` |

System and Shell names follow the platform where the concept differs (`ScreenLockWatcher` ↔ `SessionLockWatcher`).
Some files exist on one side only (`StoreKitBackend`, `TrayIconHost`); don't invent a twin for those. The store is on
both sides. `Sources/LachingCore/Drawing/` has no C# twin: the drawing math is Swift and Kotlin only (`port-to-kotlin`).

## Translation rules

- Same type and member names, PascalCased. Durations are `double` seconds with a `Seconds` suffix
  (`interval: TimeInterval` ↔ `IntervalSeconds`). Config types use `{ get; init; }` with the same defaults.
- Same doc comment content (what it does, what it needs, what it never does), as `///` XML docs.
- Same algorithm in the same order. Calendar math uses the vector's IANA time zone and the Gregorian calendar; see
  `conformance/README.md` for rounding rules (for example `floor(x + 0.5)`, not banker's rounding).
- **JSON: source-generated `JsonSerializerContext` only**, never reflection-based `JsonSerializer` overloads.
  Apps publish with Native AOT, and `TreatWarningsAsErrors` turns trim/AOT warnings into build failures.
- `Laching.Core` stays platform-neutral (`net10.0`, `IsAotCompatible`). Win32 / Windows App SDK code goes in
  `Laching.Windows`.
- The package ships no strings: anything user-visible is a parameter.

## Verify

From the repository's root:

```sh
dotnet test dotnet/Laching.Tests.slnf   # Core, Companion, Companion.Shell and Testing; the vector failures name the vector and step
dotnet format whitespace dotnet --folder
```

`Laching.Windows` can't fully build off Windows (MakePri). Its CI job in `dotnet.yml` is the check: push the branch.
