"""Generates data/corpus.jsonl and data/answer_key.jsonl together from data/world.md's
persona, so the answer key is a by-product of authoring the corpus rather than a
guess made after the fact.

Usage: python generate_corpus.py [--seed N] [--n N]
"""
import argparse
import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED_DEFAULT = 20260114
N_DEFAULT = 500

OUT_DIR = Path(__file__).parent
WEEK_START = datetime(2026, 1, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))

# --------------------------------------------------------------------------
# World roster (mirrors world.md exactly)
# --------------------------------------------------------------------------

PEOPLE = [
    "Kavya Iyer", "Rohit Sharma", "Arun Kumar", "Meera Pillai", "Priya Nair",
    "Sanjay Reddy", "Divya Krishnan", "Vikram Nath", "Ramesh",
]
FIRST_NAMES = ["Kavya", "Rohit", "Arun", "Meera", "Priya", "Sanjay", "Divya", "Vikram", "Ramesh"]
PROJECTS = ["Tara", "Kavach", "Merchant Onboarding Revamp"]
VENDORS = ["Veriscan", "CloudEdge", "TestForge"]
APP_CONTEXTS = ["Slack", "Gmail", "Notes", "Notion", "Google Docs", "Calendar"]
WINDOW_TITLES = {
    "Slack": ["#tara-build", "#kavach-eng", "#general", "#onboarding-revamp", "#vendor-veriscan", "#random"],
    "Gmail": ["Re: Veriscan renewal terms", "CloudEdge — rate limit escalation", "Tara launch checklist",
              "Kavach rollout plan", "Weekly sync notes"],
    "Notes": ["Untitled note", "Today", "Standup notes", "Scratchpad"],
    "Notion": ["Tara — Engineering", "Kavach — Product Spec", "Team Wiki"],
    "Google Docs": ["Tara Launch Plan", "Q1 Retro", "Onboarding Revamp — Draft"],
    "Calendar": ["Standup", "1:1 with Vikram", "Tara sync", "Kavach review"],
}

# ASR-plausible mangled variants of proper nouns. Applied to Latin-script text only.
PHONETIC_VARIANTS = {
    "Kavya": ["kavia", "kaviya", "kavya"],
    "Rohit": ["rohit", "rohith", "rohiit"],
    "Arun": ["arun", "aroon", "arunn"],
    "Meera": ["mira", "meera", "myra"],
    "Priya": ["preeya", "pria", "priyaa"],
    "Sanjay": ["sanjai", "sanjay", "sanjey"],
    "Divya": ["divia", "divya", "diviya"],
    "Vikram": ["vikram", "vicram", "vikaram"],
    "Ramesh": ["ramesh", "rameysh"],
    "Tara": ["tara", "tarah", "taara"],
    "Kavach": ["kavach", "kavaach", "kawach"],
    "Veriscan": ["veri scan", "veriscaan", "verry scan"],
    "CloudEdge": ["cloud edge", "claud edge", "cloudedge"],
    "TestForge": ["test forge", "testforj"],
    "PayBridge": ["pay bridge", "paybrij"],
    "Finloop": ["fin loop", "finlup"],
}

FILLER_WORDS_DROPPABLE = {
    "is", "the", "a", "an", "to", "of", "that", "this", "for", "and", "will",
    "be", "been", "it", "its", "on", "in", "at", "with", "have", "has",
}


def mangle_proper_nouns(text: str, rng: random.Random) -> str:
    for canonical, variants in PHONETIC_VARIANTS.items():
        if canonical in text and rng.random() < 0.7:
            text = text.replace(canonical, rng.choice(variants))
    return text


def degrade(formatted_output: str, language: list[str], rng: random.Random) -> str:
    """raw_asr: meaningfully worse than formatted_output — no punctuation, dropped
    short function words, mangled proper nouns. Devanagari text gets lighter
    treatment since the phonetic table is Latin-script only."""
    text = formatted_output
    if "hi-deva" not in language:
        text = mangle_proper_nouns(text, rng)
    text = text.lower() if re.search(r"[a-zA-Z]", text) else text
    text = re.sub(r"[.,!?;:]", "", text)
    words = text.split()
    out = [w for w in words if not (w in FILLER_WORDS_DROPPABLE and rng.random() < 0.35)]
    return " ".join(out)


def pick_app(rng: random.Random) -> tuple[str, str]:
    app = rng.choice(APP_CONTEXTS)
    return app, rng.choice(WINDOW_TITLES[app])


def pick_duration(rng: random.Random, n_words: int) -> int:
    # ~150-180 wpm dictation pace, plus jitter
    return int(n_words / rng.uniform(2.2, 3.0) * 1000) + rng.randint(-300, 800)


# --------------------------------------------------------------------------
# Record container
# --------------------------------------------------------------------------

@dataclass
class Rec:
    week: int
    day_offset: int  # 0-6 within the week
    formatted_output: str
    language: list[str]
    tags: list[str] = field(default_factory=list)  # e.g. "fact:kavach", "contradiction:tara_date:2"
    style_id: str = "notes_terse"
    app_context: str | None = None
    window_title: str | None = None


# --------------------------------------------------------------------------
# 1. Hand-authored narrative records — distributed facts, contradictions, handover
# --------------------------------------------------------------------------

def narrative_records() -> list[Rec]:
    recs = []

    # --- Distributed fact A: Kavach owner + date (3 records, no overlap) ---
    recs.append(Rec(1, 2, "Kicking off Kavach scoping this week, I'm driving it for now alongside Tara.",
                     ["en"], ["fact:kavach:owner_kavya"]))
    recs.append(Rec(8, 1, "Handing Kavach off to Sanjay Reddy, my bandwidth is going entirely to the Tara relaunch.",
                     ["en"], ["fact:kavach:owner_sanjay"]))
    recs.append(Rec(10, 3, "Sanjay confirmed the Kavach rollout date, it's March 15.",
                     ["en"], ["fact:kavach:date"]))

    # --- Distributed fact B: Veriscan renewal cost (also contradiction 3) ---
    recs.append(Rec(2, 1, "Veriscan sent the renewal notice, they want a 12 percent cost increase this cycle.",
                     ["en"], ["fact:veriscan:notice", "contradiction:veriscan_cost:1"]))
    recs.append(Rec(6, 2, "Divya kicked off negotiation with Veriscan, target is anything below the 12 percent they asked for.",
                     ["en"], ["fact:veriscan:negotiating"]))
    recs.append(Rec(9, 0, "Veriscan's new terms are signed, 8 percent increase, not the 12 they originally wanted.",
                     ["en"], ["fact:veriscan:final", "contradiction:veriscan_cost:2"]))

    # --- Distributed fact C: CloudEdge rate limit ---
    recs.append(Rec(4, 2, "Tara load testing keeps hitting the CloudEdge API rate limit, it's blocking us and we don't have a fix yet.",
                     ["en"], ["fact:cloudedge:problem"]))
    recs.append(Rec(6, 4, "kal cloudedge ka rate limit issue Ramesh ko escalate kar diya, unki taraf se koi update nahi aya abhi tak",
                     ["hi", "en"], ["fact:cloudedge:escalated"]))
    recs.append(Rec(9, 1, "Good news, CloudEdge agreed the new rate limit, 500 requests per second, effective immediately.",
                     ["en"], ["fact:cloudedge:resolved"]))

    # --- Distributed fact D: Priya's scope (crosses language boundary) ---
    recs.append(Rec(3, 3, "Confirmed with Vikram, Priya owns the design system going forward.",
                     ["en"], ["fact:priya:design_system"]))
    recs.append(Rec(8, 2, "प्रिया अब brand refresh भी lead कर रही हैं, design system ke saath saath",
                     ["hi-deva", "en"], ["fact:priya:brand_refresh"]))
    recs.append(Rec(8, 5, "Priya's scope has grown enough between the design system and brand refresh that she's asked for a second designer.",
                     ["en"], ["fact:priya:needs_help"]))

    # --- Contradiction 1: Tara launch date, two-hop chain ---
    recs.append(Rec(1, 0, "Tara migration plan is finalised, we're targeting launch on February 20th.",
                     ["en"], ["contradiction:tara_date:1"]))
    recs.append(Rec(7, 1, "Bad news on Tara, a critical bug came up in QA, launch is slipping from Feb 20 to March 10.",
                     ["en"], ["contradiction:tara_date:2"]))
    recs.append(Rec(10, 0, "Tara is slipping again, partner integration delay pushed launch from March 10 to March 24.",
                     ["en"], ["contradiction:tara_date:3"]))

    # --- Contradiction 2: Merchant Onboarding Revamp owner ---
    recs.append(Rec(1, 4, "Arun owns the Merchant Onboarding Revamp alongside his Tara work for now.",
                     ["en"], ["contradiction:onboarding_owner:1"]))
    recs.append(Rec(7, 3, "Since Arun rotated out, Divya is picking up the Merchant Onboarding Revamp as a side initiative.",
                     ["en"], ["contradiction:onboarding_owner:2"]))

    # --- Handover: Arun -> Meera on Tara backend ---
    recs.append(Rec(5, 0, "Arun mentioned he's rotating to the platform team soon, need to start planning the Tara backend handover.",
                     ["en"], ["handover:1"]))
    recs.append(Rec(5, 3, "Meera's joining Tara backend, Arun's going to ramp her up on his open tickets this week.",
                     ["en"], ["handover:2"]))
    recs.append(Rec(6, 1, "arun apne open items document kar raha hai, meera unhe directly pick up karna shuru kar chuki hai",
                     ["hi", "en"], ["handover:3"]))
    recs.append(Rec(6, 5, "Rohit says the handover from Arun to Meera is going smoothly, no gaps so far on Tara backend.",
                     ["en"], ["handover:4"]))
    recs.append(Rec(7, 2, "Meera is now the sole owner of Tara's backend, handover from Arun is complete.",
                     ["en"], ["handover:5"]))

    # --- A few extra standalone durable facts anchored to the world (not templated) ---
    recs.append(Rec(1, 1, "We're using TestForge for the Tara QA cycle this quarter.",
                     ["en"], ["durable:vendor"]))
    recs.append(Rec(3, 0, "Decided to go with a phased rollout for Tara instead of a big bang launch, lower risk given the scale.",
                     ["en"], ["durable:decision"]))
    recs.append(Rec(2, 4, "I prefer status updates as bullet points, not paragraphs, easier to scan before standup.",
                     ["en"], ["durable:preference"]))
    recs.append(Rec(11, 2, "Meera signed off on Tara QA, we're clear for the March 24 launch.",
                     ["en"], ["durable:decision"]))
    recs.append(Rec(11, 4, "Kavach launched on schedule today, March 15, first cohort of merchants live.",
                     ["en"], ["durable:decision"]))
    recs.append(Rec(12, 1, "Tara is live as of today, March 24. Retro is scheduled for Friday.",
                     ["en"], ["durable:decision"]))

    return recs


# --------------------------------------------------------------------------
# 2. Hand-authored excluded-category records (must be rejected by the policy filter)
# --------------------------------------------------------------------------

def excluded_records() -> list[Rec]:
    health_emotional_personal = [
        "God I'm so exhausted, barely slept, this week has been brutal.",
        "Need to remember to take my thyroid medication before the meeting, keep forgetting.",
        "Feeling really anxious about the board review tomorrow, hope I don't freeze up.",
        "Call mom back, she's been trying to reach me about dad's checkup results.",
        "My back has been killing me since Monday, need to book a physio appointment.",
        "Honestly so burnt out right now, need a proper vacation after this launch.",
        "Skipped lunch again, should really stop doing that, feeling lightheaded.",
        "Had a huge fight with Rohan last night, can't stop thinking about it during standup.",
        "Doctor said the blood pressure reading was borderline, need to follow up next month.",
        "Feeling really proud of how the team pulled together, but also just so tired.",
        "Anniversary is next week and I haven't planned anything, need to sort that out.",
        "Therapy session helped today, need to keep practicing the breathing thing she suggested.",
        "Can't shake this headache, third day in a row, maybe it's the new glasses.",
        "Really missing home today, Bengaluru traffic is wearing me down mentally.",
        "Nervous about the parent-teacher meeting for Aarav tomorrow, hope his grades are okay.",
    ]
    opinion_financial = [
        "Rohit is honestly so difficult to work with sometimes, never listens in meetings.",
        "Sanjay just takes credit for everything, it's exhausting.",
        "Need to transfer money to the landlord before the 5th, rent is due.",
        "Priya can be a bit condescending in reviews honestly.",
        "Thinking about switching banks, this one's fees are ridiculous.",
    ]
    recs = []
    all_lines = [(t, "health_emotional_personal") for t in health_emotional_personal] + \
                [(t, "opinion_financial") for t in opinion_financial]
    for i, (text, subcategory) in enumerate(all_lines):
        week = 1 + (i * 11) % 12  # spread across all 12 weeks
        recs.append(Rec(week, i % 7, text, ["en"], [f"excluded:{subcategory}"]))
    return recs


# --------------------------------------------------------------------------
# 3. Hand-authored ambiguous-referent records (extractor should drop, not guess)
# --------------------------------------------------------------------------

def ambiguous_records() -> list[Rec]:
    lines = [
        "Tell him the deck needs another pass before Thursday.",
        "She said it's fine to push it by a week.",
        "Send them the updated numbers once it's confirmed.",
        "He's still waiting on the sign-off from the other side.",
        "Loop them in before the call tomorrow.",
        "Check with her about the timeline change.",
        "That vendor thing needs to close out this week.",
        "The other team is still blocking us on that.",
        "He'll send it once it's ready, don't chase again.",
        "Just flag it to them and move on.",
        "She's not happy about the delay, need to manage that.",
        "That number needs to be double checked before we share it.",
    ]
    recs = []
    for i, text in enumerate(lines):
        week = 1 + (i * 5) % 12
        recs.append(Rec(week, i % 7, text, ["en"], ["ambiguous:referent"]))
    return recs


# --------------------------------------------------------------------------
# 4. Organic (templated) distributed-fact and contradiction clusters
# --------------------------------------------------------------------------

DISTRIBUTED_CLUSTER_TOPICS = [
    "the {vendor} invoice", "the {vendor} SLA review", "the {vendor} security questionnaire",
    "the {project} budget approval", "the {project} staffing request", "the {project} risk review",
    "the office wifi vendor ticket", "the {project} analytics dashboard",
]

CLUSTER_TEMPLATES = {
    "en": {
        "opened": "{topic} came up, {person_a} is looking into it, no timeline yet.",
        "progress": "{person_a} looped in {person_b} on {topic}, they're waiting on a response.",
        "resolved": "{topic} is resolved, {person_b} closed it out in {number} days.",
    },
    "hi": {
        "opened": "{topic} ka issue aaya, {person_a} dekh rahe hain, abhi koi timeline nahi hai.",
        "progress": "{person_a} ne {person_b} ko {topic} pe loop kiya, response ka wait hai.",
        "resolved": "{topic} solve ho gaya, {person_b} ne {number} din mein close kar diya.",
    },
    "ta-en": {
        "opened": "{topic} pathi pesanum, {person_a} paakraanga, innum timeline illa.",
        "progress": "{person_a} {person_b} kitta {topic} pathi sonnaanga, reply wait pandranga.",
        "resolved": "{topic} solve aayiduchu, {person_b} {number} naala mudichaanga.",
    },
    "kn-en": {
        "opened": "{topic} bagge maathu ide, {person_a} nodutha idhaare, innu timeline illa.",
        "progress": "{person_a} {person_b} ge {topic} bagge helidru, reply kayuthidhaare.",
        "resolved": "{topic} solve aagide, {person_b} {number} dina alli close madidru.",
    },
    "hi-deva": {
        "opened": "{topic} ka मुद्दा आया, {person_a} देख रहे हैं, अभी कोई timeline नहीं है।",
        "progress": "{person_a} ने {person_b} को {topic} पर loop किया, response का wait है।",
        "resolved": "{topic} solve हो गया, {person_b} ने {number} दिन में close कर दिया।",
    },
}


def cap_first(s: str) -> str:
    """Uppercase only the first character — str.capitalize() would lowercase
    embedded proper nouns like CloudEdge."""
    return s[0].upper() + s[1:] if s else s


def topic_for(topic: str, lang_key: str) -> str:
    """Topics are authored as English noun phrases ("the X review"). The leading
    English article reads naturally inside an English sentence but not stitched
    into Hindi/Tamil/Kannada, so strip it for every non-English template."""
    if lang_key != "en" and topic.startswith("the "):
        return topic[4:]
    return topic


def organic_distributed_clusters(rng: random.Random, n_clusters: int) -> list[Rec]:
    recs = []
    for i in range(n_clusters):
        topic_tmpl = rng.choice(DISTRIBUTED_CLUSTER_TOPICS)
        topic_en = topic_tmpl.format(vendor=rng.choice(VENDORS), project=rng.choice(PROJECTS))
        person_a, person_b = rng.sample(FIRST_NAMES, 2)
        number = rng.choice([2, 3, 5, 7, 10, 14])
        week1 = rng.randint(1, 8)
        fact_id = f"organic_fact_{i}"
        lang_key = weighted_lang(rng)
        tmpl = CLUSTER_TEMPLATES[lang_key]
        topic = topic_for(topic_en, lang_key)

        def render(stage: str) -> str:
            return cap_first(tmpl[stage].format(topic=topic, person_a=person_a, person_b=person_b, number=number))

        recs.append(Rec(week1, rng.randint(0, 6), render("opened"),
                         LANG_TAGS[lang_key], [f"fact:{fact_id}:opened"], style_id=STYLE_BY_LANG[lang_key]))
        recs.append(Rec(min(week1 + rng.randint(1, 2), 12), rng.randint(0, 6), render("progress"),
                         LANG_TAGS[lang_key], [f"fact:{fact_id}:progress"], style_id=STYLE_BY_LANG[lang_key]))
        recs.append(Rec(min(week1 + rng.randint(2, 4), 12), rng.randint(0, 6), render("resolved"),
                         LANG_TAGS[lang_key], [f"fact:{fact_id}:resolved"], style_id=STYLE_BY_LANG[lang_key]))
    return recs


CONTRADICTION_TOPICS = [
    "the {project} design review", "the {project} demo", "the {vendor} kickoff call",
    "the {project} retro", "the quarterly planning offsite", "the {project} sync with leadership",
]

CONTRADICTION_DATE_TEMPLATES = {
    "en": ("{topic} is scheduled for {month} {d1}.",
           "{topic} moved, now it's {month} {d2} instead of {month} {d1}."),
    "hi": ("{topic} {month} {d1} ko schedule hai.",
           "{topic} move ho gaya, ab {month} {d2} hai, {month} {d1} ki jagah."),
    "ta-en": ("{topic} {month} {d1} ku schedule pannirukaanga.",
              "{topic} maari, ippo {month} {d2}, {month} {d1} illa."),
    "kn-en": ("{topic} {month} {d1} ge schedule aagide.",
              "{topic} badalaythu, ivaga {month} {d2}, {month} {d1} alla."),
    "hi-deva": ("{topic} {month} {d1} को schedule है।",
                "{topic} move हो गया, अब {month} {d2} है, {month} {d1} की जगह।"),
}

CONTRADICTION_OWNER_TEMPLATES = {
    "en": ("{person_a} is running {topic}.",
           "{person_b} is taking over {topic} from {person_a}."),
    "hi": ("{topic} abhi {person_a} sambhal rahe hain.",
           "{topic} ab {person_a} se {person_b} sambhal rahe hain."),
    "ta-en": ("{topic} ippo {person_a} paakraanga.",
              "{topic} ippo {person_a} kitta irundhu {person_b} kitta poguthu."),
    "kn-en": ("{topic} ivaga {person_a} nodkoltha idhaare.",
              "{topic} ivaga {person_a} inda {person_b} ge hogthaithe."),
    "hi-deva": ("{topic} अभी {person_a} संभाल रहे हैं।",
                "{topic} अब {person_a} से {person_b} संभाल रहे हैं।"),
}


def organic_contradiction_pairs(rng: random.Random, n_pairs: int) -> list[Rec]:
    recs = []
    for i in range(n_pairs):
        topic_tmpl = rng.choice(CONTRADICTION_TOPICS)
        topic_en = topic_tmpl.format(vendor=rng.choice(VENDORS), project=rng.choice(PROJECTS))
        week1 = rng.randint(1, 6)
        week2 = min(week1 + rng.randint(1, 4), 12)
        contradiction_id = f"organic_contradiction_{i}"
        lang_key = weighted_lang(rng)
        topic = topic_for(topic_en, lang_key)

        if rng.random() < 0.6:
            d1 = rng.randint(1, 25)
            d2 = d1 + rng.randint(3, 14)
            month = rng.choice(["January", "February", "March"])
            t1, t2 = CONTRADICTION_DATE_TEMPLATES[lang_key]
            line1 = cap_first(t1.format(topic=topic, month=month, d1=d1, d2=d2))
            line2 = cap_first(t2.format(topic=topic, month=month, d1=d1, d2=d2))
        else:
            person_a, person_b = rng.sample(FIRST_NAMES, 2)
            t1, t2 = CONTRADICTION_OWNER_TEMPLATES[lang_key]
            line1 = cap_first(t1.format(topic=topic, person_a=person_a, person_b=person_b))
            line2 = cap_first(t2.format(topic=topic, person_a=person_a, person_b=person_b))

        recs.append(Rec(week1, rng.randint(0, 6), line1, LANG_TAGS[lang_key],
                         [f"contradiction:{contradiction_id}:1"], style_id=STYLE_BY_LANG[lang_key]))
        recs.append(Rec(week2, rng.randint(0, 6), line2, LANG_TAGS[lang_key],
                         [f"contradiction:{contradiction_id}:2"], style_id=STYLE_BY_LANG[lang_key]))
    return recs


# --------------------------------------------------------------------------
# 5. Templated filler — durable (standalone) and noise (transient)
# --------------------------------------------------------------------------

DURABLE_TEMPLATES = {
    "en": [
        "{person} now owns {project} on the engineering side.",
        "We're going with {vendor} for the next phase of {project}, decision is final.",
        "Decided {project} will report progress weekly on Fridays going forward.",
        "{person} is the point of contact for anything {vendor}-related now.",
        "New convention: {project} tickets get tagged by owner, not by team.",
        "Locked in {vendor} as the vendor of record for {project} this year.",
        "{person} will lead the {project} demo for leadership next cycle.",
        "Standing decision: {project} changes need sign-off from {person} before merge.",
    ],
    "hi": [
        "{project} ka ownership ab {person} ke paas hai, confirm ho gaya.",
        "{vendor} ke saath hi {project} ka next phase karenge, final decision hai.",
        "{person} ab {vendor} ke liye single point of contact hain.",
        "{project} ke updates ab sirf Friday ko denge, weekly cadence set kar diya.",
    ],
    "ta-en": [
        "{project} ippo {person} dhaan handle pannuvaanga, confirm pannitaanga.",
        "{vendor} kooda continue pannalaam nu decide pannitom {project} kaaga.",
    ],
    "kn-en": [
        "{project} ownership ivaga {person} hatra ide, confirm aagide.",
        "{vendor} jothe mundhe hogona antha {project} ge decide madidivi.",
    ],
    "hi-deva": [
        "{project} ka decision ho gaya, {person} ab isko lead karenge.",
        "{vendor} ke saath hi आगे बढ़ना है, ये final call hai {project} ke liye.",
    ],
}

NOISE_TEMPLATES = {
    "en": [
        "Reminder to grab coffee before the {time} call.",
        "Need to leave by {time} to catch the flight.",
        "Thanks, got it, will do.",
        "Running {mins} minutes late for standup.",
        "Lunch with the team at the {place} today.",
        "Wifi in the {room} is acting up again.",
        "Reminder to book the {room} for tomorrow.",
        "Forgot my {item} at home again, need a spare for the office.",
        "Quick reminder to submit expenses before {day}.",
        "Traffic on the way in was rough today, {mins} minutes for what's usually 15.",
        "Need to restart my laptop, it's been acting slow all morning.",
        "Coffee machine near the {room} is broken again.",
        "Reminder to update my calendar, forgot to block out lunch.",
        "Will call back in {mins} minutes, stepping into another meeting.",
        "Note to self, order more sticky notes for the whiteboard.",
        "Reminder to renew the parking pass before {day}.",
        "Ok noted, thanks for the heads up.",
        "Sounds good, see you at the sync.",
        "Got the invite, will accept once I check my calendar.",
        "Just landed, will call once I'm out of the airport.",
        "Need to book the {room} before {person}'s slot fills up.",
        "{person} pushed the {time} call by fifteen minutes.",
    ],
    "hi": [
        "yaar {time} wali call se pehle coffee lena mat bhoolna.",
        "flight pakadne ke liye {time} nikalna hai.",
        "thanks yaar, ho jayega.",
        "standup ke liye {mins} minute late ho raha hoon.",
        "aaj {place} pe lunch hai team ke saath.",
        "{room} ka wifi phir se kharab hai.",
        "kal ke liye {room} book karna yaad rakhna.",
        "{item} ghar bhool gaya, office mein ek spare rakhna padega.",
        "expenses {day} se pehle submit kar dena reminder.",
        "aaj traffic bahut zyada tha, {mins} minute ka raasta laga.",
        "{person} ne {time} wali call thodi der ke liye postpone kar di.",
        "kal {person} ke saath sync hai, thoda pehle nikal jaana.",
    ],
    "ta-en": [
        "naalaikku {room} book pannanum reminder.",
        "traffic romba irundhichu office varadhukku, {mins} minute pudichuchu.",
        "lunch {place} la irukanga today.",
        "call konjam late aagum standup ku, {mins} minute.",
        "wifi {room} la kammiyaana velai pannala.",
        "{person} {time} call ah postpone pannirukaanga konjam.",
    ],
    "kn-en": [
        "naale {room} book maadbeku reminder.",
        "traffic thumba idthu office ge banna, {mins} minute aaythu.",
        "lunch {place} nalli idhe today.",
        "call thodu late aaguthe standup ge, {mins} minute.",
        "wifi {room} nalli sariyaagi kelasa maadthilla.",
        "{person} {time} call na konjam postpone madidru.",
    ],
    "hi-deva": [
        "आज traffic बहुत ज्यादा था, ऑफिस आते वक्त {mins} minute लग गए।",
        "coffee लेना मत भूलना {time} वाली call से पहले।",
        "standup के लिए थोड़ा late हो रहा हूं, {mins} minute।",
        "{room} का wifi फिर से खराब है।",
    ],
}

STYLE_BY_LANG = {
    "en": "notes_terse", "hi": "chat_casual", "ta-en": "chat_casual",
    "kn-en": "chat_casual", "hi-deva": "notes_terse",
}
LANG_TAGS = {
    "en": ["en"], "hi": ["hi", "en"], "ta-en": ["ta", "en"],
    "kn-en": ["kn", "en"], "hi-deva": ["hi-deva", "en"],
}
LANG_WEIGHTS = {"en": 0.55, "hi": 0.30, "ta-en": 0.05, "kn-en": 0.05, "hi-deva": 0.05}


def weighted_lang(rng: random.Random) -> str:
    styles = list(LANG_WEIGHTS.keys())
    weights = list(LANG_WEIGHTS.values())
    return rng.choices(styles, weights=weights, k=1)[0]


TIME_SLOTS = ["9am", "10am", "11am", "12pm", "2pm", "3pm", "4pm", "5pm", "6pm"]
MIN_SLOTS = [5, 8, 10, 12, 15, 20, 25, 30, 40]
DAY_SLOTS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
ROOM_SLOTS = ["conference room", "third floor meeting room", "small room", "war room", "boardroom"]
PLACE_SLOTS = ["new place", "cafe downstairs", "food court", "place near the office", "rooftop cafe"]
ITEM_SLOTS = ["charger", "badge", "headphones", "notebook", "umbrella"]


def fill_template(tmpl: str, rng: random.Random) -> str:
    # format() only consumes the placeholders actually present in tmpl, so it's
    # fine to always offer the full slot set regardless of which template picked.
    return tmpl.format(
        person=rng.choice(FIRST_NAMES),
        project=rng.choice(PROJECTS),
        vendor=rng.choice(VENDORS),
        time=rng.choice(TIME_SLOTS),
        mins=rng.choice(MIN_SLOTS),
        day=rng.choice(DAY_SLOTS),
        room=rng.choice(ROOM_SLOTS),
        place=rng.choice(PLACE_SLOTS),
        item=rng.choice(ITEM_SLOTS),
    )


def generate_filler(rng: random.Random, n: int, templates: dict, tag: str) -> list[Rec]:
    recs = []
    for _ in range(n):
        lang_key = weighted_lang(rng)
        tmpl = rng.choice(templates[lang_key])
        text = fill_template(tmpl, rng)
        week = rng.randint(1, 12)
        recs.append(Rec(week, rng.randint(0, 6), text, LANG_TAGS[lang_key], [tag],
                         style_id=STYLE_BY_LANG[lang_key]))
    return recs


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def assign_timestamps(recs: list[Rec], rng: random.Random) -> list[dict]:
    out = []
    for rec in recs:
        day = WEEK_START + timedelta(weeks=rec.week - 1, days=rec.day_offset)
        hour = rng.randint(8, 19)
        minute = rng.randint(0, 59)
        second = rng.randint(0, 59)
        spoken_at = day.replace(hour=hour, minute=minute, second=second)
        out.append({"spoken_at": spoken_at, "rec": rec})
    out.sort(key=lambda x: x["spoken_at"])
    return out


def build(seed: int, n_target: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)

    recs = narrative_records() + excluded_records() + ambiguous_records()
    recs += organic_distributed_clusters(rng, 9)   # ~27 records
    recs += organic_contradiction_pairs(rng, 9)     # ~18 records

    n_so_far = len(recs)
    n_durable = 150
    n_noise = max(n_target - n_so_far - n_durable, 0)

    recs += generate_filler(rng, n_durable, DURABLE_TEMPLATES, "durable:filler")
    recs += generate_filler(rng, n_noise, NOISE_TEMPLATES, "noise")

    timestamped = assign_timestamps(recs, rng)

    corpus = []
    answer_entries = []
    for idx, item in enumerate(timestamped, start=1):
        rec: Rec = item["rec"]
        rec_id = f"dict_{idx:05d}"
        formatted = rec.formatted_output
        raw = degrade(formatted, rec.language, rng)
        n_words = len(formatted.split())
        app, window = (rec.app_context, rec.window_title) if rec.app_context else pick_app(rng)

        corpus.append({
            "id": rec_id,
            "spoken_at": item["spoken_at"].isoformat(),
            "raw_asr": raw,
            "formatted_output": formatted,
            "app_context": app,
            "window_title": window,
            "language": rec.language,
            "duration_ms": pick_duration(rng, n_words),
            "style_id": rec.style_id,
        })
        answer_entries.append({"id": rec_id, "tags": rec.tags})

    return corpus, answer_entries


def summarize_answer_key(corpus: list[dict], answer_entries: list[dict]) -> dict:
    by_id = {c["id"]: c for c in corpus}

    def has_prefix(tags, prefix):
        return any(t.startswith(prefix) for t in tags)

    counts = {
        "total": len(corpus),
        "narrative_distributed_facts": len({t.split(":")[1] for e in answer_entries for t in e["tags"]
                                             if t.startswith("fact:") and not t.startswith("fact:organic")}),
        "organic_distributed_clusters": len({t.split(":")[1] for e in answer_entries for t in e["tags"]
                                              if t.startswith("fact:organic_fact")}),
        "contradiction_chains": len({t.split(":")[1] for e in answer_entries for t in e["tags"]
                                       if t.startswith("contradiction:")}),
        "handover_records": sum(1 for e in answer_entries if has_prefix(e["tags"], "handover:")),
        "excluded_health_emotional_personal": sum(1 for e in answer_entries
                                                    if has_prefix(e["tags"], "excluded:health_emotional_personal")),
        "excluded_opinion_financial": sum(1 for e in answer_entries
                                           if has_prefix(e["tags"], "excluded:opinion_financial")),
        "ambiguous": sum(1 for e in answer_entries if has_prefix(e["tags"], "ambiguous:")),
        "durable_standalone": sum(1 for e in answer_entries if has_prefix(e["tags"], "durable:")),
        "noise": sum(1 for e in answer_entries if has_prefix(e["tags"], "noise")),
    }

    lang_counts: dict[str, int] = {}
    for c in corpus:
        key = "+".join(c["language"])
        lang_counts[key] = lang_counts.get(key, 0) + 1
    counts["language_mix"] = lang_counts
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    parser.add_argument("--n", type=int, default=N_DEFAULT)
    args = parser.parse_args()

    corpus, answer_entries = build(args.seed, args.n)

    corpus_path = OUT_DIR / "corpus.jsonl"
    answer_key_path = OUT_DIR / "answer_key.jsonl"

    with corpus_path.open("w", encoding="utf-8") as f:
        for rec in corpus:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with answer_key_path.open("w", encoding="utf-8") as f:
        for entry in answer_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    summary = summarize_answer_key(corpus, answer_entries)
    print(f"Wrote {len(corpus)} records to {corpus_path}")
    print(f"Wrote {len(answer_entries)} answer-key entries to {answer_key_path}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
