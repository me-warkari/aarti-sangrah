#!/usr/bin/env python3
"""aarti-sangrah site builder — Apache License 2.0.

Reads the Devanagari text files in content/<slug>/<slug>.md (+ SOURCE.md)
and generates a plain-HTML static site in site/.

Usage:  python3 tools/build.py   (from the repo root)
Output: site/ — index.html, aarti/*, devata/*, prasang/*, sant/*, hindi/,
        srot/*, shodh/, vishayi/, search-index.json, assets/, fonts/

No framework, no JavaScript required to read. content/ is the source of
truth; re-run after adding or fixing a work.

Site rules enforced here (do not weaken them in a later edit):
- No specific source site is ever named on a rendered page. SOURCE.md
  files in the repo may name the sources compared, but the rendered
  /srot/ pages drop the "Sources compared" section and replace every
  other site name / URL with a generic "एका स्रोतात". Wikisource and
  Wikipedia are open-licence references and may be named.
- Disclaimers are rendered only where a SOURCE.md or the text file
  itself carries one; none are invented.
"""
import datetime
import html
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote as urlquote

ROOT = Path(__file__).resolve().parent.parent   # repo root (aarti-sangrah/)
TOOLS = ROOT / "tools"
CONTENT = ROOT / "content"
SITE = ROOT / "site"
REPO = "https://github.com/me-warkari/aarti-sangrah"
ISSUES = REPO + "/issues/new"
SITE_URL = "https://me-warkari.github.io/aarti-sangrah/"
# Cloudflare Web Analytics beacon token (cookieless, anonymous pageviews).
# Empty until the Web Analytics site is created; then fill in the token.
CF_BEACON_TOKEN = ""

DEV_DIGITS = "०१२३४५६७८९"


def dev_num(n: int) -> str:
    return "".join(DEV_DIGITS[int(d)] for d in str(n))


def dev_to_int(s: str) -> int:
    return int("".join(str(DEV_DIGITS.index(c)) for c in s if c in DEV_DIGITS))


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ---------------------------------------------------------------- sanitising
# Rendered pages must never name a lyric / devotional source site (their
# texts are not ours to republish, and the user forbids naming them on the
# public site). The replacement list is DERIVED at build time from the
# SOURCE.md files — every source label and every linked hostname becomes a
# token replaced with a generic phrase — instead of a hard-coded list,
# because a hard-coded list would itself name those sites in a published
# file. URLs are stripped outright. Wikisource / Wikipedia / archive.org /
# the UPenn manuscript record are open references and are kept.

ALLOWED_HOSTS = ("wikisource.org", "wikipedia.org", "archive.org",
                 "github.com", "upenn.edu")

# First words of source labels that are ordinary vocabulary and must never
# be replaced on their own (e.g. the language word, or "Shri"/"Sai").
GENERIC_FIRST_WORDS = {
    "Marathi", "Hindi", "Sanskrit", "Aarti", "Ganpati", "Shirdi", "Sai",
    "Baba", "Shri", "Shree", "Sant", "Deva", "Devi", "Mandir", "Temple",
    "Library", "Page", "Guru", "Sagunopasana", "ShirdiBaba",
}


def _host_stem(host: str):
    """First label of a hostname, or None if allowed / too short to use."""
    host = host.lower().lstrip("www.")
    if any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        return None
    first = host.split(".")[0]
    return first if len(first) >= 4 else None


def _build_replacements(content_dir: Path):
    repl = {}
    for src in sorted(content_dir.glob("*/SOURCE.md")):
        text = src.read_text(encoding="utf-8")
        for m in re.finditer(r"https?://([A-Za-z0-9.-]+)", text):
            stem = _host_stem(m.group(1))
            if stem:
                repl[stem] = "एका स्रोतात"
        for m in re.finditer(r"\b([A-Za-z0-9-]+\.(?:com|org|in|net|info|cc|edu))\b", text):
            stem = _host_stem(m.group(1))
            if stem:
                repl[stem] = "एका स्रोतात"
        in_sources = False
        for line in text.splitlines():
            if line.startswith("## Sources compared"):
                in_sources = True
                continue
            if in_sources and line.startswith("## "):
                break
            if not in_sources:
                continue
            m = re.match(r"\s*\d+\.\s+(.+)", line)
            if not m:
                continue
            label = re.split(r"—|https?://", m.group(1))[0].strip(" :")
            if not label:
                continue
            repl[label] = "एका स्रोतात"
            repl[label.replace(" ", "")] = "एका स्रोतात"
            first = label.split()[0]
            if len(first) >= 5 and first not in GENERIC_FIRST_WORDS:
                repl[first] = "एका स्रोतात"
    # Longest tokens first so multi-word labels match before their parts.
    return sorted(repl.items(), key=lambda kv: -len(kv[0]))


SITE_REPLACEMENTS = _build_replacements(CONTENT)


def sanitize_text(s: str) -> str:
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"www\.\S+", "", s)
    # Any bare domain left in prose (not an open reference) is removed.
    def _drop_domain(m):
        host = m.group(1).lower()
        if any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            return m.group(0)
        return "एका स्रोतात"
    s = re.sub(r"\b([A-Za-z0-9-]+\.(?:com|org|in|net|info|cc|edu))\b",
               _drop_domain, s)
    for name, rep in SITE_REPLACEMENTS:
        s = re.sub(re.escape(name), rep, s, flags=re.IGNORECASE)
    # A Devanagari phrase naming two compared sources ("… या दोन स्रोतांत")
    # is reduced to the generic wording, without naming them.
    s = re.sub(r"[\u0900-\u097F]+(?:\s+[\u0900-\u097F]+)*\s+या दोन स्रोतांत",
               "दोन स्रोतांत", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\(\s*,", "(", s)
    s = re.sub(r",\s*\)", ")", s)
    return s.strip()


# ---------------------------------------------------------------- data
# WORKS is the sung order (prev/next follows it). "unit" is the word used
# for the size line. "saints" feeds /sant/. "occasions" feeds /prasang/.

WORKS = [
    {"slug": "sukhkarta-dukhharta", "title": "सुखकर्ता दुःखहर्ता",
     "short": "सुखकर्ता दुःखहर्ता", "deity": "गणेश", "deity_slug": "ganesh",
     "author": "समर्थ रामदास", "saints": ["ramdas"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "गणपतीची सर्वांत जास्त गायली जाणारी आरती."},
    {"slug": "lavthavti-vikrala", "title": "लवथवती विक्राळा",
     "short": "लवथवती विक्राळा", "deity": "शिव", "deity_slug": "shiva",
     "author": "समर्थ रामदास", "saints": ["ramdas"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "शंकराची आरती — महाशिवरात्रीला गायली जाते."},
    {"slug": "durge-durghat-bhari", "title": "दुर्गे दुर्घट भारी",
     "short": "दुर्गे दुर्घट भारी", "deity": "देवी", "deity_slug": "devi",
     "author": "नरहरी सोनार", "saints": ["narhari-sonar", "ramdas"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "महिषासुरमर्दिनी देवीची आरती — कर्तृत्व वादग्रस्त, दोन्ही परंपरा नोंदवल्या आहेत."},
    {"slug": "yuge-atthavis", "title": "युगे अठ्ठावीस विटेवरी उभा",
     "short": "युगे अठ्ठावीस", "deity": "विठ्ठल", "deity_slug": "vitthal",
     "author": "संत नामदेव", "saints": ["namdev"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "पंढरीच्या विठ्ठलाची आरती — आषाढी व कार्तिकी एकादशीला."},
    {"slug": "trigunatmak-traimurti", "title": "त्रिगुणात्मक त्रैमूर्ती",
     "short": "त्रिगुणात्मक त्रैमूर्ती", "deity": "दत्त", "deity_slug": "datta",
     "author": "संत एकनाथ", "saints": ["eknath"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "दत्ताची आरती — 'एका जनार्दनी' या मुद्रेवरून एकनाथांची मानली जाते."},
    {"slug": "satrane-uddane", "title": "सत्राणे उड्डाणे",
     "short": "सत्राणे उड्डाणे", "deity": "हनुमान", "deity_slug": "hanuman",
     "author": "समर्थ रामदास", "saints": ["ramdas"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "मारुतीची आरती — लहान, दोन कडवी."},
    {"slug": "ghalin-lotangan", "title": "घालीन लोटांगण",
     "short": "घालीन लोटांगण", "deity": "समापन", "deity_slug": "samapan",
     "author": "संत नामदेव व संस्कृत परंपरा", "saints": ["namdev"], "lang": "मराठी + संस्कृत",
     "cat": "marathi", "occasions": ["samapan"], "unit": "भाग",
     "blurb": "आरतीनंतरची समापन प्रार्थना — तीन भाग, वेगळी गाणी यात मिसळलेली नाहीत."},
    {"slug": "yei-ho-vitthale", "title": "येई हो विठ्ठले",
     "short": "येई हो विठ्ठले", "deity": "विठ्ठल", "deity_slug": "vitthal",
     "author": "विष्णुदास नामा", "saints": ["vishnudas-nama", "namdev"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "विठ्ठलाची आरती — 'विष्णुदास नामा' ही मुद्रा; जनाबाईंची म्हणून चुकून म्हटली जाते."},
    {"slug": "aarti-dnyanraja", "title": "आरती ज्ञानराजा",
     "short": "आरती ज्ञानराजा", "deity": "ज्ञानेश्वर", "deity_slug": "dnyaneshwar",
     "author": "वादग्रस्त — रामदास / नामदेव", "saints": ["ramdas", "namdev"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "ज्ञानेश्वरांची आरती — कर्तृत्व वादग्रस्त, दोन्ही परंपरा नोंदवल्या आहेत."},
    {"slug": "jay-devi-jay-devi-jay-mahalakshmi", "title": "जय देवी जय देवी जय महालक्ष्मी",
     "short": "जय देवी जय महालक्ष्मी", "deity": "देवी", "deity_slug": "devi",
     "author": "संत मुक्तेश्वर", "saints": ["mukteshwar"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "करवीरपुरवासिनी महालक्ष्मीची आरती — मार्गशीर्ष गुरुवारी."},
    {"slug": "jyeshtha-gauri-aarti", "title": "ज्येष्ठा गौरी आरती",
     "short": "ज्येष्ठा गौरी आरती", "deity": "देवी", "deity_slug": "devi",
     "author": "पारंपरिक (कर्ता अज्ञात)", "saints": ["traditional"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "ज्येष्ठा गौरीची आरती — गौरी उत्सवात गायली जाते."},
    {"slug": "ovalu-aarti-madangopala", "title": "ओवाळू आरती मदनगोपाळा",
     "short": "ओवाळू आरती मदनगोपाळा", "deity": "कृष्ण", "deity_slug": "krishna",
     "author": "संत एकनाथ (परंपरा)", "saints": ["eknath"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "कृष्णाची आरती — या आरतीचे दोन वेगळे पाठ परंपरेत चालतात."},
    {"slug": "jai-ganesh-jai-ganesh-deva", "title": "जय गणेश जय गणेश देवा",
     "short": "जय गणेश जय गणेश देवा", "deity": "गणेश", "deity_slug": "ganesh",
     "author": "पारंपरिक (हिंदी)", "saints": ["traditional"], "lang": "हिंदी",
     "cat": "hindi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "गणेशाची हिंदी आरती — महाराष्ट्रात मराठी आरतीबरोबर गायली जाते."},
    {"slug": "tukaramachi-aarti", "title": "तुकारामाची आरती",
     "short": "तुकारामाची आरती", "deity": "तुकाराम", "deity_slug": "tukaram",
     "author": "रामेश्वर भट", "saints": ["rameshwar-bhat"], "lang": "मराठी",
     "cat": "marathi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "तुकाराम महाराजांवरची आरती — त्यांनी रचलेली नाही, त्यांचे शिष्य रामेश्वर भट यांची मानली जाते."},
    {"slug": "om-jai-jagdish-hare", "title": "ॐ जय जगदीश हरे",
     "short": "ॐ जय जगदीश हरे", "deity": "विष्णू", "deity_slug": "vishnu",
     "author": "पं. श्रद्धा राम फिल्लौरी", "saints": ["phillauri"], "lang": "हिंदी",
     "cat": "hindi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "जगदीशाची (विष्णूची) हिंदी आरती — इ.स. १८७० च्या सुमारास रचलेली."},
    {"slug": "shendur-lal-chadhayo", "title": "शेंदुर लाल चढायो",
     "short": "शेंदुर लाल चढायो", "deity": "गणेश", "deity_slug": "ganesh",
     "author": "पारंपरिक (हिंदी)", "saints": ["traditional"], "lang": "हिंदी",
     "cat": "hindi", "occasions": ["utsav"], "unit": "कडवे",
     "blurb": "गणेशाची हिंदी आरती — कर्ता स्थापित नाही; चित्रपटातील आवृत्ती येथे घेतलेली नाही."},
    {"slug": "kakad-aarti", "title": "काकड आरती (विठ्ठल)",
     "short": "काकड आरती", "deity": "विठ्ठल", "deity_slug": "vitthal",
     "author": "तुकाराम, जनाबाई, नामदेव", "saints": ["tukaram", "janabai", "namdev"], "lang": "मराठी",
     "cat": "daily", "occasions": ["sakal"], "unit": "भाग",
     "blurb": "विठ्ठलाची सर्वसाधारण पहाटेची आरती — तीन भूपाळ्या व काकड आरती."},
    {"slug": "bhupali", "title": "भूपाळी — घनश्याम सुंदरा श्रीधरा",
     "short": "भूपाळी", "deity": "कृष्ण", "deity_slug": "krishna",
     "author": "शाहीर होनाजी बाळा", "saints": ["honaji-bala"], "lang": "मराठी",
     "cat": "daily", "occasions": ["sakal"], "unit": "कडवे",
     "blurb": "पहाटे कृष्णाला उठवणारी भूपाळी — विकिस्रोतावरील टंकलेखनावर आधारित."},
    {"slug": "gavlani", "title": "गवळण — गाऱ्हाणें",
     "short": "गवळण", "deity": "कृष्ण", "deity_slug": "krishna",
     "author": "संत नामदेव (परंपरा)", "saints": ["namdev"], "lang": "मराठी",
     "cat": "daily", "occasions": ["utsav"], "unit": "दोहा",
     "blurb": "गोकुळच्या स्त्रियांची गाऱ्हाणी — नामदेव-परंपरेतील एक पारंपरिक गवळण.",
     "flag": "ही गवळण एकाच स्रोतावर आधारित आहे — या विशिष्ट गवळणीचा दुसरा स्वतंत्र स्रोत अद्याप सापडलेला नाही; तपासणी बाकी आहे."},
    {"slug": "mantra-pushpanjali", "title": "मंत्रपुष्पांजली",
     "short": "मंत्रपुष्पांजली", "deity": "समापन", "deity_slug": "samapan",
     "author": "वैदिक (प्राचीन)", "saints": ["vedic"], "lang": "संस्कृत",
     "cat": "closing", "occasions": ["samapan"], "unit": "मंत्र",
     "blurb": "आरतीनंतर फुले वाहताना म्हणायची वैदिक प्रार्थना — पाच मंत्र."},
    {"slug": "ganapati-atharvashirsha", "title": "श्री गणपत्यथर्वशीर्षम्",
     "short": "गणपत्यथर्वशीर्ष", "deity": "गणेश", "deity_slug": "ganesh",
     "author": "प्राचीन (अथर्ववेद परंपरा)", "saints": ["vedic"], "lang": "संस्कृत",
     "cat": "closing", "occasions": ["samapan"], "unit": "विभाग",
     "blurb": "गणपती अथर्वशीर्ष — संपूर्ण पाठ, शांतीपाठासह."},
    {"slug": "shubham-karoti", "title": "शुभं करोति",
     "short": "शुभं करोति", "deity": "विष्णू", "deity_slug": "vishnu",
     "author": "पारंपरिक", "saints": ["traditional"], "lang": "संस्कृत",
     "cat": "closing", "occasions": ["sandhya-ratra", "samapan"], "unit": "श्लोक",
     "blurb": "संध्याकाळी दिवा लावताना म्हणायची प्रार्थना — दोन श्लोक."},
    {"slug": "karpur-gauram", "title": "कर्पूरगौरं करुणावतारं",
     "short": "कर्पूरगौरम्", "deity": "शिव", "deity_slug": "shiva",
     "author": "प्राचीन", "saints": ["vedic"], "lang": "संस्कृत",
     "cat": "closing", "occasions": ["samapan"], "unit": "श्लोक",
     "blurb": "आरतीनंतर म्हणायचा शिवाचा श्लोक — एक श्लोक."},
    {"slug": "kakad-aarti-sai", "title": "काकड आरती (साईबाबा)",
     "short": "काकड आरती (साई)", "deity": "साईबाबा", "deity_slug": "sai",
     "author": "अनेक संत — तुकाराम, जनाबाई, नामदेव, भिष्म, दासगणू",
     "saints": ["tukaram", "janabai", "namdev", "bhishma", "dasganu"], "lang": "मराठी + हिंदी",
     "cat": "sai", "occasions": ["sakal"], "unit": "भाग",
     "blurb": "शिर्डी साईबाबांची पहाटेची आरती — अर्धवट: दोन भाग वगळले आहेत.",
     "status": "partial",
     "status_note": "ही काकड आरती अर्धवट आहे — दोन भाग (साईनाथ गुरु माझे आई हे भजन आणि श्रीसद्गुरु पद) यांचे लेखक किंवा मृत्यूवर्ष पडताळता आले नाही म्हणून वगळले आहेत. तपशील स्रोत पानावर आहे."},
    {"slug": "madhyan-aarti-sai", "title": "माध्यान्ह आरती (साईबाबा)",
     "short": "माध्यान्ह आरती (साई)", "deity": "साईबाबा", "deity_slug": "sai",
     "author": "अनेक संत — आडकर, दासगणू, उपासनी बाबा, भिष्म, देव",
     "saints": ["adkar", "dasganu", "upasani", "bhishma", "deo"], "lang": "मराठी",
     "cat": "sai", "occasions": ["dupar"], "unit": "भाग",
     "blurb": "शिर्डी साईबाबांची दुपारची आरती — अर्धवट: नमस्काराष्टक वगळले आहे.",
     "status": "partial",
     "status_note": "ही माध्यान्ह आरती अर्धवट आहे — नमस्काराष्टक (मोहनी राज) यांचा लेखक पडताळता आला नाही म्हणून वगळले आहे. तपशील स्रोत पानावर आहे."},
    {"slug": "dhoop-aarti-sai", "title": "धूप आरती (साईबाबा)",
     "short": "धूप आरती (साई)", "deity": "साईबाबा", "deity_slug": "sai",
     "author": "अनेक संत — आडकर, दासगणू, उपासनी बाबा, भिष्म, देव",
     "saints": ["adkar", "dasganu", "upasani", "bhishma", "deo"], "lang": "मराठी",
     "cat": "sai", "occasions": ["sandhya-ratra"], "unit": "भाग",
     "blurb": "शिर्डी साईबाबांची संध्याकाळची आरती — अर्धवट: नमस्काराष्टक वगळले आहे.",
     "status": "partial",
     "status_note": "ही धूप आरती अर्धवट आहे — नमस्काराष्टक (मोहनी राज) यांचा लेखक पडताळता आला नाही म्हणून वगळले आहे. तपशील स्रोत पानावर आहे."},
    {"slug": "shej-aarti-sai", "title": "शेज आरती (साईबाबा)",
     "short": "शेज आरती (साई)", "deity": "साईबाबा", "deity_slug": "sai",
     "author": "अनेक संत — तुकाराम, भिष्म, रामेश्वर भट",
     "saints": ["tukaram", "bhishma", "rameshwar-bhat"], "lang": "मराठी",
     "cat": "sai", "occasions": ["sandhya-ratra"], "unit": "भाग",
     "blurb": "शिर्डी साईबाबांची रात्रीची आरती — तपासलेल्या याद्यांनुसार पूर्ण आहे.",
     "status": "full",
     "status_note": "ही शेज आरती तपासलेल्या याद्यांनुसार पूर्ण आहे — काहीही वगळलेले नाही."},
]

DEITIES = [
    ("ganesh", "गणेश", "गणपतीच्या आरत्या — सुखकर्ता दुःखहर्ता व इतर."),
    ("shiva", "शिव", "शंकराच्या आरत्या व श्लोक."),
    ("devi", "देवी", "दुर्गा, महालक्ष्मी व गौरीच्या आरत्या."),
    ("vitthal", "विठ्ठल", "पंढरीच्या विठ्ठलाच्या आरत्या व पहाटेची काकड आरती."),
    ("datta", "दत्त", "दत्ताची आरती."),
    ("hanuman", "हनुमान", "मारुतीची आरती."),
    ("krishna", "कृष्ण", "कृष्णाच्या आरत्या, भूपाळी व गवळण."),
    ("vishnu", "विष्णू", "विष्णूची हिंदी आरती व दिव्याची प्रार्थना."),
    ("dnyaneshwar", "ज्ञानेश्वर", "ज्ञानेश्वरांवरची आरती."),
    ("tukaram", "तुकाराम", "तुकाराम महाराजांवरची आरती."),
    ("sai", "साईबाबा", "शिर्डी साईबाबांच्या चार वेळच्या आरत्या."),
    ("samapan", "समापन प्रार्थना", "कोणत्याही एका देवतेसाठी नसलेल्या, आरतीनंतरच्या प्रार्थना."),
]

OCCASIONS = [
    ("sakal", "पहाट व सकाळ", "पहाटे गायल्या जाणाऱ्या आरत्या — काकड आरती व भूपाळी."),
    ("dupar", "दुपार", "दुपारी गायली जाणारी आरती."),
    ("sandhya-ratra", "संध्याकाळ व रात्र", "संध्याकाळी दिवा लावताना व रात्री गायल्या जाणाऱ्या आरत्या."),
    ("utsav", "उत्सव व सण", "गणेशोत्सव, नवरात्र, एकादशी, शिवरात्री अशा उत्सवांत गायल्या जाणाऱ्या आरत्या."),
    ("samapan", "समापन", "आरती संपल्यावर म्हणायच्या प्रार्थना व श्लोक."),
]

SAINTS = [
    ("ramdas", "समर्थ रामदास", "१६०८–१६८१"),
    ("namdev", "संत नामदेव", "सु. १२७०–१३५०"),
    ("eknath", "संत एकनाथ", "१५३३–१५९९"),
    ("tukaram", "संत तुकाराम", "१६०८–१६४९/५०"),
    ("janabai", "संत जनाबाई", "१३वे–१४वे शतक"),
    ("vishnudas-nama", "विष्णुदास नामा", "१५वे शतक"),
    ("narhari-sonar", "नरहरी सोनार", "१३वे शतक"),
    ("mukteshwar", "संत मुक्तेश्वर", "१६०८–१६६०"),
    ("rameshwar-bhat", "रामेश्वर भट", "१७वे शतक"),
    ("honaji-bala", "शाहीर होनाजी बाळा", "१७५४–१८४४"),
    ("phillauri", "पं. श्रद्धा राम फिल्लौरी", "१८३७–१८८१"),
    ("bhishma", "कृष्णशास्त्री जोगेश्वर भिष्म", "१८५४–१९३५"),
    ("dasganu", "दासगणू महाराज", "१८६८–१९६२"),
    ("adkar", "माधवराव आडकर", "१८७७–१९५५"),
    ("upasani", "उपासनी बाबा", "१८७०–१९४१"),
    ("deo", "बाळकृष्ण विश्वनाथ देव", "१९वे–२०वे शतक"),
    ("vedic", "वैदिक / प्राचीन", "प्राचीन, कर्ता अज्ञात"),
    ("traditional", "पारंपरिक / अज्ञात", "कर्ता अज्ञात"),
]

# Today's aarti: rotate by weekday (Mon=0 .. Sun=6).
TODAY_ROTATION = [
    ("sukhkarta-dukhharta", "सोमवार"),
    ("satrane-uddane", "मंगळवार"),
    ("yuge-atthavis", "बुधवार"),
    ("trigunatmak-traimurti", "गुरुवार"),
    ("durge-durghat-bhari", "शुक्रवार"),
    ("lavthavti-vikrala", "शनिवार"),
    ("yei-ho-vitthale", "रविवार"),
]

# ---------------------------------------------------------------- parsing

CLOSE_NUM = re.compile(r"॥\s*([०-९]+)\s*॥\s*$")
CLOSE_NONNUM = re.compile(r"॥\s*([^०-९॥]+?)\s*॥\s*$")
HEADING_LINE = re.compile(r"^॥\s*([^०-९॥]+?)\s*॥\s*$")
NUM_HEAD = re.compile(r"^[०-९]+\.\s+(.+)$")


def parse_text(slug: str, text: str):
    """Split a work's text into items.

    Item kinds: heading | invocation | note | refrain | verse | unnumbered.
    - verse: a block ending in ॥N॥ (Devanagari number kept as its display no.)
    - refrain: a block with ॥धृ॥, a "ध्रु —" opening, or a trailing
      ॥non-numeric words॥ marker (e.g. ॥ ॐ जय जगदीश हरे ॥).
    - unnumbered: a block with no number marker (Atharvashirsha passages,
      Shanti lines, etc.).
    - The first ॥ ॥ heading is the work title and is skipped.
    - "(टीप: ...)" lines are the work's disclaimer, not verses.
    - "(...)" note lines are rendered as notes, not verses.
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    items, disclaimers = [], []
    cur, section = [], None
    seq = 0
    first_heading_skipped = False

    def flush():
        nonlocal cur, seq
        if not cur:
            return
        block_lines = list(cur)
        cur = []
        block = "\n".join(block_lines)
        if len(block_lines) == 1 and block.strip().endswith("महाराज की जय"):
            items.append({"kind": "invocation", "text": block.strip()})
            return
        last = block_lines[-1]
        m = CLOSE_NUM.search(last)
        is_ref = ("॥धृ॥" in block or "॥ धृ ॥" in block
                  or block_lines[0].strip().startswith("ध्रु")
                  or CLOSE_NONNUM.search(last) is not None)
        seq += 1
        if m:
            items.append({"kind": "verse", "lines": block_lines,
                          "number": dev_to_int(m.group(1)), "seq": seq,
                          "section": section})
        elif is_ref:
            items.append({"kind": "refrain", "lines": block_lines,
                          "seq": seq, "section": section})
        else:
            items.append({"kind": "unnumbered", "lines": block_lines,
                          "seq": seq, "section": section})

    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            flush()
            i += 1
            continue
        if s.startswith("टीप:") or s.startswith("(टीप:"):
            buf = s
            while s.startswith("(टीप:") and not buf.rstrip().endswith(")") \
                    and i + 1 < len(lines):
                i += 1
                buf += " " + lines[i].strip()
            d = re.sub(r"^\(?टीप:\s*", "", buf)
            d = re.sub(r"\)\s*$", "", d)
            disclaimers.append(sanitize_text(d))
            i += 1
            continue
        if s.startswith("("):
            flush()
            buf = s
            while not buf.rstrip().endswith(")") and i + 1 < len(lines):
                i += 1
                buf += " " + lines[i].strip()
            items.append({"kind": "note", "text": buf})
            i += 1
            continue
        if s.startswith("## "):
            flush()
            section = s[3:].strip()
            items.append({"kind": "heading", "text": section})
            i += 1
            continue
        m = NUM_HEAD.match(s)
        if m:
            flush()
            section = m.group(1).strip()
            items.append({"kind": "heading", "text": section})
            i += 1
            continue
        m = HEADING_LINE.match(s)
        if m and not cur:
            flush()
            if not first_heading_skipped:
                first_heading_skipped = True
                section = m.group(1).strip()
                i += 1
                continue
            section = m.group(1).strip()
            items.append({"kind": "heading", "text": section})
            i += 1
            continue
        cur.append(s)
        if CLOSE_NUM.search(s) or "॥धृ॥" in s or "॥ धृ ॥" in s:
            flush()
        i += 1
    flush()
    return items, disclaimers


# ------------------------------------------------------- SOURCE.md rendering

def _inline_md(s: str) -> str:
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+?)\*", r"<em>\1</em>", s)
    return s


def _render_table(rows):
    # rows: list of raw "| a | b |" lines; first row is the header.
    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]
    if len(rows) < 2:
        return ""
    head = cells(rows[0])
    body = [cells(r) for r in rows[1:]
            if not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", r)]
    out = ['<table class="src"><thead><tr>']
    out += [f"<th>{_inline_md(c)}</th>" for c in head]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(
            f"<td>{_inline_md(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def render_source_md(slug: str) -> str:
    """SOURCE.md as sanitised HTML for the public /srot/ page.

    The 'Sources compared' section is dropped entirely (names + links
    live only in the repo's SOURCE.md). Every other site name / URL is
    replaced by the sanitiser. Tables (Sai per-part verification) render.
    """
    path = CONTENT / slug / "SOURCE.md"
    if not path.exists():
        return "<p>स्रोत माहिती उपलब्ध नाही.</p>"
    raw = sanitize_text(path.read_text(encoding="utf-8"))
    lines = raw.splitlines()
    out, para, lst, lst_tag, table = [], [], [], None, []
    skip_section = False

    def flush_para():
        if para:
            out.append(f"<p>{_inline_md(' '.join(para))}</p>")
            para.clear()

    def flush_list():
        nonlocal lst, lst_tag
        if lst:
            out.append(f"<{lst_tag}>" + "".join(
                f"<li>{_inline_md(x)}</li>" for x in lst) + f"</{lst_tag}>")
            lst, lst_tag = [], None

    def flush_table():
        nonlocal table
        if table:
            out.append(_render_table(table))
            table = []

    for raw_ln in lines:
        line = raw_ln.strip()
        if not line:
            flush_para()
            flush_list()
            flush_table()
            continue
        if line.startswith("# "):
            flush_para()
            flush_list()
            flush_table()
            continue
        m = re.match(r"^##+\s+(.+)$", line)
        if m:
            flush_para()
            flush_list()
            flush_table()
            head = m.group(1).strip()
            if head.startswith("Sources compared"):
                skip_section = True
                continue
            skip_section = False
            tag = "h3" if line.startswith("## ") else "h4"
            out.append(f"<{tag}>{esc(head)}</{tag}>")
            continue
        if skip_section:
            continue
        if line.startswith("|"):
            flush_para()
            flush_list()
            table.append(line)
            continue
        flush_table()
        if line.startswith("- "):
            flush_para()
            if lst_tag != "ul":
                flush_list()
                lst_tag = "ul"
            lst.append(line[2:].strip())
            continue
        if re.match(r"^\d+\.\s", line):
            flush_para()
            if lst_tag != "ol":
                flush_list()
                lst_tag = "ol"
            lst.append(re.sub(r"^\d+\.\s", "", line).strip())
            continue
        if lst:
            lst[-1] += " " + line
            continue
        para.append(line)
    flush_para()
    flush_list()
    flush_table()
    body = "\n".join(out)
    if "http" in body:
        print(f"WARNING: URL survived sanitising in {slug} SOURCE rendering",
              file=sys.stderr)
    body += ('<p>तुलना केलेल्या स्रोतांची नावे व जोडण्या repository मधील '
             f'<a href="{REPO}/blob/main/content/{slug}/SOURCE.md">SOURCE.md</a> '
             'फाइलमध्ये आहेत; त्या या पानावर दिलेल्या नाहीत.</p>')
    return body


def source_facts(slug: str):
    """Licence tag, proofing status, and the SOURCE.md Disclaimer section."""
    path = CONTENT / slug / "SOURCE.md"
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    licence = ("CC BY-SA 4.0 · विकिस्रोत" if "CC BY-SA" in raw
               else "सार्वजनिक क्षेत्र (Public Domain)")
    proofing = "तपासणी बाकी"  # every SOURCE.md currently says NEEDED
    disclaimer = None
    m = re.search(r"^## Disclaimer[^\n]*\n(.*?)(?=^## |\Z)", raw,
                  re.M | re.S)
    if m:
        txt = " ".join(ln.strip() for ln in m.group(1).strip().splitlines()
                       if ln.strip())
        txt = re.sub(r"^“|”$", "", txt)
        if txt:
            disclaimer = sanitize_text(txt)
    return licence, proofing, disclaimer


def load_work(work):
    text = (CONTENT / work["slug"] / f'{work["slug"]}.md').read_text(
        encoding="utf-8")
    items, disclaimers = parse_text(work["slug"], text)
    licence, proofing, src_disclaimer = source_facts(work["slug"])
    if not disclaimers and src_disclaimer:
        disclaimers = [src_disclaimer]
    verses = [i for i in items if i["kind"] == "verse"]
    refrains = [i for i in items if i["kind"] == "refrain"]
    unnumbered = [i for i in items if i["kind"] == "unnumbered"]
    return {"items": items, "verses": verses, "refrains": refrains,
            "unnumbered": unnumbered, "disclaimers": disclaimers,
            "licence": licence, "proofing": proofing}

# ---------------------------------------------------------------- template

BASE = """<!DOCTYPE html>
<html lang="mr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — आरती संग्रह</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="आरती संग्रह">
<meta property="og:title" content="{title} — आरती संग्रह">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
{beacon}
<link rel="stylesheet" href="{prefix}assets/style.css">
<script src="{prefix}assets/reader.js" defer></script>
</head>
<body>
<header class="site-head">
  <div class="wrap">
    <a class="brand" href="{prefix}index.html">॥ आरती संग्रह ॥</a>
    <nav aria-label="मुख्य">
      <a href="{prefix}index.html">मुख्य</a>
      <a href="{prefix}devata/index.html">देवता</a>
      <a href="{prefix}prasang/index.html">प्रसंग</a>
      <a href="{prefix}sant/index.html">संत</a>
      <a href="{prefix}hindi/index.html">हिंदी</a>
      <a href="{prefix}shodh/index.html">शोध</a>
      <a href="{prefix}vishayi/index.html">विषयी</a>
    </nav>
  </div>
</header>
<div class="toolbar" role="group" aria-label="वाचन सोयी">
  <div class="wrap">
    <button id="font-minus" type="button" title="अक्षर लहान">अ−</button>
    <button id="font-plus" type="button" title="अक्षर मोठे">अ+</button>
    <button id="spacing" type="button" title="ओळींमधील अंतर">अंतर</button>
    <button id="fontface" type="button" title="अक्षरशैली बदला">अक्षर</button>
    <button id="theme" type="button" title="प्रकाश / अंधारी पद्धत">☾ अंधारी</button>
  </div>
</div>
<main class="wrap">
{body}
</main>
<footer class="site-foot">
  <div class="wrap">
    <p>आरती संग्रह — मराठी व हिंदी आरत्या, मूळ मजकूर, मोफत. संकेतस्थळाचा कोड Apache 2.0;
    मजकुराचे परवाने प्रत्येक कृतीच्या स्रोत पानावर दिलेले आहेत.</p>
    <p><a href="{prefix}vishayi/index.html">विषयी व स्रोत</a> ·
    <a href="{repo}">GitHub</a></p>
  </div>
</footer>
</body>
</html>
"""


def beacon() -> str:
    """Cloudflare Web Analytics beacon (cookieless, anonymous). Empty until configured."""
    if not CF_BEACON_TOKEN:
        return ""
    return ('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
            'data-cf-beacon=\'{"token": "' + CF_BEACON_TOKEN + '"}\'></script>')


# WhatsApp brand glyph (Simple Icons, CC0) — inline so every page is self-contained.
# The svg carries explicit width/height and the link carries inline pill styling,
# so the button renders correctly even if the visitor's browser still holds a
# cached copy of style.css (new HTML + old CSS otherwise blows the icon up).
WA_ICON = ('<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" '
           'd="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z"/></svg>')
SHARE_STYLE = ("display:inline-flex;align-items:center;gap:.5em;background:#25D366;"
               "color:#fff;text-decoration:none;font-weight:700;padding:.55em 1em;border-radius:999px")


def share_link(title: str, url: str) -> str:
    """One-tap WhatsApp share for a work — how devotional text spreads."""
    text = urlquote(f"{title} — आरती संग्रह {url}")
    return (f'<p class="share"><a href="https://api.whatsapp.com/send?text={text}" style="{SHARE_STYLE}">'
            f"{WA_ICON}WhatsApp वर पाठवा</a></p>")


def page(prefix: str, title: str, desc: str, body: str, url: str = "") -> str:
    full = SITE_URL + url
    return BASE.format(prefix=prefix, title=esc(title), desc=esc(desc),
                       body=body, repo=REPO, url=full, beacon=beacon())


def breadcrumb(prefix: str, crumbs):
    parts = ['<nav class="crumbs" aria-label="मार्ग">']
    parts.append(f'<a href="{prefix}index.html">मुख्य</a>')
    for label, href in crumbs:
        parts.append(" › ")
        parts.append(f'<a href="{href}">{esc(label)}</a>' if href else esc(label))
    parts.append("</nav>")
    return "".join(parts)


def report_link(work):
    title = f"चूक नोंदवा: {work['title']}"
    body = (f"कृती: {work['title']}%0A"
            f"स्रोत: {REPO}/blob/main/content/{work['slug']}/{work['slug']}.md")
    return (f"{ISSUES}?title={html.escape(title, quote=True)}"
            f"&amp;body={body}")


def size_line(work, loaded):
    n = len(loaded["verses"])
    r = len(loaded["refrains"])
    u = len(loaded["unnumbered"])
    s = f"{dev_num(n)} {work['unit']}"
    if r:
        s += " + ध्रुवपद"
    if u:
        s += f" + {dev_num(u)} अंकित नसलेले भाग"
    return s


def work_card(prefix: str, work, loaded) -> str:
    status = ""
    if work.get("status") == "partial":
        status = " · अर्धवट — काही भाग वगळलेले"
    return (
        f'<article class="card">\n'
        f'  <h3><a href="{prefix}aarti/{work["slug"]}/index.html">{esc(work["title"])}</a></h3>\n'
        f'  <p>{esc(work["deity"])} · {esc(work["author"])} · {esc(work["lang"])}</p>\n'
        f'  <p>{esc(work["blurb"])}</p>\n'
        f'  <p class="lic">{esc(size_line(work, loaded))} · {esc(loaded["licence"])} · '
        f'{esc(loaded["proofing"])}{status}</p>\n'
        f'</article>'
    )


def block_html(work, item, prefix: str) -> str:
    kind = item["kind"]
    if kind == "heading":
        return f'<h2 class="vsection">{esc(item["text"])}</h2>'
    if kind == "invocation":
        return f'<p class="invocation">{esc(item["text"])}</p>'
    if kind == "note":
        txt = esc(item["text"])
        txt = txt.replace(
            "SOURCE.md",
            f'<a href="{prefix}srot/{work["slug"]}/index.html">स्रोत पान</a>')
        return f'<p class="note">{txt}</p>'
    lines = "<br>\n".join(esc(ln) for ln in item["lines"])
    copy_text = "\n".join(item["lines"])
    anchor = f"v-{item['seq']}"
    cls = "verse refrain" if kind == "refrain" else "verse"
    if kind == "verse":
        marker = f"॥ {dev_num(item['number'])} ॥"
        label = f"{work['unit']} {dev_num(item['number'])}"
    elif kind == "refrain":
        marker = "ध्रुवपद"
        label = "ध्रुवपद"
    else:
        marker = ""
        label = "भाग"
    meta = (f'<div class="vmeta">'
            + (f'<span class="vnum">{esc(marker)}</span>\n  ' if marker else "")
            + f'<a class="vlink" href="#{anchor}" title="या भागाची जोडणी">¶</a>\n  '
            + f'<button class="copy" type="button" data-copy="{esc(copy_text)}" '
            + f'title="हा {label} नक्कल करा">नक्कल</button></div>')
    return (f'<article class="{cls}" id="{anchor}">\n'
            f'  <p class="vlines">{lines}</p>\n  {meta}\n</article>')


def reading_page(work, loaded, prev_work, next_work) -> str:
    prefix = "../../"
    items = loaded["items"]
    head = [breadcrumb(prefix, [(work["deity"], f"{prefix}devata/{work['deity_slug']}/index.html"),
                                (work["title"], None)]),
            f"<h1>{esc(work['title'])}</h1>",
            f"<p class=\"byline\">{esc(work['deity'])} · {esc(work['author'])} · "
            f"{esc(work['lang'])} · {esc(size_line(work, loaded))}</p>",
            f"<p class=\"source-line\">परवाना: {esc(loaded['licence'])} · "
            f"{esc(loaded['proofing'])} · "
            f"<a href=\"{prefix}srot/{work['slug']}/index.html\">स्रोत, लेखक व तपासणी पहा</a></p>",
            share_link(work["title"], SITE_URL + f"aarti/{work['slug']}/")]
    for d in loaded["disclaimers"]:
        head.append(f'<section class="disclaimer" aria-label="टीप"><h2>टीप</h2><p>{esc(d)}</p></section>')
    if work.get("status_note"):
        head.append(f'<section class="status-note" aria-label="स्थिती"><p>{esc(work["status_note"])}</p></section>')
    if work.get("flag"):
        head.append(f'<section class="status-note" aria-label="नोंद"><p>{esc(work["flag"])}</p></section>')
    nav = []
    if prev_work:
        nav.append(f'<a class="prev" href="{prefix}aarti/{prev_work["slug"]}/index.html">← {esc(prev_work["short"])}</a>')
    if next_work:
        nav.append(f'<a class="next" href="{prefix}aarti/{next_work["slug"]}/index.html">{esc(next_work["short"])} →</a>')
    if nav:
        head.append(f'<nav class="prevnext" aria-label="मागील / पुढील आरती">{" ".join(nav)}</nav>')
    body = ["\n".join(head)]
    body += [block_html(work, it, prefix) for it in items]
    if nav:
        body.append(f'<nav class="prevnext" aria-label="मागील / पुढील आरती">{" ".join(nav)}</nav>')
    body.append('<section class="sourcebox" aria-label="स्रोत">'
                '<h2>स्रोत</h2>'
                f'<p>परवाना: {esc(loaded["licence"])}. {esc(loaded["proofing"])}. '
                f'लेखक, आवृत्ती, तुलना व तपासणीची स्थिती — '
                f'<a href="{prefix}srot/{work["slug"]}/index.html">स्रोत पान पहा</a>.</p>'
                f'<p><a href="{report_link(work)}">चूक नोंदवा ↗</a> — '
                'GitHub वर issue उघडेल; कृतीचे नाव आधीच भरलेले असेल.</p>'
                '</section>')
    return page(prefix, work["title"],
                f"{work['title']} — {work['deity']}, {work['author']}. {size_line(work, loaded)}.",
                "\n".join(body), url=f"aarti/{work['slug']}/index.html")


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------- main

def main() -> int:
    loaded = {}
    for work in WORKS:
        if not (CONTENT / work["slug"] / f'{work["slug"]}.md').exists():
            print(f"MISSING TEXT: {work['slug']}", file=sys.stderr)
            return 1
        if not (CONTENT / work["slug"] / "SOURCE.md").exists():
            print(f"MISSING SOURCE: {work['slug']}", file=sys.stderr)
            return 1
        loaded[work["slug"]] = load_work(work)
        ld = loaded[work["slug"]]
        print(f"{work['slug']}: {len(ld['verses'])} numbered + "
              f"{len(ld['refrains'])} refrain + "
              f"{len(ld['unnumbered'])} unnumbered = "
              f"{len(ld['verses']) + len(ld['refrains']) + len(ld['unnumbered'])} blocks; "
              f"disclaimers={len(ld['disclaimers'])}; {ld['licence']}")

    # ---- assets + fonts (copied fresh on every build)
    (SITE / "assets").mkdir(parents=True, exist_ok=True)
    (SITE / "fonts").mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "reader.js", "search.js"):
        shutil.copy2(TOOLS / "assets" / name, SITE / "assets" / name)
    for f in (TOOLS / "fonts").glob("*"):
        shutil.copy2(f, SITE / "fonts" / f.name)
    shutil.copy2(Path(__file__).resolve(), SITE / "build.py")
    write(SITE / ".nojekyll", "")

    # ---- reading pages (prev/next in sung order)
    for i, work in enumerate(WORKS):
        prev_w = WORKS[i - 1] if i > 0 else None
        next_w = WORKS[i + 1] if i + 1 < len(WORKS) else None
        write(SITE / "aarti" / work["slug"] / "index.html",
              reading_page(work, loaded[work["slug"]], prev_w, next_w))

    # ---- source pages
    for work in WORKS:
        ld = loaded[work["slug"]]
        body = (breadcrumb("../../", [(work["title"], f"../../aarti/{work['slug']}/index.html"),
                                      ("स्रोत", None)])
                + f"<h1>स्रोत — {esc(work['title'])}</h1>"
                + f"<p class=\"byline\">{esc(work['deity'])} · {esc(work['author'])} · "
                + f"{esc(work['lang'])} · परवाना: {esc(ld['licence'])} · {esc(ld['proofing'])}</p>"
                + f"<p><a href=\"../../aarti/{work['slug']}/index.html\">← {esc(work['title'])} वाचा</a></p>"
                + render_source_md(work["slug"]))
        write(SITE / "srot" / work["slug"] / "index.html",
              page("../../", f"स्रोत — {work['title']}",
                   f"{work['title']} — लेखक, स्रोत, परवाना व तपासणी.", body,
                   url=f"srot/{work['slug']}/index.html"))

    # ---- deity index + deity pages
    chips = "".join(
        f'<a href="devata/{slug}/index.html">{name}</a>'
        for slug, name, _ in DEITIES)
    cards = "\n".join(
        f'<div class="grouplist"><h3><a href="{slug}/index.html">{name}</a></h3>'
        f'<p>{blurb}</p><ul class="worklist">'
        + "".join(
            f'<li><a href="../aarti/{w["slug"]}/index.html">{esc(w["title"])}</a> '
            f'<span class="meta">— {esc(w["author"])} · {esc(size_line(w, loaded[w["slug"]]))}</span></li>'
            for w in WORKS if w["deity_slug"] == slug)
        + "</ul></div>"
        for slug, name, blurb in DEITIES)
    write(SITE / "devata" / "index.html", page(
        "../", "देवता", "आरत्या देवतेनुसार शोधा.",
        breadcrumb("../", [("देवता", None)])
        + "<h1>देवतेनुसार शोधा</h1>"
        + "<p>ज्या देवतेची आरती गायची आहे, ती निवडा. प्रत्येक देवतेच्या पानावर "
        + "तिच्या आरत्या गायल्या जाणाऱ्या क्रमाने दिल्या आहेत.</p>"
        + cards,
        url="devata/index.html"))
    for slug, name, blurb in DEITIES:
        ws = [w for w in WORKS if w["deity_slug"] == slug]
        lis = "".join(
            f'<li><a href="../../aarti/{w["slug"]}/index.html">{esc(w["title"])}</a> '
            f'<span class="meta">— {esc(w["author"])} · {esc(w["lang"])} · '
            f'{esc(size_line(w, loaded[w["slug"]]))} · {esc(loaded[w["slug"]]["proofing"])}</span></li>'
            for w in ws)
        write(SITE / "devata" / slug / "index.html", page(
            "../../", name, f"{name} — आरत्या.",
            breadcrumb("../../", [("देवता", "../index.html"), (name, None)])
            + f"<h1>{esc(name)}</h1><p>{esc(blurb)}</p>"
            + f'<ul class="worklist">{lis}</ul>'
            + '<p><a href="../index.html">← सर्व देवता</a></p>',
            url=f"devata/{slug}/index.html"))

    # ---- occasion index + pages
    write(SITE / "prasang" / "index.html", page(
        "../", "प्रसंग", "आरत्या प्रसंगानुसार शोधा.",
        breadcrumb("../", [("प्रसंग", None)])
        + "<h1>प्रसंगानुसार शोधा</h1>"
        + "<p>वेळ किंवा प्रसंग निवडा — पहाट, दुपार, संध्याकाळ, उत्सव, किंवा आरतीनंतरचे समापन.</p>"
        + "".join(
            f'<div class="grouplist"><h3><a href="{slug}/index.html">{name}</a></h3>'
            f'<p>{blurb}</p></div>'
            for slug, name, blurb in OCCASIONS),
        url="prasang/index.html"))
    for slug, name, blurb in OCCASIONS:
        ws = [w for w in WORKS if slug in w["occasions"]]
        lis = "".join(
            f'<li><a href="../../aarti/{w["slug"]}/index.html">{esc(w["title"])}</a> '
            f'<span class="meta">— {esc(w["deity"])} · {esc(w["author"])} · '
            f'{esc(size_line(w, loaded[w["slug"]]))}</span></li>'
            for w in ws)
        write(SITE / "prasang" / slug / "index.html", page(
            "../../", name, f"{name} — आरत्या.",
            breadcrumb("../../", [("प्रसंग", "../index.html"), (name, None)])
            + f"<h1>{esc(name)}</h1><p>{esc(blurb)}</p>"
            + f'<ul class="worklist">{lis}</ul>'
            + '<p><a href="../index.html">← सर्व प्रसंग</a></p>',
            url=f"prasang/{slug}/index.html"))

    # ---- saint index + pages
    write(SITE / "sant" / "index.html", page(
        "../", "संत", "आरत्या संत / कर्त्यानुसार शोधा.",
        breadcrumb("../", [("संत", None)])
        + "<h1>संत व कर्त्यानुसार शोधा</h1>"
        + "<p>ज्या संताने आरती रचली, त्यानुसार शोधा. जिथे कर्तृत्व वादग्रस्त आहे, "
        + "तिथे दोन्ही परंपरा नोंदवल्या आहेत आणि आरती दोन्ही संतांच्या पानांवर येते.</p>"
        + "".join(
            f'<div class="grouplist"><h3><a href="{slug}/index.html">{name}</a> '
            f'<span class="meta">({dates})</span></h3></div>'
            for slug, name, dates in SAINTS),
        url="sant/index.html"))
    for slug, name, dates in SAINTS:
        ws = [w for w in WORKS if slug in w["saints"]]
        if not ws:
            continue
        lis = "".join(
            f'<li><a href="../../aarti/{w["slug"]}/index.html">{esc(w["title"])}</a> '
            f'<span class="meta">— {esc(w["deity"])} · {esc(size_line(w, loaded[w["slug"]]))}</span></li>'
            for w in ws)
        write(SITE / "sant" / slug / "index.html", page(
            "../../", name, f"{name} — आरत्या.",
            breadcrumb("../../", [("संत", "../index.html"), (name, None)])
            + f"<h1>{esc(name)}</h1><p>{esc(dates)}</p>"
            + f'<ul class="worklist">{lis}</ul>'
            + '<p><a href="../index.html">← सर्व संत</a></p>',
            url=f"sant/{slug}/index.html"))

    # ---- Hindi section
    hindi = [w for w in WORKS if w["lang"].startswith("हिंदी") or w["cat"] == "hindi"]
    cards = "\n".join(work_card("../", w, loaded[w["slug"]]) for w in hindi)
    write(SITE / "hindi" / "index.html", page(
        "../", "हिंदी आरत्या", "हिंदी भाषेतील आरत्या.",
        breadcrumb("../", [("हिंदी", None)])
        + "<h1>हिंदी आरत्या</h1>"
        + "<p>या आरत्या हिंदी भाषेत आहेत, मराठीत नाहीत — त्या तशा प्रत्येक पानावर "
        + "नोंदवल्या आहेत. महाराष्ट्रात त्या मराठी आरत्यांबरोबरच गायल्या जातात.</p>"
        + f'<div class="cards">{cards}</div>',
        url="hindi/index.html"))

    # ---- home
    weekday = datetime.date.today().weekday()
    today_slug, today_day = TODAY_ROTATION[weekday]
    today = next(w for w in WORKS if w["slug"] == today_slug)
    today_ld = loaded[today_slug]
    preview_items = [it for it in today_ld["items"]
                   if it["kind"] in ("refrain", "verse")][:2]
    preview = "".join(block_html(today, it, "") for it in preview_items)
    all_cards = "\n".join(work_card("", w, loaded[w["slug"]]) for w in WORKS)
    home_body = (
        '<section class="hero">'
        '<h1>॥ आरती संग्रह ॥</h1>'
        '<p class="tagline">मराठी व हिंदी आरत्या — मूळ मजकूर, स्रोतासह, मोफत.</p>'
        '<div class="actions">'
        f'<a class="big" href="aarti/{today_slug}/index.html">आजची आरती वाचा →</a>'
        '<a class="big ghost" href="devata/index.html">देवतेनुसार शोधा</a>'
        '</div></section>'
        '<section class="todaybox"><h2>आजची आरती — '
        f'{esc(today_day)}</h2>'
        f'<p><a href="aarti/{today_slug}/index.html">{esc(today["title"])}</a> '
        f'— {esc(today["deity"])} · {esc(today["author"])} · '
        f'{esc(size_line(today, today_ld))}. पूर्ण आरती '
        f'<a href="aarti/{today_slug}/index.html">येथे वाचा</a>.</p>'
        f'{preview}</section>'
        '<section><h2>देवतेनुसार शोधा</h2>'
        f'<div class="chips">{chips}</div></section>'
        '<section><h2>सर्व आरत्या</h2>'
        f'<div class="cards">{all_cards}</div></section>'
        '<section><h2>हे संकेतस्थळ कसे आहे</h2>'
        '<p>आरती संग्रह ही मराठी व हिंदी आरत्यांची मुक्त वाचनालय आहे. '
        'प्रत्येक आरतीचा मजकूर युनिकोड देवनागरीत, स्रोत व परवान्यासह दिला आहे. '
        'अर्थ, भाष्य, भाषांतर किंवा कृत्रिम अर्थ येथे नाहीत. मजकूर तपासणीच्या प्रतीक्षेत आहे — '
        'चूक आढळल्यास प्रत्येक पानावरची “चूक नोंदवा” जोडणी वापरा.</p>'
        '<p>जाहिराती नाहीत, खाते लागत नाही. भेटींची मोजणी कुकीशिवाय व निनावी — फक्त आमच्या माहितीसाठी.</p></section>')
    write(SITE / "index.html", page(
        "", "मुख्य", "आरती संग्रह — मराठी व हिंदी आरत्या, मूळ मजकूर, मोफत.",
        home_body, url=""))

    # ---- about / vishayi
    write(SITE / "vishayi" / "index.html", page(
        "../", "विषयी", "आरती संग्रह कशासाठी, स्रोत व परवाने.",
        breadcrumb("../", [("विषयी", None)])
        + "<h1>विषयी</h1>"
        + "<h2>आरती संग्रह कशासाठी</h2>"
        "<p>मराठी व हिंदी आरत्या — गणपती, शिव, देवी, विठ्ठल, दत्त, हनुमान, कृष्ण, "
        "साईबाबा आणि आरतीनंतरच्या प्रार्थना — देवनागरीत, मोफत वाचता याव्यात, "
        "हे या संकेतस्थळाचे काम आहे. मजकूर हा संकेतस्थळाचा गाभा आहे; वाचनासाठी लागणारी "
        "सोय — मोठी अक्षरे, अंधारी पद्धत, छपाई — त्याभोवती बांधली आहे.</p>"
        "<h2>स्रोत</h2>"
        "<p>प्रत्येक आरतीचा मजकूर दोन स्वतंत्र छापील किंवा टंकलिखित स्रोतांची तुलना "
        "करून तयार केला आहे. जिथे विकिस्रोतावर टंकलिखित मजकूर उपलब्ध होता (भूपाळी, "
        "गवळण, गणपत्यथर्वशीर्ष), तिथे तो CC BY-SA 4.0 परवान्याखाली, विकिस्रोताचा "
        "उल्लेखासह वापरला आहे. प्रत्येक कृतीच्या स्रोत पानावर लेखक, मृत्यूवर्ष, "
        "तुलना केलेले पाठभेद, परवाना व तपासणीची स्थिती दिलेली आहे.</p>"
        "<h2>परवाने</h2>"
        "<ul><li>संकेतस्थळाचा कोड (हा जनरेटर): Apache License 2.0.</li>"
        "<li>आरत्यांचा मूळ मजकूर: सार्वजनिक क्षेत्र (Public Domain) — मध्ययुगीन संत, "
        "पारंपरिक रचना, किंवा १९६५ पूर्वी निधन झालेले लेखक.</li>"
        "<li>विकिस्रोतावरील टंकलेखन: CC BY-SA 4.0, विकिस्रोताचा उल्लेखासह — मूळ कृती "
        "सार्वजनिक क्षेत्रात असली तरी टंकलेखनावर विकिमीडियाचा परवाना लागू होतो.</li></ul>"
        "<h2>येथे काय नाही — आणि का</h2>"
        "<ul>"
        "<li><b>परवानगी नसलेल्या स्रोतांतील मजकूर येथे घेतलेला नाही.</b> ज्या स्रोतांकडून "
        "पुनर्प्रकाशनाची परवानगी नाही, त्यांचा मजकूर येथे घेतलेला नाही. आरती संग्रह "
        "फक्त सार्वजनिक क्षेत्रातील स्रोतांतूनच बांधला आहे.</li>"
        "<li><b>आधुनिक अर्थ, भाष्य, भाषांतरे व चित्रपट आवृत्त्या नाहीत.</b> ती बहुधा "
        "अजूनही हक्काधीन असतात; फक्त मूळ मजकूर येथे आहे.</li>"
        "<li><b>तुकडोजी महाराजांचे साहित्य नाही</b> — ते १ जानेवारी २०२९ पर्यंत "
        "सार्वजनिक क्षेत्रात येत नाही.</li>"
        "<li><b>साईबाबांच्या आरत्यांतील काही भाग वगळले आहेत.</b> नमस्काराष्टक "
        "(मोहनी राज) व काकड आरतीतील दोन भाग — यांचे लेखक किंवा मृत्यूवर्ष पडताळता "
        "आले नाही, म्हणून ते सार्वजनिक क्षेत्र म्हणून देता आले नाहीत. ते वगळलेले "
        "आहेत, लपवलेले नाहीत — प्रत्येक स्रोत पानावर त्यांची नोंद आहे.</li>"
        "<li><b>एका अधिकाराचा दावा नाही.</b> जिथे दोन परंपरा आहेत — कर्तृत्व वादग्रस्त "
        "असेल, किंवा एका आरतीचे दोन पाठ चालत असतील — तिथे दोन्हींची नोंद आहे. "
        "आम्ही कोणत्याही एका अधिकारासाठी भांडत नाही.</li>"
        "</ul>"
        "<h2>तपासणी</h2>"
        "<p>सध्या सर्व कृती “तपासणी बाकी” आहेत. दोन स्रोतांची तुलना झाली आहे, पण "
        "मराठी तपासणीसाने (proofreader) अद्याप एकही फाइल तपासलेली नाही. "
        "चूक आढळल्यास प्रत्येक वाचन पानावरची “चूक नोंदवा” जोडणी वापरा.</p>"
        "<h2>हे कसे बांधले आहे</h2>"
        "<p>मजकूर <code>content/</code> मधील साध्या मजकूर फाइल्समध्ये आहे. "
        "<code>tools/build.py</code> ही साधी Python स्क्रिप्ट त्यांतून साधी HTML पाने तयार करते — "
        "कोणतेही फ्रेमवर्क नाही, वाचण्यासाठी JavaScript लागत नाही, अक्षरे संकेतस्थळावरच "
        "ठेवलेली आहेत (Tiro Devanagari Marathi व Mukta, SIL Open Font License).</p>",
        url="vishayi/index.html"))

    # ---- search index + search page
    index = []
    for work in WORKS:
        ld = loaded[work["slug"]]
        index.append({"work": work["slug"], "title": work["title"],
                      "deity": work["deity"], "author": work["author"],
                      "n": "",
                      "text": f"{work['title']} {work['deity']} {work['author']} {work['lang']}",
                      "url": f"aarti/{work['slug']}/index.html"})
        for item in ld["items"]:
            if item["kind"] not in ("verse", "refrain", "unnumbered"):
                continue
            n = (dev_num(item["number"]) if item["kind"] == "verse"
                 else "ध्रुवपद" if item["kind"] == "refrain" else "")
            index.append({"work": work["slug"], "title": work["title"],
                          "deity": work["deity"], "author": work["author"],
                          "n": n, "text": " ".join(item["lines"]),
                          "url": f"aarti/{work['slug']}/index.html#v-{item['seq']}"})
    write(SITE / "search-index.json", json.dumps(index, ensure_ascii=False))
    write(SITE / "shodh" / "index.html", page(
        "../", "शोध", "आरती संग्रहात शोधा.",
        breadcrumb("../", [("शोध", None)])
        + "<h1>शोध</h1>"
        + "<p>आरती, देवता, संत किंवा मजकुरातील शब्द शोधा. शोध तुमच्या ब्राउझरमध्येच चालतो; "
        "काहीही सर्व्हरला पाठवले जात नाही. (शोधासाठी JavaScript लागते; ते बंद असेल तरी "
        "देवता व प्रसंग यांच्या जोडण्यांनी वाचता येते.)</p>"
        + '<p><label>शोधा: <input id="q" type="search" placeholder="उदा. गणेश, रामदास, सुखकर्ता" size="30"></label></p>'
        + '<div id="results"><p>शोध सुरू करण्यासाठी वर शब्द लिहा.</p></div>'
        + '<script src="../assets/search.js" defer></script>',
        url="shodh/index.html"))
    # ---- sitemap + robots (reach: let search engines find every page)
    urls = []
    for p in sorted(SITE.rglob("*.html")):
        rel = p.relative_to(SITE).as_posix()
        if rel == "index.html":
            loc = SITE_URL
        elif rel.endswith("/index.html"):
            loc = SITE_URL + rel[:-len("index.html")]
        else:
            loc = SITE_URL + rel
        urls.append(loc)
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls)
               + "</urlset>\n")
    write(SITE / "sitemap.xml", sitemap)
    write(SITE / "robots.txt",
          f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n")
    print(f"sitemap: {len(urls)} urls")
    print(f"search index: {len(index)} entries, {len(WORKS)} works")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
