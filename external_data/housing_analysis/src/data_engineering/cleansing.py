import pandas as pd
import geopandas as gpd
from shapely import wkt
import os

# Ścieżki
input_path = r"data/merged/final_data_krakow.csv"
output_path = r"data/merged/final_data_cleaned.csv"

print("CZYSZCZENIE")

try:
    df = pd.read_csv(input_path, encoding='utf-8-sig', low_memory=False)
except UnicodeDecodeError:
    df = pd.read_csv(input_path, encoding='utf-8', low_memory=False)

print(f"Wczytano: {len(df)} rekordów.")

#usuwamy duplikaty - zostawiam tylko jeden rekord, jeśli cena, metraż i ulica są identyczne
initial_count = len(df)
df_cleaned = df.drop_duplicates(
    subset=['cenaTransakcjiBrutto', 'powUzytkowaLokalu', 'ulica', 'geometry'], 
    keep='first'
).copy()

dropped_count = initial_count - len(df_cleaned)
print(f"Usunięto {dropped_count} duplikatów.")

print("Konwersja na format gdf")
if isinstance(df_cleaned['geometry'].iloc[0], str):
    df_cleaned['geometry'] = df_cleaned['geometry'].apply(wkt.loads)

gdf = gpd.GeoDataFrame(df_cleaned, geometry='geometry', crs="EPSG:2180")

gdf.to_csv(output_path, index=False, encoding='utf-8-sig')

print("-" * 30)
print(f"Zapisano {len(gdf)} unikalnych rekordów do: {output_path}")