"""Translation — pure tool extension (offline phrasebook).

Exposes ONE tool: `translate`. It translates a short word/phrase between
languages using a small built-in phrasebook and returns RESULT DATA. It does NOT
speak and does NOT consume the turn — the watcher routes to it, then OpenClaw
phrases the spoken reply (TTS reads that).

Why offline: the extension framework has no `network` capability yet (see
EXTENSIONS.md), and the roadmap forbids shipping a scope a tool cannot actually
use. A real translation API would also need a credential. So this stays a
deterministic, fast, offline phrasebook of common phrases.

The MODEL interprets language and fills the clean machine args:
  - `text`: the word/phrase to translate
  - `to`:   target language code or name (see LANGS)
  - `from`: optional source language (defaults to English)
This tool stays dumb: it looks the phrase up in static tables.
"""
import re

# language code -> aliases
_ALIASES = {
    "en": ("en", "english"),
    "es": ("es", "spanish", "espanol", "español"),
    "fr": ("fr", "french", "francais", "français"),
    "de": ("de", "german", "deutsch"),
    "it": ("it", "italian", "italiano"),
    "pt": ("pt", "portuguese", "portugues", "português"),
    "zh": ("zh", "chinese", "mandarin", "中文", "汉语", "中文"),
    "ja": ("ja", "japanese", "日本語", "japanese"),
    "ko": ("ko", "korean", "한국어"),
    "ru": ("ru", "russian", "русский"),
    "hi": ("hi", "hindi", "हिन्दी"),
    "ar": ("ar", "arabic", "العربية"),
    "nl": ("nl", "dutch", "nederlands"),
    "sv": ("sv", "swedish", "svenska"),
    "la": ("la", "latin", "lingua latina"),
}

_ALIAS_LOOKUP = {}
for _code, _names in _ALIASES.items():
    for _n in _names:
        _ALIAS_LOOKUP[_n] = _code

# english key -> {lang_code: translation}
_PHRASES = {
    "hello": {"es": "hola", "fr": "bonjour", "de": "hallo", "it": "ciao",
              "pt": "olá", "zh": "你好", "ja": "こんにちは", "ko": "안녕하세요",
              "ru": "привет", "hi": "नमस्ते", "ar": "مرحبا", "nl": "hallo",
              "sv": "hej", "la": "salve"},
    "goodbye": {"es": "adiós", "fr": "au revoir", "de": "auf wiedersehen",
                "it": "arrivederci", "pt": "adeus", "zh": "再见",
                "ja": "さようなら", "ko": "안녕히 가세요", "ru": "до свидания",
                "hi": "अलविदा", "ar": "وداعا", "nl": "tot ziens", "sv": "hej då",
                "la": "vale"},
    "thank you": {"es": "gracias", "fr": "merci", "de": "danke",
                  "it": "grazie", "pt": "obrigado", "zh": "谢谢",
                  "ja": "ありがとう", "ko": "감사합니다", "ru": "спасибо",
                  "hi": "धन्यवाद", "ar": "شكرا", "nl": "dank je",
                  "sv": "tack", "la": "gratias"},
    "please": {"es": "por favor", "fr": "s'il vous plaît", "de": "bitte",
               "it": "per favore", "pt": "por favor", "zh": "请",
               "ja": "お願いします", "ko": "제발", "ru": "пожалуйста",
               "hi": "कृपया", "ar": "من فضلك", "nl": "alsjeblieft",
               "sv": "snälla", "la": "quaeso"},
    "yes": {"es": "sí", "fr": "oui", "de": "ja", "it": "sì", "pt": "sim",
            "zh": "是", "ja": "はい", "ko": "네", "ru": "да", "hi": "हाँ",
            "ar": "نعم", "nl": "ja", "sv": "ja", "la": "ita"},
    "no": {"es": "no", "fr": "non", "de": "nein", "it": "no", "pt": "não",
           "zh": "不", "ja": "いいえ", "ko": "아니요", "ru": "нет", "hi": "नहीं",
           "ar": "لا", "nl": "nee", "sv": "nej", "la": "non"},
    "good morning": {"es": "buenos días", "fr": "bonjour", "de": "guten morgen",
                     "it": "buongiorno", "pt": "bom dia", "zh": "早上好",
                     "ja": "おはよう", "ko": "좋은 아침", "ru": "доброе утро",
                     "hi": "सुप्रभात", "ar": "صباح الخير",
                     "nl": "goedemorgen", "sv": "god morgon", "la": "bonum mane"},
    "good night": {"es": "buenas noches", "fr": "bonne nuit",
                   "de": "gute nacht", "it": "buonanotte", "pt": "boa noite",
                   "zh": "晚安", "ja": "おやすみ", "ko": "잘 자요",
                   "ru": "спокойной ночи", "hi": "शुभ रात्रि",
                   "ar": "تصبح على خير", "nl": "goedenacht",
                   "sv": "god natt", "la": "bonam noctem"},
    "i love you": {"es": "te quiero", "fr": "je t'aime", "de": "ich liebe dich",
                   "it": "ti amo", "pt": "eu te amo", "zh": "我爱你",
                   "ja": "愛してる", "ko": "사랑해요", "ru": "я тебя люблю",
                   "hi": "मैं तुमसे प्यार करता हूँ", "ar": "أحبك",
                   "nl": "ik hou van je", "sv": "jag älskar dig",
                   "la": "te amo"},
    "how are you": {"es": "¿cómo estás?", "fr": "comment ça va ?",
                    "de": "wie geht's?", "it": "come stai?", "pt": "como vai?",
                    "zh": "你好吗？", "ja": "お元気ですか", "ko": "잘 지내요?",
                    "ru": "как дела?", "hi": "आप कैसे हैं?", "ar": "كيف حالك؟",
                    "nl": "hoe gaat het?", "sv": "hur mår du?",
                    "la": "quomodo vales?"},
    "excuse me": {"es": "perdón", "fr": "excusez-moi", "de": "entschuldigung",
                  "it": "scusi", "pt": "com licença", "zh": "对不起",
                  "ja": "すみません", "ko": "실례합니다", "ru": "извините",
                  "hi": "क्षमा करें", "ar": "عذرا", "nl": "pardon",
                  "sv": "ursäkta", "la": "ignosce"},
    "help": {"es": "ayuda", "fr": "aide", "de": "hilfe", "it": "aiuto",
             "pt": "ajuda", "zh": "帮助", "ja": "助けて", "ko": "도와주세요",
             "ru": "помощь", "hi": "मदद", "ar": "مساعدة", "nl": "help",
             "sv": "hjälp", "la": "auxilium"},
    "water": {"es": "agua", "fr": "eau", "de": "wasser", "it": "acqua",
              "pt": "água", "zh": "水", "ja": "水", "ko": "물", "ru": "вода",
              "hi": "पानी", "ar": "ماء", "nl": "water", "sv": "vatten",
              "la": "aqua"},
    "food": {"es": "comida", "fr": "nourriture", "de": "essen", "it": "cibo",
             "pt": "comida", "zh": "食物", "ja": "食べ物", "ko": "음식",
             "ru": "еда", "hi": "खाना", "ar": "طعام", "nl": "eten",
             "sv": "mat", "la": "cibus"},
    "friend": {"es": "amigo", "fr": "ami", "de": "freund", "it": "amico",
               "pt": "amigo", "zh": "朋友", "ja": "友達", "ko": "친구",
               "ru": "друг", "hi": "दोस्त", "ar": "صديق", "nl": "vriend",
               "sv": "vän", "la": "amicus"},
    "love": {"es": "amor", "fr": "amour", "de": "liebe", "it": "amore",
             "pt": "amor", "zh": "爱", "ja": "愛", "ko": "사랑", "ru": "любовь",
             "hi": "प्यार", "ar": "حب", "nl": "liefde", "sv": "kärlek",
             "la": "amor"},
    "one": {"es": "uno", "fr": "un", "de": "eins", "it": "uno", "pt": "um",
            "zh": "一", "ja": "一", "ko": "하나", "ru": "один", "hi": "एक",
            "ar": "واحد", "nl": "een", "sv": "ett", "la": "unus"},
    "two": {"es": "dos", "fr": "deux", "de": "zwei", "it": "due", "pt": "dois",
            "zh": "二", "ja": "二", "ko": "둘", "ru": "два", "hi": "दो",
            "ar": "اثنان", "nl": "twee", "sv": "två", "la": "duo"},
    "three": {"es": "tres", "fr": "trois", "de": "drei", "it": "tre",
              "pt": "três", "zh": "三", "ja": "三", "ko": "셋", "ru": "три",
              "hi": "तीन", "ar": "ثلاثة", "nl": "drie", "sv": "tre",
              "la": "tres"},
    "good": {"es": "bueno", "fr": "bon", "de": "gut", "it": "buono",
             "pt": "bom", "zh": "好", "ja": "良い", "ko": "좋은", "ru": "хорошо",
             "hi": "अच्छा", "ar": "جيد", "nl": "goed", "sv": "bra",
             "la": "bonus"},
}


def _norm_lang(tok):
    """Map a language name/code to a canonical code, or None."""
    if not tok:
        return None
    t = str(tok).strip().lower()
    if t in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[t]
    t2 = re.sub(r"[\s\.]", "", t)
    return _ALIAS_LOOKUP.get(t2)


def _norm_text(t):
    t = (t or "").strip().lower()
    t = re.sub(r"[!?.,;:]+$", "", t).strip()
    return re.sub(r"\s+", " ", t)


def setup(ctx):
    ctx.log("translation ready")

    def do_translate(args, ctx):
        text = _norm_text(args.get("text"))
        to_code = _norm_lang(args.get("to"))
        from_code = _norm_lang(args.get("from")) or "en"
        if not text:
            return {"ok": False, "error": "no text to translate"}
        if not to_code:
            return {"ok": False,
                    "error": f"unknown target language {args.get('to')!r}"}
        if to_code == from_code:
            return {"ok": True, "data": {"text": text, "from": from_code,
                                         "to": to_code, "result": text,
                                         "note": "source and target are the same"}}

        # Only an English-keyed phrasebook is built in. Support en<->X lookups.
        if from_code != "en":
            return {"ok": False, "error": "phrasebook only translates from English",
                    "data": {"from": from_code, "to": to_code}}

        entry = _PHRASES.get(text)
        if entry is None:
            return {"ok": False,
                    "error": f"no offline translation for {text!r}",
                    "data": {"text": text, "to": to_code,
                             "hint": "only common phrases are in the offline phrasebook"}}
        result = entry.get(to_code)
        if result is None:
            return {"ok": False,
                    "error": f"no {to_code} entry for {text!r}",
                    "data": {"text": text, "to": to_code}}
        return {"ok": True, "data": {"text": text, "from": "en", "to": to_code,
                                     "result": result}}

    def tools(ctx):
        return [{
            "name": "translate",
            "description": ("Translate a common word or short phrase between "
                            "languages OFFLINE (built-in phrasebook, no network). "
                            "`to` is a target language code or name like 'es', "
                            "'spanish', 'french', 'chinese', 'japanese'. `from` is "
                            "optional and defaults to English. Only common phrases "
                            "are available; unknown phrases return ok=false."),
            "args": {
                "text": {"type": "string", "required": True},
                "to": {"type": "string", "required": True},
                "from": {"type": "string", "required": False},
            },
            "fn": do_translate,
        }]

    return {"tools": tools}
