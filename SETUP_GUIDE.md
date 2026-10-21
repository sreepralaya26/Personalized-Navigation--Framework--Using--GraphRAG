# Chennai Genie - Backend Setup (step by step)

Follow these in order. Every step has a comment on WHY it's needed.

## Step 1 - Python environment
```bash
cd chennai-genie/backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt --break-system-packages
```

## Step 2 - Install & start Ollama (local LLM, powers the RAG chatbot)
```bash
# Download from https://ollama.com if not installed, then:
ollama pull llama3.2      # or: ollama pull mistral
ollama serve               # keep this terminal running in the background
```

## Step 3 - Build the road network graph (run ONCE, or whenever you refresh OSM data)
```bash
# Option A: pull fresh from Overpass for the whole city
python graph_builder.py --place "Chennai, Tamil Nadu, India"

# Option B: use YOUR existing Overpass Turbo export (South Chennai extract)
python graph_builder.py --osm-file data/south_chennai.osm
```
This creates `storage/road_graph.graphml` and `storage/road_graph.pkl`.
Every later run just loads the cached pickle - fast startup.

## Step 4 - Drop your knowledge base files into `backend/data/`
Any/all of these (see rag_engine.py header for exact column names):
```
data/pois.csv
data/od_matrix.csv
data/traffic_notes.csv
```

## Step 5 - Build the RAG vector index (run once, or after updating data/)
```bash
python rag_engine.py --rebuild-index
```

## Step 6 - Initialize the history database (usually auto-runs, but you can do it manually)
```bash
python history_store.py
```

## Step 7 - Start the API server
```bash
uvicorn main:app --reload --port 8000
```
Visit http://localhost:8000/docs for the interactive Swagger UI to test every
endpoint (chat, voice, route, weather, history) directly in the browser.

## Step 8 - Point the React frontend at it
In `frontend/.env` set:
```
VITE_API_BASE_URL=http://localhost:8000
```

---
### Quick sanity test (no frontend needed)
```bash
curl -X POST http://localhost:8000/chat/text \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test1","message":"Best route from Anna Nagar to Marina Beach?","language":"en"}'
```
