import re

PATH = "data/raw/1261_RCN.gml"

def szukaj_w_calym_pliku(path):
    print(f"Szukanie wzorców w całym pliku: {path}")
    
    found_nier = False
    found_lokal = False
    
    re_nier = re.compile(r'<rcn:RCN_Nieruchomosc.*?>')
    re_lokal = re.compile(r'<rcn:RCN_Lokal.*?>')

    try:
        with open(path, 'r', encoding='utf-8') as f:
            buffer = ""
            for line in f:
                #Szukamy Nieruchomości
                if not found_nier and re_nier.search(line):
                    print("\nPoczątek nieruchomości. Przykład:")
                    #50 linii, żeby zobaczyć strukturę
                    example = line
                    for _ in range(50):
                        example += next(f)
                    print("-" * 50)
                    print(example)
                    print("-" * 50)
                    found_nier = True

                #Szukamy Lokalu
                if not found_lokal and re_lokal.search(line):
                    print("\nPoczątek lokalu. Przykład:")
                    example = line
                    for _ in range(50):
                        example += next(f)
                    print("-" * 50)
                    print(example)
                    print("-" * 50)
                    found_lokal = True
                
                if found_nier and found_lokal:
                    break
                    
        if not found_nier: print("Nie znaleziono RCN_Nieruchomosc w całym pliku.")
        if not found_lokal: print("Nie znaleziono RCN_Lokal w całym pliku.")

    except Exception as e:
        print(f"Błąd: {e}")

if __name__ == "__main__":
    szukaj_w_calym_pliku(PATH)