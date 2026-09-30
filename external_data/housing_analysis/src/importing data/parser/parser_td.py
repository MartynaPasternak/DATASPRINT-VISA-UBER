import xml.etree.ElementTree as ET
import pandas as pd
import os

file_path = r"data/raw/1261_RCN.gml"
output_path = r"data/processed/mapping/map_trans_doc.csv"

os.makedirs(os.path.dirname(output_path), exist_ok=True)
data = []

print("Transakcja -> Dokument")

context = ET.iterparse(file_path, events=('end',))
for event, elem in context:
    tag = elem.tag.split('}')[-1]
    
    if tag == 'RCN_Transakcja':
        t_id = None
        for k, v in elem.attrib.items():
            if k.endswith('id'): t_id = v
        
        doc_id = None
        for child in elem:
            child_tag = child.tag.split('}')[-1]
            if child_tag == 'podstawaPrawna':
                href = child.get('{http://www.w3.org/1999/xlink}href')
                if href:
                    doc_id = href.replace('#', '')
        
        if t_id and doc_id:
            data.append({
                'id_transakcji': t_id, 
                'id_dokumentu': doc_id
            })
        
        elem.clear()
    elif tag.startswith('RCN_'):
        elem.clear()

df = pd.DataFrame(data)
df.to_csv(output_path, index=False)
print(f"Zapisano {len(df)} relacji Transakcja-Dokument.")