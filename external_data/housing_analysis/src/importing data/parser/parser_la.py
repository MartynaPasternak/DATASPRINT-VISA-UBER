import xml.etree.ElementTree as ET
import pandas as pd
import os

file_path = r"data/raw/1261_RCN.gml"
output_path = r"data/processed/mapping/map_lokal_adres.csv"

os.makedirs(os.path.dirname(output_path), exist_ok=True)

data = []

print("Wyciąganie relacji Lokal -> Adres...")

context = ET.iterparse(file_path, events=('end',))

for event, elem in context:
    tag = elem.tag.split('}')[-1]
    
    if tag == 'RCN_Lokal':
        #ID lokalu (gml:id)
        l_id = elem.get('{http://www.opengis.net/gml/3.2}id')
        
        a_id = None
        #tag, który zawiera link do adresu
        for child in elem:
            child_tag = child.tag.split('}')[-1]
            if 'adres' in child_tag.lower():
                href = child.get('{http://www.w3.org/1999/xlink}href')
                if href:
                    a_id = href.replace('#', '')
        
        if l_id and a_id:
            data.append({
                'id_lokalu': l_id, 
                'id_adresu': a_id
            })
        
        elem.clear()
    
    elif tag.startswith('RCN_'):
        elem.clear()

df = pd.DataFrame(data)
df.to_csv(output_path, index=False)
print(f"Zapisano {len(df)} powiązań Lokal-Adres.")