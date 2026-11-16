#!/usr/bin/env python3
"""Tests for tools/l10n.py and tools/phrases.py over the fixture app (tools/tests/fixture-app): gaps, work files,
apply and the exports it regenerates, the Windows (.resw), Linux (.po) and Android (strings.xml) exports, manual keys,
`--since` over a landing page in its own repository, language-scoped phrases and the phrase sync."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import companion  # noqa: E402
import l10n  # noqa: E402
import phrases  # noqa: E402
from support import TOOLS, FixtureAppTestCase, git, quiet  # noqa: E402

APPLE_SPEC_RE = re.compile(r"%(?:\d+\$)?(?:ll|l|q|z)?[@diu]|%#@|%arg")
NET_ARG_RE = re.compile(r"(?<!\{)\{(\d+)\}(?!\})")


def eval_plural(expr: str, n: int) -> int:
    """Evaluates a gettext `plural=` C expression (ternaries, comparisons, &&, ||, %, !)."""
    tokens = re.findall(r"\d+|n|==|!=|>=|<=|&&|\|\||[()?:<>%!]", expr)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take(expected=None):
        nonlocal pos
        token = tokens[pos]
        if expected and token != expected:
            raise ValueError(f"expected {expected}, got {token} in {expr}")
        pos += 1
        return token

    def ternary():
        cond = logical_or()
        if peek() == "?":
            take("?")
            a = ternary()
            take(":")
            b = ternary()
            return a if cond else b
        return cond

    def logical_or():
        value = logical_and()
        while peek() == "||":
            take()
            right = logical_and()
            value = int(bool(value) or bool(right))
        return value

    def logical_and():
        value = comparison()
        while peek() == "&&":
            take()
            right = comparison()
            value = int(bool(value) and bool(right))
        return value

    def comparison():
        value = modulo()
        while peek() in ("==", "!=", ">", "<", ">=", "<="):
            op = take()
            right = modulo()
            value = int({"==": value == right, "!=": value != right, ">": value > right,
                         "<": value < right, ">=": value >= right, "<=": value <= right}[op])
        return value

    def modulo():
        value = unary()
        while peek() == "%":
            take()
            value %= unary()
        return value

    def unary():
        if peek() == "!":
            take()
            return int(not unary())
        if peek() == "(":
            take("(")
            value = ternary()
            take(")")
            return value
        token = take()
        return n if token == "n" else int(token)

    result = ternary()
    if pos != len(tokens):
        raise ValueError(f"trailing tokens in {expr}")
    return result


class L10nTestCase(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        l10n.configure(companion.load())

    def args(self, **values):
        defaults = {"since": None, "verbose": True, "strict": False, "lang": None, "out": None, "dry_run": False}
        return SimpleNamespace(**{**defaults, **values})


class ConfigurationTests(L10nTestCase):
    def test_everything_comes_from_companion_json(self):
        self.assertEqual(l10n.SOURCE, "en")
        self.assertEqual(l10n.app_languages(), ["it", "de"])
        self.assertEqual(list(l10n.CATALOGS), ["app"], "l10n.plist is null")
        self.assertEqual(l10n.WINDOWS_STRINGS, self.root / "windows/Fixture.App/Strings")
        self.assertIsNone(l10n.LINUX_PO)
        self.assertEqual(l10n.landing_languages(), [])
        self.assertEqual(l10n.GLOSSARY_KEYS, ["Say something"])
        self.assertIn("Fixy is a fixture", l10n.RULES, "the app's rules file")
        self.assertIn("Keep every format specifier", l10n.RULES, "the generic rules")
        self.assertTrue(l10n.RULES.endswith("Keep the names Fixy as they are."))

    def test_configured_landing_without_initialized_submodule_does_not_change_content_check(self):
        before = l10n.collect()
        config = companion.load()
        config.data["l10n"]["landing"] = "uninitialized-site/src/i18n"
        l10n.configure(config)
        self.assertFalse(l10n.LANDING_I18N.exists())
        self.assertEqual(l10n.landing_languages(), [])
        self.assertEqual(l10n.collect(), before)

    def test_scoped_phrases(self):
        self.assertEqual(l10n.SCOPED, {"phrase.ciao": {"it"}})
        self.assertTrue(l10n.in_scope("phrase.ciao", "it"))
        self.assertTrue(l10n.in_scope("phrase.ciao", "it-CH"))
        self.assertTrue(l10n.in_scope("phrase.ciao", "en"), "the source language always has the key")
        self.assertFalse(l10n.in_scope("phrase.ciao", "de"))
        self.assertTrue(l10n.in_scope("phrase.hello", "de"))
        self.assertEqual(l10n.base_language("pt-BR"), "pt")


class GapTests(L10nTestCase):
    def test_status_skips_keys_out_of_scope(self):
        report = l10n.collect()
        self.assertEqual(report["it"]["app"], {})
        self.assertEqual(
            report["de"]["app"],
            {"Hello. This is how I sound.": ["missing"], "Shh": ["missing"], "phrase.joke.1": ["missing"]},
        )
        result, out = quiet(l10n.cmd_status, self.args())
        self.assertEqual(result, 0)
        self.assertIn("de: app 3", out)
        self.assertNotIn("phrase.ciao", out)
        self.assertEqual(quiet(l10n.cmd_check, self.args())[0], 1)

    def test_a_substitution_needs_every_plural_category(self):
        def plural(values):
            return {"variations": {"plural": {cat: {"stringUnit": {"state": "translated", "value": value}}
                                              for cat, value in values.items()}}}

        def localization(value, lines):
            return {"stringUnit": {"state": "translated", "value": value},
                    "substitutions": {"lines": {"argNum": 1, "formatSpecifier": "lld", **plural(lines)}}}

        entry = {"localizations": {
            "en": localization("%#@lines@ muted", {"one": "%arg line", "other": "%arg lines"}),
            "it": localization("%#@lines@ silenziate", {"one": "%arg battuta"}),
            "de": localization("%#@lines@ stumm", {"one": "%arg Satz", "other": "%arg Sätze"}),
        }}
        self.assertEqual(l10n.english_substitutions(entry)["lines"]["plural"], {"one": "%arg line", "other": "%arg lines"})
        self.assertEqual(l10n.catalog_issues("%lld lines muted", entry, "it"), ["substitution lines: missing other"])
        self.assertEqual(l10n.catalog_issues("%lld lines muted", entry, "de"), [])
        self.assertEqual(l10n.substitution_issues({"localizations": {"en": {}}}, "it"), [], "no substitutions, nothing to check")

    def test_a_number_in_a_substitution_form_keeps_its_position(self):
        """A form is spliced into its string, so `%2$lld` there names the second argument: check wants every form to
        hold `%arg` and the English form's other specifiers, positions included."""
        def localization(forms):
            plural = {cat: {"stringUnit": {"state": "translated", "value": form}} for cat, form in forms.items()}
            return {"stringUnit": {"state": "translated", "value": "%#@earned@"},
                    "substitutions": {"earned": {"argNum": 1, "formatSpecifier": "lld", "variations": {"plural": plural}}}}

        key = "%lld of %lld earned"
        english = localization({"one": "%arg of %2$lld earned", "other": "%arg of %2$lld earned"})
        italian = localization({"one": "%arg di %2$lld ottenuto", "other": "%arg di %2$lld ottenuti"})
        self.assertEqual(l10n.catalog_issues(key, {"localizations": {"en": english, "it": italian}}, "it"), [])
        german = localization({"one": "%arg von %1$lld erhalten", "other": "%2$lld erhalten", "few": "%arg von %lld"})
        self.assertEqual(l10n.catalog_issues(key, {"localizations": {"en": english, "de": german}}, "de"), [
            "substitution earned one: format specifiers ['%2$lld'] != ['%1$lld']",
            "substitution earned other: needs %arg",
            "substitution earned few: format specifiers ['%2$lld'] != ['%lld']",
        ])
        self.assertEqual(l10n.form_problem("%arg lines", "100%%arg"), "needs %arg", "%%arg is a percent sign and arg")
        self.assertEqual(l10n.form_problem("%arg of %2$@", "%arg {x} %2$@"), "placeholders/tags [] != ['{x}']")
        self.assertIsNone(l10n.form_problem("%arg lines, 100%%", "%arg righe, 100%%"), "%% isn't a number")
        self.assertEqual(l10n.form_problem("%arg of %lld", "%arg di %lld"), "%lld needs its position, as %2$lld",
                         "the ports can't place it, even when the English has it so")
        self.assertEqual(l10n.form_problem("%arg (%#x)", "%arg (%#x)"), "%#x needs its position, as %2$lld",
                         "a # flag isn't a substitution")
        self.assertEqual(l10n.form_problem("%arg of %#@total@", "%arg di %#@total@"),
                         "holds another substitution, %#@total@, which the ports can't take")
        swapped = localization({"one": "%arg di %2$lld ottenuto", "other": "%arg di %2$lld ottenuti"})
        swapped["substitutions"]["earned"].update(argNum=2, formatSpecifier="@")
        self.assertEqual(l10n.catalog_issues(key, {"localizations": {"en": english, "it": swapped}}, "it"), [
            "substitution earned: argNum 2 != 1",
            "substitution earned: formatSpecifier '@' != 'lld'",
        ])

    def test_a_translation_keeps_each_argument_where_the_english_has_it(self):
        """Positions may reorder arguments, but a specifier never takes another one's: Apple numbers positionless
        specifiers on their own, so `%#@hours@, %lld min` would show the hours twice, and a type swap crashes."""
        self.assertIsNone(l10n.token_problem("%@ moved %lld", "%2$lld spostati da %1$@"))
        self.assertEqual(l10n.token_problem("%@ moved %lld", "%lld spostati da %@"),
                         "format specifiers ['%1$@', '%2$lld'] != ['%1$lld', '%2$@']")
        self.assertEqual(l10n.token_problem("%#@hours@, %2$lld min", "%#@hours@, %lld min"),
                         "format specifiers ['%#@', '%2$lld'] != ['%#@', '%1$lld']")
        self.assertEqual(l10n.token_problem("%#@hours@, %2$lld min", "%#@hours@, %1$lld min"),
                         "format specifiers ['%#@', '%2$lld'] != ['%#@', '%1$lld']")
        self.assertIsNone(l10n.token_problem("%#@hours@, %2$lld min", "%1$#@hours@, %2$lld min"))
        self.assertIsNone(l10n.token_problem("100%% of %lld", "%lld al 100%%"))
        self.assertIsNone(l10n.token_problem("100% on-device, 0% data sent", "0% dati inviati, 100% sul dispositivo"),
                          "Apple prints a string with no arguments as it is, so a % before a space is text there")
        self.assertEqual(l10n.token_problem("%lld%% done", "%lld % terminé"),
                         "format specifiers ['%1$lld'] != ['%1$lld', '%2$ te']: a percent sign beside a number is %%",
                         "beside an argument, Apple reads `% te` as a specifier and prints '5 0,000000E+00rminé'")
        self.assertIn("['%1$lld', '%2$ d']: a percent sign beside a number is %%",
                      l10n.token_problem("%lld at 50%% off", "%lld à 50 % de réduction"))
        self.assertEqual([l10n.plain(text) for text in ("100% sure", "0% data, 100% sure", "100%% sure", "%lld% done",
                                                        "%#@n@ at 100% done", "no number", "Today 50%OFF",
                                                        "100%ige Sicherheit", "Top 10%@", "%@ sicuro", "100%1$@", "Top 10%#@n@")],
                         [True, True, True, False, False, True, True, True, False, False, False, False],
                         "a % after a digit is a percent sign unless Swift would write it: `Top 10\\(name)`")
        self.assertIsNone(l10n.token_problem("100% sure", "100%ige Sicherheit"), "neither takes an argument")
        self.assertEqual(l10n.token_problem("Done", "%@ fatto"), "format specifiers [] != ['%1$@']")
        self.assertEqual(l10n.token_problem("100% sure", "%@ à 100 % sûr"), "format specifiers [] != ['%1$@', '%2$ s']",
                         "no hint for a percent sign where the English takes no argument")
        self.assertEqual(l10n.token_problem("%lld%% sure", "%lld, 100%ige Sicherheit"),
                         "format specifiers ['%1$lld'] != ['%1$lld', '%2$i']: a percent sign beside a number is %%")
        self.assertEqual(l10n.token_problem("%lld%% done", "Fatto al 100%%"), "format specifiers ['%1$lld'] != []",
                         "no hint when the percent sign is already %%")
        self.assertEqual(l10n.specifiers("%@ %2$@ %@"), ["%1$@", "%2$@", "%2$@"], "Apple's count skips %2$@")

    def test_a_percent_sign_is_written_as_the_source_writes_it(self):
        """Apple formats a string only when the call passes arguments, as the source decides for every language: then a
        percent sign is `%%` (a `%` before a space or right after a number is a specifier, one that starts no specifier
        is dropped), and without arguments a plain `%` (`%%` shows twice). A plural form is always formatted, even
        without a number."""
        stray = "Apple drops a % that starts no specifier, at times with the letter after it: write the percent sign as %%"
        specifier = "is a specifier to Apple, beside the string's arguments, and prints one that isn't there: write the " \
                    "percent sign as %%"
        twice = "Apple prints a string that takes no arguments as it is, so %% shows twice: write one %"
        digit = "after a number is a specifier to Apple, not a percent sign: write the percent sign as %%"
        self.assertEqual(l10n.percent_problem("Batterie à %lld %"), f"'%': {stray}", "Apple prints 'Batterie à 5 '")
        self.assertEqual(l10n.percent_problem("%lld % mehr"), f"'% m': {stray}")
        self.assertEqual(l10n.percent_problem("%lld %, puis"), f"'%, ': {stray}")
        self.assertEqual(l10n.percent_problem("100 %, puis %lld"), f"'%, ': {stray}", "before a specifier too")
        self.assertEqual(l10n.percent_problem("%lld % % sûr"), "'% %' prints as ' %': write the percent sign as %%")
        self.assertEqual(l10n.percent_problem("%lld %5% x"), "'%5%' prints as '5%': write the percent sign as %%",
                         "Apple drops the first %, and prints what follows it")
        self.assertEqual(l10n.percent_problem("One item, 50%OFF", True), f"'%O' {digit}", "Apple prints '505FF'")
        self.assertEqual(l10n.percent_problem("%lld left, 100%ige"), f"'%i' {digit}", "Apple prints '1000ge'")
        self.assertEqual(l10n.percent_problem("%arg file, 100%safe", True, form=True), f"'%s' {digit}",
                         "%s reads an argument as a C string")
        self.assertIsNone(l10n.percent_problem("Top 10%@", True), "what Swift writes for an interpolation")
        for text in ("Room 1%02d", "Room 1%02d is 100%% ready", "Season 20%02d, %lld runs", "Lap 1%.1f", "Floors 1%02d"):
            self.assertFalse(l10n.plain(text), f"{text!r}: Text's specifier: writes flags, width and precision too")
            self.assertIsNone(l10n.percent_problem(text, True if text.startswith("Floors") else None), text)
        self.assertEqual([l10n.to_net_format("Room 1%02d"), l10n.to_net_format("Room 1%02d is 100%% ready")],
                         ["Room 1{0}", "Room 1{0} is 100% ready"])
        self.assertIsNone(l10n.token_problem("Season 20%02d, %lld runs", "Saison 20%02d, %lld Läufe"))
        for text in ("U+00%X", "Room 1%x", "Scale 1%g", "%@: U+00%X"):
            self.assertFalse(l10n.plain(text), f"{text!r}: a bare specifier: value right after a digit, no word after it")
        self.assertEqual(l10n.to_net_format("U+00%X"), "U+00{0}")
        for text in ("100%-Garantie", "Get 50%-off", "50%-a az eszközön", "100%legit", "100%OK"):
            self.assertTrue(l10n.plain(text), f"{text!r}: a hyphen or a word after a number's % is text")
        self.assertEqual(l10n.to_net_format("100%-free"), "100%-free")
        self.assertIsNone(l10n.token_problem("100% on-device", "100%-os adatvédelem"))
        self.assertIsNone(l10n.token_problem("100% on-device", "100%-garantie op het apparaat"))
        self.assertEqual(l10n.percent_problem("%lld elem, 100%-os", True), f"'%-o' {digit}", "Apple prints '10017141s'")
        self.assertIsNone(l10n.percent_problem("%1$lld–%2$lld", True))
        self.assertIsNone(l10n.percent_problem("100%ige Sicherheit"), "a string that takes no arguments prints it as is")
        self.assertEqual(l10n.percent_problem("%lld % fatto"), f"'% f' {specifier}")
        self.assertEqual(l10n.percent_problem("%#@n@, 100% fatto"), f"'% f' {specifier}", "a substitution formats")
        self.assertEqual(l10n.percent_problem("%arg al 100% fatto", True, form=True), f"'% f' {specifier}")
        self.assertIsNone(l10n.percent_problem("%arg al 100%% fatto", True, form=True))
        self.assertIsNone(l10n.percent_problem("%lld%% done"))
        self.assertIsNone(l10n.percent_problem("%1$#@n@ (%2$lld%%)"))
        self.assertIsNone(l10n.percent_problem("100% sure"))
        self.assertEqual(l10n.percent_problem("100%% sure"), twice)
        self.assertEqual(l10n.percent_problem("Sûr à 100 %%", False), twice)
        self.assertEqual(l10n.percent_problem("One at 100% effort", True), f"'% e' {specifier}")

        def unit(value):
            return {"stringUnit": {"state": "translated", "value": value}}

        days = {"localizations": {"en": {"variations": {"plural": {"one": unit("A day"), "other": unit("Days")}}}}}
        self.assertEqual([l10n.formats(days, "streak.days"), l10n.formats({"localizations": {"en": unit("100% sure")}}),
                          l10n.formats({}, "%lld%% done"), l10n.formats({}, "100% sure"), l10n.formats({})],
                         [True, False, True, False, None], "a plural formats, whatever its key")
        own = {"localizations": {"en": unit("Done"), "it": {"stringUnit": {"state": "translated",
               "value": "%#@n@ fatto al 100%%"}, "substitutions": {"n": {"argNum": 1, "formatSpecifier": "lld",
               "variations": {"plural": {"one": unit("%arg"), "other": unit("%arg")}}}}}}}
        self.assertIsNone(l10n.number_problem(own, "Done"), "a value with a substitution formats, whatever the English")

        plain = {"localizations": {"en": unit("100% sure"), "it": unit("Sicuro al 100%"),
                                   "de": unit("100%ige Sicherheit")}}
        self.assertIsNone(l10n.number_problem(plain, "100% sure"))
        plain["localizations"]["fr"] = unit("Sûr à 100 %%")
        self.assertEqual(l10n.number_problem(plain, "100% sure"), f"fr 'Sûr à 100 %%': {twice}")
        bare = {"localizations": {"fr": unit("Sûr à 100 %")}}
        self.assertIsNone(l10n.number_problem(bare, "100% sure"), "without an en unit the key is the English")
        self.assertEqual(l10n.number_problem(bare, "%lld % done"), f"en '%lld % done': '% d' {specifier}")
        formatted = {"localizations": {"en": unit("%lld%% done"), "fr": unit("%lld % fait")}}
        self.assertEqual(l10n.number_problem(formatted, "%lld%% done"), f"fr '%lld % fait': '% f' {specifier}")
        effort = {"localizations": {"en": {"variations": {"plural": {
            "one": unit("One exercise at 100% effort"), "other": unit("%lld exercises at 100%% effort")}}}}}
        self.assertEqual(l10n.number_problem(effort, "%lld exercises at 100%% effort"),
                         f"en 'One exercise at 100% effort': '% e' {specifier}",
                         "every plural form is formatted, so '% e' is %e with a space flag, reading a missing double")
        safe = {"localizations": {"en": {"variations": {"plural": {
            "one": unit("One file, 100%safe"), "other": unit("%lld files, 100%% safe")}}}}}
        self.assertEqual(l10n.number_problem(safe, "%lld files, 100%% safe"), f"en 'One file, 100%safe': '%s' {digit}",
                         "a numberless form is formatted too: at a count of one Apple crashes there")

        self.assertEqual(l10n.validate_item("100% sure", {"value": "Sûr à 100 %%"}, plain, "fr"), [twice])
        self.assertEqual(l10n.validate_item("100% sure", {"value": "100%ige Sicherheit"}, plain, "de"), [])
        self.assertEqual(l10n.validate_item("%lld%% done", {"value": "%lld %, fait"}, formatted, "fr"),
                         [f"'%, ': {stray}"])
        self.assertEqual(l10n.validate_item("%lld exercises at 100%% effort", {"value": {
            "one": "Un exercice à 100 % d'effort", "other": "%lld exercices à 100 %% d'effort"}}, effort, "fr"), [
            "one: format specifiers ['%1$lld'] != []: a percent sign beside a number is %%"],
            "a translated form keeps the number, and the hint names the percent sign")
        self.assertEqual(l10n.validate_item("%lld exercises at 100%% effort", {"value": {
            "one": "%lld exercice à 100 %", "other": "%lld exercices à 100 %%"}}, effort, "fr"), [
            f"one: '%': {stray}"], "a stray % takes no argument, so only percent_problem sees it")
        self.assertEqual(l10n.validate_item("%lld exercises at 100%% effort", {"value": {
            "one": "%lld exercice, 50%OFF", "other": "%lld exercices à 100 %% d'effort"}}, effort, "fr"), [
            "one: format specifiers ['%1$lld'] != ['%1$lld', '%2$O']: a percent sign beside a number is %%"],
            "in a formatted translation '%O' after a number counts as an argument")
        promoted = {"en_plural": {"one": "One left, 100% done"}, "value": {"one": "x", "other": "y"}}
        self.assertEqual(l10n.validate_item("%lld left, 100%% done", promoted, {}, "it"), [
            "en_plural.one: format specifiers ['%1$lld'] != []: a percent sign beside a number is %%"])
        promoted["en_plural"]["one"] = "%lld left, 100%"
        self.assertEqual(l10n.validate_item("%lld left, 100%% done", promoted, {}, "it"), [f"en_plural.one: '%': {stray}"])

        added, errors = l10n.add_strings({"%lld% done": {"en": "%lld% done"},
                                          "%lld exercises at 100%% effort": {"plural": {
                                              "one": "One exercise at 100% effort",
                                              "other": "%lld exercises at 100%% effort"}}})
        self.assertEqual((added, errors), ([], [f"'%lld% done': '% d' {specifier}",
                                                f"'%lld exercises at 100%% effort': '% e' {specifier}"]))
        self.assertEqual(l10n.add_strings({"%lld items, 50%%OFF": {"plural": {
            "one": "One item, 50%OFF", "other": "%lld items, 50%%OFF"}}}),
            ([], [f"'%lld items, 50%%OFF': '%O' {digit}"]))
        self.assertEqual(l10n.add_strings({"%lld left, 100%% done": {"plural": {
            "one": "%lld left, 100%ige", "other": "%lld left, 100%% done"}}}),
            ([], [f"'%lld left, 100%% done': '%i' {digit}"]), "the percent sign, not the two numbers it makes")
        self.assertEqual(l10n.add_strings({"%lld left, 100%% free": {"plural": {
            "one": "%lld left, 100%-free", "other": "%lld left, 100%% free"}}}),
            ([], [f"'%lld left, 100%% free': '%-f' {digit}"]))

    def test_a_plural_over_two_numbers_is_reported(self):
        """Xcode reads a plural's forms together: it can't tell which of two numbers counts, positions or not, and
        can't build one number spelled two ways; export offers no English plural to create for either, and neither
        `add` nor `apply` writes one."""
        def unit(value):
            return {"stringUnit": {"state": "translated", "value": value}}

        def plural(value, lang="en", one=None):
            forms = {"one": unit(value if one is None else one), "other": unit(value)}
            return {"localizations": {lang: {"variations": {"plural": forms}}}}

        cases = {
            "%lld of %lld earned": True, "%02lld:%02lld": True, "%lld breaks, %.1f min": True, "%lld:%02lld": True,
            "%lld and %1$ld": True, "%1$lld of %1$02lld": True,
            "%1$lld min (%1$lld)": False, "%lld min (%1$lld)": False, "%lld at 100%%d": False, "%lld of %@": False,
            "%lld done, 100%% sure": False, "%lld done, 100% sure": True, "%lld done, 100% done": True,
        }
        for value, ambiguous in cases.items():
            self.assertEqual(l10n.number_problem(plural(value)) is not None, ambiguous, value)
        self.assertEqual(l10n.number_problem(plural("%1$lld / %2$lld 獲得", "ja")),
                         "ja '%1$lld / %2$lld 獲得' is a plural over 2 numbers, so Xcode can't tell which one counts: "
                         "count it through a substitution, %#@name@")
        self.assertEqual(l10n.number_problem(plural("%2$lld items", one="%lld item")),
                         "en '%lld item' / '%2$lld items' is a plural over 2 numbers, so Xcode can't tell which one "
                         "counts: count it through a substitution, %#@name@", "one number in each form")
        self.assertEqual(l10n.number_problem(plural("%1$lld items", one="%ld item")),
                         "en '%ld item' / '%1$lld items' is a plural spelling its number two ways, %ld and %lld, which "
                         "fails Xcode's build: spell it one way")
        self.assertIn("and spelled two ways (%.1f, %lld) it fails the build",
                      l10n.number_problem(plural("%lld breaks, %.1f min")))
        self.assertIsNone(l10n.number_problem(plural("%lld breaks", one="one break")), "a form may hold no number")
        self.assertEqual(l10n.number_problem(plural("%lld done, 100% sure")),
                         "en '%lld done, 100% sure': '% s' is a specifier to Apple, beside the string's arguments, and "
                         "prints one that isn't there: write the percent sign as %%", "Apple prints '5 done, 100(null)ure'")

        catalog = l10n.load_catalog(l10n.CATALOGS["app"])
        catalog["strings"]["%lld of %lld earned"] = {"localizations": {
            **plural("%lld of %lld earned")["localizations"], **plural("%1$lld / %2$lld 獲得", "ja")["localizations"]}}
        l10n.dump_catalog(l10n.CATALOGS["app"], catalog)
        self.assertEqual(list(l10n.number_problems()["app"]), ["%lld of %lld earned"])
        status, out = quiet(l10n.cmd_status, self.args())
        self.assertEqual(status, 0)
        self.assertIn("numbers Apple can't place in Localizable.xcstrings: 1\n  '%lld of %lld earned': en '%lld of "
                      "%lld earned' is a plural over 2 numbers", out)
        for key, offered in {"%lld of %lld done": False, "%lld breaks, %.1f min": False, "%lld:%02lld": False,
                             "%.1f min": False, "%lld and %1$ld": False, "%lld done": True, "%02lld min": True,
                             "%lld of %@": True, "%lld at 100%%d": True, "%lld left, 100%% sure": True,
                             "%lld left, 100% done": False}.items():
            self.assertEqual("en_plural" in l10n.work_item(key, {}, "de", ["missing"]), offered, key)

        added, errors = l10n.add_strings({"%lld of %lld on Windows": {"plural": {
            "one": "%lld of %lld on Windows", "other": "%lld of %lld on Windows"}}})
        self.assertEqual((added, errors), ([], [
            "'%lld of %lld on Windows': a plural over 2 numbers, so Xcode can't tell which one counts: count it through "
            "a substitution, %#@name@"]))
        promoted = {"en_plural": {"one": "%ld break", "other": "%lld breaks"}, "value": {"one": "x", "other": "y"}}
        self.assertEqual(l10n.validate_item("%lld breaks", promoted, {}, "it"), [
            "en_plural.one: format specifiers ['%1$lld'] != ['%1$ld']"])
        two = "a plural over 2 numbers, so Xcode can't tell which one counts: count it through a substitution, %#@name@"
        promoted = {"en_plural": {"one": "%lld of %lld"}, "value": {"one": "%lld di %lld", "other": "%lld di %lld"}}
        self.assertEqual(l10n.validate_item("%lld of %lld", promoted, {}, "it"), [f"en_plural.one: {two}"])
        self.assertEqual(l10n.validate_item("%lld of %lld", {"value": {"one": "%lld di %lld", "other": "%lld di %lld"}},
                                            plural("%lld of %lld"), "it"), [two], "nor apply a translation of one")

    def test_a_number_without_a_position_never_takes_a_substitutions_argument(self):
        """Apple numbers positionless specifiers without counting a substitution, so `%#@hours@, %lld min` prints the
        hours twice: reported in any language, the English too, since every translation matches it."""
        def localization(value, arg=1):
            forms = {cat: {"stringUnit": {"state": "translated", "value": "%arg hr"}} for cat in ("one", "other")}
            return {"stringUnit": {"state": "translated", "value": value},
                    "substitutions": {"hours": {"argNum": arg, "formatSpecifier": "lld",
                                                "variations": {"plural": forms}}}}

        self.assertEqual(l10n.number_problem({"localizations": {"en": localization("%#@hours@, %lld min")}}),
                         "en '%#@hours@, %lld min': %lld takes argument 1, which %#@hours@ counts, as Apple numbers it: "
                         "give it the position it means, as %2$lld")
        self.assertIn("de ", l10n.number_problem({"localizations": {
            "en": localization("%#@hours@, %2$lld min"), "de": localization("%lld Min., %#@hours@")}}))
        self.assertIn("%@ takes argument 1", l10n.number_problem({"localizations": {"en": localization("%@: %#@hours@")}}),
                      "an object is an argument too")
        for fine, arg in (("%#@hours@, %2$lld min", 1), ("%1$#@hours@ (%1$lld)", 1), ("%@: %#@hours@", 2),
                          ("noch %#@hours@, %lld fertig", 2), ("%2$#@hours@ (%1$@)", 2)):
            self.assertIsNone(l10n.number_problem({"localizations": {"de": localization(fine, arg)}}), fine)

        self.assertEqual(l10n.number_problem({"localizations": {
            "en": localization("%#@hours@, %2$lld min"), "de": localization("%2$#@hours@, %2$lld Min.")}}),
            "de '%2$#@hours@, %2$lld Min.': %2$#@hours@ picks its plural form by argument 2 but prints argument 1, its "
            "argNum: give it the same position, or none")
        unnumbered = localization("%#@hours@")
        del unnumbered["substitutions"]["hours"]["argNum"]
        self.assertEqual(l10n.number_problem({"localizations": {"en": unnumbered}}),
                         "en '%#@hours@': %#@hours@ has no argNum: give it the number of the argument it counts, as "
                         "Xcode writes it")
        self.assertEqual(l10n.catalog_issues("%lld hr", {"localizations": {"en": unnumbered, "de": localization("%#@hours@")}},
                                             "de"), ["substitution hours: argNum 1 != None"], "no KeyError")
        unspecified = localization("%#@hours@")
        del unspecified["substitutions"]["hours"]["formatSpecifier"]
        self.assertEqual(l10n.number_problem({"localizations": {"en": unspecified}}),
                         "en '%#@hours@': %#@hours@ has no formatSpecifier: give it the conversion of the number it "
                         "counts, as lld, as Xcode writes it", "xcstringstool fails the build without one")
        item = {"value": "%#@hours@", "substitutions": {"hours": {"value": {"one": "%arg Std.", "other": "%arg Std."}}}}
        self.assertEqual(l10n.validate_item("%lld hr", item, {"localizations": {"en": unspecified}}, "de"), [
            "%#@hours@ has no formatSpecifier: give it the conversion of the number it counts, as lld, as Xcode "
            "writes it"], "so apply never writes a null one")

        loose = localization("%#@hours@")
        loose["substitutions"]["hours"]["variations"]["plural"]["other"]["stringUnit"]["value"] = "%arg of %lld"
        self.assertEqual(l10n.number_problem({"localizations": {"de": loose, "en": loose}}),
                         "en substitution hours other '%arg of %lld': %lld needs its position, as %2$lld",
                         "the English is read first, so the form every translation copies is named where it's made")

        english = {"localizations": {"en": localization("%#@hours@, %lld min")}}
        item = {"value": "%#@hours@, %lld Min.", "substitutions": {"hours": {"value": {"one": "%arg Std.",
                                                                                         "other": "%arg Std."}}}}
        self.assertEqual(l10n.validate_item("%lld hr, %lld min", item, english, "de"), [
            "%lld takes argument 1, which %#@hours@ counts, as Apple numbers it: give it the position it means, as %2$lld"])

    def test_export_and_apply(self):
        out = self.root / "work"
        listed = quiet(l10n.cmd_export, self.args(out=str(out)))[1]
        self.assertEqual(listed.splitlines(), ["work/de.json (3)", "3 distinct strings in 1 file"])
        self.assertFalse((out / "it.json").exists(), "nothing to translate in Italian")
        work = json.loads((out / "de.json").read_text("utf8"))
        self.assertEqual(list(work["app"]), ["Hello. This is how I sound.", "Shh", "phrase.joke.1"])
        self.assertEqual(work["glossary"], {"Say something": "Sag etwas"})
        self.assertEqual(work["rules"], l10n.RULES)
        self.assertEqual(work["app"]["phrase.joke.1"]["comment"], "Fixy says this aloud. Part 1 of 2.")
        work["app"]["Shh"]["value"] = "Pst"
        work["app"]["phrase.joke.1"]["value"] = "Warum hat sich der Clown hingesetzt? %@"
        (out / "de.json").write_text(json.dumps(work, ensure_ascii=False), "utf8")
        result, text = quiet(l10n.cmd_apply, SimpleNamespace(files=[str(out / "de.json")]))
        self.assertEqual(result, 1, "a specifier the English lacks is rejected")
        self.assertIn("applied 1", text)
        self.assertIn("regenerated 3 of 3 exports in windows/Fixture.App/Strings", text, "what went in is exported")
        self.assertEqual(
            l10n.collect()["de"]["app"], {"Hello. This is how I sound.": ["missing"], "phrase.joke.1": ["missing"]}
        )

    def test_export_ends_with_the_batch_size(self):
        """The distinct strings across the files: what the localization skill's threshold counts."""
        catalog = l10n.load_catalog(l10n.CATALOGS["app"])
        del catalog["strings"]["Shh"]["localizations"]["it"]
        l10n.dump_catalog(l10n.CATALOGS["app"], catalog)
        out = str(self.root / "work")
        listed = quiet(l10n.cmd_export, self.args(out=out))[1]
        self.assertEqual(
            listed.splitlines(), ["work/de.json (3)", "work/it.json (1)", "3 distinct strings in 2 files"],
            "a string two languages lack counts once",
        )
        listed = quiet(l10n.cmd_export, self.args(out=out, lang=["it"]))[1]
        self.assertEqual(listed.splitlines(), ["work/it.json (1)", "1 distinct string in 1 file"])
        (self.root / "work" / "it.json").write_text(json.dumps({"lang": "it", "app": {"Shh": {"value": "Zitto"}}}), "utf8")
        quiet(l10n.cmd_apply, SimpleNamespace(files=[str(self.root / "work" / "it.json")]))
        self.assertEqual(quiet(l10n.cmd_export, self.args(out=out, lang=["it"]))[1], "nothing to translate\n")

    def test_glossary_sources_add_terms_from_the_code(self):
        (self.root / "macos/Fixture/Items.swift").write_text(
            'Item(name: String(localized: "Shh"))\nItem(name: String(localized: "Say something"))\n', "utf8"
        )
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["l10n"]["glossarySources"] = [
            {"file": "macos/Fixture/Items.swift", "pattern": r'\bname:\s*String\(localized:\s*"([^"]+)"'}
        ]
        path.write_text(json.dumps(data), "utf8")
        l10n.configure(companion.load())
        self.assertEqual(l10n.glossary_keys(), ["Say something", "Shh"], "the fixed keys first, each term once")
        strings = l10n.load_catalog(l10n.CATALOGS["app"])["strings"]
        self.assertEqual(l10n.glossary(strings, "it"), {"Say something": "Dì qualcosa", "Shh": "Zitto"})

    def test_substitutions_round_trip(self):
        """A `%#@name@` string exports with a slot per substitution and applies with argNum and format kept."""
        key = "Muted %lld lines in %@."

        def unit(value):
            return {"stringUnit": {"state": "translated", "value": value}}

        catalog = l10n.load_catalog(l10n.CATALOGS["app"])
        catalog["strings"][key] = {"localizations": {"en": {
            **unit("Muted %#@lines@ in %2$@."),
            "substitutions": {"lines": {"argNum": 1, "formatSpecifier": "lld", "variations": {"plural": {
                "one": unit("%arg line"), "other": unit("%arg lines")}}}},
        }}}
        l10n.dump_catalog(l10n.CATALOGS["app"], catalog)
        out = self.root / "work"
        quiet(l10n.cmd_export, self.args(lang=["de"], out=str(out)))
        work = json.loads((out / "de.json").read_text("utf8"))
        item = work["app"][key]
        self.assertEqual(item["substitutions"]["lines"]["categories"], ["one", "other"])
        self.assertNotIn("en_plural", item)
        item["value"] = "%#@lines@ in %2$@ stummgeschaltet."
        item["substitutions"]["lines"]["value"] = {"one": "%arg Zeile", "other": "Zeilen"}
        (out / "de.json").write_text(json.dumps(work, ensure_ascii=False), "utf8")
        result, text = quiet(l10n.cmd_apply, SimpleNamespace(files=[str(out / "de.json")]))
        self.assertEqual(result, 1)
        self.assertIn("substitution lines other: needs %arg", text)
        item["substitutions"]["lines"]["value"]["other"] = "%arg Zeilen"
        (out / "de.json").write_text(json.dumps(work, ensure_ascii=False), "utf8")
        self.assertEqual(quiet(l10n.cmd_apply, SimpleNamespace(files=[str(out / "de.json")]))[0], 0)
        entry = l10n.load_catalog(l10n.CATALOGS["app"])["strings"][key]
        self.assertEqual(l10n.catalog_issues(key, entry, "de"), [])
        lines = entry["localizations"]["de"]["substitutions"]["lines"]
        self.assertEqual((lines["argNum"], lines["formatSpecifier"]), (1, "lld"))
        self.assertEqual(lines["variations"]["plural"]["other"]["stringUnit"]["value"], "%arg Zeilen")

    def test_a_substitution_over_the_whole_phrase_round_trips(self):
        """A substitution can hold the whole phrase and the string's other number (`%2$lld`), for a language whose
        words agree after it: apply wants that number where the English form has it, and the ports get both."""
        key = "%lld of %lld earned"

        def unit(value):
            return {"stringUnit": {"state": "translated", "value": value}}

        catalog = l10n.load_catalog(l10n.CATALOGS["app"])
        catalog["strings"][key] = {"localizations": {"en": {
            **unit("%#@earned@"),
            "substitutions": {"earned": {"argNum": 1, "formatSpecifier": "lld", "variations": {"plural": {
                "one": unit("%arg of %2$lld earned"), "other": unit("%arg of %2$lld earned")}}}},
        }}}
        l10n.dump_catalog(l10n.CATALOGS["app"], catalog)
        out = self.root / "work"
        quiet(l10n.cmd_export, self.args(lang=["de"], out=str(out)))
        work = json.loads((out / "de.json").read_text("utf8"))
        item = work["app"][key]
        self.assertEqual(item["substitutions"]["earned"]["en"]["other"], "%arg of %2$lld earned")
        item["value"] = "%#@earned@"
        item["substitutions"]["earned"]["value"] = {"one": "%arg von %lld erhalten", "other": "%arg von %2$lld erhalten"}
        (out / "de.json").write_text(json.dumps(work, ensure_ascii=False), "utf8")
        result, text = quiet(l10n.cmd_apply, SimpleNamespace(files=[str(out / "de.json")]))
        self.assertEqual(result, 1)
        self.assertIn("substitution earned one: format specifiers ['%2$lld'] != ['%lld']", text)
        item["substitutions"]["earned"]["value"]["one"] = "%arg von %2$lld erhalten"
        (out / "de.json").write_text(json.dumps(work, ensure_ascii=False), "utf8")
        self.assertEqual(quiet(l10n.cmd_apply, SimpleNamespace(files=[str(out / "de.json")]))[0], 0)
        units = {lang: {u["key"]: u for u in l10n.export_units(lang)}[key] for lang in ("en", "de")}
        self.assertEqual(units["de"]["plural"], {"one": "{0} von {1} erhalten", "other": "{0} von {1} erhalten"})
        self.assertEqual(units["de"]["countArg"], 0)
        self.assertEqual(units["en"]["plural"], {"one": "{0} of {1} earned", "other": "{0} of {1} earned"})
        self.assertEqual(l10n.stale_exports(), [], "apply wrote the ports' files")


class ApplyExportsTests(L10nTestCase):
    """`apply` leaves the ports' exports in line with the catalog it wrote, so `check` passes with no export step."""

    GERMAN = {
        "Hello. This is how I sound.": "Hallo. So klinge ich.",
        "Shh": "Pst",
        "phrase.joke.1": "Warum hat sich der Clown hingesetzt?",
    }
    STALE = ("error: the catalog was written, but the exports made from it are stale: fix that, then run "
             "`export-windows` / `export-linux` / `export-android`")

    def reconfigure(self, change) -> None:
        """Loads the fixture's companion.json after `change(data)`."""
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        change(data)
        path.write_text(json.dumps(data), "utf8")
        l10n.configure(companion.load())

    def translated(self) -> Path:
        """German's work file with every gap filled."""
        out = self.root / "work"
        quiet(l10n.cmd_export, self.args(out=str(out)))
        work = json.loads((out / "de.json").read_text("utf8"))
        for key, item in work["app"].items():
            item["value"] = self.GERMAN[key]
        (out / "de.json").write_text(json.dumps(work, ensure_ascii=False), "utf8")
        return out / "de.json"

    def apply(self, file: Path) -> tuple[int, str, str]:
        """`apply` in this process: its result, what it printed and what it said went wrong."""
        err = io.StringIO()
        with redirect_stderr(err):
            result, out = quiet(l10n.cmd_apply, SimpleNamespace(files=[str(file)]))
        return result, out, err.getvalue()

    def run_tool(self, *args: str, **streams) -> subprocess.CompletedProcess:
        """`l10n.py` as an app runs it, from the fixture's root."""
        streams = streams or {"capture_output": True}
        return subprocess.run([sys.executable, str(TOOLS / "l10n.py"), *args], cwd=self.root, encoding="utf8", **streams)

    def test_check_passes_straight_after_apply_with_windows_and_android_sides(self):
        def sides(data):
            data["android"] = {"dir": "android", "strings": "android/app/src/main/res"}
            data["l10n"]["linuxPo"] = "linux/po"  # named, but the app has no Linux port

        self.reconfigure(sides)
        (self.root / "android").mkdir()
        file = self.translated()
        self.assertEqual(len(l10n.stale_exports()), 7, "three .resw, three strings.xml and locales_config.xml")
        applied = self.run_tool("apply", str(file.parent / "*.json"))
        self.assertEqual((applied.returncode, applied.stderr), (0, ""))
        self.assertEqual(applied.stdout.splitlines(), [
            "applied 3, skipped 0 empty, rejected 0",
            "regenerated 3 of 3 exports in windows/Fixture.App/Strings",
            "regenerated 4 of 4 exports in android/app/src/main/res",
        ])
        checked = self.run_tool("check")
        self.assertEqual((checked.returncode, checked.stdout), (0, "de: ok\nit: ok\n"), checked.stderr)
        self.assertIn("<value>Pst</value>", (self.root / "windows/Fixture.App/Strings/de/Resources.resw").read_text("utf8"))
        res = self.root / "android/app/src/main/res"
        self.assertIn(">Pst</string>", (res / "values-de" / "strings.xml").read_text("utf8"))
        self.assertTrue((res / "xml" / "locales_config.xml").is_file())
        self.assertFalse((self.root / "linux").exists(), "a port without a folder gets no files: check asks for none")
        again = self.run_tool("apply", str(file))
        self.assertEqual(again.stdout, "applied 3, skipped 0 empty, rejected 0\n", "nothing differs, nothing is rewritten")

    def test_check_passes_straight_after_apply_without_ports(self):
        self.reconfigure(lambda data: data.pop("windows"))
        self.assertEqual(l10n.export_ports(), [])
        self.assertEqual(self.apply(self.translated()), (0, "applied 3, skipped 0 empty, rejected 0\n", ""))
        self.assertEqual(quiet(l10n.cmd_check, self.args(verbose=False)), (0, "de: ok\nit: ok\n"))
        self.assertFalse((self.root / "windows" / "Fixture.App").exists(), "nothing but the catalog is written")
        catalog = l10n.load_catalog(l10n.CATALOGS["app"])
        unit = {"stringUnit": {"state": "translated", "value": "%lld of %lld"}}
        catalog["strings"]["%lld of %lld"] = {"shouldTranslate": False, "localizations": {
            "en": {"variations": {"plural": {"one": unit, "other": unit}}}}}
        l10n.dump_catalog(l10n.CATALOGS["app"], catalog)
        result, out = quiet(l10n.cmd_check, self.args(verbose=False))
        self.assertEqual((result, out.splitlines()[:3]), (1, [
            "de: ok", "it: ok", "numbers Apple can't place in Localizable.xcstrings: 1"]), "a number problem alone fails check")

    def test_a_string_no_port_can_take_fails_apply_after_the_catalog_is_written(self):
        def unit(value):
            return {"stringUnit": {"state": "translated", "value": value}}

        def count(number, one, other):
            return {"argNum": number, "formatSpecifier": "lld", "variations": {"plural": {"one": unit(one), "other": unit(other)}}}

        file = self.translated()
        catalog = l10n.load_catalog(l10n.CATALOGS["app"])
        catalog["strings"]["%lld lines, %lld jokes"] = {"localizations": {"en": {
            **unit("%#@lines@, %#@jokes@"),
            "substitutions": {"lines": count(1, "%arg line", "%arg lines"), "jokes": count(2, "%arg joke", "%arg jokes")},
        }}}
        l10n.dump_catalog(l10n.CATALOGS["app"], catalog)
        # Both streams in one pipe, as a terminal or an agent reads them: the summary, the reason, then where it leaves things.
        done = self.run_tool("apply", str(file), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(done.stdout.splitlines(), [
            "applied 3, skipped 0 empty, rejected 0",
            "error: '%lld lines, %lld jokes' (en): only one plural substitution per string is supported",
            self.STALE,
        ])
        self.assertEqual(done.returncode, 2)
        strings = l10n.load_catalog(l10n.CATALOGS["app"])["strings"]
        self.assertEqual(strings["Shh"]["localizations"]["de"]["stringUnit"]["value"], "Pst")
        self.assertFalse((self.root / "windows" / "Fixture.App").exists(), "every export is made before one is written")

    def test_an_export_that_cant_be_written_fails_apply_with_the_reason(self):
        l10n.WINDOWS_STRINGS.mkdir(parents=True)
        (l10n.WINDOWS_STRINGS / "de").write_text("", "utf8")  # a file where German's folder goes
        file = self.translated()
        result, out, err = self.apply(file)
        self.assertEqual((result, out), (2, "applied 3, skipped 0 empty, rejected 0\n"))
        reason, stale = err.splitlines()
        self.assertTrue(reason.startswith("error: [") and "Strings" in reason, reason)
        self.assertEqual(stale, self.STALE)
        self.assertEqual(l10n.collect()["de"]["app"], {}, "the translations are in the catalog")
        file.write_text(json.dumps({"lang": "de", "app": {"Shh": {"value": ""}}}), "utf8")
        result, out, err = self.apply(file)
        self.assertEqual((result, out), (2, "applied 0, skipped 1 empty, rejected 0\n"))
        self.assertIn("error: the catalog was left as it was, but the exports made from it are stale", err)

    def test_an_export_that_breaks_says_the_catalog_was_written_and_keeps_its_traceback(self):
        file = self.translated()
        err = io.StringIO()
        with mock.patch.object(l10n, "windows_files", side_effect=KeyError("other")), redirect_stderr(err):
            with self.assertRaises(KeyError):
                quiet(l10n.cmd_apply, SimpleNamespace(files=[str(file)]))
        self.assertEqual(err.getvalue().splitlines(), [self.STALE])
        self.assertEqual(l10n.collect()["de"]["app"], {}, "the translations are in the catalog")


class ExportTests(L10nTestCase):
    def setUp(self):
        super().setUp()
        self.resw = l10n.windows_files(self.root / "windows")
        self.po = l10n.linux_files(self.root / "linux")
        self.android = l10n.android_files(self.root / "android")
        self.units = {lang: l10n.export_units(lang) for lang in l10n.export_languages()}

    def test_every_language_is_exported(self):
        self.assertEqual(l10n.export_languages(), ["en", "it", "de"])
        self.assertEqual(len(self.resw), 3)
        self.assertEqual(sorted(path.name for path in self.po), ["LINGUAS", "de.po", "en.po", "it.po"])
        self.assertEqual(sorted(path.parent.name for path in self.android), ["values", "values-de", "values-it", "xml"])
        self.assertEqual(l10n.android_values_dir("zh-Hans"), "values-zh-rCN")
        self.assertEqual(l10n.android_values_dir("pt-BR"), "values-pt-rBR")
        self.assertEqual(self.android[self.root / "android" / "xml" / "locales_config.xml"], (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!-- Generated from macos/Fixture/Resources/Localizable.xcstrings by laching/tools/l10n.py export-android. "
            "Don't edit. -->\n"
            '<locale-config xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '  <locale android:name="en"/>\n  <locale android:name="it"/>\n  <locale android:name="de"/>\n'
            "</locale-config>\n"))
        self.assertEqual(l10n.android_locale("zh-Hans"), "zh-CN", "what values-zh-rCN says")
        self.assertEqual(l10n.android_locale("pt-BR"), "pt-BR")

    def test_a_scoped_phrase_is_left_out_of_other_languages(self):
        keys = {lang: {unit["key"] for unit in units} for lang, units in self.units.items()}
        self.assertIn("phrase.ciao", keys["en"])
        self.assertIn("phrase.ciao", keys["it"])
        self.assertNotIn("phrase.ciao", keys["de"])
        self.assertIn("phrase.joke.1", keys["de"], "a missing translation falls back to English, still exported")
        de = (self.root / "windows" / "de" / "Resources.resw")
        self.assertNotIn(l10n.resw_name("phrase.ciao"), self.resw[de])

    def test_no_apple_specifiers_remain(self):
        for lang, units in self.units.items():
            for unit in units:
                for text in [unit.get("value", "")] + list(unit.get("plural", {}).values()):
                    self.assertIsNone(APPLE_SPEC_RE.search(text), f"{lang} {unit['key']}: {text}")

    def test_known_conversions(self):
        plural = {u["key"]: u for u in self.units["de"]}["%lld lines"]
        self.assertEqual(plural["plural"], {"one": "{0} Zeile", "other": "{0} Zeilen"})
        self.assertEqual(l10n.to_net_format("%2$@ and %1$@, 100%% {x}"), "{1} and {0}, 100% {{x}}")

    def test_a_percent_sign_exports_as_apple_prints_it(self):
        def unit(value):
            return {"stringUnit": {"state": "translated", "value": value}}

        catalog = l10n.load_catalog(l10n.CATALOGS["app"])
        catalog["strings"]["%lld left, 100%% done"] = {"localizations": {"en": {"variations": {"plural": {
            "one": unit("One left, 100%% done"), "other": unit("%lld left, 100%% done")}}},
            "it": unit("Tutto fatto al 100%%"), "de": unit("Zu 100%% fertig")}}
        catalog["strings"]["100% sure"] = {"localizations": {"en": unit("100% sure"), "it": unit("Sicuro al 100%")}}
        l10n.dump_catalog(l10n.CATALOGS["app"], catalog)
        exported = {lang: {u["key"]: u for u in l10n.export_units(lang)} for lang in ("en", "it")}
        self.assertEqual(exported["en"]["%lld left, 100%% done"]["plural"],
                         {"one": "One left, 100% done", "other": "{0} left, 100% done"},
                         "Apple formats every plural form, even one without the number")
        self.assertEqual(exported["it"]["%lld left, 100%% done"]["value"], "Tutto fatto al 100%",
                         "a translation without the plural is formatted as its source is")
        self.assertEqual((exported["en"]["100% sure"]["value"], exported["it"]["100% sure"]["value"]),
                         ("100% sure", "Sicuro al 100%"))

    def test_a_substitution_form_converts_its_own_numbers(self):
        def subs(one, other):
            return {"earned": {"argNum": 1, "formatSpecifier": "lld", "variations": {"plural": {
                "one": {"stringUnit": {"value": one}}, "other": {"stringUnit": {"value": other}}}}}}

        earned = subs("%arg di %2$lld ottenuto", "%arg di %2$lld ottenuti, {x} 100%% {y}")
        self.assertEqual(l10n.to_net_format("%#@earned@", earned, "one"), "{0} di {1} ottenuto")
        self.assertEqual(l10n.to_net_format("%#@earned@", earned, "other"), "{0} di {1} ottenuti, {{x}} 100% {{y}}",
                         "braces are doubled before and after each token")
        self.assertEqual(l10n.to_net_format("%#@earned@", earned, "few"), "{0} di {1} ottenuti, {{x}} 100% {{y}}",
                         "a category the language lacks takes its other form")
        self.assertEqual(l10n.to_net_format("%#@earned@, %2$lld", subs("%arg", "%arg"), "one"), "{0}, {1}")
        self.assertEqual(l10n.to_net_format("%1$#@earned@ (%2$lld)", earned, "one"), "{0} di {1} ottenuto ({1})",
                         "Apple's compiled form, with the substitution's position")
        left = {"left": {**subs("%arg more break to go!", "%arg more breaks to go!")["earned"], "argNum": 2}}
        self.assertEqual(l10n.to_net_format("%1$lld done, %#@left@", left, "one"), "{0} done, {1} more break to go!",
                         "%arg is the argument argNum names")
        self.assertEqual(l10n.to_net_format("noch %#@left@, %lld fertig", left, "one"),
                         "noch {1} more break to go!, {0} fertig",
                         "a positionless number takes argument 1, as on Apple: the count goes on past the substitution")
        self.assertEqual(l10n.to_net_format("%@ %2$@ %@"), "{0} {1} {1}", "Apple's count skips %2$@")
        self.assertEqual(l10n.to_net_format("100% on-device, 0% data sent"), "100% on-device, 0% data sent",
                         "Apple prints a string with no arguments as it is, and so do the ports")
        self.assertEqual(l10n.to_net_format("%lld % fatto"), "{0} {1}atto", "as Apple reads it, and check reports it")
        self.assertEqual(l10n.form_number_problem("%arg, 100% done"),
                         "'% d' is a specifier to Apple, beside the string's arguments, and prints one that isn't "
                         "there: write the percent sign as %%")
        self.assertEqual(l10n.to_net_format("%2$@ %@ %lld"), "{1} {0} {1}")
        self.assertEqual(l10n.to_net_format("%lld % % sûr"), "{0}  % sûr", "'% %' prints ' %', as on Apple")
        self.assertEqual(l10n.to_net_format("%lld moves, 10%-20% faster"), "{0} moves, 10-20% faster",
                         "Apple drops a flagged percent sign's first %, and check reports it")
        self.assertEqual(l10n.form_to_net("%arg moves, 10%-20% faster", 0, "x"), "{0} moves, 10-20% faster")
        self.assertEqual(l10n.to_net_format("Today 50%OFF"), "Today 50%OFF", "Swift writes a bare literal as it is")
        self.assertEqual(l10n.to_net_format("%#@earned@", subs("%arg at 100% %", "%arg at 100%%"), "one"),
                         "{0} at 100 %")
        self.assertEqual(l10n.to_net_format("One left, 100%% done", literal=False), "One left, 100% done",
                         "a numberless plural form is formatted all the same")
        self.assertEqual(l10n.to_net_format("Sûr à 100 %%"), "Sûr à 100 %%", "a plain string is exported as Apple shows it")
        self.assertIsNone(l10n.markup_problem("Battery 8%", "Batterie 8 %"), "a landing string's % isn't printf")
        self.assertEqual(l10n.markup_problem("{n} at 8%", "8 % pour"), "placeholders/tags ['{n}'] != []")

        def failure(text, substitutions):
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
                l10n.to_net_format(text, substitutions, "one")
            self.assertEqual(caught.exception.code, 2)
            return err.getvalue()

        self.assertIn("%lld in the substitution form '%arg di %lld' of '%#@earned@' needs its position",
                      failure("%#@earned@", subs("%arg di %lld", "%arg di %2$lld")))
        self.assertIn("the substitution form '%arg di %#@total@' of '%#@earned@' holds another substitution, %#@total@",
                      failure("%#@earned@", subs("%arg di %#@total@", "%arg")))
        unnumbered = {"earned": {key: value for key, value in earned["earned"].items() if key != "argNum"}}
        self.assertIn("the substitution %#@earned@ in '%@: %#@earned@' has no argNum",
                      failure("%@: %#@earned@", unnumbered))

    def test_resw_is_valid_and_names_are_unique(self):
        for path, text in self.resw.items():
            names = [data.get("name") for data in ET.fromstring(text).iter("data")]
            self.assertEqual(len(names), len(set(names)), path)
            self.assertIn("Localizable.xcstrings by laching/tools/l10n.py", text)
        self.assertEqual(l10n.resw_name("A_b é"), "_0041_005Fb_0020_00E9")
        self.assertEqual(l10n.resw_name("💪"), "_D83D_DCAA")
        self.assertNotEqual(l10n.resw_name("To").lower(), l10n.resw_name("to").lower())
        long_key = "x" * 300
        self.assertLessEqual(len(l10n.resw_name(long_key)), l10n.RESW_NAME_MAX)

    def test_android_xml_is_valid_and_names_are_unique(self):
        for path, text in self.android.items():
            names = [el.get("name") for el in ET.fromstring(text).iter("string")]
            self.assertEqual(len(names), len(set(names)), path)
            self.assertTrue(all(re.fullmatch(r"s_[a-z0-9_]+", name) for name in names), path)
            self.assertIn("Localizable.xcstrings by laching/tools/l10n.py export-android", text)

    def test_po_is_valid(self):
        for path, text in self.po.items():
            if path.name == "LINGUAS":
                self.assertEqual(text, "en\nit\nde\n")
                continue
            self.assertIn("Plural-Forms: nplurals=2; plural=(n != 1);", text)
            self.assertEqual(text.count("msgid_plural"), 1, path)

    def test_gettext_plural_forms_match_cldr(self):
        # CLDR sample numbers per category (only the categories the language's catalog uses; the rest fall back
        # to "other").
        samples = {
            "en": {"one": [1], "other": [0, 2, 5, 11, 21]},
            "fr": {"one": [0, 1], "other": [2, 5, 100]},
            "ro": {"one": [1], "few": [0, 2, 19, 101, 119], "other": [20, 21, 100, 120]},
            "pl": {"one": [1], "few": [2, 4, 22, 24, 102], "many": [0, 5, 11, 12, 14, 21, 25, 112]},
            "cs": {"one": [1], "few": [2, 3, 4], "other": [0, 5, 11, 100]},
            "ja": {"other": [0, 1, 2, 11]},
        }
        for lang, categories in samples.items():
            forms, order = l10n.po_plurals(lang)
            expression = re.search(r"plural=(.*);", forms).group(1)
            for category, numbers in categories.items():
                for n in numbers:
                    self.assertEqual(order[eval_plural(expression, n)], category, f"{lang} n={n}")
        self.assertEqual(l10n.po_plurals("zh-Hans"), l10n.po_plurals("ko"))

    def test_check_flags_stale_exports(self):
        l10n.WINDOWS_STRINGS = self.root / "port" / "Fixture.App" / "Strings"
        self.assertEqual(l10n.stale_exports(), [], "no port folder yet: nothing to check")
        (self.root / "port").mkdir()
        self.assertEqual(len(l10n.stale_exports()), 3, "the port exists: its exports must too")
        quiet(l10n.write_export, l10n.windows_files(), False)
        self.assertEqual(l10n.stale_exports(), [])
        l10n.ANDROID_DIR = self.root / "droid"
        l10n.ANDROID_STRINGS = l10n.ANDROID_DIR / "app" / "src" / "main" / "res"
        self.assertEqual(l10n.stale_exports(), [], "no Android folder yet: nothing to check")
        l10n.ANDROID_DIR.mkdir()
        self.assertEqual(len(l10n.stale_exports()), 4, "three strings.xml and locales_config.xml")
        quiet(l10n.write_export, l10n.android_files(), False)
        self.assertEqual(l10n.stale_exports(), [])
        l10n.ANDROID_DIR = None
        self.assertEqual(l10n.stale_exports(), [], "without android.dir the folder is four up from res/")
        stale = l10n.WINDOWS_STRINGS / "de" / "Resources.resw"
        stale.write_text("stale", "utf8")
        self.assertEqual(l10n.stale_exports(), [stale])

    def test_add_writes_manual_entries(self):
        new = self.root / "new.json"
        new.write_text(json.dumps({"Only on Windows": {"comment": "A tray tooltip", "en": "Only on Windows"}}), "utf8")
        self.assertEqual(quiet(l10n.cmd_add, SimpleNamespace(file=str(new)))[0], 0)
        entry = l10n.load_catalog(l10n.CATALOGS["app"])["strings"]["Only on Windows"]
        self.assertEqual(entry["extractionState"], "manual")
        self.assertEqual(l10n.collect()["de"]["app"]["Only on Windows"], ["missing"])


class SinceTests(L10nTestCase):
    """`--since REF` over a landing page kept in its own repository: a submodule, or a nested checkout the app ignores."""

    def write_title(self, title: str, landing: Path | None = None) -> None:
        en = (landing or self.root / "landing") / "src" / "i18n" / "en.json"
        en.write_text(json.dumps({"hero": {"title": title, "body": "Move"}}), "utf8")

    def landing_repository(self, landing: Path) -> None:
        i18n = landing / "src" / "i18n"
        i18n.mkdir(parents=True)
        (i18n / "languages.ts").write_text('export const languages = [{ code: "en" }, { code: "it" }];\n', "utf8")
        (i18n / "it.json").write_text(json.dumps({"hero": {"title": "Siediti meno", "body": "Muoviti"}}), "utf8")
        self.write_title("Sit less", landing)
        git(landing, "init", "-q")
        git(landing, "add", "-A")
        git(landing, "commit", "-q", "-m", "landing")

    def commit_app(self, landing: str) -> None:
        """The fixture app as a repository whose landing/ is a `submodule` or a `nested` checkout it ignores."""
        config = self.root / "companion.json"
        data = json.loads(config.read_text("utf8"))
        data["l10n"]["landing"] = "landing/src/i18n"
        config.write_text(json.dumps(data), "utf8")
        git(self.root, "init", "-q")
        if landing == "submodule":
            source = self.root.parent / "landing-source"
            self.landing_repository(source)
            git(self.root, "submodule", "add", "-q", str(source), "landing")
        else:
            self.landing_repository(self.root / "landing")
            (self.root / ".gitignore").write_text("landing/\n", "utf8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "app")
        l10n.configure(companion.load())

    def test_a_submodule_is_read_at_the_commit_the_app_records(self):
        self.commit_app("submodule")
        self.assertEqual(l10n.git_show("HEAD", l10n.CATALOGS["app"]), l10n.CATALOGS["app"].read_text("utf8"),
                         "the app's own files come from the app's repository")
        self.write_title("Sit less, really")
        git(self.root / "landing", "commit", "-q", "-am", "New title")
        self.assertEqual(l10n.changed_landing_keys("HEAD"), {"hero.title"},
                         "the app's HEAD still records the submodule's first commit")
        self.assertEqual(l10n.collect("HEAD")["it"]["landing"], {"hero.title": ["English changed since HEAD"]})
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / ".git")}):
            self.assertEqual(l10n.changed_landing_keys("HEAD"), {"hero.title"}, "under a git hook too")
        git(self.root, "commit", "-q", "-am", "Landing: new title")
        self.assertEqual(l10n.changed_landing_keys("HEAD"), set())
        self.assertEqual(l10n.changed_landing_keys("HEAD~1"), {"hero.title"})
        # The app records a commit the submodule hasn't fetched: the submodule's own HEAD instead.
        git(self.root, "update-index", "--cacheinfo", f"160000,{'1' * 40},landing")
        git(self.root, "commit", "-q", "-m", "Landing: elsewhere")
        self.assertEqual(l10n.changed_landing_keys("HEAD"), set())
        self.write_title("Sit less, please")
        self.assertEqual(l10n.changed_landing_keys("HEAD"), {"hero.title"})

    def test_a_nested_repository_the_app_doesnt_record_is_read_at_its_own_ref(self):
        self.commit_app("nested")
        self.assertEqual(l10n.changed_landing_keys("HEAD"), set())
        self.write_title("Sit less, really")
        self.assertEqual(l10n.changed_landing_keys("HEAD"), {"hero.title"})
        self.assertEqual(l10n.changed_landing_keys("no-such-ref"), set())


class PhrasesTests(L10nTestCase):
    def config(self) -> companion.Config:
        return companion.load()

    def rewrite_bank(self, change):
        path = self.root / "conformance" / "phrases.json"
        data = json.loads(path.read_text("utf8"))
        change(data["phrases"])
        path.write_text(json.dumps(data, ensure_ascii=False), "utf8")

    def test_the_fixture_is_in_step(self):
        self.assertEqual(quiet(phrases.cmd_check, self.config())[0], 0)

    def test_check_reports_a_weight_under_1_by_id(self):
        def change(bank):
            next(p for p in bank if p["id"] == "hello")["weight"] = 0
            next(p for p in bank if p["id"] == "joke")["weight"] = -1
        self.rewrite_bank(change)
        result, out = quiet(phrases.cmd_check, self.config())
        self.assertEqual(result, 1)
        self.assertIn("weight under 1 in the bank: 'hello' has weight 0", out)
        self.assertIn("weight under 1 in the bank: 'joke' has weight -1", out)

    def test_sync_adds_parts_updates_text_and_retires_lines(self):
        def change(bank):
            bank[:] = [p for p in bank if p["id"] != "hello"]
            joke = next(p for p in bank if p["id"] == "joke")
            joke["parts"][1] = "His feet were sore."
            bank.append({
                "id": "long-joke", "kind": "sequence", "tags": ["general"], "weight": 3, "skins": [], "flavor": "a",
                "keys": ["phrase.long-joke.1", "phrase.long-joke.2", "phrase.long-joke.3"],
                "parts": ["One.", "Two.", "Three."], "languages": ["it"],
            })
        self.rewrite_bank(change)
        self.assertEqual(quiet(phrases.cmd_check, self.config())[0], 1)
        result, out = quiet(phrases.cmd_sync, self.config())
        self.assertEqual(result, 0, out)
        strings = l10n.load_catalog(l10n.CATALOGS["app"])["strings"]
        self.assertNotIn("phrase.hello", strings)
        self.assertEqual(strings["phrase.joke.2"]["localizations"], {"en": {"stringUnit": {"state": "translated", "value": "His feet were sore."}}})
        self.assertIn("it", strings["phrase.joke.1"]["localizations"], "an unchanged part keeps its translations")
        self.assertEqual(strings["phrase.long-joke.3"]["extractionState"], "manual")
        self.assertEqual(
            strings["phrase.long-joke.2"]["comment"],
            "Fixy says this aloud (general). Flavor a: the first flavor. Part 2 of 3 of a joke told in beats. "
            "Exists only in it: don't translate it into other languages.",
        )
        self.assertEqual(
            strings["phrase.joke.1"]["comment"],
            "Fixy says this aloud (buttonPress, general). Flavor b: the second flavor. Part 1 of 2 of a joke told in beats.",
        )
        self.assertEqual(quiet(phrases.cmd_check, self.config())[0], 0)
        l10n.configure(companion.load())
        report = l10n.collect()
        self.assertNotIn("phrase.long-joke.1", report["de"]["app"])
        self.assertIn("phrase.long-joke.1", report["it"]["app"])
        self.assertIn("phrase.joke.2", report["it"]["app"], "a rewritten part needs translating again")

    def edit_catalog(self, change):
        path = l10n.CATALOGS["app"]
        data = l10n.load_catalog(path)
        change(data["strings"])
        l10n.dump_catalog(path, data)

    def sync_comments(self):
        """Brings the fixture's comments in step (they predate the template), so a later sync starts from zero."""
        result, out = quiet(phrases.cmd_sync, self.config())
        self.assertEqual(result, 0, out)
        self.assertIn("comments refreshed 4", out)

    def test_check_names_missing_stale_and_orphaned_keys(self):
        def change(bank):
            bank[:] = [p for p in bank if p["id"] != "hello"]
            next(p for p in bank if p["id"] == "joke")["parts"][1] = "His feet were sore."
            bank.append({"id": "bye", "kind": "line", "key": "phrase.bye", "english": "Bye.", "tags": ["general"],
                         "weight": 3, "skins": [], "flavor": "a"})
        self.rewrite_bank(change)
        result, out = quiet(phrases.cmd_check, self.config())
        self.assertEqual(result, 1)
        self.assertEqual(out.splitlines(), [
            "missing in the catalog: 'phrase.bye' (run phrases.py sync)",
            "source text changed in the bank: 'phrase.joke.2' (run phrases.py sync, then re-export the translations)",
            "in the catalog but not in the bank: 'phrase.hello' (retired? phrases.py sync removes it)",
        ])

    def test_a_missing_bank_asks_for_the_vectors(self):
        (self.root / "conformance" / "phrases.json").unlink()
        expected = (f"{Path('conformance/phrases.json')} is missing: "
                    "regenerate the vectors first (laching/tools/conformance.sh)")
        for command in (phrases.cmd_check, phrases.cmd_sync):
            with self.subTest(command=command.__name__), self.assertRaises(SystemExit) as caught:
                quiet(command, self.config())
            self.assertEqual(caught.exception.code, expected)

    def test_sync_in_step_adds_nothing_and_leaves_the_catalog_alone(self):
        self.sync_comments()
        before = l10n.CATALOGS["app"].read_bytes()
        result, out = quiet(phrases.cmd_sync, self.config())
        self.assertEqual(result, 0, out)
        self.assertEqual(out.splitlines(), ["added 0", "updated 0, removed 0, comments refreshed 0"])
        self.assertEqual(l10n.CATALOGS["app"].read_bytes(), before)

    def test_the_comment_template_fills_every_field(self):
        config = self.config()
        config.data["l10n"]["phraseComment"] = "{character}|{tags}|{flavor}|{flavorNote}|{part}"
        bank = phrases.load_bank(config)
        self.assertEqual(phrases.comment_for(config, bank["phrase.hello"]), "Fixy|general|a|a: the first flavor|")
        self.assertEqual(phrases.comment_for(config, bank["phrase.joke.2"]),
                         "Fixy|buttonPress, general|b|b: the second flavor| Part 2 of 2 of a joke told in beats.")

    def test_without_a_template_the_default_comment_is_used(self):
        config = self.config()
        del config.data["l10n"]["phraseComment"]
        bank = phrases.load_bank(config)
        self.assertEqual(phrases.comment_for(config, bank["phrase.hello"]),
                         "Fixy says this aloud (general). Flavor a: the first flavor.")

    def test_a_flavor_without_a_note_reads_as_its_name(self):
        self.rewrite_bank(lambda bank: next(p for p in bank if p["id"] == "hello").update(flavor="c"))
        config = self.config()
        self.assertEqual(phrases.comment_for(config, phrases.load_bank(config)["phrase.hello"]),
                         "Fixy says this aloud (general). Flavor c.")

    def test_a_changed_template_refreshes_every_comment(self):
        self.sync_comments()
        path = self.root / "companion.json"
        data = json.loads(path.read_text("utf8"))
        data["l10n"]["phraseComment"] = "{character} says it.{part}"
        path.write_text(json.dumps(data, ensure_ascii=False), "utf8")
        result, out = quiet(phrases.cmd_sync, self.config())
        self.assertEqual(result, 0, out)
        self.assertIn("updated 0, removed 0, comments refreshed 4", out)
        strings = l10n.load_catalog(l10n.CATALOGS["app"])["strings"]
        self.assertEqual(strings["phrase.hello"]["comment"], "Fixy says it.")
        self.assertEqual(strings["phrase.joke.1"]["comment"], "Fixy says it. Part 1 of 2 of a joke told in beats.")
        self.assertEqual(
            strings["phrase.ciao"]["comment"],
            "Fixy says it. Exists only in it: don't translate it into other languages.",
        )

    def test_a_changed_line_is_stale_and_loses_its_translations(self):
        self.rewrite_bank(lambda bank: next(p for p in bank if p["id"] == "hello").update(english="Hello, you."))
        result, out = quiet(phrases.cmd_check, self.config())
        self.assertEqual(result, 1)
        self.assertIn("source text changed in the bank: 'phrase.hello'", out)
        result, out = quiet(phrases.cmd_sync, self.config())
        self.assertEqual(result, 0, out)
        self.assertIn("  ~ 'phrase.hello': source text updated, translations dropped (re-export them)", out)
        strings = l10n.load_catalog(l10n.CATALOGS["app"])["strings"]
        self.assertEqual(strings["phrase.hello"]["localizations"],
                         {"en": {"stringUnit": {"state": "translated", "value": "Hello, you."}}})
        self.assertEqual(quiet(phrases.cmd_check, self.config())[0], 0)

    def test_a_key_without_a_source_unit_reads_as_its_own_key(self):
        self.edit_catalog(lambda strings: strings["phrase.hello"]["localizations"].pop("en"))
        self.assertEqual(phrases.catalog_source(self.config())["phrase.hello"], "phrase.hello")
        # So it differs from the bank's text: stale, and sync writes the source text back.
        result, out = quiet(phrases.cmd_check, self.config())
        self.assertEqual(result, 1)
        self.assertIn("source text changed in the bank: 'phrase.hello'", out)
        quiet(phrases.cmd_sync, self.config())
        strings = l10n.load_catalog(l10n.CATALOGS["app"])["strings"]
        self.assertEqual(strings["phrase.hello"]["localizations"]["en"]["stringUnit"]["value"], "Hello there.")

    def test_the_command_line(self):
        # Run from the fixture's root without $COMPANION_ROOT, so the tool finds companion.json from where it runs.
        env = {key: value for key, value in os.environ.items() if key != "COMPANION_ROOT"}

        def run():
            return subprocess.run([sys.executable, str(TOOLS / "phrases.py"), "check"], cwd=self.root, env=env,
                                  capture_output=True, encoding="utf8")
        done = run()
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.rewrite_bank(lambda bank: bank.remove(next(p for p in bank if p["id"] == "hello")))
        done = run()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("in the catalog but not in the bank: 'phrase.hello'", done.stdout)

    def test_the_command_line_refuses_an_unknown_command(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            phrases.main(["publish"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("invalid choice: 'publish'", err.getvalue())


if __name__ == "__main__":
    unittest.main()
