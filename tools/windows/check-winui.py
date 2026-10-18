#!/usr/bin/env python3
"""Fails on WinUI code that builds and runs in Debug but breaks a companion app's Store build or its XAML compiler.
Each rule cost an app a release or a day (mybackhurts first, then Laching's fixture and the companions):

- Casting an untyped XAML object: `(Style)Application.Current.Resources[key]`, `GetTemplateChild(...) as T`,
  `VisualTreeHelper.GetParent(...) is T`, `AppWindow.Presenter is OverlappedPresenter`, `(Brush)e.NewValue` or
  `(Brush?)GetValue(...)` for a property holding a XAML object. Under Native AOT (every Store package) the object can
  arrive wrapped as a base type, so the cast throws and `as`/`is` quietly fail. Use `XamlCast.As<T>` (Laching.Windows),
  or a typed helper of the app's that goes through it.
- A Style setting a shape's Data, Points or StrokeDashArray: the value is one object that WinUI uses for one element
  only, so repeated shapes come out blank. Set it on each element.
- An element named `Language` (it hides FrameworkElement.Language in the generated code), and an `x:Bind` function
  call with a number argument (the XAML compiler crashes, WMC9999): bind a static property instead.
- The app's XAML (a `CompanionApp` or `Application` root) anywhere but next to the project file: only a root App.xaml is
  the ApplicationDefinition, and elsewhere the app starts without its XAML type information and stops at the first
  custom type (XamlParseException 0x802B000A, no message).

Usage: python3 laching/tools/windows/check-winui.py [files or folders...]
       (none: the app in companion.json's windows.dir/windows.app)
Silence a line that is fine with a trailing `winui-check: ok` comment saying why.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TYPE = r"\(\s*[A-Z][\w.]*\??\s*\)\s*"  # a cast: (Style) or (Microsoft.UI.Xaml.Style?)
UNTYPED = r"(?:(?:Application\.Current\.)?Resources\[[^\]]+\]|GetTemplateChild\([^)]*\)|VisualTreeHelper\.GetParent\([^)]*\)|AppWindow\.Presenter\b)"
# The WinRT and XAML classes a dependency property holds; managed types and values are fine.
XAML_TYPE = (r"\(\s*(?:Brush|SolidColorBrush|Style|FontFamily|ImageSource|Geometry|DataTemplate|ControlTemplate"
             r"|UIElement|FrameworkElement|DependencyObject|MediaPlayer|ResourceDictionary)\??\s*\)\s*")
PROPERTY = r"(?:\b\w+\.(?:NewValue|OldValue)\b|\bGetValue\()"
CSHARP = [
    (re.compile(TYPE + UNTYPED), "casts an untyped XAML object; use XamlCast.As<T>"),
    (re.compile(UNTYPED + r"\s+(?:as|is)\s+(?!null\b|not\s+null\b)"), "type-tests an untyped XAML object; use XamlCast.As<T>"),
    (re.compile(XAML_TYPE + PROPERTY), "casts a property's XAML object; use XamlCast.As<T>"),
]
XAML = [
    (re.compile(r'<Setter\s+Property="(?:\w+\.)?(?:Data|Points|StrokeDashArray)"'),
     "a Style can't share this object between elements; set it on each element"),
    (re.compile(r'x:Name="Language"'), "an element named Language hides FrameworkElement.Language; name it otherwise"),
    (re.compile(r"\{x:Bind\s+[\w.:]+\([^)]*(?<![\w.])-?\d+(?:\.\d+)?\b[^)]*\)"),
     "an x:Bind function with a number argument crashes the XAML compiler (WMC9999); bind a static property"),
]
APP_ROOT = re.compile(r"<(?:\w+:)?(?:CompanionApp|Application)\b")


def check(path: Path) -> list[str]:
    rules = CSHARP if path.suffix == ".cs" else XAML if path.suffix == ".xaml" else []
    text = path.read_text(encoding="utf-8-sig")
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        if "winui-check: ok" in line:
            continue
        for pattern, message in rules:
            if pattern.search(line):
                problems.append(f"{path}:{number}: {message}\n    {line.strip()}")
    if path.suffix == ".xaml" and APP_ROOT.search(text[:600]) and not any(path.parent.glob("*.csproj")):
        problems.append(f"{path}: the app's XAML must sit next to the project file (only a root App.xaml is the ApplicationDefinition)")
    return problems


def files_in(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix in (".cs", ".xaml") else []
    return [p for p in target.rglob("*") if p.suffix in (".cs", ".xaml") and not {"bin", "obj"} & set(p.parts)]


def main(args: list[str]) -> int:
    if args:
        targets = [Path(a) for a in args if Path(a).exists()]
    else:
        import companion

        config = companion.load()
        app = config.path("windows.app", "windows.dir")
        if app is None:
            print("companion.json has no windows.app", file=sys.stderr)
            return 1
        targets = [app]
    files = sorted({f for target in targets for f in files_in(target)})
    problems = [problem for f in files for problem in check(f)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} WinUI problem(s); laching/tools/windows/check-winui.py says why.")
        return 1
    print(f"ok: {len(files)} WinUI files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
