#!/usr/bin/env python3
"""Generate the FaceTime testing-slot list (docs/slots/slots.json) and the
GitHub issue form that books them (.github/ISSUE_TEMPLATE/testing-slot.yml).
Edit WINDOWS / MINUTES and re-run; the page and the booking bot read slots.json."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ET = timezone(timedelta(hours=-4))  # EDT
MINUTES = 15
WINDOWS = [  # (start, end) local ET, end exclusive
    (datetime(2026, 10, 10, 19, 0, tzinfo=ET), datetime(2026, 10, 11, 0, 0, tzinfo=ET)),
    (datetime(2026, 10, 11, 9, 0, tzinfo=ET), datetime(2026, 10, 11, 18, 0, tzinfo=ET)),
]
ROOT = Path(__file__).resolve().parent.parent


def label(t):
    return t.strftime("%a %b %-d · %-I:%M %p ET")


slots = []
for start, end in WINDOWS:
    t = start
    while t < end:
        slots.append({"label": label(t), "start": t.isoformat(), "end": (t + timedelta(minutes=MINUTES)).isoformat()})
        t += timedelta(minutes=MINUTES)

(ROOT / "docs/slots/slots.json").write_text(json.dumps({"minutes": MINUTES, "slots": slots}, indent=1) + "\n")

opts = "\n".join(f'        - "{s["label"]}"' for s in slots)
(ROOT / ".github/ISSUE_TEMPLATE/testing-slot.yml").write_text(f'''name: Book a FaceTime testing slot
description: Claim a {MINUTES}-minute turn on the shared Chert FaceTime line. First come, first served.
title: "[slot] "
labels: ["slot"]
body:
  - type: markdown
    attributes:
      value: |
        See open slots at https://seraphimlabs-dev.github.io/brodie-starter/slots/ . If the slot is already taken, the bot closes this and tells you. Max 2 upcoming slots per team.
  - type: dropdown
    id: slot
    attributes:
      label: Slot
      options:
{opts}
    validations:
      required: true
  - type: input
    id: team
    attributes:
      label: Team name
      placeholder: Team Squat Rack
    validations:
      required: true
  - type: input
    id: agent
    attributes:
      label: Agent name
      description: The AGENT_NAME from your .env.local, used in `chert dev --agent`
      placeholder: team-yourname-agent
    validations:
      required: true
  - type: input
    id: contact
    attributes:
      label: Discord handle
      description: So the team before you can ping you when the line is free
    validations:
      required: true
  - type: dropdown
    id: where
    attributes:
      label: Testing from
      options:
        - In person (JMHH 265)
        - Remote
    validations:
      required: true
''')
print(len(slots), "slots")
