import numpy as np
import pandas as pd
from mip import Model, xsum, maximize, BINARY, OptimizationStatus

class OFLModel:
    def __init__(self, revenue, cost, budget, max_locations, postal_codes):
        self.r = revenue
        self.c = cost
        self.b = budget
        self.k = max_locations
        self.postal_codes = postal_codes
    
    def solve(self):
        m = Model("ofl")

        I = range(len(self.r)) 
        x = [m.add_var(var_type=BINARY) for i in I]
        
        m.objective = maximize(xsum((self.r[i] - self.c[i]) * x[i] for i in I))
        
        m += xsum(self.c[i] * x[i] for i in I) <= self.b
        
        m += xsum(x[i] for i in I) <= self.k

        m.max_mip_gap = 0.09
        status = m.optimize(max_seconds=180)

        if status == OptimizationStatus.OPTIMAL or status == OptimizationStatus.FEASIBLE:
            selected_idx = [i for i in I if x[i].x >= 0.99]
            selected_postal_codes = [self.postal_codes[i] for i in selected_idx]
            return selected_postal_codes
        return []


if __name__ == "__main__":
    postal_codes_df = pd.read_csv('data/krakow_postal_codes.csv')
    postal_codes = postal_codes_df['postal_code'].tolist()

    rng = np.random.default_rng()
    square_meter_prices = {pc: rng.uniform(low=0.5, high=1.0) * 1000 for pc in postal_codes}

    I = range(len(postal_codes))
    r = [rng.uniform(low=0.5, high=1.0) * 20000 * 10 for pc in postal_codes]
    square_meters_needed = 25
    c = [square_meters_needed*square_meter_prices[pc] for pc in postal_codes]

    b = 50000
    k = 1
    m = OFLModel(r, c, b, k, postal_codes)
    selected_postal_codes = m.solve()
    print('Selected:', selected_postal_codes)