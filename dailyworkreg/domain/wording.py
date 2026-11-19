"""The words a register is written in.

Two decisions belong to the office rather than to the code: which language a date's items
are written in, and what a calendar reason is called beside the date. They live here so the
generation request, the stored record, the document projection and the HTTP surface all
read one definition, and so a value can never be spelled one way when it is stored and
another when it is compared.

Nothing in this module decides anything about work. It names the choices.
"""

from __future__ import annotations

#: Which language a date's items are written in.
LANGUAGE_ENGLISH = "en"
LANGUAGE_KANNADA = "kn"
LANGUAGE_BOTH = "both"

#: Every language a register may be written in.
ENTRY_LANGUAGES = (LANGUAGE_ENGLISH, LANGUAGE_KANNADA, LANGUAGE_BOTH)

#: The platform's own default. English, because it is the language the register is audited
#: in and the one the office asked for; Kannada and bilingual remain one click away, and
#: whichever is chosen is recorded with the generation that produced it. A closed month
#: filed before this default existed keeps the wording it was issued in.
DEFAULT_ENTRY_LANGUAGE = LANGUAGE_ENGLISH

#: How each language is named where an operator chooses it.
LANGUAGE_LABELS = {
    LANGUAGE_ENGLISH: "English",
    LANGUAGE_KANNADA: "Kannada",
    LANGUAGE_BOTH: "Both",
}

#: How each language is described in the issued record.
LANGUAGE_DESCRIPTIONS = {
    LANGUAGE_ENGLISH: "English",
    LANGUAGE_KANNADA: "Kannada",
    LANGUAGE_BOTH: "English and Kannada",
}

#: What a non-working date's stored classification means to the office. The stored reason
#: says which date it is ("Sunday", "Second Saturday"), and this says what that date is
#: ("Weekly off", "Holiday"), because a bare "Sunday" beside a date reads as the date
#: itself rather than as the reason it carries no work. A kind absent from this map carries
#: no parenthetical rather than a guessed one.
#:
#: The Second and Fourth Saturdays are recorded here as **holidays**, which is the office's
#: own usage: the section is closed by rule on those days, and the register says so in the
#: word the office uses for a day the section does not work. The Sundays and any other
#: recurring off day remain weekly offs.
REASON_KIND_LABELS = {
    "sunday": "Weekly off",
    "second_saturday": "Holiday",
    "fourth_saturday": "Holiday",
    "other_weekly_off": "Weekly off",
    "general_holiday": "Holiday",
    "leave": "Leave",
    "other_non_working": "Non-working day",
}

#: The non-working kinds the office staffs for other work, so the register records what was
#: allocated on a day the section was closed. Leave and a non-working day that is neither a
#: holiday nor a weekly off are deliberately absent: those are an individual's absence and an
#: unexplained closure, not a closed section that still carried maintenance work.
STAFFED_NON_WORKING_KINDS = (
    "sunday",
    "second_saturday",
    "fourth_saturday",
    "other_weekly_off",
    "general_holiday",
)

#: What a staffed non-working date carries beside its reason. The office's own sentence,
#: spelled for a filed document. It is a narrative like a visit's, so it is written in the
#: language the register is filed in only where a translation exists; none is recorded for
#: it, and an untranslated narrative prints in English rather than as a blank line.
STAFF_ALLOCATION_NOTE = (
    "Allocated operational and maintenance works to be done on that day to staff"
)


def normalise_language(value: str | None) -> str:
    """Read a requested language, or the default when nothing was asked for.

    Refusing an unknown value is the caller's job; this only answers "what does an absent
    choice mean", and it means the platform's default rather than the last one used.
    """

    if value is None or str(value).strip() == "":
        return DEFAULT_ENTRY_LANGUAGE
    text = str(value).strip().lower()
    return text if text in ENTRY_LANGUAGES else DEFAULT_ENTRY_LANGUAGE


def is_known_language(value: str | None) -> bool:
    return value is not None and str(value).strip().lower() in ENTRY_LANGUAGES
