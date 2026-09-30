import sys
from pathlib import Path
import streamlit as st
import pandas as pd
import numpy as np
import folium
from streamlit_folium import st_folium

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from src.OFL.ofl import OFLModel
from src.OFL.revenue import run as run_revenue


@st.cache_data
def load_base_data():
    geo_path = ROOT_DIR / 'data' / 'krakow_postal_codes.csv'
    prices_path = ROOT_DIR / 'data' / 'price_per_m2_statistics_postal_codes.csv'
    
    df_geo = pd.read_csv(geo_path)
    
    try:
        df_prices = pd.read_csv(prices_path, sep=';')
        df_prices.columns = df_prices.columns.str.strip()
        if 'avg_price_m2' not in df_prices.columns:
            df_prices = pd.read_csv(prices_path, sep=',')
            df_prices.columns = df_prices.columns.str.strip()
    except Exception:
        df_prices = pd.read_csv(prices_path, sep=',')
        df_prices.columns = df_prices.columns.str.strip()

    if 'avg_price_m2' in df_prices.columns and df_prices['avg_price_m2'].dtype == 'object':
        df_prices['avg_price_m2'] = df_prices['avg_price_m2'].astype(str).str.replace(',', '.').astype(float)
        
    if 'medium_price_m2' in df_prices.columns and df_prices['medium_price_m2'].dtype == 'object':
        df_prices['medium_price_m2'] = df_prices['medium_price_m2'].astype(str).str.replace(',', '.').astype(float)

    df_merged = pd.merge(df_geo, df_prices, on='postal_code', how='inner')
    return df_merged


@st.cache_data
def load_revenue_data():
    df_rev = run_revenue(rebuild=False)
    
    df_rev = df_rev.rename(columns={'zip_code': 'postal_code'})
    return df_rev


st.set_page_config(page_title="Nextspot - Krakow", layout="wide")
st.title("Cheapest Locations Finder - Krakow")

st.markdown("""
Set your budget and required space to find the optimal locations for investment on the map.

**How does it work?**  
This tool acts as a virtual bargain hunter. The mathematical optimization algorithm analyzes the real estate market data along with revenue predictions, and automatically selects the locations that maximize your profit without exceeding the budget limit.
""")

df_base = load_base_data()
df_rev = load_revenue_data()

available_types = df_rev['type'].unique().tolist()

with st.sidebar:
    st.header("Search Parameters")
    place_type = st.selectbox("Type of place", available_types)
    budget = st.number_input("Budget (PLN)", min_value=10000, max_value=10000000, value=2500000, step=50000)
    sq_meters_needed = st.slider("Required space (m2)", min_value=10, max_value=200, value=25, step=5)
    max_locations = st.number_input("Max number of locations", min_value=1, max_value=20, value=1, step=1)

df_rev_filtered = df_rev[df_rev['type'] == place_type].copy()

df_base['postal_code_clean'] = df_base['postal_code'].astype(str).str.replace('-', '').str.strip()
df_rev_filtered['postal_code_clean'] = df_rev_filtered['postal_code'].astype(str).str.replace('-', '').str.strip()

df = pd.merge(df_base, df_rev_filtered[['postal_code_clean', 'predicted_revenue']], on='postal_code_clean', how='inner')

df = df.drop(columns=['postal_code_clean'])

df['revenue'] = df['predicted_revenue']
df['total_cost'] = (df['avg_price_m2'] * sq_meters_needed) / 12

postal_codes_list = df['postal_code'].tolist()
r_list = df['revenue'].tolist()
c_list = df['total_cost'].tolist()

if not postal_codes_list:
    st.error(f"Nie znaleziono pokrywających się danych dla Krakowa dla typu: {place_type}.")
else:
    model = OFLModel(
        revenue=r_list, 
        cost=c_list, 
        budget=budget,
        max_locations=max_locations,
        postal_codes=postal_codes_list
    )

    with st.spinner('MIP solver optimization in progress...'):
        selected_pc = model.solve()

    if not selected_pc:
        st.error(f"Could not find a solution for the budget of {budget:,.0f} PLN and required space.")
    else:
        st.success(f"Successfully matched locations to your budget.")
        
        df_selected = df[df['postal_code'].isin(selected_pc)].copy()
        df_selected['profit'] = df_selected['revenue'] - df_selected['total_cost']
        
        df_selected['avg_price_m2'] = np.ceil(df_selected['avg_price_m2'])
        df_selected['total_cost'] = np.ceil(df_selected['total_cost'])
        
        df_selected = df_selected.sort_values(by='profit', ascending=False).reset_index(drop=True)
        
        selected_row_idx = None
        if "locations_table" in st.session_state:
            selected_rows = st.session_state["locations_table"]["selection"]["rows"]
            if selected_rows:
                selected_row_idx = selected_rows[0]

        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.subheader("Map of selected locations")
            
            if selected_row_idx is not None:
                center_lat = df_selected.loc[selected_row_idx, 'latitude']
                center_lon = df_selected.loc[selected_row_idx, 'longitude']
                start_zoom = 14
            else:
                center_lat = 50.0614
                center_lon = 19.9366
                start_zoom = 12
                
            m = folium.Map(location=[center_lat, center_lon], zoom_start=start_zoom)
            
            for idx, row in df_selected.iterrows():
                is_selected = (selected_row_idx == idx)
                
                popup_html = f"""
                <b>Code: {row['postal_code']}</b><br>
                Predicted Revenue: {row['revenue']:,.0f} PLN<br>
                Price per m2: {row['avg_price_m2']:,.0f} PLN<br>
                Total cost: {row['total_cost']:,.0f} PLN
                """
                
                marker_color = 'red' if is_selected else 'green'
                marker_icon = 'star' if is_selected else 'home'
                
                folium.Marker(
                    location=[row['latitude'], row['longitude']],
                    popup=folium.Popup(popup_html, max_width=300),
                    icon=folium.Icon(color=marker_color, icon=marker_icon)
                ).add_to(m)
                
            st_folium(m, width=700, height=500)
            
        with col2:
            st.subheader("Financial Details")
            
            st.dataframe(
                df_selected[['postal_code', 'revenue', 'total_cost', 'profit']],
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="locations_table",
                column_config={
                    "postal_code": "Postal Code",
                    "revenue": st.column_config.NumberColumn(
                        "Revenue (PLN)", format="%.0f"
                    ),
                    "total_cost": st.column_config.NumberColumn(
                        "Cost (PLN)", format="%.0f"
                    ),
                    "profit": st.column_config.NumberColumn(
                        "Est. Profit (PLN)", format="%.0f"
                    )
                }
            )
            
            st.metric("Number of available locations", f"{len(df_selected)}")
            st.metric("Used budget", f"{df_selected['total_cost'].sum():,.0f} PLN")
            st.metric("Estimated Profit", f"{df_selected['profit'].sum():,.0f} PLN")