import streamlit as st
import pandas as pd
import math
import json
from datetime import time, timedelta
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

# --- 2. YAPAY ZEKA ÇOKLU GÜN VERİ ÜRETİM MOTORU ---
def get_multi_day_plan_from_ai(user_prompt):
    """Kullanıcının isteğini günlere ve şehirlere bölen kompleks JSON üretici."""
    try:
        model = genai.GenerativeModel('gemini-3.6-flash') 
    except Exception as e:
        st.error(f"Model yüklenirken hata oluştu: {e}")
        return []
    
    prompt = f"""
    Sen küresel bir seyahat planlama API'sisin.
    Kullanıcının özel isteği: "{user_prompt}"
    
    Lütfen bu isteğe uygun olarak GÜN GÜN planlanmış mantıklı bir seyahat rotası oluştur.
    Her gün için o gün bulunulan şehri ve gezilecek 5-6 mekanı belirle.
    Mekanların ENLEM (lat) ve BOYLAM (lon) koordinatlarını gerçeğe en yakın şekilde (noktadan sonra en az 4 hane) sağla.
    Kategori olarak: Tarihi, Müze, Gastronomi, Doğa vb. kullan. Ziyaret süresini (ort_sure) dakika olarak belirle.
    
    SADECE VE SADECE AŞAĞIDAKİ JSON FORMATINDA ÇIKTI VER. BAŞKA HİÇBİR AÇIKLAMA YAZMA:
    [
        {{
            "gun": 1,
            "sehir": "Prag",
            "mekanlar": [
                {{"name": "Charles Bridge", "lat": 50.0865, "lon": 14.4114, "kategori": "Tarihi", "ort_sure": 45}},
                {{"name": "Prague Castle", "lat": 50.0903, "lon": 14.3996, "kategori": "Müze", "ort_sure": 120}}
            ]
        }},
        {{
            "gun": 2,
            "sehir": "Viyana",
            "mekanlar": [
                {{"name": "Schönbrunn Sarayı", "lat": 48.1849, "lon": 16.3122, "kategori": "Tarihi", "ort_sure": 180}}
            ]
        }}
    ]
    """
    
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        
        ticks = chr(96) * 3
        raw_text = raw_text.replace(ticks + "json", "").replace(ticks, "").strip()
            
        plan_data = json.loads(raw_text)
        return plan_data
    except Exception as e:
        st.error(f"Yapay zeka planı oluştururken bir hata yaşadı: {e}")
        return []

# --- 3. ARAYÜZ VE GİRDİLER ---
st.set_page_config(page_title="Global Rota Planlayıcı V2", layout="wide", initial_sidebar_state="expanded")

with st.sidebar:
    st.header("🌍 Büyük Avrupa Turu")
    
    st.subheader("Günlük Zaman Planı")
    col1, col2 = st.columns(2)
    with col1:
        start_time = st.time_input("Mesai Başlangıcı", time(9, 0))
    with col2:
        end_time = st.time_input("Mesai Bitişi", time(20, 0))
        
    st.markdown("---")
    st.subheader("🤖 Yapay Zeka Asistanı")
    user_ai_prompt = st.text_area(
        "Tüm seyahat planını detaylıca yaz:", 
        placeholder="Örn: 3 gün Prag, 2 gün Viyana, 2 gün Budapeşte. Bol bol tarihi yer görelim ve yöresel lezzetler tadalım.",
        height=130
    )
    
    st.markdown("---")
    generate_btn = st.button("Devasa Planı Oluştur 🚀", use_container_width=True)

# --- 4. ANA UYGULAMA MANTIĞI VE SEKMELER (TABS) ---
st.title("🗺️ Global Rota Planlayıcı V2 (Çoklu Şehir & Çoklu Gün)")
st.caption("Yapay zeka tüm seyahatinizi gün gün analiz eder ve her gün için ayrı bir harita çıkarır.")

if generate_btn:
    if not user_ai_prompt:
        st.error("Lütfen hayalinizdeki seyahat planını yazın!")
    else:
        with st.spinner("🧠 Yapay zeka tüm günleri ve şehirleri planlıyor, koordinatları hesaplıyor (10-20 saniye sürebilir)..."):
            
            multi_day_plan = get_multi_day_plan_from_ai(user_ai_prompt)
            
            if not multi_day_plan:
                st.warning("Veri çekilemedi. Lütfen planınızı biraz daha basitleştirip tekrar deneyin.")
            else:
                st.balloons()
                
                # Dinamik Sekmeleri (Tabs) Oluşturma
                tab_titles = [f"📅 Gün {day['gun']} ({day['sehir']})" for day in multi_day_plan]
                tabs = st.tabs(tab_titles)
                
                # Her sekmenin içini kendi gününün verisiyle doldurma
                for i, tab in enumerate(tabs):
                    with tab:
                        day_data = multi_day_plan[i]
                        city_name = day_data['sehir']
                        df_day = pd.DataFrame(day_data['mekanlar'])
                        
                        if df_day.empty:
                            st.warning(f"{city_name} için mekan bulunamadı.")
                            continue
                            
                        # API Çökme Riskine Karşı Otomatik Başlangıç (O günün ilk mekanı)
                        baslangic_lat = df_day.iloc[0]['lat']
                        baslangic_lon = df_day.iloc[0]['lon']
                        
                        start_dt = pd.Timestamp(f"2000-01-01 {start_time}")
                        end_dt = pd.Timestamp(f"2000-01-01 {end_time}")
                        total_available_minutes = int((end_dt - start_dt).total_seconds() / 60)
                        
                        gunluk_rota = optimize_route(df_day, baslangic_lat, baslangic_lon, total_available_minutes)
                        
                        if gunluk_rota.empty:
                            st.warning("Zaman yetersiz!")
                        else:
                            map_col, timeline_col = st.columns([3, 2])
                            
                            with map_col:
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
                                    
                                folium.PolyLine(locations=route_coords, color="red", weight=4, opacity=0.7, dash_array='10').add_to(m)
                                
                                # Streamlit Folium'un sekme içinde düzgün çalışması için key ataması
                                folium_static(m, width=550, height=450)
                                
                            with timeline_col:
                                st.subheader(f"📍 {city_name} Çizelgesi")
                                current_time = start_dt
                                st.info(f"**{current_time.strftime('%H:%M')}** | 🚶‍♂️ Güne Başlangıç")
                                
                                for idx, row in gunluk_rota.iterrows():
                                    current_time += timedelta(minutes=int(row['travel_time']))
                                    varis_saati = current_time.strftime('%H:%M')
                                    current_time += timedelta(minutes=int(row['ort_sure']))
                                    cikis_saati = current_time.strftime('%H:%M')
                                    
                                    st.warning(
                                        f"**{varis_saati} - {cikis_saati}** | 📍 {idx+1}. {row['name']}\n\n"
                                        f"*Kategori: {row['kategori']} | Geçiş: {int(row['travel_time'])} dk*"
                                    )
