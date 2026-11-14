"""May the strategic planner answer this turn without a plan?

The meta-planning prompt lets the model answer a purely conversational turn ("hello",
"thanks") with `{"plan_type": "conversational", "response": "…"}` — no phases, no tools.
FOUND (Optimize campaign OC-F13, 2026-10-29): a weak planner copies that example for DATA
questions — "How many customers…", "today's date according to the database" — and the
turn delivered the prompt's canned greeting ("I'm doing well, thank you for asking! How
can I help you…") as the answer, 3 times in 10 targeted runs on MiMo v2.6 Flash.

The rule is deterministic and asks of the QUESTION, never of the model: the shortcut is
accepted only for small talk — a short turn that opens with a greeting, thanks, farewell
or acknowledgement and asks for nothing. Anything else is re-planned once with a
correction note (planner.py). A false "not small talk" costs one planning call; a false
"small talk" delivers a greeting for a question — so the rule leans to re-planning.
"""
import re

_MAX_WORDS = 12

# Small-talk phrases, longest first (so "thank you very much" is removed before "thank you").
_PHRASES = sorted([
    "hi", "hello", "hey", "hiya", "howdy", "greetings",
    "good morning", "good afternoon", "good evening", "good day", "good night",
    "thanks", "thank you", "thx", "cheers", "much appreciated", "appreciated",
    "bye", "goodbye", "good bye", "see you", "see you later", "talk later",
    "how are you", "how are you doing", "how are things", "hows it going", "how is it going",
    "whats up", "nice to meet you", "pleased to meet you",
    "youre welcome", "you are welcome", "no problem",
], key=lambda p: -len(p.split()))

# Words that may surround a greeting without asking for anything.
_FILLER = {
    "so", "very", "much", "a", "lot", "again", "there", "all", "everyone", "friend", "buddy",
    "mate", "for", "your", "the", "help", "today", "tonight", "and", "too", "as", "well",
    "dear", "uderia", "agent", "assistant", "bot", "oh", "ah", "well", "just", "wanted", "to",
    "say", "i", "doing", "hope", "you", "are", "good",
}


def _tokens(text: str) -> list:
    return re.findall(r"[a-z]+", text.lower().replace("'", "").replace("\u2019", ""))


def goal_is_small_talk(goal: str) -> bool:
    """True when the turn may be answered conversationally, without a plan.

    Small talk = at least one greeting/thanks/farewell phrase, and NOTHING else once those
    phrases and a short list of filler words are removed. "thanks, now break it down by
    region" leaves "now break it down by region" -> a request. Consent ("yes", "ok",
    "sure") is deliberately NOT small talk: after "Shall I list the tables?" it is a request.
    """
    text = (goal or "").strip()
    if not text:
        return True
    words = _tokens(text)
    if not words or len(words) > _MAX_WORDS:
        return False
    found = False
    i, rest = 0, []
    while i < len(words):
        for p in _PHRASES:
            pw = p.split()
            if words[i:i + len(pw)] == pw:
                found = True
                i += len(pw)
                break
        else:
            rest.append(words[i])
            i += 1
    return found and all(w in _FILLER for w in rest)


REPLAN_NOTE = (
    "\n\nIMPORTANT: Your previous response classified this request as a conversational turn "
    "and answered it with a greeting. It is NOT small talk — the user asked for information "
    "or an action:\n---\n{goal}\n---\n"
    "Do NOT return plan_type \"conversational\". Respond with ONLY a valid JSON array of "
    "phase objects that answers the request with the available tools, ending with the "
    "final report."
)
