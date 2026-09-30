import pandas as pd
import geopandas as gpd
import numpy as np
from shapely import wkt
import os

INPUT_FILE = r"data/merged/final_data_cleaned.csv"
DISTRICTS_PATH = r"data/krakow_data/data.gpkg"
OUTPUT_FILE = r"data/merged/final_data_preprocessed.csv"

#Rynek Główny w EPSG:2180
RYNEK_X = 566350 
RYNEK_Y = 244150 

print("Preprocessing")


df = pd.read_csv(INPUT_FILE, low_memory=False)
df['geometry'] = df['geometry'].apply(wkt.loads)
gdf = gpd.GeoDataFrame(df, geometry='geometry', crs="EPSG:2178")

#join z dzielnicami
print("Łączenie z dzielnicami")
districts = gpd.read_file(DISTRICTS_PATH, layer='districts')

#Sprowadzamy do EPSG2180
gdf = gdf.to_crs(districts.crs) 

#Dzielnica do punktu
gdf = gpd.sjoin(gdf, districts[['nazwa', 'geometry']], how='left', predicate='within')
gdf = gdf.rename(columns={'nazwa': 'dzielnica'})


keywords_to_drop = ['udział', 'udz', 'część', '1/2', '1/4', '1/3', 'ułamek']
pattern = '|'.join(keywords_to_drop)
gdf['dodatkoweInformacje'] = gdf['dodatkoweInformacje'].fillna('')
gdf = gdf[~gdf['dodatkoweInformacje'].str.contains(pattern, case=False)].copy()


print("Dodatkowe cechy")
gdf['info_lower'] = gdf['dodatkoweInformacje'].str.lower()
gdf['has_parking'] = gdf['info_lower'].str.contains('miejsce|garaż|parking|postojow').astype(int)
gdf['has_balcony'] = gdf['info_lower'].str.contains('balkon|loggia|taras').astype(int)
gdf['has_storage'] = gdf['info_lower'].str.contains('piwnic|komórk|pomieszczenie przynależne|schowek|pom. przynależne').astype(int)



gdf['rodzajRynku'] = gdf['rodzajRynku'].astype(float).map({1.0: 'Pierwotny', 2.0: 'Wtórny'})

# def map_building(code):
#     c = str(code)
#     if '110' in c : return 'Mieszkalny'
#     return 'Inny'

# gdf['typ_zabudowy'] = gdf['rodzajBudynku'].apply(map_building)

#czas i odległość od rynku
gdf['dataSporzadzeniaDokumentu'] = pd.to_datetime(gdf['dataSporzadzeniaDokumentu'], errors='coerce')
gdf = gdf.dropna(subset=['dataSporzadzeniaDokumentu'])
gdf['rok'] = gdf['dataSporzadzeniaDokumentu'].dt.year

gdf['X_2180'] = gdf.geometry.x
gdf['Y_2180'] = gdf.geometry.y
gdf['dist_to_center'] = np.sqrt((gdf['X_2180'] - RYNEK_X)**2 + (gdf['Y_2180'] - RYNEK_Y)**2)

#outliery w metrażu
gdf = gdf[(gdf['powUzytkowaLokalu'] >= 15) & (gdf['powUzytkowaLokalu'] <= 200)].copy()

#wybór funkcjiLokalu == "mieszkalna"
gdf = gdf[gdf['funkcjaLokalu'].astype(str).str.startswith('1')].copy()

#transakcje rynkowe z wolnyRynek - bez komornika ani innych
gdf = gdf[gdf['rodzajTransakcji'].astype(str).str.startswith('1')].copy()

gdf = gdf[gdf['rodzajBudynku'] == 110].copy()

#kolumny do modelowania
cols_for_ml = [
    'cena_m2', 'powUzytkowaLokalu', 'nrKondygnacji', 'liczbaIzb', 
    'rodzajRynku', 'dzielnica', 'rok', 
    'dist_to_center', 'has_parking', 'has_balcony', 'has_storage', 'X_2180', 'Y_2180'
]
df_final = gdf[cols_for_ml].dropna().copy()

df_final = df_final[(df_final['rok'] >= 2023) & (df_final['rok'] <= 2025)].copy()
df_final = df_final[df_final['nrKondygnacji'] >= 0].copy()


df_final.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')

print(f"Zapisano {len(df_final)} rekordów.")
print(f"Średnia cena m2 w zbiorze: {df_final['cena_m2'].mean():.2f} zł")