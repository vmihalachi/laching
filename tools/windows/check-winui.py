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
- A collection expression for a read-only list interface: `IReadOnlyList<int> sizes = [20, 30];`, `return [.. items];`
  from a method declared with one, `Start([exercise])` for a parameter of that type. With `IEnumerable<T>`,
  `IReadOnlyCollection<T>` or `IReadOnlyList<T>` as its type the compiler makes up the class, which the Native AOT
  publish of a WinUI project reports as CsWinRT1032, one site a run. Build an array (`new[] { … }`, `.ToArray()`) or a
  List. An empty `[]` is fine, and so are `IList<T>` and `ICollection<T>` (a List). The rule reads no types but the
  ones written down, so it reports a site only where the sources say what the expression becomes:
  - a field, property or local declared with the type, initialized or assigned in the same file;
  - an expression body, a getter or a `return` of a method, property or local function declared with it (a
    `Task<…>` of it too);
  - an argument, by position or by name, when every declaration the call reaches that takes as many arguments has
    the type there, and a lambda passed as a `Func<…>` returning it. `new T(…)` reaches T's constructors, and `new(…)`
    those of the type it is declared as. `x.M(…)` reaches the methods of x's type (its bases' and the extensions for
    it included): x a type's name, or a local, parameter, field, property, `this` or string whose type is written
    down and declared in the sources, `model.Core` a property of one. `M(…)` reaches the local functions in scope,
    else the methods of the type around it. The declarations are read from the checked files' projects and the
    projects those reference (Laching's packages from the sources next to this tool);
  - a branch of `a ? b : c` or an arm of a switch expression in one of those places, when no other branch or arm has
    a type of its own (beside `named` in `ok ? named : ["light"]` the expression becomes what `named` is).
  It misses what is declared elsewhere or takes the compiler to tell: a member of another file assigned here, a
  member set in an object initializer, a parameter of the platform's or another library's method, a method a type
  inherits from one, a call on what a call or an indexer gives, on a `var` set to anything but `new T(…)` or on a
  lambda's parameter, a `params` parameter, a call whose overloads differ or whose type arguments hold a comma, the
  right side of `??`, a branch or an arm beside one with a type, a cast, `yield return`, a `return` in a lambda's
  block, `base(…)` and `this(…)`.

Usage: python3 laching/tools/windows/check-winui.py [files or folders...]
       (none: the app in companion.json's windows.dir/windows.app)
Silence a line that is fine with a trailing `winui-check: ok` comment saying why.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

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

# --- CsWinRT1032: a collection expression for a read-only list interface ---

LISTS = ("IEnumerable", "IReadOnlyCollection", "IReadOnlyList")  # ICollection<T> and IList<T> get a List<T>
PACKAGES = Path(__file__).resolve().parents[2] / "dotnet"  # Laching's packages, as the sources they are built from
# Comments, strings, characters and preprocessor lines: blanked before anything is read as code, a quote left where
# a string or a character began (`["a"]` isn't an empty expression).
LITERAL = re.compile(r'//[^\n]*|/\*.*?\*/|("{3,}).*?\1|(?:@\$|\$@|@)"(?:[^"]|"")*"|"(?:\\.|[^"\\\n])*"'
                     r"|'(?:\\.|[^'\\\n])+'|^[ \t]*#[^\n]*", re.S | re.M)
# What a collection expression follows; an indexer, an attribute, an array type or a pattern follows something else.
EXPRESSION = re.compile(r"(?:(?<![=!<>])=>?|[(,]|(?<!:):|\?\?|(?<=\s)\?(?=\s)|\breturn\b)\s*\[")
CALLABLE = re.compile(r"(?<![\w@.])(\w+)\s*(?:<[^<>()]*>\s*)?\(")
GENERICS = re.compile(r"\s*<[^<>()]*>\s*$")  # the type parameters of M<T>(…)
SOLID = re.compile(r"\S")
LABEL = re.compile(r"\s*@?(\w+)\s*:(?!:)\s*")  # the name of a named argument
INITIALIZER = re.compile(r"(?:\bnew\b[\w\s.<>,?\[\]]*|\bwith|=)\s*$")  # what opens the braces of an object initializer
KEYWORDS = frozenset(
    "return else await async yield throw new in is as out ref case goto do when and or not if while using typeof nameof "
    "default switch foreach for lock catch finally try base this sizeof checked unchecked unsafe fixed where get set "
    "init class struct record interface enum namespace".split())
BLOCKS = frozenset("if else for foreach while do switch try catch finally using lock fixed checked unchecked unsafe when".split())
TYPE_DECLARATION = re.compile(r"(?<![\w@.])(record(?:\s+(?:class|struct))?|class|struct|interface)\s+@?(\w+)")
NAMESPACE = re.compile(r"(?<![\w@.])namespace\s+([\w.]+)")
MEMBER = re.compile(r"(?<![\w.@])@?(\w+)(?=\s*[;={])")  # a field's or a property's name, in a type's top-level code
STATIC_USING = re.compile(r"\busing\s+static\s+(?:global::)?([\w.]+)\s*;")
ALIASES = {"String": "string", "Object": "object", "Int32": "int", "Int64": "long", "Boolean": "bool", "Double": "double",
           "Single": "float", "Char": "char"}
Parameters = list[tuple[str, str, str]]  # (type, name, kind) each; kind is "this", "params", "optional" or ""


class Source:
    """One C# file: its text, and its code with every literal and comment blanked in place and the brackets paired."""

    def __init__(self, path: Path):
        self.text = path.read_text(encoding="utf-8-sig", errors="replace")
        self.code = LITERAL.sub(self.blank, self.text)
        self.partner: dict[int, int] = {}
        stack: list[int] = []
        for m in re.finditer(r"[()\[\]{}]", self.code):
            if m.group() in "([{":
                stack.append(m.start())
            elif stack:
                self.partner[m.start()] = stack[-1]
                self.partner[stack.pop()] = m.start()

    @staticmethod
    def blank(m: re.Match) -> str:
        spaces = re.sub(r"[^\n]", " ", m.group())
        return spaces if m.group()[0] in "/# \t" else '"' + spaces[1:]

    def before(self, j: int) -> int:
        """The last character before j that isn't white space, or -1."""
        j -= 1
        while j >= 0 and self.code[j].isspace():
            j -= 1
        return j

    def head(self, end: int) -> str:
        """The code that ends before `end`: enough of it to hold a declaration."""
        return self.code[max(0, end - 400):max(0, end)]

    def enclosing(self, j: int) -> int:
        """The bracket that is open at j, or -1."""
        j -= 1
        while j >= 0:
            c = self.code[j]
            if c in ")]}":
                j = self.partner.get(j, -1)
            elif c in "([{":
                return j
            j -= 1
        return -1


SOURCES: dict[Path, Source] = {}


def source(path: Path) -> Source:
    if path not in SOURCES:
        SOURCES[path] = Source(path)
    return SOURCES[path]


def word(c: str) -> bool:
    return c.isalnum() or c == "_"


def declaration(text: str) -> tuple[str, str] | None:
    """`… TYPE name`, read from its end: (TYPE, name), or None for text that doesn't end in a declaration."""
    named = re.search(r"\s@?(\w+)\s*$", text)
    if not named:
        return None
    rest = text[:named.start()].rstrip()
    if rest.endswith("=>"):
        return None  # what a lambda or a member's body gives
    end = len(rest)
    while rest.endswith(("?", "[]"), 0, end):
        end -= 1 if rest[end - 1] == "?" else 2
    start = end
    if rest.endswith((">", ")"), 0, end):
        opener, closer = "<>" if rest[end - 1] == ">" else "()"
        depth = 0
        for start in range(end - 1, -1, -1):
            depth += (rest[start] == closer) - (rest[start] == opener)
            if depth == 0:
                break
        else:
            return None
        if closer == ")":  # a tuple; one name in brackets is an expression
            return (rest[start:], named.group(1)) if "," in rest[start:end] else None
    name = re.search(r"(?:global::)?[\w.]+$", rest[:start])
    if not name or name.group() in KEYWORDS:
        return None
    return rest[name.start():], named.group(1)


def alias(name: str) -> str:
    """The keyword for a type that has one (`String` is `string`)."""
    return ALIASES.get(name, name)


def generic(type_: str) -> tuple[str, list[str]]:
    """`Ns.Name<A, B>?` as ("Name", ["A", "B"]); a type with no arguments, or a tuple, as (its name, [])."""
    text = re.sub(r"\s+", "", type_).rstrip("?")
    start = text.find("<")
    if start > 0 and text.endswith(">") and text[0] != "(":
        arguments, depth, begin = [], 0, start + 1
        for at in range(start, len(text)):
            depth += (text[at] in "<(") - (text[at] in ">)")
            if depth == 0 and at < len(text) - 1:
                break  # Outer<T>.Inner
            if depth == 0 or (depth == 1 and text[at] == ","):
                arguments.append(text[begin:at])
                begin = at + 1
        else:
            return re.split(r"[.:]", text[:start])[-1], arguments
    return re.split(r"[.:]", text)[-1], []


def read_only(type_: str | None) -> bool:
    """Whether a collection expression for this type is one CsWinRT1032 reports."""
    name, arguments = generic(type_ or "")
    if name in ("Task", "ValueTask") and len(arguments) == 1:  # what the `return` of an async method gives
        name, arguments = generic(arguments[0])
    return name in LISTS and len(arguments) == 1


def verdict(type_: str) -> str:
    """What makes two parameter types the same to this rule: both such a list, both a function returning one, or one type."""
    name, arguments = generic(type_)
    if read_only(type_):
        return "a read-only list"
    return "a function returning one" if name == "Func" and arguments and read_only(arguments[-1]) else type_


def parameters(text: str) -> Parameters | None:
    """A parameter list, or None for text that isn't one (the arguments of a call)."""
    if not text.strip() or re.search(r"=>|[;{]", text):
        return None
    found, depth, begin = [], 0, 0
    for at, c in enumerate(text + ","):
        depth += (c in "<([") - (c in ">)]")
        if c == "," and depth == 0:
            part, _, default = re.sub(r"^\s*(?:\[[^\]]*\]\s*)*", "", text[begin:at]).partition("=")
            begin = at + 1
            modifiers = re.match(r"(?:(?:this|ref|in|out|params|scoped|readonly)\s+)*", part)
            declared = declaration(" " + part[modifiers.end():])
            if not declared or declared[0] == "var":
                return None  # `out var x` is a call's argument
            kind = next((k for k in ("this", "params") if re.search(rf"\b{k}\s", modifiers.group())), "optional" if default else "")
            found.append((*declared, kind))
    return found


def referenced(projects: set[Path]) -> set[Path]:
    """The project folders with the ones they reference: other projects, and Laching's packages as their sources."""
    todo, seen = list(projects), set()
    while todo:
        folder = todo.pop()
        if folder in seen:
            continue
        seen.add(folder)
        for project in folder.glob("*.csproj"):
            text = project.read_text(encoding="utf-8-sig", errors="replace")
            for kind, name in re.findall(r'<(Project|Package)Reference\s+Include="([^"]+)"', text):
                other = (folder / name.replace("\\", "/")).parent if kind == "Project" else PACKAGES / name
                if other.is_dir():
                    todo.append(other.resolve())
    return seen


class Part:
    """A class, struct, record or interface that a file declares (one part of a partial one): its name, the braces of
    its body, the types it names as its bases, and a record's parameters."""

    def __init__(self, s: Source, m: re.Match):
        code = s.code
        self.source, self.name, self.at, self.found = s, m.group(2), m.start(2), None
        opens = re.compile(r"\s*(?:<[^<>()]*>\s*)?\(").match(code, m.end())
        close = s.partner.get(opens.end() - 1) if opens else None
        self.primary = (parameters(code[opens.end():close]) or []) if close and m.group(1).startswith("record") else []
        end, c = next(((at, c) for at, c in top(s, m.end(), len(code)) if c in "{;"), (len(code), ";"))
        self.body = (end, s.partner[end]) if c == "{" and end in s.partner else None
        head = re.split(r"\bwhere\b", "".join(" " if c in "([" else c for _, c in top(s, m.end(), end)))[0]
        self.bases, depth, begin = [], 0, None
        for at, c in enumerate(head + ","):
            depth += (c == "<") - (c == ">")
            if depth == 0 and c == ":" and begin is None:
                begin = at + 1
            elif depth == 0 and c == "," and begin is not None:
                if head[begin:at].strip():
                    self.bases.append(alias(generic(head[begin:at])[0]))
                begin = at + 1

    def members(self) -> dict[str, set[str]]:
        """Its fields and properties, a record's parameters included: each name with the types it is declared with."""
        if self.found is None:
            self.found = {}
            for type_, name, _ in self.primary:
                self.found.setdefault(name, set()).add(type_)
            if self.body:
                flat = "".join(c for _, c in top(self.source, self.body[0] + 1, self.body[1]))
                for m in MEMBER.finditer(flat):
                    declared = declaration(flat[max(0, m.end() - 400):m.end()])
                    if declared:
                        self.found.setdefault(declared[1], set()).add(declared[0])
        return self.found


class Callable(NamedTuple):
    """A declaration with parameters: a constructor (a primary one included), a member (a method, a delegate) or a
    local function, which is called only within `scope`."""
    parameters: Parameters
    owner: Part | None
    role: str  # "constructor", "member" or "local"
    source: Source
    scope: tuple[int, int]


class Declarations:
    """What the checked files' projects, and the projects those reference, declare: types with their bases and
    members, and what takes parameters. Read when the first argument asks."""

    def __init__(self, files: list[Path]):
        self.files = [f for f in files if f.suffix == ".cs"]
        self.loaded = False

    def load(self):
        if self.loaded:
            return
        self.loaded = True
        self.types: dict[str, list[Part]] = {}
        self.parts: dict[Source, list[Part]] = {}
        self.callables: dict[str, list[Callable]] = {}
        self.namespaces: set[str] = set()
        checked = {f.resolve() for f in self.files}
        projects = {next((p for p in f.resolve().parents if any(p.glob("*.csproj"))), None) for f in self.files}
        others = {p for folder in referenced(projects - {None}) for p in files_in(folder) if p.suffix == ".cs"}
        for s in map(source, self.files + sorted(p for p in others if p.resolve() not in checked)):
            self.parts[s] = [Part(s, m) for m in TYPE_DECLARATION.finditer(s.code) if m.group(2) not in KEYWORDS]
            for part in self.parts[s]:
                self.types.setdefault(part.name, []).append(part)
            for m in NAMESPACE.finditer(s.code):
                names = m.group(1).split(".")
                self.namespaces.update(".".join(names[:n]) for n in range(1, len(names) + 1))
            for m in CALLABLE.finditer(s.code):
                close = s.partner.get(m.end() - 1)
                declared = parameters(s.code[m.end():close]) if close and m.group(1) not in KEYWORDS else None
                if declared:
                    self.callables.setdefault(m.group(1), []).append(self.callable(s, m, declared))

    def callable(self, s: Source, m: re.Match, declared: Parameters) -> Callable:
        primary = next((p for p in self.parts[s] if p.at == m.start(1)), None)
        if primary:
            return Callable(declared, primary, "constructor", s, (0, 0))
        around, brace = self.inside(s, m.start()), s.enclosing(m.start())
        owner = around[0] if around else None
        if owner and brace == owner.body[0]:
            return Callable(declared, owner, "constructor" if m.group(1) == owner.name else "member", s, owner.body)
        return Callable(declared, owner, "local", s, (brace, s.partner.get(brace, len(s.code))) if brace >= 0 else (-1, len(s.code)))

    def inside(self, s: Source, j: int) -> list[Part]:
        """The types of this file whose body holds j, the innermost first."""
        around = [p for p in self.parts.get(s, []) if p.body and p.body[0] < j < p.body[1]]
        return sorted(around, key=lambda p: -p.body[0])

    def lineage(self, name: str) -> set[str]:
        """The type and the bases the sources declare for it, theirs included."""
        found, todo = set(), [alias(name)]
        while todo:
            name = todo.pop()
            if name not in found:
                found.add(name)
                todo += [base for part in self.types.get(name, []) for base in part.bases]
        return found

    def methods(self, type_: str, static: bool, name: str) -> list[Parameters]:
        """The methods called `name` that a value of `type_` has (its bases' and the extensions for it), or the type."""
        line, found = self.lineage(type_), []
        for c in self.callables.get(name, []):
            if c.role == "member" and c.owner.name in line:
                found.append(c.parameters)
            elif c.role == "member" and not static and c.parameters[0][2] == "this" and alias(generic(c.parameters[0][0])[0]) in line:
                found.append(c.parameters[1:])
        return found

    def member(self, type_: str, name: str) -> tuple[str, bool] | None:
        """What `type_.name` is: (its type, False) for a field or a property declared with one type, (name, True) for
        a type nested in it; None when the sources don't say."""
        parts = [p for n in self.lineage(type_) for p in self.types.get(n, [])]
        found = {alias(generic(t)[0]) for p in parts for t in p.members().get(name, ())}
        if found:
            return (found.pop(), False) if len(found) == 1 else None
        nested = any(o.source is p.source and p.body and p.body[0] < o.at < p.body[1] for o in self.types.get(name, []) for p in parts)
        return (name, True) if nested else None

    def constructors(self, name: str) -> list[Parameters]:
        """What `new name(…)` reaches."""
        self.load()
        return [c.parameters for c in self.callables.get(name, []) if c.role == "constructor" and c.owner.name == name]

    def called(self, s: Source, dot: int, name: str) -> list[Parameters]:
        """What `x.name(…)` reaches, the `.` at `dot`: the methods of x's type, when x is a local, a parameter, a field,
        a property, `this` or a string whose type the sources declare, or of the type x names; none when the sources
        don't say what x is."""
        self.load()
        names = receiver(s, dot)
        if not names:
            return []
        first, rest, found = names[0], names[1:], None
        if first == '"':
            found = "string", False
        elif first in ("this", "base"):
            around = self.inside(s, dot)
            found = (around[0].name, False) if around else None
        else:
            declared = variable(s, first)
            if declared == "":
                return []
            found = (declared, False) if declared else None
            for part in (() if found else self.inside(s, dot)):  # a member declared in another file
                found = self.member(part.name, first)
                if found:
                    break
            if not found and first in self.types:
                found = first, True
            if not found:  # Namespace.Type
                spaces = [n for n in range(1, len(names)) if ".".join(names[:n]) in self.namespaces and names[n] in self.types]
                found, rest = ((names[spaces[-1]], True), names[spaces[-1] + 1:]) if spaces else (None, rest)
        for next_ in rest:
            found = self.member(found[0], next_) if found else None
        return self.methods(*found, name) if found else []

    def bare(self, s: Source, at: int, name: str) -> list[Parameters]:
        """What `name(…)` at `at` reaches: the local functions in scope, else the methods of the innermost type around
        it that has one (its bases' included), else those of a `using static` type."""
        self.load()
        found = self.callables.get(name, [])
        local = [c.parameters for c in found if c.role == "local" and c.source is s and c.scope[0] < at < c.scope[1]]
        if local:
            return local
        for type_ in [p.name for p in self.inside(s, at)] + [generic(t)[0] for t in STATIC_USING.findall(s.code)]:
            line = self.lineage(type_)
            members = [c.parameters for c in found if c.role == "member" and c.owner.name in line]
            if members:
                return members
        return []


def receiver(s: Source, dot: int) -> list[str] | None:
    """The names before the `.` at `dot`, first to last (`model`, `Core` in `model.Core.Start(…)`), a string as `"`;
    None for anything else there: a call, an indexer, a cast, a generic type."""
    code, names, j = s.code, [], dot
    while True:
        k = s.before(j)
        while k >= 0 and code[k] in "?!":  # x?.Name, x!.Name
            k = s.before(k)
        if k >= 0 and code[k] == '"':
            return ['"'] + names
        if k < 0 or not word(code[k]):
            return None
        start = k
        while start and word(code[start - 1]):
            start -= 1
        names.insert(0, code[start:k + 1])
        j = s.before(start)
        if j < 0 or code[j] != ".":
            return names


def variable(s: Source, name: str) -> str | None:
    """The type that this file declares `name` with (a local, a parameter, a field, a property): "" when it can't be
    told (a `var` set to anything but `new T(…)`, a lambda's parameter, declarations that differ), None when the file
    declares nothing by that name."""
    code, types = s.code, set()
    for m in re.finditer(rf"(?<![\w.@])@?{re.escape(name)}\b(?=\s*(?:[;={{,)]|in\b))", code):
        declared = declaration(s.head(m.end()))
        if declared and declared[0] == "var":
            made = re.compile(r"\s*=\s*new\s+(?:global::)?((?:\w+\s*\.\s*)*\w+)\s*[(<{]").match(code, m.end())
            types.add(alias(generic(made.group(1))[0]) if made else "")
        elif declared:
            types.add(alias(generic(declared[0])[0]))
        else:
            opens = s.enclosing(m.start())
            close = s.partner.get(opens) if opens >= 0 and code[opens] == "(" else None
            if re.compile(r"\s*=>").match(code, m.end()) or close and re.compile(r"\s*=>").match(code, close + 1):
                types.add("")  # x => …, (x, y) => …
    if not types:
        return None
    return types.pop() if len(types) == 1 else ""


def top(s: Source, start: int, end: int):
    """The characters of code[start:end] outside its brackets, opening ones included, each with where it is."""
    j = start
    while j < end:
        yield j, s.code[j]
        j = s.partner.get(j, j) + 1 if s.code[j] in "([{" else j + 1


def choice(s: Source, j: int) -> bool:
    """Whether the `?` at j is the one of `a ? b : c`: this rule reads it only with a space on both sides."""
    return s.code[j - 1].isspace() and s.code[j + 1].isspace()


def typeless(s: Source, start: int, end: int) -> bool:
    """Whether code[start:end] has no type of its own: a collection expression, null, default or a throw."""
    text = s.code[start:end].strip()
    start += len(s.code[start:end]) - len(s.code[start:end].lstrip())
    whole = text.startswith("[") and s.partner.get(start) == start + len(text) - 1
    return whole or text in ("null", "default") or bool(re.match(r"throw\b", text))


def alone(s: Source, i: int) -> bool:
    """Whether the other branch of the `? :` that the expression at i is a branch of has no type either. With a type
    there the conditional has that type, and so has the expression, whatever the two are assigned to."""
    code, j = s.code, s.before(i)
    if code[j] == "?":
        colon = SOLID.search(code, s.partner[i] + 1)
        if not colon or colon.group() != ":":
            return False
        end = next((at for at, c in top(s, colon.end(), len(code)) if c in ";,)]}"), len(code))
        return typeless(s, colon.end(), end)
    k = j - 1
    while k >= 0 and code[j] == ":":
        if code[k] in ")]}":
            k = s.partner.get(k, -1)
        elif code[k] in "([{;,":
            break
        elif code[k] == "?" and choice(s, k):
            return typeless(s, k + 1, j)
        k -= 1
    return False


def governor(s: Source, i: int) -> tuple | None:
    """What gives the expression at i its type, walking left past the rest of the expression it is part of:
    ("=", at), ("=>", at), ("return", at), or ("(", at, commas, start) for an argument, `start` where its text begins.
    None for the right side of `??` (it takes the type of the left side) and for a branch of `? :` beside a typed one."""
    code, commas, start, choices, found, j = s.code, 0, None, 0, None, i - 1
    while j >= 0 and not found:
        c = code[j]
        if c in ")]}":
            j = s.partner.get(j, -1)
        elif c == "(":
            found = "(", j, commas, j + 1 if start is None else start
        elif c in "[{;":
            return None
        elif c == ",":
            commas += 1
            start = j + 1 if start is None else start
        elif commas:
            pass  # an earlier argument
        elif c == "?" and "?" in (code[j - 1], code[j + 1]):
            return None
        elif c == "?":
            choices += choice(s, j)
        elif c == "=" and code[j + 1] == ">":
            found = "=>", j
        elif c == "=" and code[j + 1] != "=" and code[j - 1] not in "=!<>":
            found = "=", j
        elif c == "r" and code.startswith("return", j) and not word(code[j + 6]) and not (j and word(code[j - 1])):
            found = "return", j
        j -= 1
    if not found or choices > 1 or (choices and not alone(s, i)):
        return None
    if code[s.before(i)] == ":" and not choices and not (found[0] == "(" and LABEL.fullmatch(code, found[3], i)):
        return None  # a `:` that is neither a conditional's nor the one after an argument's name
    return found


def target(s: Source, known: Declarations, i: int, hops: int = 0) -> str | None:
    """The type, as the sources declare it, that the expression at i becomes; None where they don't say."""
    found = governor(s, i) if hops < 4 else None
    if not found:
        return None
    kind, at = found[:2]
    if kind == "=":
        return assigned(s, at)
    if kind == "return":
        return None if re.search(r"\byield\s*$", s.head(at)) else returned(s, at)
    if kind == "=>":
        return arrow(s, known, at, hops)
    label = LABEL.match(s.code, found[3], i)
    return argument(s, known, at, found[2], label.group(1) if label else None, hops)


def assigned(s: Source, at: int) -> str | None:
    """The type of what the `=` at `at` assigns to: the declaration it ends, or the ones this file has for the name."""
    end = s.before(at) + 1
    if s.code[max(0, end - 2):end] == "??":
        end = s.before(end - 2) + 1
    if s.code[max(0, end - 1):end] == "}":  # a property's accessors: Name { get; } = …
        end = s.before(s.partner.get(end - 1, 0)) + 1
    head = s.head(end)
    found = declaration(head)
    if found:
        return found[0]
    name = re.search(r"(?:^|[;{}:)>]|\belse)\s*(?:this\s*\.\s*)?(\w+)$", head)
    brace = s.enclosing(end)
    if not name or (brace >= 0 and s.code[brace] == "{" and initializer(s, brace)):
        return None  # a member of another object, or of the one an initializer makes
    uses = re.finditer(rf"(?<![\w.@]){re.escape(name.group(1))}\s*(?=[;={{,)])", s.code)
    types = [d[0] for d in (declaration(s.head(m.end())) for m in uses) if d]
    return types[0] if types and all(map(read_only, types)) else None


def initializer(s: Source, brace: int) -> bool:
    """Whether the `{` at `brace` opens an object initializer or a `with`: its members are another type's."""
    j = s.before(brace)
    if j >= 0 and s.code[j] == ")":
        j = s.before(s.partner.get(j, 0))
    return bool(INITIALIZER.search(s.head(j + 1)))


def arms(s: Source, brace: int) -> bool:
    """Whether no arm of the switch expression at `brace` has a type of its own, which leaves it to its target type."""
    close = s.partner.get(brace, brace)
    commas = [at for at, c in top(s, brace + 1, close) if c == ","]
    for start, end in zip([brace + 1] + [at + 1 for at in commas], commas + [close]):
        arrow_ = next((at for at, c in top(s, start, end) if c == "=" and s.code[at + 1] == ">"), None)
        if arrow_ is None and s.code[start:end].strip() or arrow_ is not None and not typeless(s, arrow_ + 2, end):
            return False
    return True


def returned(s: Source, at: int) -> str | None:
    """The declared type of the method, local function or getter that the `return` at `at` leaves; None in a lambda."""
    code, brace = s.code, s.enclosing(at)
    while brace >= 0 and code[brace] == "{":
        j = s.before(brace)
        call = j >= 0 and code[j] == ")"
        head = GENERICS.sub("", s.head(s.partner.get(j, 0))).rstrip() if call else s.head(j + 1)
        opens = re.search(r"\w+$", head)
        if not opens:
            return None  # a lambda's block
        if opens.group() in BLOCKS:
            brace = s.enclosing(brace)
            continue
        if opens.group() == "get" and not call:
            brace = s.enclosing(brace)
            head = s.head(brace) if brace >= 0 and code[brace] == "{" else ""
        found = declaration(head) if call or opens.group() == "get" else None
        return found[0] if found else None
    return None


def arrow(s: Source, known: Declarations, at: int, hops: int) -> str | None:
    """The type of what the `=>` at `at` gives: the declared type of a member, local function or getter, the type a
    switch expression becomes, or what the function a lambda is passed as returns."""
    code = s.code
    j = s.before(at)
    if j < 0:
        return None
    brace = s.enclosing(at)
    if brace >= 0 and code[brace] == "{" and re.search(r"\bswitch\s*$", s.head(brace)):
        return target(s, known, brace, hops + 1) if arms(s, brace) else None
    start, found = j + 1, None
    if code[j] == ")":
        start = s.partner.get(j, 0)
        found = declaration(GENERICS.sub("", s.head(start)))
    elif word(code[j]):
        while start and word(code[start - 1]):
            start -= 1
        if code[start:j + 1] == "get":
            found = declaration(s.head(brace)) if brace >= 0 and code[brace] == "{" else None
            return found[0] if found else None
        found = declaration(s.head(j + 1))
    if found:
        return found[0]
    name, arguments = generic(target(s, known, start, hops + 1) or "")
    return arguments[-1] if name == "Func" and arguments else None


def argument(s: Source, known: Declarations, open_: int, position: int, label: str | None, hops: int) -> str | None:
    """The type that every declaration the call at `open_` reaches gives the argument at `position`, or the one named
    `label`: the first one's words when all say the same, the interface alone when they differ in what it holds. A
    call reaches the constructors of `new T(…)` (of the type a `new(…)` is declared with), the methods of x's type in
    `x.M(…)`, and in `M(…)` the local functions in scope or the methods of the type around it (Declarations)."""
    code = s.code
    callee = re.search(r"(\w+)\s*(?:<[^()=;]*>)?\s*$", s.head(open_))
    if not callee:
        return None  # brackets or a tuple
    name, at = callee.group(1), open_ - len(s.head(open_)) + callee.start(1)
    if name == "new":  # new(…): the type the expression is declared with
        reached = known.constructors(generic(target(s, known, at, hops + 1) or "")[0])
    elif name in KEYWORDS:
        return None
    elif re.search(r"\bnew\s+(?:global::)?(?:\w+\s*\.\s*)*$", s.head(at)):
        reached = known.constructors(name)
    elif s.before(at) >= 0 and code[s.before(at)] == ".":
        reached = known.called(s, s.before(at), name)
    else:
        reached = known.bare(s, at, name)
    flat = "".join(c for _, c in top(s, open_ + 1, s.partner.get(open_, open_)))
    if re.search(r"<[^<>]*,[^<>]*>", flat):
        return None  # a comma that may be a type's (Dictionary<string, int>): the arguments can't be counted
    count = flat.count(",") + 1
    types = []
    for shape in reached:
        more = bool(shape) and shape[-1][2] == "params"  # any number of arguments from there on
        if not sum(1 for p in shape if p[2] in ("", "this")) <= count <= (count if more else len(shape)):
            continue
        at = min(position, len(shape) - 1) if more else position
        chosen = [p for p in shape if p[1] == label] if label else shape[at:at + 1]
        if chosen:
            types.append("params" if chosen[0][2] == "params" else chosen[0][0])
    if len(set(map(verdict, types))) != 1:
        return None
    same = len({re.sub(r"\s+|\?$", "", type_) for type_ in types}) == 1
    return types[0] if same or not read_only(types[0]) else f"{generic(types[0])[0]}<…>"


def collection_expressions(path: Path, known: Declarations) -> list[str]:
    s = source(path)
    lines = s.text.split("\n")  # the lines `number` counts: splitlines() also breaks at \f, \v, \x1c-\x1e, \x85,
    problems: dict[int, str] = {}  # one a line
    for m in EXPRESSION.finditer(s.code):
        i = m.end() - 1
        close = s.partner.get(i)
        after = SOLID.search(s.code, close + 1) if close else None
        if not after or after.group() not in ";,):}" or not s.code[i + 1:close].strip():
            continue  # not an expression, or an empty one (a shared empty array)
        type_ = target(s, known, i)
        number = s.code.count("\n", 0, i) + 1
        if read_only(type_) and "winui-check: ok" not in lines[number - 1]:
            problems.setdefault(number, f"{path}:{number}: a collection expression for {' '.join(type_.split()).rstrip('?')} has no "
                                f"class of its own under Native AOT (CsWinRT1032); build an array or a List\n    {lines[number - 1].strip()}")
    return list(problems.values())


def check(path: Path, known: Declarations) -> list[str]:
    rules = CSHARP if path.suffix == ".cs" else XAML if path.suffix == ".xaml" else []
    text = path.read_text(encoding="utf-8-sig")
    problems = []
    for number, line in enumerate(text.split("\n"), 1):
        if "winui-check: ok" in line:
            continue
        for pattern, message in rules:
            if pattern.search(line):
                problems.append(f"{path}:{number}: {message}\n    {line.strip()}")
    if path.suffix == ".cs":
        problems += collection_expressions(path, known)
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
    known = Declarations(files)
    problems = [problem for f in files for problem in check(f, known)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} WinUI problem(s); laching/tools/windows/check-winui.py says why.")
        return 1
    print(f"ok: {len(files)} WinUI files")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # UTF-8 on a cp1252 Windows console too (companion.utf8_console)
        stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
