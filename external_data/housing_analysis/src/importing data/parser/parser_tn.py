import xml.etree.ElementTree as ET
import pandas as pd
import os

file_path = r"data/raw/1261_RCN.gml"
output_path = r"data/processed/mapping/map_trans_prop.csv"

os.makedirs(os.path.dirname(output_path), exist_ok=True)

data = []

print("Transakcja -> Nieruchomość")

context = ET.iterparse(file_path, events=('end',))

for event, elem in context:
    tag = elem.tag.split('}')[-1]
    
    if tag == 'RCN_Transakcja':
        t_id = None
        for attr_name, attr_val in elem.attrib.items():
            if attr_name.endswith('id'):
                t_id = attr_val
        
        # Przeszukujemy dzieci
        for child in elem:
            child_tag = child.tag.split('}')[-1]
            if 'nieruchomosc' in child_tag:
                # Szukamy linku href
                href = None
                for attr_name, attr_val in child.attrib.items():
                    if attr_name.endswith('href'):
                        href = attr_val
                
                if href:
                    data.append({
                        'id_transakcji': t_id, 
                        'id_nieruchomosci': href.replace('#', '')
                    })
        
        elem.clear()
    
    elif tag.startswith('RCN_'):
        elem.clear()

df = pd.DataFrame(data)
df.to_csv(output_path, index=False)
print(f"Zapisano {len(df)} relacji Transakcja-Nieruchomość.")

