"""Standalone transliteration contracts; stdlib only, no conftest/DB/API.

Run: python -m unittest discover -s tests/unit_services -p test_p10_t23_utility_cleanup.py -v
"""

import inspect
import random
import unittest

from app.utils.translit import translit_slug

# Frozen public alphabet, not implementation internals. The compatibility oracle
# below intentionally retains the old algorithm to detect changed slug identities.
_RUSSIAN = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
_LATIN = (
    "a",
    "b",
    "v",
    "g",
    "d",
    "e",
    "e",
    "zh",
    "z",
    "i",
    "y",
    "k",
    "l",
    "m",
    "n",
    "o",
    "p",
    "r",
    "s",
    "t",
    "u",
    "f",
    "kh",
    "ts",
    "ch",
    "sh",
    "shch",
    "",
    "y",
    "",
    "e",
    "yu",
    "ya",
)
_LEGACY_MAPPING = dict(zip(_RUSSIAN, _LATIN))
assert len(_RUSSIAN) == len(_LATIN) and len(_LEGACY_MAPPING) == len(_RUSSIAN)


def _legacy_slug(text):
    translated = [_LEGACY_MAPPING.get(character, character) for character in text.lower()]
    return "".join(part for part in translated if part.isascii() and part.isalnum())


class SlugCompatibilityTests(unittest.TestCase):
    def test_empty_text(self):
        self.assertEqual(translit_slug(""), "")

    def test_entire_russian_alphabet(self):
        self.assertEqual(translit_slug(_RUSSIAN), "".join(_LATIN))

    def test_uppercase_russian_alphabet(self):
        self.assertEqual(translit_slug(_RUSSIAN.upper()), "".join(_LATIN))

    def test_each_russian_letter(self):
        for letter, expected in zip(_RUSSIAN, _LATIN):
            with self.subTest(letter=letter):
                self.assertEqual(translit_slug(letter), expected)

    def test_multiletter_transliterations(self):
        self.assertEqual(translit_slug("ЖХЦЧШЩЮЯ"), "zhkhtschshshchyuya")

    def test_signs_disappear(self):
        self.assertEqual(translit_slug("ъьЪЬ"), "")

    def test_realistic_mixed_title(self):
        self.assertEqual(translit_slug("AI: Щёлково / Объект №42, v2"), "aishchelkovoobekt42v2")

    def test_ascii_filter(self):
        self.assertEqual(translit_slug("a B_09-!\n\t/\\+[]{}.,:;"), "ab09")

    def test_unsupported_scripts_symbols_and_accents(self):
        self.assertEqual(translit_slug("中文 αβ 🐍 éß —\u200d\u202e"), "")

    def test_unicode_lowercase_expansions(self):
        self.assertEqual(translit_slug("Kİſẞ"), "ki")

    def test_combining_marks(self):
        self.assertEqual(translit_slug("Е\u0308лка café"), "elkacaf")

    def test_public_signature(self):
        parameters = inspect.signature(translit_slug).parameters
        self.assertEqual(list(parameters), ["text"])
        self.assertEqual(parameters["text"].default, inspect.Parameter.empty)
        self.assertEqual(translit_slug(text="Я"), "ya")

    def test_every_unicode_codepoint_matches_legacy(self):
        # Bounded chunks also cover lone surrogates without encoding them or
        # logging them. No network, application configuration or DB is imported.
        for first in range(0, 0x110000, 4096):
            text = "".join(map(chr, range(first, min(first + 4096, 0x110000))))
            with self.subTest(first_codepoint=first):
                self.assertEqual(translit_slug(text), _legacy_slug(text))

    def test_deterministic_mixed_strings_match_legacy(self):
        randomizer = random.Random(23)
        alphabet = _RUSSIAN + _RUSSIAN.upper() + " AZaz09KİΣςÉßё🧪-/ \n\u0307"
        for case in range(200):
            text = "".join(randomizer.choices(alphabet, k=randomizer.randrange(150)))
            with self.subTest(case=case):
                self.assertEqual(translit_slug(text), _legacy_slug(text))


if __name__ == "__main__":
    unittest.main()
