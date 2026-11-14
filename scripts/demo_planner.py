#!/usr/bin/env python3
"""Independent toy information-gain demonstration using generated synthetic catalogues.
Not a replacement for backend/optimization/planner.py or proof of accuracy.
"""
from validate_catalogues import validate
from math import log2

def entropy(dist):
    return -sum(p*log2(p) for p in dist.values() if p>0)

def information_gain(b, table):
    current=entropy(b)
    outcomes=next(iter(table.values())).keys()
    expected=0
    for outcome in outcomes:
        prob_out=sum(b[h]*table[h][outcome] for h in b)
        if prob_out<=0: continue
        conditional={h:b[h]*table[h][outcome]/prob_out for h in b}
        expected+=prob_out*entropy(conditional)
    return current-expected

if __name__ == '__main__':
    knowledge, tests, model=validate()
    scores=[]
    for tid, tab in model['likelihoods'].items():
        ig=information_gain(model['priors'],tab)
        cost=tests['tests'][tid]['synthetic_cost_for_planner_simulation']['energy_j']
        scores.append((ig/cost, ig,tid,cost))
    print('\nToy IG/J ranking from synthetic assumptions (not an execution order):')
    for score,ig,tid,cost in sorted(scores,reverse=True)[:8]:
        print(f'{tid:36} IG={ig:.3f} bits   cost={cost:.2f} synthetic J   score={score:.2f} bits/J')
