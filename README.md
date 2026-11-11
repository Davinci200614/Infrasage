infrasage/
│
├── backend/
│   ├── main.py                       # MEMBER 3  thin FastAPI entry point
│   │
│   ├── collectors/                   # MEMBER 1
│   │   ├── base.py                   # execution_status vs observation contract
│   │   ├── docker.py                 # container state
│   │   ├── http.py                   # health endpoint probe
│   │   ├── redis.py                  # connectivity from Compose network
│   │   └── postgres.py               # connectivity from Compose network
│   │
│   ├── observations/                 # MEMBER 3
│   │   ├── schema.py                 # frozen Observation contract
│   │   └── normalization.py
│   │
│   ├── diagnosis/                    # MEMBER 2
│   │   ├── hypotheses.py             # includes other_or_unknown
│   │   ├── likelihoods.py            # hand-set, labeled as assumptions
│   │   ├── bayesian.py
│   │   ├── information_gain.py
│   │   ├── planner.py                # cost-weighted selection
│   │   └── stopping.py               # threshold plus abstention
│   │
│   └── core/                         # MEMBER 3
│       ├── models.py
│       ├── orchestrator.py
│       └── report.py                 # cause, evidence, uncertainty
│
├── experiments/                      # outside backend: labels stay hidden
│   ├── fault_injection.py            # MEMBER 1
│   ├── ground_truth/                 # MEMBER 1  labels, not importable by backend
│   ├── baselines.py                  # MEMBER 2  fixed-order planner
│   └── evaluation.py                 # MEMBER 2  accuracy, test count, time
│
├── frontend/                         # MEMBER 3, only if the API works early
│   ├── src/
│   │   ├── pages/
│   │   │   └── Dashboard.tsx         # DigitalTwin.tsx removed
│   │   └── App.tsx
│   └── package.json
│
├── lab/                              # MEMBER 1
│   ├── compose.yaml
│   └── scenarios/                    # stop_worker, stop_redis, stop_postgres
│
├── tests/
│   ├── reasoning/                    # MEMBER 2  worked-example checks
│   ├── collectors/                   # MEMBER 1  recorded fixtures, no live Docker
│   └── integration/                  # MEMBER 3  end-to-end run per scenario
│
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md                         # MEMBER 3, with lab section from MEMBER 1
