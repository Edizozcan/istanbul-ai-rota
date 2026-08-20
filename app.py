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
    try:
        model = genai.GenerativeModel('gemini-3.6-flash') 
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
        
        ticks = chr(96) * 3
        raw_text = raw_text.replace(ticks + "json", "").replace(ticks, "").strip()
            
        places_list = json.loads(raw_text)
        return pd.DataFrame(places_list)
    except Exception as e:
        st.error(f"Yapay zeka veriyi işlerken bir hata yaşadı (Lütfen tekrar deneyin): {e}")
        return pd.DataFrame()

# --- 3. ARAYÜZ VE GİRDİLER ---
st.set_page_config(page_title="Küresel Akıllı Rota Planlayıcı", layout="wide", initial_sidebar_state="expanded")

with st.sidebar:
    st.header("🌍 Hedef ve Seyahat")
    
    target_city = st.text_input("Gidilecek Şehir", value="Prag, Çekya", help="Örn: Roma, Tokyo, Kapadokya")
    start_location = st.text_input("Başlangıç Noktası (Otel vb.)", value="Old Town Square", help="Şehirdeki oteliniz veya ilk durağınız")
    
    st.subheader("Zaman Planı")
    col1, col2 = st.columns(2)
    with col1:
        start_time = st.time_input("Başlangıç", time(9, 0))
    with col2:
        end_time = st.time_input("Bitiş", time(19, 0))
        
    st.markdown("---")
    st.subheader("🤖 Yapay Zeka Asistanı")
    user_ai_prompt = st.text_area(
        "Nasıl bir gün geçirmek istersin?", 
        placeholder="Örn: Prag'ın tarihi sokaklarında kaybolmak, yerel tatlılar denemek ve günü nehir kenarında bitirmek istiyorum.",
        height=100
    )
    
    st.markdown("---")
    generate_btn = st.button("Küresel Rotamı Oluştur 🚀", use_container_width=True)

# --- 4. ANA UYGULAMA MANTIĞI ---
st.title("🗺️ Küresel Akıllı Rota Planlayıcı (AI & JSON Destekli)")
st.caption("Veritabanı olmadan, dünyanın herhangi bir şehri için anlık rota üretimi.")

if generate_btn:
    if not user_ai_prompt or not target_city:
        st.error("Lütfen gideceğiniz şehri ve nasıl bir gün geçirmek istediğinizi yazın!")
    else:
        with st.spinner(f"🧠 Yapay zeka {target_city} için tüm verileri baştan üretiyor (Bu işlem 5-10 saniye sürebilir)..."):
            
            dinamik_df = get_dynamic_places_from_ai(target_city, user_ai_prompt)
            
            if dinamik_df.empty:
                st.warning("Veri çekilemedi. Lütfen farklı bir prompt ile tekrar deneyin.")
            else:
                # Zaman aşımını (timeout) artırdık ve özel bir user_agent tanımladık
                geolocator = Nominatim(user_agent="kontrol_atak_global_rota_v1", timeout=10)
                try:
                    search_query = f"{start_location}, {target_city}"
                    location = geolocator.geocode(search_query)
                    
                    if location:
                        baslangic_lat, baslangic_lon = location.latitude, location.longitude
                        st.success(f"📍 Başlangıç Tespit Edildi: {location.address}")
                    else:
                        st.warning(f"Otel konumu tam bulunamadı, yapay zekanın önerdiği ilk mekan başlangıç kabul ediliyor.")
                        baslangic_lat = dinamik_df.iloc[0]['lat']
                        baslangic_lon = dinamik_df.iloc[0]['lon']
                except Exception as e:
                    # ÇÖKME KORUMASI: Harita API'si çökse bile program çalışmaya devam edecek!
                    st.warning("Harita arama servisi şu an yoğun. Başlangıç noktası olarak rotadaki ilk mekan baz alınıyor.")
                    baslangic_lat = dinamik_df.iloc[0]['lat']
                    baslangic_lon = dinamik_df.iloc[0]['lon']

                start_dt = pd.Timestamp(f"2000-01-01 {start_time}")
                end_dt = pd.Timestamp(f"2000-01-01 {end_time}")
                total_available_minutes = int((end_dt - start_dt).total_seconds() / 60)
                
                gunluk_rota = optimize_route(dinamik_df, baslangic_lat, baslangic_lon, total_available_minutes)
                
                if gunluk_rota.empty:
                    st.warning("Zaman aralığınız bu rotayı gezmek için yeterli değil!")
                else:
                    st.balloons()
                    map_col, timeline_col = st.columns([3, 2])
                    
                    with map_col:
                        st.subheader(f"{target_city} Günlük Rotası")
                        
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
                                icon=folium.Icon(color="darkblue", icon="info-sign")
                            ).add_to(m)
                            
                        folium.PolyLine(
                            locations=route_coords,
                            color="red",
                            weight=4,
                            opacity=0.7,
                            dash_array='10' 
                        ).add_to(m)
                        
                        folium_static(m, width=600, height=500)
                        
                    with timeline_col:
                        st.subheader("Zaman Çizelgesi")
                        current_time = start_dt
                        st.info(f"**{current_time.strftime('%H:%M')}** | 🚶‍♂️ Güne Başlangıç\n\n*{start_location} ({target_city}) civarından hareket*")
                        
                        for idx, row in gunluk_rota.iterrows():
                            current_time += timedelta(minutes=int(row['travel_time']))
                            varis_saati = current_time.strftime('%H:%M')
                            
                            current_time += timedelta(minutes=int(row['ort_sure']))
                            cikis_saati = current_time.strftime('%H:%M')
                            
                            st.warning(
                                f"**{varis_saati} - {cikis_saati}** | 📍 {idx+1}. {row['name']}\n\n"
                                f"*Kategori: {row['kategori']} | Geçiş: {int(row['travel_time'])} dk | Ziyaret: {row['ort_sure']} dk*"
                            )
