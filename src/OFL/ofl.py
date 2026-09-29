import numpy as np
import pandas as pd
from mip import Model, xsum, maximize, BINARY, OptimizationStatus


class OFLModel:
    def __init__(self, revenue, cost, budget):
        self.r = revenue
        self.c = cost
        self.b = budget
    
    def solve(self):
        m = Model("ofl")

        x = [m.add_var(var_type=BINARY) for i in postal_codes]
        m.objective = maximize(xsum((r[i] - c[i]) * x[i] for i in I))
        m += xsum(c[i] * x[i] for i in I) <= b

        m.max_mip_gap = 0.09
        status = m.optimize(max_seconds=180)

        if status == OptimizationStatus.OPTIMAL or status == OptimizationStatus.FEASIBLE:
            selected_idx = [i for i in I if x[i].x >= 0.99]
            selected_postal_codes = [postal_codes[i] for i in selected_idx]
            return selected_postal_codes


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
    
    m = OFLModel(r, c, b)
    selected_postal_codes = m.solve()
    print('Selected:', selected_postal_codes)
