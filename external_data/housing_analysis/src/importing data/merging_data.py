import pandas as pd
import geopandas as gpd
from shapely import wkt
import os

PATH_MAP = r"data/processed/mapping"
PATH_RAW = r"data/processed/single_files"
PATH_OUT = r"data/merged"

print("MERGE DANYCH ")

#mappingi
m_trans_prop = pd.read_csv(os.path.join(PATH_MAP, "map_trans_prop.csv"), dtype=str)
m_trans_doc  = pd.read_csv(os.path.join(PATH_MAP, "map_trans_doc.csv"), dtype=str)
m_prop_lokal = pd.read_csv(os.path.join(PATH_MAP, "map_prop_lokal.csv"), dtype=str)
m_lokal_adres = pd.read_csv(os.path.join(PATH_MAP, "map_lokal_adres.csv"), dtype=str)
m_prop_build = pd.read_csv(os.path.join(PATH_MAP, "map_prop_building.csv"), dtype=str)

#mosty
bridge = pd.merge(m_trans_prop, m_trans_doc, on='id_transakcji', how='left')
bridge = pd.merge(bridge, m_prop_lokal, on='id_nieruchomosci', how='inner')
bridge = pd.merge(bridge, m_lokal_adres, on='id_lokalu', how='left')
bridge = pd.merge(bridge, m_prop_build, on='id_nieruchomosci', how='left')

#pliki layers
df_trans = pd.read_csv(os.path.join(PATH_RAW, "RCN_Transakcja.csv"), low_memory=False, dtype=str)
df_lokal = pd.read_csv(os.path.join(PATH_RAW, "RCN_Lokal.csv"), low_memory=False, dtype=str)
df_doc   = pd.read_csv(os.path.join(PATH_RAW, "RCN_Dokument.csv"), low_memory=False, dtype=str)
df_build = pd.read_csv(os.path.join(PATH_RAW, "RCN_Budynek.csv"), low_memory=False, dtype=str)
df_adres = pd.read_csv(os.path.join(PATH_RAW, "RCN_Adres.csv"), low_memory=False, dtype=str)

#joiny
#Cena i Metraż
final = pd.merge(bridge, df_trans, left_on='id_transakcji', right_on='gml_id')
final = pd.merge(final, df_lokal, left_on='id_lokalu', right_on='gml_id', suffixes=('', '_lo'))

#Dokument (Data)
if 'dataSporzadzeniaDokumentu' in df_doc.columns:
    final = pd.merge(final, df_doc[['gml_id', 'dataSporzadzeniaDokumentu']], 
                     left_on='id_dokumentu', right_on='gml_id', how='left')

#Budynek
if 'rodzajBudynku' in df_build.columns:
    final = pd.merge(final, df_build[['gml_id', 'rodzajBudynku']], 
                     left_on='id_budynku', right_on='gml_id', how='left', suffixes=('', '_bu'))

#Adres (ulica i numer)
final = pd.merge(final, df_adres[['gml_id', 'ulica', 'numerPorzadkowy']], 
                 left_on='id_adresu', right_on='gml_id', how='left', suffixes=('', '_ad'))

#Obliczenia
final['cenaTransakcjiBrutto'] = pd.to_numeric(final['cenaTransakcjiBrutto'], errors='coerce')
final['powUzytkowaLokalu'] = pd.to_numeric(final['powUzytkowaLokalu'], errors='coerce')

#cena za m2
final = final.dropna(subset=['cenaTransakcjiBrutto', 'powUzytkowaLokalu', 'geometry_wkt'])
final['cena_m2'] = final['cenaTransakcjiBrutto'] / final['powUzytkowaLokalu']

#Filtracja rynkowa dla Krakowa
final = final[(final['cena_m2'] >= 5000) & (final['cena_m2'] <= 55000)].copy()

#Konwersja na GeoDataFrame
final['geometry'] = final['geometry_wkt'].apply(wkt.loads)
gdf = gpd.GeoDataFrame(final, geometry='geometry', crs="EPSG:2180")

#Usuwanie technicznych kolumn
cols_to_drop = [c for c in gdf.columns if 'id' in c or 'gml' in c or 'geometry_wkt' in c]
gdf = gdf.drop(columns=cols_to_drop)

#unikalne transakcje
unique_trans = gdf['id_transakcji_full'].nunique() if 'id_transakcji_full' in gdf.columns else "Brak kolumny ID"
print(f"Liczba wszystkich wierszy: {len(gdf)}")
print(f"Liczba unikalnych ID transakcji: {unique_trans}")

#duplikaty
duplicates_check = gdf.duplicated(subset=['cenaTransakcjiBrutto', 'powUzytkowaLokalu', 'geometry'], keep=False).sum()
print(f"Liczba wierszy, które mają identyczną cenę, metraż i lokalizację: {duplicates_check}")


output_file = os.path.join(PATH_OUT, "final_data_krakow.csv")
gdf.to_csv(output_file, index=False, encoding='utf-8-sig')

print("-" * 30)
print(f"Utworzono plik: {output_file}")
print(f"Liczba rekordów: {len(gdf)}")