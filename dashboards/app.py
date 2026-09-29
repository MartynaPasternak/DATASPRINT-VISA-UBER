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
import src.OFL.ofl as ofl_module

def get_revenue_predictions(df):
    """
    Placeholder function for revenue generation. 
    Will be replaced by src.OFL.revenue model.
    """
    rng = np.random.default_rng(42)
    # Increased the baseline revenue to 1,000,000 PLN so it is always higher 
    # than the real Krakow real estate costs. This forces the model to 
    # always see a positive profit and maximize the number of locations.
    return [rng.uniform(low=0.8, high=1.2) * 1000000 for _ in range(len(df))]

@st.cache_data
def load_data():
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

st.set_page_config(page_title="Nextspot - Krakow", layout="wide")
st.title("Nextspot - Krakow")
st.markdown("Set your budget and required space to find the optimal, cheapest locations for investment on the map.")

df = load_data()
df['revenue'] = get_revenue_predictions(df)

with st.sidebar:
    st.header("Search Parameters")
    budget = st.number_input("Budget (PLN)", min_value=10000, max_value=10000000, value=2500000, step=50000)
    sq_meters_needed = st.slider("Required space (m2)", min_value=10, max_value=200, value=25, step=5)
    
df['total_cost'] = df['avg_price_m2'] * sq_meters_needed

postal_codes_list = df['postal_code'].tolist()
r_list = df['revenue'].tolist()
c_list = df['total_cost'].tolist()


ofl_module.postal_codes = postal_codes_list
ofl_module.I = range(len(postal_codes_list))
ofl_module.r = r_list
ofl_module.c = c_list
ofl_module.b = budget

model = OFLModel(
    revenue=r_list, 
    cost=c_list, 
    budget=budget
)

with st.spinner('MIP solver optimization in progress'):
    selected_pc = model.solve()

if not selected_pc:
    st.error(f"Could not find a solution for the budget of {budget:,.2f} PLN and required space.")
else:
    st.success(f"Successfully matched locations to your budget.")
    
    df_selected = df[df['postal_code'].isin(selected_pc)].copy()
    df_selected['profit'] = df_selected['revenue'] - df_selected['total_cost']
    
    df_selected = df_selected.sort_values(by='total_cost', ascending=True)
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Map of selected locations")
        m = folium.Map(location=[50.0614, 19.9366], zoom_start=12)
        
        for idx, row in df_selected.iterrows():
            popup_html = f"""
            <b>Code: {row['postal_code']}</b><br>
            Price per m2: {row['avg_price_m2']:,.2f} PLN<br>
            Total cost: {row['total_cost']:,.2f} PLN
            """
            folium.Marker(
                location=[row['latitude'], row['longitude']],
                popup=folium.Popup(popup_html, max_width=300),
                icon=folium.Icon(color='green', icon='home')
            ).add_to(m)
            
        st_folium(m, width=700, height=500)
        
    with col2:
        st.subheader("Financial Details")
        st.dataframe(
            df_selected[['postal_code', 'avg_price_m2', 'total_cost']]
            .style.format({
                'avg_price_m2': "{:,.2f} PLN",
                'total_cost': "{:,.2f} PLN"
            }),
            hide_index=True
        )
        
        st.metric("Number of available locations", f"{len(df_selected)}")
        st.metric("Used budget", f"{df_selected['total_cost'].sum():,.2f} PLN")