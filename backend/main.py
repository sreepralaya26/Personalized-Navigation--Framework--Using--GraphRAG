import os
import re
import json
import time
from pathlib import Path
from typing import Optional

import requests
import networkx as nx
import osmnx as ox

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel


# ============================================================
# OPTIONAL IMPORTS
# ============================================================

try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None

try:
    import chromadb
except ImportError:
    chromadb = None


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

GRAPH_FILE = BASE_DIR / "data" / "graph.graphml"
CHROMA_DIR = BASE_DIR / "chroma_db"
HISTORY_FILE = BASE_DIR / "history.json"


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://localhost:11434/api/generate"
)

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "llama3.2:latest"
)

OPENWEATHER_API_KEY = os.getenv(
    "OPENWEATHER_API_KEY",
    ""
)

ORS_API_KEY = os.getenv(
    "ORS_API_KEY",
    ""
)

NEO4J_URI = os.getenv(
    "NEO4J_URI",
    "neo4j://localhost:7687"
)

NEO4J_USERNAME = os.getenv(
    "NEO4J_USERNAME",
    "neo4j"
)

NEO4J_PASSWORD = os.getenv(
    "NEO4J_PASSWORD",
    ""
)

NEO4J_DATABASE = os.getenv(
    "NEO4J_DATABASE",
    "neo4j"
)

TOMTOM_API_KEY = os.getenv(
    "TOMTOM_API_KEY",
    ""
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="GraphRAG Chatbot",
    version="2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

    message: str

    session_id: str = "default"


class RouteRequest(BaseModel):

    origin: str

    destination: str

    vehicle: str = "car"

    mode: str = "shortest"

    session_id: str = "default"


# ============================================================
# GRAPHML
# ============================================================

GRAPH = None


def load_graph():

    global GRAPH

    if not GRAPH_FILE.exists():

        print(
            "WARNING: graph.graphml not found:"
        )

        print(GRAPH_FILE)

        return None

    try:

        GRAPH = ox.load_graphml(
            GRAPH_FILE
        )

        print(
            f"GRAPHML LOADED: "
            f"{len(GRAPH.nodes)} nodes | "
            f"{len(GRAPH.edges)} edges"
        )

        return GRAPH

    except Exception as e:

        print(
            "GRAPHML ERROR:",
            e
        )

        return None


load_graph()


# ============================================================
# NEO4J
# ============================================================

neo4j_driver = None


def connect_neo4j():

    global neo4j_driver

    if GraphDatabase is None:

        print(
            "Neo4j Python driver is not installed."
        )

        return

    if not NEO4J_PASSWORD:

        print(
            "NEO4J_PASSWORD is not configured."
        )

        return

    try:

        neo4j_driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(
                NEO4J_USERNAME,
                NEO4J_PASSWORD
            )
        )

        neo4j_driver.verify_connectivity()

        print(
            "NEO4J CONNECTED:",
            NEO4J_URI
        )

    except Exception as e:

        neo4j_driver = None

        print(
            "NEO4J CONNECTION ERROR:",
            e
        )


connect_neo4j()


# ============================================================
# NEO4J QUERY HELPER
# ============================================================

def neo4j_query(
    query: str,
    parameters: Optional[dict] = None
):

    if neo4j_driver is None:

        return []

    try:

        with neo4j_driver.session(
            database=NEO4J_DATABASE
        ) as session:

            result = session.run(
                query,
                parameters or {}
            )

            return [
                record.data()
                for record in result
            ]

    except Exception as e:

        print(
            "NEO4J QUERY ERROR:",
            e
        )

        return []


# ============================================================
# NEO4J SEARCH
# ============================================================

def search_neo4j(
    question: str,
    limit: int = 8
):

    query = """
    MATCH (n)
    WHERE
        any(
            key IN keys(n)
            WHERE
                toString(n[key]) CONTAINS $question
        )
    RETURN
        labels(n) AS labels,
        properties(n) AS properties
    LIMIT $limit
    """

    return neo4j_query(
        query,
        {
            "question": question,
            "limit": limit
        }
    )


# ============================================================
# NEO4J GRAPH CONTEXT
# ============================================================

def get_graph_context(
    question: str
):

    if neo4j_driver is None:

        return []

    results = search_neo4j(
        question
    )

    context = []

    for item in results:

        context.append(
            {
                "labels":
                    item.get(
                        "labels",
                        []
                    ),

                "properties":
                    item.get(
                        "properties",
                        {}
                    )
            }
        )

    return context


# ============================================================
# NEO4J ROUTE CONNECTION CONTEXT
# ============================================================

def get_neo4j_route_context(
    origin: str,
    destination: str
):

    if neo4j_driver is None:

        return []

    query = """
    MATCH (a), (b)
    WHERE
        any(
            key IN keys(a)
            WHERE
                toString(a[key]) CONTAINS $origin
        )
        AND
        any(
            key IN keys(b)
            WHERE
                toString(b[key]) CONTAINS $destination
        )

    OPTIONAL MATCH p =
        (a)-[*1..4]-(b)

    RETURN
        labels(a) AS origin_labels,
        properties(a) AS origin_properties,
        labels(b) AS destination_labels,
        properties(b) AS destination_properties,
        [x IN nodes(p) |
            {
                labels: labels(x),
                properties: properties(x)
            }
        ] AS connected_nodes

    LIMIT 5
    """

    return neo4j_query(
        query,
        {
            "origin": origin,
            "destination": destination
        }
    )


# ============================================================
# HISTORY
# ============================================================

def load_history():

    if not HISTORY_FILE.exists():

        return {}

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


HISTORY = load_history()


def save_history():

    try:

        with open(
            HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                HISTORY,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print(
            "HISTORY ERROR:",
            e
        )


# ============================================================
# USER PREFERENCE TO NEO4J
# ============================================================

def save_user_to_neo4j(
    session_id,
    language,
    vehicle,
    mode
):

    if neo4j_driver is None:

        return

    query = """
    MERGE (u:User {
        session_id: $session_id
    })

    SET
        u.language = $language,
        u.vehicle = $vehicle,
        u.route_mode = $mode,
        u.updated_at = datetime()

    RETURN u
    """

    neo4j_query(
        query,
        {
            "session_id": session_id,
            "language": language,
            "vehicle": vehicle,
            "mode": mode
        }
    )


# ============================================================
# SAVE CHAT TO NEO4J
# ============================================================

def save_chat_to_neo4j(
    session_id,
    message,
    language,
    vehicle,
    mode
):

    if neo4j_driver is None:

        return

    query = """
    MERGE (u:User {
        session_id: $session_id
    })

    CREATE (m:ChatMessage {
        message: $message,
        language: $language,
        vehicle: $vehicle,
        route_mode: $mode,
        timestamp: datetime()
    })

    CREATE (u)-[:ASKED]->(m)

    RETURN m
    """

    neo4j_query(
        query,
        {
            "session_id": session_id,
            "message": message,
            "language": language,
            "vehicle": vehicle,
            "mode": mode
        }
    )


# ============================================================
# LANGUAGES
# ============================================================

LANGUAGES = {

    "english": {
        "code": "en",
        "name": "English"
    },

    "tamil": {
        "code": "ta",
        "name": "Tamil"
    },

    "telugu": {
        "code": "te",
        "name": "Telugu"
    },

    "hindi": {
        "code": "hi",
        "name": "Hindi"
    },

    "kannada": {
        "code": "kn",
        "name": "Kannada"
    },

    "malayalam": {
        "code": "ml",
        "name": "Malayalam"
    },

    "urdu": {
        "code": "ur",
        "name": "Urdu"
    }
}


# ============================================================
# LANGUAGE DETECTION
# ============================================================

def detect_language(text):

    if re.search(
        r"[\u0B80-\u0BFF]",
        text
    ):
        return "tamil"

    if re.search(
        r"[\u0C00-\u0C7F]",
        text
    ):
        return "telugu"

    if re.search(
        r"[\u0C80-\u0CFF]",
        text
    ):
        return "kannada"

    if re.search(
        r"[\u0D00-\u0D7F]",
        text
    ):
        return "malayalam"

    if re.search(
        r"[\u0900-\u097F]",
        text
    ):
        return "hindi"

    if re.search(
        r"[\u0600-\u06FF]",
        text
    ):
        return "urdu"

    return "english"


# ============================================================
# VEHICLE DETECTION
# ============================================================

def detect_vehicle(text):

    t = text.lower()

    bike_words = [

        "bike",
        "motorbike",
        "motorcycle",
        "scooter",
        "two wheeler",
        "2 wheeler",

        "பைக்",
        "பைக்கில்",
        "ஸ்கூட்டர்",

        "బైక్",
        "బైక్‌తో",
        "స్కూటర్",

        "बाइक",
        "बाइक से",
        "स्कूटर",

        "ಬೈಕ್",
        "ಸ್ಕೂಟರ್",

        "ബൈക്ക്",
        "സ്കൂട്ടർ",

        "موٹر سائیکل",
        "بائیک",
        "سکوٹر"
    ]

    walk_words = [

        "walk",
        "walking",
        "on foot",

        "பாதசாரி",
        "நடந்து",
        "நடக்க",

        "నడిచి",
        "నడక",

        "चलकर",
        "पैदल",

        "ನಡೆದು",
        "ನಡಿಗೆ",

        "നടന്ന്",
        "നടക്കാൻ",

        "پیدل",
        "پیدل چل کر"
    ]

    car_words = [

        "car",
        "auto",
        "cab",
        "taxi",

        "கார்",
        "காரில்",

        "కారు",
        "కారులో",

        "कार",
        "कार से",

        "ಕಾರು",
        "ಕಾರಿನಲ್ಲಿ",

        "കാർ",
        "കാറിൽ",

        "کار",
        "گاڑی"
    ]

    for word in bike_words:

        if word in t:
            return "bike"

    for word in walk_words:

        if word in t:
            return "walk"

    for word in car_words:

        if word in t:
            return "car"

    return "car"


# ============================================================
# ROUTE MODE
# ============================================================

def detect_route_mode(text):

    t = text.lower()

    if any(
        x in t
        for x in [

            "shortest",
            "short route",
            "least distance",
            "minimum distance",

            "குறுகிய",
            "குறுகிய பாதை",
            "சிறிய பாதை",

            "చిన్న మార్గం",
            "అతి తక్కువ దూరం",

            "सबसे छोटा",
            "छोटा रास्ता",
            "कम दूरी",

            "ಕಡಿಮೆ ದೂರ",
            "ಚಿಕ್ಕ ಮಾರ್ಗ",

            "കുറഞ്ഞ ദൂരം",
            "ഏറ്റവും ചെറിയ വഴി",

            "مختصر راستہ",
            "کم فاصلے"
        ]
    ):

        return "shortest"

    if any(
        x in t
        for x in [

            "fastest",
            "quickest",
            "fast route",
            "quick route",

            "வேகமான",
            "வேகமான வழி",

            "త్వరగా",
            "వేగవంతమైన మార్గం",

            "तेज़ रास्ता",
            "सबसे तेज़",

            "ವೇಗವಾದ ಮಾರ್ಗ",
            "ತ್ವರಿತ ಮಾರ್ಗ",

            "വേഗത്തിലുള്ള വഴി",
            "ഏറ്റവും വേഗത്തിലുള്ള",

            "تیز ترین راستہ",
            "سب سے تیز"
        ]
    ):

        return "fastest"

    if any(
        x in t
        for x in [

            "relaxed",
            "peaceful",
            "calm",
            "slow",

            "அமைதியான",
            "நிதானமான",

            "ప్రశాంతమైన",
            "నెమ్మదైన",

            "शांत रास्ता",
            "आरामदायक",

            "ಶಾಂತವಾದ",
            "ನಿಧಾನವಾದ",

            "ശാന്തമായ",
            "സമാധാനപരമായ",

            "پرسکون راستہ",
            "آرام دہ"
        ]
    ):

        return "relaxed"

    if any(
        x in t
        for x in [

            "scenic",
            "beautiful",
            "view",
            "beach road",

            "அழகான",
            "காட்சி",

            "అందమైన",
            "దృశ్యమయమైన",

            "सुंदर",
            "खूबसूरत",

            "ಸುಂದರ",
            "ದೃಶ್ಯಮಯ",

            "മനോഹരമായ",
            "കാഴ്ച",

            "خوبصورت",
            "منظر"
        ]
    ):

        return "scenic"

    return "shortest"


# ============================================================
# PLACE NAME NORMALIZATION
# ============================================================

PLACE_ALIASES = {

    # ADYAR
    "அடையார்": "Adyar",
    "அடையாரி": "Adyar",
    "அடையாரிலிருந்து": "Adyar",
    "அடையார் இருந்து": "Adyar",

    "అడయార్": "Adyar",
    "అడయార్ నుండి": "Adyar",

    "अड्यार": "Adyar",
    "अड्यार से": "Adyar",

    "ಅಡಯಾರ್": "Adyar",

    "അടയാർ": "Adyar",

    "اڈیار": "Adyar",

    # VELACHERY
    "வேளச்சேரி": "Velachery",
    "வேளச்சேரிக்கு": "Velachery",
    "வேளச்சேரியில்": "Velachery",
    "வேளச்சேரி செல்ல": "Velachery",

    "వేళచేరి": "Velachery",
    "వెలాచేరి": "Velachery",

    "वेलाचेरी": "Velachery",

    "ವೇಲಚೇರಿ": "Velachery",

    "വേളച്ചേരി": "Velachery",

    "ویلاچیری": "Velachery",

    # T NAGAR
    "தி.நகர்": "T Nagar",
    "டி நகர்": "T Nagar",
    "டிநகர்": "T Nagar",

    "టి నగర్": "T Nagar",
    "టి.నగర్": "T Nagar",

    "टी नगर": "T Nagar",
    "टी.नगर": "T Nagar",

    "ಟಿ ನಗರ": "T Nagar",

    "ടി നഗർ": "T Nagar",

    "ٹی نگر": "T Nagar"
}


def normalize_place_name(place):

    if not place:
        return place

    result = place.strip()

    # Exact aliases first
    for key, value in PLACE_ALIASES.items():

        if result == key:
            return value

    # Replace aliases occurring inside larger text
    for key, value in PLACE_ALIASES.items():

        result = result.replace(
            key,
            value
        )

    # Remove common route suffixes
    result = re.sub(
        r"(?:விலிருந்து|இருந்து|க்கு|வரை|से|तक|से\s+तक|"
        r"నుండి|కు|వరకు|ದಿಂದ|ಗೆ|ವರೆಗೆ|"
        r"ൽ നിന്ന്|ലേക്ക്|വരെ|سے|تک)\s*$",
        "",
        result,
        flags=re.IGNORECASE
    )

    return result.strip()


# ============================================================
# MULTILINGUAL ROUTE EXTRACTION
# ============================================================

def extract_route_places(text):

    original = text.strip()

    # --------------------------------------------------------
    # English
    # --------------------------------------------------------

    patterns = [

        r"from\s+(.+?)\s+to\s+(.+)",

        r"take me from\s+(.+?)\s+to\s+(.+)",

        r"route from\s+(.+?)\s+to\s+(.+)",

        r"directions from\s+(.+?)\s+to\s+(.+)",

        r"(.+?)\s+to\s+(.+)",

        r"(.+?)\s*→\s*(.+)"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            original,
            re.IGNORECASE
        )

        if match:

            origin = match.group(1).strip()

            destination = match.group(2).strip()

            destination = re.split(
                r"\b(?:by|via|using|with|please|through|"
                r"and|on the way|along the way)\b",
                destination,
                flags=re.IGNORECASE
            )[0].strip()

            origin = normalize_place_name(
                origin
            )

            destination = normalize_place_name(
                destination
            )

            if origin and destination:

                print(
                    f"EXTRACTED ROUTE: "
                    f"{origin} -> {destination}"
                )

                return origin, destination

    # --------------------------------------------------------
    # Tamil
    #
    # Example:
    # அடையாரிலிருந்து வேளச்சேரிக்கு
    # அடையாரிலிருந்து வேளச்சேரிக்கு காரில்...
    # --------------------------------------------------------

    tamil_patterns = [

        r"(.+?)லிருந்து\s+(.+?)(?:க்கு|வரை)",

        r"(.+?)இருந்து\s+(.+?)(?:க்கு|வரை)",

        r"(.+?)\s+முதல்\s+(.+?)(?:வரை|க்கு)"
    ]

    for pattern in tamil_patterns:

        match = re.search(
            pattern,
            original
        )

        if match:

            origin = normalize_place_name(
                match.group(1).strip()
            )

            destination = normalize_place_name(
                match.group(2).strip()
            )

            destination = re.split(
                r"(?:காரில்|பைக்கில்|நடந்து|வரை|"
                r"காட்டவும்|காட்டுங்கள்|கண்டுபிடித்து)",
                destination
            )[0].strip()

            if origin and destination:

                print(
                    f"EXTRACTED ROUTE: "
                    f"{origin} -> {destination}"
                )

                return origin, destination

    # --------------------------------------------------------
    # Telugu
    #
    # అడయార్ నుండి వేళచేరికి
    # --------------------------------------------------------

    telugu_patterns = [

        r"(.+?)\s*నుండి\s+(.+?)(?:కి|వరకు)",

        r"(.+?)\s*నుంచి\s+(.+?)(?:కి|వరకు)",

        r"(.+?)\s*నుండి\s+(.+?)\s*వరకు"
    ]

    for pattern in telugu_patterns:

        match = re.search(
            pattern,
            original
        )

        if match:

            origin = normalize_place_name(
                match.group(1).strip()
            )

            destination = normalize_place_name(
                match.group(2).strip()
            )

            destination = re.split(
                r"(?:కారులో|బైక్‌లో|నడిచి|చూపించండి|"
                r"కనుగొని)",
                destination
            )[0].strip()

            if origin and destination:

                print(
                    f"EXTRACTED ROUTE: "
                    f"{origin} -> {destination}"
                )

                return origin, destination

    # --------------------------------------------------------
    # Hindi
    #
    # अड्यार से वेलाचेरी तक
    # --------------------------------------------------------

    hindi_patterns = [

        r"(.+?)\s+से\s+(.+?)(?:\s+तक|$)",

        r"(.+?)\s+से\s+(.+?)\s+तक"
    ]

    for pattern in hindi_patterns:

        match = re.search(
            pattern,
            original
        )

        if match:

            origin = normalize_place_name(
                match.group(1).strip()
            )

            destination = normalize_place_name(
                match.group(2).strip()
            )

            destination = re.split(
                r"(?:कार से|बाइक से|पैदल|मैप पर|"
                r"दिखाइए|दिखाएं|खोजकर)",
                destination
            )[0].strip()

            if origin and destination:

                print(
                    f"EXTRACTED ROUTE: "
                    f"{origin} -> {destination}"
                )

                return origin, destination

    # --------------------------------------------------------
    # Kannada
    #
    # ಅಡಯಾರಿನಿಂದ ವೇಲಚೇರಿಗೆ
    # --------------------------------------------------------

    kannada_patterns = [

        r"(.+?)\s*ನಿಂದ\s+(.+?)(?:ಗೆ|ವರೆಗೆ)",

        r"(.+?)\s*ಇಂದ\s+(.+?)(?:ಗೆ|ವರೆಗೆ)"
    ]

    for pattern in kannada_patterns:

        match = re.search(
            pattern,
            original
        )

        if match:

            origin = normalize_place_name(
                match.group(1).strip()
            )

            destination = normalize_place_name(
                match.group(2).strip()
            )

            destination = re.split(
                r"(?:ಕಾರಿನಲ್ಲಿ|ಬೈಕ್‌ನಲ್ಲಿ|ನಡೆದು|"
                r"ತೋರಿಸಿ|ಹುಡುಕಿ)",
                destination
            )[0].strip()

            if origin and destination:

                print(
                    f"EXTRACTED ROUTE: "
                    f"{origin} -> {destination}"
                )

                return origin, destination

    # --------------------------------------------------------
    # Malayalam
    #
    # അടയാറിൽ നിന്ന് വേളച്ചേരിയിലേക്ക്
    # --------------------------------------------------------

    malayalam_patterns = [

        r"(.+?)\s*ൽ\s*നിന്ന്\s+(.+?)(?:ലേക്ക്|വരെ)",

        r"(.+?)\s*നിന്ന്\s+(.+?)(?:ലേക്ക്|വരെ)"
    ]

    for pattern in malayalam_patterns:

        match = re.search(
            pattern,
            original
        )

        if match:

            origin = normalize_place_name(
                match.group(1).strip()
            )

            destination = normalize_place_name(
                match.group(2).strip()
            )

            destination = re.split(
                r"(?:കാറിൽ|ബൈക്കിൽ|നടന്ന്|"
                r"കാണിക്കുക|കണ്ടെത്തി)",
                destination
            )[0].strip()

            if origin and destination:

                print(
                    f"EXTRACTED ROUTE: "
                    f"{origin} -> {destination}"
                )

                return origin, destination

    # --------------------------------------------------------
    # Urdu
    #
    # اڈیار سے ویلاچیری تک
    # --------------------------------------------------------

    urdu_patterns = [

        r"(.+?)\s+سے\s+(.+?)(?:\s+تک|$)"
    ]

    for pattern in urdu_patterns:

        match = re.search(
            pattern,
            original
        )

        if match:

            origin = normalize_place_name(
                match.group(1).strip()
            )

            destination = normalize_place_name(
                match.group(2).strip()
            )

            destination = re.split(
                r"(?:کار سے|بائیک سے|پیدل|"
                r"نقشے پر|دکھائیں|تلاش کرکے)",
                destination
            )[0].strip()

            if origin and destination:

                print(
                    f"EXTRACTED ROUTE: "
                    f"{origin} -> {destination}"
                )

                return origin, destination

    return None, None


# ============================================================
# GEOCODING
# ============================================================

def geocode_place(place):

    if not place:

        return None

    place = normalize_place_name(
        place
    )

    try:

        response = requests.get(
            "https://nominatim.openstreetmap.org/search",

            params={
                "q":
                    f"{place}, Chennai, "
                    "Tamil Nadu, India",

                "format":
                    "json",

                "limit":
                    1
            },

            headers={
                "User-Agent":
                    "GraphRAG-Chatbot"
            },

            timeout=10
        )

        if response.status_code != 200:

            return None

        data = response.json()

        if not data:

            return None

        return {

            "lat":
                float(
                    data[0]["lat"]
                ),

            "lon":
                float(
                    data[0]["lon"]
                ),

            "display_name":
                data[0]["display_name"]
        }

    except Exception as e:

        print(
            "GEOCODE ERROR:",
            e
        )

        return None


# ============================================================
# WEATHER
# ============================================================

def get_weather(
    lat,
    lon
):

    if not OPENWEATHER_API_KEY:

        return {
            "available": False
        }

    try:

        response = requests.get(

            "https://api.openweathermap.org/"
            "data/2.5/weather",

            params={

                "lat":
                    lat,

                "lon":
                    lon,

                "appid":
                    OPENWEATHER_API_KEY,

                "units":
                    "metric"
            },

            timeout=10
        )

        if response.status_code != 200:

            return {
                "available": False
            }

        data = response.json()

        return {

            "available":
                True,

            "temperature":
                data["main"]["temp"],

            "feels_like":
                data["main"]["feels_like"],

            "humidity":
                data["main"]["humidity"],

            "description":
                data["weather"][0]["description"],

            "wind_speed":
                data["wind"]["speed"]
        }

    except Exception as e:

        print(
            "WEATHER ERROR:",
            e
        )

        return {
            "available": False
        }


# ============================================================
# CHROMADB
# ============================================================

CHROMA_COLLECTION = None


def load_chroma():

    global CHROMA_COLLECTION

    if chromadb is None:

        print(
            "ChromaDB not installed."
        )

        return

    if not CHROMA_DIR.exists():

        print(
            "ChromaDB directory not found."
        )

        return

    try:

        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR)
        )

        collections = (
            client.list_collections()
        )

        if collections:

            CHROMA_COLLECTION = (
                client.get_collection(
                    collections[0].name
                )
            )

            print(
                "CHROMADB LOADED:",
                collections[0].name
            )

    except Exception as e:

        print(
            "CHROMADB ERROR:",
            e
        )


load_chroma()


def search_chroma(
    question,
    n_results=5
):

    if CHROMA_COLLECTION is None:

        return []

    try:

        result = CHROMA_COLLECTION.query(
            query_texts=[question],
            n_results=n_results
        )

        documents = result.get(
            "documents",
            [[]]
        )

        if documents:

            return documents[0]

    except Exception as e:

        print(
            "CHROMA SEARCH ERROR:",
            e
        )

    return []


# ============================================================
# ROUTE WEIGHT
# ============================================================

def edge_length(data):

    try:

        return float(
            data.get(
                "length",
                1
            )
        )

    except Exception:

        return 1.0


def highway_value(data):

    highway = data.get(
        "highway",
        ""
    )

    if isinstance(
        highway,
        list
    ):

        highway = (
            highway[0]
            if highway
            else ""
        )

    return str(
        highway
    ).lower()


def route_weight(
    data,
    mode,
    vehicle
):

    length = edge_length(
        data
    )

    highway = highway_value(
        data
    )

    if vehicle == "walk":

        if highway in [
            "motorway",
            "motorway_link",
            "trunk"
        ]:

            return float("inf")

    if vehicle == "bike":

        if highway in [
            "motorway",
            "motorway_link"
        ]:

            return float("inf")

    if mode == "shortest":

        return length

    if mode == "fastest":

        try:

            speed = data.get(
                "maxspeed",
                30
            )

            if isinstance(
                speed,
                list
            ):

                speed = speed[0]

            numbers = re.findall(
                r"\d+",
                str(speed)
            )

            speed = (
                float(numbers[0])
                if numbers
                else 30
            )

        except Exception:

            speed = 30

        return length / max(
            speed,
            5
        )

    if mode == "relaxed":

        penalty = {

            "primary": 1.30,

            "secondary": 1.20,

            "tertiary": 1.10,

            "residential": 0.85,

            "service": 0.70
        }

        return (
            length *
            penalty.get(
                highway,
                1
            )
        )

    if mode == "scenic":

        penalty = {

            "primary": 1.25,

            "secondary": 1.05,

            "tertiary": 0.90,

            "residential": 0.85,

            "service": 0.90
        }

        return (
            length *
            penalty.get(
                highway,
                1
            )
        )

    return length


# ============================================================
# LOCAL GRAPH ROUTE
# ============================================================

def calculate_local_route(
    origin,
    destination,
    vehicle,
    mode
):

    if GRAPH is None:

        return {

            "success":
                False,

            "message":
                "GraphML graph is not loaded."
        }

    try:

        origin_node = (
            ox.distance.nearest_nodes(

                GRAPH,

                origin["lon"],

                origin["lat"]
            )
        )

        destination_node = (
            ox.distance.nearest_nodes(

                GRAPH,

                destination["lon"],

                destination["lat"]
            )
        )

        route = nx.shortest_path(

            GRAPH,

            origin_node,

            destination_node,

            weight=lambda u, v, d:

                route_weight(
                    d,
                    mode,
                    vehicle
                )
        )

        coordinates = []

        distance = 0

        for node in route:

            coordinates.append([

                float(
                    GRAPH.nodes[node]["y"]
                ),

                float(
                    GRAPH.nodes[node]["x"]
                )
            ])

        for i in range(
            len(route) - 1
        ):

            edges = GRAPH.get_edge_data(

                route[i],

                route[i + 1]
            )

            if edges:

                edge = list(
                    edges.values()
                )[0]

                distance += edge_length(
                    edge
                )

        return {

            "success":
                True,

            "coordinates":
                coordinates,

            "distance_m":
                round(
                    distance,
                    1
                ),

            "distance_km":
                round(
                    distance / 1000,
                    2
                ),

            "origin":
                origin,

            "destination":
                destination,

            "vehicle":
                vehicle,

            "mode":
                mode,

            "source":
                "Local Graph"
        }

    except Exception as e:

        print(
            "LOCAL ROUTE ERROR:",
            e
        )

        return {

            "success":
                False,

            "message":
                str(e)
        }


# ============================================================
# OPENROUTESERVICE
# ============================================================

def ors_route(
    origin,
    destination,
    vehicle
):

    if not ORS_API_KEY:

        return None

    profile = {

        "car":
            "driving-car",

        "bike":
            "cycling-regular",

        "walk":
            "foot-walking"

    }.get(
        vehicle,
        "driving-car"
    )

    try:

        response = requests.post(

            "https://api.openrouteservice.org/"
            f"v2/directions/{profile}/geojson",

            headers={

                "Authorization":
                    ORS_API_KEY,

                "Content-Type":
                    "application/json"
            },

            json={

                "coordinates": [

                    [
                        origin["lon"],
                        origin["lat"]
                    ],

                    [
                        destination["lon"],
                        destination["lat"]
                    ]
                ]
            },

            timeout=30
        )

        if response.status_code != 200:

            print(
                "ORS STATUS:",
                response.status_code
            )

            return None

        data = response.json()

        feature = data[
            "features"
        ][0]

        coordinates = [

            [
                lat,
                lon
            ]

            for lon, lat

            in feature[
                "geometry"
            ][
                "coordinates"
            ]
        ]

        summary = feature[
            "properties"
        ][
            "summary"
        ]

        return {

            "success":
                True,

            "coordinates":
                coordinates,

            "distance_m":
                round(
                    summary["distance"],
                    1
                ),

            "distance_km":
                round(
                    summary["distance"] / 1000,
                    2
                ),

            "duration_seconds":
                round(
                    summary["duration"]
                ),

            "origin":
                origin,

            "destination":
                destination,

            "vehicle":
                vehicle,

            "source":
                "OpenRouteService"
        }

    except Exception as e:

        print(
            "ORS ERROR:",
            e
        )

        return None


# ============================================================
# ROUTE ENGINE
# ============================================================

def calculate_route(
    origin_name,
    destination_name,
    vehicle,
    mode
):

    print(
        f"ROUTE REQUEST: "
        f"{origin_name} -> "
        f"{destination_name} | "
        f"{vehicle} | "
        f"{mode}"
    )

    origin_name = normalize_place_name(
        origin_name
    )

    destination_name = normalize_place_name(
        destination_name
    )

    origin = geocode_place(
        origin_name
    )

    destination = geocode_place(
        destination_name
    )

    if not origin:

        return {

            "success":
                False,

            "message":
                f"Could not locate {origin_name}",

            "origin_name":
                origin_name,

            "destination_name":
                destination_name
        }

    if not destination:

        return {

            "success":
                False,

            "message":
                f"Could not locate {destination_name}",

            "origin_name":
                origin_name,

            "destination_name":
                destination_name
        }

    # --------------------------------------------------------
    # ORS for shortest / fastest
    # --------------------------------------------------------

    if (
        ORS_API_KEY
        and mode in [
            "shortest",
            "fastest"
        ]
    ):

        result = ors_route(

            origin,

            destination,

            vehicle
        )

        if result:

            result[
                "origin_name"
            ] = origin_name

            result[
                "destination_name"
            ] = destination_name

            return result

    # --------------------------------------------------------
    # Local GraphRAG graph
    # --------------------------------------------------------

    result = calculate_local_route(

        origin,

        destination,

        vehicle,

        mode
    )

    if result:

        result[
            "origin_name"
        ] = origin_name

        result[
            "destination_name"
        ] = destination_name

    return result


# ============================================================
# TRAFFIC
# ============================================================

def get_traffic(
    lat,
    lon
):

    if not TOMTOM_API_KEY:

        return {

            "available":
                False,

            "level":
                "estimated",

            "message":
                "No live traffic API configured."
        }

    try:

        response = requests.get(

            "https://api.tomtom.com/"
            "traffic/services/4/"
            "flowSegmentData/"
            "absolute/10/json",

            params={

                "point":
                    f"{lat},{lon}",

                "unit":
                    "KMPH",

                "key":
                    TOMTOM_API_KEY
            },

            timeout=10
        )

        if response.status_code != 200:

            return {
                "available":
                    False
            }

        flow = response.json()[

            "flowSegmentData"
        ]

        current = flow[
            "currentSpeed"
        ]

        free = flow[
            "freeFlowSpeed"
        ]

        ratio = (

            current /
            max(free, 1)
        )

        if ratio < 0.4:

            level = "heavy"

        elif ratio < 0.7:

            level = "moderate"

        else:

            level = "low"

        return {

            "available":
                True,

            "level":
                level,

            "current_speed":
                current,

            "free_flow_speed":
                free
        }

    except Exception as e:

        print(
            "TRAFFIC ERROR:",
            e
        )

        return {
            "available":
                False
        }


# ============================================================
# INTENT
# ============================================================

def is_navigation_question(text):

    t = text.lower()

    words = [

        "route",
        "road",
        "go to",
        "get to",
        "directions",
        "navigate",
        "take me",
        "from",
        " to ",
        "shortest",
        "fastest",
        "bike",
        "car",
        "walk",

        "பாதை",
        "வழி",
        "சாலை",
        "செல்ல",
        "இருந்து",
        "க்கு",
        "வரை",

        "మార్గం",
        "దారి",
        "వెళ్ళ",
        "నుండి",
        "కి",
        "వరకు",

        "रास्ता",
        "मार्ग",
        "जाना",
        "से",
        "तक",

        "ಮಾರ್ಗ",
        "ದಾರಿ",
        "ಹೋಗಿ",
        "ನಿಂದ",
        "ಗೆ",

        "വഴി",
        "റൂട്ട്",
        "പോകാൻ",
        "നിന്ന്",
        "ലേക്ക്",

        "راستہ",
        "راستے",
        "سے",
        "تک"
    ]

    return any(
        word in t
        for word in words
    )


# ============================================================
# WEATHER INTENT
# ============================================================

def is_weather_question(text):

    t = text.lower()

    words = [

        "weather",
        "temperature",
        "rain",
        "raining",

        "வானிலை",
        "மழை",

        "వాతావరణం",
        "వర్షం",

        "मौसम",
        "बारिश",

        "ಹವಾಮಾನ",
        "ಮಳೆ",

        "കാലാവസ്ഥ",
        "മഴ",

        "موسم",
        "بارش"
    ]

    return any(
        word in t
        for word in words
    )


# ============================================================
# OLLAMA
# ============================================================

def ask_ollama(
    question,
    language,
    chroma_context,
    neo4j_context,
    route_context,
    weather,
    traffic,
    route,
    preferences
):

    language_name = LANGUAGES[
        language
    ]["name"]

    # --------------------------------------------------------
    # IMPORTANT:
    # Ollama does NOT decide whether a route exists.
    # Python has already calculated the route.
    # --------------------------------------------------------

    route_exists = (
        isinstance(route, dict)
        and route.get("success") is True
    )

    prompt = f"""

You are GraphRAG Chatbot, an intelligent
multilingual navigation and knowledge assistant
for South Chennai.

The user's detected language is:

{language_name}

============================================================
STRICT LANGUAGE RULE
============================================================

You MUST answer completely in {language_name}.

Do NOT switch to English.

Do NOT mix English sentences into the answer.

Do NOT translate the answer into another language.

Use natural, grammatically correct {language_name}.

Place names such as Adyar, Velachery and T Nagar
may remain in their commonly recognized names.

============================================================
ROUTE RULE
============================================================

Python has already calculated the route.

You MUST trust the ROUTE object below.

ROUTE SUCCESS:

{route_exists}

If ROUTE SUCCESS is true:

- Tell the user that the route was found.
- Describe the route briefly.
- Mention distance if available.
- Mention travel mode if available.
- Mention route type if available.
- Do NOT say that the route could not be found.
- Do NOT ask the user to clarify Adyar or Velachery.
- Do NOT invent hospitals, restaurants, streets or landmarks.
- The map route is already being returned separately to the frontend.

If ROUTE SUCCESS is false:

- Explain the actual failure briefly.
- Do not invent a route.

============================================================
IMPORTANT RULES
============================================================

1. Answer only in the detected language.

2. Understand natural multilingual questions.

3. Never ask the user to choose a language.

4. Never tell the user to ask ChatGPT.

5. Never require dropdown selections.

6. Use Neo4j context when relevant.

7. Use ChromaDB context when relevant.

8. Use route information when available.

9. Use weather information when available.

10. Never invent live traffic.

11. Clearly distinguish estimated traffic from live traffic.

12. Never invent places.

13. Never invent hospitals or landmarks.

14. Do not replace the calculated route with a made-up route.

15. Be concise and useful.

16. If a route exists, do not respond with "I couldn't find the route."

============================================================
USER QUESTION
============================================================

{question}

============================================================
USER PREFERENCES
============================================================

{json.dumps(
    preferences,
    ensure_ascii=False
)}

============================================================
CHROMADB GRAPHRAG CONTEXT
============================================================

{json.dumps(
    chroma_context,
    ensure_ascii=False
)}

============================================================
NEO4J KNOWLEDGE GRAPH
============================================================

{json.dumps(
    neo4j_context,
    ensure_ascii=False,
    default=str
)}

============================================================
NEO4J ROUTE CONNECTION CONTEXT
============================================================

{json.dumps(
    route_context,
    ensure_ascii=False,
    default=str
)}

============================================================
CALCULATED ROUTE
============================================================

{json.dumps(
    route,
    ensure_ascii=False,
    default=str
)}

============================================================
WEATHER
============================================================

{json.dumps(
    weather,
    ensure_ascii=False
)}

============================================================
TRAFFIC
============================================================

{json.dumps(
    traffic,
    ensure_ascii=False
)}

============================================================

Now answer the user naturally and concisely
in {language_name} only.

"""

    try:

        print(
            "OLLAMA REQUEST:",
            language_name
        )

        response = requests.post(

            OLLAMA_URL,

            json={

                "model":
                    OLLAMA_MODEL,

                "prompt":
                    prompt,

                "stream":
                    False
            },

            timeout=300
        )

        response.raise_for_status()

        data = response.json()

        answer = data.get(
            "response",
            ""
        ).strip()

        if not answer:

            raise Exception(
                "Ollama returned an empty response."
            )

        print(
            "OLLAMA RESPONSE RECEIVED"
        )

        return answer

    except Exception as e:

        print(
            "OLLAMA ERROR:",
            e
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # Return a useful fallback instead of destroying
        # the route response.
        # ----------------------------------------------------

        if route_exists:

            fallback = {

                "english":
                    "The route has been calculated and is shown on the map.",

                "tamil":
                    "பாதை கணக்கிடப்பட்டு வரைபடத்தில் காட்டப்பட்டுள்ளது.",

                "telugu":
                    "మార్గం కనుగొనబడింది మరియు మ్యాప్‌లో చూపించబడింది.",

                "hindi":
                    "मार्ग खोज लिया गया है और मैप पर दिखाया गया है।",

                "kannada":
                    "ಮಾರ್ಗವನ್ನು ಕಂಡುಹಿಡಿಯಲಾಗಿದೆ ಮತ್ತು ನಕ್ಷೆಯಲ್ಲಿ ತೋರಿಸಲಾಗಿದೆ.",

                "malayalam":
                    "മാർഗം കണ്ടെത്തി മാപ്പിൽ കാണിച്ചിരിക്കുന്നു.",

                "urdu":
                    "راستہ تلاش کر لیا گیا ہے اور نقشے پر دکھایا گیا ہے۔"
            }

            return fallback.get(
                language,
                fallback["english"]
            )

        return {

            "english":
                "I could not connect to Ollama.",

            "tamil":
                "Ollama-வுடன் இணைக்க முடியவில்லை.",

            "telugu":
                "Ollamaకి కనెక్ట్ కాలేకపోయాను.",

            "hindi":
                "मैं Ollama से कनेक्ट नहीं कर सका।",

            "kannada":
                "Ollama ಗೆ ಸಂಪರ್ಕಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ.",

            "malayalam":
                "Ollama-യുമായി ബന്ധപ്പെടാൻ കഴിഞ്ഞില്ല.",

            "urdu":
                "میں Ollama سے رابطہ نہیں کر سکا۔"

        }.get(
            language,
            "I could not connect to Ollama."
        )


# ============================================================
# USER PREFERENCES
# ============================================================

def get_preferences(
    session_id
):

    if session_id not in HISTORY:

        return {}

    return HISTORY[
        session_id
    ].get(
        "preferences",
        {}
    )


def update_preferences(
    session_id,
    language,
    vehicle,
    mode
):

    if session_id not in HISTORY:

        HISTORY[
            session_id
        ] = {

            "messages": [],

            "preferences": {}
        }

    HISTORY[
        session_id
    ][
        "preferences"
    ] = {

        "language":
            language,

        "vehicle":
            vehicle,

        "route_mode":
            mode
    }


def save_message_history(
    session_id,
    message
):

    if session_id not in HISTORY:

        HISTORY[
            session_id
        ] = {

            "messages": [],

            "preferences": {}
        }

    HISTORY[
        session_id
    ][
        "messages"
    ].append({

        "timestamp":
            time.time(),

        "message":
            message
    })

    HISTORY[
        session_id
    ][
        "messages"
    ] = HISTORY[
        session_id
    ][
        "messages"
    ][-50:]

    save_history()


# ============================================================
# CHAT
# ============================================================

@app.post("/chat")
def chat(
    request: ChatRequest
):

    message = request.message.strip()

    if not message:

        return {

            "success":
                False,

            "answer":
                "Please enter a question.",

            "route":
                None
        }

    # --------------------------------------------------------
    # Language
    # --------------------------------------------------------

    language = detect_language(
        message
    )

    # --------------------------------------------------------
    # Vehicle
    # --------------------------------------------------------

    vehicle = detect_vehicle(
        message
    )

    # --------------------------------------------------------
    # Route mode
    # --------------------------------------------------------

    mode = detect_route_mode(
        message
    )

    # --------------------------------------------------------
    # Previous preferences
    # --------------------------------------------------------

    previous = get_preferences(
        request.session_id
    )

    if (
        vehicle == "car"
        and previous.get("vehicle")
    ):

        explicit_vehicle = any(

            word in message.lower()

            for word in [

                "bike",
                "motorbike",
                "scooter",
                "walk",
                "walking",
                "car",

                "கார்",
                "பைக்",
                "ஸ்கூட்டர்",
                "நடந்து",

                "కారు",
                "బైక్",
                "నడిచి",

                "कार",
                "बाइक",
                "पैदल",

                "ಕಾರು",
                "ಬೈಕ್",

                "കാർ",
                "ബൈക്ക്",

                "کار",
                "بائیک",
                "پیدل"
            ]
        )

        if not explicit_vehicle:

            vehicle = previous[
                "vehicle"
            ]

    # --------------------------------------------------------
    # Save preferences
    # --------------------------------------------------------

    update_preferences(

        request.session_id,

        language,

        vehicle,

        mode
    )

    save_message_history(

        request.session_id,

        message
    )

    # --------------------------------------------------------
    # Neo4j user
    # --------------------------------------------------------

    save_user_to_neo4j(

        request.session_id,

        language,

        vehicle,

        mode
    )

    save_chat_to_neo4j(

        request.session_id,

        message,

        language,

        vehicle,

        mode
    )

    # --------------------------------------------------------
    # ChromaDB
    # --------------------------------------------------------

    chroma_context = search_chroma(
        message,
        5
    )

    # --------------------------------------------------------
    # Neo4j
    # --------------------------------------------------------

    neo4j_context = get_graph_context(
        message
    )

    # --------------------------------------------------------
    # Route variables
    # --------------------------------------------------------

    route = None

    route_context = []

    weather = None

    traffic = None

    # --------------------------------------------------------
    # Navigation
    # --------------------------------------------------------

    if is_navigation_question(
        message
    ):

        origin_name, destination_name = (

            extract_route_places(
                message
            )
        )

        if (
            origin_name
            and destination_name
        ):

            print(
                f"ROUTE FOUND IN MESSAGE: "
                f"{origin_name} -> "
                f"{destination_name}"
            )

            route_context = (
                get_neo4j_route_context(

                    origin_name,

                    destination_name
                )
            )

            route = calculate_route(

                origin_name,

                destination_name,

                vehicle,

                mode
            )

            # ------------------------------------------------
            # IMPORTANT:
            # Even if Ollama fails,
            # route remains in response.
            # ------------------------------------------------

            if (
                route
                and route.get("success")
            ):

                destination = route.get(
                    "destination"
                )

                if destination:

                    weather = get_weather(

                        destination[
                            "lat"
                        ],

                        destination[
                            "lon"
                        ]
                    )

                    traffic = get_traffic(

                        destination[
                            "lat"
                        ],

                        destination[
                            "lon"
                        ]
                    )

        else:

            print(
                "ROUTE NOT EXTRACTED"
            )

    # --------------------------------------------------------
    # Weather question
    # --------------------------------------------------------

    if is_weather_question(
        message
    ):

        weather = get_weather(

            12.9300,

            80.2300
        )

    # --------------------------------------------------------
    # Ollama
    # --------------------------------------------------------

    answer = ask_ollama(

        question=
            message,

        language=
            language,

        chroma_context=
            chroma_context,

        neo4j_context=
            neo4j_context,

        route_context=
            route_context,

        weather=
            weather,

        traffic=
            traffic,

        route=
            route,

        preferences=
            get_preferences(
                request.session_id
            )
    )

    # --------------------------------------------------------
    # FINAL RESPONSE
    #
    # The frontend gets route independently of answer.
    # --------------------------------------------------------

    return {

        "success":
            True,

        "answer":
            answer,

        "language":
            language,

        "language_name":
            LANGUAGES[
                language
            ]["name"],

        "vehicle":
            vehicle,

        "mode":
            mode,

        "route":
            route,

        "weather":
            weather,

        "traffic":
            traffic,

        "origin":
            (
                route.get(
                    "origin"
                )
                if route
                else None
            ),

        "destination":
            (
                route.get(
                    "destination"
                )
                if route
                else None
            ),

        "neo4j_used":
            neo4j_driver is not None,

        "chroma_used":
            CHROMA_COLLECTION is not None
    }


# ============================================================
# ROUTE ENDPOINT
# ============================================================

@app.post("/route")
def route_endpoint(
    request: RouteRequest
):

    route = calculate_route(

        request.origin,

        request.destination,

        request.vehicle,

        request.mode
    )

    route_context = (
        get_neo4j_route_context(

            request.origin,

            request.destination
        )
    )

    return {

        **route,

        "neo4j_context":
            route_context
    }


# ============================================================
# WEATHER ENDPOINT
# ============================================================

@app.get("/weather")
def weather_endpoint(

    lat: float = 12.9300,

    lon: float = 80.2300
):

    return get_weather(
        lat,
        lon
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {

        "status":
            "running",

        "graphml":
            GRAPH is not None,

        "neo4j":
            neo4j_driver is not None,

        "chromadb":
            CHROMA_COLLECTION is not None,

        "ollama":
            OLLAMA_URL,

        "ollama_model":
            OLLAMA_MODEL,

        "openweather":
            bool(
                OPENWEATHER_API_KEY
            ),

        "openrouteservice":
            bool(
                ORS_API_KEY
            ),

        "traffic_api":
            bool(
                TOMTOM_API_KEY
            )
    }


# ============================================================
# FRONTEND
# ============================================================

@app.get("/")
def frontend():

    html_file = (

        PROJECT_DIR /

        "frontend" /

        "index.html"
    )

    if html_file.exists():

        return FileResponse(
            html_file
        )

    return {

        "message":
            "GraphRAG backend running."
    }


# ============================================================
# SHUTDOWN
# ============================================================

@app.on_event("shutdown")
def shutdown():

    global neo4j_driver

    if neo4j_driver:

        neo4j_driver.close()