"""
One-command check that the AI chat works with YOUR Groq key. Run from the project root:

    python scripts/check_groq.py                      # built-in sample questions
    python scripts/check_groq.py "your question"      # any language, e.g. Hindi / Odia

It (1) verifies the key, (2) checks your configured model names still exist on Groq
(Groq retires models from time to time), then (3) sends real questions through the SAME code
path and system prompt the API uses, against a small sample field snapshot, and prints the
answers so you can judge their quality.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import llm  # noqa: E402
from app.services.chat import SYSTEM_PROMPT  # noqa: E402

SAMPLE_SNAPSHOT = """Current time: 2026-09-20 11:00 UTC (month: September).

FARM: Demo Farm | location (lat, long): UNKNOWN (no coordinates set) - ask the farmer their region before giving region-specific advice
ZONE: North greenhouse | soil type: loam | auto mode: on
CROP: tomato (healthy moisture 40-70%)
DEVICE FN-001: ONLINE (last seen 0 min ago) | pump running: no

LATEST SENSOR READING (1 min ago):
  soil moisture: 33.0%
  temperature: 34.0 C | humidity: not measured | light (raw LDR): 800
RAIN: rain sensor is DRY (not raining at the field right now).
SOIL MOISTURE LAST 24H: min 33%, avg 45%, max 62% (48 readings) - falling.
RECENT WATERING ACTIVITY (newest first):
  20h ago: AUTO, ~1.5 L, 75s - Soil moisture dropped below threshold.

IRRIGATION ADVISOR VERDICT RIGHT NOW (machine-learning model + safety rules):
  should water now: YES - about 2.1 L, pump ~105s
  reason: Watering 2.10L recommended - soil moisture is 33%, below the 40% threshold for this crop; temperature is elevated at 34.0C.
  rain forecast: NOT AVAILABLE (farm has no coordinates, so there is no real forecast - do not quote any rain percentage)
CROPS ALREADY CONFIGURED IN THIS APP (healthy moisture range):
  cotton: 25-55% | maize: 35-65% | rice: 60-90% | tomato: 40-70% | wheat: 30-60%"""

QUESTIONS = [
    "Should I water today?",
    "My tomato leaves are turning yellow. What should I do?",
    "Which plants should I grow here, and which should I avoid?",
    "मेरे टमाटर के पत्ते मुरझा रहे हैं, क्या करूँ?",
]


def main():
    print(f"Base URL : {settings.GROQ_BASE_URL}\nModel    : {settings.GROQ_MODEL}"
          f"\nFallback : {settings.GROQ_FALLBACK_MODEL}\n")
    if not settings.GROQ_API_KEY:
        sys.exit("FAIL: GROQ_API_KEY is empty. Put it in .env (free key: console.groq.com).")

    # 1) key + model names
    try:
        r = requests.get(f"{settings.GROQ_BASE_URL.rstrip('/')}/models",
                         headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"}, timeout=15)
    except requests.exceptions.RequestException as exc:
        sys.exit(f"FAIL: can't reach Groq ({exc.__class__.__name__}). Check your internet connection.")
    if r.status_code in (401, 403):
        sys.exit("FAIL: Groq rejected the key. Re-copy it from console.groq.com/keys.")
    if r.status_code != 200:
        sys.exit(f"FAIL: Groq /models returned HTTP {r.status_code}: {r.text[:200]}")
    available = {m["id"] for m in r.json().get("data", [])}
    print("OK: key is valid.")
    for name, label in [(settings.GROQ_MODEL, "GROQ_MODEL"), (settings.GROQ_FALLBACK_MODEL, "GROQ_FALLBACK_MODEL")]:
        if name in available:
            print(f"OK: {label}='{name}' exists.")
        else:
            print(f"WARNING: {label}='{name}' is NOT in Groq's current list. Pick one of:\n   "
                  + "\n   ".join(sorted(available)))

    # 2) real answers through the real code path
    system = SYSTEM_PROMPT.format(snapshot=SAMPLE_SNAPSHOT, language="en")
    questions = sys.argv[1:] or QUESTIONS
    for q in questions:
        t0 = time.time()
        try:
            reply, model = llm.chat_completion([{"role": "system", "content": system},
                                                {"role": "user", "content": q}])
        except (llm.LLMConfigError, llm.LLMUnavailable) as exc:
            print(f"\nQ: {q}\nFAIL: {exc}")
            continue
        print(f"\nQ: {q}\n[{model}, {time.time() - t0:.1f}s]\n{reply}")


if __name__ == "__main__":
    main()
