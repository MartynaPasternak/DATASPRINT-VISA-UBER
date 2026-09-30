import xml.etree.ElementTree as ET
import pandas as pd
import os

file_path = r"data/raw/1261_RCN.gml"
output_path = r"data/processed/mapping/map_prop_lokal.csv"

os.makedirs(os.path.dirname(output_path), exist_ok=True)

data = []

print("Nieruchomość -> Lokal")

context = ET.iterparse(file_path, events=('end',))

for event, elem in context:
    tag_clean = elem.tag.split('}')[-1]
    
    if tag_clean == 'RCN_Nieruchomosc':
        n_id = None
        for k, v in elem.attrib.items():
            if k.endswith('id'):
                n_id = v
        
        for child in elem:
            child_tag = child.tag.split('}')[-1]
            if 'lokal' in child_tag:
                # Szukamy linku href do lokalu
                href = None
                for k, v in child.attrib.items():
                    if k.endswith('href'):
                        href = v
                
                if href:
                    data.append({
                        'id_nieruchomosci': n_id, 
                        'id_lokalu': href.replace('#', '')
                    })
        
        elem.clear()
    
    elif tag_clean in ['RCN_Budynek', 'RCN_Dzialka', 'RCN_Dokument']:
        elem.clear()

df = pd.DataFrame(data)
df.to_csv(output_path, index=False)
print(f"Zapisano {len(df)} relacji Nieruchomość-Lokal.")