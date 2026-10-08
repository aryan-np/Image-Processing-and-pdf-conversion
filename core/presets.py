REALISTIC = [
    ("PASSPORT", "Passport", "single", True),
    ("CITIZENSHIP_FRONT", "Citizenship", "pair", True),
    ("CITIZENSHIP_BACK", "Citizenship", "pair", True),
    ("BACHELORS", "Education", "single", True),
    ("GRADE_12", "Education", "single", True),
    ("GRADE_10", "Education", "single", True),
    ("ENGLISH_CERT", "Education", "single", False),
    ("MEDIUM_OF_INSTRUCTION", "Education", "single", True),
    ("LOR", "Experience", "single", True),
    ("WORK_EXP", "Experience", "single", True),
    ("CV", "Personal", "single", True),
    ("PERSONAL_STATEMENT", "Personal", "single", True),
]
# note: spec says 11 slots but lists 12 entries; keep 12 as realistic (front+back counted)
PRESETS = {"realistic": REALISTIC, "25": None, "50": None, "100": None, "200": None}

def make_slots(session, preset="realistic", count=None):
    from .models import Slot
    existing = session.slots.count()
    order = existing
    created = []
    if preset == "realistic":
        for label, group, layout, req in REALISTIC:
            created.append(Slot(session=session, label=label, group=group, order=order,
                                layout=layout, required=req))
            order += 1
    else:
        n = count or int(preset)
        for k in range(n):
            layout = "pair" if (k % 2 == 0 and k + 1 < n and n % 2 == 0) else "single"
            created.append(Slot(session=session, label=f"L{k+1:03d}", group="Load",
                                order=order, layout=layout, required=True))
            order += 1
    Slot.objects.bulk_create(created)
    return len(created)
