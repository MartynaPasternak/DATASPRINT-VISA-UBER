import xml.etree.ElementTree as ET
import pandas as pd
import os

file_path = r"data/raw/1261_RCN.gml"
output_path = r"data/processed/mapping/map_prop_dzialka.csv"

os.makedirs(os.path.dirname(output_path), exist_ok=True)
data = []

print("Nieruchomość -> Działka")

context = ET.iterparse(file_path, events=('end',))
for event, elem in context:
    tag = elem.tag.split('}')[-1]
    
    if tag == 'RCN_Nieruchomosc':
        n_id = elem.get('{http://www.opengis.net/gml/3.2}id')
        
        for child in elem:
            child_tag = child.tag.split('}')[-1]
            if 'dzialka' in child_tag:
                href = child.get('{http://www.w3.org/1999/xlink}href')
                if href:
                    data.append({
                        'id_nieruchomosci': n_id,
                        'id_dzialki': href.replace('#', '')
                    })
        
        elem.clear()
    elif tag.startswith('RCN_'):
        elem.clear()

df = pd.DataFrame(data)
df.to_csv(output_path, index=False)
print(f"Zapisano {len(df)} relacji Nieruchomość-Działka.")