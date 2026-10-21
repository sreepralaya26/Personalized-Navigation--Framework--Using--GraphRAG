import os
import math
import sys
import json
import requests
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

# Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "Sree_2005")

# Bounding box for South Chennai (Guindy, Velachery, Adyar, Thiruvanmiyur, OMR)
BBOX = "12.90,80.20,13.01,80.28"

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points in meters."""
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

def fetch_osm_data():
    """Query Overpass API for South Chennai roads and POIs (optimized)."""
    print("[1/6] Fetching roadmap data from OpenStreetMap Overpass API for South Chennai...")
    headers = {
        "User-Agent": "GraphRAGNavigationBot/1.0 (https://github.com/user/graphrag-navigation)",
        "Accept-Encoding": "gzip, deflate"
    }
    
    overpass_url = "https://lz4.overpass-api.de/api/interpreter"
    
    # Optimized query: Omit "residential" streets to decrease search load by 90%
    query = f"""
    [out:json][timeout:90];
    (
      way["highway"~"motorway|trunk|primary|secondary|tertiary"]({BBOX});
      node["amenity"~"restaurant|cafe|hospital|fuel|pharmacy"]({BBOX});
      node["tourism"~"attraction"]({BBOX});
    );
    out body;
    >;
    out skel qt;
    """
    
    try:
        response = requests.post(overpass_url, data={'data': query}, headers=headers, timeout=60)
        response.raise_for_status()
        print("[1/6] Successfully downloaded map data from primary mirror.")
        return response.json()
    except Exception as e:
        print(f"[1/6] Primary mirror failed: {e}. Trying fallback server...")
        try:
            fallback_url = "https://overpass-api.de/api/interpreter"
            response = requests.post(fallback_url, data={'data': query}, headers=headers, timeout=60)
            response.raise_for_status()
            print("[1/6] Successfully downloaded map data from fallback server.")
            return response.json()
        except Exception as ex:
            print(f"[1/6] Error downloading map data: {ex}")
            return None

def clear_neo4j_database(driver):
    """Clear all nodes and relationships in the Neo4j database."""
    print("[3/6] Clearing all existing nodes/relationships in Neo4j database...")
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    print("[3/6] Neo4j database cleared successfully.")

def build_graph_in_neo4j(osm_data):
    if not osm_data or 'elements' not in osm_data:
        print("[Error] No valid OSM data elements found.")
        return
        
    print(f"[2/6] Connecting to Neo4j instance at {NEO4J_URI}...")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        print("[2/6] Connection verified successfully.")
    except Exception as e:
        print(f"[Error] Failed to connect to Neo4j: {e}")
        print("Please check that Neo4j is running and your .env credentials are correct.")
        return

    # Clear database
    clear_neo4j_database(driver)

    elements = osm_data['elements']
    
    # Parse elements
    nodes = {}
    ways = []
    pois = []
    
    for elem in elements:
        elem_type = elem.get('type')
        if elem_type == 'node':
            node_id = elem['id']
            lat = elem['lat']
            lon = elem['lon']
            tags = elem.get('tags', {})
            
            nodes[node_id] = {
                'id': node_id,
                'lat': lat,
                'lon': lon,
                'name': tags.get('name', ''),
                'tags': tags
            }
            
            if 'amenity' in tags:
                pois.append({
                    'id': node_id,
                    'name': tags.get('name', f"Unnamed {tags['amenity']}"),
                    'type': tags['amenity'],
                    'lat': lat,
                    'lon': lon,
                    'tags': tags
                })
        elif elem_type == 'way':
            ways.append({
                'id': elem['id'],
                'nodes': elem['nodes'],
                'tags': elem.get('tags', {})
            })

    print(f"[4/6] Parsing elements finished. Found {len(nodes)} nodes, {len(ways)} road ways, and {len(pois)} POIs.")

    # Write Intersections (Nodes)
    print("[5/6] Ingesting Intersection nodes into Neo4j...")
    with driver.session() as session:
        try:
            session.run("CREATE CONSTRAINT FOR (i:Intersection) REQUIRE i.id IS UNIQUE")
            session.run("CREATE CONSTRAINT FOR (p:POI) REQUIRE p.id IS UNIQUE")
        except Exception:
            pass

        node_query = """
        UNWIND $batch AS node
        MERGE (i:Intersection {id: node.id})
        SET i.lat = node.lat,
            i.lon = node.lon,
            i.name = node.name
        """
        
        nodes_list = list(nodes.values())
        for i in range(0, len(nodes_list), 1000):
            chunk = nodes_list[i:i+1000]
            session.run(node_query, batch=[{
                'id': str(n['id']),
                'lat': float(n['lat']),
                'lon': float(n['lon']),
                'name': n['name']
            } for n in chunk])
        print(f"[5/6] Intersection nodes write complete. Count: {len(nodes_list)}")

    # Write Roads (Relationships)
    print("[5/6] Connecting intersections with ROAD relationships...")
    road_relationships = []
    for way in ways:
        way_nodes = way['nodes']
        tags = way['tags']
        road_name = tags.get('name', 'Unnamed Road')
        highway_type = tags.get('highway', 'residential')
        one_way = tags.get('oneway', 'no') == 'yes'
        
        for idx in range(len(way_nodes) - 1):
            n1_id = way_nodes[idx]
            n2_id = way_nodes[idx+1]
            
            if n1_id in nodes and n2_id in nodes:
                n1 = nodes[n1_id]
                n2 = nodes[n2_id]
                dist = haversine_distance(n1['lat'], n1['lon'], n2['lat'], n2['lon'])
                
                road_relationships.append({
                    'from': str(n1_id),
                    'to': str(n2_id),
                    'name': road_name,
                    'type': highway_type,
                    'distance': dist,
                    'oneway': one_way
                })

    with driver.session() as session:
        rel_query = """
        UNWIND $batch AS rel
        MATCH (a:Intersection {id: rel.from})
        MATCH (b:Intersection {id: rel.to})
        MERGE (a)-[r:ROAD {name: rel.name, type: rel.type}]->(b)
        SET r.distance = rel.distance
        """
        
        for i in range(0, len(road_relationships), 1000):
            chunk = road_relationships[i:i+1000]
            session.run(rel_query, batch=chunk)
            
            two_way_chunk = [
                {
                    'from': r['to'],
                    'to': r['from'],
                    'name': r['name'],
                    'type': r['type'],
                    'distance': r['distance']
                }
                for r in chunk if not r['oneway']
            ]
            if two_way_chunk:
                session.run(rel_query, batch=two_way_chunk)
        print(f"[5/6] Road network connections write complete. Count: {len(road_relationships)}")

    # Write POIs
    print("[5/6] Ingesting POI nodes into Neo4j...")
    poi_data = []
    for poi in pois:
        poi_data.append({
            'id': str(poi['id']),
            'name': poi['name'],
            'type': poi['type'],
            'lat': float(poi['lat']),
            'lon': float(poi['lon'])
        })

    with driver.session() as session:
        poi_query = """
        UNWIND $batch AS poi
        MERGE (p:POI {id: poi.id})
        SET p.name = poi.name,
            p.type = poi.type,
            p.lat = poi.lat,
            p.lon = poi.lon
        """
        for i in range(0, len(poi_data), 1000):
            chunk = poi_data[i:i+1000]
            session.run(poi_query, batch=chunk)
        print(f"[5/6] POI nodes write complete. Count: {len(poi_data)}")

        print("[6/6] Linking POI nodes to their nearest road intersections (within 300m)...")
        link_query = """
        MATCH (p:POI)
        MATCH (i:Intersection)
        WITH p, i, 
             point({latitude: p.lat, longitude: p.lon}) AS pPoint, 
             point({latitude: i.lat, longitude: i.lon}) AS iPoint
        WITH p, i, point.distance(pPoint, iPoint) AS dist
        WHERE dist < 300
        WITH p, i, dist ORDER BY dist ASC
        WITH p, collect({node: i, dist: dist})[0] AS nearest
        WHERE nearest IS NOT NULL
        WITH p, nearest.node AS i, nearest.dist AS dist
        MERGE (p)-[r:NEARBY]->(i)
        SET r.distance = dist
        """
        session.run(link_query)
        print("[6/6] Nearest node connection linking complete.")

    print("[Finished] Graph built successfully in Neo4j!")
    driver.close()

if __name__ == "__main__":
    osm_data = None
    if len(sys.argv) > 1:
        local_file = sys.argv[1]
        print(f"Loading local OSM data file from: {local_file}")
        try:
            with open(local_file, "r", encoding="utf-8") as f:
                osm_data = json.load(f)
            print("Successfully loaded JSON map data locally.")
        except Exception as e:
            print(f"[Error] Reading local file failed: {e}")
            sys.exit(1)
    else:
        osm_data = fetch_osm_data()
        
    if osm_data:
        build_graph_in_neo4j(osm_data)
    else:
        print("[Error] Failed to acquire roadmap data.")