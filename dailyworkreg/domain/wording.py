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

#: The platform's own default. The office files a bilingual register, so an English
#: register and a Kannada one are not the same document - but which of the three is filed
#: is the office's decision, recorded with the generation that produced it.
DEFAULT_ENTRY_LANGUAGE = LANGUAGE_BOTH

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
REASON_KIND_LABELS = {
    "sunday": "Weekly off",
    "second_saturday": "Weekly off",
    "fourth_saturday": "Weekly off",
    "other_weekly_off": "Weekly off",
    "general_holiday": "Holiday",
    "leave": "Leave",
    "other_non_working": "Non-working day",
}


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
