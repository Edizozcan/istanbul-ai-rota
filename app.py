import streamlit as st
import pandas as pd
import sqlite3
import math
from datetime import time, timedelta
from geopy.geocoders import Nominatim
import google.generativeai as genai
import folium
from streamlit_folium import folium_static 

# --- GÜVENLİ API AYARLARI ---
# Artık anahtarı doğrudan yazmıyoruz, Streamlit'in gizli kasasından çekiyoruz.
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_API_KEY)

# --- 1. FONKSİYONLAR ---
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

def load_data():
    conn = sqlite3.connect("istanbul_mekanlar.db")
    df = pd.read_sql_query("SELECT isim as name, enlem as lat, boylam as lon, kategori, ort_sure FROM mekanlar", conn)
    conn.close()
    return df

def get_ai_recommendations(user_prompt, available_places):
    try:
        model = genai.GenerativeModel('gemini-3.6-flash')
    except Exception as e:
        st.error(f"Model yüklenirken bir hata oluştu: {e}")
        return []
    
    places_str = "\n".join([f"- {row['name']} ({row['kategori']})" for index, row in available_places.iterrows()])
    
    prompt = f"""
    Sen profesyonel bir İstanbul seyahat rehberisin.
    Kullanıcının özel isteği: "{user_prompt}"
    
    Elimizdeki gezilebilir kaliteli mekanlar şunlar:
    {places_str}
    
    Lütfen kullanıcının isteğine en uygun 4 veya 5 mekanı seç.
    SADECE seçtiğin mekanların isimlerini virgülle ayırarak yaz. Başka hiçbir açıklama, giriş cümlesi veya kelime ekleme.
    Örnek çıktı: Ayasofya-i Kebir Cami-i Şerifi, Yerebatan Sarnıcı, Tarihi Sultanahmet Köftecisi
    """
    
    try:
        response = model.generate_content(prompt)
        selected_places = [place.strip() for place in response.text.split(",")]
        return selected_places
    except Exception as e:
        st.error(f"Yapay zeka ile iletişim kurulamadı: {e}")
        return []

# --- 2. ARAYÜZ VE GİRDİLER ---
mekanlar_df = load_data()
st.set_page_config(page_title="İstanbul Akıllı Rota Planlayıcı", layout="wide", initial_sidebar_state="expanded")

with st.sidebar:
    st.header("📍 Seyahat Detayları")
    start_location = st.text_input("Güne Başlama Noktası", value="Beşiktaş")
    
    st.subheader("Zaman Planı")
    col1, col2 = st.columns(2)
    with col1:
        start_time = st.time_input("Başlangıç", time(10, 0))
    with col2:
        end_time = st.time_input("Bitiş", time(19, 0))
        
    st.markdown("---")
    st.subheader("🤖 Yapay Zeka Asistanı")
    user_ai_prompt = st.text_area(
        "Nasıl bir gün geçirmek istersin?", 
        placeholder="Örn: Deniz havası alabileceğim, sakin bir gün geçirmek istiyorum. Akşam da şık bir yerde yemek yiyeyim.",
        height=100
    )
    
    st.markdown("---")
    generate_btn = st.button("Rotamı Oluştur 🚀", use_container_width=True)

# --- 3. ANA UYGULAMA MANTIĞI ---
st.title("🗺️ İstanbul Akıllı Rota Planlayıcı (AI Destekli)")

if generate_btn:
    if not user_ai_prompt:
        st.error("Lütfen yapay zeka asistanına nasıl bir gün geçirmek istediğinizi yazın!")
    else:
        with st.spinner("🧠 Yapay zeka rotanızı tasarlıyor..."):
            ai_selected_names = get_ai_recommendations(user_ai_prompt, mekanlar_df)
            filtrelenmis_df = mekanlar_df[mekanlar_df['name'].isin(ai_selected_names)].copy()
            
            if filtrelenmis_df.empty:
                st.warning("Yapay zeka isteğinize uygun bir mekan bulamadı. Lütfen isteğinizi değiştirin.")
            else:
                geolocator = Nominatim(user_agent="istanbul_rota_app")
                try:
                    location = geolocator.geocode(start_location + ", İstanbul, Türkiye")
                    if location:
                        baslangic_lat, baslangic_lon = location.latitude, location.longitude
                        st.success(f"📍 Başlangıç Tespit Edildi: {location.address}")
                    else:
                        st.warning("Konum bulunamadı, varsayılan olarak Karaköy baz alınıyor.")
                        baslangic_lat, baslangic_lon = 41.0223, 28.9753
                except:
                    st.warning("Konum servisine ulaşılamadı, Karaköy baz alınıyor.")
                    baslangic_lat, baslangic_lon = 41.0223, 28.9753

                start_dt = pd.Timestamp(f"2000-01-01 {start_time}")
                end_dt = pd.Timestamp(f"2000-01-01 {end_time}")
                total_available_minutes = int((end_dt - start_dt).total_seconds() / 60)
                
                gunluk_rota = optimize_route(filtrelenmis_df, baslangic_lat, baslangic_lon, total_available_minutes)
                
                if gunluk_rota.empty:
                    st.warning("Zaman aralığınız bu rotayı gezmek için yeterli değil!")
                else:
                    st.balloons()
                    map_col, timeline_col = st.columns([3, 2])
                    
                    with map_col:
                        st.subheader("Günün Rotası")
                        
                        map_center = [gunluk_rota['lat'].mean(), gunluk_rota['lon'].mean()]
                        m = folium.Map(location=map_center, zoom_start=13)
                        
                        route_coords = []
                        
                        for idx, row in gunluk_rota.iterrows():
                            coord = [row['lat'], row['lon']]
                            route_coords.append(coord)
                            
                            folium.Marker(
                                location=coord,
                                tooltip=f"{idx+1}. {row['name']}",
                                popup=folium.Popup(f"<b>{idx+1}. Durak:</b> {row['name']}<br><i>{row['kategori']}</i>", max_width=250),
                                icon=folium.Icon(color="red", icon="info-sign")
                            ).add_to(m)
                            
                        folium.PolyLine(
                            locations=route_coords,
                            color="blue",
                            weight=4,
                            opacity=0.7,
                            dash_array='10' 
                        ).add_to(m)
                        
                        folium_static(m, width=600, height=500)
                        
                    with timeline_col:
                        st.subheader("Zaman Çizelgesi")
                        current_time = start_dt
                        st.info(f"**{current_time.strftime('%H:%M')}** | 🚶‍♂️ Güne Başlangıç\n\n*{start_location} konumundan hareket*")
                        
                        for idx, row in gunluk_rota.iterrows():
                            current_time += timedelta(minutes=int(row['travel_time']))
                            varis_saati = current_time.strftime('%H:%M')
                            
                            current_time += timedelta(minutes=int(row['ort_sure']))
                            cikis_saati = current_time.strftime('%H:%M')
                            
                            st.warning(
                                f"**{varis_saati} - {cikis_saati}** | 📍 {idx+1}. {row['name']}\n\n"
                                f"*Kategori: {row['kategori']} | Geçiş: {int(row['travel_time'])} dk | Ziyaret: {row['ort_sure']} dk*"
                            )