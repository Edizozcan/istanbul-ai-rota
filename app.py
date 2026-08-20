import streamlit as st
import pandas as pd
import math
import json
from datetime import time, timedelta
from geopy.geocoders import Nominatim
import google.generativeai as genai
import folium
from streamlit_folium import folium_static 

# --- GÜVENLİ API AYARLARI ---
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)

# --- 1. MATEMATİKSEL VE OPTİMİZASYON FONKSİYONLARI ---
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)
    dlat, dlon = lat2_rad - lat1_rad, lon2_rad - lon1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c 

def optimize_route(df, start_lat, start_lon, total_minutes):
    unvisited = df.copy()
    current_lat, current_lon = start_lat, start_lon
    route = []
    
    while not unvisited.empty and total_minutes > 0:
        unvisited['distance_km'] = unvisited.apply(
            lambda row: haversine(current_lat, current_lon, row['lat'], row['lon']), axis=1
        )
        nearest = unvisited.loc[unvisited['distance_km'].idxmin()]
        
        # Ortalama şehir içi hızları (Uzaksa araç 25km/s, yakınsa yürüme 4km/s)
        if nearest['distance_km'] > 3.0:
            travel_time = int((nearest['distance_km'] / 25.0) * 60)
        else:
            travel_time = int((nearest['distance_km'] / 4.0) * 60)
            
        if travel_time < 2: travel_time = 2
            
        time_needed = travel_time + nearest['ort_sure']
        
        if total_minutes >= time_needed:
            nearest_dict = nearest.to_dict()
            nearest_dict['travel_time'] = travel_time
            route.append(nearest_dict)
            
            total_minutes -= time_needed
            current_lat, current_lon = nearest['lat'], nearest['lon']
            unvisited = unvisited.drop(nearest.name)
        else:
            break
            
    return pd.DataFrame(route)

# --- 2. YAPAY ZEKA VERİ ÜRETİM MOTORU ---
def get_dynamic_places_from_ai(target_city, user_prompt):
    """Veritabanı yerine tüm dünyayı kapsayan dinamik JSON üretici."""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash') 
    except Exception as e:
        st.error(f"Model yüklenirken hata oluştu: {e}")
        return pd.DataFrame()
    
    prompt = f"""
    Sen küresel bir seyahat planlama API'sisin ve coğrafi sistemler (GIS) uzmanısın.
    Kullanıcı şu an '{target_city}' şehrine seyahat ediyor.
    Kullanıcının özel isteği: "{user_prompt}"
    
    Lütfen bu şehirden, kullanıcının isteğine en uygun, haritada çizilebilecek ve mantıklı bir rota oluşturulabilecek en iyi 6-8 mekanı belirle.
    Bu mekanların ENLEM (lat) ve BOYLAM (lon) koordinatlarını gerçeğe en yakın şekilde (noktadan sonra en az 4 hane) sağla.
    Kategori olarak: Tarihi, Müze, Gastronomi, Doğa, Eğlence vb. kullan.
    Ortalama ziyaret süresini (ort_sure) dakika cinsinden (tamsayı) belirle.
    
    SADECE VE SADECE AŞAĞIDAKİ JSON FORMATINDA ÇIKTI VER. BAŞKA HİÇBİR KELİME, GİRİŞ VEYA AÇIKLAMA YAZMA:
    [
        {{"name": "Charles Bridge", "lat": 50.0865, "lon": 14.4114, "kategori": "Tarihi", "ort_sure": 45}},
        {{"name": "Prague Castle", "lat": 50.0903, "lon": 14.3996, "kategori": "Müze", "ort_sure": 120}}
    ]
    """
    
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        
   # LLM'lerin eklediği Markdown etiketlerini tek satırda güvenle temizliyoruz
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
