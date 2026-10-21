import os
import json
import uuid
from datetime import datetime

HISTORY_FILE = "history.json"


# ---------------------------------------------------------
# CREATE FILE AUTOMATICALLY
# ---------------------------------------------------------

def ensure_history_file():

    if not os.path.exists(
        HISTORY_FILE
    ):

        with open(
            HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                [],
                f,
                indent=2,
                ensure_ascii=False
            )


# ---------------------------------------------------------
# LOAD
# ---------------------------------------------------------

def load_history():

    ensure_history_file()

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return []


# ---------------------------------------------------------
# SAVE
# ---------------------------------------------------------

def save_history(history):

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            history,
            f,
            indent=2,
            ensure_ascii=False
        )


# ---------------------------------------------------------
# ADD TRIP
# ---------------------------------------------------------

def add_entry(
    origin,
    destination,
    preference,
    mood,
    vehicle,
    language,
    weather,
    summary,
    poi=None
):

    history = load_history()

    entry = {

        "id": str(uuid.uuid4()),

        "timestamp":
            datetime.now().isoformat(),

        "origin":
            origin,

        "destination":
            destination,

        "preference":
            preference,

        "mood":
            mood,

        "vehicle":
            vehicle,

        "language":
            language,

        "weather":
            weather,

        "poi":
            poi or [],

        "summary":
            summary
    }

    history.append(entry)

    # Keep latest 100 trips.
    history = history[-100:]

    save_history(history)

    return entry


# ---------------------------------------------------------
# RECENT
# ---------------------------------------------------------

def get_recent(
    n=10
):

    history = load_history()

    return history[-n:][::-1]


# ---------------------------------------------------------
# SEARCH HISTORY
# ---------------------------------------------------------

def search_history(
    query,
    n=5
):

    query = query.lower()

    history = load_history()

    matches = []

    for item in reversed(history):

        text = json.dumps(
            item,
            ensure_ascii=False
        ).lower()

        if query in text:

            matches.append(item)

        if len(matches) >= n:

            break

    return matches


# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------

ensure_history_file()
if __name__ == "__main__":
    ensure_history_file()

    print("History store is working!")
    print("History file:", HISTORY_FILE)

    history = load_history()
    print("Current history entries:", len(history))