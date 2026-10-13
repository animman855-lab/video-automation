"""Central profile and metadata-market configuration.

The publishing pipeline infers localization from the Notion Avatar value.  This
keeps language rules out of the individual platform publishers and preserves
English behavior for unknown profiles.
"""

from __future__ import annotations


DEFAULT_PROFILE_CONFIG = {
    "market": "global_english",
    "audience": "global English learners",
    "metadata_language": "English",
    "metadata_instruction": "Use natural, globally understandable English.",
    "cta": "Comment APP and I will send you the link.",
    "fallback_title_prefix": "Practice English",
    "fallback_description_prefix": "Practice useful English with Saloo English.",
    "localized_hashtags": [],
}


PROFILE_CONFIG = {
    "kayla": {
        **DEFAULT_PROFILE_CONFIG,
        "audience": "global English learners",
    },
    "teacherryan": {
        **DEFAULT_PROFILE_CONFIG,
        "audience": "global English learners",
    },
    "cindy": {
        **DEFAULT_PROFILE_CONFIG,
        "market": "arabic",
        "audience": "Arabic-speaking English learners",
        "metadata_language": "clear Modern Standard Arabic with English teaching examples",
        "metadata_instruction": (
            "Write primarily in clear, broadly understandable Modern Standard Arabic. "
            "Keep the English phrase being taught in English. Avoid narrow dialect slang."
        ),
        "cta": "اكتب APP في التعليقات وسأرسل لك الرابط.",
        "fallback_title_prefix": "تعلّم الإنجليزية",
        "fallback_description_prefix": "تدرّب على الإنجليزية بطريقة عملية مع Saloo English.",
        "localized_hashtags": ["#تعلم_الانجليزية", "#انجليزي"],
    },
    "thefluentbuild": {
        **DEFAULT_PROFILE_CONFIG,
        "audience": "global English learners",
    },
    "oliviaa": {
        **DEFAULT_PROFILE_CONFIG,
        "audience": "global English learners",
    },
}


def get_profile_config(profile: str) -> dict:
    """Return a copy so callers cannot mutate the shared configuration."""

    key = (profile or "").strip().lower()
    config = PROFILE_CONFIG.get(key, DEFAULT_PROFILE_CONFIG)
    return dict(config)
