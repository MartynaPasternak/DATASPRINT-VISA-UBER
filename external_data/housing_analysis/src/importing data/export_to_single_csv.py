import geopandas as gpd
import pandas as pd
import os

os.environ['GML_ATTRIBUTES_TO_OGR_FIELDS'] = 'YES'
os.environ['GML_EXPOSE_XLINK'] = 'YES' 

file_path = r".\data\raw\1261_RCN.gml" 
output_folder = r".\data\processed\single_files"

layers = [
    'RCN_Transakcja', 'RCN_Dokument', 'RCN_Nieruchomosc', 
    'RCN_Dzialka', 'RCN_Budynek', 'RCN_Lokal', 'RCN_Adres'
]

if not os.path.exists(output_folder):
    os.makedirs(output_folder)

for layer in layers:
    try:
        print(f"Przetwarzanie warstwy: {layer}")
        gdf = gpd.read_file(file_path, layer=layer)
        
        if 'geometry' in gdf.columns:
            gdf['geometry_wkt'] = gdf['geometry'].apply(lambda x: x.wkt if x is not None else None)
            df = pd.DataFrame(gdf.drop(columns='geometry'))
        else:
            df = pd.DataFrame(gdf)
            
        output_file = os.path.join(output_folder, f"{layer}.csv")
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"Zapisano {len(df)} wierszy.")
        
    except Exception as e:
        print(f"Błąd przy warstwie {layer}: {e}")

print("\nEksport zakończony.")