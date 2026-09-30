# NextSpot Kraków

NextSpot helps someone decide **where in Kraków to open a restaurant or a fast-food place**.

The user picks the type of place, a budget in PLN, the floor area they need, and how many locations they want. The app then shows the best postal codes on a map, with predicted revenue, cost, and estimated profit for each one.

It is built for founders, franchise operators, and city teams. You don't need to read any code to use it.

## What the app does

Open the map with the command in [How to open the map](#how-to-open-the-map). The page is called **NextSpot - Krakow**.

The left-hand panel has four controls:


| Control                 | What it does                                       | Starting value                 |
| ----------------------- | -------------------------------------------------- | ------------------------------ |
| Type of place           | Restaurant or fast food                            | First type in the revenue file |
| Budget (PLN)            | The most the chosen places may cost in total       | 2,500,000                      |
| Required space (m²)     | Floor area used to work out the cost of each place | 25                             |
| Max number of locations | How many postal codes may be chosen                | 1                              |


After a few seconds the page shows:

- a map of Kraków with a pin on each chosen postal code
- a table with each code's predicted revenue, cost, and estimated profit
- three totals: the number of places chosen, how much of the budget they use, and the total estimated profit

Click a row in the table to zoom the map to that pin and highlight it in red. Click any pin to see its numbers.

## The three numbers on a pin

**Predicted revenue.** An estimate of typical card spend for one shop of the chosen type in that postal code over twelve months (July 2025 to June 2026). It was produced by a machine-learning model trained on Visa card transactions across Poland. The results are stored in `data/zip_revenue.csv`.

**Cost.** The average apartment price per square metre in that postal code, multiplied by the floor area the user asked for, divided by 12. The prices come from 36,862 real apartment sales in Kraków from 2023 to 2025.

**Estimated profit.** Predicted revenue minus cost.

The app keeps the combination of postal codes with the highest total estimated profit that stays within the budget and the maximum number of locations.

## How it works

```text
Visa card transactions
        │
        ▼
Revenue model (src/OFL/revenue.py)
        │
        ▼
data/zip_revenue.csv ──────────────┐
                                   │
data/krakow_postal_codes.csv ──────┤   where each postal code is
data/price_per_m2_statistics_...  ─┤   what space costs there
                                   ▼
                     Map app (dashboards/app.py)
                                   │
                                   ▼
                     Location picker (src/OFL/ofl.py)
```



### 1. Revenue model

`src/OFL/revenue.py` turns the Visa transactions into a revenue estimate for each postal code in Poland.

1. **Find the shops.** It keeps in-person card payments at Polish restaurants (merchant category 5812) and fast-food places (5814). Several card terminals in one shop are merged into one shop. Postcodes that act as payment hubs, where 60 or more different merchants appear in one month, are ignored so shops are placed at their real address.
2. **Group shops into brands.** `src/OFL/restaurants_pl_brands.py` cleans merchant names by removing payment-company prefixes, legal forms, shop numbers, and city names. National chains such as McDonald's, KFC, and Sphinx each become one brand. Places named only with generic words, such as "Restauracja", stay separate so unrelated businesses are not merged. Vending machines are removed.
3. **Describe each area.** For each postal code, the model is given the number of shops and brands, competition, card activity, average ticket, the share of spend that is local, foreign, premium, or business, dinner-time and weekend spend, year-on-year trend, nearby grocery and hotel spend, and how much people living within 5 km spend on eating out. A shop is never used to describe its own area.
4. **Predict.** An XGBoost model learns the typical yearly spend of a shop that was open all year in each postal code and category. It is tested on 30% of postal codes it has never seen. The number in the file for each postal code comes from a version of the model that did not see that postal code during training.

The output has 10,620 rows covering 7,591 postal codes: 6,929 restaurant rows and 3,691 fast-food rows.

> **Visa data required for retraining:** The original Visa parquet is not included in this repository. To retrain the revenue model, obtain `datasprint_sample_data.parquet` through the approved data-sharing process and place it in `src/OFL/`, beside `revenue.py`. The existing map does not require this file.

### 2. Kraków locations and costs

`notebooks/postal_codes.ipynb` builds the Kraków files:

1. It takes the official list of Polish postal codes (`data/PL.txt`, from GeoNames) and keeps Kraków and the surrounding county. That gives 1,151 postal codes, each with a map position.
2. It assigns each of the 36,862 apartment sales to its nearest postal code.
3. It calculates the average and median price per square metre for each postal code, which covers 911 codes.

The map can show 407 Kraków postal codes that have a position, a price, and a revenue estimate.

### 3. Location picker

`src/OFL/ofl.py` treats every postal code as a yes/no choice. It uses an optimisation solver to find the set of codes with the highest total estimated profit, while keeping:

- the total cost within the budget
- the number of locations at or below the user's maximum



## What is in this folder


| Path                                            | What it is                                                                                                                                          |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dashboards/app.py`                             | The map app                                                                                                                                         |
| `src/OFL/ofl.py`                                | The location picker                                                                                                                                 |
| `src/OFL/revenue.py`                            | The revenue model                                                                                                                                   |
| `src/OFL/restaurants_pl_brands.py`              | Merchant name cleaning and brand matching                                                                                                           |
| `data/zip_revenue.csv`                          | Predicted revenue per postal code and type (`zip_code`, `type`, `predicted_revenue`)                                                                |
| `data/krakow_postal_codes.csv`                  | 1,151 Kraków-area postal codes with latitude and longitude                                                                                          |
| `data/price_per_m2_statistics_postal_codes.csv` | Average and median apartment price per m² for 911 postal codes                                                                                      |
| `data/input_data.csv`                           | 36,862 Kraków apartment sales: price per m², size, floor, rooms, market, district, year, distance to centre, parking, balcony, storage, coordinates |
| `data/apartments_with_postal_codes.csv`         | The same sales with a postal code attached                                                                                                          |
| `data/PL.txt`                                   | GeoNames list of Polish postal codes                                                                                                                |
| `notebooks/postal_codes.ipynb`                  | Builds the Kraków postal code and price files                                                                                                       |




## How to open the map

From this folder, install `streamlit`, `pandas`, `numpy`, `folium`, `streamlit-folium`, and `mip`, then run:

```bash
streamlit run dashboards/app.py
```

Run the command from this folder, not from inside `dashboards/`.

To rebuild the Kraków postal code and price files, open `notebooks/postal_codes.ipynb` from inside `notebooks/` and run all cells. This needs `pandas`, `numpy`, `pyproj`, and `scipy`.