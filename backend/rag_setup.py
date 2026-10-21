import os
import json
import re
import xml.etree.ElementTree as ET

import chromadb

OSM_FILE = "data/south_chennai.osm"
CHROMA_DIR = "chroma_db"

COLLECTION_NAME = "south_chennai_context"


# ---------------------------------------------------------
# CLEAN TEXT
# ---------------------------------------------------------

def clean_text(value):

    if value is None:
        return ""

    value = str(value)

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


# ---------------------------------------------------------
# EXTRACT OSM DATA
# ---------------------------------------------------------

def extract_osm_context():

    print()
    print("Reading South Chennai OSM data...")

    tree = ET.parse(OSM_FILE)
    root = tree.getroot()

    node_coordinates = {}

    for node in root.findall("node"):

        node_id = node.attrib.get("id")

        lat = node.attrib.get("lat")
        lon = node.attrib.get("lon")

        if lat and lon:
            node_coordinates[node_id] = (
                float(lat),
                float(lon)
            )

    documents = []
    ids = []
    metadata = []

    # -----------------------------------------------------
    # ROADS
    # -----------------------------------------------------

    road_count = 0

    for way in root.findall("way"):

        tags = {}

        for tag in way.findall("tag"):

            key = tag.attrib.get("k")
            value = tag.attrib.get("v")

            if key and value:
                tags[key] = value

        highway = tags.get("highway")

        if not highway:
            continue

        name = tags.get(
            "name",
            "Unnamed Road"
        )

        text = (
            f"Road: {name}. "
            f"Road type: {highway}. "
            f"Location: South Chennai."
        )

        documents.append(text)

        ids.append(
            f"road_{road_count}"
        )

        metadata.append({
            "type": "road",
            "name": name,
            "highway": highway
        })

        road_count += 1

    # -----------------------------------------------------
    # POI NODES
    # -----------------------------------------------------

    poi_count = 0

    poi_tags = [
        "amenity",
        "shop",
        "tourism",
        "leisure",
        "healthcare",
        "public_transport",
        "railway",
        "office",
        "education"
    ]

    for node in root.findall("node"):

        tags = {}

        for tag in node.findall("tag"):

            key = tag.attrib.get("k")
            value = tag.attrib.get("v")

            if key and value:
                tags[key] = value

        matching_type = None

        for key in poi_tags:

            if key in tags:

                matching_type = key
                break

        if not matching_type:
            continue

        name = tags.get(
            "name",
            "Unnamed place"
        )

        lat = node.attrib.get("lat")
        lon = node.attrib.get("lon")

        if not lat or not lon:
            continue

        text = (
            f"POI: {name}. "
            f"Category: {matching_type}. "
            f"Type: {tags.get(matching_type)}. "
            f"Location: South Chennai. "
            f"Coordinates: {lat}, {lon}."
        )

        documents.append(text)

        ids.append(
            f"poi_{poi_count}"
        )

        metadata.append({
            "type": "poi",
            "name": name,
            "category": matching_type,
            "poi_type": tags.get(matching_type),
            "lat": float(lat),
            "lon": float(lon)
        })

        poi_count += 1

    print(
        f"Road entries : {road_count}"
    )

    print(
        f"POI entries  : {poi_count}"
    )

    print(
        f"Total RAG entries : {len(documents)}"
    )

    return documents, ids, metadata


# ---------------------------------------------------------
# BUILD CHROMA
# ---------------------------------------------------------

def build_index(
    documents,
    ids,
    metadata
):

    print()
    print("Building ChromaDB...")

    client = chromadb.PersistentClient(
        path=CHROMA_DIR
    )

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME
    )

    # Delete previous collection contents.
    existing = collection.get()

    if existing and existing.get("ids"):

        collection.delete(
            ids=existing["ids"]
        )

    batch_size = 500

    for i in range(
        0,
        len(documents),
        batch_size
    ):

        collection.add(
            documents=documents[
                i:i + batch_size
            ],
            ids=ids[
                i:i + batch_size
            ],
            metadatas=metadata[
                i:i + batch_size
            ]
        )

        print(
            f"Indexed "
            f"{min(i + batch_size, len(documents))}"
            f"/{len(documents)}"
        )

    print(
        f"ChromaDB ready: {CHROMA_DIR}"
    )


# ---------------------------------------------------------
# RETRIEVE CONTEXT
# ---------------------------------------------------------

def retrieve_context(
    query,
    n_results=8
):

    client = chromadb.PersistentClient(
        path=CHROMA_DIR
    )

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME
    )

    if collection.count() == 0:

        return {
            "pois": [],
            "roads": []
        }

    result = collection.query(
        query_texts=[query],
        n_results=min(
            n_results,
            collection.count()
        )
    )

    documents = result.get(
        "documents",
        [[]]
    )[0]

    metadatas = result.get(
        "metadatas",
        [[]]
    )[0]

    pois = []
    roads = []

    for doc, meta in zip(
        documents,
        metadatas
    ):

        item = {
            "text": doc,
            "metadata": meta
        }

        if meta.get("type") == "poi":
            pois.append(item)
        else:
            roads.append(item)

    return {
        "pois": pois,
        "roads": roads
    }


# ---------------------------------------------------------
# HISTORY INTO RAG
# ---------------------------------------------------------

def add_history_entry(
    entry_id,
    text,
    metadata
):

    client = chromadb.PersistentClient(
        path=CHROMA_DIR
    )

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME
    )

    collection.add(
        documents=[text],
        ids=[
            f"history_{entry_id}"
        ],
        metadatas=[
            {
                "type": "history",
                **metadata
            }
        ]
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

if __name__ == "__main__":

    docs, ids, metadata = (
        extract_osm_context()
    )

    build_index(
        docs,
        ids,
        metadata
    )

    print()
    print("RAG SETUP COMPLETE.")