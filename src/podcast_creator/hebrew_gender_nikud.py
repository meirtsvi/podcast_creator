"""Resolve ambiguous Hebrew second-person forms before the script reaches TTS.

Hebrew second-person singular is written identically for a man and a woman in ktiv male and
differs only in the nikud: `שלך` is either `שֶׁלְּךָ` (to a man) or `שֶׁלָּךְ` (to a woman), `אמרת` is
either `אָמַרְתָּ` or `אָמַרְתְּ`. Left bare, the TTS engine has to guess, and it guesses wrong.

Deciding per speaker - "Yuval spoke, so every `you` in his line is feminine" - is what
translations.csv used to do, and it is wrong far more often than it is right: most second
person in these scripts is generic (the listener, "the developer", "the car owner"), not the
co-host. So each occurrence is resolved individually, from the whole conversation.

The model only ever classifies. It returns {id, referent, gender} and never a single character
of text; every replacement string comes from the paradigm tables below and is spliced into the
line at a known offset. That is what makes it safe for this step to also swap wrong-gender word
forms (`תראי` -> `תראה`): the model picks among two or three precomputed strings, so a bad
response can drop a change but can never invent one.
"""

import argparse
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import dotenv
from google import genai
from google.genai import types

from podcast_creator.config import Configuration
from podcast_creator.logger import logger
from podcast_creator.templates import render_template

dotenv.load_dotenv()

# Same tier chapters.py uses: this is a bounded classification over a pre-computed candidate
# list, not generation, and an episode carries a median of 3 candidates.
NIKUD_MODEL = "gemini-3.6-flash"
NIKUD_TEMPERATURE = 0.0          # nikud has exactly one right answer
NIKUD_RETRY_TEMPERATURE = 0.3    # a re-roll at 0 reproduces the same failure
# Measured over the 369 archived episodes that have any candidates: median 15, p90 36.
# At 40 per call nine episodes in ten resolve in a single request, and the whole script
# is re-sent as context on every one, so fewer calls is real savings.
MAX_CANDIDATES_PER_CALL = 40
PROMPT_TEMPLATE = "prompt_for_hebrew_gender_nikud.j2"

PRE_TEXT_FILENAME = "podcast_text_pre_gender_nikud.txt"
REPORT_FILENAME = "gender_nikud_report.json"

MASCULINE = "masculine"
FEMININE = "feminine"

# ---------------------------------------------------------------------------------------
# Nikud primitives
# ---------------------------------------------------------------------------------------

# Only the combining marks. The category filter is what keeps the real punctuation that shares
# this block alive - maqaf U+05BE, paseq U+05C0, sof pasuq U+05C3 are Pd/Po, not Mn.
_NIKUD_SET = frozenset(chr(cp) for cp in range(0x0591, 0x05C8)
                       if unicodedata.category(chr(cp)) == "Mn")
_NIKUD_TABLE = {ord(ch): None for ch in _NIKUD_SET}

HEBREW_LETTERS = "\u05d0-\u05ea"


def strip_nikud(text: str) -> str:
    """Return `text` with every combining nikud mark removed and nothing else changed."""
    return text.translate(_NIKUD_TABLE)


def has_nikud(text: str) -> bool:
    return text != strip_nikud(text)


def _nfc(text: str) -> str:
    """Canonical mark ordering, so a table entry written by hand matches one built by
    concatenation regardless of the order the marks were typed in."""
    return unicodedata.normalize("NFC", text)


# ---------------------------------------------------------------------------------------
# Paradigm tables - the only source of replacement text in this module
# ---------------------------------------------------------------------------------------

# Prepositions and possessives carrying the 2nd-person suffix. Closed set on purpose: a shape
# rule like "ends with final kaf" would swallow דרך, צריך, איך, כך, תהליך, ערך, מסמך.
CLITIC_PARADIGM = {
    "שלך":    {MASCULINE: "שֶׁלְּךָ",     FEMININE: "שֶׁלָּךְ"},
    "לך":     {MASCULINE: "לְךָ",        FEMININE: "לָךְ"},
    "אותך":   {MASCULINE: "אוֹתְךָ",     FEMININE: "אוֹתָךְ"},
    "איתך":   {MASCULINE: "אִיתְּךָ",      FEMININE: "אִיתָּךְ"},
    "אתך":    {MASCULINE: "אִתְּךָ",      FEMININE: "אִתָּךְ"},
    "כמוך":   {MASCULINE: "כָּמוֹךָ",     FEMININE: "כָּמוֹךְ"},
    "ממך":    {MASCULINE: "מִמְּךָ",      FEMININE: "מִמֵּךְ"},
    "עליך":   {MASCULINE: "עָלֶיךָ",     FEMININE: "עָלַיִךְ"},
    "אליך":   {MASCULINE: "אֵלֶיךָ",     FEMININE: "אֵלַיִךְ"},
    "בך":     {MASCULINE: "בְּךָ",       FEMININE: "בָּךְ"},
    "בשבילך": {MASCULINE: "בִּשְׁבִילְךָ", FEMININE: "בִּשְׁבִילֵךְ"},
    "אצלך":   {MASCULINE: "אֶצְלְךָ",     FEMININE: "אֶצְלֵךְ"},
    "מולך":   {MASCULINE: "מוּלְךָ",     FEMININE: "מוּלֵךְ"},
    "עבורך":  {MASCULINE: "עֲבוּרְךָ",   FEMININE: "עֲבוּרֵךְ"},
    "בגללך":  {MASCULINE: "בִּגְלָלְךָ",  FEMININE: "בִּגְלָלֵךְ"},
    "עצמך":   {MASCULINE: "עַצְמְךָ",     FEMININE: "עַצְמֵךְ"},
    "לידך":   {MASCULINE: "לְיָדְךָ",     FEMININE: "לְיָדֵךְ"},
    "אחריך":  {MASCULINE: "אַחֲרֶיךָ",   FEMININE: "אַחֲרַיִךְ"},
    "לפניך":  {MASCULINE: "לְפָנֶיךָ",    FEMININE: "לְפָנַיִךְ"},
    "דעתך":   {MASCULINE: "דַּעְתְּךָ",    FEMININE: "דַּעְתֵּךְ"},
    "בעיניך": {MASCULINE: "בְּעֵינֶיךָ",  FEMININE: "בְּעֵינַיִךְ"},
    "שלומך": {MASCULINE: "שְׁלוֹמְךָ", FEMININE: "שְׁלוֹמֵךְ"},
    "שמך": {MASCULINE: "שִׁמְךָ", FEMININE: "שְׁמֵךְ"},
    "בשמך": {MASCULINE: "בְּשִׁמְךָ", FEMININE: "בִּשְׁמֵךְ"},
    "מקומך": {MASCULINE: "מְקוֹמְךָ", FEMININE: "מְקוֹמֵךְ"},
    "במקומך": {MASCULINE: "בִּמְקוֹמְךָ", FEMININE: "בִּמְקוֹמֵךְ"},
    "זמנך": {MASCULINE: "זְמַנְּךָ", FEMININE: "זְמַנֵּךְ"},
    "לדעתך": {MASCULINE: "לְדַעְתְּךָ", FEMININE: "לְדַעְתֵּךְ"},
    "לעצמך": {MASCULINE: "לְעַצְמְךָ", FEMININE: "לְעַצְמֵךְ"},
    "בעצמך": {MASCULINE: "בְּעַצְמְךָ", FEMININE: "בְּעַצְמֵךְ"},
    "משלך": {MASCULINE: "מִשֶּׁלְּךָ", FEMININE: "מִשֶּׁלָּךְ"},
    "סביבך": {MASCULINE: "סְבִיבְךָ", FEMININE: "סְבִיבֵךְ"},
    "אינך": {MASCULINE: "אֵינְךָ", FEMININE: "אֵינֵךְ"},
}

# `לך` is also the masculine imperative "go". Same letters as the preposition, so it cannot be
# told apart by shape - hence its own referent in the schema.
IMPERATIVE_GO = {MASCULINE: "לֵךְ", FEMININE: "לְכִי"}

# Past tense 2ms/2fs. Only entries whose vocalization is certain; everything else falls back to
# marking the final tav (see _past2_fallback), which carries the gender without risking a wrong
# vowel elsewhere in the word. Every fallback is logged so this table can grow.
PAST2_PARADIGM = {
    "אמרת":    {MASCULINE: "אָמַרְתָּ",     FEMININE: "אָמַרְתְּ"},
    "שמעת":    {MASCULINE: "שָׁמַעְתָּ",     FEMININE: "שָׁמַעְתְּ"},
    "ראית":    {MASCULINE: "רָאִיתָ",      FEMININE: "רָאִית"},
    "ידעת":    {MASCULINE: "יָדַעְתָּ",     FEMININE: "יָדַעְתְּ"},
    "חשבת":    {MASCULINE: "חָשַׁבְתָּ",     FEMININE: "חָשַׁבְתְּ"},
    "הבנת":    {MASCULINE: "הֵבַנְתָּ",     FEMININE: "הֵבַנְתְּ"},
    "עשית":    {MASCULINE: "עָשִׂיתָ",      FEMININE: "עָשִׂית"},
    "היית":    {MASCULINE: "הָיִיתָ",      FEMININE: "הָיִית"},
    "רצית":    {MASCULINE: "רָצִיתָ",      FEMININE: "רָצִית"},
    "צללת":    {MASCULINE: "צָלַלְתָּ",     FEMININE: "צָלַלְתְּ"},
    "דיברת":   {MASCULINE: "דִּיבַּרְתָּ",    FEMININE: "דִּיבַּרְתְּ"},
    "דברת":    {MASCULINE: "דִּבַּרְתָּ",    FEMININE: "דִּבַּרְתְּ"},
    "הזכרת":   {MASCULINE: "הִזְכַּרְתָּ",   FEMININE: "הִזְכַּרְתְּ"},
    "הצגת":    {MASCULINE: "הִצַּגְתָּ",    FEMININE: "הִצַּגְתְּ"},
    "התכוונת": {MASCULINE: "הִתְכַּווַּנְתָּ", FEMININE: "הִתְכַּווַּנְתְּ"},
    "שמת":     {MASCULINE: "שַׂמְתָּ",      FEMININE: "שַׂמְתְּ"},
    "קראת":    {MASCULINE: "קָרָאתָ",      FEMININE: "קָרָאת"},
    "כתבת":    {MASCULINE: "כָּתַבְתָּ",    FEMININE: "כָּתַבְתְּ"},
    "בדקת":    {MASCULINE: "בָּדַקְתָּ",    FEMININE: "בָּדַקְתְּ"},
    "שאלת":    {MASCULINE: "שָׁאַלְתָּ",    FEMININE: "שָׁאַלְתְּ"},
    "צדקת":    {MASCULINE: "צָדַקְתָּ",    FEMININE: "צָדַקְתְּ"},
    "למדת":    {MASCULINE: "לָמַדְתָּ",    FEMININE: "לָמַדְתְּ"},
    "עברת":    {MASCULINE: "עָבַרְתָּ",    FEMININE: "עָבַרְתְּ"},
    "הלכת":    {MASCULINE: "הָלַכְתָּ",    FEMININE: "הָלַכְתְּ"},
    "ישבת":    {MASCULINE: "יָשַׁבְתָּ",    FEMININE: "יָשַׁבְתְּ"},
    "חזרת":    {MASCULINE: "חָזַרְתָּ",    FEMININE: "חָזַרְתְּ"},
    "זכרת":    {MASCULINE: "זָכַרְתָּ",    FEMININE: "זָכַרְתְּ"},
    "שכחת":    {MASCULINE: "שָׁכַחְתָּ",    FEMININE: "שָׁכַחְתְּ"},
    "בחרת":    {MASCULINE: "בָּחַרְתָּ",    FEMININE: "בָּחַרְתְּ"},
    "נתת":     {MASCULINE: "נָתַתָּ",      FEMININE: "נָתַתְּ"},
    "טעית":    {MASCULINE: "טָעִיתָ",      FEMININE: "טָעִית"},
    "בנית":    {MASCULINE: "בָּנִיתָ",     FEMININE: "בָּנִית"},
    "גילית":   {MASCULINE: "גִּילִּיתָ",    FEMININE: "גִּילִּית"},
    "ניסית":   {MASCULINE: "נִיסִּיתָ",    FEMININE: "נִיסִּית"},
    "ענית":    {MASCULINE: "עָנִיתָ",      FEMININE: "עָנִית"},
    "קנית":    {MASCULINE: "קָנִיתָ",      FEMININE: "קָנִית"},
    "הצלחת": {MASCULINE: "הִצְלַחְתָּ", FEMININE: "הִצְלַחְתְּ"},
    "קיבלת": {MASCULINE: "קִיבַּלְתָּ", FEMININE: "קִיבַּלְתְּ"},
    "הבטחת": {MASCULINE: "הִבְטַחְתָּ", FEMININE: "הִבְטַחְתְּ"},
    "באת": {MASCULINE: "בָּאתָ", FEMININE: "בָּאת"},
    "ניחשת": {MASCULINE: "נִיחַשְׁתָּ", FEMININE: "נִיחַשְׁתְּ"},
    "התחלת": {MASCULINE: "הִתְחַלְתָּ", FEMININE: "הִתְחַלְתְּ"},
    "נגעת": {MASCULINE: "נָגַעְתָּ", FEMININE: "נָגַעְתְּ"},
    "מצאת": {MASCULINE: "מָצָאתָ", FEMININE: "מָצָאת"},
    "הרגשת": {MASCULINE: "הִרְגַּשְׁתָּ", FEMININE: "הִרְגַּשְׁתְּ"},
    "השתמשת": {MASCULINE: "הִשְׁתַּמַּשְׁתָּ", FEMININE: "הִשְׁתַּמַּשְׁתְּ"},
    "סיפרת": {MASCULINE: "סִיפַּרְתָּ", FEMININE: "סִיפַּרְתְּ"},
    "תיארת": {MASCULINE: "תֵּיאַרְתָּ", FEMININE: "תֵּיאַרְתְּ"},
    "עבדת": {MASCULINE: "עָבַדְתָּ", FEMININE: "עָבַדְתְּ"},
    "גלגלת": {MASCULINE: "גִּלְגַּלְתָּ", FEMININE: "גִּלְגַּלְתְּ"},
    "פתחת": {MASCULINE: "פָּתַחְתָּ", FEMININE: "פָּתַחְתְּ"},
    "המשכת": {MASCULINE: "הִמְשַׁכְתָּ", FEMININE: "הִמְשַׁכְתְּ"},
    "הסברת": {MASCULINE: "הִסְבַּרְתָּ", FEMININE: "הִסְבַּרְתְּ"},
    "שילמת": {MASCULINE: "שִׁילַּמְתָּ", FEMININE: "שִׁילַּמְתְּ"},
    "מכרת": {MASCULINE: "מָכַרְתָּ", FEMININE: "מָכַרְתְּ"},
    "קפצת": {MASCULINE: "קָפַצְתָּ", FEMININE: "קָפַצְתְּ"},
    "סקרת": {MASCULINE: "סָקַרְתָּ", FEMININE: "סָקַרְתְּ"},
    "בחנת": {MASCULINE: "בָּחַנְתָּ", FEMININE: "בָּחַנְתְּ"},
    "ניהלת": {MASCULINE: "נִיהַלְתָּ", FEMININE: "נִיהַלְתְּ"},
    "הפכת": {MASCULINE: "הָפַכְתָּ", FEMININE: "הָפַכְתְּ"},
}

# Past-tense verbs that reach the model but have no table entry; the final tav carries the
# gender on its own and the rest of the word is left bare rather than guessed at.
_QAMATS = "\u05b8"
_SHVA = "\u05b0"

# Stems that are ambiguous past-tense forms but whose full vocalization is not in the table.
# They are candidates anyway - the fallback handles the spelling.
# Deliberately absent, though they are real past-tense forms: לקחת is an infinitive 54 times
# in the archive, נכנסת a present-tense 3fs 24 times, and העברת / חווית / הוספת / הזמנת /
# הכרת / החלטת / עצרת are noun constructs ("data transfer", "user experience", "government
# decision"). Recall costs almost nothing here; a wrong swap on a noun would be audible.
PAST2_EXTRA_STEMS = "ציינת סיימת חיפשת שיתפת".split()

# Forms whose letters already encode the gender, so they carry no ambiguity - but the generator
# sometimes picks the wrong one. Pairs are (masculine, feminine) and need no nikud: once the
# letters are right the reading is unambiguous.
FUTURE_PAIRS = [
    ("תגיד", "תגידי"), ("תספר", "תספרי"), ("תסביר", "תסבירי"), ("תשמע", "תשמעי"),
    ("תבין", "תביני"), ("תזכור", "תזכרי"), ("תחשוב", "תחשבי"), ("תדע", "תדעי"),
    ("תוכל", "תוכלי"), ("תרצה", "תרצי"), ("תבוא", "תבואי"), ("תלך", "תלכי"),
    ("תיתן", "תיתני"), ("תעשה", "תעשי"), ("תתאר", "תתארי"), ("תציין", "תצייני"),
    ("תפרט", "תפרטי"), ("תמשיך", "תמשיכי"), ("תתחיל", "תתחילי"), ("תסיים", "תסיימי"),
    ("תבדוק", "תבדקי"), ("תקרא", "תקראי"), ("תכתוב", "תכתבי"), ("תשאל", "תשאלי"),
    ("תענה", "תעני"), ("תצלול", "תצללי"), ("תפרק", "תפרקי"), ("תחכה", "תחכי"),
    ("תוסיף", "תוסיפי"), ("תגלה", "תגלי"), ("תנסה", "תנסי"), ("תסכים", "תסכימי"),
    ("תכיר", "תכירי"), ("תשים", "תשימי"), ("תראה", "תראי"), ("תהיה", "תהיי"),
    ("תיקח", "תיקחי"), ("תשתף", "תשתפי"),
]
# Imperatives cannot be confused with a 3fs future, so both directions are safe here. Forms
# that are also common nouns are deliberately absent: ספר is a book far more often than it is
# "tell", and the same goes for שמע and הסבר.
IMPERATIVE_PAIRS = [
    ("תן", "תני"), ("בוא", "בואי"), ("קח", "קחי"), ("שים", "שימי"), ("חכה", "חכי"),
]
# `את <2fs participle>` / `אתה <2ms participle>`. Only the pronoun is vocalized; the participle
# already distinguishes the gender by its letters.
PARTICIPLE_PAIRS = [
    ("יודע", "יודעת"), ("רוצה", "רוצה"), ("יכול", "יכולה"), ("חושב", "חושבת"),
    ("מבין", "מבינה"), ("זוכר", "זוכרת"), ("צודק", "צודקת"), ("מכיר", "מכירה"),
    ("מרגיש", "מרגישה"), ("מסכים", "מסכימה"), ("בטוח", "בטוחה"), ("נשמע", "נשמעת"),
    ("הולך", "הולכת"), ("אומר", "אומרת"), ("מתכוון", "מתכוונת"), ("שואל", "שואלת"),
    ("מנסה", "מנסה"), ("מדבר", "מדברת"), ("רואה", "רואה"), ("מתאר", "מתארת"),
    ("מדמיין", "מדמיינת"), ("קורא", "קוראת"), ("עובד", "עובדת"),
]

AT_PRONOUN_MASC = "אתה"   # unambiguous as written; nikud here would be noise
AT_PRONOUN_FEM = "אַתְּ"

# A bare `את` is overwhelmingly the accusative particle - 382 occurrences across five sampled
# episodes, of which five were the pronoun. It only becomes a candidate when a 2fs participle
# follows, or when the woman's name precedes it (vocative). Precision matters far more than
# recall here: a wrong `אַתְּ` on the particle is a glaring audio bug, a missed one is inaudible.
AT_FOLLOWERS = frozenset(fem for _, fem in PARTICIPLE_PAIRS) | frozenset(
    "יודעת רואה צודקת חושבת יכולה מבינה מרגישה נשמעת הולכת מסכימה אומרת זוכרת מכירה "
    "בטוחה מתכוונת שואלת מנסה מדברת מתארת קוראת עובדת פשוט באמת ממש".split())

# Prefixes that attach to a clitic or a past-tense verb, with their own vocalization.
PREFIX_NIKUD = {"": "", "ו": "וְ", "ש": "שֶׁ", "וש": "וְשֶׁ", "כש": "כְּשֶׁ"}

KIND_CLITIC = "clitic"
KIND_PAST2 = "past2"
KIND_AT = "at"
KIND_FORM = "form"

REFERENTS = ("cohost", "generic", "third_party", "imperative_go", "not_second_person")


def _past2_fallback(token: str, gender: str) -> str:
    """Mark only the final tav, which is where the whole distinction lives."""
    if not token.endswith("ת"):
        return None
    return token + (_QAMATS if gender == MASCULINE else _SHVA)


def _build_surface_table():
    """surface form (no nikud) -> {"kind": ..., "forms": {gender: replacement}}.

    Prefixed variants are generated first so that a base entry always wins a collision - `שלך`
    is "yours" far more often than it is `ש` + `לך`.
    """
    table = {}

    def add(surface, kind, forms, source="table"):
        surface = _nfc(surface)
        checked = {}
        for gender, form in forms.items():
            if form is None:
                continue
            form = _nfc(form)
            # KIND_FORM swaps letters on purpose; everything else must only gain nikud. A
            # vocalization that quietly drops a ktiv-male letter - אִתְּךָ for איתך - is a typo,
            # and catching it here beats discovering it one rejected candidate at a time.
            if kind != KIND_FORM and strip_nikud(form) != surface:
                logger.warning(f"Dropping {gender} form {form!r}: its letters do not match "
                               f"{surface!r}")
                continue
            checked[gender] = form
        table[surface] = {"kind": kind, "forms": checked, "source": source}

    for prefix, prefix_nikud in PREFIX_NIKUD.items():
        if not prefix:
            continue
        for base, forms in CLITIC_PARADIGM.items():
            add(prefix + base, KIND_CLITIC, {g: prefix_nikud + v for g, v in forms.items()})
        for base, forms in PAST2_PARADIGM.items():
            add(prefix + base, KIND_PAST2, {g: prefix_nikud + v for g, v in forms.items()})
        for base in PAST2_EXTRA_STEMS:
            add(prefix + base, KIND_PAST2,
                {g: prefix_nikud + _past2_fallback(base, g) for g in (MASCULINE, FEMININE)},
                source="fallback")

    for base, forms in CLITIC_PARADIGM.items():
        add(base, KIND_CLITIC, forms)
    for base, forms in PAST2_PARADIGM.items():
        add(base, KIND_PAST2, forms)
    for base in PAST2_EXTRA_STEMS:
        add(base, KIND_PAST2, {g: _past2_fallback(base, g) for g in (MASCULINE, FEMININE)},
            source="fallback")

    for masc, fem in FUTURE_PAIRS:
        # Only the feminine surface becomes a candidate. `תוכלי` can only be 2fs, but `תוכל` is
        # just as likely to belong to a 3fs subject - "החברה תוכל", "המערכת תהיה" - and turning
        # one of those into `תוכלי` would be a glaring bug. This keeps the direction that
        # actually misfires today, a feminine form left standing on a generic "you".
        add(fem, KIND_FORM, {MASCULINE: masc, FEMININE: fem})
    for masc, fem in IMPERATIVE_PAIRS:
        for surface in (masc, fem):
            add(surface, KIND_FORM, {MASCULINE: masc, FEMININE: fem})
    for masc, fem in PARTICIPLE_PAIRS:
        add("אתה " + masc, KIND_FORM,
            {MASCULINE: AT_PRONOUN_MASC + " " + masc, FEMININE: AT_PRONOUN_FEM + " " + fem})
        add("את " + fem, KIND_FORM,
            {MASCULINE: AT_PRONOUN_MASC + " " + masc, FEMININE: AT_PRONOUN_FEM + " " + fem})

    add("את", KIND_AT, {FEMININE: AT_PRONOUN_FEM})
    return table


SURFACE_TABLE = _build_surface_table()

# Longest first so `את יודעת` wins over `את`, and `בשבילך` over nothing shorter it contains.
_SURFACE_RE = re.compile(
    "(?<![%s])(?:%s)(?![%s])" % (
        HEBREW_LETTERS,
        "|".join(re.escape(s) for s in sorted(SURFACE_TABLE, key=len, reverse=True)),
        HEBREW_LETTERS),
)
_WORD_RE = re.compile("[%s]+" % HEBREW_LETTERS)


# ---------------------------------------------------------------------------------------
# Candidate detection
# ---------------------------------------------------------------------------------------

@dataclass
class Candidate:
    id: int
    line_index: int
    speaker: str
    kind: str
    token: str          # surface form, guaranteed free of nikud
    char_start: int     # offset into the full line, including the speaker prefix
    char_end: int
    context: str

    # filled in during resolution
    referent: str = ""
    gender: str = ""
    replacement: str = ""
    source: str = ""
    applied: bool = False
    reason: str = ""


def _spoken_span(line: str):
    """Return (prefix, start offset of the spoken part). The speaker prefix is never scanned,
    so it can never be modified - split_text_into_chunks matches on it verbatim."""
    prefix, sep, _ = line.partition(":")
    if not sep:
        return "", 0
    return prefix + sep, len(prefix) + 1


def _strip_with_map(text: str):
    """Return (text without nikud, index of each surviving char in the original)."""
    kept, index = [], []
    for i, ch in enumerate(text):
        if ch not in _NIKUD_SET:
            kept.append(ch)
            index.append(i)
    return "".join(kept), index


def find_candidates(podcast_text: str, configuration: Configuration):
    """Every ambiguous or possibly-wrong-gender second-person span, in reading order."""
    candidates, prevocalized = [], []
    woman = strip_nikud(configuration.woman_speaker_name)
    next_id = 0

    for line_index, line in enumerate(podcast_text.split("\n")):
        prefix, offset = _spoken_span(line)
        spoken = line[offset:]
        stripped, index_map = _strip_with_map(spoken)

        for match in _SURFACE_RE.finditer(stripped):
            surface = match.group()
            start = offset + index_map[match.start()]
            end = offset + index_map[match.end() - 1] + 1
            original = line[start:end]

            # Already vocalized, by translations.csv or by a previous run of this step. Leaving
            # it alone is what makes the step idempotent.
            if has_nikud(original):
                prevocalized.append({"line_index": line_index, "token": original,
                                     "base": surface, "speaker": prefix.rstrip(":")})
                continue

            entry = SURFACE_TABLE[_nfc(surface)]
            if entry["kind"] == KIND_AT and not _at_looks_pronominal(stripped, match, woman):
                continue

            candidates.append(Candidate(
                id=next_id, line_index=line_index, speaker=prefix.rstrip(":"),
                kind=entry["kind"], token=surface, char_start=start, char_end=end,
                context=_context(spoken, index_map, match)))
            next_id += 1

    return candidates, prevocalized


def _at_looks_pronominal(stripped: str, match, woman_name: str) -> bool:
    """A positive cue is required, not merely the absence of a negative one."""
    after = _WORD_RE.search(stripped, match.end())
    if after and after.group() in AT_FOLLOWERS and after.start() - match.end() <= 2:
        return True
    before = None
    for word in _WORD_RE.finditer(stripped, 0, match.start()):
        before = word.group()
    return before == woman_name


def _context(spoken: str, index_map, match, window: int = 70) -> str:
    """The candidate inside its sentence, marked with brackets the model can see."""
    start, end = index_map[match.start()], index_map[match.end() - 1] + 1
    left = spoken[max(0, start - window):start].lstrip()
    right = spoken[end:end + window].rstrip()
    return f"{left}\u27e6{spoken[start:end]}\u27e7{right}"


# ---------------------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------------------

GENDER_NIKUD_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "decisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "INTEGER"},
                    "referent": {"type": "STRING", "enum": list(REFERENTS)},
                    "gender": {"type": "STRING", "enum": [MASCULINE, FEMININE]},
                },
                "required": ["id", "referent", "gender"],
            },
        }
    },
    "required": ["decisions"],
}


def _call_model(prompt: str, temperature: float) -> list:
    """Return the model's "decisions" array, or [] on any failure.

    Every error path degrades to "leave these candidates alone" rather than raising: a missing
    nikud mark is a small audio flaw, a crashed episode is not.
    """
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model=NIKUD_MODEL,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GENDER_NIKUD_SCHEMA,
                temperature=temperature,
            ),
        )
        return json.loads(response.text)["decisions"]
    except Exception as e:
        logger.error(f"Gender-nikud resolution call failed, leaving this batch bare: {e}")
        return []


def _resolve_batch(script_lines: list, batch: list, configuration: Configuration) -> dict:
    """Ask the model to classify one batch of candidates. Returns {id: decision}."""
    prompt = render_template(
        PROMPT_TEMPLATE,
        man_speaker=configuration.man_speaker_name,
        woman_speaker=configuration.woman_speaker_name,
        script_lines=[{"index": i, "text": line}
                      for i, line in enumerate(script_lines) if line.strip()],
        candidates=[{"id": c.id, "line_index": c.line_index, "speaker": c.speaker,
                     "token": c.token, "kind": c.kind, "context": c.context} for c in batch],
    )
    decisions = _call_model(prompt, NIKUD_TEMPERATURE)
    if not decisions:
        # Only a wholly unusable response is worth a retry, and only at a different temperature.
        decisions = _call_model(prompt, NIKUD_RETRY_TEMPERATURE)

    by_id = {}
    wanted = {c.id for c in batch}
    for decision in decisions:
        try:
            decision_id = int(decision["id"])
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Dropping malformed decision: {decision}")
            continue
        if decision_id not in wanted or decision_id in by_id:
            logger.warning(f"Dropping decision with unknown or duplicate id: {decision}")
            continue
        by_id[decision_id] = decision
    return by_id


def _cohost_gender(speaker: str, configuration: Configuration):
    """Who is being spoken to. Derived from the prefix, never taken from the model."""
    speaker = strip_nikud(speaker).strip()
    if speaker == strip_nikud(configuration.man_speaker_name):
        return FEMININE
    if speaker == strip_nikud(configuration.woman_speaker_name):
        return MASCULINE
    return None


def _decide(candidate: Candidate, decision: dict, configuration: Configuration) -> bool:
    """Fill in referent/gender/replacement. Returns whether the candidate should be applied."""
    referent = decision.get("referent")
    gender = decision.get("gender")
    if referent not in REFERENTS:
        candidate.reason = f"unknown referent {referent!r}"
        return False
    candidate.referent = referent

    if referent == "not_second_person":
        candidate.reason = "not a second-person form"
        return False
    if referent == "generic":
        # The prompt says so, but the whole point of this step is not to trust a blanket rule -
        # including the model's memory of one.
        gender = MASCULINE
    elif referent == "cohost":
        gender = _cohost_gender(candidate.speaker, configuration) or gender
    if gender not in (MASCULINE, FEMININE):
        candidate.reason = f"unknown gender {gender!r}"
        return False
    candidate.gender = gender

    if referent == "imperative_go":
        if candidate.token != "לך":
            candidate.reason = "imperative_go on a token that is not לך"
            return False
        candidate.replacement, candidate.source = IMPERATIVE_GO[gender], "imperative_go"
    else:
        entry = SURFACE_TABLE[_nfc(candidate.token)]
        if gender not in entry["forms"]:
            candidate.reason = f"no {gender} form for {candidate.token!r}"
            return False
        candidate.replacement = entry["forms"][gender]
        candidate.source = entry["source"]

    if candidate.replacement == candidate.token:
        candidate.reason = "already correct"
        return False

    # The invariant. For everything but a deliberate form swap the letters must be untouched.
    if candidate.kind != KIND_FORM and strip_nikud(candidate.replacement) != candidate.token:
        candidate.reason = (f"replacement {candidate.replacement!r} changes the letters of "
                            f"{candidate.token!r}")
        return False
    return True


# ---------------------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------------------

def _apply_to_lines(lines: list, candidates: list) -> int:
    """Splice accepted replacements in. A line that fails its checks reverts whole."""
    applied_count = 0
    by_line = {}
    for candidate in candidates:
        if candidate.applied:
            by_line.setdefault(candidate.line_index, []).append(candidate)

    for line_index, line_candidates in by_line.items():
        original = lines[line_index]
        prefix, _ = _spoken_span(original)
        new_line = original
        ok = True
        # Descending, so the offsets of the edits still to come stay valid.
        for candidate in sorted(line_candidates, key=lambda c: c.char_start, reverse=True):
            if new_line[candidate.char_start:candidate.char_end] != candidate.token:
                logger.error(f"Offset drift on line {line_index} for {candidate.token!r}; "
                             f"reverting the line")
                ok = False
                break
            new_line = (new_line[:candidate.char_start] + candidate.replacement
                        + new_line[candidate.char_end:])

        if ok and not new_line.startswith(prefix):
            logger.error(f"Line {line_index} lost its speaker prefix; reverting")
            ok = False

        if not ok:
            for candidate in line_candidates:
                candidate.applied = False
                candidate.reason = "line reverted"
            continue

        lines[line_index] = new_line
        applied_count += len(line_candidates)
    return applied_count


# ---------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------

def apply_gender_nikud(podcast_text: str, configuration: Configuration,
                       force: bool = False) -> str:
    """Write ambiguous Hebrew second-person forms with the gender they actually refer to.

    Returns `podcast_text` unchanged for a non-Hebrew episode, a single-host or same-gender
    episode, when the feature flag is off, and on any failure at all. `force=True` bypasses the
    flag and is used by the offline evaluation harness.
    """
    mode = "on" if force else os.getenv("HEBREW_GENDER_NIKUD", "on").strip().lower()
    if mode not in ("on", "report"):
        return podcast_text
    if configuration.output_language != "hebrew":
        return podcast_text
    if {h.lower() for h in (configuration.hosts or [])} != {"male", "female"}:
        logger.info("Skipping gender nikud: needs exactly one male and one female host")
        return podcast_text
    if not (configuration.man_speaker_name and configuration.woman_speaker_name):
        return podcast_text

    try:
        return _run(podcast_text, configuration, mode)
    except Exception as e:
        logger.error(f"Gender-nikud step failed, keeping the script as it was: {e}")
        return podcast_text


def _run(podcast_text: str, configuration: Configuration, mode: str) -> str:
    lines = podcast_text.split("\n")
    candidates, prevocalized = find_candidates(podcast_text, configuration)
    logger.info(f"Gender nikud: {len(candidates)} ambiguous second-person span(s) across "
                f"{len(lines)} lines ({len(prevocalized)} already vocalized)")

    if candidates:
        for start in range(0, len(candidates), MAX_CANDIDATES_PER_CALL):
            batch = candidates[start:start + MAX_CANDIDATES_PER_CALL]
            decisions = _resolve_batch(lines, batch, configuration)
            for candidate in batch:
                decision = decisions.get(candidate.id)
                if decision is None:
                    candidate.reason = "no decision returned"
                    continue
                candidate.applied = _decide(candidate, decision, configuration)

    applied = _apply_to_lines(lines, candidates)
    new_text = "\n".join(lines)

    if len(new_text.split("\n")) != len(podcast_text.split("\n")):
        logger.error("Gender nikud changed the line count; abandoning the step")
        return podcast_text

    _write_artifacts(podcast_text, candidates, prevocalized, configuration, mode, applied)

    if mode == "report":
        logger.info(f"Gender nikud in report mode: {applied} change(s) recorded, none applied")
        return podcast_text

    logger.info(f"Gender nikud applied {applied} change(s), "
                f"{len(new_text) - len(podcast_text)} character(s) added")
    return new_text


def _write_artifacts(podcast_text, candidates, prevocalized, configuration, mode, applied):
    folder = getattr(configuration, "episode_folder", None)
    if folder is None:
        return
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / PRE_TEXT_FILENAME).write_text(podcast_text, encoding="utf-8")

        referents = {}
        for candidate in candidates:
            if candidate.referent:
                referents[candidate.referent] = referents.get(candidate.referent, 0) + 1
        report = {
            "mode": mode,
            "model": NIKUD_MODEL,
            "n_candidates": len(candidates),
            "n_applied": applied,
            "n_skipped_prevocalized": len(prevocalized),
            "referent_counts": referents,
            "changes": [_change_row(c) for c in candidates],
            "skipped_prevocalized": prevocalized,
        }
        (folder / REPORT_FILENAME).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Could not write the gender-nikud artifacts: {e}")


def _change_row(candidate: Candidate) -> dict:
    return {
        "line_index": candidate.line_index,
        "speaker": candidate.speaker,
        "kind": candidate.kind,
        "token": candidate.token,
        "replacement": candidate.replacement,
        "referent": candidate.referent,
        "gender": candidate.gender,
        "source": candidate.source,
        "applied": candidate.applied,
        "reason": candidate.reason,
        "context": candidate.context,
    }


# ---------------------------------------------------------------------------------------
# Offline evaluation harness
# ---------------------------------------------------------------------------------------

RLM = "\u200f"


def _parse_episodes(spec: str):
    episodes = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            first, last = part.split("-", 1)
            episodes.extend(range(int(first), int(last) + 1))
        elif part:
            episodes.append(int(part))
    return episodes


def main():
    from podcast_creator.gen_podcast_text import apply_translations, cleanup_text

    parser = argparse.ArgumentParser(
        description="Evaluate Hebrew gender-nikud resolution over archived episodes.")
    parser.add_argument("--root", default=r"C:\Users\meir\Dropbox\tech_podcast_hebrew")
    parser.add_argument("--episodes", default="458-477", help="e.g. 458-477 or 465,470,477")
    parser.add_argument("--out", default=r"C:\temp\gender_nikud_eval")
    parser.add_argument("--dry-run", action="store_true",
                        help="candidate detection only - no model calls, no cost")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    configuration = Configuration("hebrew")
    configuration.episode_folder = out / "_artifacts"

    totals, frequency = {}, {}
    rows = ["\t".join(["episode", "line", "speaker", "kind", "token", "replacement",
                       "referent", "gender", "source", "applied", "reason"])]

    for episode in _parse_episodes(args.episodes):
        source = Path(args.root) / f"Episode_{episode}" / "podcast_text_original.txt"
        if not source.exists():
            continue
        text = cleanup_text(source.read_text(encoding="utf-8"), configuration)
        text = apply_translations(text, configuration)

        if args.dry_run:
            candidates, prevocalized = find_candidates(text, configuration)
            for candidate in candidates:
                frequency[candidate.token] = frequency.get(candidate.token, 0) + 1
            totals[episode] = (len(candidates), len(prevocalized), 0)
            rows.extend("\t".join([str(episode), str(c.line_index), c.speaker, c.kind,
                                   c.token, "", "", "", "", "", ""]) for c in candidates)
            print(f"Episode_{episode}: {len(candidates)} candidate(s), "
                  f"{len(prevocalized)} already vocalized")
            continue

        before = text.split("\n")
        after = apply_gender_nikud(text, configuration, force=True).split("\n")
        report = json.loads((configuration.episode_folder / REPORT_FILENAME)
                            .read_text(encoding="utf-8"))

        assert len(before) == len(after), f"Episode_{episode} changed its line count"
        changed = [i for i in range(len(before)) if before[i] != after[i]]
        diff = []
        for i in changed:
            diff.append(f"{RLM}line {i}")
            diff.append(f"{RLM}- {before[i]}")
            diff.append(f"{RLM}+ {after[i]}")
            diff.append("")
        (out / f"Episode_{episode}.diff.txt").write_text("\n".join(diff), encoding="utf-8")

        for change in report["changes"]:
            frequency[change["token"]] = frequency.get(change["token"], 0) + 1
            rows.append("\t".join([str(episode), str(change["line_index"]), change["speaker"],
                                   change["kind"], change["token"], change["replacement"],
                                   change["referent"], change["gender"], change["source"],
                                   str(change["applied"]), change["reason"]]))
        totals[episode] = (report["n_candidates"], report["n_skipped_prevocalized"],
                           report["n_applied"])
        print(f"Episode_{episode}: {report['n_candidates']} candidate(s), "
              f"{report['n_applied']} applied, {len(changed)} line(s) changed")

    (out / "summary.tsv").write_text("\n".join(rows), encoding="utf-8")
    print(f"\n{len(totals)} episode(s): "
          f"{sum(t[0] for t in totals.values())} candidates, "
          f"{sum(t[2] for t in totals.values())} applied, "
          f"{sum(t[1] for t in totals.values())} already vocalized")
    print("\nMost frequent candidates:")
    for token, count in sorted(frequency.items(), key=lambda kv: -kv[1])[:40]:
        print(f"  {count:5d}  {token}")
    print(f"\nWrote {out / 'summary.tsv'}")


if __name__ == "__main__":
    main()
