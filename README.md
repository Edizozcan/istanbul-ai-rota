# 🌍 Global Route Planner V2 (AI-Powered)

An autonomous, intelligent travel planning assistant built with Streamlit and powered by Google Gemini AI. This application creates highly optimized, multi-city travel itineraries with dynamic cost estimation and smart caching.

## 🚀 Key Features

*   **AI-Driven Routing:** Leverages Google Gemini API to generate logical daily routines, historical insights, and time-optimized travel flows.
*   **Smart Database Caching:** Integrates with Supabase (PostgreSQL) to cache previously generated routes. This reduces API dependency, lowers costs to near zero, and delivers instant results for popular queries.
*   **Dynamic Cost Estimation:** Uses Flixbus API and OSRM (Open Source Routing Machine) failsafe mechanics to calculate intercity travel costs and distances in real-time.
*   **Interactive Mapping:** Generates intercontinental tour maps and daily city markers using Folium and OpenStreetMap.
*   **Export & Share:** Instantly exports the AI-generated itinerary as a professionally formatted PDF brochure (ReportLab) or an `.ics` calendar file for Apple/Google Calendar integration.
*   **Bilingual Support:** Seamlessly switches between English and Turkish UI/Outputs.

## 🛠️ Tech Stack

*   **Frontend & Framework:** Python, Streamlit
*   **Database & Cache:** Supabase (PostgreSQL)
*   **AI & Logic:** Google Generative AI (Gemini 1.5 Pro)
*   **Mapping:** Folium, Streamlit-Folium
*   **Document Generation:** ReportLab (PDF)
*   **APIs:** OSRM, Flixbus (Custom Integration)

## ⚙️ Architecture Workflow

1.  **User Input:** The user selects origin, destination, budget constraints, and travel pace.
2.  **Cache Check:** The system queries Supabase. If the exact route combo exists, it instantly pulls the data (Cache Hit).
3.  **AI Generation:** If the route is new (Cache Miss), the system constructs a precise JSON prompt and sends it to Gemini.
4.  **Data Processing:** The AI response is parsed, map coordinates are fetched, and the results are simultaneously displayed on the UI and saved to Supabase for future users.

## 💻 Local Installation

To run this project locally, clone the repository and install the dependencies:

```bash
git clone [https://github.com/Edizozcan/istanbul-ai-rota.git](https://github.com/Edizozcan/istanbul-ai-rota.git)
cd istanbul-ai-rota
pip install -r requirements.txt


You will need to create a .streamlit/secrets.toml file in the root directory and add your API keys:

GEMINI_API_KEY = "your_google_gemini_key"
SUPABASE_URL = "your_supabase_url"
SUPABASE_KEY = "your_supabase_key"

Then, run the app:
streamlit run app.py


This project is for educational and portfolio purposes.
